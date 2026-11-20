"""Fail-closed identity checks for one reviewed PNG metadata correction.

The retained charts prove sample preservation for this profile, not a generic
color transform. New templates/builds need their own reviewed captures/profile.
This module uses no TouchDesigner API and never renders or modifies evidence.
"""
import hashlib
import json
import pathlib

from pipeline.common import PipelineError, sha256_file
from pipeline.png import redeclare_color

PROFILE_PATH = pathlib.Path(__file__).with_name("soft_gradient-v1-td2025.33230.json")


def _reject(message):
    raise PipelineError("unverified_color_calibration", message)


def load_profile(path=PROFILE_PATH):
    """Load the shipped approval and verify its retained evidence, not a caller flag."""
    path = pathlib.Path(path)
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
        evidence_path = path.parent / profile["evidence"]["path"]
        if sha256_file(evidence_path) != profile["evidence"]["sha256"]:
            _reject("calibration evidence hash differs from the reviewed profile")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if (profile["schema_version"] != 1 or evidence["schema_version"] != 1 or
                evidence["status"] != "reviewed_existing_capture" or
                evidence["td_build"] != profile["identity"]["td_build"] or
                evidence["template_sha256"] != profile["template"]["sha256"] or
                evidence["shader_sha256"] != profile["shader"]["sha256"] or
                evidence["source_contract"] != profile["source"]["color"]):
            _reject("calibration evidence does not identify this profile")
        samples = []
        for item in evidence["samples"]:
            sample = evidence_path.parent / item["file"]
            if sha256_file(sample) != item["sha256"] or not item["verify_chart"]["ok"]:
                _reject("retained chart evidence is missing, altered, or not verified")
            samples.append(sample.read_bytes())
        fix = profile["png_color_redeclare"]
        if (len(samples) != 2 or redeclare_color(samples[0],
                remove=tuple(k.encode() for k in fix["remove"]),
                cicp=bytes(fix["cicp"])) != samples[1]):
            _reject("retained raw/corrected chart samples do not match the approved rewrite")
        profile["profile_sha256"] = sha256_file(path)
        return profile
    except PipelineError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as error:
        _reject("cannot load reviewed calibration: " + str(error))


def validate_identity(profile, identity, project_file, job, cook_delay, repo):
    """Match the live source/output AND the on-disk provenance before any save."""
    repo = pathlib.Path(repo)
    for key, expected in profile["identity"].items():
        if identity.get(key) != expected:
            _reject(f"live {key} is {identity.get(key)!r}; reviewed value is {expected!r}")
    declared = profile["template"]
    if job["template"] != {k: declared[k] for k in ("id", "version", "path")}:
        _reject("job does not declare the reviewed template id, version and path")
    if job["source"] != profile["source"]:
        _reject("job source contract differs from the reviewed calibration")
    if [job["width"], job["height"]] != profile["identity"]["output_size"]:
        _reject("job size differs from the reviewed output configuration")
    if cook_delay != profile["cook_delay_frames"]:
        _reject("cook delay differs from the reviewed export configuration")
    if not project_file or pathlib.Path(project_file).resolve() != (repo / declared["path"]).resolve():
        _reject("live project is not the reviewed template file")
    for kind in ("template", "shader"):
        item = profile[kind]
        try:
            actual = sha256_file(repo / item["path"])
        except OSError as error:
            _reject(f"cannot read reviewed {kind}: {error}")
        if actual != item["sha256"]:
            _reject(f"{kind} file differs from the reviewed calibration")
    return {
        "profile_id": profile["id"], "profile_sha256": profile["profile_sha256"],
        "evidence_sha256": profile["evidence"]["sha256"],
        "live_identity": identity,
    }


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
