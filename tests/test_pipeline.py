"""Pipeline tests on tiny synthetic sequences. Run from the repository root:

    python3 -m unittest discover -s tests -v

Pipeline tests need ffmpeg/ffprobe on PATH and are skipped otherwise. These
tests exercise the disk tooling only; they say nothing about a TD export.
"""
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import job as jobmod  # noqa: E402
from pipeline.attempt import Attempt, create_attempt, frame_name, write_render_report  # noqa: E402
from pipeline.bundle import compare_attempts, finalize  # noqa: E402
from pipeline.common import PipelineError, sha256_file  # noqa: E402
from pipeline.png import encode_rgb8  # noqa: E402
from pipeline.synthetic import frame_png  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
W, H, N = 128, 72, 48


def make_job(**changes):
    raw = {
        "schema_version": 1, "job_id": "test_job", "template": {"id": "synthetic-scroll", "version": 1},
        "seed": 42, "width": W, "height": H, "fps": 24, "frame_count": N, "parameters": {},
        "source": {"format": "png", "bit_depth": 8, "color": "rgb_rec709_bt709trc_full"},
        "output_dir": "renders/test_job",
    }
    raw.update(changes)
    return raw


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="pipeline-test-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def attempt(self, attempt_id=None, **changes):
        return create_attempt(jobmod.validate_job(make_job(**changes)), self.root, attempt_id)

    def render(self, attempt, phase_total=None, report=True):
        """Write frames like a renderer would. phase_total != N breaks the loop on purpose."""
        for index in range(N):
            data = frame_png(W, H, index, phase_total or N, 1, 42)
            (attempt.frames_dir / frame_name(index)).write_bytes(data)
        if report:
            write_render_report(attempt, "test", N)

    def assert_failed_unpublished(self, attempt, reason):
        state = attempt.state()
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["failure"]["reason"], reason)
        self.assertFalse(attempt.bundle_path.exists())
        self.assertEqual(list((attempt.output_dir / "bundles").iterdir()), [])


class JobValidationTests(unittest.TestCase):
    def test_repository_example_jobs_are_valid(self):
        paths = sorted((REPO / "jobs").glob("*.json"))
        self.assertGreaterEqual(len(paths), 2)
        for path in paths:
            with self.subTest(job=path.name):
                jobmod.load_job(path)

    def test_first_real_job_matches_the_handoff(self):
        job = jobmod.load_job(REPO / "jobs" / "gradient_seed42_720p24.json")
        self.assertEqual((job["width"], job["height"], job["fps"], job["frame_count"], job["seed"]),
                         (1280, 720, 24, 480, 42))
        self.assertEqual(job["frame_count"] / job["fps"], 20.0)

    def test_defaults_are_filled(self):
        job = jobmod.validate_job(make_job())
        self.assertEqual(job["encode"], jobmod.DEFAULT_ENCODE)
        self.assertEqual(job["validation"]["seam_ratio_max"], 3.0)

    def test_rejections(self):
        cases = {
            "odd width": {"width": 127},
            "zero fps": {"fps": 0},
            "bool fps": {"fps": True},
            "one frame": {"frame_count": 1},
            "unknown key": {"durations": 20},
            "unknown color contract": {"source": {"format": "png", "bit_depth": 8, "color": "srgb"}},
            "missing color contract": {"source": {"format": "png", "bit_depth": 8}},
            "16-bit only 8/16": {"source": {"format": "png", "bit_depth": 12,
                                            "color": "rgb_rec709_bt709trc_full"}},
            "absolute output": {"output_dir": "/tmp/renders/x"},
            "escaping output": {"output_dir": "renders/../x"},
            "output outside renders": {"output_dir": "out/x"},
            "template outside td": {"template": {"id": "x", "version": 1, "path": "../x.toe"}},
            "template not toe": {"template": {"id": "x", "version": 1, "path": "td/x.py"}},
            "bad job id": {"job_id": "Has Spaces"},
            "non-scalar parameter": {"parameters": {"palette": ["a"]}},
            "unknown encode key": {"encode": {"bitrate": "5M"}},
            "bad preset": {"encode": {"preset": "fastest"}},
        }
        for name, change in cases.items():
            with self.subTest(case=name), self.assertRaises(PipelineError) as caught:
                jobmod.validate_job(make_job(**change))
            self.assertEqual(caught.exception.code, "invalid_job")

    def test_digest_ignores_key_order(self):
        a = jobmod.validate_job(make_job())
        b = jobmod.validate_job(dict(reversed(list(make_job().items()))))
        self.assertEqual(jobmod.job_digest(a), jobmod.job_digest(b))


