"""Checks for debug renders of the TD template (see td/shaders/soft_gradient.frag).

verify_index: every saved frame k must carry the binary index k in its bottom
strip. This catches cook-timing errors (a frame saved before the new index was
cooked) that a loop-seam check cannot see. It also reports where the strip was
found; 'bottom' means the PNG is upright.

verify_chart: frame 0 of a chart render must hold the known code values, which
shows whether TD writes the shader's 8-bit values unchanged into the PNG.

Both read source PNGs through FFmpeg (rgb24/gray decode, no color conversion).
"""
from .attempt import Attempt
from .media import FRAME_PATTERN, _raw_rgb

INDEX_BITS = 12          # must match the shader
INDEX_STRIP = 1 / 24     # bottom fraction of the height
SAMPLES_PER_CELL = 4
CHART = [(0.0, 0.0, 0.0), (0.25, 0.25, 0.25), (0.5, 0.5, 0.5), (0.75, 0.75, 0.75),
         (1.0, 1.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]


def strip_rows(height):
    return sum(1 for y in range(height) if (y + 0.5) / height < INDEX_STRIP)


def _cells(samples, cells):
    """Average the two inner samples of each cell (edges are excluded)."""
    out = []
    for cell in range(cells):
        base = cell * SAMPLES_PER_CELL
        out.append((samples[base + 1] + samples[base + 2]) / 2)
    return out


def _read_strips(attempt, at_bottom):
    job = attempt.job
    w, h, n = job["width"], job["height"], job["frame_count"]
    rows = strip_rows(h)
    band = max(1, rows // 2)
    top = (h - rows + (rows - band) // 2) if at_bottom else (rows - band) // 2
    width = INDEX_BITS * SAMPLES_PER_CELL
    raw = _raw_rgb(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
                    "-framerate", str(job["fps"]), "-start_number", "0", "-i", FRAME_PATTERN,
                    "-frames:v", str(n),
                    "-vf", f"crop={w}:{band}:0:{top},scale={width}:1:flags=area,format=gray",
                    "-f", "rawvideo", "-"], attempt.path, n * width)
    return [_cells(raw[i * width:(i + 1) * width], INDEX_BITS) for i in range(n)]


def _decode(cells):
    if any(64 <= value <= 192 for value in cells):
        return None  # not a clean black/white strip
    return int("".join("1" if value > 192 else "0" for value in cells), 2)


def verify_index(attempt_path):
    attempt = Attempt(attempt_path)
    n = attempt.job["frame_count"]
    if n > 2 ** INDEX_BITS:
        return {"ok": False, "error": f"frame_count {n} exceeds {2 ** INDEX_BITS} indexable frames"}
    # A dark area decodes as index 0, so pick the location whose indices match 0..N-1 best.
    candidates = {}
    for location in ("bottom", "top"):
        decoded = [_decode(cells) for cells in _read_strips(attempt, location == "bottom")]
        candidates[location] = (sum(value == i for i, value in enumerate(decoded)), decoded)
    location = max(candidates, key=lambda key: candidates[key][0])
    matches, decoded = candidates[location]
    if matches == 0:
        return {"ok": False, "error": "no frame carries its own index at the bottom or top"}
    unreadable = [i for i, value in enumerate(decoded) if value is None]
    mismatched = [{"frame": i, "index": value} for i, value in enumerate(decoded)
                  if value is not None and value != i]
    return {"ok": not unreadable and not mismatched and location == "bottom",
            "strip_location": location, "upright": location == "bottom", "frames": n,
            "unreadable": unreadable[:50], "mismatched": mismatched[:50],
            "mismatched_count": len(mismatched)}


def verify_chart(attempt_path, tolerance=1):
    attempt = Attempt(attempt_path)
    job = attempt.job
    w, h = job["width"], job["height"]
    width = len(CHART) * SAMPLES_PER_CELL
    raw = _raw_rgb(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
                    "-i", "frames/frame_000000.png",
                    "-vf", f"crop={w}:{max(2, h // 3)}:0:{h // 3},scale={width}:1:flags=area,format=rgb24",
                    "-frames:v", "1", "-f", "rawvideo", "-"], attempt.path, width * 3)
    bars = []
    for bar, expected in enumerate(CHART):
        actual = []
        for channel in range(3):
            samples = [raw[(bar * SAMPLES_PER_CELL + s) * 3 + channel] for s in (1, 2)]
            actual.append(round(sum(samples) / 2, 1))
        want = [round(value * 255) for value in expected]
        bars.append({"bar": bar, "expected": want, "actual": actual,
                     "ok": all(abs(a - b) <= tolerance for a, b in zip(actual, want))})
    return {"ok": all(bar["ok"] for bar in bars), "tolerance": tolerance, "bars": bars}
