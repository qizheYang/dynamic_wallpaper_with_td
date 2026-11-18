"""Tests for the debug-render checks (frame-index strip and known-value chart).

Frames are drawn in Python exactly as the shader specifies, so these tests prove
the readers, not the shader or TouchDesigner.
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import job as jobmod  # noqa: E402
from pipeline.attempt import create_attempt, frame_name  # noqa: E402
from pipeline.png import encode_rgb8  # noqa: E402
from pipeline.verify import CHART, INDEX_BITS, strip_rows, verify_chart, verify_index  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg"))
W, H, N = 240, 144, 20


def index_frame(index, strip_at_bottom=True):
    main = bytes([40, 30, 90]) * W
    cells = format(index, f"0{INDEX_BITS}b")
    strip = b"".join(bytes([255] * 3 if cells[min(INDEX_BITS - 1, x * INDEX_BITS // W)] == "1" else [0] * 3)
                     for x in range(W))
    rows_strip = strip_rows(H)
    rows = [main] * (H - rows_strip)
    rows = rows + [strip] * rows_strip if strip_at_bottom else [strip] * rows_strip + rows
    return encode_rgb8(W, H, rows)


def chart_frame(perturb_bar=None):
    row = bytearray()
    for x in range(W):
        bar = x * len(CHART) // W
        value = [round(v * 255) for v in CHART[bar]]
        if bar == perturb_bar:
            value = [min(255, v + 6) for v in value]
        row += bytes(value)
    return encode_rgb8(W, H, [bytes(row)] * H)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not on PATH")
class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="verify-test-"))
        raw = {"schema_version": 1, "job_id": "verify", "template": {"id": "soft_gradient", "version": 1},
               "seed": 1, "width": W, "height": H, "fps": 24, "frame_count": N, "parameters": {},
               "source": {"format": "png", "bit_depth": 8, "color": "rgb_rec709_bt709trc_full"},
               "output_dir": "renders/verify"}
        self.attempt = create_attempt(jobmod.validate_job(raw), self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, frames):
        for index, data in enumerate(frames):
            (self.attempt.frames_dir / frame_name(index)).write_bytes(data)

    def test_correct_index_strip_passes(self):
        self.write(index_frame(i) for i in range(N))
        result = verify_index(self.attempt.path)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["strip_location"], "bottom")

    def test_frames_saved_one_cook_late_are_detected(self):
        self.write(index_frame(max(0, i - 1)) for i in range(N))  # frame k holds index k-1
        result = verify_index(self.attempt.path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["mismatched_count"], N - 1)
        self.assertEqual(result["mismatched"][0], {"frame": 1, "index": 0})

    def test_upside_down_frames_are_reported(self):
        self.write(index_frame(i, strip_at_bottom=False) for i in range(N))
        result = verify_index(self.attempt.path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["strip_location"], "top")
        self.assertFalse(result["upright"])

    def test_chart_values_pass_and_shift_fails(self):
        self.write([chart_frame()])
        result = verify_chart(self.attempt.path)
        self.assertTrue(result["ok"], result)
        (self.attempt.frames_dir / frame_name(0)).write_bytes(chart_frame(perturb_bar=2))
        result = verify_chart(self.attempt.path)
        self.assertFalse(result["ok"])
        self.assertEqual([bar["bar"] for bar in result["bars"] if not bar["ok"]], [2])


if __name__ == "__main__":
    unittest.main()
