#!/usr/bin/env python3
"""Prepare a new public copy of benchmark JSON/JSONL; review before publishing.

Only JSON and JSONL files are copied. Original input files are never changed.
This removes known metadata, not arbitrary secrets or identifying free text.
"""
import argparse
import json
from pathlib import Path


WALL_CLOCK_KEYS = {"started_unix_seconds", "unix_seconds"}
PID_KEYS = {"pid", "probe_pid"}


def collect_pids(value, pids):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PID_KEYS and type(item) is int:
                pids.add(item)
            collect_pids(item, pids)
    elif isinstance(value, list):
        for item in value:
            collect_pids(item, pids)


def collect_starts(value, starts):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "start_time" and isinstance(item, str):
                starts.add(item)
            collect_starts(item, starts)
    elif isinstance(value, list):
        for item in value:
            collect_starts(item, starts)


def normalize(value, replacements, pid_map, start_map=None):
    start_map = start_map or {}
    if isinstance(value, dict):
        return {
            key: (pid_map[item] if key in PID_KEYS and type(item) is int
                  else start_map[item] if key == "start_time" and isinstance(item, str) and item in start_map
                  else normalize(item, replacements, pid_map, start_map))
            for key, item in value.items() if key not in WALL_CLOCK_KEYS
        }
    if isinstance(value, list):
        return [normalize(item, replacements, pid_map, start_map) for item in value]
    if isinstance(value, str):
        for prefix, placeholder in replacements:
            if value == prefix or value.startswith(prefix + "/"):
                return placeholder + value[len(prefix):]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Private results directory")
    parser.add_argument("output", type=Path, help="New, nonexistent public directory")
    parser.add_argument("--project-root", required=True,
                        help="Exact original project path to replace with $PROJECT_ROOT")
    parser.add_argument("--fixture-root", action="append", required=True,
                        help="Original fixture prefix; repeat for aliases such as /tmp")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if not source.is_dir():
        parser.error("source must be an existing directory")
    if output.exists() or source == output or source in output.parents or output in source.parents:
        parser.error("output must be new and outside the source directory")
    prefixes = [(args.project_root, "$PROJECT_ROOT")]
    prefixes.extend((p, "$FIXTURE_DIR") for p in args.fixture_root)
    if any(not Path(p).is_absolute() or p.rstrip("/") == "" for p, _ in prefixes):
        parser.error("replacement roots must be absolute paths other than /")
    replacements = sorted(((p.rstrip("/"), marker) for p, marker in prefixes),
                          key=lambda item: len(item[0]), reverse=True)

    records, pid_sets, start_sets = {}, {}, {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            parser.error("source must not contain symbolic links")
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        relative = path.relative_to(source)
        # Each run is one directory; preserve identity across its JSON/JSONL files.
        group = relative.parent
        value = ([json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                 if path.suffix == ".jsonl" else json.loads(path.read_text()))
        records[relative] = value
        collect_pids(value, pid_sets.setdefault(group, set()))
        collect_starts(value, start_sets.setdefault(group, set()))
    if not records:
        parser.error("source contains no JSON or JSONL files")
    pid_maps = {group: {pid: i + 1 for i, pid in enumerate(sorted(pids))}
                for group, pids in pid_sets.items()}

    start_maps = {group: {stamp: f"anonymous-start-{i + 1}" for i, stamp in enumerate(sorted(stamps))}
                  for group, stamps in start_sets.items()}

    output.mkdir(parents=True, exist_ok=False)
    for relative, value in records.items():
        clean = normalize(value, replacements, pid_maps[relative.parent], start_maps[relative.parent])
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = ("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in clean)
                   if relative.suffix == ".jsonl"
                   else json.dumps(clean, ensure_ascii=False, indent=2) + "\n")
        destination.write_text(content)
    print(f"Prepared {len(records)} JSON/JSONL files; input unchanged. Review the output before publication.")


if __name__ == "__main__":
    main()