class AttemptTests(TempRoot):
    def test_attempt_id_is_never_reused(self):
        self.attempt("fixed-id")
        with self.assertRaises(PipelineError) as caught:
            self.attempt("fixed-id")
        self.assertEqual(caught.exception.code, "attempt_exists")

    def test_new_attempt_is_ready_for_rendering(self):
        attempt = self.attempt()
        state = attempt.state()
        self.assertEqual(state["state"], "rendering")
        self.assertEqual([h["state"] for h in state["history"]], ["queued", "preparing", "rendering"])

    def test_render_report_refused_after_cancel(self):
        attempt = self.attempt()
        attempt.fail("cancelled", "test")
        with self.assertRaises(PipelineError):
            write_render_report(attempt, "test", N)

    def test_synthetic_renderer_refuses_non_synthetic_job_without_state_change(self):
        from pipeline.synthetic import render
        attempt = self.attempt(template={"id": "soft_gradient", "version": 1})
        with self.assertRaises(PipelineError) as caught:
            render(attempt.path)
        self.assertEqual(caught.exception.code, "wrong_renderer")
        self.assertEqual(attempt.state()["state"], "rendering")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not on PATH")
class FinalizeTests(TempRoot):
    def test_success_publishes_validated_bundle(self):
        attempt = self.attempt()
        self.render(attempt)
        result = finalize(attempt.path)
        bundle = pathlib.Path(result["bundle"])
        self.assertEqual(sorted(p.name for p in bundle.iterdir()),
                         ["cover.png", "frames.sha256.txt", "loop.mp4", "manifest.json"])
        state = attempt.state()
        self.assertEqual([h["state"] for h in state["history"]],
                         ["queued", "preparing", "rendering", "encoding", "validating", "complete"])
        self.assertEqual(state["bundle"], f"renders/test_job/bundles/{attempt.attempt_id}")
        manifest = json.loads((bundle / "manifest.json").read_text())
        self.assertTrue(all(check["ok"] for check in manifest["validation"]["probe"]))
        self.assertEqual(manifest["video"]["frame_count"], N)
        self.assertEqual(manifest["video"]["sha256"], sha256_file(bundle / "loop.mp4"))
        self.assertEqual(manifest["cover"]["sha256"], sha256_file(attempt.frames_dir / frame_name(0)))
        self.assertEqual(manifest["validation"]["loop_seam"]["verdict"], "continuous")
        self.assertIn("Not a TouchDesigner export (renderer: test)", manifest["not_verified"])
        self.assertEqual(len((bundle / "frames.sha256.txt").read_text().splitlines()), N)
        self.assertFalse((attempt.path / "bundle.partial").exists())

    def test_finalize_twice_is_refused(self):
        attempt = self.attempt()
        self.render(attempt)
        finalize(attempt.path)
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path)
        self.assertEqual(caught.exception.code, "wrong_state")

    def test_unfinished_render_is_refused_without_state_change(self):
        attempt = self.attempt()
        self.render(attempt, report=False)
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path)
        self.assertEqual(caught.exception.code, "render_incomplete")
        self.assertEqual(attempt.state()["state"], "rendering")

    def test_missing_frame_fails(self):
        attempt = self.attempt()
        self.render(attempt)
        (attempt.frames_dir / frame_name(5)).unlink()
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path)
        self.assertEqual(caught.exception.code, "invalid_sequence")
        self.assertIn("missing 1 frame(s): 5", caught.exception.message)
        self.assert_failed_unpublished(attempt, "invalid_sequence")

    def test_numbering_gap_and_out_of_range_frame_fail(self):
        attempt = self.attempt()
        self.render(attempt)
        os.rename(attempt.frames_dir / frame_name(N - 1), attempt.frames_dir / frame_name(N))
        (attempt.frames_dir / "frame_12.png").write_bytes(b"x")
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path)
        self.assertIn(f"missing 1 frame(s): {N - 1}", caught.exception.message)
        self.assertIn(frame_name(N), caught.exception.message)
        self.assertIn("frame_12.png", caught.exception.message)
        self.assert_failed_unpublished(attempt, "invalid_sequence")

    def test_corrupt_and_truncated_frames_fail(self):
        attempt = self.attempt()
        self.render(attempt)
        corrupt = attempt.frames_dir / frame_name(3)
        data = bytearray(corrupt.read_bytes())
        data[60] ^= 0xFF  # inside IDAT: CRC no longer matches
        corrupt.write_bytes(bytes(data))
        truncated = attempt.frames_dir / frame_name(7)
        truncated.write_bytes(truncated.read_bytes()[:-20])
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path)
        self.assertIn(f"{frame_name(3)}: CRC mismatch", caught.exception.message)
        self.assertIn(f"{frame_name(7)}:", caught.exception.message)
        self.assert_failed_unpublished(attempt, "invalid_sequence")

    def test_dimension_mismatch_fails(self):
        attempt = self.attempt()
        self.render(attempt)
        (attempt.frames_dir / frame_name(2)).write_bytes(frame_png(W + 2, H, 2, N))
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path)
        self.assertIn(f"{W + 2}x{H}, expected {W}x{H}", caught.exception.message)
        self.assert_failed_unpublished(attempt, "invalid_sequence")

    def test_seam_discontinuity_fails(self):
        attempt = self.attempt()
        self.render(attempt, phase_total=2 * N)  # only half a cycle: big jump at the loop point
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path)
        self.assertIn("seam_discontinuity", caught.exception.message)
        self.assert_failed_unpublished(attempt, "validation_failed")

    def test_duplicated_endpoint_fails(self):
        attempt = self.attempt()
        for index in range(N - 1):  # phase i/(N-1), then the start frame again: i=N rendered
            (attempt.frames_dir / frame_name(index)).write_bytes(frame_png(W, H, index, N - 1))
        shutil.copyfile(attempt.frames_dir / frame_name(0), attempt.frames_dir / frame_name(N - 1))
        write_render_report(attempt, "test", N)
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path)
        self.assertIn("duplicate_endpoint", caught.exception.message)
        self.assert_failed_unpublished(attempt, "validation_failed")

    def test_color_matrix_mismatch_is_detected(self):
        contract = dict(jobmod.COLOR_CONTRACTS["rgb_rec709_bt709trc_full"])
        contract["decode_filter"] = "scale=in_color_matrix=bt601:in_range=tv:out_range=pc,format=rgb24"
        attempt = self.attempt()
        self.render(attempt)
        with mock.patch.dict(jobmod.COLOR_CONTRACTS, {"rgb_rec709_bt709trc_full": contract}):
            with self.assertRaises(PipelineError) as caught:
                finalize(attempt.path)
        self.assertIn("round_trip", caught.exception.message)
        self.assert_failed_unpublished(attempt, "validation_failed")

    def test_encoder_failure_fails_without_publishing(self):
        fake = self.root / "fake-ffmpeg"
        fake.write_text(f"#!{sys.executable}\nimport sys\n"
                        "if '-version' in sys.argv: print('ffmpeg version fake'); sys.exit(0)\n"
                        "sys.stderr.write('simulated encoder failure'); sys.exit(1)\n")
        fake.chmod(0o755)
        attempt = self.attempt()
        self.render(attempt)
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path, ffmpeg=str(fake))
        self.assertEqual(caught.exception.code, "encode_failed")
        self.assertIn("simulated encoder failure", caught.exception.message)
        self.assert_failed_unpublished(attempt, "encode_failed")

    def test_cancel_during_encode_stops_encoder_and_publishes_nothing(self):
        marker = self.root / "fake-ffmpeg.pid"
        fake = self.root / "fake-ffmpeg"
        fake.write_text(f"#!{sys.executable}\nimport os, sys, time\n"
                        "if '-version' in sys.argv: print('ffmpeg version fake'); sys.exit(0)\n"
                        f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
                        "time.sleep(60)\n")
        fake.chmod(0o755)
        attempt = self.attempt()
        self.render(attempt)
        process = subprocess.Popen([sys.executable, "-m", "pipeline", "finalize", str(attempt.path),
                                    "--ffmpeg", str(fake)], cwd=REPO,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(marker.exists(), "fake encoder never started")
        encoder_pid = int(marker.read_text())
        self.assertEqual(attempt.state()["state"], "encoding")
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 130, stderr.decode())
        self.assert_failed_unpublished(attempt, "cancelled")
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(encoder_pid, 0)

    def test_cancelled_attempt_cannot_be_finalized(self):
        attempt = self.attempt()
        self.render(attempt)
        Attempt(attempt.path).fail("cancelled", "user")
        with self.assertRaises(PipelineError) as caught:
            finalize(attempt.path)
        self.assertEqual(caught.exception.code, "wrong_state")
        self.assertFalse(attempt.bundle_path.exists())

    def test_same_job_twice_reproduces_frames_and_video(self):
        first, second = self.attempt(), self.attempt()
        for attempt in (first, second):
            self.render(attempt)
            finalize(attempt.path)
        comparison = compare_attempts(first.path, second.path)
        self.assertTrue(comparison["same_job"])
        self.assertTrue(comparison["identical_source_frames"])
        self.assertTrue(comparison["identical_video"])


class CommandLineTests(TempRoot):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "pipeline", *args], cwd=REPO,
                              capture_output=True, text=True, timeout=60)

    def test_invalid_job_exits_2(self):
        path = self.root / "bad.json"
        path.write_text(json.dumps(make_job(width=127)))
        result = self.run_cli("validate-job", str(path))
        self.assertEqual(result.returncode, 2)
        self.assertIn("width and height must be even", result.stderr)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not on PATH")
    def test_prepare_render_finalize_from_cli(self):
        path = self.root / "job.json"
        path.write_text(json.dumps(make_job()))
        prepared = self.run_cli("prepare", str(path), "--root", str(self.root))
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        attempt = json.loads(prepared.stdout)["attempt"]
        self.assertEqual(self.run_cli("render-synthetic", attempt).returncode, 0)
        done = self.run_cli("finalize", attempt)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["loop_seam"], "continuous")
        self.assertEqual(json.loads(self.run_cli("status", attempt).stdout)["state"], "complete")


if __name__ == "__main__":
    unittest.main()
