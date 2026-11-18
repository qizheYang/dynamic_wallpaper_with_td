"""Synthetic stand-in renderer for exercising the disk pipeline without TD.

It draws a horizontally scrolling periodic gradient whose phase is i/N, so the
sequence loops by construction. It is labelled 'synthetic' in render.json and in
every manifest; its output is never evidence of a TouchDesigner export.
"""
import math

from .attempt import Attempt, frame_name, write_render_report
from .common import PipelineError
from .png import encode_rgb8

_OFFSETS = (0.0, 2.1, 4.2)  # per-channel phase offsets, radians


def frame_png(width, height, index, frame_count, spatial_cycles=1, seed=0):
    phase = index / frame_count
    shift = (seed % 997) / 997
    row = bytearray(width * 3)
    for x in range(width):
        angle = 2 * math.pi * (spatial_cycles * x / width + phase + shift)
        for channel, offset in enumerate(_OFFSETS):
            row[x * 3 + channel] = round(128 + 90 * math.sin(angle + offset))
    return encode_rgb8(width, height, [row] * height)


def render(attempt_path, progress=None):
    attempt = Attempt(attempt_path)
    job = attempt.job
    if not job["template"]["id"].startswith("synthetic"):
        raise PipelineError("wrong_renderer",
                            "the synthetic renderer only renders jobs whose template.id starts with 'synthetic'")
    if job["source"]["bit_depth"] != 8:
        raise PipelineError("wrong_renderer", "the synthetic renderer writes 8-bit PNG only")
    if attempt.state()["state"] != "rendering":
        raise PipelineError("wrong_state", "attempt is not in 'rendering'")
    if any(entry.name for entry in attempt.frames_dir.iterdir() if not entry.name.startswith(".")):
        raise PipelineError("frames_not_empty", "frames/ already contains files; use a new attempt")
    cycles = job["parameters"].get("spatial_cycles", 1)
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles < 1:
        raise PipelineError("invalid_job", "parameters.spatial_cycles must be a positive integer")
    total = job["frame_count"]
    # Preconditions above never change state; from here on a failure is a failed render.
    try:
        for index in range(total):
            if index % 24 == 0 and attempt.state()["state"] != "rendering":
                raise PipelineError("cancelled", f"attempt left 'rendering' at frame {index}")
            data = frame_png(job["width"], job["height"], index, total, cycles, job["seed"])
            with open(attempt.frames_dir / frame_name(index), "xb") as handle:
                handle.write(data)
            if progress:
                progress(index + 1, total)
        write_render_report(attempt, "synthetic", total,
                            {"pattern": "scrolling periodic gradient", "phase": "i/N, i=0..N-1"})
    except BaseException as error:
        attempt.fail("render_failed", str(error) or type(error).__name__)
        raise
    return total
