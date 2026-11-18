"""Schema-2 analysis: align sampler intervals to actual CLOCK_MONOTONIC phases.

This reports observations and data-quality failures, never a whole-system pass.
Legacy pilot calculations remain in summarize.py and are not rewritten.
"""
import json
import math
from statistics import mean

from system_metrics import identity_matches


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def read_rows(path):
    rows, errors = [], []
    if not path.exists():
        return rows, [f"missing {path.name}"]
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError()
            rows.append(value)
        except ValueError:
            errors.append(f"invalid {path.name} record {number}")
    return rows, errors


def contained(sample, start, end):
    a, b = sample.get("started_monotonic_seconds"), sample.get("finished_monotonic_seconds")
    return finite(a) and finite(b) and start <= a <= b < end


def stats(values):
    values = [v for v in values if finite(v)]
    return ({"count": len(values), "mean": round(mean(values), 6),
             "min": min(values), "max": max(values)} if values else None)


def process_cpu(samples, name):
    """No delta across missing/changed identity or cumulative-counter regression."""
    if len(samples) < 2:
        return {"status": "inconclusive", "reason": "fewer_than_two_samples"}
    previous, total_cpu, total_time, problems = None, 0.0, 0.0, []
    for sample in samples:
        matches = [p for p in sample.get("processes", []) if p.get("name") == name]
        current = matches[0] if len(matches) == 1 else None
        if current is None:
            problems.append("process_missing_or_not_unique")
            previous = None
            continue
        timestamp = sample.get("finished_monotonic_seconds")
        if not finite(timestamp) or not finite(current.get("cpu_seconds")):
            problems.append("counter_or_timestamp_unavailable")
            previous = None
            continue
        if previous:
            old, old_time = previous
            if not identity_matches(old.get("identity"), current.get("identity")):
                problems.append("process_identity_changed_or_unknown")
            elif current["cpu_seconds"] < old["cpu_seconds"]:
                problems.append("cumulative_cpu_regressed")
            elif timestamp <= old_time:
                problems.append("non_increasing_timestamp")
            else:
                total_cpu += current["cpu_seconds"] - old["cpu_seconds"]
                total_time += timestamp - old_time
        previous = (current, timestamp)
    if problems or not total_time:
        return {"status": "inconclusive", "reasons": sorted(set(problems)) or ["no_valid_interval"]}
    return {"status": "observed", "cpu_percent_one_core": round(100 * total_cpu / total_time, 6),
            "interval_seconds": total_time}


def gpu_stats(samples):
    """Keep device identity separate; do not average different GPUs together."""
    groups, errors = {}, []
    expected = None
    for sample in samples:
        devices = sample.get("gpu_global", [])
        identities = set()
        for index, device in enumerate(devices):
            identity = device.get("device_id")
            value = device.get("Device Utilization %")
            if identity is None or not finite(value) or not 0 <= value <= 100:
                errors.append("device_identity_or_counter_unavailable")
                continue
            identity = str(identity)
            if identity in identities:
                errors.append("duplicate_device_identity")
            identities.add(identity)
            groups.setdefault(identity, []).append(value)
        if not identities:
            errors.append("gpu_counter_missing")
        if expected is not None and identities != expected:
            errors.append("gpu_device_set_changed")
        expected = identities
    return {"status": "inconclusive" if errors or not groups else "observed_global",
            "devices": {key: stats(values) for key, values in groups.items()},
            "reasons": sorted(set(errors))}


