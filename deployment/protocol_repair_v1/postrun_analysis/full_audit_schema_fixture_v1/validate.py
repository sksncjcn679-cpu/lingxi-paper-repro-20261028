"""Prepared pure-data checks of the reviewed full audit; never a study replay.

No runtime, SDK, model, source study data, registered plan or raw ledger is used.
Functions and the no-attempt branch are extracted from the hash-bound actual
auditor AST, not copied/reimplemented. Run only in a separately permitted window.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import socket
import sys
import traceback

HERE = Path(__file__).resolve().parent
DEFAULT_HELPER = HERE.parent / 'full_metric_audit.py'
REVIEWED_HELPER_SHA256 = 'c6a09166d2eede459b907681a02950c3ff8cc683635554b257336b130ef18158'
DEFAULT_DESTINATION = Path('/evidence/protocol_repair_20261020_v1/calibration/full_audit_schema_fixture_v1')
FUNCTIONS = ('digest', 'integer', 'number', 'sha256_string', 'Audit', 'canonical', 'lifecycle_gate',
             'audit_usage', 'projection', 'audit_requests', 'memory_policy', 'index_boundaries')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def load_actual_checks(path, wanted):
    if sha(path) != wanted:
        raise RuntimeError('reviewed helper hash differs; do not silently rebind or retry')
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(path))
    definitions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    selected = [copy.deepcopy(definitions[name]) for name in FUNCTIONS]
    namespace = {'__name__': '_bound_full_audit_pure_data_checks', 'Path': Path, 'Counter': Counter,
                 'defaultdict': defaultdict, 'hashlib': hashlib, 'json': json, 'math': math, 're': re}
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                             *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    candidates = []
    for node in ast.walk(definitions['main']):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == 'calls'
                and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                        and child.func.id == 'audit_usage' for child in ast.walk(ast.Module(body=node.body, type_ignores=[])))
                and any(isinstance(child, ast.Constant) and child.value ==
                        'NO_PROVIDER_ATTEMPT_OBSERVED_USAGE_NOT_CALIBRATED' for child in ast.walk(ast.Module(body=node.orelse, type_ignores=[])))):
            candidates.append(node)
    if len(candidates) != 1:
        raise RuntimeError('the actual single usage/no-attempt branch is no longer uniquely identifiable')
    actual_branch = candidates[0]
    # The body below is the actual reviewed node, unchanged. Only a function
    # signature and return of its already-assigned complete/reported are added.
    wrapper = ast.FunctionDef(name='_actual_usage_dispatch',
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=name) for name in ('a', 'rid', 'task', 'calls')],
                           vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
        body=[copy.deepcopy(actual_branch), ast.Return(value=ast.Tuple(
            elts=[ast.Name(id='complete', ctx=ast.Load()), ast.Name(id='reported', ctx=ast.Load())], ctx=ast.Load()))],
        decorator_list=[], returns=None, type_comment=None)
    if ast.dump(wrapper.body[0], include_attributes=False) != ast.dump(actual_branch, include_attributes=False):
        raise RuntimeError('actual usage branch must remain AST-identical inside its fixture wrapper')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), str(path), 'exec'), namespace)
    provenance = {'helper_sha256': wanted,
        'actual_definition_ast_sha256': {name: hashlib.sha256(ast.dump(definitions[name], include_attributes=False).encode()).hexdigest()
                                         for name in FUNCTIONS},
        'actual_usage_branch_ast_sha256': hashlib.sha256(ast.dump(actual_branch, include_attributes=False).encode()).hexdigest(),
        'actual_usage_branch_source_sha256': hashlib.sha256(ast.get_source_segment(source, actual_branch).encode()).hexdigest(),
        'actual_usage_branch_source_lines': [actual_branch.lineno, actual_branch.end_lineno]}
    return namespace, provenance


def fail_codes(audit):
    return {item['code'] for item in audit.findings if item['level'] == 'FAIL'}


def unknown_codes(audit):
    return {item['code'] for item in audit.findings if item['level'] == 'UNKNOWN'}


def run_scenarios(checks, destination, record):
    def auditor():
        return checks['Audit'](destination / 'no_source_study_here')

    def no_fail(audit):
        assert not fail_codes(audit), fail_codes(audit)

    def lifecycle_case(mode):
        root = destination / 'lifecycle_metadata_fixtures' / mode
        stages = [{'name': name, 'status': 'TERMINAL', 'exit_code': 0,
                   'finished_utc': '2000-01-01T00:00:01+00:00'}
                  for name in ('main', 'native_remaining', 'external')]
        value = {'fixture_only': True, 'status': 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT',
                 'finished_utc': '2000-01-01T00:00:02+00:00', 'stages': stages}
        if mode == 'live_child':
            value['supervised_child_observed_running'] = True
        elif mode == 'missing_third_stage':
            value['stages'] = stages[:2]
        elif mode == 'third_stage_running':
            stages[-1].update(status='RUNNING', exit_code=None, finished_utc=None)
        write_new(root / 'formal_execution_v1/execution.json', value)
        write_new(root / 'public_pilot_v1/execution.json',
                  {'fixture_only': True, 'status': 'TERMINAL', 'finished_utc': '2000-01-01T00:00:00+00:00'})
        if mode == 'closed_three_stages':
            assert checks['lifecycle_gate'](root) == value
        else:
            try:
                checks['lifecycle_gate'](root)
            except RuntimeError:
                return {'expected_rejection': True}
            raise AssertionError('nonterminal or child-live lifecycle was admitted')
        return {'expected_rejection': False}

    for mode in ('closed_three_stages', 'live_child', 'missing_third_stage', 'third_stage_running'):
        record('lifecycle/' + mode, lambda mode=mode: lifecycle_case(mode))

    token_fields = ('provider_input_tokens', 'provider_output_tokens', 'provider_total_tokens',
                    'provider_cached_input_tokens', 'reported_token_lower_bound')

    def no_attempt_case(mode):
        task = {'provider_usage_complete': True, 'success': False,
                **{name: None if mode == 'legacy_null' else 0 for name in token_fields}}
        if mode == 'nonzero_rejected':
            task['provider_total_tokens'] = 1
        before = copy.deepcopy(task)
        a = auditor()
        real_kernel = checks['audit_usage']

        def forbidden_kernel(*args, **kwargs):
            raise AssertionError('no-attempt path incorrectly called the public provider/cache kernel')

        checks['audit_usage'] = forbidden_kernel
        try:
            complete, reported = checks['_actual_usage_dispatch'](a, 'fixture-no-attempt', task, [])
        finally:
            checks['audit_usage'] = real_kernel
        assert complete is False and reported == 0 and task == before
        assert 'NO_PROVIDER_ATTEMPT_OBSERVED_USAGE_NOT_CALIBRATED' in unknown_codes(a)
        if mode == 'nonzero_rejected':
            assert 'NO_REQUEST_HAS_NONZERO_OR_INVALID_USAGE' in fail_codes(a)
        else:
            no_fail(a)
        return {'derived_usage_complete': complete, 'source_unchanged': task == before,
                'fail_codes': sorted(fail_codes(a)), 'unknown_codes': sorted(unknown_codes(a))}

    for mode in ('legacy_cache_zero', 'legacy_null', 'nonzero_rejected'):
        record('no_attempt/' + mode, lambda mode=mode: no_attempt_case(mode))

    def provider_usage_case(mode):
        known = mode in ('reported_cache_zero', 'missing_cached_null', 'missing_cached_backfill_zero')
        usage = {'prompt_tokens': 42, 'completion_tokens': 18, 'total_tokens': 60} if known else None
        if mode == 'reported_cache_zero':
            usage['prompt_tokens_details'] = {'cached_tokens': 0}
        task = {'provider_usage_complete': known, 'provider_input_tokens': 42 if known else None,
                'provider_output_tokens': 18 if known else None, 'provider_total_tokens': 60 if known else None,
                'provider_cached_input_tokens': 0 if mode in ('reported_cache_zero', 'missing_cached_backfill_zero') else None,
                'reported_token_lower_bound': 60 if known else 0}
        if mode == 'missing_usage_backfilled_zero':
            for name in ('provider_input_tokens', 'provider_output_tokens', 'provider_total_tokens'):
                task[name] = 0
        call = {'status': 'ERROR' if mode in ('failed_missing_usage', 'missing_usage_backfilled_zero') else 'OK',
                'usage_raw': usage, 'usage_source': 'provider_reported' if known else 'missing'}
        before = copy.deepcopy(task)
        a = auditor()
        complete, reported = checks['audit_usage'](a, 'fixture-provider-metadata', task, [call])
        assert complete is known and reported == (60 if known else 0) and task == before
        if mode == 'missing_usage_backfilled_zero':
            assert 'INCOMPLETE_USAGE_REPLACED_WITH_NUMBER' in fail_codes(a)
        elif mode == 'missing_cached_backfill_zero':
            assert 'MISSING_CACHED_USAGE_REPLACED_WITH_NUMBER' in fail_codes(a)
        else:
            no_fail(a)
        if not known:
            assert 'REAL_PROVIDER_USAGE_INCOMPLETE' in unknown_codes(a)
        return {'derived_usage_complete': complete, 'source_unchanged': task == before,
                'fail_codes': sorted(fail_codes(a)), 'unknown_codes': sorted(unknown_codes(a))}

    for mode in ('failed_missing_usage', 'success_missing_usage', 'missing_usage_backfilled_zero',
                 'reported_cache_zero', 'missing_cached_null', 'missing_cached_backfill_zero'):
        record('provider_usage/' + mode, lambda mode=mode: provider_usage_case(mode))

    def external_case(mode):
        rid = 'schema-fixture-external'
        unit = {'phase': 'external_autogen', 'model': 'main', 'model_id': 'qwen3.7-plus'}
        observations = {'measurement': 1}
        case = {'observations': observations}
        # A deliberately tiny, literal fixture byte string is the independent
        # request-boundary expectation. It is never sent to any SDK/provider.
        body_bytes = b'{"model":"qwen3.7-plus","messages":[{"role":"user","content":"schema fixture"}],"temperature":0,"max_tokens":1500,"enable_thinking":false}'
        facts = {'source_facts_equal': True, 'observation_copies': 1, 'fact_inventory': ['measurement'],
                 'source_facts_sha256': hashlib.sha256(b'{"measurement":1}').hexdigest(),
                 'expected_fields_present': False, 'representation': 'fixed_external_json_role_handoff_v1'}
        if mode == 'native_fact_schema_rejected':
            facts['expected_fact_inventory'] = facts.pop('fact_inventory')
        start = {'run_id': rid, 'request_id': rid + '-1', 'attempt': 1, 'requested_model': 'qwen3.7-plus',
                 'endpoint': 'https://www.dmxapi.cn/v1/chat/completions', 'temperature': 0, 'max_tokens': 1500,
                 'timeout_sec': 60, 'extra_body': {'enable_thinking': False},
                 'actual_prompt_sha256': hashlib.sha256(b'explicit pure-data prompt hash fixture').hexdigest(),
                 'business_facts_equal': True, 'fact_audit': [facts]}
        end = {**start, 'status': 'OK', 'end_utc': '2000-01-01T00:00:01+00:00',
               'actual_http_body_sha256': hashlib.sha256(body_bytes).hexdigest(),
               'actual_request_utf8_bytes': len(body_bytes), 'actual_http_dispatches': 1, 'sdk_max_retries': 0}
        boundary = checks['projection']('model_io.jsonl', {'phase': unit['phase'], 'direction': 'http_request_body',
                    'request_id': rid + '-1', 'request': json.loads(body_bytes),
                    'actual_utf8_bytes': len(body_bytes), 'headers': 'NOT_RETAINED'})
        assert 'request' not in boundary and boundary['reconstructed_body_sha256'] == hashlib.sha256(body_bytes).hexdigest()
        if mode == 'body_hash_tamper_rejected':
            end['actual_http_body_sha256'] = '0' * 64
        a = auditor()
        checks['audit_requests'](a, rid, unit, case, [start], [end], [], [boundary],
                                 {'max_actual_utf8_request_bytes': 131072})
        if mode == 'valid_external_schema':
            no_fail(a)
            assert not unknown_codes(a)
        elif mode == 'body_hash_tamper_rejected':
            assert 'ACTUAL_REQUEST_BODY_HASH_BYTES_JOIN_FAILED' in fail_codes(a)
        else:
            assert 'EXTERNAL_FIXED_FACT_HANDOFF_AUDIT_FAILED' in fail_codes(a)
        return {'fail_codes': sorted(fail_codes(a)), 'unknown_codes': sorted(unknown_codes(a)),
                'http_requests_dispatched': 0}

    for mode in ('valid_external_schema', 'body_hash_tamper_rejected', 'native_fact_schema_rejected'):
        record('external/' + mode, lambda mode=mode: external_case(mode))

    def memory_case(mode):
        current = {'task_id': 'fixture-current', 'chain_id': 'fixture-chain', 'observations': {'checkpoint': 3}}
        before = {'task_id': 'fixture-before', 'chain_id': 'fixture-chain', 'observations': {'checkpoint': 1}}
        later = {'task_id': 'fixture-later', 'chain_id': 'fixture-chain', 'observations': {'checkpoint': 5}}
        other = {'task_id': 'fixture-other', 'chain_id': 'other-chain', 'observations': {'checkpoint': 1}}
        cases = {value['task_id']: value for value in (current, before, later, other)}
        unit = {'intervention': None}
        source, chain, related = before['task_id'], before['chain_id'], True
        if mode == 'ordinary_future_rejected':
            source = later['task_id']
        elif mode == 'ordinary_other_chain_rejected':
            source, chain = other['task_id'], other['chain_id']
        elif mode.startswith('seed_'):
            condition = mode.split('/')[0].removeprefix('seed_')
            unit['intervention'] = 'memory_' + condition
            source, related = 'prior-' + current['task_id'], condition == 'related'
            if mode.endswith('/unknown_label_rejected'):
                related = None
            elif mode.endswith('/wrong_true_rejected'):
                related = True
        event = {'event': 'retrieve', 'candidates': [{'source_task_id': source, 'source_chain_id': chain,
                  'related_under_automatic_causal_rule': related, 'label_policy_version': 'causal_same_chain_any_prior_v1'}]}
        a = auditor()
        checks['memory_policy'](a, 'fixture-memory', unit, current, [event], cases)
        expected_rejection = mode.endswith('rejected')
        assert bool(fail_codes(a)) is expected_rejection
        return {'expected_rejection': expected_rejection, 'recorded_related_flag': related,
                'fail_codes': sorted(fail_codes(a))}

    memory_modes = ('ordinary_prior', 'ordinary_future_rejected', 'ordinary_other_chain_rejected',
                    'seed_related', 'seed_unrelated', 'seed_stale', 'seed_wrong',
                    'seed_unrelated/unknown_label_rejected', 'seed_wrong/wrong_true_rejected')
    for mode in memory_modes:
        record('memory/' + mode, lambda mode=mode: memory_case(mode))

    def index_case(mode):
        unit = {'arm': 'SS1', 'intervention': 'memory_related' if mode.startswith('seed') else None}
        task = {'memory_count_before': 0, 'memory_count_after_condition_seed': 1,
                'wall_start_monotonic_ns': 100, 'wall_end_monotonic_ns': 1000}
        event = {'event': 'index_operation', 'operation': 'search', 'parent_memory_operation': 'write',
                 'start_ns': 110, 'end_ns': 120, 'index_latency_ns': 10, 'cpu_ns': 2,
                 'boundary': 'native vector index method only; embedding, SQLite, candidate merging and reranking excluded'}
        spans = [{'category': 'memory', 'start_ns': 100, 'end_ns': 150}] if mode == 'ordinary_with_outer_span' else []
        if mode.startswith('seed'):
            spans = [{'category': 'retrieval', 'start_ns': 200, 'end_ns': 300}]
        if mode == 'seed_wrong_initial_count_rejected':
            task['memory_count_before'] = 1
        a = auditor()
        count, seed = checks['index_boundaries'](a, 'fixture-index', task, unit, [event], spans)
        assert count == 1
        if mode == 'seed_without_outer_span_unknown':
            assert seed == 1 and not fail_codes(a)
            assert unknown_codes(a) == {'CONDITION_SEED_LOGICAL_WRITE_SPAN_NOT_OBSERVED'}
            assert a.findings[0]['logical_seed_write_latency'] == 'NOT_OBSERVED'
            assert 'including condition seeding' in a.findings[0]['blocks_claim']
        elif mode == 'ordinary_with_outer_span':
            assert seed == 0 and not a.findings
        else:
            assert seed == 0 and 'INDEX_ONLY_WITHOUT_ENCLOSING_MEMORY_SPAN' in fail_codes(a)
        return {'index_events': count, 'precise_seed_events': seed, 'fail_codes': sorted(fail_codes(a)),
                'unknown_codes': sorted(unknown_codes(a))}

    for mode in ('ordinary_with_outer_span', 'ordinary_without_outer_span_rejected',
                 'seed_without_outer_span_unknown', 'seed_wrong_initial_count_rejected'):
        record('index/' + mode, lambda mode=mode: index_case(mode))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper', type=Path, default=DEFAULT_HELPER)
    parser.add_argument('--helper-sha256', default=REVIEWED_HELPER_SHA256)
    parser.add_argument('--destination', type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.parent != DEFAULT_DESTINATION.parent.resolve() or destination.exists():
        parser.error('use a new direct calibration child; never overwrite or automatically retry a failed version')
    destination.mkdir(parents=True)
    blocked = Counter()

    def deny_access(event, values):
        kind = None
        if event == 'socket.__new__' and values[1] in (socket.AF_INET, socket.AF_INET6):
            kind = 'internet_socket'
        elif event in ('socket.connect', 'socket.getaddrinfo'):
            kind = event
        elif event == 'open' and isinstance(values[0], (str, bytes)):
            name = values[0].decode() if isinstance(values[0], bytes) else values[0]
            if Path(name).name in ('.env', 'model.env') or '/run/secrets/' in Path(name).as_posix():
                kind = 'credential_file'
        if kind:
            blocked[kind] += 1
            raise RuntimeError('pure-data fixture prohibited ' + kind)

    sys.addaudithook(deny_access)
    scenarios = []
    report = {'status': 'FAIL_FIXTURE_VALIDATION_RETAINED', 'scope': 'pure-data schema fixtures, not study audit',
              'model_evidence': 'pure_data_schema_fixture_not_study_outcome', 'paid_api_calls': 0,
              'sdk_requests': 0, 'study_outcomes_read': 0, 'new_research_acceptance_criteria_added': False,
              'requested_helper_sha256': args.helper_sha256, 'helper_file': str(args.helper.resolve())}
    failure = None
    with (destination / 'transcript.jsonl').open('x', encoding='utf-8') as transcript:
        def record(name, operation):
            entry = {'scenario': name, 'fixture_only': True}
            try:
                entry.update(operation() or {})
                entry['status'] = 'PASS_EXPECTED_SCHEMA_BEHAVIOR'
            except BaseException as exc:
                entry.update(status='FAIL_RETAINED', error_type=type(exc).__name__,
                             error=str(exc)[:500], traceback=[{'function': frame.name, 'line': frame.lineno}
                                                           for frame in traceback.extract_tb(exc.__traceback__)])
            scenarios.append(entry)
            transcript.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + '\n')
            transcript.flush()

        try:
            report['observed_helper_sha256'] = sha(args.helper)
            checks, provenance = load_actual_checks(args.helper, args.helper_sha256)
            report.update(provenance)
            run_scenarios(checks, destination, record)
            assert len(scenarios) == 29, 'the finite listed fixture matrix changed'
            assert not blocked
            assert sha(args.helper) == args.helper_sha256
            if all(row['status'] == 'PASS_EXPECTED_SCHEMA_BEHAVIOR' for row in scenarios):
                report['status'] = 'PASS_PURE_DATA_SCHEMA_FIXTURES_NOT_A_STUDY_AUDIT'
            else:
                failure = AssertionError('one or more fixture scenarios failed; retain this version, no automatic retry')
        except BaseException as exc:
            failure = exc
            report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    report.update(scenarios_executed=len(scenarios), scenario_counts=dict(Counter(row['status'] for row in scenarios)),
                  blocked_access_attempts=dict(blocked), validation_source_sha256=sha(Path(__file__)),
                  transcript_sha256=sha(destination / 'transcript.jsonl'), automatic_retries=0,
                  actual_full_matrix_audit_performed=False)
    write_new(destination / 'audit.json', report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if failure is not None:
        raise failure


if __name__ == '__main__':
    main()
