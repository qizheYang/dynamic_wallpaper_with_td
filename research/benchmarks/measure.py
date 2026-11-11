#!/usr/bin/env python3
"""Bounded native playback probe + read-only system counters. No system changes."""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import time


def cpu_seconds(value):
    days = 0
    if "-" in value:
        day, value = value.split("-", 1)
        days = int(day)
    total = 0.0
    for part in value.split(":"):
        total = total * 60 + float(part)
    return days * 86400 + total


def process_snapshot():
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,time=,rss=,comm="],
        capture_output=True, text=True, timeout=5, check=True,
    )
    wanted = {"WindowServer", "VTDecoderXPCService", "WallpaperAgent"}
    rows = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4:
            continue
        name = parts[3].rsplit("/", 1)[-1]
        if name in wanted:
            rows.append({"pid": int(parts[0]), "name": name,
                         "cpu_seconds": cpu_seconds(parts[1]),
                         "rss_bytes": int(parts[2]) * 1024})
    return rows


def gpu_snapshot():
    result = subprocess.run(
        ["/usr/sbin/ioreg", "-r", "-c", "IOAccelerator", "-d", "1", "-l"],
        capture_output=True, text=True, timeout=5, check=True,
    )
    wanted = {"Device Utilization %", "Renderer Utilization %",
              "Tiler Utilization %", "In use system memory", "Alloc system memory"}
    devices = []
    for line in result.stdout.splitlines():
        if '"PerformanceStatistics"' in line:
            pairs = re.findall(r'"([^"\n]+)"=(-?\d+)', line)
            devices.append({key: int(value) for key, value in pairs if key in wanted})
    return devices


def last_probe_sample(path):
    if not path.exists():
        return None
    for line in reversed(path.read_text().splitlines()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") == "sample":
            return {key: row.get(key) for key in
                    ("elapsed_seconds", "phase", "phase_index", "layer_ready",
                     "window_visible", "player_rate", "media_seconds")}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=pathlib.Path, required=True)
    parser.add_argument("--video", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--schedule", default="idle:10,play:25,pause:15,idle:10")
    parser.add_argument("--cover", action="store_true")
    parser.add_argument("--pause-on-occlusion", action="store_true")
    args = parser.parse_args()
    segments = [segment.split(":") for segment in args.schedule.split(",")]
    duration = sum(float(segment[1]) for segment in segments)
    if not 0 < duration <= 120 or any(float(s[1]) <= 0 for s in segments):
        parser.error("schedule duration must be 0 < duration <= 120 seconds")
    for path in (args.probe, args.video):
        if not path.is_file():
            parser.error(f"file not found: {path}")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    log = out / "probe.jsonl"
    command = [str(args.probe.resolve()), "--video", str(args.video.resolve()),
               "--output", str(log), "--schedule", args.schedule]
    if args.cover:
        command.append("--cover")
    if args.pause_on_occlusion:
        command.append("--pause-on-occlusion")
    metadata = {"command": command, "started_unix_seconds": time.time(),
                "probe_sha256": hashlib.sha256(args.probe.read_bytes()).hexdigest(),
                "video_sha256": hashlib.sha256(args.video.read_bytes()).hexdigest(),
                "sample_interval_seconds": 2,
                "notes": ["GPU counters are global, driver-specific snapshots, not power.",
                          "VTDecoder processes are system-wide; ownership is not attributed.",
                          "CPU uses deltas of cumulative process CPU time, 100%=one core.",
                          "RSS is not physical footprint; shared memory may be double-counted."]}
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    proc = None
    previous, previous_time = {}, None
    with (out / "probe.stdout.log").open("w") as stdout, \
         (out / "probe.stderr.log").open("w") as stderr, \
         (out / "system.jsonl").open("w") as samples:
        started = time.monotonic()
        try:
            proc = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            while proc.poll() is None:
                sample_start = time.monotonic()
                if sample_start - started > duration + 20:
                    raise TimeoutError("probe exceeded its schedule plus 20 seconds")
                row = {"elapsed_seconds": sample_start - started,
                       "unix_seconds": time.time(), "probe": last_probe_sample(log)}
                try:
                    processes = process_snapshot()
                    now = time.monotonic()
                    for item in processes:
                        old = previous.get(item["pid"])
                        item["cpu_percent_one_core"] = (
                            100 * max(0, item["cpu_seconds"] - old["cpu_seconds"]) /
                            (now - previous_time)
                            if old and previous_time is not None else None)
                    row["processes"] = processes
                    previous = {item["pid"]: item for item in processes}
                    previous_time = now
                    row["gpu_global"] = gpu_snapshot()
                except (OSError, subprocess.SubprocessError) as error:
                    row["measurement_error"] = str(error)
                samples.write(json.dumps(row) + "\n")
                samples.flush()
                time.sleep(max(0, 2 - (time.monotonic() - sample_start)))
            metadata["probe_exit_code"] = proc.returncode
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            metadata["wall_seconds"] = time.monotonic() - started
            (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({"output_dir": str(out), **metadata}, indent=2))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
