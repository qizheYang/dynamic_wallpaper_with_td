"""TouchDesigner-side export controller for one prepared attempt.

Usage inside the dedicated project (see td/README.md). Import it from disk so
that __file__ locates the repository:

    import sys; sys.path.insert(0, project.folder + '/controller')
    import export_controller
    ctl = export_controller.start_export(attempt_path, template=op('soft_gradient'),
                                         output=op('soft_gradient/null_out'))
    ctl.cancel()   # optional; marks the attempt failed and stops

The controller renders frame i at phase i/N for i = 0..N-1, one frame per TD
frame: it sets the template's frame index, lets TD cook, saves the output TOP
on a later frame, then advances. It never blocks the TD event loop and never
loops inside a single callback. It writes render.json only after the last
frame is saved; `python3 -m pipeline finalize` does all validation and encoding
outside TD.

Status: the controller logic is unit-tested outside TD with fake adapters
(tests/test_td_controller.py). TDAdapter, the only code that touches the TD
API, has run in TouchDesigner 2025.33230 through td/run_td.py: chart, index and
two identical 480-frame exports (see td/README.md).
"""
import os
import pathlib
import re
import sys

_REPO = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pipeline.attempt import Attempt, frame_name, write_render_report  # noqa: E402
from pipeline.common import PipelineError, sha256_file  # noqa: E402
from pipeline.png import inspect_png, redeclare_color  # noqa: E402

# Color chunks TD 2025.33230's PNG writer emits on every frame (see TDAdapter).
TD_PNG_COLOR_CONSTANT = {
    "sRGB": {"rendering_intent": 0},
    "gAMA": {"gamma": 0.45455},
    "cHRM": {"chromaticities": [31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000]},
}

TEMPLATE_ID = "soft_gradient"
TEMPLATE_VERSION = 1
# Job parameters this template understands, with their validators.
TEMPLATE_PARAMETERS = {
    "palette": lambda value: value in ("dusk",),
    "motion_cycles": lambda value: isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 8,
    # Verification renders only (pipeline verify-chart / verify-index); never final art.
    "debug_chart": lambda value: isinstance(value, bool),
    "debug_index": lambda value: isinstance(value, bool),
}
NON_COMMERCIAL_MAX = 1280
COOK_DELAY_FRAMES = 2  # frames between setting the frame index and saving the image


class ExportController:
    def __init__(self, adapter, attempt_path, cook_delay_frames=COOK_DELAY_FRAMES):
        self.adapter = adapter
        self.attempt = Attempt(attempt_path)
        self.job = self.attempt.job
        self.cook_delay = cook_delay_frames
        self.index = 0
        self.status = "idle"
        self.message = ""
        self._saved_realtime = None

    # ---- preconditions: refuse without changing the attempt's state ----
    def check(self):
        job, attempt = self.job, self.attempt
        problems = []
        if attempt.state()["state"] != "rendering":
            problems.append(f"attempt is {attempt.state()['state']!r}, not 'rendering'")
        if any(not entry.name.startswith(".") for entry in attempt.frames_dir.iterdir()):
            problems.append("frames/ is not empty; prepare a new attempt")
        if (job["template"]["id"], job["template"]["version"]) != (TEMPLATE_ID, TEMPLATE_VERSION):
            problems.append(f"job template is {job['template']['id']} v{job['template']['version']}, "
                            f"this controller renders {TEMPLATE_ID} v{TEMPLATE_VERSION}")
        declared = job["template"].get("path")
        project_file = self.adapter.project_file()
        if declared and project_file and pathlib.Path(project_file).resolve() != (_REPO / declared).resolve():
            problems.append(f"open project is {project_file}, job declares {declared}")
        if job["source"]["bit_depth"] != 8:
            problems.append("this template exports 8-bit PNG only")
        for key, value in job["parameters"].items():
            validator = TEMPLATE_PARAMETERS.get(key)
            if validator is None:
                problems.append(f"template does not support parameter {key!r}")
            elif not validator(value):
                problems.append(f"invalid value for parameter {key!r}: {value!r}")
        license_type = self.adapter.license_type()
        if (license_type is None or "non-commercial" in license_type.lower()) and \
                max(job["width"], job["height"]) > NON_COMMERCIAL_MAX:
            problems.append(f"license {license_type or 'unknown'} is limited to "
                            f"{NON_COMMERCIAL_MAX}px; job is {job['width']}x{job['height']}")
        size = self.adapter.output_size()
        if size != (job["width"], job["height"]):
            problems.append(f"output TOP is {size[0]}x{size[1]}, job needs {job['width']}x{job['height']}")
        return problems

    def start(self):
        problems = self.check()
        if problems:
            self.status, self.message = "refused", "; ".join(problems)
            raise PipelineError("export_refused", self.message)
        self.adapter.configure(self.job)
        self._saved_realtime = self.adapter.get_realtime()
        self.adapter.set_realtime(False)
        self.status = "rendering"
        self._set_frame()
        return self

    # ---- per-frame stepping ----
    def _set_frame(self):
        self.adapter.set_frame(self.index, self.job["frame_count"])
        self.adapter.schedule(self, "_save_frame", self.cook_delay)

    def _save_frame(self):
        if self.status != "rendering":
            return
        try:
            if self.attempt.state()["state"] != "rendering":
                self._stop("cancelled", "attempt left 'rendering'", mark_failed=False)
                return
            path = self.attempt.frames_dir / frame_name(self.index)
            self.adapter.save(path)
            if not path.is_file() or path.stat().st_size == 0:
                raise PipelineError("save_failed", f"{path.name} was not written")
            fix = getattr(self.adapter, "png_color_redeclare", None)
            if fix:
                data = path.read_bytes()
                found = inspect_png(data)["color_chunks"]
                if found != fix["expect"]:
                    raise PipelineError("unexpected_png_color", f"{path.name} color chunks {found} differ from "
                                        f"the verified renderer constant {fix['expect']}; re-verify before export")
                fixed = redeclare_color(data, remove=tuple(k.encode() for k in fix["remove"]),
                                        cicp=bytes(fix["cicp"]))
                partial = path.with_name(f".{path.name}.partial")
                partial.write_bytes(fixed)
                os.replace(partial, path)
            self.index += 1
            if self.index < self.job["frame_count"]:
                self._set_frame()
            else:
                self._finish()
        except Exception as error:  # report every failure; never leave a half state
            self._stop("failed", repr(error), mark_failed=True)

    def _finish(self):
        info = dict(self.adapter.info())
        declared = self.job["template"].get("path")
        if declared and (_REPO / declared).is_file():
            info["template_file"] = declared
            info["template_file_sha256"] = sha256_file(_REPO / declared)
        info["cook_delay_frames"] = self.cook_delay
        fix = getattr(self.adapter, "png_color_redeclare", None)
        if fix:
            info["png_color_redeclared"] = dict(fix)
        write_render_report(self.attempt, self.adapter.renderer_name, self.index, info)
        self._restore()
        self.status, self.message = "complete", f"{self.index} frames written"

    def cancel(self, reason="cancelled in TouchDesigner"):
        if self.status == "rendering":
            self._stop("cancelled", reason, mark_failed=True)

    def _stop(self, status, message, mark_failed):
        self.status, self.message = status, message
        if mark_failed:
            self.attempt.fail("render_failed" if status == "failed" else "cancelled", message,
                              frames_saved=self.index)
        self._restore()

    def _restore(self):
        if self._saved_realtime is not None:
            self.adapter.set_realtime(self._saved_realtime)
            self._saved_realtime = None


