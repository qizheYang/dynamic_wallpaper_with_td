"""Controller logic tests with a fake TD adapter (no TouchDesigner involved).

These prove the stepping/cancel/precondition logic of td/controller/export_controller.py.
They do not prove that TDAdapter's TouchDesigner API calls are correct.
"""
import importlib.util
import copy
import enum
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline import job as jobmod  # noqa: E402
from pipeline.attempt import create_attempt, frame_name, validate_sequence  # noqa: E402
from pipeline.bundle import finalize  # noqa: E402
from pipeline.common import PipelineError, read_json  # noqa: E402
from pipeline.png import _chunk as png_chunk, inspect_png  # noqa: E402
from pipeline.synthetic import frame_png  # noqa: E402

_spec = importlib.util.spec_from_file_location("export_controller",
                                               REPO / "td" / "controller" / "export_controller.py")
ctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctl)

W, H, N = 128, 72, 24
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class FakeAdapter:
    """Simulates TD frames: schedule() queues a call `delay` frames ahead; pump() advances."""
    renderer_name = "fake-td-adapter"

    def __init__(self, size=(W, H), license_type="Non-Commercial", fail_on_save=None):
        self.size, self.license = size, license_type
        self.fail_on_save = fail_on_save
        self.realtime = True
        self.frame_now = 0
        self.queue = []
        self.current_index = None
        self.set_at = {}
        self.saved_at = {}
        self.configured = None

    def project_file(self):
        return None

    def license_type(self):
        return self.license

    def output_size(self):
        return self.size

    def configure(self, job):
        self.configured = job

    def set_frame(self, index, total):
        self.current_index, self.total = index, total
        self.set_at[index] = self.frame_now

    def save(self, path):
        if self.fail_on_save is not None and self.current_index == self.fail_on_save:
            raise OSError("simulated disk error")
        pathlib.Path(path).write_bytes(frame_png(W, H, self.current_index, self.total))
        self.saved_at[self.current_index] = self.frame_now

    def schedule(self, controller, method, delay_frames):
        self.queue.append((self.frame_now + delay_frames, getattr(controller, method)))

    def get_realtime(self):
        return self.realtime

    def set_realtime(self, value):
        self.realtime = value

    def info(self):
        return {"td_build": "fake"}

    def pump(self, frames=10_000):
        for _ in range(frames):
            if not self.queue:
                return
            self.frame_now += 1
            due = [item for item in self.queue if item[0] <= self.frame_now]
            self.queue = [item for item in self.queue if item[0] > self.frame_now]
            for _, call in due:
                call()


def chunk_bodies(data, kind):
    bodies, offset = [], 8
    while offset < len(data):
        length, found = struct.unpack(">I4s", data[offset:offset + 8])
        if found == kind:
            bodies.append(data[offset + 8:offset + 8 + length])
        offset += 12 + length
    return bodies


class TDLikeAdapter(FakeAdapter):
    """Writes PNGs tagged like TD 2025.33230's writer: sRGB + gAMA + Rec.709 cHRM."""

    def __init__(self, redeclare, gamma=45455, **kwargs):
        kwargs.setdefault("size", (1280, 720))
        super().__init__(**kwargs)
        if redeclare:
            self.png_color_redeclare = dict(ctl.TDAdapter.png_color_redeclare)
        self.gamma = gamma
        self.written = {}
        self.identity = copy.deepcopy(ctl.load_profile()["identity"])

    def project_file(self):
        return str(REPO / "td/wallpaper_template.toe")

    def color_identity(self):
        return copy.deepcopy(self.identity)

    def save(self, path):
        # Exact retained TD chart payload; fake scheduling does not calibrate TD.
        plain = (REPO / "td/calibration/raw-chart.png").read_bytes()
        chunks, offset = [], 8
        while offset < len(plain):
            length, kind = struct.unpack(">I4s", plain[offset:offset + 8])
            if kind not in (b"sRGB", b"gAMA", b"cHRM"):
                chunks.append(plain[offset:offset + length + 12])
            offset += length + 12
        plain = plain[:8] + b"".join(chunks)
        tags = (png_chunk(b"gAMA", struct.pack(">I", self.gamma)) + png_chunk(b"sRGB", b"\0")
                + png_chunk(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)))
        header_end = 8 + 12 + 13
        data = plain[:header_end] + tags + plain[header_end:]
        self.written[self.current_index] = data
        pathlib.Path(path).write_bytes(data)
        self.saved_at[self.current_index] = self.frame_now


