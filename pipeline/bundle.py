"""Finalize a rendered attempt into a published bundle, and compare attempts.

finalize() is the only code path that publishes. It refuses unless the attempt
is in 'rendering' with a complete render report, and it publishes only after
sequence, encode, probe, full-decode, color round-trip and loop-seam checks
all pass. The bundle is assembled in the attempt directory and moved into
bundles/ with a single same-filesystem rename.
"""
import os
import pathlib
import platform
import shutil

from . import PIPELINE_VERSION
from .attempt import Attempt, frame_name, validate_sequence
from .common import PipelineError, read_json, sha256_bytes, sha256_file, utc_now, write_json_new
from .job import COLOR_CONTRACTS, job_digest
from .media import encode_args, full_decode, loop_seam, probe, round_trip, run_logged, tool_version

PARTIAL_VIDEO = "work/loop.partial.mp4"


def _repo_root(attempt):
    root = attempt.output_dir
    for _ in pathlib.PurePosixPath(attempt.job["output_dir"]).parts:
        root = root.parent
    return root


def _render_report(attempt):
    path = attempt.render_report_path
    if not path.exists():
        raise PipelineError("render_incomplete", "render.json is missing; the renderer has not finished")
    report = read_json(path)
    expected = attempt.job["frame_count"]
    if report.get("status") != "complete" or report.get("frames_written") != expected:
        raise PipelineError("render_incomplete",
                            f"render report is {report.get('status')!r} with "
                            f"{report.get('frames_written')} of {expected} frames")
    return report