def summarize(directory):
    rows, errors = read_rows(directory / "probe.jsonl")
    systems, system_errors = read_rows(directory / "system.jsonl")
    errors += system_errors
    try:
        metadata = json.loads((directory / "metadata.json").read_text())
    except (OSError, ValueError):
        metadata = {}
        errors.append("metadata_missing_or_invalid")
    if not isinstance(metadata, dict):
        metadata = {}
        errors.append("metadata_not_an_object")
    for system in systems:
        for key, rows_key in (("process_sample", "processes"), ("gpu_sample", "gpu_global"),
                              ("footprint_sample", "footprints"), ("footprint_identity_check", "processes")):
            if key not in system:
                continue
            if not isinstance(system[key], dict):
                errors.append(f"invalid_sample_object:{key}")
                system[key] = {}
            observed = system[key]
            for list_key in (rows_key, "errors"):
                values = observed.get(list_key, [])
                if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
                    errors.append(f"invalid_sample_list:{key}:{list_key}")
                    observed[list_key] = []
    phases = [r for r in rows if r.get("event") == "phase"]
    finish = next((r for r in reversed(rows) if r.get("event") == "finish"), None)
    errors += ["probe_error" for r in rows if r.get("event") == "error" or r.get("playback_error")]
    for system in systems:
        for key in ("process_sample", "gpu_sample", "footprint_sample", "footprint_identity_check"):
            for error in system.get(key, {}).get("errors", []):
                errors.append(f"{key}:{error.get('code', 'error')}")
    errors += [f"probe_log:{e.get('code','error')}" for e in metadata.get("probe_log_errors", [])]
    warmup, tail = metadata.get("warmup_seconds", 4), metadata.get("tail_guard_seconds", 1)
    if not finite(warmup) or warmup < 0 or not finite(tail) or tail < 0:
        errors.append("invalid_analysis_window")
        warmup, tail = 4, 1
    result = {"schema_version": 2, "run": directory.name, "purpose": metadata.get("purpose", "unknown"),
              "run_status": metadata.get("run_status", "unknown"),
              "finish_reason": finish.get("reason") if finish else None,
              "errors": sorted(set(errors)), "phases": [],
              "sampler_seconds": stats([r.get("sampler_seconds") for r in systems]),
              "activity_names": sorted({name for r in systems for name in r.get("process_sample", {}).get("activity_names", [])}),
              "lifecycle_events": [{k: r[k] for k in ("event", "elapsed_seconds", "pause_reasons", "session_active") if k in r}
                                   for r in rows if r.get("event") in
                                   {"occlusion", "display_sleep", "display_wake", "session_resigned_active", "session_became_active"}],
              "acceptance": {"status": "inconclusive", "reasons": [
                  "A run summary is not the repeated controlled M2 acceptance protocol.",
                  "System services are shared; decoder attribution is not established.",
                  "Full visibility, baseline stability and sampler overhead require separate evidence.",
                  "No battery-power, actual-lock, or sustained-release conclusion follows from this report."]}}
    if not phases:
        result["errors"].append("no_phase_records")
    for index, phase in enumerate(phases):
        boundary = phase.get("phase_start_monotonic_seconds", phase.get("monotonic_seconds"))
        next_boundary = (phases[index + 1].get("phase_start_monotonic_seconds", phases[index + 1].get("monotonic_seconds")) if index + 1 < len(phases)
                         else finish.get("monotonic_seconds") if finish else None)
        item = {"index": phase.get("phase_index"), "phase": phase.get("phase"), "quality": []}
        if not finite(boundary) or not finite(next_boundary) or next_boundary <= boundary:
            item["quality"].append("missing_or_invalid_phase_boundary")
            result["phases"].append(item)
            continue
        start, end = boundary + warmup, next_boundary - tail
        item["window"] = {"start_monotonic_seconds": start, "end_monotonic_seconds": end,
                          "seconds": max(0, end - start), "warmup_seconds": warmup, "tail_guard_seconds": tail}
        probes = [r for r in rows if r.get("event") == "sample"
                  and r.get("phase_index") == phase.get("phase_index")
                  and finite(r.get("monotonic_seconds")) and start <= r["monotonic_seconds"] < end]
        process_samples = [r["process_sample"] for r in systems if contained(r.get("process_sample", {}), start, end)]
        gpu_samples = [r["gpu_sample"] for r in systems if contained(r.get("gpu_sample", {}), start, end)]
        footprints = [r["footprint_sample"] for r in systems if contained(r.get("footprint_sample", {}), start, end)]
        item.update({"probe_samples": len(probes), "process_samples": len(process_samples),
                     "gpu_samples": len(gpu_samples), "footprint_samples": len(footprints),
                     "windowserver_cpu": process_cpu(process_samples, "WindowServer"),
                     "gpu_global": gpu_stats(gpu_samples)})
        item["player_footprint_mib"] = stats([r["physical_footprint_bytes"] / 1048576 for r in probes if finite(r.get("physical_footprint_bytes"))])
        item["player_rss_mib"] = stats([r["resident_bytes"] / 1048576 for r in probes if finite(r.get("resident_bytes"))])
        if any(not finite(r.get("physical_footprint_bytes")) for r in probes):
            item["quality"].append("player_footprint_samples_missing")
        if len(probes) >= 2:
            counters = [r.get("cpu_seconds") for r in probes]
            times = [r["monotonic_seconds"] for r in probes]
            if (all(finite(c) for c in counters) and all(a <= b for a, b in zip(counters, counters[1:]))
                    and all(a < b for a, b in zip(times, times[1:]))):
                item["player_cpu_percent_one_core"] = round(100 * (counters[-1] - counters[0]) / (times[-1] - times[0]), 6)
            else:
                item["quality"].append("invalid_player_cpu_counter")
        else:
            item["quality"].append("insufficient_player_samples")
        memories = []
        for sample in footprints:
            for memory in sample.get("footprints", []):
                process = next((p for ps in process_samples for p in ps.get("processes", [])
                                if p.get("name") == "WindowServer" and identity_matches(p.get("identity"), memory.get("identity"))), None)
                if process and memory.get("identity_status") == "matched_before_after":
                    value = memory.get("physical_footprint_bytes")
                    if finite(value):
                        memories.append(value / 1048576)
        item["windowserver_footprint_mib_rounded"] = stats(memories)
        if not memories:
            item["quality"].append("windowserver_footprint_unavailable")
        if not process_samples:
            item["quality"].append("no_aligned_process_samples")
        item["visible_samples"] = sum(r.get("window_visible") is True for r in probes)
        item["ready_samples"] = sum(r.get("layer_ready") is True for r in probes)
        item["pause_reasons_observed"] = sorted({reason for r in probes for reason in r.get("pause_reasons", [])})
        if item["phase"] in {"play", "uncover"}:
            moving = any((finite(a.get("media_seconds")) and finite(b.get("media_seconds"))
                          and b["media_seconds"] > a["media_seconds"])
                         or b.get("loop_count", 0) > a.get("loop_count", 0) for a, b in zip(probes, probes[1:]))
            item["playback_observed"] = bool(moving and any(r.get("layer_ready") and r.get("player_rate", 0) > 0 for r in probes))
            if not item["playback_observed"]:
                item["quality"].append("playback_not_confirmed")
        result["phases"].append(item)
    return result
