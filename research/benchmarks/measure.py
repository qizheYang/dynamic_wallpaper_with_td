#!/usr/bin/env python3
"""Bounded native probe with timestamped read-only system measurements (schema 2)."""
import argparse
import hashlib
import json
import math
import os
import pathlib
import platform
import re
import signal
import subprocess
import sys
import threading
import time

from system_metrics import (identity_matches, monotonic_seconds, sample_footprints,
                            sample_gpu, sample_processes)

STATES = {"idle", "play", "pause", "cover", "uncover"}
NUMBER = re.compile(r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def parse_schedule(value, sustained=False, cover=False):
    phases = []
    for segment in value.split(","):
        pair = segment.split(":")
        if len(pair) != 2 or pair[0] not in STATES or not NUMBER.fullmatch(pair[1]):
            raise ValueError(f"invalid schedule segment: {segment!r}")
        seconds = float(pair[1])
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("phase durations must be positive and finite")
        if pair[0] in {"cover", "uncover"} and not cover:
            raise ValueError("cover/uncover phases require --cover")
        phases.append({"state": pair[0], "seconds": seconds})
    total = sum(p["seconds"] for p in phases)
    limit = 3600 if sustained else 120
    if not math.isfinite(total) or not 1 <= total <= limit:
        raise ValueError(f"schedule total must be 1...{limit} seconds")
    return phases


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    os.replace(temp, path)


class ProbeReader:
    """Read only newly appended, complete JSONL records; partial writes wait."""
    def __init__(self, path):
        self.path, self.offset, self.pending = pathlib.Path(path), 0, b""
        self.latest = None
        self.errors = []

    def poll(self):
        if not self.path.exists():
            return self.latest
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
            self.offset = handle.tell()
        parts = (self.pending + data).split(b"\n")
        self.pending = parts.pop()
        for part in parts:
            if not part.strip():
                continue
            try:
                record = json.loads(part)
                if record.get("event") == "sample":
                    self.latest = {key: record.get(key) for key in
                                   ("monotonic_seconds", "elapsed_seconds", "phase", "phase_index",
                                    "layer_ready", "window_visible", "player_rate", "media_seconds",
                                    "pause_reasons", "session_active")}
            except (ValueError, AttributeError):
                self.errors.append({"source": "probe_log", "code": "invalid_json_record"})
        return self.latest


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def add_cpu_deltas(current, previous):
    old_by_pid = {p["pid"]: p for p in (previous or {}).get("processes", [])}
    interval = (current["finished_monotonic_seconds"] - previous["finished_monotonic_seconds"]
                if previous else None)
    for item in current["processes"]:
        item["cpu_percent_one_core"] = None
        old = old_by_pid.get(item["pid"])
        if not old:
            status = "first_sample_or_missing_predecessor"
        elif not identity_matches(item.get("identity"), old.get("identity")):
            status = "identity_changed_or_unknown"
        elif not finite(interval) or interval <= 0:
            status = "invalid_interval"
        elif not finite(item.get("cpu_seconds")) or not finite(old.get("cpu_seconds")):
            status = "missing_counter"
        elif item["cpu_seconds"] < old["cpu_seconds"]:
            status = "counter_regression"
        else:
            status = "ok"
            item["cpu_percent_one_core"] = 100 * (item["cpu_seconds"] - old["cpu_seconds"]) / interval
        item["cpu_delta_status"] = status


def join_footprints(footprints, before, after):
    """Reject a top PID read unless its identity survives the enclosing ps reads."""
    left = {p["pid"]: p for p in before.get("processes", [])}
    right = {p["pid"]: p for p in after.get("processes", [])}
    for item in footprints["footprints"]:
        a, b = left.get(item["pid"], {}), right.get(item["pid"], {})
        matched = identity_matches(a.get("identity"), b.get("identity"))
        item["identity_status"] = "matched_before_after" if matched else "changed_missing_or_unknown"
        item["identity"] = a.get("identity") if matched else None
        if not matched:
            item["physical_footprint_bytes"] = None
            footprints["errors"].append({"source": "top MEM", "code": "identity_unverified", "pid": item["pid"]})
    return footprints


def collect_system(player_pid=None, footprint=False, previous=None):
    row = {"schema_version": 2, "started_monotonic_seconds": monotonic_seconds()}
    processes = sample_processes(player_pid)
    add_cpu_deltas(processes, previous)
    row["process_sample"] = processes
    row["gpu_sample"] = sample_gpu()
    if footprint:
        pids = [p["pid"] for p in processes["processes"]]
        memory = sample_footprints(pids)
        after = sample_processes(player_pid)
        row["footprint_sample"] = join_footprints(memory, processes, after)
        row["footprint_identity_check"] = after
    row["finished_monotonic_seconds"] = monotonic_seconds()
    row["sampler_seconds"] = row["finished_monotonic_seconds"] - row["started_monotonic_seconds"]
    return row


def power_context():
    result = {"source": "pmset -g batt", "power_source": "unknown"}
    try:
        output = subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True,
                                text=True, timeout=3, check=True).stdout
        match = re.search(r"Now drawing from '([^']+)'", output)
        if match:
            result["power_source"] = match[1]
    except (OSError, subprocess.SubprocessError):
        result["error"] = "power_source_unavailable"
    return result


