"""Synthetic invariant tests only; no formal outcome or provider is used."""
from pathlib import Path
import copy
import json
import unittest
from formal_statistics import analyze, holm, paired_signflip, quality_safety_bound


def fixture():
    planned, runs = [], []
    for domain in ('a', 'b', 'c', 'd'):
        for task in range(2):
            for repeat in range(3):
                for index, arm in enumerate(('T0', 'S0', 'SS0', 'T1', 'S1', 'SS1')):
                    row = {'task_id': f'{domain}-{task}', 'chain_id': f'{domain}-chain',
                           'domain': domain, 'arm': arm, 'repeat': repeat, 'model': 'SYNTHETIC_TEST', 'topology': 'inproc'}
                    planned.append(row)
                    runs.append({**row, 'success': True, 'provider_input_tokens': 100 - index * 5,
                                 'observable_ipc_bytes': 1000 - index * 40})
    return planned, runs


class StatisticsInvariants(unittest.TestCase):
    def test_holm_planned_family_missing(self):
        self.assertEqual(holm([.01, .03, .04]), [.03, .06, .06])
        self.assertAlmostEqual(holm([.001] + [None] * 9)[0], .01)
        self.assertIsNone(holm([.001, None])[1])

    def test_signflip_exact_and_no_repeat_inflation(self):
        result = paired_signflip([1, 1, 1, 1])
        self.assertEqual(result['draws'], 16)
        self.assertEqual(result['p_raw'], 2 / 16)
        self.assertEqual(paired_signflip([0, 0])['p_raw'], 1)

    def test_all_pass_has_non_degenerate_safety_bound(self):
        plan, runs = fixture()
        report = analyze(runs, plan)
        self.assertEqual(report['planned_units'], 144)
        comparisons = report['strata'][0]['comparisons']
        for comparison in comparisons:
            quality = comparison['quality']
            self.assertEqual(quality['unique_tasks'], 8)
            self.assertEqual(quality['chain_count'], 4)
            self.assertEqual(quality['all_planned_unit_denominator_per_arm'], 24)
            self.assertLess(quality['quality_difference_one_sided_lower95'], -.05)
            self.assertLess(quality['quality_conservative_ci95'][0], 0)
            self.assertGreater(quality['quality_conservative_ci95'][1], 0)
            metric = comparison['metrics']['provider_input_tokens']
            self.assertEqual(metric['holm_family_size'], 10)
            self.assertEqual(metric['bootstrap_draws'], 10000)
            self.assertEqual(metric['draws'], 16)
            self.assertLess(metric['paired_difference'], 0)
            self.assertEqual(metric['both_success_auxiliary']['paired_units'], 24)

    def test_missing_quality_counted_cost_unknown(self):
        plan, runs = fixture()
        missing = next(i for i, r in enumerate(runs) if r['arm'] == 'S0')
        del runs[missing]
        report = analyze(runs, plan)
        comparison = report['strata'][0]['comparisons'][0]
        self.assertEqual(report['missing_units'], 1)
        self.assertEqual(comparison['planned_paired_units'], 24)
        self.assertLess(comparison['quality']['target_mean'], 1)
        self.assertEqual(comparison['metrics']['provider_input_tokens']['status'], 'INCOMPLETE')
        self.assertIsNone(comparison['metrics']['provider_input_tokens']['p_holm'])

    def test_duplicate_rejected(self):
        plan, runs = fixture()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            analyze(runs + [runs[0]], plan)

    def test_unequal_chain_subset_conservative_and_balanced_unchanged(self):
        pairs = []
        for chain, tasks in [('long', 6), ('short', 3)]:
            for task in range(tasks):
                for repeat in range(3):
                    pairs.append({'domain': 'a', 'chain_id': chain, 'task_id': f'{chain}-{task}',
                                  'repeat': repeat, 'a': {'success': True}, 'b': {'success': True}})
        result = quality_safety_bound(pairs)
        domain = result['domains'][0]
        self.assertEqual(domain['unequal_chain_length_conservative_factor'], 12 / 9)
        self.assertLess(result['quality_difference_one_sided_lower95'], -.05)
        balanced = [p for p in pairs if not p['task_id'].startswith('long-') or int(p['task_id'].split('-')[1]) < 3]
        original = quality_safety_bound(balanced)
        self.assertEqual(original['domains'][0]['unequal_chain_length_conservative_factor'], 1)
        self.assertLessEqual(result['quality_difference_one_sided_lower95'], original['quality_difference_one_sided_lower95'])

    def test_failed_low_cost_retained_with_warning(self):
        plan, runs = fixture()
        for row in runs:
            if row['arm'] == 'S0':
                row['success'] = False
                row['provider_input_tokens'] = 0
        comparison = analyze(runs, plan)['strata'][0]['comparisons'][0]
        self.assertEqual(comparison['quality']['target_mean'], 0)
        self.assertTrue(comparison['metrics']['provider_input_tokens']['early_failure_warning'])
        self.assertEqual(comparison['metrics']['provider_input_tokens']['both_success_auxiliary']['status'], 'NO_BOTH_SUCCESS_PAIRS')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StatisticsInvariants)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'status': 'PASS' if result.wasSuccessful() else 'FAILED', 'tests_run': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors),
              'scope': 'synthetic invariants; zero real provider calls; no formal outcomes used'}
    target = Path('/evidence/formal_20261019/analysis/stats/synthetic_invariants_v2.json')
    target.parent.mkdir(exist_ok=True)
    with target.open('x') as output:
        json.dump(report, output, indent=2)
    raise SystemExit(not result.wasSuccessful())
