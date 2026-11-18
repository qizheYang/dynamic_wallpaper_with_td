"""Run one batch request in a fresh TouchDesigner process and wait for it.

    python3 td/run_td.py probe               # report TD API facts; changes nothing
    python3 td/run_td.py build               # build td/wallpaper_template.toe from source
    python3 td/run_td.py export <attempt>    # render a prepared attempt's frames

"probe" and "build" open td/bootstrap.toe (git-ignored), which this script
generates from TouchDesigner's own blank project plus the batch hook. "export"
opens the built td/wallpaper_template.toe. Each run starts and quits its own TD
process; the script refuses to start while another TouchDesigner is running,
so a batch render never shares the machine with an interactive session or a
playback measurement. On timeout the process is terminated, then killed.

The result JSON is printed; the exit code is 0 only if the request succeeded.
"""
import argparse
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time

TD_APP = pathlib.Path(os.environ.get("TOUCHDESIGNER_APP", "/Applications/TouchDesigner.app"))
TD_BIN = TD_APP / "Contents/MacOS/TouchDesigner"
BASE_PROJECT = TD_APP / "Contents/Resources/tfs/Samples/Setup/Base/NewProject.toe"
HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
BOOTSTRAP = HERE / "bootstrap.toe"
TEMPLATE = HERE / "wallpaper_template.toe"
REQUEST_ENV = "WALLPAPER_TD_REQUEST"

HOOK_NAME = "batch_hook"
HOOK_SCRIPT = '''# Batch hook: runs controller/td_runner.py only when WALLPAPER_TD_REQUEST is set.
# Opening this project by hand does nothing. See td/run_td.py.
import os
import sys

def onStart():
    if not os.environ.get("WALLPAPER_TD_REQUEST"):
        return
    folder = project.folder + "/controller"
    if folder not in sys.path:
        sys.path.insert(0, folder)
    import td_runner
    td_runner.main()
    return
'''


def _dat_text(body):
    # Expanded-.toe DAT text: "2\\n*", five int32 header fields, int32 byte length, UTF-8 text.
    data = body.encode("utf-8")
    return b"2\n*" + struct.pack(">IIIII", 1, 1, 1, 1, 2) + struct.pack(">I", len(data)) + data


