#!/usr/bin/env python3
"""Summarize saved probe runs; retain raw logs as the source of truth."""
import argparse
import json
from pathlib import Path
from statistics import mean


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def rounded(value):
    return round(value, 4) if value is not None else None


def summarize(directory):
    metadata = directory / "metadata.json"
    if metadata.exists() and json.loads(metadata.read_text()).get("schema_version") == 2:
        from summarize_v2 import summarize as summarize_current
        return summarize_current(directory)
    return summarize_legacy(directory)


def summarize_legacy(directory):
    rows = read_rows(directory / "probe.jsonl")
    systems = read_rows(directory / "system.jsonl")
    phases = [r for r in rows if r.get("event") == "phase"]
    finish = next((r for r in reversed(rows) if r.get("event") == "finish"), None)
    result = {"run": directory.name,
              "finish_reason": finish.get("reason") if finish else None,
              "errors": [r for r in rows if r.get("event") == "error" or "playback_error" in r],
              "measurement_errors": [r["measurement_error"] for r in systems if "measurement_error" in r],
              "phases": []}
    for index, phase in enumerate(phases):
        start = phase["elapsed_seconds"]
        end = (phases[index + 1]["elapsed_seconds"] if index + 1 < len(phases)
               else finish["elapsed_seconds"] if finish else float("inf"))
        kept = [r for r in rows if r.get("event") == "sample"
                and r.get("phase_index") == phase["phase_index"]
                and start + 4 <= r["elapsed_seconds"] < end - 1]
        system_kept = [r for r in systems if r.get("probe")
                       and r["probe"]["phase_index"] == phase["phase_index"]
                       and start + 4 <= r["probe"]["elapsed_seconds"] < end - 3]
        row = {"index": phase["phase_index"], "phase": phase["phase"],
               "retained_samples": len(kept), "retained_system_samples": len(system_kept)}
        if len(kept) >= 2:
            elapsed = kept[-1]["elapsed_seconds"] - kept[0]["elapsed_seconds"]
            row.update({
                "cpu_percent_one_core": rounded(100 * (kept[-1]["cpu_seconds"] - kept[0]["cpu_seconds"]) / elapsed),
                "cpu_interval_seconds": rounded(elapsed),
                "footprint_mean_mib": rounded(mean(r["physical_footprint_bytes"] / 1048576 for r in kept)),
                "footprint_peak_mib": rounded(max(r["physical_footprint_bytes"] / 1048576 for r in kept)),
                "rss_mean_mib": rounded(mean(r["resident_bytes"] / 1048576 for r in kept)),
                "visible_samples": sum(r["window_visible"] for r in kept),
                "ready_samples": sum(r["layer_ready"] for r in kept),
                "rate_min": min(r["player_rate"] for r in kept),
                "rate_max": max(r["player_rate"] for r in kept),
                "loop_count_max": max(r["loop_count"] for r in kept),
                "time_control_statuses": sorted(set(r["time_control_status"] for r in kept)),
            })
        gpu = [d["Device Utilization %"] for r in system_kept
               for d in r.get("gpu_global", []) if "Device Utilization %" in d]
        if gpu:
            row["gpu_global_range_percent"] = [min(gpu), max(gpu)]
            row["gpu_global_mean_percent"] = rounded(mean(gpu))
        ws = [(r, p) for r in system_kept for p in r.get("processes", []) if p["name"] == "WindowServer"]
        if len(ws) >= 2 and ws[0][1]["pid"] == ws[-1][1]["pid"]:
            row["windowserver_cpu_percent_one_core"] = rounded(
                100 * (ws[-1][1]["cpu_seconds"] - ws[0][1]["cpu_seconds"]) /
                (ws[-1][0]["elapsed_seconds"] - ws[0][0]["elapsed_seconds"]))
        decoders = [sum(p["rss_bytes"] for p in r.get("processes", [])
                        if p["name"] == "VTDecoderXPCService") / 1048576 for r in system_kept]
        if decoders:
            row["all_vtdecoder_rss_mean_mib_not_attributed"] = rounded(mean(decoders))
        result["phases"].append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    result = {
        "method": "Schema 1 retains the original pilot algorithm. Schema 2 aligns complete sampler intervals to actual CLOCK_MONOTONIC phase boundaries, applies recorded warmup/tail guards, and rejects missing/restarted process identities.",
        "limitations": "A run summary is not whole-system acceptance. window_visible means at least partial visibility. Global GPU and shared helpers are not per-player attribution. Legacy pilots remain uncontrolled short observations.",
        "runs": [summarize(p) for p in sorted(args.results.iterdir())
                 if p.is_dir() and (p / "probe.jsonl").exists()],
    }
    (args.results / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
