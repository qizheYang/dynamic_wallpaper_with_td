"""Versioned render job: strict loading, validation, and normalization.

Schema version 1 (see jobs/README.md). Unknown keys are rejected so that a
typo cannot silently fall back to a default.
"""
import json
import math
import pathlib
import re

from .common import PipelineError, sha256_bytes

SCHEMA_VERSION = 1

# Each source color contract states what the PNG sample values already are, and
# how they are converted/tagged for H.264. A contract is a claim the renderer
# must satisfy; the encoder never infers it. Unknown contracts are rejected.
COLOR_CONTRACTS = {
    "rgb_rec709_bt709trc_full": {
        "description": "Full-range RGB, Rec.709 primaries, values already encoded "
                       "with the BT.709 transfer function.",
        "filter": ("scale=in_range=pc:out_range=tv:out_color_matrix=bt709,"
                   "format=yuv420p,"
                   "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"),
        "expected_tags": {"color_space": "bt709", "color_primaries": "bt709",
                          "color_transfer": "bt709", "color_range": "tv"},
        # Used to decode the video back to RGB for the round-trip check.
        "decode_filter": "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24",
    },
}

X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast",
                "medium", "slow", "slower", "veryslow")

DEFAULT_ENCODE = {"codec": "libx264", "preset": "medium", "crf": 18}
DEFAULT_VALIDATION = {"seam_ratio_max": 3.0, "roundtrip_mean_abs_max": 3.0}

_JOB_KEYS = {"schema_version", "job_id", "template", "seed", "width", "height", "fps",
             "frame_count", "parameters", "source", "encode", "validation", "output_dir"}
_REQUIRED = _JOB_KEYS - {"encode", "validation"}
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _fail(message):
    raise PipelineError("invalid_job", message)


def _int(job, key, low, high):
    value = job.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{key} must be an integer")
    if not low <= value <= high:
        _fail(f"{key} must be in {low}..{high}, got {value}")
    return value


def _number(section, key, low, high, where):
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(f"{where}.{key} must be a finite number")
    if not low <= value <= high:
        _fail(f"{where}.{key} must be in {low}..{high}, got {value}")
    return float(value)


def _keys(section, allowed, required, where):
    if not isinstance(section, dict):
        _fail(f"{where} must be an object")
    unknown = sorted(set(section) - set(allowed))
    if unknown:
        _fail(f"{where} has unknown keys: {', '.join(unknown)}")
    missing = sorted(set(required) - set(section))
    if missing:
        _fail(f"{where} is missing: {', '.join(missing)}")


def _relative_path(value, where, prefix):
    if not isinstance(value, str) or not value:
        _fail(f"{where} must be a non-empty string")
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        _fail(f"{where} must be a project-relative path without '..'")
    if path.parts[0] != prefix:
        _fail(f"{where} must be under {prefix}/")
    return str(path)


def validate_job(raw):
    """Return a normalized copy of a job dict, or raise PipelineError."""
    _keys(raw, _JOB_KEYS, _REQUIRED, "job")
    if raw["schema_version"] != SCHEMA_VERSION:
        _fail(f"schema_version must be {SCHEMA_VERSION}")
    job_id = raw["job_id"]
    if not isinstance(job_id, str) or not _ID.match(job_id):
        _fail("job_id must match [a-z0-9][a-z0-9_-]{0,63}")

    template = raw["template"]
    _keys(template, {"id", "version", "path"}, {"id", "version"}, "template")
    if not isinstance(template["id"], str) or not _ID.match(template["id"]):
        _fail("template.id must match [a-z0-9][a-z0-9_-]{0,63}")
    _int(template, "version", 1, 1_000_000)
    normalized_template = {"id": template["id"], "version": template["version"]}
    if "path" in template:
        normalized_template["path"] = _relative_path(template["path"], "template.path", "td")
        if not normalized_template["path"].endswith((".toe", ".tox")):
            _fail("template.path must be a .toe or .tox file")

    seed = _int(raw, "seed", 0, 2**31 - 1)
    width = _int(raw, "width", 16, 3840)
    height = _int(raw, "height", 16, 2160)
    if width % 2 or height % 2:
        _fail("width and height must be even for yuv420p")
    fps = _int(raw, "fps", 1, 120)
    frame_count = _int(raw, "frame_count", 2, 1_000_000)

    parameters = raw["parameters"]
    if not isinstance(parameters, dict):
        _fail("parameters must be an object")
    for key, value in parameters.items():
        if not _ID.match(key):
            _fail(f"parameter name {key!r} must match [a-z0-9][a-z0-9_-]{{0,63}}")
        if not (value is None or isinstance(value, (str, int, float, bool))):
            _fail(f"parameter {key!r} must be a JSON scalar")
        if isinstance(value, float) and not math.isfinite(value):
            _fail(f"parameter {key!r} must be finite")

    source = raw["source"]
    _keys(source, {"format", "bit_depth", "color"}, {"format", "bit_depth", "color"}, "source")
    if source["format"] != "png":
        _fail("source.format must be 'png'")
    if source["bit_depth"] not in (8, 16) or isinstance(source["bit_depth"], bool):
        _fail("source.bit_depth must be 8 or 16")
    if source["color"] not in COLOR_CONTRACTS:
        _fail(f"source.color {source['color']!r} is not a supported contract; "
              f"supported: {', '.join(sorted(COLOR_CONTRACTS))}")

    encode = dict(DEFAULT_ENCODE)
    if "encode" in raw:
        _keys(raw["encode"], DEFAULT_ENCODE, (), "encode")
        encode.update(raw["encode"])
    if encode["codec"] != "libx264":
        _fail("encode.codec must be 'libx264'")
    if encode["preset"] not in X264_PRESETS:
        _fail(f"encode.preset must be one of {', '.join(X264_PRESETS)}")
    _int(encode, "crf", 0, 51)

    validation = dict(DEFAULT_VALIDATION)
    if "validation" in raw:
        _keys(raw["validation"], DEFAULT_VALIDATION, (), "validation")
        validation.update(raw["validation"])
    validation["seam_ratio_max"] = _number(validation, "seam_ratio_max", 1.0, 100.0, "validation")
    validation["roundtrip_mean_abs_max"] = _number(validation, "roundtrip_mean_abs_max", 0.0, 255.0,
                                                   "validation")

    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "template": normalized_template,
        "seed": seed,
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "parameters": dict(parameters),
        "source": {"format": "png", "bit_depth": source["bit_depth"], "color": source["color"]},
        "encode": encode,
        "validation": validation,
        "output_dir": _relative_path(raw["output_dir"], "output_dir", "renders"),
    }


def load_job(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raise PipelineError("job_not_found", f"no job file at {path}") from None
    except json.JSONDecodeError as error:
        raise PipelineError("invalid_job", f"not valid JSON: {error}") from None
    return validate_job(raw)


def job_digest(job):
    """Stable hash of a normalized job, independent of key order and whitespace."""
    canonical = json.dumps(job, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_bytes(canonical.encode("utf-8"))


def duration_seconds(job):
    return job["frame_count"] / job["fps"]
