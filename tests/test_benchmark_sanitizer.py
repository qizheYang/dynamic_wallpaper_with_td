"""Public schema-2 identity must preserve PID-reuse distinctions without dates."""
import pathlib
import sys
import unittest

BENCH = pathlib.Path(__file__).resolve().parents[1] / 'research/benchmarks'
sys.path.insert(0, str(BENCH))
from sanitize_results import collect_pids, collect_starts, normalize
from system_metrics import identity_matches


class BenchmarkSanitizerTests(unittest.TestCase):
    def test_identity_mapping_retains_restart_and_main_pid_consistency(self):
        original = {'probe_pid': 1234, 'unix_seconds': 1234567890,
                    'rows': [{'identity': {'pid': 1234, 'start_time': 'Tue Nov 17 10:20:30 2026'}, 'cpu_seconds': 12.34},
                             {'identity': {'pid': 1234, 'start_time': 'Tue Nov 17 10:20:30 2026'}, 'cpu_seconds': 13.34},
                             {'identity': {'pid': 1234, 'start_time': 'Tue Nov 17 11:20:30 2026'}, 'cpu_seconds': .34}]}
        pids, starts = set(), set()
        collect_pids(original, pids)
        collect_starts(original, starts)
        self.assertEqual(pids, {1234})
        normalized = normalize(original, [], {1234: 1}, {s: f'start-{i}' for i, s in enumerate(sorted(starts))})
        self.assertNotIn('unix_seconds', normalized)
        self.assertEqual(normalized['probe_pid'], 1)
        a,b,c = [r['identity'] for r in normalized['rows']]
        self.assertTrue(identity_matches(a,b))
        self.assertFalse(identity_matches(b,c))
        self.assertEqual([r['cpu_seconds'] for r in original['rows']], [r['cpu_seconds'] for r in normalized['rows']])
        self.assertNotIn('2026', str(normalized))

    def test_legacy_schema_without_start_map_still_normalizes(self):
        result = normalize({'pid': 42, 'path': '/project/video.mp4', 'value': 3.5}, [('/project','$PROJECT_ROOT')], {42:1})
        self.assertEqual(result, {'pid': 1, 'path': '$PROJECT_ROOT/video.mp4', 'value': 3.5})


if __name__ == '__main__': unittest.main()
