"""FFmpeg/ffprobe operations: encode, probe, full decode, round trip, loop seam.

Every subprocess gets an argument list (never a shell string). Commands run
with cwd set to the attempt directory so recorded arguments contain only
attempt-relative paths.
"""
import json
import operator
import statistics
import subprocess

from .common import PipelineError
from .job import COLOR_CONTRACTS

FRAME_PATTERN = "frames/frame_%06d.png"


def tool_version(executable):
    try:
        result = subprocess.run([executable, "-hide_banner", "-version"], capture_output=True,
                                text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        raise PipelineError("tool_unavailable", f"cannot run {executable}: {error}") from None
    if result.returncode != 0 or not result.stdout:
        raise PipelineError("tool_unavailable", f"{executable} -version failed")
    return result.stdout.splitlines()[0].strip()


def encode_args(ffmpeg, job, output):
    contract = COLOR_CONTRACTS[job["source"]["color"]]
    encode = job["encode"]
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-n", "-loglevel", "error",
        "-framerate", str(job["fps"]), "-start_number", "0", "-i", FRAME_PATTERN,
        "-frames:v", str(job["frame_count"]), "-an",
        "-vf", contract["filter"],
        "-c:v", "libx264", "-preset", encode["preset"], "-crf", str(encode["crf"]),
        "-pix_fmt", "yuv420p",
        "-map_metadata", "-1", "-fflags", "+bitexact",
        "-movflags", "+faststart",
        output,
    ]


def run_logged(args, cwd, log_path):
    """Run a command; on interruption or any error, stop the child before re-raising."""
    with open(log_path, "wb") as log:
        process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=log)
        try:
            return process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise


def _capture(args, cwd, timeout=600):
    result = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout,
                            stdin=subprocess.DEVNULL)
    return result.returncode, result.stdout, result.stderr.decode("utf-8", "replace").strip()


def probe(ffprobe, job, video, cwd):
    args = [ffprobe, "-v", "error", "-count_frames",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,pix_fmt,r_frame_rate,avg_frame_rate,"
            "nb_read_frames,color_space,color_primaries,color_transfer,color_range"
            ":format=duration,format_name",
            "-of", "json", video]
    code, out, err = _capture(args, cwd)
    if code != 0:
        raise PipelineError("probe_failed", err or f"ffprobe exited {code}")
    data = json.loads(out)
    streams = data.get("streams", [])
    fps = job["fps"]
    checks = [("stream_count", 1, len(streams))]
    stream = streams[0] if streams else {}
    expected = {"codec_type": "video", "codec_name": "h264", "width": job["width"],
                "height": job["height"], "pix_fmt": "yuv420p", "r_frame_rate": f"{fps}/1",
                "avg_frame_rate": f"{fps}/1", "nb_read_frames": str(job["frame_count"])}
    expected.update(COLOR_CONTRACTS[job["source"]["color"]]["expected_tags"])
    checks += [(key, value, stream.get(key)) for key, value in expected.items()]
    duration = float(data.get("format", {}).get("duration", "nan"))
    target = job["frame_count"] / fps
    results = [{"check": name, "expected": want, "actual": got, "ok": want == got}
               for name, want, got in checks]
    results.append({"check": "duration_seconds", "expected": target, "actual": duration,
                    "ok": abs(duration - target) <= 0.5 / fps})
    return {"ok": all(item["ok"] for item in results), "checks": results, "raw": data}


def full_decode(ffmpeg, video, cwd):
    code, _, err = _capture([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror",
                             "-i", video, "-f", "null", "-"], cwd)
    return {"ok": code == 0 and not err, "exit_code": code, "stderr": err[:2000]}


def _raw_rgb(args, cwd, expected_bytes):
    code, out, err = _capture(args, cwd)
    if code != 0 or len(out) != expected_bytes:
        raise PipelineError("decode_failed",
                            f"expected {expected_bytes} bytes, got {len(out)} (exit {code}): {err[:300]}")
    return out


def _difference(a, b):
    deltas = list(map(abs, map(operator.sub, a, b)))
    return sum(deltas) / len(deltas), max(deltas)


def round_trip(ffmpeg, job, video, cwd, indices):
    """Decode chosen video frames back to RGB and compare with their source PNGs."""
    contract = COLOR_CONTRACTS[job["source"]["color"]]
    size = job["width"] * job["height"] * 3
    limit = job["validation"]["roundtrip_mean_abs_max"]
    frames = []
    for index in indices:
        decoded = _raw_rgb([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", video,
                            "-vf", f"select=eq(n\\,{index}),{contract['decode_filter']}",
                            "-frames:v", "1", "-f", "rawvideo", "-"], cwd, size)
        source = _raw_rgb([ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
                           "-i", f"frames/frame_{index:06d}.png", "-vf", "format=rgb24",
                           "-frames:v", "1", "-f", "rawvideo", "-"], cwd, size)
        mean, peak = _difference(decoded, source)
        frames.append({"frame": index, "mean_abs_error": round(mean, 4), "max_abs_error": peak,
                       "ok": mean <= limit})
    return {"ok": all(item["ok"] for item in frames), "limit_mean_abs": limit,
            "decode_filter": contract["decode_filter"], "frames": frames}


def analysis_size(width, height, target=96):
    w = min(width, target) // 2 * 2
    h = max(2, round(w * height / width / 2) * 2)
    return w, h


def loop_seam(ffmpeg, job, cwd):
    """Compare the last->first change with ordinary adjacent-frame changes."""
    n = job["frame_count"]
    w, h = analysis_size(job["width"], job["height"])
    frame_bytes = w * h * 3
    raw = _raw_rgb([ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
                    "-framerate", str(job["fps"]), "-start_number", "0", "-i", FRAME_PATTERN,
                    "-frames:v", str(n), "-vf", f"scale={w}:{h}:flags=area,format=rgb24",
                    "-f", "rawvideo", "-"], cwd, n * frame_bytes)
    frames = [raw[i * frame_bytes:(i + 1) * frame_bytes] for i in range(n)]
    adjacent = [_difference(frames[i], frames[i + 1])[0] for i in range(n - 1)]
    seam = _difference(frames[-1], frames[0])[0]
    median = statistics.median(adjacent)
    largest = max(adjacent)
    zero_share = sum(1 for value in adjacent if value == 0) / len(adjacent)
    ratio_max = job["validation"]["seam_ratio_max"]
    if largest == 0 and seam == 0:
        verdict, ok = "static", True
    elif seam == 0 and zero_share < 0.05:
        verdict, ok = "duplicate_endpoint", False
    elif seam > ratio_max * median and seam > largest:
        verdict, ok = "seam_discontinuity", False
    else:
        verdict, ok = "continuous", True
    return {"ok": ok, "verdict": verdict, "analysis_size": [w, h],
            "seam_mean_abs": round(seam, 4), "adjacent_median": round(median, 4),
            "adjacent_max": round(largest, 4), "adjacent_zero_share": round(zero_share, 4),
            "seam_to_median": round(seam / median, 4) if median else None,
            "seam_ratio_max": ratio_max}
