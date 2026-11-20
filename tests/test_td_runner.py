"""Runner regressions using temporary Python children and fake TD objects only."""
import contextlib
import importlib.util
import io
import json
import math
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

from pipeline.attempt import create_attempt
from pipeline.job import validate_job

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load("reviewed_td_host", "td/run_td.py")
inside = load("reviewed_td_inside", "td/controller/td_runner.py")

CHILD = '''import json,os,pathlib,signal,sys,time
r=json.loads(pathlib.Path(os.environ["WALLPAPER_TD_REQUEST"]).read_text())
mode=os.environ.get("FAKE_TD_MODE","normal")
if r["action"]=="export" and mode!="refuse":
    keys=("request_token","attempt","attempt_state_sha256","attempt_job_sha256")
    marker={k:r[k] for k in keys}
    if mode=="wrong-owner": marker["request_token"]="someone-else"
    pathlib.Path(r["started_export"]).write_text(json.dumps(marker))
if mode=="terminal":
    p=pathlib.Path(r["attempt"])/"state.json"
    state=json.loads(p.read_text());state["state"]="complete";p.write_text(json.dumps(state))
if "FAKE_TD_READY" in os.environ:
    pathlib.Path(os.environ["FAKE_TD_READY"]).write_text(str(os.getpid()))
if mode=="hang": time.sleep(30)
if mode=="missing": sys.exit(4)
value={"ok":mode not in ("refuse","wrong-owner","terminal"),"errors":[],
       "request_token":r["request_token"],"action":r["action"]}
if not value["ok"]: value["errors"]=["fake export refused or failed"]
if mode=="bad-bool": value["ok"]=1
if mode=="stale-token": value["request_token"]="old-run"
if mode=="wrong-action": value["action"]="another-action"
if mode=="invalid": value=[]
pathlib.Path(r["result"]).write_text(json.dumps(value))
sys.exit(int(os.environ.get("FAKE_TD_EXIT","0")))
'''


class TDRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="td-runner-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name)
        self.child = self.path / "fake_child.py"
        self.child.write_text(CHILD)
        job = json.loads((ROOT / "jobs/gradient_seed42_720p24.json").read_text())
        self.attempt = create_attempt(validate_job(job), self.path, "test-attempt")

    def run_fake(self, mode="normal", code=0, timeout=5, export=False):
        request = {"action": "export", "attempt": str(self.attempt.path)} if export else {"action": "probe"}
        with patch.object(host, "TD_BIN", pathlib.Path(sys.executable)), \
             patch.dict(os.environ, FAKE_TD_MODE=mode, FAKE_TD_EXIT=str(code)):
            return host.run(self.child, request, timeout, self.path / "child.log")

    def test_invalid_timeouts_are_rejected_before_any_mutation_or_process(self):
        before = sorted(str(p.relative_to(self.path)) for p in self.path.rglob("*"))
        with patch.object(host, "td_running") as query, patch.object(host, "make_bootstrap") as build, \
             patch.object(host, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            for value in ("nan", "inf", "-inf", "0", "-1", "3600.001"):
                with self.subTest(value=value), self.assertRaises(SystemExit) as error:
                    host.main(["probe", "--timeout=" + value])
                self.assertEqual(error.exception.code, 2)
        query.assert_not_called(); build.assert_not_called(); run.assert_not_called()
        self.assertEqual(before, sorted(str(p.relative_to(self.path)) for p in self.path.rglob("*")))
        for value in (math.nan, math.inf, -1, 0, 3601, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.run(self.child, {"action": "probe"}, value, self.path / "invalid.log")
        self.assertFalse((self.path / "invalid.log").exists())

    def test_normal_result_and_handler_restoration(self):
        handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        result, code = self.run_fake()
        self.assertTrue(result["ok"]); self.assertEqual(code, 0)
        self.assertEqual(handlers, {s: signal.getsignal(s) for s in handlers})

    def test_success_file_does_not_hide_nonzero_shutdown(self):
        result, code = self.run_fake(code=17, export=True)
        self.assertFalse(result["ok"]); self.assertEqual(code, 17)
        self.assertIn("status 17", " ".join(result["errors"]))
        self.assertEqual(self.attempt.state()["state"], "failed")

    def test_cli_also_requires_zero_exit(self):
        bootstrap = self.path / "bootstrap.toe"; bootstrap.write_bytes(b"fake")
        with patch.object(host, "BOOTSTRAP", bootstrap), patch.object(host, "REPO", self.path), \
             patch.object(host, "td_running", return_value=[]), \
             patch.object(host, "run", return_value=({"ok": True, "errors": []}, -11)), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(host.main(["probe"]), 3)

    def test_invalid_or_missing_results_fail(self):
        for mode in ("invalid", "missing", "bad-bool", "stale-token", "wrong-action"):
            with self.subTest(mode=mode):
                result, _code = self.run_fake(mode)
                self.assertFalse(result["ok"])
                self.assertTrue(result["errors"])

    def test_timeout_fails_only_owned_incomplete_attempt(self):
        result, _code = self.run_fake("hang", timeout=.2, export=True)
        self.assertFalse(result["ok"])
        self.assertTrue(result["attempt_marked_failed"])
        self.assertEqual(self.attempt.state()["state"], "failed")

    def test_td_refusal_does_not_change_attempt(self):
        before = (self.attempt.path / "state.json").read_bytes()
        result, _code = self.run_fake("refuse", export=True)
        self.assertFalse(result["ok"]); self.assertFalse(result["attempt_marked_failed"])
        self.assertEqual(before, (self.attempt.path / "state.json").read_bytes())

    def test_wrong_owner_and_externally_terminal_state_are_preserved(self):
        before = (self.attempt.path / "state.json").read_bytes()
        result, _code = self.run_fake("wrong-owner", export=True)
        self.assertFalse(result["attempt_marked_failed"])
        self.assertEqual(before, (self.attempt.path / "state.json").read_bytes())
        result, _code = self.run_fake("terminal", export=True)
        self.assertFalse(result["attempt_marked_failed"])
        self.assertEqual(self.attempt.state()["state"], "complete")

    def test_preexisting_terminal_attempt_never_launches_or_gets_a_log(self):
        self.attempt.fail("test", "terminal fixture")
        before = (self.attempt.path / "state.json").read_bytes()
        with patch.object(host, "execute") as execute, self.assertRaises(ValueError):
            self.run_fake(export=True)
        execute.assert_not_called()
        self.assertFalse((self.path / "child.log").exists())
        self.assertEqual(before, (self.attempt.path / "state.json").read_bytes())

    def test_existing_interactive_session_is_not_touched(self):
        with patch.object(host, "td_running", return_value=[123]), patch.object(host, "run") as run, \
             patch.object(host, "make_bootstrap") as build, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(host.main(["build", "--replace-template"]), 2)
        run.assert_not_called(); build.assert_not_called()

    def test_pgrep_error_is_not_treated_as_no_session(self):
        with patch.object(host, "execute", return_value=subprocess.CompletedProcess([], 2, "", "error")):
            with self.assertRaises(RuntimeError): host.td_running()

    def test_bootstrap_helpers_share_deadline_and_do_not_publish_when_hung(self):
        base = self.path / "base.toe"; base.write_bytes(b"base")
        destination = self.path / "bootstrap.toe"
        limit = time.monotonic() + 1
        with patch.object(host, "BASE_PROJECT", base), \
             patch.object(host, "execute", side_effect=TimeoutError("hung expand")) as execute:
            with self.assertRaises(TimeoutError):
                host.make_bootstrap(destination, deadline=limit)
        self.assertLessEqual(execute.call_args.args[1], limit)
        self.assertFalse(destination.exists())

    def test_bootstrap_collapse_is_also_bounded(self):
        base = self.path / "base.toe"; base.write_bytes(b"base")
        destination = self.path / "bootstrap.toe"
        limit = time.monotonic() + 1
        seen = []
        def fake_execute(command, deadline, cancel, **kwargs):
            seen.append(deadline)
            self.assertLessEqual(deadline, limit)
            if pathlib.Path(command[0]).name == "toeexpand":
                toe = pathlib.Path(command[1])
                expanded = toe.with_name(toe.name + ".dir"); expanded.mkdir()
                (expanded / "project1.n").write_text("fixture")
                toe.with_name(toe.name + ".toc").write_text("project1.n\n")
                return subprocess.CompletedProcess(command, 1)
            raise TimeoutError("hung collapse")
        with patch.object(host, "BASE_PROJECT", base), patch.object(host, "execute", side_effect=fake_execute):
            with self.assertRaises(TimeoutError):
                host.make_bootstrap(destination, deadline=limit)
        self.assertEqual(len(seen), 2)
        self.assertFalse(destination.exists())

    def test_expired_shared_deadline_never_launches(self):
        with patch.object(host, "execute") as execute, self.assertRaises(TimeoutError):
            host.run(self.child, {"action": "probe"}, 10, self.path / "expired.log",
                     deadline=time.monotonic() - 1)
        execute.assert_not_called()
        self.assertFalse((self.path / "expired.log").exists())

    def test_hung_child_is_killed_after_ignoring_terminate(self):
        proc = subprocess.Popen([sys.executable, "-c", "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('ready',flush=True);time.sleep(30)"],
                                stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            with patch.object(host, "TERMINATE_SECONDS", .1), patch.object(host, "KILL_SECONDS", 1):
                host.terminate_owned(proc)
            self.assertIsNotNone(proc.returncode)
        finally:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=2)
            proc.stdout.close()

    def test_keyboardinterrupt_cleans_owned_child(self):
        child = Mock()
        child.poll.side_effect = [None]
        child.communicate.side_effect = KeyboardInterrupt()
        with patch.object(host.subprocess, "Popen", return_value=child):
            with self.assertRaises(host.BatchCancelled):
                host.execute(["fake"], time.monotonic() + 1, host.Cancellation())
        child.terminate.assert_called_once(); child.wait.assert_called_once()

    def test_real_wrapper_sigterm_cleans_only_its_child_and_marks_cancelled(self):
        ready = self.path / "ready.pid"
        output = self.path / "wrapper.json"
        launcher = self.path / "wrapper.py"
        launcher.write_text(
            "import importlib.util,json,pathlib,sys\n"
            "s=importlib.util.spec_from_file_location('host',%r);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
            "m.TD_BIN=pathlib.Path(sys.executable)\n"
            "r,c=m.run(pathlib.Path(%r),{'action':'export','attempt':%r},10,pathlib.Path(%r))\n"
            "pathlib.Path(%r).write_text(json.dumps(r));sys.exit(c)\n" %
            (str(ROOT / "td/run_td.py"), str(self.child), str(self.attempt.path), str(self.path / "signal.log"), str(output)))
        env = dict(os.environ, FAKE_TD_MODE="hang", FAKE_TD_READY=str(ready))
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        wrapper = subprocess.Popen([sys.executable, str(launcher)], env=env)
        pid = None
        try:
            until = time.monotonic() + 5
            while not ready.exists() and wrapper.poll() is None and time.monotonic() < until: time.sleep(.02)
            self.assertTrue(ready.exists(), "fake TD did not start")
            pid = int(ready.read_text())
            wrapper.send_signal(signal.SIGTERM)
            self.assertEqual(wrapper.wait(timeout=8), 143)
            self.assertFalse(json.loads(output.read_text())["ok"])
            self.assertEqual(self.attempt.state()["failure"]["reason"], "cancelled")
            with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
            self.assertIsNone(unrelated.poll())
        finally:
            for process in (wrapper, unrelated):
                if process.poll() is None: process.kill()
                process.wait(timeout=2)
            if pid:
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def test_td_side_invalid_timeout_and_preflight_refusal(self):
        fake_td = types.SimpleNamespace(run=Mock(), project=object())
        request = self.path / "request.json"
        result = self.path / "result.json"
        with patch.dict(sys.modules, td=fake_td):
            for value in (0, -1, math.inf, math.nan, 3601):
                request.write_text(json.dumps({"result": str(result), "timeout_seconds": value}))
                runner = inside.Runner(request)
                runner._step("begin")
                self.assertFalse(json.loads(result.read_text())["ok"])
            controller = Mock(); controller.check.return_value = ["uncalibrated"]
            fake_td.op = Mock(return_value=types.SimpleNamespace(op=Mock(return_value=object())))
            module = types.SimpleNamespace(TDAdapter=Mock(), ExportController=Mock(return_value=controller))
            with patch.dict(sys.modules, export_controller=module):
                runner = inside.Runner(request)
                runner.request = {"attempt": str(self.attempt.path), "started_export": str(self.path / "marker")}
                with self.assertRaisesRegex(RuntimeError, "refused"): runner.export()
                controller.start.assert_not_called()
                self.assertIsNone(runner.controller)
                self.assertFalse((self.path / "marker").exists())

    def test_td_side_claims_export_only_after_checks_and_snapshot_match(self):
        marker = self.path / "marker"
        controller = Mock(); controller.check.return_value = []
        template = types.SimpleNamespace(op=Mock(return_value=object()))
        fake_td = types.SimpleNamespace(run=Mock(), project=object(), op=Mock(return_value=template))
        module = types.SimpleNamespace(TDAdapter=Mock(), ExportController=Mock(return_value=controller))
        request = {"action": "export", "attempt": str(self.attempt.path), "request_token": "test-token",
                   "started_export": str(marker)}
        request.update(host.export_snapshot(request))
        with patch.dict(sys.modules, td=fake_td, export_controller=module):
            runner = inside.Runner(self.path / "request.json")
            runner.request = dict(request, attempt_job_sha256="wrong")
            with self.assertRaisesRegex(RuntimeError, "attempt changed"): runner.export()
            controller.start.assert_not_called(); self.assertFalse(marker.exists())
            runner.request = request
            runner.export()
            controller.start.assert_called_once()
        saved = json.loads(marker.read_text())
        self.assertEqual(saved["request_token"], "test-token")
        self.assertEqual(saved["attempt_state_sha256"], request["attempt_state_sha256"])


if __name__ == "__main__":
    unittest.main()
