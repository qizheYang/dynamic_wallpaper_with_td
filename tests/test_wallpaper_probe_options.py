"""Non-GUI checks for the native probe's bounded configuration contract.

These invoke --validate (or --help) only; no NSApplication session is started.
Set WALLPAPER_PROBE_BINARY to reuse an explicitly compiled probe, otherwise the
test compiles the current Swift source into a temporary directory on macOS.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcrun"), "macOS Swift toolchain required")
class WallpaperProbeOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_directory = tempfile.TemporaryDirectory(prefix="wallpaper-probe-options-", dir="/private/tmp")
        cls.addClassCleanup(cls.build_directory.cleanup)
        supplied = os.environ.get("WALLPAPER_PROBE_BINARY")
        cls.binary = pathlib.Path(supplied) if supplied else pathlib.Path(cls.build_directory.name) / "WallpaperProbe"
        if not supplied:
            subprocess.run([
                "xcrun", "swiftc", "-O", "-module-cache-path", cls.build_directory.name + "/module-cache",
                str(ROOT / "research/benchmarks/WallpaperProbe.swift"), "-o", str(cls.binary),
                "-framework", "AppKit", "-framework", "AVFoundation", "-framework", "CoreGraphics",
            ], check=True, capture_output=True, text=True, timeout=120)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="probe-option-case-")
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name)
        self.output = self.path / "probe.jsonl"
        # --validate verifies readability/type only; it deliberately does not decode media.
        self.media = self.path / "readable-file.mp4"
        self.media.write_bytes(b"readability fixture; not a valid movie")

    def validate(self, schedule="idle:1", extra=(), expected=0):
        before = set(self.path.iterdir())
        result = subprocess.run([
            str(self.binary), "--validate", "--output", str(self.output),
            "--schedule", schedule, *extra,
        ], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertEqual(set(self.path.iterdir()), before, "validation created a file")
        if expected == 0:
            config = json.loads(result.stdout)
            self.assertEqual(config["schema_version"], 2)
            return config
        self.assertEqual(result.stdout, "")
        return result

    def test_idle_validation_needs_no_media_and_creates_nothing(self):
        config = self.validate()
        self.assertIsNone(config["video_path"])
        self.assertEqual(config["schedule"], [{"state": "idle", "seconds": 1}])
        self.assertEqual(config["max_duration_seconds"], 120)
        self.assertFalse(config["sustained"])

    def test_default_bound_and_explicit_sustained_bound(self):
        self.validate("idle:120")
        self.validate("idle:120.001", expected=2)
        self.validate("idle:3600", expected=2)
        config = self.validate("idle:3600", ["--sustained"])
        self.assertEqual(config["total_seconds"], 3600)
        self.assertEqual(config["max_duration_seconds"], 3600)
        self.assertTrue(config["sustained"])
        self.validate("idle:3600.001", ["--sustained"], expected=2)
        self.validate("idle:.999", ["--sustained"], expected=2)

    def test_finite_decimal_and_scientific_durations(self):
        for value in ("idle:+1", "idle:1.", "idle:1e2", "idle:.5,idle:5e-1"):
            with self.subTest(value=value):
                self.validate(value)

    def test_empty_malformed_nonfinite_and_out_of_range_segments(self):
        invalid = [
            "", ",idle:1", "idle:1,", "idle:1,,idle:1", "idle:1:", "idle::1", ":1", "idle:",
            "idle:1,play:1:1", "unknown:1", "idle:-1", "idle:0", "idle:nan", "idle:NaN",
            "idle:inf", "idle:Infinity", "idle:1e309", "idle:1e308,idle:1e308", "idle: 1",
            "idle:1 ", " idle:1", "idle:0x1", "idle:1_0", "idle:.1", "idle:1e-999",
        ]
        for value in invalid:
            with self.subTest(value=value):
                self.validate(value, expected=2)

    def test_playback_requires_readable_regular_media(self):
        self.validate("play:1", expected=2)
        self.validate("play:1", ["--video", str(self.path / "missing.mp4")], expected=2)
        self.validate("play:1", ["--video", str(self.path)], expected=2)
        config = self.validate("play:1", ["--video", str(self.media)])
        self.assertEqual(pathlib.Path(config["video_path"]).resolve(), self.media.resolve())

    def test_supplied_idle_media_is_also_checked(self):
        self.validate(extra=["--video", str(self.path / "missing.mp4")], expected=2)

    def test_cover_requires_explicit_permission(self):
        media = ["--video", str(self.media)]
        self.validate("cover:.5,uncover:.5", media, expected=2)
        config = self.validate("cover:.5,uncover:.5", media + ["--cover", "--pause-on-occlusion"])
        self.assertTrue(config["cover_allowed"])
        self.assertTrue(config["pause_on_occlusion"])

    def test_existing_output_is_preserved(self):
        self.output.write_text("preserve me")
        self.validate(expected=2)
        self.assertEqual(self.output.read_text(), "preserve me")

    def test_dangling_output_symlink_is_preserved(self):
        self.output.symlink_to(self.path / "missing-target")
        self.validate(expected=2)
        self.assertTrue(self.output.is_symlink())

    def test_unknown_duplicate_and_missing_options(self):
        for extra in (["--unknown"], ["--video"], ["--video", "--cover"], ["--output", "again"],
                      ["--sustained", "--sustained"], ["--validate"]):
            with self.subTest(extra=extra):
                self.validate(extra=extra, expected=2)

    def test_output_is_required_even_for_validation(self):
        result = subprocess.run([str(self.binary), "--validate", "--schedule", "idle:1"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--output is required", result.stderr)

    def test_help_exits_without_output(self):
        result = subprocess.run([str(self.binary), "--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--sustained", result.stdout)
        self.assertIn("--validate", result.stdout)


if __name__ == "__main__":
    unittest.main()