def capabilities():
    row = collect_system(footprint=True)
    return {"schema_version": 2, "purpose": "capability_snapshot_not_performance_acceptance",
            "platform": platform.platform(), "power": power_context(), "sample": row,
            "notes": ["No sudo or GUI used; missing fields are not zero.",
                      "top MEM is a rounded physical-footprint ledger observation.",
                      "IOKit counters are whole-machine observations, not task GPU time.",
                      "This snapshot does not establish clean baselines, ownership, or sampler overhead."]}


def build_command(args, log):
    command = [str(args.probe.resolve()), "--output", str(log), "--schedule", args.schedule]
    if args.video:
        command += ["--video", str(args.video.resolve())]
    for flag, enabled in [("--cover", args.cover), ("--pause-on-occlusion", args.pause_on_occlusion),
                          ("--sustained", args.sustained)]:
        if enabled:
            command.append(flag)
    return command


def validate_probe(command, expected_total):
    check = subprocess.run(command + ["--validate"], capture_output=True, text=True, timeout=10)
    if check.returncode:
        raise ValueError("probe validation failed: " + check.stderr.strip()[:1000])
    try:
        value = json.loads(check.stdout)
    except ValueError:
        raise ValueError("probe did not return schema-2 validation JSON; rebuild it") from None
    if (not isinstance(value, dict) or value.get("schema_version") != 2
            or not finite(value.get("total_seconds"))
            or not math.isclose(value["total_seconds"], expected_total)):
        raise ValueError("probe/wrapper schedule or schema mismatch; rebuild the probe")
    return value


