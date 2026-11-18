"""Small publication/cancellation and PNG source-contract regressions."""
import json
import pathlib
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import attempt as attempt_module  # noqa: E402
from pipeline import bundle  # noqa: E402
from pipeline.attempt import create_attempt, frame_name, validate_sequence, write_render_report  # noqa: E402
from pipeline.common import PipelineError  # noqa: E402
from pipeline.job import validate_job  # noqa: E402
from pipeline.png import encode_rgb8  # noqa: E402


def chunk(kind, body):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


class PublicationAndColorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pipeline-regression-")
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.job = validate_job({
            "schema_version": 1, "job_id": "regression", "template": {"id": "test", "version": 1},
            "seed": 42, "width": 16, "height": 16, "fps": 24, "frame_count": 2,
            "parameters": {}, "source": {"format": "png", "bit_depth": 8,
                                          "color": "rgb_rec709_bt709trc_full"},
            "output_dir": "renders/regression",
        })

    def attempt(self, extra_chunks=b""):
        attempt = create_attempt(self.job, self.root)
        data = encode_rgb8(16, 16, [bytes([128, 128, 128]) * 16] * 16)
        data = data[:33] + extra_chunks + data[33:]
        for index in range(2):
            (attempt.frames_dir / frame_name(index)).write_bytes(data)
        write_render_report(attempt, "test", 2)
        return attempt

    def staged(self):
        attempt = self.attempt()
        attempt.transition("rendering", "encoding")
        attempt.transition("encoding", "validating")
        staging = attempt.path / "bundle.partial"
        staging.mkdir()
        (staging / "marker").write_text("validated fixture")
        return attempt, staging

    def test_cancelled_attempt_cannot_publish(self):
        attempt, staging = self.staged()
        attempt.fail("cancelled", "external cancellation")
        with self.assertRaises(PipelineError) as caught:
            attempt.publish(staging, "unused")
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertFalse(attempt.bundle_path.exists())
        self.assertTrue(staging.is_dir())
        self.assertEqual(attempt.state()["state"], "failed")

    def test_state_write_failure_rolls_back_publication(self):
        attempt, staging = self.staged()
        before = attempt.state()
        original = attempt_module.write_json_atomic

        def fail_complete(path, value):
            if value["state"] == "complete":
                raise OSError("simulated state write failure")
            return original(path, value)

        with mock.patch.object(attempt_module, "write_json_atomic", fail_complete):
            with self.assertRaises(OSError):
                attempt.publish(staging, "unused")
        self.assertEqual(attempt.state(), before)
        self.assertFalse(attempt.bundle_path.exists())
        self.assertEqual((staging / "marker").read_text(), "validated fixture")

    def test_interrupt_after_state_replacement_rolls_back_both_paths(self):
        attempt, staging = self.staged()
        before = attempt.state()
        original = attempt_module.write_json_atomic

        def interrupt_after_write(path, value):
            original(path, value)
            if value["state"] == "complete":
                raise KeyboardInterrupt

        with mock.patch.object(attempt_module, "write_json_atomic", interrupt_after_write):
            with self.assertRaises(KeyboardInterrupt):
                attempt.publish(staging, "unused")
        self.assertEqual(attempt.state(), before)
        self.assertTrue(staging.is_dir())
        self.assertFalse(attempt.bundle_path.exists())

    def test_publication_holds_interprocess_lock_until_complete(self):
        attempt, staging = self.staged()
        original = attempt_module.write_json_atomic
        children = []
        child_code = """
import fcntl, json, pathlib, sys
from pipeline.attempt import Attempt
path = pathlib.Path(sys.argv[1])
with open(path / '.state.lock', 'a+b') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = False
        fcntl.flock(lock, fcntl.LOCK_UN)
    except BlockingIOError:
        blocked = True
print(json.dumps({'lock_blocked': blocked}), flush=True)
changed = Attempt(path).fail('cancelled', 'concurrent cancellation')
print(json.dumps({'changed': changed}), flush=True)
"""

        def cancel_during_commit(path, value):
            if value["state"] == "complete":
                process = subprocess.Popen([sys.executable, "-c", child_code, str(attempt.path)],
                                           cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True)
                children.append(process)
                self.assertTrue(select.select([process.stdout], [], [], 5)[0], "child did not start")
                self.assertEqual(json.loads(process.stdout.readline()), {"lock_blocked": True})
            return original(path, value)

        try:
            with mock.patch.object(attempt_module, "write_json_atomic", cancel_during_commit):
                attempt.publish(staging, "renders/regression/bundles/test")
            out, err = children[0].communicate(timeout=5)
            self.assertEqual(children[0].returncode, 0, err)
            self.assertEqual(json.loads(out), {"changed": False})
        finally:
            for process in children:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        self.assertEqual(attempt.state()["state"], "complete")
        self.assertTrue(attempt.bundle_path.is_dir())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires ffmpeg/ffprobe")
    def test_external_cancel_during_validation_publishes_nothing(self):
        attempt = self.attempt()
        original = bundle.loop_seam

        def cancel_during_validation(*args):
            result = original(*args)
            attempt.fail("cancelled", "external cancellation during validation")
            return result

        with mock.patch.object(bundle, "loop_seam", cancel_during_validation):
            with self.assertRaises(PipelineError) as caught:
                bundle.finalize(attempt.path)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertEqual(attempt.state()["state"], "failed")
        self.assertFalse(attempt.bundle_path.exists())

    def test_rejects_consistent_conflicting_or_uninterpreted_color_metadata(self):
        cases = {
            "sRGB": chunk(b"sRGB", b"\0"),
            "gAMA": chunk(b"gAMA", struct.pack(">I", 45455)),
            "iCCP": chunk(b"iCCP", b"test\0\0" + zlib.compress(b"uninterpreted profile")),
            "cICP": chunk(b"cICP", bytes([1, 13, 0, 1])),
            "cHRM": chunk(b"cHRM", struct.pack(">8I", *([30000] * 8))),
        }
        for kind, data in cases.items():
            with self.subTest(kind=kind):
                report = validate_sequence(self.attempt(data))
                self.assertFalse(report["ok"])
                self.assertTrue(any(kind in error for error in report["errors"]), report)

    def test_accepts_untagged_or_matching_bt709_rgb_metadata(self):
        matching = (chunk(b"cICP", bytes([1, 1, 0, 1]))
                    + chunk(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000,
                                                 30000, 60000, 15000, 6000)))
        for data in (b"", matching):
            with self.subTest(tagged=bool(data)):
                report = validate_sequence(self.attempt(data))
                self.assertTrue(report["ok"], report)


if __name__ == "__main__":
    unittest.main()
