"""Bounded, read-only macOS observations for the M1 measurement harness.

All intervals use CLOCK_MONOTONIC, including time spent invoking each tool.
No sampler launches sudo, stores process arguments, or attributes system helper
work to a player. ``ps lstart`` is a local-calendar, second-resolution identity
guard, not a globally unique process identifier. Unknown identity stays None.

``top MEM`` reports physical footprint, but its human-readable units are rounded.
The converted byte count is an approximation of that displayed observation, not
an exact ledger read. A caller must bracket top with process snapshots and join
matching identities before using a footprint for a process. IORegistry counters
are global, driver-specific observations, not process GPU time or power. Registry
entry IDs identify devices within a boot; they are not cross-boot identifiers.

References: local macOS man pages ps(1), top(1) (mem), footprint(1) (Physical
Footprint). The activity watchlist is deliberately narrow: presence does not
establish cooking/recording/encoding, and absence does not prove a quiet machine.
"""

import datetime
from decimal import Decimal, ROUND_HALF_UP
import math
import os
import re
import subprocess
import time


PROCESS_NAMES = frozenset({"WindowServer", "WallpaperAgent", "VTDecoderXPCService"})
ACTIVITY_NAMES = frozenset({"TouchDesigner", "TouchPlayer", "ffmpeg", "OBS", "QuickTime Player"})
GPU_COUNTERS = frozenset({
    "Device Utilization %", "Renderer Utilization %", "Tiler Utilization %",
    "In use system memory", "Alloc system memory",
})
_MONTHS = {name: index + 1 for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}
_WEEKDAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


def monotonic_seconds():
    """Match the Swift probe's explicit POSIX CLOCK_MONOTONIC clock domain."""
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def _error(source, code, message, pid=None):
    result = {"source": source, "code": code, "message": message}
    if pid is not None:
        result["pid"] = pid
    return result


def _pid(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("pid must be a positive integer")
    return value


def _run(command, timeout, source):
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout must be finite and positive")
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout, check=False,
                                env=dict(os.environ, LC_ALL="C", LANG="C"))
    except subprocess.TimeoutExpired:
        return None, [_error(source, "timeout", "Sampler exceeded its subprocess timeout.")]
    except OSError as exc:
        # Avoid copying tool output, paths, or process arguments into public logs.
        return None, [_error(source, "os_error", "Sampler could not execute (errno=%s)." % exc.errno)]
    if result.returncode != 0:
        return None, [_error(source, "command_failed", "Sampler exited with status %s." % result.returncode)]
    return result.stdout, []


def parse_cpu_seconds(value):
    """Parse ps cumulative [[days-]hours:]minutes:seconds, including fractions."""
    days = 0
    if "-" in value:
        day_text, value = value.split("-", 1)
        if not day_text.isdigit():
            raise ValueError("invalid CPU days")
        days = int(day_text)
    parts = value.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts[:-1]):
        raise ValueError("invalid CPU time")
    if not re.fullmatch(r"\d+(?:\.\d+)?", parts[-1]):
        raise ValueError("invalid CPU seconds")
    seconds = float(parts[-1])
    if not 0 <= seconds < 60 or (len(parts) == 3 and int(parts[1]) >= 60):
        raise ValueError("invalid CPU time range")
    total = float(days * 86400)
    subtotal = 0.0
    for part in parts:
        subtotal = subtotal * 60 + float(part)
    total += subtotal
    if not math.isfinite(total):
        raise ValueError("non-finite CPU time")
    return total


def _start_time(tokens):
    if len(tokens) != 5 or tokens[0] not in _WEEKDAYS or tokens[1] not in _MONTHS:
        return None
    try:
        hour, minute, second = (int(part) for part in tokens[3].split(":"))
        datetime.datetime(int(tokens[4]), _MONTHS[tokens[1]], int(tokens[2]), hour, minute, second)
    except (ValueError, TypeError):
        return None
    # Keep the local calendar representation; inventing a timezone would be wrong.
    return " ".join(tokens)


def _ps_fields(line):
    # Preserve metrics when ps explicitly cannot provide a start time.
    short = line.split(None, 4)
    if len(short) == 5 and short[1] in {"-", "?", "N/A"}:
        return short[0], [], short[2], short[3], short[4]
    parts = line.split(None, 8)
    if len(parts) == 9:
        return parts[0], parts[1:6], parts[6], parts[7], parts[8]
    return None