def finalize(attempt_path, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    attempt = Attempt(attempt_path)
    state = attempt.state()["state"]
    if state != "rendering":
        raise PipelineError("wrong_state", f"attempt is {state!r}; only a 'rendering' attempt can be finalized")
    render = _render_report(attempt)  # refuse without changing state if not finished
    tools = {"ffmpeg": tool_version(ffmpeg), "ffprobe": tool_version(ffprobe),
             "python": platform.python_version(), "pipeline": PIPELINE_VERSION}
    if attempt.bundle_path.exists():
        raise PipelineError("bundle_exists", f"{attempt.bundle_path} already exists")

    attempt.transition("rendering", "encoding", step="sequence_check")
    try:
        return _finalize(attempt, render, tools, ffmpeg, ffprobe)
    except KeyboardInterrupt:
        if attempt.state()["state"] == "complete":
            raise PipelineError("cancelled", f"interrupted after publication; complete bundle: "
                                f"{attempt.bundle_path}") from None
        attempt.fail("cancelled", "interrupted before publication")
        raise PipelineError("cancelled", "interrupted; attempt marked failed, nothing published") from None
    except PipelineError as error:
        attempt.fail(error.code, error.message)
        raise
    except Exception as error:
        attempt.fail("internal_error", repr(error))
        raise


def _finalize(attempt, render, tools, ffmpeg, ffprobe):
    job, work = attempt.job, attempt.work_dir
    sequence = validate_sequence(attempt)
    write_json_new(work / "sequence.json", sequence)
    if not sequence["ok"]:
        raise PipelineError("invalid_sequence", "; ".join(sequence["errors"])[:2000])

    args = encode_args(ffmpeg, job, PARTIAL_VIDEO)
    code = run_logged(args, attempt.path, work / "encode.log")
    if code != 0:
        tail = (work / "encode.log").read_text(errors="replace")[-1000:].strip()
        raise PipelineError("encode_failed", f"ffmpeg exited {code}: {tail}")

    attempt.transition("encoding", "validating")
    checks = {
        "probe": probe(ffprobe, job, PARTIAL_VIDEO, attempt.path),
        "decode": full_decode(ffmpeg, PARTIAL_VIDEO, attempt.path),
        "round_trip": round_trip(ffmpeg, job, PARTIAL_VIDEO, attempt.path,
                                 sorted({0, job["frame_count"] // 2})),
        "loop_seam": loop_seam(ffmpeg, job, attempt.path),
    }
    write_json_new(work / "validation.json", checks)
    failed = [name for name, result in checks.items() if not result["ok"]]
    if failed:
        details = []
        for item in checks["probe"]["checks"]:
            if not item["ok"]:
                details.append(f"{item['check']}: expected {item['expected']!r}, got {item['actual']!r}")
        if "loop_seam" in failed:
            details.append(f"loop seam: {checks['loop_seam']['verdict']}")
        raise PipelineError("validation_failed", f"failed checks: {', '.join(failed)}. "
                            + "; ".join(details)[:1500])

    staging = attempt.path / "bundle.partial"
    staging.mkdir()
    os.replace(attempt.path / PARTIAL_VIDEO, staging / "loop.mp4")
    shutil.copyfile(attempt.frames_dir / frame_name(0), staging / "cover.png")
    listing = "".join(f"{digest}  {frame_name(index)}\n"
                      for index, digest in enumerate(sequence["frame_sha256"]))
    (staging / "frames.sha256.txt").write_text(listing, encoding="utf-8")
    contract = COLOR_CONTRACTS[job["source"]["color"]]
    template = dict(job["template"])
    if "path" in template and (_repo_root(attempt) / template["path"]).is_file():
        template["sha256"] = sha256_file(_repo_root(attempt) / template["path"])
    not_verified = [
        "Color appearance of a real export compared between TD and AVPlayerLayer",
        "Loop smoothness observed in actual AVPlayer playback",
        "Whole-system playback resource cost (M1/M2 gates)",
    ]
    if render["renderer"] != "touchdesigner":
        not_verified.insert(0, f"Not a TouchDesigner export (renderer: {render['renderer']})")
    manifest = {
        "manifest_version": 1,
        "job_id": job["job_id"],
        "attempt_id": attempt.attempt_id,
        "job_sha256": job_digest(job),
        "job": job,
        "template": template,
        "renderer": render,
        "video": {
            "file": "loop.mp4", "sha256": sha256_file(staging / "loop.mp4"),
            "bytes": (staging / "loop.mp4").stat().st_size, "codec": "h264", "pix_fmt": "yuv420p",
            "width": job["width"], "height": job["height"], "frame_rate": f"{job['fps']}/1",
            "frame_count": job["frame_count"], "duration_seconds": job["frame_count"] / job["fps"],
            "audio": False, "color_tags": contract["expected_tags"],
            "encode_args": ["ffmpeg"] + args[1:],
        },
        "cover": {"file": "cover.png", "sha256": sha256_file(staging / "cover.png"),
                  "source_frame": 0, "color_contract": job["source"]["color"]},
        "source": {
            "format": "png", "bit_depth": job["source"]["bit_depth"],
            "color_contract": job["source"]["color"], "contract_description": contract["description"],
            "png_color_type": sequence["png_color_type"], "alpha_present": sequence["alpha_present"],
            "png_color_chunks": sequence["png_color_chunks"],
            "frames_file": "frames.sha256.txt", "frames_digest": sha256_bytes(listing.encode("utf-8")),
        },
        "tools": tools,
        "validation": {
            "sequence": {key: sequence[key] for key in ("ok", "expected_frames", "found_frames")},
            "probe": checks["probe"]["checks"], "decode": checks["decode"],
            "round_trip": checks["round_trip"], "loop_seam": checks["loop_seam"],
        },
        "not_verified": not_verified,
        "published_utc": utc_now(),
    }
    write_json_new(staging / "manifest.json", manifest)
    relative = os.path.relpath(attempt.bundle_path, _repo_root(attempt))
    attempt.publish(staging, relative)
    return {"bundle": str(attempt.bundle_path), "manifest": manifest}


def compare_attempts(first, second):
    """Compare two completed attempts of the same job: source frames and video."""
    a, b = Attempt(first), Attempt(second)
    bundles = []
    for attempt in (a, b):
        if attempt.state()["state"] != "complete":
            raise PipelineError("wrong_state", f"{attempt.attempt_id} is not complete")
        bundles.append(read_json(attempt.bundle_path / "manifest.json"))
    lists = [(attempt.bundle_path / "frames.sha256.txt").read_text().splitlines() for attempt in (a, b)]
    differing = [index for index, (x, y) in enumerate(zip(lists[0], lists[1])) if x != y]
    return {
        "same_job": bundles[0]["job_sha256"] == bundles[1]["job_sha256"],
        "frame_count": [len(lists[0]), len(lists[1])],
        "identical_source_frames": len(lists[0]) == len(lists[1]) and not differing,
        "differing_frames": differing[:50],
        "differing_frame_count": len(differing),
        "identical_video": bundles[0]["video"]["sha256"] == bundles[1]["video"]["sha256"],
        "tools_equal": bundles[0]["tools"] == bundles[1]["tools"],
        "renderers": [bundles[0]["renderer"]["renderer"], bundles[1]["renderer"]["renderer"]],
    }
