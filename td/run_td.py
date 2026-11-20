"""Run one batch request in a fresh TouchDesigner process and wait for it.

    python3 td/run_td.py probe               # report TD API facts; writes bootstrap/logs
    python3 td/run_td.py build               # build td/wallpaper_template.toe from source
    python3 td/run_td.py export <attempt>    # render a prepared attempt's frames

"probe" and "build" open td/bootstrap.toe (git-ignored), which this script
generates from TouchDesigner's own blank project plus the batch hook. "export"
opens the built td/wallpaper_template.toe. Each run starts and quits its own TD
process; the script refuses to start while another TouchDesigner is detected.
This is not a lock against concurrent launches or playback measurements: keep
render and measurement sessions separate. The finite deadline includes setup,
startup and shutdown; cancellation/timeout cleans only this run's child.

The result JSON is printed; the exit code is 0 only if the request succeeded.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import pathlib
import shutil
import secrets
import signal
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
MAX_TIMEOUT = 3600.0
HELPER_TIMEOUT = 30.0
POLL_SECONDS = 0.1
TERMINATE_SECONDS = 5.0
KILL_SECONDS = 5.0


def validate_timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= MAX_TIMEOUT:
        raise ValueError("timeout must be finite and greater than 0, at most 3600 seconds")
    return float(value)


class BatchCancelled(Exception):
    def __init__(self, signum):
        self.signum = signum
        super().__init__("batch cancelled by signal %s" % signum)


class Cancellation:
    def __init__(self):
        self.signum = None

    def handle(self, signum, _frame=None):
        # Do not raise between Popen returning and storing its owned handle.
        if self.signum is None:
            self.signum = signum

    def check(self):
        if self.signum is not None:
            raise BatchCancelled(self.signum)


@contextmanager
def cancellation_scope():
    cancel = Cancellation()
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, cancel.handle)
        yield cancel
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def remaining(deadline, cancel):
    cancel.check()
    seconds = deadline - time.monotonic()
    if not math.isfinite(seconds) or seconds <= 0:
        raise TimeoutError("batch deadline exceeded")
    return seconds


def terminate_owned(process):
    """Bounded cleanup of a Popen child; never signal names or process groups."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=TERMINATE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=KILL_SECONDS)
    except subprocess.TimeoutExpired:
        raise RuntimeError("owned child did not exit after bounded terminate/kill") from None


def execute(command, deadline, cancel, **kwargs):
    remaining(deadline, cancel)
    process = None
    try:
        process = subprocess.Popen(command, **kwargs)
        while True:
            budget = remaining(deadline, cancel)
            try:
                stdout, stderr = process.communicate(timeout=min(POLL_SECONDS, budget))
                cancel.check()
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        cancel.handle(signal.SIGINT)
        raise BatchCancelled(signal.SIGINT) from None
    finally:
        if process is not None:
            terminate_owned(process)