def parse_processes(text, player_pid=None):
    """Return (selected process rows, errors); do not retain other process names."""
    if player_pid is not None:
        _pid(player_pid)
    rows, errors = [], []
    for line in text.splitlines():
        fields = _ps_fields(line)
        if fields is None:
            if player_pid is not None and line.split()[:1] == [str(player_pid)]:
                errors.append(_error("ps", "malformed_row", "Cannot parse selected process row.", player_pid))
            continue
        pid_text, start_tokens, cpu_text, rss_text, command = fields
        try:
            pid = _pid(int(pid_text))
        except ValueError:
            continue
        name = command.rsplit("/", 1)[-1]
        if name not in PROCESS_NAMES and pid != player_pid:
            continue
        start = _start_time(start_tokens)
        row = {"pid": pid, "name": name,
               "identity": {"pid": pid, "start_time": start} if start else None,
               "cpu_seconds": None, "rss_bytes": None}
        if start is None:
            errors.append(_error("ps", "identity_unavailable", "Start time is unavailable or invalid.", pid))
        try:
            row["cpu_seconds"] = parse_cpu_seconds(cpu_text)
        except (ValueError, OverflowError):
            errors.append(_error("ps", "cpu_unavailable", "Cumulative CPU time is invalid.", pid))
        if rss_text.isdigit():
            row["rss_bytes"] = int(rss_text) * 1024
        else:
            errors.append(_error("ps", "rss_unavailable", "RSS is unavailable or invalid.", pid))
        rows.append(row)
    if player_pid is not None and not any(row["pid"] == player_pid for row in rows):
        errors.append(_error("ps", "process_missing", "Requested player was absent from this snapshot.", player_pid))
    if not rows:
        errors.append(_error("ps", "selected_processes_missing", "No selected processes could be read from this snapshot."))
    return rows, errors


