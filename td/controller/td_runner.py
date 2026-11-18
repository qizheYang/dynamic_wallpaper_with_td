"""Batch entry point inside TouchDesigner: run one request from disk, then quit.

`python3 td/run_td.py ...` starts a fresh TouchDesigner process on a dedicated
project whose Execute DAT calls main() from onStart. main() does nothing unless
the WALLPAPER_TD_REQUEST environment variable names a request file, so opening
the project by hand is inert.

Request (JSON): {"action": "probe" | "build" | "export", "result": <path>,
                 "attempt": <path, export>, "output_project": <path, build>,
                 "timeout_seconds": <number>}
The result file is written exactly once, then TouchDesigner quits without
saving (except that "build" saves the template project before quitting).

All work is scheduled with td.run, one step per TD frame; nothing blocks the
TD event loop. The host-side script also enforces a timeout and kills the
process if it hangs.
"""
import json
import os
import pathlib
import sys
import time
import traceback

REQUEST_ENV = "WALLPAPER_TD_REQUEST"
_REPO = pathlib.Path(__file__).resolve().parents[2]
_CONTROLLER = pathlib.Path(__file__).resolve().parent
for _path in (str(_REPO), str(_CONTROLLER)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

TEMPLATE_PARENT = "/project1"
TEMPLATE_NAME = "soft_gradient"
BUILD_SETTLE_FRAMES = 10
POLL_FRAMES = 6


def main():
    """Called from the dedicated project's Execute DAT onStart."""
    path = os.environ.get(REQUEST_ENV)
    if not path:
        return None
    runner = Runner(path)
    runner.later("begin", 2)  # let the project finish loading first
    return runner


class Runner:
    def __init__(self, request_path):
        import td
        self.td = td
        self.request_path = pathlib.Path(request_path)
        self.request = {}
        self.result = {"ok": False, "errors": [], "info": {},
                       "started_unix": time.time(), "request": str(request_path)}
        self.deadline = time.monotonic() + 600
        self.done = False
        self.controller = None

    # ---- scheduling helpers ----
    def later(self, method, frames=1):
        self.td.run(f"args[0]._step({method!r})", self, delayFrames=max(1, int(frames)))

    def _step(self, method):
        if self.done:
            return
        try:
            if time.monotonic() > self.deadline:
                raise TimeoutError(f"runner deadline passed during {method}")
            getattr(self, method)()
        except Exception:
            self.result["errors"].append(traceback.format_exc(limit=8))
            if self.controller is not None and self.controller.status == "rendering":
                self.controller.cancel("runner error")
            self.finish()

    def finish(self):
        if self.done:
            return
        self.done = True
        self.result["finished_unix"] = time.time()
        self.result["ok"] = self.result["ok"] and not self.result["errors"]
        target = pathlib.Path(self.request.get("result") or str(self.request_path) + ".result.json")
        partial = target.with_name(target.name + ".partial")
        partial.write_text(json.dumps(self.result, indent=2, default=str), encoding="utf-8")
        os.replace(partial, target)
        self.td.run("args[0].quit(force=True)", self.td.project, delayFrames=2)

    # ---- steps ----
    def begin(self):
        self.request = json.loads(self.request_path.read_text(encoding="utf-8"))
        self.deadline = time.monotonic() + float(self.request.get("timeout_seconds", 600))
        td = self.td
        self.result["action"] = self.request.get("action")
        self.result["info"].update({
            "td_build": str(td.app.build), "td_version": str(td.app.version),
            "license_type": _license_type(td), "project_folder": str(td.project.folder),
            "project_name": str(td.project.name)})
        if td.op("/TDMCP") is not None:
            raise RuntimeError("this project contains /TDMCP; batch runs use a project without it")
        action = self.request.get("action")
        if action == "probe":
            self.probe()
        elif action == "build":
            self.build()
        elif action == "export":
            self.export()
        elif action == "probe_color":
            self.probe_color()
        else:
            raise ValueError(f"unknown action {action!r}")

    def probe(self):
        """Report the API facts the template build relies on; changes nothing on disk."""
        td = self.td
        parent = td.op(TEMPLATE_PARENT)
        info = self.result["info"]
        before = {child.name for child in parent.children}
        glsl = parent.create(td.glslmultiTOP, "probe_glsl")
        info["created_with_glsl"] = sorted({child.name for child in parent.children} - before)
        info["glsl_docked"] = [dock.path for dock in getattr(glsl, "docked", [])]
        keys = ("res", "format", "vec", "pixel", "output", "compute", "mode", "pass", "extend", "filter")
        info["glsl_pars"] = [[p.name, p.label, p.style, str(p.eval())] for p in glsl.pars()
                             if any(key in p.name.lower() for key in keys)]
        try:
            info["glsl_sequences"] = [[seq.name, seq.numBlocks] for seq in glsl.seq]
        except Exception as error:
            info["glsl_sequences_error"] = repr(error)
        text = parent.create(td.textDAT, "probe_text")
        info["text_pars"] = [[p.name, p.label, str(p.eval())] for p in text.pars()
                             if any(key in p.name.lower() for key in ("file", "sync", "language", "load", "write"))]
        null = parent.create(td.nullTOP, "probe_null")
        info["null_pars_sample"] = [p.name for p in null.pars()][:40]
        base = parent.create(td.baseCOMP, "probe_base")
        page = base.appendCustomPage("Probe")
        made = page.appendInt("Frame")
        info["append_int_returns"] = type(made).__name__
        info["base_pars_common"] = [p.name for p in base.pars()
                                    if any(key in p.name.lower() for key in ("shortcut", "extension", "tox"))]
        info["top_save_doc"] = (getattr(null.save, "__doc__", "") or "")[:600]
        info["run_doc"] = (getattr(td.run, "__doc__", "") or "")[:600]
        info["realtime"] = bool(td.project.realTime)
        info["cook_rate"] = float(td.project.cookRate)
        self.result["ok"] = True
        self.finish()

    def probe_color(self):
        """Save the debug chart once per TOP color-space option (template project, never saved)."""
        td = self.td
        template = td.op(f"{TEMPLATE_PARENT}/{TEMPLATE_NAME}")
        self.glsl, self.null = template.op("glsl_art"), template.op("null_out")
        template.par.Debugchart = True
        info = self.result["info"]
        for target in (self.glsl, self.null):
            for name in ("parmcolorspace", "parmreferencewhite"):
                par = getattr(target.par, name)
                info[f"{target.name}.{name}"] = {"value": str(par.eval()), "names": list(par.menuNames),
                                                 "labels": list(par.menuLabels)}
        self.out_dir = pathlib.Path(self.request["output_dir"])
        self.options = list(self.glsl.par.parmcolorspace.menuNames)
        self.saved = []
        self.later("probe_color_next", 1)

    def probe_color_next(self):
        if not self.options:
            self.result["info"]["saved"] = self.saved
            self.result["ok"] = True
            self.finish()
            return
        option = self.options.pop(0)
        self.glsl.par.parmcolorspace = option
        self.null.par.parmcolorspace = option
        self.current = option
        self.later("probe_color_save", 3)

    def probe_color_save(self):
        path = self.out_dir / f"chart_{self.current}.png"
        self.null.save(str(path))
        self.saved.append({"option": self.current, "file": str(path),
                           "errors": self.null.errors(recurse=False) or self.glsl.errors(recurse=False)})
        self.later("probe_color_next", 1)

    def build(self):
        import soft_gradient_build
        td = self.td
        parent = td.op(TEMPLATE_PARENT)
        if parent.op(TEMPLATE_NAME) is not None:
            raise RuntimeError(f"{TEMPLATE_PARENT}/{TEMPLATE_NAME} already exists")
        self.template = soft_gradient_build.build(parent, _REPO / soft_gradient_build.SHADER)
        self.result["info"]["template_op"] = self.template.path
        self.later("check_build", BUILD_SETTLE_FRAMES)

    def check_build(self):
        import soft_gradient_build
        report = soft_gradient_build.check(self.template)
        self.result["info"]["build_check"] = report
        if report["problems"]:
            self.result["errors"].extend(report["problems"])
            self.finish()
            return
        output = pathlib.Path(self.request["output_project"]).resolve()
        if output.suffix != ".toe" or output.parent != (_REPO / "td").resolve():
            raise ValueError(f"output_project must be a .toe directly under td/: {output}")
        self.td.project.save(str(output))
        self.result["info"]["saved_project"] = str(output)
        self.result["ok"] = True
        self.finish()

    def export(self):
        import export_controller
        td = self.td
        template = td.op(f"{TEMPLATE_PARENT}/{TEMPLATE_NAME}")
        if template is None:
            raise RuntimeError(f"{TEMPLATE_PARENT}/{TEMPLATE_NAME} not found; open the built template project")
        output = template.op("null_out")
        self.controller = export_controller.start_export(self.request["attempt"], template=template, output=output)
        self.started = time.monotonic()
        self.later("poll_export", POLL_FRAMES)

    def poll_export(self):
        ctl = self.controller
        if ctl.status == "rendering":
            self.later("poll_export", POLL_FRAMES)
            return
        self.result["info"]["export_status"] = ctl.status
        self.result["info"]["export_message"] = ctl.message
        self.result["info"]["frames_saved"] = ctl.index
        self.result["info"]["export_seconds"] = round(time.monotonic() - self.started, 3)
        self.result["ok"] = ctl.status == "complete"
        if not self.result["ok"]:
            self.result["errors"].append(f"export {ctl.status}: {ctl.message}")
        self.finish()


def _license_type(td):
    value = getattr(getattr(td, "licenses", None), "type", None)
    return str(value) if value else None