def file_hash(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def export_snapshot(request):
    """Read-only preflight. Refused/terminal attempts never become owned exports."""
    if request.get("action") != "export":
        return {}
    attempt = pathlib.Path(request["attempt"])
    state = json.loads((attempt / "state.json").read_text())
    if state.get("state") != "rendering" or (attempt / "render.json").exists():
        raise ValueError("export requires a fresh rendering attempt without render.json")
    if any(not p.name.startswith(".") for p in (attempt / "frames").iterdir()):
        raise ValueError("export requires an empty frame directory")
    return {"attempt_state_sha256": file_hash(attempt / "state.json"),
            "attempt_job_sha256": file_hash(attempt / "job.json")}


def fail_owned_export(request, marker_path, reason, message):
    """Only a TD-confirmed, unchanged, still-rendering attempt can be failed."""
    if request.get("action") != "export" or not marker_path.exists():
        return False
    marker = json.loads(marker_path.read_text())
    keys = ("request_token", "attempt", "attempt_state_sha256", "attempt_job_sha256")
    if not isinstance(marker, dict) or any(marker.get(k) != request.get(k) for k in keys):
        return False
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from pipeline.attempt import Attempt
    attempt = Attempt(request["attempt"])
    # Never wait indefinitely for another worker's state lock during cleanup.
    with open(attempt.path / ".state.lock", "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if (attempt.state().get("state") != "rendering"
                    or file_hash(attempt.path / "state.json") != request["attempt_state_sha256"]
                    or file_hash(attempt.path / "job.json") != request["attempt_job_sha256"]):
                return False
            attempt._transition_locked("rendering", "failed", reason=reason, message=message)
            return True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

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


def make_bootstrap(path=BOOTSTRAP, *, deadline=None, cancel=None):
    """TD's blank base project plus a root-level Execute DAT running the batch hook."""
    cancel = cancel or Cancellation()
    deadline = deadline if deadline is not None else time.monotonic() + HELPER_TIMEOUT
    remaining(deadline, cancel)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        toe = tmp / "bootstrap.toe"
        shutil.copyfile(BASE_PROJECT, toe)
        # toeexpand exits 1 even when it succeeds (TD 2025.33230), so check its output instead.
        execute([str(TD_APP / "Contents/MacOS/toeexpand"), str(toe)],
                min(deadline, time.monotonic() + HELPER_TIMEOUT), cancel, stdout=subprocess.DEVNULL)
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
        collapsed = execute([str(TD_APP / "Contents/MacOS/toecollapse"), str(toe)],
                            min(deadline, time.monotonic() + HELPER_TIMEOUT), cancel, stdout=subprocess.DEVNULL)
        collapsed.check_returncode()
        remaining(deadline, cancel)
        shutil.copyfile(toe, path)
    return path


def td_running(*, deadline=None, cancel=None):
    cancel = cancel or Cancellation()
    deadline = min(deadline if deadline is not None else math.inf, time.monotonic() + 5)
    found = execute(["/usr/bin/pgrep", "-x", "TouchDesigner"], deadline, cancel,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if found.returncode not in (0, 1):
        raise RuntimeError("cannot determine whether TouchDesigner is already running")
    return [int(pid) for pid in found.stdout.split()]


def run(project, request, timeout, log_path, *, deadline=None, cancel=None):
    timeout = validate_timeout(timeout)
    if cancel is None:
        with cancellation_scope() as owned_cancel:
            return run(project, request, timeout, log_path, deadline=deadline, cancel=owned_cancel)
    started = time.monotonic()
    deadline = deadline if deadline is not None else started + timeout
    remaining(deadline, cancel)
    snapshot = export_snapshot(request)
    with tempfile.TemporaryDirectory(prefix="td-batch-") as tmp:
        tmp = pathlib.Path(tmp)
        result_path = tmp / "result.json"
        marker = tmp / "started-export.json"
        request = dict(request, **snapshot, result=str(result_path), started_export=str(marker),
                       request_token=secrets.token_hex(16), timeout_seconds=remaining(deadline, cancel))
        request_path = tmp / "request.json"
        request_path.write_text(json.dumps(request, indent=2, allow_nan=False), encoding="utf-8")
        env = dict(os.environ, **{REQUEST_ENV: str(request_path), "TOUCH_TEXT_CONSOLE": "1"})
        result, code, reason = {"ok": False, "errors": []}, 3, "render_failed"
        try:
            with open(log_path, "ab") as log:
                completed = execute([str(TD_BIN), str(project)], deadline, cancel,
                                    env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            code = completed.returncode
            value = json.loads(result_path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or type(value.get("ok")) is not bool
                    or not isinstance(value.get("errors"), list)
                    or any(not isinstance(e, str) for e in value["errors"])
                    or value.get("request_token") != request["request_token"]
                    or value.get("action") != request["action"]):
                raise ValueError("TouchDesigner returned an invalid or mismatched result")
            result = value
            if code != 0:
                result["errors"].append("TouchDesigner exited with status %s" % code)
            result["ok"] = result["ok"] and not result["errors"] and code == 0
        except (BatchCancelled, KeyboardInterrupt) as error:
            signum = error.signum if isinstance(error, BatchCancelled) else signal.SIGINT
            code, reason = 128 + signum, "cancelled"
            result = {"ok": False, "errors": ["batch cancelled by signal %s" % signum]}
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            result = {"ok": False, "errors": ["%s: %s" % (type(error).__name__, error)]}
            if code == 0:
                code = 3
        if not result["ok"]:
            try:
                result["attempt_marked_failed"] = fail_owned_export(request, marker, reason, "; ".join(result["errors"]))
            except Exception as error:
                result["errors"].append("attempt cleanup needs inspection: %s" % error)
        result["wall_seconds"] = round(time.monotonic() - started, 3)
        result["exit_code"] = code
        return result, code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=["probe", "build", "export", "probe-color"])
    parser.add_argument("attempt", nargs="?",
                        help="attempt directory (export) or output directory (probe-color)")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="whole-run seconds, >0 and <=3600 (default 900); cleanup allowance <=10s per owned child")
    parser.add_argument("--rebuild-bootstrap", action="store_true")
    parser.add_argument("--replace-template", action="store_true",
                        help="build: overwrite an existing td/wallpaper_template.toe")
    args = parser.parse_args(argv)
    try:
        args.timeout = validate_timeout(args.timeout)
    except ValueError as error:
        parser.error(str(error))
    deadline = time.monotonic() + args.timeout
    with cancellation_scope() as cancel:
        try:
            return perform(args, parser, deadline, cancel)
        except (BatchCancelled, KeyboardInterrupt) as error:
            signum = error.signum if isinstance(error, BatchCancelled) else signal.SIGINT
            print("Batch cancelled by signal %s" % signum, file=sys.stderr)
            return 128 + signum
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print("%s: %s" % (type(error).__name__, error), file=sys.stderr)
            return 3


def perform(args, parser, deadline, cancel):
    running = td_running(deadline=deadline, cancel=cancel)
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
        if args.action == "build" and TEMPLATE.exists() and not args.replace_template:
            parser.error(f"{TEMPLATE.relative_to(REPO)} exists; pass --replace-template to rebuild it")
        if args.rebuild_bootstrap or not BOOTSTRAP.is_file():
            make_bootstrap(BOOTSTRAP, deadline=deadline, cancel=cancel)
        project = BOOTSTRAP
        log_dir = REPO / "renders" / "td_batch_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{args.action}-{time.strftime('%Y%m%dT%H%M%S')}.log"
        if args.action == "build":
            request["output_project"] = str(TEMPLATE)
    result, code = run(project, request, args.timeout, log_path, deadline=deadline, cancel=cancel)
    result["log"] = os.path.relpath(log_path, REPO)
    if args.action == "build" and result.get("ok") is True and code == 0:
        # TD also writes its incremental-save name next to the template; an identical
        # copy would only make the opened-project check ambiguous, so remove it.
        numbered = TEMPLATE.with_name(TEMPLATE.stem + ".1.toe")
        if numbered.is_file() and TEMPLATE.is_file() and numbered.read_bytes() == TEMPLATE.read_bytes():
            numbered.unlink()
    print(json.dumps(result, indent=2, default=str))
    if code in (128 + signal.SIGINT, 128 + signal.SIGTERM):
        return code
    return 0 if result.get("ok") is True and not result.get("errors") and code == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
