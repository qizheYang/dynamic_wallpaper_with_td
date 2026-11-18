"""Pure/synthetic checks for phase alignment, identity guards and wrapper cleanup."""
import copy
import contextlib
import io
import json
import pathlib
import signal
import sys
import tempfile
import types
import unittest
from unittest import mock

BENCH = pathlib.Path(__file__).resolve().parents[1] / 'research/benchmarks'
sys.path.insert(0, str(BENCH))
import measure
import summarize
import summarize_v2 as v2


def process(cpu, start='start-a', pid=7, name='WindowServer'):
    return {'pid': pid, 'name': name, 'identity': {'pid': pid, 'start_time': start},
            'cpu_seconds': cpu, 'rss_bytes': 1000}


def sample(at, cpu=1, start='start-a'):
    return {'started_monotonic_seconds': at, 'finished_monotonic_seconds': at + .1,
            'processes': [process(cpu, start)], 'errors': []}


class ScheduleTests(unittest.TestCase):
    def test_bounded_opt_in_and_cover(self):
        measure.parse_schedule('idle:120')
        measure.parse_schedule('idle:3600', sustained=True)
        for value in ('idle:121', 'idle:3601'):
            with self.assertRaises(ValueError):
                measure.parse_schedule(value)
        with self.assertRaises(ValueError):
            measure.parse_schedule('cover:1')
        self.assertEqual(len(measure.parse_schedule('cover:.5,uncover:5e-1', cover=True)), 2)

    def test_rejects_ambiguous_or_unbounded_schedules(self):
        for value in ('', ',idle:1', 'idle:1,', 'idle::1', 'idle:0', 'idle:-1', 'idle:nan',
                      'idle:inf', 'idle:1e309', 'idle: 1', 'idle:1 ', 'bad:1', 'idle:.9'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                measure.parse_schedule(value, sustained=True)

    def test_probe_validation_failure_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            probe, output = root / 'probe', root / 'new-run'
            probe.write_text('stub')
            with mock.patch.object(measure, 'validate_probe', side_effect=ValueError('old probe')):
                with self.assertRaises(SystemExit) as error, contextlib.redirect_stderr(io.StringIO()):
                    measure.main(['--probe', str(probe), '--output-dir', str(output), '--schedule', 'idle:1'])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(output.exists())


    def test_non_object_or_null_probe_configuration_is_rejected(self):
        for value in ([], None, {"schema_version": 2, "total_seconds": None}):
            reply = types.SimpleNamespace(returncode=0, stdout=json.dumps(value))
            with mock.patch.object(measure.subprocess, 'run', return_value=reply), self.assertRaises(ValueError):
                measure.validate_probe(['stub'], 1)


class IdentityTests(unittest.TestCase):
    def test_cpu_does_not_bridge_pid_reuse_or_counter_reset(self):
        old = sample(1, 10)
        for current in (sample(3, 1, start='reused'), sample(3, 1)):
            measure.add_cpu_deltas(current, old)
            self.assertIsNone(current['processes'][0]['cpu_percent_one_core'])
            self.assertNotEqual(current['processes'][0]['cpu_delta_status'], 'ok')
        current = sample(3, 10.04)
        measure.add_cpu_deltas(current, old)
        self.assertAlmostEqual(current['processes'][0]['cpu_percent_one_core'], 2)

    def test_footprint_join_rejects_reused_pid(self):
        foot = {'footprints': [{'pid': 7, 'physical_footprint_bytes': 4096}], 'errors': []}
        joined = measure.join_footprints(foot, sample(1), sample(3, start='reused'))
        self.assertIsNone(joined['footprints'][0]['physical_footprint_bytes'])
        self.assertEqual(joined['errors'][0]['code'], 'identity_unverified')

    def test_partial_probe_record_is_read_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'probe.jsonl'
            reader = measure.ProbeReader(path)
            self.assertIsNone(reader.poll())
            path.write_text('{"event":"sample","phase":"play"')
            self.assertIsNone(reader.poll())
            with path.open('a') as handle:
                handle.write('}\nnot json\n')
            self.assertEqual(reader.poll()['phase'], 'play')
            reader.poll()
            self.assertEqual(len(reader.errors), 1)


class SummaryTests(unittest.TestCase):
    def test_rejects_intervals_crossing_phase_boundary(self):
        self.assertFalse(v2.contained({'started_monotonic_seconds': 9.9, 'finished_monotonic_seconds': 10.2}, 0, 10))
        self.assertTrue(v2.contained({'started_monotonic_seconds': 9.5, 'finished_monotonic_seconds': 9.9}, 0, 10))

    def test_windowserver_restart_or_gap_is_not_zero_cpu(self):
        for samples in ([sample(1, 10), sample(3, 1, start='reused')],
                        [sample(1, 10), sample(3, 1)],
                        [sample(1, 10), {**sample(2), 'processes': []}, sample(3, 10.1)]):
            self.assertEqual(v2.process_cpu(samples, 'WindowServer')['status'], 'inconclusive')
        observed = v2.process_cpu([sample(1, 10), sample(3, 10.04)], 'WindowServer')
        self.assertEqual(observed['status'], 'observed')
        self.assertAlmostEqual(observed['cpu_percent_one_core'], 2)

    def test_gpu_devices_are_separate_and_missing_not_zero(self):
        rows = [{'gpu_global': [{'device_id': 'a', 'Device Utilization %': 0},
                                {'device_id': 'b', 'Device Utilization %': 80}]}]
        observed = v2.gpu_stats(rows)
        self.assertEqual(observed['devices']['a']['mean'], 0)
        self.assertEqual(observed['devices']['b']['mean'], 80)
        self.assertEqual(v2.gpu_stats([{'gpu_global': []}])['status'], 'inconclusive')
        self.assertEqual(v2.gpu_stats(rows + [{'gpu_global': []}])['status'], 'inconclusive')

    def test_legacy_four_run_results_are_unchanged(self):
        base = BENCH / 'results/2026-11-10'
        saved = json.loads((base / 'summary.json').read_text())['runs']
        for expected in saved:
            self.assertEqual(summarize.summarize(base / expected['run']), expected)

    def test_schema2_uses_real_interval_not_stale_phase_label(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            rows = [{'event': 'phase', 'phase': 'idle', 'phase_index': 0, 'monotonic_seconds': 100},
                    {'event': 'phase', 'phase': 'play', 'phase_index': 1, 'monotonic_seconds': 113, 'phase_start_monotonic_seconds': 110},
                    {'event': 'sample', 'phase': 'play', 'phase_index': 1, 'monotonic_seconds': 115, 'physical_footprint_bytes': None},
                    {'event': 'session_resigned_active', 'monotonic_seconds': 116, 'elapsed_seconds': 16},
                    {'event': 'finish', 'reason': 'schedule_complete', 'monotonic_seconds': 120}]
            systems = [{'probe': {'phase': 'idle', 'phase_index': 0},
                        'process_sample': sample(112, 10),
                        'gpu_sample': {'started_monotonic_seconds': 112, 'finished_monotonic_seconds': 112.1,
                                       'gpu_global': [{'device_id': 'a', 'Device Utilization %': 5}]}},
                       {'process_sample': sample(114, 10.04)}]
            (root / 'metadata.json').write_text(json.dumps({'schema_version': 2, 'warmup_seconds': 0, 'tail_guard_seconds': 0}))
            (root / 'probe.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            (root / 'system.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in systems))
            result = summarize.summarize(root)
            self.assertEqual(result['phases'][0]['process_samples'], 0)
            self.assertEqual(result['phases'][1]['process_samples'], 2)
            self.assertEqual(result['phases'][1]['gpu_samples'], 1)
            self.assertEqual(result['acceptance']['status'], 'inconclusive')
            self.assertFalse(result['phases'][1]['playback_observed'])
            self.assertIsNone(result['phases'][1]['player_footprint_mib'])
            self.assertEqual(result['lifecycle_events'][0]['event'], 'session_resigned_active')


class CleanupTests(unittest.TestCase):
    def test_wrapper_cancel_terminates_owned_probe_and_records_signal(self):
        class Child:
            pid = 123
            returncode = None
            def poll(self): return self.returncode
            def terminate(self): self.returncode = -signal.SIGTERM
            def kill(self): self.returncode = -signal.SIGKILL
            def wait(self, timeout=None): return self.returncode
        child = Child()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            probe = root / 'probe'
            probe.write_text('stub')
            args = types.SimpleNamespace(output_dir=root/'run', probe=probe, video=None, purpose='smoke',
                                         sample_interval=2, footprint_interval=10, warmup_seconds=0)
            handlers = {}
            def register(sig, handler):
                old = handlers.get(sig, signal.SIG_DFL)
                handlers[sig] = handler
                return old
            def cancel(*args, **kwargs):
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                return {'process_sample': sample(1), 'sampler_seconds': 0}
            with mock.patch.object(measure.subprocess, 'Popen', return_value=child), \
                 mock.patch.object(measure, 'collect_system', side_effect=cancel), \
                 mock.patch.object(measure, 'power_context', return_value={}), \
                 mock.patch.object(measure.platform, 'platform', return_value='test'), \
                 mock.patch.object(measure.signal, 'signal', side_effect=register):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = measure.run(args, [{'state':'idle','seconds':1}], ['stub'], {})
            self.assertEqual(code, 143)
            self.assertEqual(child.returncode, -signal.SIGTERM)
            metadata = json.loads((root/'run/metadata.json').read_text())
            self.assertEqual(metadata['run_status'], 'cancelled')
            self.assertEqual(metadata['probe_exit_code'], -signal.SIGTERM)
            self.assertIn('wall_seconds', metadata)
            self.assertEqual(handlers[signal.SIGTERM], signal.SIG_DFL)


if __name__ == '__main__':
    unittest.main()