class TDAdapter:
    """The only code that calls the TouchDesigner API (exercised on TD 2025.33230)."""

    renderer_name = "touchdesigner"
    # TD 2025.33230's PNG writer tags every file sRGB + gAMA 0.45455 + Rec.709 cHRM,
    # whatever the TOP's Color Space parameter, and never changes the samples
    # (td/run_td.py probe-color, all 11 options). The template's samples are
    # BT.709-OETF encoded, which the chart check confirms survive unchanged, so
    # the controller replaces the two wrong tags with a cICP that describes them.
    png_color_redeclare = {
        "expect": TD_PNG_COLOR_CONSTANT, "remove": ["sRGB", "gAMA"], "cicp": [1, 1, 0, 1],
        "reason": "TD PNG writer emits constant sRGB/gAMA tags; samples are BT.709-OETF (chart-verified)",
    }

    def __init__(self, template, output):
        import td  # available only inside TouchDesigner
        self.td = td
        self.template = template
        self.output = output

    def project_file(self):
        # TD 2025 reports an opened "name.toe" as "name.1.toe", the name its next
        # incremental save would use; map that back to the file actually on disk.
        project = self.td.project
        folder, name = pathlib.Path(project.folder), str(project.name)
        match = re.fullmatch(r"(.+)\.\d+\.toe", name)
        if match and (folder / f"{match.group(1)}.toe").is_file() and not (folder / name).is_file():
            name = f"{match.group(1)}.toe"
        return str(folder / name)

    def license_type(self):
        value = getattr(getattr(self.td, "licenses", None), "type", None)
        return str(value) if value else None

    def output_size(self):
        return (int(self.output.width), int(self.output.height))

    def configure(self, job):
        par = self.template.par
        par.Seed = job["seed"]
        par.Framecount = job["frame_count"]
        par.Motioncycles = job["parameters"].get("motion_cycles", 1)
        par.Debugchart = bool(job["parameters"].get("debug_chart", False))
        par.Debugindex = bool(job["parameters"].get("debug_index", False))

    def set_frame(self, index, total):
        self.template.par.Frame = index

    def save(self, path):
        self.output.save(str(path))

    def schedule(self, controller, method, delay_frames):
        self.td.run(f"args[0].{method}()", controller, delayFrames=delay_frames)

    def get_realtime(self):
        return bool(self.td.project.realTime)

    def set_realtime(self, value):
        self.td.project.realTime = value

    def info(self):
        app = self.td.app
        return {"td_build": str(getattr(app, "build", "")), "td_version": str(getattr(app, "version", "")),
                "td_product": str(getattr(app, "product", "")), "license_type": self.license_type(),
                "template_op": self.template.path, "output_op": self.output.path}


def start_export(attempt_path, template, output, cook_delay_frames=COOK_DELAY_FRAMES):
    return ExportController(TDAdapter(template, output), attempt_path, cook_delay_frames).start()
