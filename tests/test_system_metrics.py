"""Sampler contracts and failure semantics without invoking system tools."""
import importlib.util
import pathlib
import subprocess
import unittest
from unittest import mock


MODULE = pathlib.Path(__file__).resolve().parents[1] / "research/benchmarks/system_metrics.py"
SPEC = importlib.util.spec_from_file_location("system_metrics_under_test", MODULE)
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def process_line(pid, command, start="Mon Nov  9 12:27:08 2026", cpu="598:16.09", rss="2048"):
    return "%s %s %s %s %s" % (pid, start, cpu, rss, command)


def gpu_entry(name="GPU0", entry_id="0x100001", utilization=0):
    return ('+-o %s  <class AGXAccelerator, id %s, registered, active>\n'
            '  | "PerformanceStatistics" = {"Device Utilization %%"=%s,'
            '"Renderer Utilization %%"=0,"Tiler Utilization %%"=0,'
            '"In use system memory"=1024,"Alloc system memory"=4096}\n'
            % (name, entry_id, utilization))


class ProcessParsingTests(unittest.TestCase):
    def test_selected_rows_keep_identity_and_cumulative_cpu_without_arguments(self):
        text = "\n".join([
            process_line(101, "/System/Library/WindowServer"),
            process_line(102, "/System/Library/VTDecoderXPCService", cpu="0:00.00", rss="0"),
            process_line(103, "/Applications/Some Player.app/Contents/MacOS/Some Player"),
            process_line(104, "/Applications/Unrelated Secret Project"),
        ])
        rows, errors = metrics.parse_processes(text, player_pid=103)
        self.assertEqual(errors, [])
        self.assertEqual([row["pid"] for row in rows], [101, 102, 103])
        self.assertEqual(rows[0]["identity"], {"pid": 101, "start_time": "Mon Nov 9 12:27:08 2026"})
        self.assertAlmostEqual(rows[0]["cpu_seconds"], 598 * 60 + 16.09)
        self.assertEqual(rows[0]["rss_bytes"], 2048 * 1024)
        self.assertEqual(rows[1]["cpu_seconds"], 0)
        self.assertEqual(rows[1]["rss_bytes"], 0)
        self.assertEqual(rows[2]["name"], "Some Player")
        self.assertNotIn("/Applications", repr(rows))
        self.assertNotIn("Secret", repr(rows))

    def test_unknown_or_invalid_start_does_not_create_pid_only_identity(self):
        for start in ["-", "?", "Mon Bad 9 12:27:08 2026", "Mon Feb 31 12:27:08 2026"]:
            with self.subTest(start=start):
                rows, errors = metrics.parse_processes(process_line(101, "/System/WindowServer", start=start))
                self.assertIsNone(rows[0]["identity"])
                self.assertIsNotNone(rows[0]["cpu_seconds"])
                self.assertIn("identity_unavailable", [error["code"] for error in errors])

    def test_missing_cpu_and_rss_remain_null(self):
        rows, errors = metrics.parse_processes(process_line(101, "WindowServer", cpu="?", rss="?"))
        self.assertIsNone(rows[0]["cpu_seconds"])
        self.assertIsNone(rows[0]["rss_bytes"])
        self.assertEqual({error["code"] for error in errors}, {"cpu_unavailable", "rss_unavailable"})

    def test_absent_requested_player_is_an_error(self):
        rows, errors = metrics.parse_processes(process_line(101, "WindowServer"), player_pid=999)
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors[-1]["code"], "process_missing")
        self.assertEqual(errors[-1]["pid"], 999)

    def test_empty_or_unrecognized_ps_is_not_an_empty_success(self):
        for text in ["", "unexpected ps output"]:
            with self.subTest(text=text):
                rows, errors = metrics.parse_processes(text)
                self.assertEqual(rows, [])
                self.assertEqual(errors[0]["code"], "selected_processes_missing")

    def test_time_formats_and_invalid_counters(self):
        for text, expected in [("00:00.00", 0), ("123:45.67", 7425.67),
                               ("2:03:04.5", 7384.5), ("1-02:03:04.50", 93784.5)]:
            with self.subTest(text=text):
                self.assertAlmostEqual(metrics.parse_cpu_seconds(text), expected)
        for text in ["-1:00", "1:60.0", "1:60:00", "nan", "1:NaN", "1:inf", "1"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                metrics.parse_cpu_seconds(text)

    def test_identity_reuse_or_unknown_never_matches(self):
        identity = {"pid": 101, "start_time": "Mon Nov 9 12:27:08 2026"}
        self.assertTrue(metrics.identity_matches(identity, dict(identity)))
        for other in [None, {}, {"pid": 101, "start_time": None},
                      {"pid": 101, "start_time": "Mon Nov 9 12:27:09 2026"},
                      {"pid": 102, "start_time": identity["start_time"]}]:
            with self.subTest(other=other):
                self.assertFalse(metrics.identity_matches(identity, other))
        self.assertFalse(metrics.identity_matches(None, None))
        self.assertFalse(metrics.identity_matches({"pid": 1, "start_time": "x"}, {"pid": True, "start_time": "x"}))


class GPUParsingTests(unittest.TestCase):
    def test_multiple_gpu_entries_preserve_boot_ids_and_zero(self):
        rows, errors = metrics.parse_gpu(gpu_entry() + gpu_entry("GPU1", "0x100002", 25))
        self.assertEqual(errors, [])
        self.assertEqual([row["device_id"] for row in rows], ["0x100001", "0x100002"])
        self.assertEqual([row["Device Utilization %"] for row in rows], [0, 25])
        self.assertEqual(rows[0]["device_id_scope"], "boot")
        self.assertEqual(rows[0]["In use system memory"], 1024)

    def test_missing_and_negative_counters_stay_null(self):
        text = gpu_entry(utilization=-1).replace('"Renderer Utilization %"=0,', '')
        rows, errors = metrics.parse_gpu(text)
        self.assertIsNone(rows[0]["Device Utilization %"])
        self.assertIsNone(rows[0]["Renderer Utilization %"])
        self.assertEqual(rows[0]["Tiler Utilization %"], 0)
        self.assertEqual(len(errors), 2)

    def test_unknown_device_does_not_reuse_previous_identifier(self):
        unknown = gpu_entry("GPU1", "0x100002").replace("id 0x100002,", "unknown,")
        rows, errors = metrics.parse_gpu(gpu_entry() + unknown)
        self.assertIsNone(rows[1]["device_id"])
        self.assertIn("device_identity_unavailable", [error["code"] for error in errors])
        rows, errors = metrics.parse_gpu("no statistics")
        self.assertEqual(rows, [])
        self.assertEqual(errors[0]["code"], "counters_missing")


class FootprintParsingTests(unittest.TestCase):
    def test_binary_units_decimals_and_change_markers(self):
        for text, expected in [("0B", 0), ("512", 512), ("7840K", 7840 * 1024),
                               ("1102M", 1102 * 1024 ** 2), ("1.5G+", 3 * 1024 ** 3 // 2),
                               ("15M-", 15 * 1024 ** 2), ("1.25MiB", 1310720),
                               ("2kb", 2048), ("1.1M", 1153434)]:
            with self.subTest(text=text):
                self.assertEqual(metrics.parse_top_memory(text), expected)
        for text in ["N/A", "-", "?", "--"]:
            self.assertIsNone(metrics.parse_top_memory(text))
        for text in ["-2M", "nan", "5%", "1.2.3G", "3M++"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                metrics.parse_top_memory(text)

    def test_targeted_rows_missing_is_not_zero_and_identity_requires_join(self):
        text = "Processes: 100 total\nPID MEM\n101+ 1102M\n102 0B\n103 5M\n104 N/A\n"
        rows, errors = metrics.parse_top_footprints(text, [101, 102, 104, 999])
        self.assertEqual([row["pid"] for row in rows], [101, 102, 104, 999])
        self.assertEqual(rows[0]["physical_footprint_bytes"], 1102 * 1024 ** 2)
        self.assertEqual(rows[0]["display_value"], "1102M")
        self.assertEqual(rows[0]["identity_status"], "not_checked")
        self.assertTrue(rows[0]["rounded"])
        self.assertEqual(rows[1]["physical_footprint_bytes"], 0)
        self.assertIsNone(rows[2]["physical_footprint_bytes"])
        self.assertIsNone(rows[3]["physical_footprint_bytes"])
        self.assertEqual({error["code"] for error in errors}, {"footprint_unavailable", "process_missing"})

    def test_no_header_or_duplicate_sample_cannot_be_accepted(self):
        for text in ["101 20M\n", "PID MEM\n101 20M\n101 22M\n"]:
            with self.subTest(text=text):
                rows, errors = metrics.parse_top_footprints(text, [101])
                self.assertIsNone(rows[0]["physical_footprint_bytes"])
                self.assertTrue(errors)


class SamplerExecutionTests(unittest.TestCase):
    def test_explicit_monotonic_clock(self):
        with mock.patch.object(metrics.time, "clock_gettime", return_value=42.0) as clock:
            self.assertEqual(metrics.monotonic_seconds(), 42.0)
        clock.assert_called_once_with(metrics.time.CLOCK_MONOTONIC)

    def test_process_locale_watchlist_and_interval(self):
        text = "\n".join([process_line(101, "WindowServer"), process_line(201, "TouchDesigner"),
                          process_line(202, "/Applications/QuickTime Player.app/Contents/MacOS/QuickTime Player"),
                          process_line(203, "/usr/local/bin/ffmpeg"), process_line(204, "ffmpeg"),
                          process_line(205, "PrivateUnrelatedApp")])
        result = subprocess.CompletedProcess([], 0, stdout=text)
        with mock.patch.object(metrics.subprocess, "run", return_value=result) as run, \
                mock.patch.object(metrics, "monotonic_seconds", side_effect=[10.0, 10.2]):
            sample = metrics.sample_processes(timeout=2.5)
        self.assertEqual(sample["activity_names"], ["QuickTime Player", "TouchDesigner", "ffmpeg"])
        self.assertNotIn("PrivateUnrelatedApp", repr(sample))
        self.assertEqual(sample["started_monotonic_seconds"], 10.0)
        self.assertEqual(sample["finished_monotonic_seconds"], 10.2)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(run.call_args.kwargs["timeout"], 2.5)
        self.assertNotIn("args=", " ".join(run.call_args.args[0]))

    def test_timeout_is_an_error_not_an_empty_success(self):
        with mock.patch.object(metrics.subprocess, "run", side_effect=subprocess.TimeoutExpired("private command", 1)):
            for sampler in [metrics.sample_processes, metrics.sample_gpu, lambda: metrics.sample_footprints([101])]:
                with self.subTest(sampler=sampler):
                    sample = sampler()
                    self.assertEqual(sample["errors"][0]["code"], "timeout")
                    self.assertNotIn("private command", repr(sample))
                    self.assertGreaterEqual(sample["finished_monotonic_seconds"], sample["started_monotonic_seconds"])
        self.assertIsNone(sample["footprints"][0]["physical_footprint_bytes"])

    def test_tool_failure_keeps_private_stderr_out_and_does_not_parse_partial_output(self):
        result = subprocess.CompletedProcess([], 1, stdout=gpu_entry(), stderr="/Users/private-name/private-argument")
        with mock.patch.object(metrics.subprocess, "run", return_value=result):
            sample = metrics.sample_gpu()
        self.assertEqual(sample["gpu_global"], [])
        self.assertEqual(sample["errors"][0]["code"], "command_failed")
        self.assertNotIn("private-name", repr(sample))
        with mock.patch.object(metrics.subprocess, "run", side_effect=PermissionError(1, "secret path")):
            sample = metrics.sample_processes()
        self.assertEqual(sample["errors"][0]["code"], "os_error")
        self.assertNotIn("secret path", repr(sample))

    def test_top_is_finite_targeted_and_never_invoked_with_empty_pid_set(self):
        result = subprocess.CompletedProcess([], 0, stdout="PID MEM\n101 1M\n102 2M\n")
        with mock.patch.object(metrics.subprocess, "run", return_value=result) as run:
            sample = metrics.sample_footprints([101, 102, 101])
            self.assertEqual(run.call_args.args[0], ["/usr/bin/top", "-l", "1", "-stats", "pid,mem", "-pid", "101", "-pid", "102"])
            self.assertEqual(len(sample["footprints"]), 2)
            run.reset_mock()
            self.assertEqual(metrics.sample_footprints([])["footprints"], [])
            run.assert_not_called()

    def test_invalid_inputs_cannot_reach_subprocess(self):
        with mock.patch.object(metrics.subprocess, "run") as run:
            for pid in [0, -1, True, "101"]:
                with self.subTest(pid=pid), self.assertRaises(ValueError):
                    metrics.sample_footprints([pid])
            for timeout in [0, -1, float("inf"), float("nan"), True, "5"]:
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    metrics.sample_gpu(timeout=timeout)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