def make_bootstrap(path=BOOTSTRAP):
    """TD's blank base project plus a root-level Execute DAT running the batch hook."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        toe = tmp / "bootstrap.toe"
        shutil.copyfile(BASE_PROJECT, toe)
        # toeexpand exits 1 even when it succeeds (TD 2025.33230), so check its output instead.
        subprocess.run([str(TD_APP / "Contents/MacOS/toeexpand"), str(toe)], stdout=subprocess.DEVNULL)
        folder = tmp / "bootstrap.toe.dir"
        toc = tmp / "bootstrap.toe.toc"
        if not (folder / "project1.n").is_file() or not toc.is_file():
            raise RuntimeError(f"toeexpand did not expand {BASE_PROJECT}")
        (folder / f"{HOOK_NAME}.n").write_text(
            "DAT:execute\ntile -200 -150 130 90\nflags =  viewer 1 parlanguage 0\nend\n")
        (folder / f"{HOOK_NAME}.parm").write_text("?\nstart 0 on\nlanguage 0 python\n?\n")
        (folder / f"{HOOK_NAME}.text").write_bytes(_dat_text(HOOK_SCRIPT))
        entries = toc.read_text().splitlines() + [f"{HOOK_NAME}.n", f"{HOOK_NAME}.parm", f"{HOOK_NAME}.text"]
        toc.write_text("\n".join(entries) + "\n")
        toe.unlink()
        subprocess.run([str(TD_APP / "Contents/MacOS/toecollapse"), str(toe)], check=True,
                       stdout=subprocess.DEVNULL)
        shutil.copyfile(toe, path)
    return path


def td_running():
    found = subprocess.run(["pgrep", "-x", "TouchDesigner"], capture_output=True, text=True)
    return [int(pid) for pid in found.stdout.split()]


def run(project, request, timeout, log_path):
    with tempfile.TemporaryDirectory(prefix="td-batch-") as tmp:
        tmp = pathlib.Path(tmp)
        result_path = tmp / "result.json"
        request = dict(request, result=str(result_path), timeout_seconds=timeout)
        request_path = tmp / "request.json"
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        env = dict(os.environ, **{REQUEST_ENV: str(request_path), "TOUCH_TEXT_CONSOLE": "1"})
        started = time.monotonic()
        with open(log_path, "wb") as log:
            process = subprocess.Popen([str(TD_BIN), str(project)], env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=timeout + 60)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if result_path.exists():
                    raise
                return {"ok": False, "errors": [f"TouchDesigner did not finish within {timeout + 60}s; "
                                                f"terminated. Log: {log_path}"]}, process.returncode
        elapsed = round(time.monotonic() - started, 3)
        if not result_path.exists():
            return {"ok": False, "errors": [f"TouchDesigner exited {code} without a result. Log: {log_path}"],
                    "wall_seconds": elapsed}, code
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["wall_seconds"] = elapsed
        result["exit_code"] = code
        return result, code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=["probe", "build", "export", "probe-color"])
    parser.add_argument("attempt", nargs="?",
                        help="attempt directory (export) or output directory (probe-color)")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds (default 900)")
    parser.add_argument("--rebuild-bootstrap", action="store_true")
    parser.add_argument("--replace-template", action="store_true",
                        help="build: overwrite an existing td/wallpaper_template.toe")
    args = parser.parse_args(argv)

    running = td_running()
    if running:
        print(f"TouchDesigner is already running (pid {', '.join(map(str, running))}); "
              "close it first so the batch render runs alone.", file=sys.stderr)
        return 2
    request = {"action": args.action.replace("-", "_")}
    if args.action == "probe-color":
        # Saves the debug chart once per TOP color-space option; the template is not saved.
        if not args.attempt or not TEMPLATE.is_file():
            parser.error("probe-color needs an output directory and a built template")
        out_dir = pathlib.Path(args.attempt).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        request["output_dir"] = str(out_dir)
        project, log_path = TEMPLATE, out_dir / "td_probe_color.log"
    elif args.action == "export":
        if not args.attempt:
            parser.error("export needs an attempt directory")
        attempt = pathlib.Path(args.attempt).resolve()
        if not (attempt / "job.json").is_file():
            parser.error(f"{attempt} is not an attempt directory")
        if not TEMPLATE.is_file():
            parser.error(f"{TEMPLATE.relative_to(REPO)} does not exist; run 'build' first")
        request["attempt"] = str(attempt)
        project, log_path = TEMPLATE, attempt / "work" / "td_render.log"
    else:
        if args.rebuild_bootstrap or not BOOTSTRAP.is_file():
            make_bootstrap()
        project = BOOTSTRAP
        log_dir = REPO / "renders" / "td_batch_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{args.action}-{time.strftime('%Y%m%dT%H%M%S')}.log"
        if args.action == "build":
            if TEMPLATE.exists() and not args.replace_template:
                parser.error(f"{TEMPLATE.relative_to(REPO)} exists; pass --replace-template to rebuild it")
            request["output_project"] = str(TEMPLATE)
    result, code = run(project, request, args.timeout, log_path)
    result["log"] = os.path.relpath(log_path, REPO)
    if args.action == "build":
        # TD also writes its incremental-save name next to the template; an identical
        # copy would only make the opened-project check ambiguous, so remove it.
        numbered = TEMPLATE.with_name(TEMPLATE.stem + ".1.toe")
        if numbered.is_file() and TEMPLATE.is_file() and numbered.read_bytes() == TEMPLATE.read_bytes():
            numbered.unlink()
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
