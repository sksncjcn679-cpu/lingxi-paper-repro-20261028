"""Small pure-data checks for the new secondary-analysis boundaries.

Prepared during collection; DO NOT EXECUTE during real/performance timing.
No business runtime, model, network, credential, environment or outcome access.
Only this source, the hash-bound analysis source and existing numeric source are
read. Fixtures remain in memory. A fresh explicitly named output is mandatory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
TARGET = HERE / 'secondary_heterogeneity.py'
TARGET_SHA256 = '0b3c25faba54d566c105e7faba227f0f1c25807852ea3c550068d023545844e8'
KERNEL = HERE.parents[1] / 'formal_statistics.py'
KERNEL_SHA256 = '7e201b28e8a85e712cdace33951e900ad47e6c8dcd8f4554763cccd69870e373'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_module(name, path, expected):
    if digest(path) != expected:
        raise ValueError('test target source identity changed: ' + path.name)
    # Loading definitions never invokes the guarded CLI. Avoid bytecode writes
    # into the source/snapshot directories under test.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def planned(arm='SS1'):
    return {'run_id': 'synthetic-unit', 'phase': 'main', 'task_id': 'synthetic-case',
        'chain_id': 'synthetic-chain', 'domain': 'synthetic-domain', 'arm': arm,
        'repeat': 0, 'model': 'SYNTHETIC', 'model_id': 'NO_PROVIDER',
        'topology': 'SYNTHETIC', 'processes': False}


def case():
    return {'difficulty': 'basic', 'state_spec': {'applicable': True, 'size_class': 'small'},
        'observations': {'checkpoint': 1}}


def trace(event, **fields):
    return ('synthetic-table', 1, {'event': event, **fields})


def record(task, chain, repeat, value, *, arm='T0', metric='provider_input_tokens', domain='D'):
    return {'run_id': f'{arm}/{task}/{repeat}', 'task_id': task, 'chain_id': chain,
        'domain': domain, 'repeat': repeat, 'arm': arm, 'success': True,
        'metrics': {metric: {'value': value, 'status': 'RECORDED'}}}


class SecondaryBoundaryChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.subject = source_module('_secondary_boundary_subject', TARGET, TARGET_SHA256)

    def test_shared_release_is_one_measured_close_and_conflict_rejected(self):
        # Two URI components refer to ONE10ns native close; a second group is4ns.
        events = [trace('state_buffer_release', uri='state://a', start_ns=1, end_ns=11,
                        shared_release_group='close-A', shared_group_release_ns=10),
                  trace('state_buffer_release', uri='state://b', start_ns=1, end_ns=11,
                        shared_release_group='close-A', shared_group_release_ns=10),
                  trace('state_buffer_release', uri='state://c', start_ns=20, end_ns=24,
                        shared_release_group='close-B', shared_group_release_ns=4)]
        result = self.subject.trace_value(planned(), case(), events, 'state_shared_close_ns')
        self.assertEqual((result['value'], result['event_count']), (14, 2))
        self.assertNotEqual(result['value'], 24)  # Naive per-URI sum is wrong.
        events.append(trace('state_buffer_release', uri='state://d', start_ns=1, end_ns=12,
                            shared_release_group='close-A', shared_group_release_ns=11))
        with self.assertRaisesRegex(ValueError, 'conflicting shared release'):
            self.subject.trace_value(planned(), case(), events, 'state_shared_close_ns')

    def test_existing_map_is_no_mapping_measurement_instead_of_zero_ns(self):
        events = [trace('state_buffer_materialize', materialize_ns=7, mapping_event_count=0,
                        mapping_status='NO_NEW_MAP_EXISTING_ARENA_OR_INPROC')]
        mapping = self.subject.trace_value(planned(), case(), events, 'state_os_mapping_ns')
        materialize = self.subject.trace_value(planned(), case(), events, 'state_materialize_ns')
        self.assertIsNone(mapping['value'])
        self.assertEqual(mapping['status'], 'NOT_OBSERVED')
        self.assertEqual(materialize['value'], 7)
        disabled = self.subject.trace_value(planned('T0'), case(), [], 'state_os_mapping_ns')
        self.assertIsNone(disabled['value'])
        self.assertTrue(disabled['status'].startswith('NOT_APPLICABLE'))

    def test_native_index_scope_does_not_add_enclosing_memory_work(self):
        events = [trace('memory_retrieve', inclusive_ns=100),
                  trace('index_operation', operation='search', parent_memory_operation='retrieve',
                        index_latency_ns=5),
                  trace('index_operation', operation='search', parent_memory_operation='retrieve',
                        index_latency_ns=4)]
        result = self.subject.trace_value(planned('S1'), case(), events, 'vector_index_search_ns')
        self.assertEqual((result['value'], result['event_count']), (9, 2))
        self.assertNotEqual(result['value'], 109)
        no_add = self.subject.trace_value(planned('S1'), case(), events, 'vector_index_add_ns')
        self.assertIsNone(no_add['value'])

    def test_missing_failed_usage_cache_and_total_are_never_inferred(self):
        length = {'length_quartile': 'Q1', 'canonical_fact_input_utf8_bytes': 1000}
        failed = {'success': False, 'status': 'FAILED', 'provider_input_tokens': None,
            'provider_output_tokens': None, 'provider_total_tokens': None,
            'provider_cached_input_tokens': None, 'provider_attempts': 3, 'wall_sec': 2, 'provider_wait_sec': 1}
        result = self.subject.unit_record(planned(), case(), failed, [], length)
        missing = self.subject.unit_record(planned(), case(), None, [], length)
        for name in ('provider_input_tokens', 'provider_output_tokens', 'provider_total_tokens',
                     'provider_cached_input_tokens'):
            self.assertIsNone(result['metrics'][name]['value'])
            self.assertIsNone(missing['metrics'][name]['value'])
        self.assertEqual(result['metrics']['quality_success']['value'], 0)
        self.assertFalse(missing['terminal_observed'])
        self.assertEqual(result['metrics']['provider_attempts']['value'], 3)
        self.assertIsNone(missing['metrics']['provider_attempts']['value'])
        self.assertEqual(result['metrics']['provider_wait_fraction']['value'], .5)
        # Actually supplied values survive failure; absent cache/total still do
        # not become0 or prompt+completion. An explicit measured0 is distinct.
        known = dict(failed, provider_input_tokens=17, provider_output_tokens=2)
        measured = self.subject.unit_record(planned(), case(), known, [], length)
        self.assertEqual(measured['metrics']['provider_input_tokens']['value'], 17)
        self.assertIsNone(measured['metrics']['provider_cached_input_tokens']['value'])
        self.assertIsNone(measured['metrics']['provider_total_tokens']['value'])
        zero = self.subject.unit_record(planned(), case(), dict(failed, provider_input_tokens=0), [], length)
        self.assertEqual(zero['metrics']['provider_input_tokens']['value'], 0)

    def test_signed_paired_effects_average_tasks_before_unequal_repeats(self):
        # TaskA has3 repeats with -20; taskB has1 with +20. The task estimand
        # is0, whereas treating four repetitions as four cases incorrectly gives-10.
        pairs = [(record('A', 'chain-A', repeat, 100),
                  record('A', 'chain-A', repeat, 80, arm='S0')) for repeat in range(3)]
        pairs += [(record('B', 'chain-B', 0, 100), record('B', 'chain-B', 0, 120, arm='S0'))]
        result = self.subject.paired_distribution(None, pairs, 'provider_input_tokens', 71)
        self.assertEqual(result['planned_paired_units'], 4)
        self.assertEqual(result['unique_tasks'], 2)
        self.assertEqual(result['paired_difference'], 0)
        self.assertEqual(sorted(r['difference'] for r in result['task_pair_distribution']), [-20, 20])
        # A strictly negative paired effect remains signed through the interval;
        # nonnegative input-cost validation must not discard negative differences.
        signed = [{'domain': 'D', 'chain_id': 'C1', 'task_id': 'A', 'a': 10, 'b': 9, 'difference': -1},
                  {'domain': 'D', 'chain_id': 'C2', 'task_id': 'B', 'a': 10, 'b': 7, 'difference': -3}]
        interval = self.subject.chain_resample_ci(signed, True, 72)
        self.assertLess(interval['ci95'][1], 0)
        self.assertGreaterEqual(interval['ci95'][0], -3)
        self.assertLessEqual(interval['ci95'][1], -1)

    def test_single_arm_summary_counts_tasks_rather_than_repetitions(self):
        records = [record('A', 'chain-A', repeat, 1, metric='wall_sec') for repeat in range(3)]
        records += [record('B', 'chain-B', 0, 9, metric='wall_sec')]
        result = self.subject.distribution(records, 'wall_sec', 73)
        self.assertEqual((result['planned_units'], result['unique_tasks']), (4, 2))
        self.assertEqual(result['task_mean'], 5)
        self.assertNotEqual(result['task_mean'], 3)  # Naive unit mean.
        self.assertEqual(sorted(r['repeat_count'] for r in result['task_distribution']), [1, 3])

    def test_one_chain_in_a_domain_has_no_bootstrap_interval(self):
        # Two chains overall do not make either domain's one chain resampleable.
        values = [{'domain': 'D1', 'chain_id': 'C1', 'task_id': 'A', 'value': 1},
                  {'domain': 'D2', 'chain_id': 'C2', 'task_id': 'B', 'value': 2}]
        result = self.subject.chain_resample_ci(values, False, 74)
        self.assertIsNone(result['ci95'])
        self.assertTrue(result['status'].startswith('SPARSE_NOT_ESTIMABLE'))
        self.assertEqual(result['chains_per_domain'], {'D1': 1, 'D2': 1})

    def test_relative_interval_retains_every_undefined_bootstrap_draw(self):
        # These are mathematical boundary fixtures, not observed provider data.
        # A zero-baseline chain sampled twice makes that draw's ratio undefined;
        # dropping it would condition the interval on a favorable denominator.
        fixtures = (
            ('partly_zero', (0, 1), (1, 1), 'partial'),
            ('all_zero', (0, 0), (1, 2), 'all'),
            ('nonfinite_ratio', (1e-300, 1e-300), (1e100, 1e100), 'all'),
            ('all_defined', (1, 1), (1, 2), 'none'),
        )
        for name, baselines, differences, expected in fixtures:
            with self.subTest(name=name):
                tasks = [{'domain': 'D', 'chain_id': f'C{index}', 'task_id': f'T{index}',
                          'a': a, 'b': a + difference, 'difference': difference}
                         for index, (a, difference) in enumerate(zip(baselines, differences))]
                result = self.subject.chain_resample_ci(tasks, True, 77)
                self.assertEqual(result['draws'], 10000)
                undefined = result['relative_change_undefined_draw_count']
                defined = result['relative_change_defined_draw_count']
                self.assertEqual(undefined + defined, 10000)
                self.assertIsNotNone(result['ci95'])  # Absolute difference remains defined.
                if expected == 'none':
                    self.assertEqual(undefined, 0)
                    self.assertIsNotNone(result['relative_change_ci95'])
                    self.assertEqual(result['relative_change_ci95_status'], 'COMPLETE_ALL_DRAWS_DEFINED')
                else:
                    self.assertGreater(undefined, 0)
                    self.assertIsNone(result['relative_change_ci95'])
                    self.assertEqual(result['relative_change_ci95_status'], 'UNDEFINED_DRAWS_RETAINED')
                    if expected == 'all':
                        self.assertEqual(undefined, 10000)
                    else:
                        self.assertLess(undefined, 10000)

    def test_constant_quality_withholds_bootstrap_and_keeps_existing_exact_bound(self):
        records = [record(task, chain, repeat, 1, metric='quality_success')
            for task, chain in (('A', 'C1'), ('B', 'C2')) for repeat in range(3)]
        individual = self.subject.distribution(records, 'quality_success', 75)
        self.assertIsNone(individual['ci95'])
        self.assertEqual(individual['unique_tasks'], 2)
        self.assertTrue(individual['status'].startswith('CONSTANT_QUALITY'))
        # Integration check of the NEW wrapper, not a rerun of the old suite:
        # the two-independent-chain all-pass exact bound has known lower sqrt(.05)-1.
        kernel = source_module('_secondary_existing_safety_kernel', KERNEL, KERNEL_SHA256)
        paired = [(r, {**r, 'arm': 'S0'}) for r in records]
        result = self.subject.paired_distribution(kernel, paired, 'quality_success', 76)
        self.assertIsNone(result['ci95'])
        self.assertIsNone(result['relative_change_ci95'])
        self.assertEqual(result['relative_change_ci95_status'], 'CONSTANT_QUALITY_BOOTSTRAP_IS_NOT_A_SAFETY_INTERVAL')
        self.assertEqual(result['relative_change_defined_draw_count'] + result['relative_change_undefined_draw_count'], 10000)
        self.assertAlmostEqual(result['conservative_quality_difference_one_sided_lower95'], math.sqrt(.05) - 1)
        self.assertLess(result['quality_conservative_ci95'][0], 0)
        self.assertGreater(result['quality_conservative_ci95'][1], 0)
        self.assertNotIn('p_raw', result)
        self.assertNotIn('p_holm', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True, help='new directory for small transcript/receipt only')
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists():
        raise ValueError('validation destination already exists; retain it and choose a new version')
    destination.mkdir(parents=True, exist_ok=False)
    began = datetime.now(timezone.utc).isoformat()
    output = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SecondaryBoundaryChecks)
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    transcript = destination / 'unittest_transcript.txt'
    with transcript.open('x', encoding='utf-8') as stream:
        stream.write(output.getvalue())
    receipt = {'schema_version': 1, 'started_utc': began, 'finished_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'PASS' if result.wasSuccessful() else 'FAIL', 'tests_run': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors),
        'scope': 'new secondary-boundary pure-data checks, no business runtime/model/network/secrets/outcomes; no prior suite rerun',
        'fixture_files': 0, 'formal_or_public_outcome_files_read': 0, 'paid_calls': 0,
        'source_sha256': {'validation_source': digest(Path(__file__)), 'analysis_target': digest(TARGET),
            'existing_numeric_source': digest(KERNEL)}, 'transcript_sha256': digest(transcript),
        'numeric_scope': 'small two-chain fixtures and10,000-chain-resample draws only; no signflip/permutation or full analysis'}
    with (destination / 'audit.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': receipt['status'], 'tests_run': result.testsRun, 'output': str(destination)}))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
