"""Pure in-memory state-lifecycle examples; no experiment/raw/model/runtime read.

Only proposal/preimage/current audit SOURCE is read. Output stays in a fresh
subdirectory of this proposal directory. No audit of experiment files is offered.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
CURRENT = HERE.parent / 'full_metric_audit.py'
PROPOSAL = HERE / 'proposed_state_audit.py'
PREIMAGE = HERE / 'full_metric_audit.py.preimage'
EXPECTED = '0d9a564522c8e61bf9ed19c143ad7a452415ae721d349fc62aaef79edfbc34cf'
H1, H2 = 'a' * 64, 'a' * 32 + 'b' * 32
URI = 'state://pure-fixture/' + H1[:32]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_subject():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('_proposed_state_audit_fixture', PROPOSAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reference(content_hash=H1, dedup=False):
    return {'uri': URI, 'kind': 'plan', 'content_hash': content_hash,
        'deduplicated': dedup, 'materialized_bytes': 0 if dedup else 16,
        'nbytes': 16, 'logical_nbytes': 32, 'precision': 'float32', 'quant_scale': None}


def event(name, start, end, **extra):
    return {'event': name, 'start_ns': start, 'end_ns': end, 'uri': URI, **extra}


def materialize(component, start, end):
    return event('state_buffer_materialize', start, end, buffer_key=URI + component,
        local_copied_bytes=8, error_type=None, mapping_event_count=0,
        mapping_status='NO_NEW_MAP_EXISTING_ARENA_OR_INPROC')


def release(component, start, end, **extra):
    return event('state_buffer_release', start, end, buffer_key=URI + component,
        buffer_key_absent=True, release_ns=end-start, **extra)


def consume(start=30):
    return event('state_consumer_compute', start, start+3, read_and_compute_fused=True,
        read_similarity_compute_ns=1, stable_sort_ns=1, reorder_ns=1)


def normal():
    return [materialize('#emb', 11, 13), materialize('#vec', 14, 16),
        event('state_produce', 10, 20, reference=reference(), error_type=None),
        event('state_resolve', 26, 28, reference=reference(), error_type=None),
        event('state_rpc_handoff', 28, 30, reply_payload_bytes=8, reply_framed_socket_bytes=12, consumer_pid=501),
        consume(), release('#emb', 40, 42), release('#vec', 43, 45)]


def dedup(rows=None, content_hash=H1):
    return (deepcopy(rows) if rows is not None else normal()) + [
        event('state_produce', 35, 38, reference=reference(content_hash, True), error_type=None)]


def rematerialized(content_hash=H1):
    return normal() + [materialize('#emb', 51, 53), materialize('#vec', 54, 56),
        event('state_produce', 50, 60, reference=reference(content_hash), error_type=None),
        event('state_resolve', 66, 68, reference=reference(content_hash), error_type=None),
        consume(70), release('#emb', 80, 82), release('#vec', 83, 85)]


def task(success=True):
    return {'success': success, 'state_exchange_enabled': True,
        'worker_pids': {'retriever': 501}, 'observable_ipc_bytes': 100}


class StateLifecycleFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_subject()
        cls.results = []

    def check_case(self, name, rows, expected_fail=None, success=True, legacy_failure=None):
        audit = self.module.LEGACY.Audit(None)
        summary = self.module.audit_state_lifecycles(audit, name, task(success), deepcopy(rows), [])
        fails = [r['code'] for r in audit.findings if r['level'] == 'FAIL']
        if expected_fail:
            self.assertIn(expected_fail, fails, (name, audit.findings))
        else:
            self.assertEqual(fails, [], (name, audit.findings))
        if legacy_failure:
            old = self.module.LEGACY.Audit(None)
            self.module.audit_state_legacy(old, name, task(success), deepcopy(rows), [])
            self.assertIn(legacy_failure, [r['code'] for r in old.findings if r['level'] == 'FAIL'])
        self.results.append({'fixture': name, 'expected_fail_code': expected_fail,
            'findings': audit.findings, 'summary': summary,
            'legacy_false_pairing_demonstrated': legacy_failure})
        return summary, audit

    def test_existing_normal_path_and_late_dedup_alias(self):
        summary, _ = self.check_case('ordinary_normal', normal())
        self.assertEqual((summary['produced_plan_uris'], summary['consumed_plan_uris'],
                          summary['native_mapping_events'], summary['state_rpc_handoffs']), (1, 1, 0, 1))
        summary, _ = self.check_case('valid_later_dedup', dedup(), legacy_failure='CONSUME_PRECEDES_PRODUCE')
        self.assertEqual(len(summary['materialized_generations']), 1)
        self.assertEqual(summary['dedup_aliases'][0]['generation'], 1)
        self.assertEqual(summary['consumption_generation_matches'][0]['generation'], 1)

    def test_first_consume_before_producer_and_after_release_rejected(self):
        early = [r for r in normal() if r['event'] != 'state_consumer_compute'] + [consume(1)]
        self.check_case('before_first_real_producer', early, 'CONSUME_PRECEDES_PRODUCE')
        late = [r for r in normal() if r['event'] != 'state_consumer_compute'] + [consume(46)]
        self.check_case('after_all_components_released', late, 'PLAN_RELEASE_OR_REPLACEMENT_PRECEDES_CONSUMPTION_END')

    def test_dedup_cannot_create_origin_or_change_hash(self):
        orphan = [r for r in dedup() if r['event'] != 'state_buffer_materialize' and
                  not (r['event'] == 'state_produce' and not r['reference']['deduplicated'])]
        self.check_case('dedup_without_real_origin', orphan, 'DEDUP_WITHOUT_LIVE_MATERIALIZED_GENERATION')
        self.check_case('dedup_wrong_full_hash', dedup(content_hash=H2), 'DEDUP_REFERENCE_HASH_MISMATCH')
        dead = normal() + [event('state_produce', 46, 48, reference=reference(H1, True), error_type=None)]
        self.check_case('dedup_after_release', dead, 'DEDUP_WITHOUT_LIVE_MATERIALIZED_GENERATION')

    def test_real_rematerialization_creates_a_new_generation(self):
        summary, _ = self.check_case('legal_same_hash_rematerialization', rematerialized(),
                                    legacy_failure='CONSUME_PRECEDES_PRODUCE')
        self.assertEqual([r['generation'] for r in summary['consumption_generation_matches']], [1, 2])
        gap = rematerialized() + [consume(46)]
        self.check_case('consume_in_released_rematerialization_gap', gap,
                        'PLAN_RELEASE_OR_REPLACEMENT_PRECEDES_CONSUMPTION_END')
        no_release = [r for r in rematerialized() if not (r['event'] == 'state_buffer_release' and r['start_ns'] < 50)]
        self.check_case('overwrite_without_releasing_old_components', no_release,
                        'REMATERIALIZATION_WITHOUT_PRIOR_COMPONENT_RELEASE')

    def test_hash_change_must_bind_to_correct_generation(self):
        summary, _ = self.check_case('new_hash_real_generation', rematerialized(H2))
        self.assertEqual([r['content_hash'] for r in summary['consumption_generation_matches']], [H1, H2])
        stale = rematerialized(H2)
        for row in stale:
            if row['event'] == 'state_resolve' and row['start_ns'] == 66:
                row['reference'] = reference(H1)
        self.check_case('stale_resolve_hash_after_rematerialization', stale, 'CONSUMER_REFERENCE_HASH_MISMATCH')

    def test_component_liveness_not_any_same_uri_release(self):
        partial = [r for r in normal() if not (r['event'] == 'state_consumer_compute' or
                   (r['event'] == 'state_buffer_release' and r['buffer_key'].endswith('#vec')))]
        partial += [consume(43), release('#vec', 50, 52)]
        self.check_case('emb_released_but_vec_still_live', partial,
                        'PLAN_RELEASE_OR_REPLACEMENT_PRECEDES_CONSUMPTION_END')
        # Native delete also emits no-op optional-scale deletion. It must not
        # close unrelated real #emb/#vec components when scale never existed.
        self.check_case('optional_absent_scale_noop', normal() + [release('#scale', 25, 26)])

    def test_shared_group_and_individual_component_cleanup_rules(self):
        grouped = [r for r in normal() if r['event'] != 'state_buffer_release']
        grouped += [release(component, 40, 45, shared_release_group='one-close',
                            shared_group_release_ns=5, individual_uri_release_ns=None)
                    for component in ('#emb', '#vec')]
        summary, _ = self.check_case('one_shared_close_two_components', grouped)
        self.assertEqual(set(summary['materialized_generations'][0]['components']), {URI+'#emb', URI+'#vec'})
        bad = deepcopy(grouped)
        bad[-1]['individual_uri_release_ns'] = 5
        self.check_case('shared_close_not_a_per_uri_duration', bad, 'SHARED_RELEASE_TIMING_DUPLICATED_PER_URI')
        missing = [r for r in normal() if not (r['event'] == 'state_buffer_release' and r['buffer_key'].endswith('#vec'))]
        self.check_case('one_component_never_released', missing, 'MATERIALIZED_COMPONENT_NOT_RELEASED')

    def test_non_temporal_mapping_fused_and_rpc_rules_remain_active(self):
        mapping = normal()
        mapping[0]['mapping_event_count'] = 1
        self.check_case('mapping_count_still_checked', mapping, 'MAPPING_EVENT_COUNT_MISMATCH')
        fused = normal()
        next(r for r in fused if r['event'] == 'state_consumer_compute')['read_and_compute_fused'] = False
        self.check_case('fused_flag_still_checked', fused, 'NATIVE_FUSED_READ_COMPUTE_SEMANTICS_LOST')
        partition = normal()
        next(r for r in partition if r['event'] == 'state_consumer_compute')['reorder_ns'] = 2
        self.check_case('partition_still_checked', partition, 'CONSUMER_COMPUTE_PARTITION_MISMATCH')
        rpc = normal()
        next(r for r in rpc if r['event'] == 'state_rpc_handoff')['reply_framed_socket_bytes'] = 11
        self.check_case('RPC_framing_still_checked', rpc, 'STATE_RPC_FRAMING_MISMATCH')

    def test_failed_producer_is_not_a_real_generation_and_failure_unknown_preserved(self):
        failed = dedup()
        next(r for r in failed if r['event'] == 'state_produce' and not r['reference']['deduplicated'])['error_type'] = 'SyntheticFailure'
        self.check_case('failed_origin_not_repaired_by_dedup', failed, 'DEDUP_WITHOUT_LIVE_MATERIALIZED_GENERATION', success=False)
        absent_consume = [r for r in normal() if r['event'] != 'state_consumer_compute']
        _, audit = self.check_case('failed_task_missing_consumption_remains_unknown', absent_consume, success=False)
        self.assertIn('FAILED_UNIT_PLAN_CONSUMPTION_NOT_OBSERVED', [r['code'] for r in audit.findings if r['level'] == 'UNKNOWN'])


def named_ast(path):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(path.read_text(encoding='utf-8')).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='fixture_validation_v1')
    args = parser.parse_args()
    if not args.version or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.version):
        raise ValueError('simple new local output directory name required')
    if digest(PREIMAGE) != EXPECTED or digest(CURRENT) != EXPECTED:
        raise ValueError('parent audit changed; this proposal cannot silently rebase')
    before_ast, current_ast = named_ast(PREIMAGE), named_ast(CURRENT)
    module = load_subject()
    pure = module.LEGACY.PURE_REUSED
    ast_equal = {name: before_ast[name] == current_ast[name] for name in pure}
    if len(ast_equal) != 9 or not all(ast_equal.values()):
        raise ValueError('legacy nine pure definitions changed')
    destination = HERE / args.version
    destination.mkdir(exist_ok=False)
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(StateLifecycleFixtures))
    (destination / 'unittest_transcript.txt').write_text(log.getvalue(), encoding='utf-8')
    (destination / 'fixture_cases.json').write_text(json.dumps(StateLifecycleFixtures.results,
        ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    # Review-only integration patch. It is deliberately not applied by this tool.
    old = CURRENT.read_text(encoding='utf-8')
    new = old.replace("SCHEMA_VERSION = ",
        "from state_lifecycle_audit_correction_v1.proposed_state_audit import audit_state_lifecycles\n\nSCHEMA_VERSION = ", 1)
    old_call = "state = audit_state(a, rid, task, data['states.jsonl'], data['spans.jsonl'])"
    new_call = "state = audit_state_lifecycles(a, rid, task, data['states.jsonl'], data['spans.jsonl'])"
    if new.count(old_call) != 1:
        raise ValueError('explicit review-only state call site not found exactly once')
    new = new.replace(old_call, new_call, 1)
    patch = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
        fromfile='full_metric_audit.py (unchanged current)', tofile='full_metric_audit.py (review-only proposed integration)'))
    (destination / 'integration_proposal.diff').write_text(patch, encoding='utf-8')
    unchanged = digest(CURRENT) == EXPECTED and digest(PREIMAGE) == EXPECTED
    receipt = {'created_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'PASS' if result.wasSuccessful() and unchanged else 'FAIL',
        'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'fixture_cases': len(StateLifecycleFixtures.results), 'legacy_nine_AST_identical': ast_equal,
        'main_auditor_unchanged': unchanged, 'parent_preimage_sha256': EXPECTED,
        'proposal_sha256': digest(PROPOSAL), 'validation_source_sha256': digest(Path(__file__)),
        'active_main_state_entry': 'legacy audit_state remains active; integration NOT applied',
        'proposal_state_entry': 'audit_state_lifecycles is new generation-aware code, not AST-identical legacy reuse',
        'data_scope': 'pure in-memory fixture dictionaries only; no real raw files, outcomes, model, runtime, Docker, network or credentials',
        'existing_full_audit_v2_FAIL_retained': True,
        'files': {p.name: digest(p) for p in sorted(destination.iterdir()) if p.is_file()}}
    with (destination/'receipt.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'status': receipt['status'], 'tests_run': result.testsRun,
        'fixture_cases': receipt['fixture_cases'], 'output': str(destination)}))
    raise SystemExit(0 if receipt['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