def terminate_owned(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def run(args, phases, command, config):
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    metadata = {"schema_version": 2, "command": command, "probe_config": config,
                "purpose": args.purpose, "started_unix_seconds": time.time(),
                "probe_sha256": sha256(args.probe), "video_sha256": sha256(args.video) if args.video else None,
                "sample_interval_seconds": args.sample_interval, "footprint_interval_seconds": args.footprint_interval,
                "warmup_seconds": args.warmup_seconds, "tail_guard_seconds": 1,
                "platform": platform.platform(), "python": platform.python_version(),
                "power_start": power_context(), "run_status": "starting",
                "source_sha256": {p.name: sha256(p) for p in pathlib.Path(__file__).parent.glob('*.py')},
                "notes": ["All sampler/probe times use CLOCK_MONOTONIC in seconds.",
                          "Use sampler intervals and actual phase boundaries, never latest-sample phase labels, for analysis.",
                          "Whole-system GPU and system-wide decoder services are not attributed to the player.",
                          "top MEM is rounded footprint, separate from ps RSS; monitor overhead remains uncalibrated.",
                          "Known activity names indicate presence only; this is not a complete background-work inventory."]}
    write_json(out / "metadata.json", metadata)
    stopped = threading.Event()
    received = []
    old_handlers = {}
    def on_signal(signum, frame):
        received.append(signum)
        stopped.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        old_handlers[sig] = signal.signal(sig, on_signal)
    proc, previous, reader = None, None, ProbeReader(out / "probe.jsonl")
    started = monotonic_seconds()
    deadline = started + sum(p["seconds"] for p in phases) + 20
    next_footprint = started
    exit_code = 3
    try:
        with (out / "probe.stdout.log").open("w") as stdout, (out / "probe.stderr.log").open("w") as stderr, \
             (out / "system.jsonl").open("w") as samples:
            proc = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            metadata["probe_pid"] = proc.pid
            metadata["run_status"] = "running"
            write_json(out / "metadata.json", metadata)
            while proc.poll() is None and not stopped.is_set():
                sample_start = monotonic_seconds()
                if sample_start > deadline:
                    metadata["run_status"] = "timeout"
                    break
                due = args.footprint_interval > 0 and sample_start >= next_footprint
                row = collect_system(proc.pid, footprint=due, previous=previous)
                previous = row["process_sample"]
                row["elapsed_seconds"] = sample_start - started
                row["probe"] = reader.poll()  # diagnostic only; summary uses exact phase records
                row["unix_seconds"] = time.time()
                samples.write(json.dumps(row, allow_nan=False) + "\n")
                samples.flush()
                if due:
                    next_footprint = monotonic_seconds() + args.footprint_interval
                stopped.wait(max(0, args.sample_interval - (monotonic_seconds() - sample_start)))
            if received:
                metadata["run_status"] = "cancelled"
                metadata["cancel_signal"] = received[-1]
                exit_code = 128 + received[-1]
            elif metadata["run_status"] != "timeout":
                exit_code = proc.wait(timeout=5)
                metadata["run_status"] = "complete" if exit_code == 0 else "probe_failed"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        metadata["run_status"] = "wrapper_failed"
        metadata["wrapper_error"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            terminate_owned(proc)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            reader.poll()
            metadata["probe_exit_code"] = proc.returncode if proc else None
            metadata["wall_seconds"] = monotonic_seconds() - started
            metadata["power_end"] = power_context()
            metadata["probe_log_errors"] = reader.errors
            if reader.pending.strip():
                metadata["probe_log_errors"].append({"source": "probe_log", "code": "truncated_final_record"})
            write_json(out / "metadata.json", metadata)
    print(json.dumps({"output_dir": str(out), "run_status": metadata["run_status"],
                      "probe_exit_code": metadata["probe_exit_code"], "wall_seconds": metadata["wall_seconds"]}, indent=2))
    return exit_code if exit_code >= 0 else 128 - exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=pathlib.Path)
    parser.add_argument("--video", type=pathlib.Path)
    parser.add_argument("--output-dir", type=pathlib.Path)
    parser.add_argument("--schedule", default="idle:10,play:25,pause:15,idle:10")
    parser.add_argument("--cover", action="store_true")
    parser.add_argument("--pause-on-occlusion", action="store_true")
    parser.add_argument("--sustained", action="store_true", help="raise the finite duration limit from 120 to 3600 seconds")
    parser.add_argument("--sample-interval", type=float, default=2)
    parser.add_argument("--footprint-interval", type=float, default=10, help="top MEM cadence; 0 disables")
    parser.add_argument("--warmup-seconds", type=float, default=4)
    parser.add_argument("--purpose", choices=["smoke", "pilot", "controlled"], default="pilot")
    parser.add_argument("--validate", action="store_true", help="validate inputs/probe contract without GUI or writing output")
    parser.add_argument("--capabilities", action="store_true", help="one read-only sampler snapshot; no probe or sudo")
    args = parser.parse_args(argv)
    if args.capabilities:
        print(json.dumps(capabilities(), indent=2, allow_nan=False))
        return 0
    try:
        phases = parse_schedule(args.schedule, args.sustained, args.cover)
        if not finite(args.sample_interval) or not 0.5 <= args.sample_interval <= 60:
            raise ValueError("sample interval must be 0.5...60 seconds")
        if not finite(args.footprint_interval) or (args.footprint_interval != 0 and not 2 <= args.footprint_interval <= 300):
            raise ValueError("footprint interval must be 0 (off) or 2...300 seconds")
        if not finite(args.warmup_seconds) or args.warmup_seconds < 0:
            raise ValueError("warmup seconds must be nonnegative and finite")
        if args.probe is None or not args.probe.is_file():
            raise ValueError("--probe must name an existing executable")
        if args.output_dir is None or args.output_dir.exists():
            raise ValueError("--output-dir must name a new directory")
        if any(p["state"] != "idle" for p in phases) and args.video is None:
            raise ValueError("playback schedules need --video")
        if args.video is not None and not args.video.is_file():
            raise ValueError("--video must be an existing file")
        command = build_command(args, args.output_dir.resolve() / "probe.jsonl")
        config = validate_probe(command, sum(p["seconds"] for p in phases))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    if args.validate:
        print(json.dumps(config, indent=2))
        return 0
    return run(args, phases, command, config)


if __name__ == "__main__":
    raise SystemExit(main())