def td_job(**changes):
    raw = {"schema_version": 1, "job_id": "td_test", "template": {"id": "soft_gradient", "version": 1},
           "seed": 42, "width": W, "height": H, "fps": 24, "frame_count": N,
           "parameters": {"palette": "dusk", "motion_cycles": 1},
           "source": {"format": "png", "bit_depth": 8, "color": "rgb_rec709_bt709trc_full"},
           "output_dir": "renders/td_test"}
    raw.update(changes)
    return jobmod.validate_job(raw)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="td-controller-test-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def controller(self, adapter=None, **job_changes):
        attempt = create_attempt(td_job(**job_changes), self.root)
        adapter = adapter or FakeAdapter()
        return ctl.ExportController(adapter, attempt.path), adapter, attempt

    def calibrated_controller(self, adapter=None, **job_changes):
        changes = {"width": 1280, "height": 720,
                   "template": {"id": "soft_gradient", "version": 1,
                                "path": "td/wallpaper_template.toe"}}
        changes.update(job_changes)
        return self.controller(adapter or TDLikeAdapter(redeclare=True), **changes)

    def test_renders_every_frame_after_cook_delay_and_reports(self):
        controller, adapter, attempt = self.controller()
        controller.start()
        self.assertFalse(adapter.realtime, "realTime must be off while exporting")
        adapter.pump()
        self.assertEqual(controller.status, "complete")
        self.assertTrue(adapter.realtime, "realTime must be restored")
        self.assertEqual(sorted(adapter.saved_at), list(range(N)))
        for index in range(N):
            self.assertEqual(adapter.saved_at[index] - adapter.set_at[index], ctl.COOK_DELAY_FRAMES)
        report = read_json(attempt.render_report_path)
        self.assertEqual((report["renderer"], report["frames_written"]), ("fake-td-adapter", N))
        self.assertEqual(adapter.configured["seed"], 42)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not on PATH")
    def test_controller_output_finalizes(self):
        controller, adapter, attempt = self.controller()
        controller.start()
        adapter.pump()
        manifest = finalize(attempt.path)["manifest"]
        self.assertIn("Not a TouchDesigner export (renderer: fake-td-adapter)", manifest["not_verified"])

    def test_cancel_mid_render_stops_without_report(self):
        controller, adapter, attempt = self.controller()
        controller.start()
        adapter.pump(frames=10)
        controller.cancel()
        adapter.pump()
        self.assertEqual(controller.status, "cancelled")
        self.assertEqual(attempt.state()["failure"]["reason"], "cancelled")
        self.assertFalse(attempt.render_report_path.exists())
        self.assertTrue(adapter.realtime)
        self.assertLess(len(adapter.saved_at), N)

    def test_external_cancel_is_observed(self):
        controller, adapter, attempt = self.controller()
        controller.start()
        adapter.pump(frames=6)
        attempt.fail("cancelled", "from the command line")
        adapter.pump()
        self.assertEqual(controller.status, "cancelled")
        self.assertFalse(attempt.render_report_path.exists())
        self.assertTrue(adapter.realtime)

    def test_save_error_marks_failed(self):
        controller, adapter, attempt = self.controller(FakeAdapter(fail_on_save=5))
        controller.start()
        adapter.pump()
        self.assertEqual(controller.status, "failed")
        state = attempt.state()
        self.assertEqual((state["state"], state["failure"]["reason"]), ("failed", "render_failed"))
        self.assertIn("simulated disk error", state["failure"]["message"])
        self.assertEqual(state["failure"]["frames_saved"], 5)
        self.assertFalse(attempt.render_report_path.exists())
        self.assertTrue(adapter.realtime)

    def test_preconditions_refuse_without_state_change(self):
        cases = {
            "output size": (FakeAdapter(size=(W + 2, H)), {}),
            "non-commercial over 1280": (FakeAdapter(size=(1920, 1080)), {"width": 1920, "height": 1080}),
            "unknown license over 1280": (FakeAdapter(size=(1920, 1080), license_type=None),
                                          {"width": 1920, "height": 1080}),
            "unsupported parameter": (FakeAdapter(), {"parameters": {"speed": 2}}),
            "invalid parameter value": (FakeAdapter(), {"parameters": {"palette": "neon"}}),
            "wrong template": (FakeAdapter(), {"template": {"id": "other", "version": 1}}),
            "16-bit source": (FakeAdapter(), {"source": {"format": "png", "bit_depth": 16,
                                                         "color": "rgb_rec709_bt709trc_full"}}),
        }
        for name, (adapter, changes) in cases.items():
            with self.subTest(case=name):
                controller, adapter, attempt = self.controller(adapter, **changes)
                with self.assertRaises(PipelineError):
                    controller.start()
                self.assertEqual(attempt.state()["state"], "rendering")
                self.assertTrue(adapter.realtime)
                self.assertEqual(adapter.queue, [])

    def test_commercial_license_allows_1080p(self):
        adapter = FakeAdapter(size=(1920, 1080), license_type="Commercial")
        controller, _, _ = self.controller(adapter, width=1920, height=1080)
        self.assertEqual(controller.check(), [])

    def test_td_adapter_reads_mode_enum_without_td_module_export(self):
        class ParMode(enum.Enum):
            CONSTANT = 0
            EXPRESSION = 1
            EXPORT = 2
            BIND = 3

        adapter = object.__new__(ctl.TDAdapter)
        adapter.td = SimpleNamespace()  # observed TD 2025: no td.ParMode member
        for mode in ParMode:
            with self.subTest(mode=mode):
                self.assertEqual(adapter._expression_active(SimpleNamespace(mode=mode)),
                                 mode is ParMode.EXPRESSION)
        with self.assertRaises(PipelineError) as caught:
            adapter._expression_active(SimpleNamespace(mode="ParMode.EXPRESSION"))
        self.assertIn("builtins.str", caught.exception.message)

    def test_td_png_color_tags_are_redeclared_without_touching_pixels(self):
        adapter = TDLikeAdapter(redeclare=True)
        controller, adapter, attempt = self.calibrated_controller(adapter)
        controller.start()
        adapter.pump()
        self.assertEqual(controller.status, "complete")
        for index in range(N):
            data = (attempt.frames_dir / frame_name(index)).read_bytes()
            self.assertEqual(inspect_png(data)["color_chunks"], {
                "cHRM": ctl.TD_PNG_COLOR_CONSTANT["cHRM"],
                "cICP": {"primaries": 1, "transfer": 1, "matrix": 0, "full_range": 1}})
            self.assertEqual(chunk_bodies(data, b"IDAT"), chunk_bodies(adapter.written[index], b"IDAT"))
        self.assertEqual(sorted(p.name for p in attempt.frames_dir.iterdir()),
                         [frame_name(i) for i in range(N)], "no partial files may remain")
        self.assertTrue(validate_sequence(attempt)["ok"])
        info = read_json(attempt.render_report_path)["info"]
        self.assertEqual(info["png_color_redeclared"]["cicp"], [1, 1, 0, 1])
        self.assertEqual(info["color_calibration"]["profile_id"], "soft_gradient-v1-td2025.33230")
        self.assertEqual(info["color_calibration"]["live_identity"], adapter.identity)

    def test_td_png_color_tags_are_rejected_without_redeclaration(self):
        controller, adapter, attempt = self.calibrated_controller(TDLikeAdapter(redeclare=False))
        controller.start()
        adapter.pump()
        report = validate_sequence(attempt)
        self.assertFalse(report["ok"])
        self.assertTrue(any("sRGB" in error for error in report["errors"]))

    def test_unexpected_renderer_color_chunks_fail_the_export(self):
        adapter = TDLikeAdapter(redeclare=True, gamma=100000)  # not the verified TD constant
        controller, adapter, attempt = self.calibrated_controller(adapter)
        controller.start()
        adapter.pump()
        self.assertEqual(controller.status, "failed")
        self.assertIn("unexpected_png_color", attempt.state()["failure"]["message"])
        self.assertFalse(attempt.render_report_path.exists())

    def test_matching_png_tags_do_not_approve_wrong_renderer_provenance(self):
        mutations = {
            "td_build": "2099.99999", "template_op": "/project1/other",
            "shader_sha256": "0" * 64, "output_color_space": "srgblinear",
            "output_pixel_format": "rgba16float", "output_inputs": ["/project1/linear"],
            "output_lock": True, "glsl_auxiliary_shader_bindings": ["predat"],
            "glsl_mode": "compute",
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                adapter = TDLikeAdapter(redeclare=True)
                adapter.identity[key] = value
                controller, _, attempt = self.calibrated_controller(adapter)
                with self.assertRaises(PipelineError) as caught:
                    controller.start()
                self.assertIn(key, caught.exception.message)
                self.assertEqual(attempt.state()["state"], "rendering")
                self.assertEqual(list(attempt.frames_dir.iterdir()), [])
                self.assertFalse(attempt.render_report_path.exists())

    def test_live_shader_edit_mid_export_stops_before_next_save(self):
        controller, adapter, attempt = self.calibrated_controller()
        controller.start()
        adapter.pump(frames=ctl.COOK_DELAY_FRAMES)
        self.assertEqual(len(adapter.saved_at), 1)
        adapter.identity["shader_sha256"] = "0" * 64
        adapter.pump()
        self.assertEqual(controller.status, "failed")
        self.assertEqual(len(adapter.saved_at), 1)
        self.assertFalse(attempt.render_report_path.exists())
        self.assertTrue(adapter.realtime)

    def test_calibration_requires_declared_template_and_evidence_capability(self):
        adapter = TDLikeAdapter(redeclare=True)
        controller, _, attempt = self.calibrated_controller(adapter,
            template={"id": "soft_gradient", "version": 1})
        with self.assertRaises(PipelineError):
            controller.start()
        self.assertEqual(list(attempt.frames_dir.iterdir()), [])
        adapter = FakeAdapter()
        adapter.png_color_redeclare = dict(ctl.TDAdapter.png_color_redeclare)
        controller, _, attempt = self.controller(adapter)
        with self.assertRaises(PipelineError):
            controller.start()
        self.assertEqual(list(attempt.frames_dir.iterdir()), [])

    def test_non_empty_frames_dir_is_refused(self):
        controller, adapter, attempt = self.controller()
        (attempt.frames_dir / frame_name(0)).write_bytes(b"stale")
        with self.assertRaises(PipelineError) as caught:
            controller.start()
        self.assertIn("frames/ is not empty", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
