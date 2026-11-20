"""Pinned calibration integrity/identity checks; no TD process or new rendering."""
import copy
import pathlib
import shutil
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "td/calibration"))
import wallpaper_calibration as calibration  # noqa: E402
from pipeline.common import PipelineError  # noqa: E402


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.profile = calibration.load_profile()
        self.job = {
            "template": {key: self.profile["template"][key] for key in ("id", "version", "path")},
            "source": copy.deepcopy(self.profile["source"]), "width": 1280, "height": 720,
        }

    def validate(self, **changes):
        args = {"profile": self.profile, "identity": copy.deepcopy(self.profile["identity"]),
                "project_file": REPO / self.profile["template"]["path"],
                "job": self.job, "cook_delay": 2, "repo": REPO}
        args.update(changes)
        return calibration.validate_identity(**args)

    def test_reviewed_profile_retains_real_matching_chart_evidence(self):
        result = self.validate()
        self.assertEqual(result["profile_id"], "soft_gradient-v1-td2025.33230")
        self.assertEqual(len(result["profile_sha256"]), 64)
        self.assertEqual(result["evidence_sha256"], self.profile["evidence"]["sha256"])

    def test_evidence_must_exist_and_match_hash(self):
        for name in ("soft_gradient-v1-evidence.json", "raw-chart.png", "corrected-chart.png"):
            with self.subTest(file=name), tempfile.TemporaryDirectory() as temporary:
                target = pathlib.Path(temporary) / "calibration"
                shutil.copytree(calibration.PROFILE_PATH.parent, target)
                (target / name).write_bytes(b"not the reviewed evidence")
                with self.assertRaises(PipelineError) as caught:
                    calibration.load_profile(target / calibration.PROFILE_PATH.name)
                self.assertEqual(caught.exception.code, "unverified_color_calibration")

    def test_modified_toe_or_disk_shader_is_rejected_even_with_matching_live_snapshot(self):
        for kind in ("template", "shader"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                repo = pathlib.Path(temporary)
                for item_kind in ("template", "shader"):
                    relative = self.profile[item_kind]["path"]
                    target = repo / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(REPO / relative, target)
                target = repo / self.profile[kind]["path"]
                target.write_bytes(target.read_bytes() + b"changed")
                with self.assertRaises(PipelineError) as caught:
                    self.validate(repo=repo, project_file=repo / self.profile["template"]["path"])
                self.assertIn(kind + " file differs", caught.exception.message)

    def test_unreviewed_delay_size_source_or_project_cannot_claim_calibration(self):
        bad_size = dict(self.job, width=1920, height=1080)
        bad_source = dict(self.job, source=dict(self.job["source"], color="linear"))
        for changes in ({"cook_delay": 1}, {"job": bad_size}, {"job": bad_source},
                        {"project_file": None}, {"project_file": REPO / "other.toe"}):
            with self.subTest(changes=changes), self.assertRaises(PipelineError):
                self.validate(**changes)


if __name__ == "__main__":
    unittest.main()
