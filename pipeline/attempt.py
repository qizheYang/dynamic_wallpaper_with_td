"""Render attempts: unique directories, the job state machine, and sequence checks.

Layout under the job's output_dir (all on one filesystem):

    attempts/<attempt_id>/
        job.json        normalized job, written once
        state.json      current state and history (atomic rewrites)
        frames/         frame_000000.png ... frame_{N-1}.png, written by a renderer
        render.json     renderer's completion report, written last by the renderer
        work/           encoder output, logs, validation reports
    bundles/<attempt_id>/   published only after every check passes
"""
import datetime
import fcntl
import os
import pathlib
import re
import secrets
from contextlib import contextmanager

from .common import PipelineError, read_json, sha256_file, utc_now, write_json_atomic, write_json_new
from .job import validate_job
from .png import PNGError, inspect_png

STATES = ("queued", "preparing", "rendering", "encoding", "validating", "complete", "failed")
_NEXT = {"queued": "preparing", "preparing": "rendering", "rendering": "encoding",
         "encoding": "validating", "validating": "complete"}
TERMINAL = ("complete", "failed")
FRAME_NAME = re.compile(r"^frame_(\d{6})\.png$")
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def frame_name(index):
    return f"frame_{index:06d}.png"


def new_attempt_id():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


class Attempt:
    def __init__(self, path):
        self.path = pathlib.Path(path).resolve()
        self.job = validate_job(read_json(self.path / "job.json"))
        self.attempt_id = self.path.name
        self.output_dir = self.path.parent.parent

    frames_dir = property(lambda self: self.path / "frames")
    work_dir = property(lambda self: self.path / "work")
    render_report_path = property(lambda self: self.path / "render.json")
    bundle_path = property(lambda self: self.output_dir / "bundles" / self.attempt_id)

    def state(self):
        return read_json(self.path / "state.json")

    @contextmanager
    def _state_lock(self):
        """Serialize state changes and publication across local processes."""
        with open(self.path / ".state.lock", "a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def transition(self, expected, new, **detail):
        """Move expected -> new; refuses if the stored state is not `expected`."""
        with self._state_lock():
            return self._transition_locked(expected, new, **detail)

    def _transition_locked(self, expected, new, **detail):
        """The caller must hold _state_lock for the entire read/update/write."""
        record = self.state()
        current = record["state"]
        if current != expected:
            raise PipelineError("wrong_state", f"attempt is {current!r}, expected {expected!r}")
        if new != "failed" and _NEXT.get(current) != new:
            raise PipelineError("bad_transition", f"{current} -> {new} is not allowed")
        if new == "failed" and current in TERMINAL:
            raise PipelineError("bad_transition", f"attempt is already {current}")
        record["state"] = new
        record["history"].append({"state": new, "at_utc": utc_now(), **detail})
        if new == "failed":
            record["failure"] = {"stage": current, **detail}
        if new == "complete":
            record["bundle"] = detail.get("bundle")
        write_json_atomic(self.path / "state.json", record)
        return record

    def fail(self, reason, message, **extra):
        """Mark a non-terminal attempt failed; returns False if already terminal."""
        with self._state_lock():
            current = self.state()["state"]
            if current in TERMINAL:
                return False
            self._transition_locked(current, "failed", reason=reason, message=message, **extra)
            return True

    def publish(self, staging, relative_bundle):
        """Commit a validated bundle, mutually exclusive with cancellation.

        A handled exception rolls the directory and state back before releasing
        the lock. This is not a crash-safe transaction across two filesystem
        paths: SIGKILL/power loss still requires inspection of the attempt.
        """
        staging = pathlib.Path(staging)
        with self._state_lock():
            previous = self.state()
            if previous["state"] != "validating":
                if (previous.get("failure") or {}).get("reason") == "cancelled":
                    raise PipelineError("cancelled", "attempt cancelled before publication")
                raise PipelineError("wrong_state", "only a validating attempt can publish")
            if self.bundle_path.exists():
                raise PipelineError("bundle_exists", f"{self.bundle_path} already exists")
            try:
                os.rename(staging, self.bundle_path)
                self._transition_locked("validating", "complete", bundle=relative_bundle)
            except BaseException:
                # Checking the paths also covers an interrupt just after rename
                # returns, before Python could record that it succeeded.
                try:
                    if self.bundle_path.exists() and not staging.exists():
                        os.rename(self.bundle_path, staging)
                    if self.state() != previous:
                        write_json_atomic(self.path / "state.json", previous)
                except BaseException as rollback_error:
                    raise PipelineError("publication_rollback_failed",
                                        f"inspect {self.path} before reuse: {rollback_error}") from rollback_error
                raise


def create_attempt(job, root, attempt_id=None):
    """Create a new attempt directory for a normalized job; never reuses one."""
    attempt_id = attempt_id or new_attempt_id()
    if not _ATTEMPT_ID.match(attempt_id):
        raise PipelineError("invalid_attempt_id", "attempt id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    output_dir = pathlib.Path(root).resolve() / job["output_dir"]
    attempts = output_dir / "attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    (output_dir / "bundles").mkdir(exist_ok=True)
    path = attempts / attempt_id
    if (output_dir / "bundles" / attempt_id).exists():
        raise PipelineError("attempt_exists", f"a bundle named {attempt_id} already exists")
    try:
        path.mkdir()
    except FileExistsError:
        raise PipelineError("attempt_exists", f"attempt directory already exists: {path}") from None
    write_json_new(path / "job.json", job)
    write_json_new(path / "state.json", {
        "attempt_id": attempt_id, "job_id": job["job_id"], "state": "queued",
        "history": [{"state": "queued", "at_utc": utc_now()}], "failure": None, "bundle": None})
    attempt = Attempt(path)
    attempt.transition("queued", "preparing")
    attempt.frames_dir.mkdir()
    attempt.work_dir.mkdir()
    attempt.transition("preparing", "rendering")
    return attempt


def write_render_report(attempt, renderer, frames_written, info=None):
    """Called by a renderer after its last frame is on disk."""
    if attempt.state()["state"] != "rendering":
        raise PipelineError("wrong_state", "attempt is no longer rendering; report not written")
    write_json_new(attempt.render_report_path, {
        "status": "complete", "renderer": renderer, "frames_written": frames_written,
        "finished_utc": utc_now(), "info": info or {}})


def _ranges(indices):
    """[1,2,3,7] -> '1-3, 7' for compact error messages."""
    parts, start, previous = [], None, None
    for index in sorted(indices):
        if start is None:
            start = previous = index
        elif index == previous + 1:
            previous = index
        else:
            parts.append(f"{start}" if start == previous else f"{start}-{previous}")
            start = previous = index
    if start is not None:
        parts.append(f"{start}" if start == previous else f"{start}-{previous}")
    return ", ".join(parts)


def validate_sequence(attempt, max_errors=20):
    """Check the frame directory against the job. Returns a report with 'ok'."""
    job = attempt.job
    expected = job["frame_count"]
    present, unexpected = {}, []
    for entry in os.scandir(attempt.frames_dir):
        if entry.name.startswith("."):
            continue  # Finder metadata and similar; never treated as frames
        match = FRAME_NAME.match(entry.name)
        if not match or not entry.is_file():
            unexpected.append(entry.name)
            continue
        index = int(match.group(1))
        if index >= expected:
            unexpected.append(entry.name)
        else:
            present[index] = entry.path
    missing = [index for index in range(expected) if index not in present]
    errors = []
    if missing:
        errors.append(f"missing {len(missing)} frame(s): {_ranges(missing)}")
    if unexpected:
        errors.append(f"unexpected entries: {', '.join(sorted(unexpected)[:max_errors])}")

    frame_hashes, color_chunks, color_types = [], set(), set()
    for index in range(expected):
        if index not in present:
            continue
        path = present[index]
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            info = inspect_png(data)
        except (OSError, PNGError) as error:
            errors.append(f"{frame_name(index)}: {error}")
            continue
        if (info["width"], info["height"]) != (job["width"], job["height"]):
            errors.append(f"{frame_name(index)}: {info['width']}x{info['height']}, "
                          f"expected {job['width']}x{job['height']}")
        if info["bit_depth"] != job["source"]["bit_depth"]:
            errors.append(f"{frame_name(index)}: bit depth {info['bit_depth']}, "
                          f"expected {job['source']['bit_depth']}")
        if info["color_type"] not in (2, 6):
            errors.append(f"{frame_name(index)}: color type {info['color_type']} is not RGB/RGBA")
        for error in _color_metadata_errors(info["color_chunks"]):
            errors.append(f"{frame_name(index)}: {error}")
        color_types.add(info["color_type"])
        color_chunks.add(repr(sorted(info["color_chunks"].items())))
        frame_hashes.append(sha256_file(path))
        if len(errors) > max_errors:
            errors.append("too many errors; stopped")
            break

    if len(color_types) > 1:
        errors.append(f"frames mix PNG color types {sorted(color_types)}")
    if len(color_chunks) > 1:
        errors.append("frames carry different PNG color metadata chunks")
    first_chunks = None
    if present.get(0) and not errors:
        first_chunks = inspect_png(pathlib.Path(present[0]).read_bytes())["color_chunks"]
    return {
        "ok": not errors, "errors": errors, "expected_frames": expected,
        "found_frames": len(present), "missing": _ranges(missing), "unexpected": sorted(unexpected),
        "png_color_type": sorted(color_types)[0] if len(color_types) == 1 else None,
        "alpha_present": 6 in color_types,
        "png_color_chunks": first_chunks,
        "frame_sha256": frame_hashes if not errors else None,
    }


def _color_metadata_errors(chunks):
    """Reject metadata the sole BT.709 RGB source contract cannot honor.

    Untagged frames retain the renderer's declared contract; this does not
    establish that their sample values really use the declared transfer.
    """
    errors = []
    for kind in ("sRGB", "gAMA", "iCCP"):
        if kind in chunks:
            errors.append(f"PNG {kind} metadata is unsupported by the BT.709-transfer source contract; "
                          "convert and verify the source before exporting")
    if "cICP" in chunks and chunks["cICP"] != {
            "primaries": 1, "transfer": 1, "matrix": 0, "full_range": 1}:
        errors.append("PNG cICP conflicts with full-range RGB / Rec.709 primaries / BT.709 transfer")
    rec709_chromaticities = [31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000]
    if "cHRM" in chunks and chunks["cHRM"] != {"chromaticities": rec709_chromaticities}:
        errors.append("PNG cHRM does not describe Rec.709 primaries and D65 white")
    return errors