def identity_matches(left, right):
    """Unknown identities never match, even when their PIDs happen to match."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    pid = left.get("pid")
    start = left.get("start_time")
    return (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
            and isinstance(start, str) and bool(start)
            and isinstance(right.get("pid"), int) and not isinstance(right.get("pid"), bool)
            and pid == right.get("pid") and start == right.get("start_time"))


def sample_processes(player_pid=None, *, timeout=5.0):
    if player_pid is not None:
        _pid(player_pid)
    started = monotonic_seconds()
    output, errors = _run(["/bin/ps", "-axo", "pid=,lstart=,time=,rss=,comm="], timeout, "ps")
    rows, activities = [], set()
    if output is not None:
        rows, parsed_errors = parse_processes(output, player_pid)
        errors.extend(parsed_errors)
        for line in output.splitlines():
            fields = _ps_fields(line)
            if fields:
                name = fields[4].rsplit("/", 1)[-1]
                if name in ACTIVITY_NAMES:
                    activities.add(name)
    return {"started_monotonic_seconds": started,
            "finished_monotonic_seconds": monotonic_seconds(),
            "processes": rows, "activity_names": sorted(activities), "errors": errors}


def parse_gpu(text):
    """Return global counters grouped by IORegistry entry, never by process."""
    devices, errors = [], []
    device_id = device_name = None
    header = re.compile(r"\+-o\s+(.*?)\s+<class\s+[^,>]+,\s*id\s+(0x[0-9a-fA-F]+|\d+)[,>]")
    for line in text.splitlines():
        match = header.search(line)
        if match:
            device_name, device_id = match.groups()
        elif "+-o " in line:
            # Do not accidentally attach a new, unidentified entry to its predecessor.
            device_id = device_name = None
        if '"PerformanceStatistics"' not in line:
            continue
        row = {"device_id": device_id, "device_name": device_name,
               "device_id_scope": "boot" if device_id else None,
               "source": "IORegistry PerformanceStatistics"}
        pairs = dict(re.findall(r'"([^"\n]+)"\s*=\s*(-?\d+)\s*(?=[,}])', line))
        for key in sorted(GPU_COUNTERS):
            value = int(pairs[key]) if key in pairs else None
            if value is None or value < 0:
                errors.append(_error("ioreg", "counter_unavailable", "GPU counter unavailable: " + key))
                row[key] = None
            else:
                row[key] = value
        if device_id is None:
            errors.append(_error("ioreg", "device_identity_unavailable", "GPU registry entry identity is unavailable."))
        devices.append(row)
    if not devices:
        errors.append(_error("ioreg", "counters_missing", "No supported GPU PerformanceStatistics entries found."))
    return devices, errors


def sample_gpu(*, timeout=5.0):
    started = monotonic_seconds()
    output, errors = _run(["/usr/sbin/ioreg", "-r", "-c", "IOAccelerator", "-d", "1", "-l"], timeout, "ioreg")
    devices = []
    if output is not None:
        devices, parsed_errors = parse_gpu(output)
        errors.extend(parsed_errors)
    return {"started_monotonic_seconds": started,
            "finished_monotonic_seconds": monotonic_seconds(),
            "gpu_global": devices, "errors": errors}


def parse_top_memory(value):
    """Convert a rounded top MEM token (including trailing change +/-) to bytes.

    Return None for explicit missing data; reject other malformed/negative tokens.
    Unit suffixes use powers of 1024, as in macOS top's displayed memory sizes.
    """
    if value.strip().upper() in {"N/A", "NA", "-", "--", "?"}:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|[KMGTPE](?:I?B)?)?([+-])?\s*", value, re.I)
    if not match:
        raise ValueError("invalid top MEM value")
    amount, unit, _change = match.groups()
    exponent = "BKMGTPE".index((unit or "B")[0].upper())
    return int((Decimal(amount) * (1024 ** exponent)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _empty_footprint(pid):
    return {"pid": pid, "physical_footprint_bytes": None, "display_value": None,
            "source": "top MEM", "rounded": True, "identity_status": "not_checked"}


def parse_top_footprints(text, pids):
    """Return a row for every requested PID, with None when missing or invalid."""
    requested = list(dict.fromkeys(_pid(pid) for pid in pids))
    rows = {pid: _empty_footprint(pid) for pid in requested}
    errors, seen = [], set()
    columns = None
    for line in text.splitlines():
        parts = line.split()
        if "PID" in parts and "MEM" in parts:
            columns = (parts.index("PID"), parts.index("MEM"))
            continue
        if columns is None or len(parts) <= max(columns):
            continue
        pid_text = parts[columns[0]]
        if not re.fullmatch(r"\d+[+*\-]?", pid_text):
            continue
        pid = int(pid_text.rstrip("+*-"))
        if pid not in rows:
            continue
        if pid in seen:
            rows[pid] = _empty_footprint(pid)
            errors.append(_error("top", "duplicate_process", "More than one observation for requested PID.", pid))
            continue
        seen.add(pid)
        value = parts[columns[1]]
        rows[pid]["display_value"] = value
        try:
            rows[pid]["physical_footprint_bytes"] = parse_top_memory(value)
            if rows[pid]["physical_footprint_bytes"] is None:
                errors.append(_error("top", "footprint_unavailable", "top MEM is unavailable.", pid))
        except (ValueError, ArithmeticError):
            errors.append(_error("top", "invalid_footprint", "top MEM could not be parsed.", pid))
    if columns is None and requested:
        errors.append(_error("top", "header_missing", "Expected PID/MEM header was not found."))
    for pid in requested:
        if pid not in seen:
            errors.append(_error("top", "process_missing", "Requested process was absent from top output.", pid))
    return list(rows.values()), errors


def sample_footprints(pids, *, timeout=5.0):
    """Targeted top read; caller must validate identity before/after this interval."""
    requested = list(dict.fromkeys(_pid(pid) for pid in pids))
    started = monotonic_seconds()
    rows, errors = [], []
    if requested:
        command = ["/usr/bin/top", "-l", "1", "-stats", "pid,mem"]
        for pid in requested:
            command.extend(["-pid", str(pid)])
        output, errors = _run(command, timeout, "top")
        if output is None:
            rows = [_empty_footprint(pid) for pid in requested]
        else:
            rows, parsed_errors = parse_top_footprints(output, requested)
            errors.extend(parsed_errors)
    return {"started_monotonic_seconds": started,
            "finished_monotonic_seconds": monotonic_seconds(),
            "footprints": rows, "errors": errors}
