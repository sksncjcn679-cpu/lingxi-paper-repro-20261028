"""Prepared post-run audit of the corrected full 6504-unit recorded matrix.

Standard library only. Never imports a runtime/driver, reads credentials, calls a
provider or resumes a task. Public and formal cohorts stay separately identified.
The lifecycle receipt must already be terminal before any raw file is read.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import sqlite3
import types
import uuid

STUDY = 'protocol_repair_20261020_v1'
PHASE = 'public_calibration'
ARMS = {'T0', 'S0', 'SS0', 'T1', 'S1', 'SS1'}
SCHEMA_VERSION = 'full-metric-audit-v3-generation-aware-state-lifecycle'
RAW_NAMES = (
    'task_unit_starts.jsonl', 'task_runs.jsonl',
    'provider_attempt_starts.jsonl', 'provider_calls.jsonl',
    'http_request_preflight.jsonl', 'messages.jsonl', 'ipc_socket_events.jsonl',
    'ipc_accounting.jsonl', 'states.jsonl', 'memory_events.jsonl', 'spans.jsonl',
    'resource_samples.jsonl', 'observer_identity.jsonl', 'observer_summary.jsonl',
    'execution_events.jsonl',
)
FULL_RAW_NAMES = RAW_NAMES + ('provider_retry_waits.jsonl', 'external_execution_events.jsonl',
                              'external_framework_runs.jsonl', 'model_io.jsonl')
PUBLIC_AUDITOR_SHA256 = '8f8100697b768473c65d408e49b0bec1961441aa016b620a309674a92704c0dc'
FORMAL_FREEZE_SHA256 = '2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e'
PURE_REUSED = ('digest', 'integer', 'number', 'sha256_string', 'Audit', 'groups',
               'audit_usage', 'audit_spans', 'audit_state')
MODEL_IDS = {'main': 'qwen3.7-plus', 'cross': 'doubao-seed-2-0-mini-260215'}
ABLATIONS = ('reference_first_off', 'session_dictionary_off', 'state_dedup_off',
             'quantization_off', 'delta_off', 'bm25_off', 'feedback_isolation_off', 'adoption_gate_off')
PHASE_COUNTS = {'public_calibration': 144, 'main': 2160, 'cross_model': 720, 'multiprocess': 360,
                'external_autogen': 120, 'external_langgraph': 120, 'ablation_baseline': 180,
                **{'ablation_' + key: 180 for key in ABLATIONS},
                **{'serialization_' + key: 180 for key in ('pure_json', 'pure_msgpack')},
                **{'memory_condition_' + key: 180 for key in ('none', 'related', 'unrelated', 'stale', 'wrong')}}
IDENTITY = ('study_id', 'phase', 'task_id', 'chain_id', 'domain', 'difficulty', 'arm',
            'repeat', 'model', 'intervention', 'unit_id', 'seed', 'topology')
OBSERVATION_FIELDS = IDENTITY + (
    'run_id', 'model_id', 'processes', 'actual_topology', 'source_sha256', 'task_sha256',
    'effective_contract_sha256', 'model_evidence', 'start_utc', 'end_utc', 'status', 'success', 'quality',
    'wall_sec', 'wall_start_monotonic_ns', 'wall_end_monotonic_ns', 'provider_wait_sec',
    'exclusive_spans_sec', 'span_partition_error_sec', 'metrics', 'runtime_cpu_sec',
    'sampled_peak_process_group_rss', 'sampled_peak_process_group_fds', 'fd_count_before', 'fd_count_after',
    'observable_ipc_bytes', 'ipc_accounting', 'local_state_materialized_bytes', 'logical_state_bytes',
    'state_consumed', 'state_exchange_enabled', 'state_dimension', 'state_applicable', 'state_size_class',
    'public_state_calibration_override', 'worker_pids', 'agent_worker_cpu_final_sec', 'memory_count_before',
    'memory_count_after_condition_seed', 'memory_count_after', 'memory_lineage', 'memory_adopted_id',
    'memory_feedback_attribution', 'execution_provenance', 'provider_attempts', 'provider_usage_complete',
    'provider_input_tokens', 'provider_output_tokens', 'provider_total_tokens', 'provider_cached_input_tokens',
    'provider_cached_input_tokens_complete', 'reported_token_lower_bound', 'workers_remaining',
    'shm_names_created_but_remaining', 'comparison_scope', 'external_framework_versions', 'role_order')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sha256_string(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


class Audit:
    def __init__(self, out):
        self.out = out
        self.evidence = {}
        self.findings = []
        self.units = []

    def read(self, relative, jsonl=False, required=True):
        path = self.out / relative
        if not path.is_file():
            self.find('FAIL' if required else 'UNKNOWN', 'INPUT_MISSING', file=relative)
            return [] if jsonl else {}
        self.evidence[relative] = {'sha256': digest(path), 'bytes': path.stat().st_size}
        try:
            if jsonl:
                rows = []
                with path.open(encoding='utf-8') as f:
                    for line_no, line in enumerate(f, 1):
                        if line.strip():
                            row = json.loads(line)
                            if not isinstance(row, dict):
                                raise ValueError('row is not an object')
                            rows.append(row)
                return rows
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError) as exc:
            self.find('FAIL', 'INPUT_UNREADABLE', file=relative, error_type=type(exc).__name__)
            return [] if jsonl else {}

    def find(self, level, code, run_id=None, **detail):
        self.findings.append({'level': level, 'code': code,
                              **({'run_id': run_id} if run_id else {}), **detail})

    def require(self, condition, code, run_id=None, **detail):
        if not condition:
            self.find('FAIL', code, run_id, **detail)
        return bool(condition)

    def unknown(self, code, run_id=None, **detail):
        self.find('UNKNOWN', code, run_id, **detail)

    def unique(self, rows, field, code):
        counts = Counter(r.get(field) for r in rows)
        for key, count in counts.items():
            self.require(key is not None and count == 1, code,
                         key if field == 'run_id' else None,
                         **{field: key, 'count': count} if field != 'run_id' else {'count': count})
        return {r.get(field): r for r in rows}

    def stable(self):
        for relative, before in self.evidence.items():
            path = self.out / relative
            self.require(path.is_file() and digest(path) == before['sha256'],
                         'EVIDENCE_CHANGED_DURING_AUDIT', file=relative)


def groups(rows, key='run_id'):
    result = defaultdict(list)
    for row in rows:
        result[row.get(key)].append(row)
    return result


def audit_usage(a, run_id, task, calls):
    fields = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    complete = bool(calls)
    reported = 0
    for call in calls:
        usage = call.get('usage_raw')
        if usage is None:
            a.require(call.get('usage_source') in ('missing', 'none'),
                      'MISSING_USAGE_WRONGLY_LABELLED', run_id)
            complete = False
            continue
        a.require(call.get('usage_source') == 'provider_reported',
                  'NON_PROVIDER_USAGE_IN_REAL_COHORT', run_id)
        valid = isinstance(usage, dict) and all(integer(usage.get(k)) for k in fields)
        a.require(valid, 'PROVIDER_USAGE_FIELDS_INVALID', run_id,
                  request_id=call.get('request_id'))
        complete = complete and valid and call.get('status') == 'OK'
        if valid:
            a.require(usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens'],
                      'PROVIDER_USAGE_TOTAL_NOT_CONSERVED', run_id)
            reported += usage['total_tokens']
    # No request means zero requests were observed, but it is not real usage calibration.
    if calls:
        a.require(task.get('provider_usage_complete') is complete,
                  'TASK_USAGE_COMPLETENESS_MISMATCH', run_id)
    mapping = {'provider_input_tokens': 'prompt_tokens',
               'provider_output_tokens': 'completion_tokens',
               'provider_total_tokens': 'total_tokens'}
    for target, source in mapping.items():
        value = task.get(target)
        if complete:
            a.require(value == sum(c['usage_raw'][source] for c in calls),
                      'TASK_USAGE_SUM_MISMATCH', run_id, field=target)
        elif calls:
            a.require(value is None, 'INCOMPLETE_USAGE_REPLACED_WITH_NUMBER', run_id, field=target)
        elif value not in (None, 0):
            a.find('FAIL', 'NO_REQUEST_HAS_NONZERO_USAGE', run_id, field=target)
    if calls and not complete:
        a.unknown('REAL_PROVIDER_USAGE_INCOMPLETE', run_id,
                  consequence='retain null; no paired primary CI/p for incomplete pairs')
    if 'reported_token_lower_bound' in task:
        a.require(task['reported_token_lower_bound'] == reported,
                  'REPORTED_TOKEN_LOWER_BOUND_MISMATCH', run_id)
    cached = task.get('provider_cached_input_tokens')
    cached_values = [(c.get('usage_raw') or {}).get('prompt_tokens_details') or {} for c in calls]
    if complete and all(integer(v.get('cached_tokens')) for v in cached_values):
        a.require(cached == sum(v['cached_tokens'] for v in cached_values),
                  'TASK_CACHED_PROVIDER_USAGE_SUM_MISMATCH', run_id)
    elif cached is not None:
        a.find('FAIL', 'MISSING_CACHED_USAGE_REPLACED_WITH_NUMBER', run_id)
    return complete, reported


def audit_spans(a, run_id, task, spans):
    start, end = task.get('wall_start_monotonic_ns'), task.get('wall_end_monotonic_ns')
    if not (integer(start) and integer(end) and end >= start):
        a.unknown('WALL_BOUNDARY_NOT_OBSERVED', run_id)
        return
    wall = (end - start) / 1e9
    a.require(number(task.get('wall_sec')) and abs(task['wall_sec'] - wall) < 1e-8,
              'WALL_SECONDS_MISMATCH', run_id)
    partition = task.get('exclusive_spans_sec')
    if not isinstance(partition, dict) or not all(number(v) and v >= 0 for v in partition.values()):
        a.find('FAIL', 'EXCLUSIVE_PARTITION_INVALID', run_id)
        return
    a.require(abs(sum(partition.values()) - wall) < 1e-6,
              'EXCLUSIVE_PARTITION_NOT_CONSERVED', run_id)
    a.require(number(task.get('span_partition_error_sec')) and
              task['span_partition_error_sec'] < 1e-6,
              'REPORTED_PARTITION_ERROR', run_id)
    by_id = a.unique(spans, 'span_id', 'DUPLICATE_SPAN_ID')
    for span in spans:
        left, right = span.get('start_ns'), span.get('end_ns')
        if not a.require(integer(left) and integer(right) and start <= left <= right <= end,
                         'SPAN_OUTSIDE_WALL_OR_REVERSED', run_id):
            continue
        a.require(number(span.get('inclusive_sec')) and
                  abs(span['inclusive_sec'] - (right - left) / 1e9) < 1e-8,
                  'SPAN_DURATION_MISMATCH', run_id)
        parent = by_id.get(span.get('parent_span_id'))
        if span.get('parent_span_id') is not None:
            a.require(parent is not None and parent.get('thread') == span.get('thread') and
                      parent.get('start_ns', right + 1) <= left <= right <= parent.get('end_ns', -1),
                      'SPAN_PARENT_NESTING_INVALID', run_id)
    # Rebuild the exclusive partition from recorded spans; nested inclusive durations
    # are deliberately never added to wall time.
    threads = {s.get('thread') for s in spans}
    if len(threads) != 1:
        a.unknown('EXCLUSIVE_REBUILD_THREAD_AMBIGUOUS', run_id, threads=len(threads))
    else:
        points = sorted({start, end, *[s[k] for s in spans for k in ('start_ns', 'end_ns')
                                      if integer(s.get(k)) and start <= s[k] <= end]})
        rebuilt = Counter()
        for left, right in zip(points, points[1:]):
            covering = [s for s in spans if s.get('start_ns', end + 1) <= left and s.get('end_ns', -1) >= right]
            selected = max(covering, key=lambda s: (s.get('depth', 0), s.get('span_id', 0))) if covering else None
            rebuilt[selected['category'] if selected else 'runtime_and_recording_other'] += (right - left) / 1e9
        a.require(all(abs(rebuilt[k] - partition.get(k, 0)) < 1e-6
                      for k in set(rebuilt) | set(partition)),
                  'EXCLUSIVE_PARTITION_REBUILD_MISMATCH', run_id)
    wait = sum(s.get('inclusive_sec', 0) for s in spans if s.get('category') == 'provider_wait')
    a.require(number(task.get('provider_wait_sec')) and abs(task['provider_wait_sec'] - wait) < 1e-6,
              'PROVIDER_WAIT_SUM_MISMATCH', run_id)


def audit_state(a, run_id, task, rows, spans):
    for row in rows:
        if 'start_ns' in row and 'end_ns' in row:
            a.require(integer(row['start_ns']) and integer(row['end_ns']) and row['end_ns'] >= row['start_ns'],
                      'STATE_BOUNDARY_INVALID', run_id, event=row.get('event'))
    produced = [r for r in rows if r.get('event') == 'state_produce' and
                (r.get('reference') or {}).get('kind') == 'plan' and not r.get('error_type')]
    consumed = [r for r in rows if r.get('event') == 'state_consumer_compute']
    releases = [r for r in rows if r.get('event') == 'state_buffer_release']
    mats = [r for r in rows if r.get('event') == 'state_buffer_materialize' and not r.get('error_type')]
    maps = [r for r in rows if r.get('event') == 'state_os_mapping']
    for prod in produced:
        ref, uri = prod['reference'], prod.get('uri')
        a.require(uri == ref.get('uri') and sha256_string(ref.get('content_hash')),
                  'PLAN_REFERENCE_IDENTITY_MISSING', run_id)
        matches = [r for r in consumed if r.get('uri') == uri]
        released = [r for r in releases if r.get('uri') == uri and r.get('buffer_key_absent') is True]
        a.require(bool(released), 'PRODUCED_PLAN_NOT_RELEASED', run_id, uri=uri)
        if not matches:
            if task.get('success'):
                a.find('FAIL', 'SUCCESSFUL_STATE_PLAN_NOT_CONSUMED', run_id, uri=uri)
            else:
                a.unknown('FAILED_UNIT_PLAN_CONSUMPTION_NOT_OBSERVED', run_id, uri=uri)
        for con in matches:
            a.require(prod['end_ns'] <= con.get('start_ns', -1), 'CONSUME_PRECEDES_PRODUCE', run_id)
            a.require(con.get('read_and_compute_fused') is True,
                      'NATIVE_FUSED_READ_COMPUTE_SEMANTICS_LOST', run_id)
            parts = [con.get(k) for k in ('read_similarity_compute_ns', 'stable_sort_ns', 'reorder_ns')]
            a.require(all(integer(v) for v in parts) and sum(parts) == con['end_ns'] - con['start_ns'],
                      'CONSUMER_COMPUTE_PARTITION_MISMATCH', run_id)
            a.require(any(r.get('start_ns', -1) >= con['end_ns'] for r in released),
                      'PLAN_RELEASE_PRECEDES_CONSUMPTION', run_id)
    for mat in mats:
        key = mat.get('buffer_key')
        a.require(any(r.get('buffer_key') == key and r.get('buffer_key_absent') is True for r in releases),
                  'MATERIALIZED_COMPONENT_NOT_RELEASED', run_id, buffer_key=key)
        count = mat.get('mapping_event_count')
        matching = [m for m in maps if m.get('buffer_key') == key and
                    mat['start_ns'] <= m.get('start_ns', -1) <= m.get('end_ns', -1) <= mat['end_ns']]
        a.require(integer(count) and count == len(matching), 'MAPPING_EVENT_COUNT_MISMATCH', run_id)
        status = 'NEW_NATIVE_MAPPING_OBSERVED' if matching else 'NO_NEW_MAP_EXISTING_ARENA_OR_INPROC'
        a.require(mat.get('mapping_status') == status, 'MAPPING_REUSE_MISLABELLED', run_id)
    for release in releases:
        a.require(release.get('buffer_key_absent') is True, 'STATE_BUFFER_REMAINED_AFTER_RELEASE', run_id)
        if release.get('shared_release_group'):
            a.require(release.get('individual_uri_release_ns') is None,
                      'SHARED_RELEASE_TIMING_DUPLICATED_PER_URI', run_id)
    for con in consumed:
        a.require(any(p.get('uri') == con.get('uri') for p in produced),
                  'CONSUMER_WITHOUT_PRODUCED_PLAN', run_id)
    expected = task.get('state_exchange_enabled') is True
    if expected and not produced:
        if task.get('success'):
            a.find('FAIL', 'STATE_ENABLED_WITHOUT_OBSERVED_PLAN', run_id)
        else:
            a.unknown('STATE_ENABLED_WITHOUT_OBSERVED_PLAN', run_id)
    handoffs = [r for r in rows if r.get('event') == 'state_rpc_handoff']
    for handoff in handoffs:
        a.require(integer(handoff.get('reply_payload_bytes')) and
                  handoff.get('reply_framed_socket_bytes') == handoff['reply_payload_bytes'] + 4,
                  'STATE_RPC_FRAMING_MISMATCH', run_id)
        a.require(handoff.get('consumer_pid') in (task.get('worker_pids') or {}).values(),
                  'STATE_RPC_CONSUMER_NOT_RECORDED_WORKER', run_id)
    if handoffs and number(task.get('observable_ipc_bytes')):
        a.require(sum(r['reply_framed_socket_bytes'] for r in handoffs) <= task['observable_ipc_bytes'],
                  'STATE_RPC_BYTES_EXCEED_TASK_SOCKET_TOTAL', run_id)
    return {'produced_plan_uris': len({r.get('uri') for r in produced}),
            'consumed_plan_uris': len({r.get('uri') for r in consumed}),
            'native_mapping_events': len(maps), 'state_rpc_handoffs': len(handoffs)}


def interval(row):
    return integer(row.get('start_ns')) and integer(row.get('end_ns')) and row['start_ns'] <= row['end_ns']
def _other_state_rules(a, run_id, task, rows):
    """Preserved non-lifecycle predicates, explicitly separated from new joins."""
    for row in rows:
        if 'start_ns' in row and 'end_ns' in row:
            a.require(interval(row), 'STATE_BOUNDARY_INVALID', run_id, event=row.get('event'))
    consumed = [r for r in rows if r.get('event') == 'state_consumer_compute']
    releases = [r for r in rows if r.get('event') == 'state_buffer_release']
    mats = [r for r in rows if r.get('event') == 'state_buffer_materialize' and not r.get('error_type')]
    maps = [r for r in rows if r.get('event') == 'state_os_mapping']
    for con in consumed:
        a.require(con.get('read_and_compute_fused') is True,
                  'NATIVE_FUSED_READ_COMPUTE_SEMANTICS_LOST', run_id)
        parts = [con.get(k) for k in ('read_similarity_compute_ns', 'stable_sort_ns', 'reorder_ns')]
        a.require(interval(con) and all(integer(v) for v in parts)
                  and sum(parts) == con['end_ns'] - con['start_ns'],
                  'CONSUMER_COMPUTE_PARTITION_MISMATCH', run_id)
    for mat in mats:
        key = mat.get('buffer_key')
        a.require(any(r.get('buffer_key') == key and r.get('buffer_key_absent') is True for r in releases),
                  'MATERIALIZED_COMPONENT_NOT_RELEASED', run_id, buffer_key=key)
        matching = [m for m in maps if m.get('buffer_key') == key and interval(mat) and interval(m)
                    and mat['start_ns'] <= m['start_ns'] <= m['end_ns'] <= mat['end_ns']]
        a.require(integer(mat.get('mapping_event_count')) and mat['mapping_event_count'] == len(matching),
                  'MAPPING_EVENT_COUNT_MISMATCH', run_id)
        status = 'NEW_NATIVE_MAPPING_OBSERVED' if matching else 'NO_NEW_MAP_EXISTING_ARENA_OR_INPROC'
        a.require(mat.get('mapping_status') == status, 'MAPPING_REUSE_MISLABELLED', run_id)
    for release in releases:
        a.require(release.get('buffer_key_absent') is True, 'STATE_BUFFER_REMAINED_AFTER_RELEASE', run_id)
        if release.get('shared_release_group'):
            a.require(release.get('individual_uri_release_ns') is None,
                      'SHARED_RELEASE_TIMING_DUPLICATED_PER_URI', run_id)
    handoffs = [r for r in rows if r.get('event') == 'state_rpc_handoff']
    for handoff in handoffs:
        a.require(integer(handoff.get('reply_payload_bytes')) and
                  handoff.get('reply_framed_socket_bytes') == handoff['reply_payload_bytes'] + 4,
                  'STATE_RPC_FRAMING_MISMATCH', run_id)
        a.require(handoff.get('consumer_pid') in (task.get('worker_pids') or {}).values(),
                  'STATE_RPC_CONSUMER_NOT_RECORDED_WORKER', run_id)
    if handoffs and number(task.get('observable_ipc_bytes')):
        a.require(all(integer(r.get('reply_framed_socket_bytes')) for r in handoffs)
                  and sum(r['reply_framed_socket_bytes'] for r in handoffs) <= task['observable_ipc_bytes'],
                  'STATE_RPC_BYTES_EXCEED_TASK_SOCKET_TOTAL', run_id)
    return consumed, releases, mats, maps, handoffs
def _components(a, run_id, rows, mats, releases):
    """Track each physical buffer-key materialization, not an entire URI label."""
    result = []
    for mat in mats:
        if not interval(mat):
            continue  # Invalid boundaries are already explicit FAIL findings.
        key, uri = mat.get('buffer_key'), mat.get('uri')
        if not a.require(isinstance(key, str) and key.split('#', 1)[0] == uri,
                         'COMPONENT_URI_KEY_MISMATCH', run_id, buffer_key=key):
            continue
        future_puts = [r for r in mats if r is not mat and r.get('buffer_key') == key and interval(r)
                       and r['start_ns'] >= mat['end_ns']]
        next_put = min((r['start_ns'] for r in future_puts), default=None)
        following = [r for r in releases if r.get('buffer_key') == key and r.get('buffer_key_absent') is True
                     and interval(r) and r['start_ns'] >= mat['end_ns']]
        released = min(following, key=lambda r: (r['start_ns'], r['end_ns'])) if following else None
        a.require(released is not None, 'MATERIALIZATION_GENERATION_NOT_RELEASED', run_id,
                  buffer_key=key, materialize_end_ns=mat['end_ns'])
        if next_put is not None:
            a.require(released is not None and released['end_ns'] <= next_put,
                      'REMATERIALIZATION_WITHOUT_PRIOR_COMPONENT_RELEASE', run_id, buffer_key=key)
        overlapping = [r for r in mats if r is not mat and r.get('buffer_key') == key and interval(r)
                       and r['start_ns'] < mat['end_ns'] and r['end_ns'] > mat['start_ns']]
        a.require(not overlapping, 'OVERLAPPING_SAME_KEY_MATERIALIZATION', run_id, buffer_key=key)
        ends = ([released['start_ns']] if released else []) + ([next_put] if next_put is not None else [])
        result.append({'row': mat, 'key': key, 'uri': uri, 'start_ns': mat['start_ns'],
            'end_ns': mat['end_ns'], 'live_until_ns': min(ends) if ends else None,
            'release': released})
    return result
def _live(generation, left, right):
    return generation['ready_ns'] <= left and all(component['end_ns'] <= left and
        (component['live_until_ns'] is None or right <= component['live_until_ns'])
        for component in generation['components'].values())
def _consumer_hash(a, run_id, con, rows, generations):
    explicit = con.get('content_hash')
    if explicit is None and isinstance(con.get('reference'), dict):
        explicit = con['reference'].get('content_hash')
    if explicit is not None:
        a.require(sha256_string(explicit), 'CONSUMER_REFERENCE_HASH_INVALID', run_id)
        return explicit, 'consumer_reference'
    resolves = [r for r in rows if r.get('event') == 'state_resolve' and r.get('uri') == con.get('uri')
                and not r.get('error_type') and interval(r) and r['end_ns'] <= con['start_ns']]
    if resolves:
        resolved = max(resolves, key=lambda r: (r['end_ns'], r['start_ns']))
        reference = resolved.get('reference') or {}
        value = reference.get('content_hash')
        a.require(reference.get('uri') == con.get('uri') and sha256_string(value),
                  'CONSUMER_RESOLVE_REFERENCE_IDENTITY_MISSING', run_id)
        return value, 'latest_successful_resolve_reference'
    # The frozen compute event carries URI, not a new payload digest. A unique
    # producer hash can identify its observed generation, but is not presented
    # as an independent re-hash of the consumer's ndarray. Once a URI has more
    # than one observed full hash, URI alone cannot choose the content generation.
    hashes = {g['content_hash'] for g in generations if g['uri'] == con.get('uri')
              and g['ready_ns'] <= con['start_ns']}
    if len(hashes) == 1:
        return next(iter(hashes)), 'URI_only_unique_observed_producer_hash'
    a.require(False, 'CONSUMER_HASH_AMBIGUOUS_ACROSS_GENERATIONS', run_id, uri=con.get('uri'))
    return None, 'not_established'
def audit_state_lifecycles(a, run_id, task, rows, spans):
    """New explicit state entry, preserving other rules and replacing all-to-all joins.

    No phase names are examined. Every consume needs a completed true generation
    and every required physical component must survive its entire interval.
    A dedup put is an alias, never a new ready time or a substitute origin.
    """
    consumed, releases, mats, maps, handoffs = _other_state_rules(a, run_id, task, rows)
    produced = [r for r in rows if r.get('event') == 'state_produce' and
                isinstance(r.get('reference'), dict) and r['reference'].get('kind') == 'plan'
                and not r.get('error_type')]
    components = _components(a, run_id, rows, mats, releases)
    generations, aliases = [], []
    for prod in sorted(produced, key=lambda r: (r.get('end_ns', -1), r.get('start_ns', -1))):
        ref, uri = prod['reference'], prod.get('uri')
        identity_ok = a.require(uri == ref.get('uri') and sha256_string(ref.get('content_hash')),
                                'PLAN_REFERENCE_IDENTITY_MISSING', run_id)
        if not interval(prod) or not identity_ok:
            continue
        nested = [c for c in components if c['uri'] == uri and
                  prod['start_ns'] <= c['start_ns'] <= c['end_ns'] <= prod['end_ns']]
        dedup = ref.get('deduplicated')
        if dedup is True:
            a.require(ref.get('materialized_bytes') == 0 and not nested,
                      'DEDUP_REFERENCE_HAS_NEW_MATERIALIZATION', run_id, uri=uri)
            live = [g for g in generations if g['uri'] == uri and _live(g, prod['start_ns'], prod['end_ns'])]
            matched = [g for g in live if g['content_hash'] == ref['content_hash']]
            if not matched:
                a.find('FAIL', 'DEDUP_REFERENCE_HASH_MISMATCH' if live else
                       'DEDUP_WITHOUT_LIVE_MATERIALIZED_GENERATION', run_id, uri=uri)
            else:
                target = max(matched, key=lambda g: g['ready_ns'])
                aliases.append({'uri': uri, 'content_hash': ref['content_hash'],
                    'producer_end_ns': prod['end_ns'], 'generation': target['generation']})
            continue
        real = a.require(dedup is False and integer(ref.get('materialized_bytes')) and ref['materialized_bytes'] > 0,
                         'NONDEDUP_REFERENCE_WITHOUT_REAL_MATERIALIZATION', run_id, uri=uri)
        latest = {}
        for component in sorted(nested, key=lambda c: c['end_ns']):
            latest[component['key']] = component
        required = {uri + '#emb', uri + '#vec'}
        precision = str(ref.get('precision', ''))
        if ref.get('quant_scale') is not None or precision.startswith(('int8', 'int4', 'q4b')):
            required.add(uri + '#scale')
        backed = a.require(required <= set(latest), 'PLAN_PHYSICAL_COMPONENTS_MISSING', run_id,
                           uri=uri, missing_keys=sorted(required - set(latest)))
        if not real or not backed:
            continue
        generation = {'uri': uri, 'content_hash': ref['content_hash'], 'ready_ns': prod['end_ns'],
            'components': latest, 'generation': 1 + sum(g['uri'] == uri for g in generations)}
        a.require(_live(generation, prod['end_ns'], prod['end_ns']),
                  'PLAN_COMPONENT_NOT_LIVE_AT_PRODUCE_COMPLETION', run_id, uri=uri)
        generations.append(generation)

    matches = []
    for con in consumed:
        if not interval(con):
            continue
        uri = con.get('uri')
        a.require(any(p.get('uri') == uri for p in produced), 'CONSUMER_WITHOUT_PRODUCED_PLAN', run_id)
        content_hash, basis = _consumer_hash(a, run_id, con, rows, generations)
        live = [g for g in generations if g['uri'] == uri and _live(g, con['start_ns'], con['end_ns'])]
        matched = [g for g in live if g['content_hash'] == content_hash]
        if not matched:
            ready = [g for g in generations if g['uri'] == uri and g['ready_ns'] <= con['start_ns']]
            code = ('CONSUMER_REFERENCE_HASH_MISMATCH' if live else 'CONSUME_PRECEDES_PRODUCE' if not ready
                    else 'PLAN_RELEASE_OR_REPLACEMENT_PRECEDES_CONSUMPTION_END')
            a.find('FAIL', code, run_id, uri=uri, consume_start_ns=con['start_ns'], consume_end_ns=con['end_ns'])
            continue
        target = max(matched, key=lambda g: g['ready_ns'])
        matches.append({'uri': uri, 'content_hash': content_hash, 'generation': target['generation'],
            'consume_start_ns': con['start_ns'], 'consume_end_ns': con['end_ns'], 'hash_basis': basis})
    # Preserve the legacy URI-level success/failure consumption requirement. An
    # unused but cleaned-up real generation is not invented as a new task failure.
    for uri in sorted({p.get('uri') for p in produced if isinstance(p.get('uri'), str)}):
        a.require(any(r.get('uri') == uri and r.get('buffer_key_absent') is True for r in releases),
                  'PRODUCED_PLAN_NOT_RELEASED', run_id, uri=uri)
        if not any(match['uri'] == uri for match in matches):
            if task.get('success'):
                a.find('FAIL', 'SUCCESSFUL_STATE_PLAN_NOT_CONSUMED', run_id, uri=uri)
            else:
                a.unknown('FAILED_UNIT_PLAN_CONSUMPTION_NOT_OBSERVED', run_id, uri=uri)
    if task.get('state_exchange_enabled') is True and not produced:
        if task.get('success'):
            a.find('FAIL', 'STATE_ENABLED_WITHOUT_OBSERVED_PLAN', run_id)
        else:
            a.unknown('STATE_ENABLED_WITHOUT_OBSERVED_PLAN', run_id)
    return {'produced_plan_uris': len({r.get('uri') for r in produced}),
        'consumed_plan_uris': len({r.get('uri') for r in consumed}),
        'native_mapping_events': len(maps), 'state_rpc_handoffs': len(handoffs),
        'active_state_entry': 'audit_state_lifecycles_proposal_v1',
        'legacy_state_entry_retained_unchanged': True,
        'materialized_generations': [{'uri': g['uri'], 'content_hash': g['content_hash'],
            'generation': g['generation'], 'ready_ns': g['ready_ns'],
            'components': {key: {'materialize_end_ns': c['end_ns'], 'live_until_ns': c['live_until_ns']}
                           for key, c in g['components'].items()}} for g in generations],
        'dedup_aliases': aliases, 'consumption_generation_matches': matches,
        'hash_observation_limit': 'compute events record URI, not a new payload hash; reference hash is joined from successful resolve when present, otherwise only a unique observed producer hash may identify the generation'}



def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def runtime_source_sha(root):
    """Same file selection and byte framing as the frozen native source hasher."""
    candidates = [root / 'pyproject.toml']
    for folder in (root / 'src', root / 'scenarios', root / 'scripts'):
        if folder.is_dir():
            candidates.extend(p for p in folder.rglob('*') if p.is_file() and p.suffix in {'.py', '.sh', '.json', '.npz'})
    value = hashlib.sha256()
    for path in sorted(candidates, key=lambda p: p.relative_to(root).as_posix()):
        value.update(path.relative_to(root).as_posix().encode())
        value.update(b'\0')
        value.update(path.read_bytes())
        value.update(b'\0')
    return value.hexdigest()


def lifecycle_gate(out):
    # Only small mutable lifecycle receipts are read before this gate. No raw,
    # corpus, plan, model I/O, source snapshot or analysis result is opened here.
    path = out / 'formal_execution_v1/execution.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    stages = record.get('stages') or []
    if (record.get('status') != 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT'
            or not record.get('finished_utc') or record.get('supervised_child_observed_running') is True
            or [s.get('name') for s in stages] != ['main', 'native_remaining', 'external']
            or not all(s.get('status') == 'TERMINAL' and s.get('exit_code') == 0 and s.get('finished_utc') for s in stages)):
        raise RuntimeError('formal sequence is not fully terminal; do not read raw or restart it')
    public = json.loads((out / 'public_pilot_v1/execution.json').read_text(encoding='utf-8'))
    if not public.get('finished_utc') or public.get('status') == 'RUNNING':
        raise RuntimeError('public lifecycle is not terminal')
    # This analysis must also be scheduled before a separate live retrieval run.
    # The retrieval driver has no global process receipt/lock suitable for proving
    # liveness; a caller-supplied quiescence receipt is required by main().
    return record


def acquire_readonly_execution_locks(out, stack):
    import fcntl
    for name in ('formal_sequence_execution.lock', 'real_provider_execution.lock'):
        stream = stack.enter_context((out / 'manifests' / name).open('r'))
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Closing the descriptor releases the advisory lock. The file is never
        # created, truncated or written by this audit.


def raw_identity(a, name, row, planned, cases, source):
    rid = row.get('run_id')
    unit = planned.get(rid)
    if not a.require(unit is not None, 'FOREIGN_RAW_UNIT_REJECTED', rid, file=name):
        return False
    a.require(all(row.get(key) == unit.get(key) for key in IDENTITY if key in row),
              'RAW_UNIT_IDENTITY_MISMATCH', rid, file=name)
    a.require(row.get('phase') == unit['phase'] and row.get('task_id') == unit['task_id'],
              'RAW_PHASE_TASK_IDENTITY_MISMATCH', rid, file=name)
    if name == 'resource_samples.jsonl':
        measured = row.get('processes')
        a.require(isinstance(measured, list) and bool(measured) and
                  all(isinstance(value, dict) and integer(value.get('pid')) and value['pid'] > 0
                      and integer(value.get('rss')) and integer(value.get('fds'))
                      and number(value.get('cpu_sec')) and value['cpu_sec'] >= 0 for value in measured),
                  'RESOURCE_PROCESS_MEASUREMENT_SCHEMA_INVALID', rid)
    else:
        a.require(row.get('processes') is unit.get('processes'), 'RAW_PROCESS_MODE_MISMATCH', rid, file=name)
    for field, wanted in (('source_sha256', source),
                          ('effective_contract_sha256', unit['effective_contract_sha256']),
                          ('model_evidence', 'real_provider'),
                          ('task_sha256', cases[unit['task_id']]['content_sha256']),
                          ('model_id', unit['model_id'])):
        if field in row or name == 'task_runs.jsonl':
            a.require(row.get(field) == wanted, 'RAW_SOURCE_CONTRACT_MODEL_OR_TASK_MISMATCH', rid,
                      file=name, field=field)
    return True


def projection(name, row):
    # No prompt/transcript/response text, logical wire body, memory record payload,
    # or RPC contents are copied into the on-disk metadata index.
    if name == 'model_io.jsonl':
        if not row.get('phase', '').startswith('external_') or row.get('direction') != 'http_request_body':
            return None
        body = row.get('request')
        encoded = json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        return {key: value for key, value in row.items() if key != 'request'} | {
            'reconstructed_body_sha256': hashlib.sha256(encoded).hexdigest(),
            'reconstructed_body_bytes': len(encoded)}
    if name == 'external_framework_runs.jsonl':
        excluded = {'framework_data', 'model_messages', 'model_response_text', 'events'}
        return {key: value for key, value in row.items() if key not in excluded} | {
            'actual_scheduler': (row.get('framework_data') or {}).get('actual_scheduler')}
    if name == 'messages.jsonl':
        return {key: value for key, value in row.items() if key not in {'logical_message', 'llm_wire_view'}}
    if name == 'memory_events.jsonl':
        return {key: value for key, value in row.items() if key not in {'query', 'record', 'arguments', 'options'}}
    return row


class MetadataIndex:
    """Bounded-memory, per-unit audit retrieval; raw sources are read-only."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA cache_size=-16384')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('PRAGMA mmap_size=0')
        self.db.execute('CREATE TABLE metadata (file TEXT, line INTEGER, run_id TEXT, request_id TEXT, data TEXT, PRIMARY KEY(file,line))')
        self.db.execute('CREATE INDEX by_unit ON metadata(run_id,file)')
        self.counts = Counter()

    def ingest(self, a, name, planned, cases, source):
        relative = 'raw/' + name
        path = a.out / relative
        if not path.exists():
            if name in ('task_unit_starts.jsonl', 'task_runs.jsonl'):
                a.find('FAIL', 'RAW_FILE_MISSING', file=name)
            # Event files may legitimately be absent when their operation was
            # never reached. The per-unit audit determines applicability.
            return
        before = path.stat()
        value = hashlib.sha256()
        with path.open('rb') as stream:
            for line_no, line in enumerate(stream, 1):
                value.update(line)
                if not line.strip():
                    continue
                # Native model I/O is opaque/hash-only. For external requests,
                # decode just to project exact SDK body hash/byte metadata; the
                # body itself is never retained or printed by this analyzer.
                if name == 'model_io.jsonl' and not re.search(rb'"phase"\s*:\s*"external_(?:autogen|langgraph)"', line):
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError('not an object')
                    if not raw_identity(a, name, row, planned, cases, source):
                        continue
                    selected = projection(name, row)
                    if selected is not None:
                        selected.update(_raw_file=name, _raw_line=line_no,
                                        _raw_line_sha256=hashlib.sha256(line).hexdigest())
                        self.db.execute('INSERT INTO metadata VALUES (?,?,?,?,?)',
                                        (name, line_no, row['run_id'], row.get('request_id'),
                                         json.dumps(selected, ensure_ascii=False)))
                        self.counts[name] += 1
                except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                    a.find('FAIL', 'RAW_ROW_UNREADABLE_OR_UNPROJECTABLE', file=name,
                           line=line_no, error_type=type(exc).__name__)
        self.db.commit()
        after = path.stat()
        a.evidence[relative] = {'sha256': value.hexdigest(), 'bytes': after.st_size}
        a.require(before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns,
                  'RAW_CHANGED_DURING_INGEST', file=name)

    def rows(self, name, rid=None):
        sql, parameters = ('SELECT data FROM metadata WHERE file=?', [name])
        if rid is not None:
            sql = 'SELECT data FROM metadata INDEXED BY by_unit WHERE file=? AND run_id=?'
            parameters.append(rid)
        sql += ' ORDER BY line'
        return [json.loads(value[0]) for value in self.db.execute(sql, parameters)]

    def close(self):
        self.db.close()

    def identities(self, a, name, field, code):
        if field not in ('run_id', 'request_id'):
            raise ValueError('unsupported metadata identity column')
        result = set()
        # Only ID/count pairs leave SQLite. Global wide task/call JSON is never
        # loaded into RAM; every detailed record is fetched one unit at a time.
        for value, count in self.db.execute('SELECT ' + field + ',COUNT(*) FROM metadata WHERE file=? GROUP BY ' + field,
                                            (name,)):
            a.require(value is not None and count == 1, code,
                      value if field == 'run_id' else None, identity=value, count=count)
            result.add(value)
        return result


def memory_policy(a, rid, unit, case, events, cases):
    condition = unit.get('intervention') or ''
    seeded = condition.startswith('memory_') and condition != 'memory_none'
    for event in events:
        if event.get('event') != 'retrieve':
            continue
        for candidate in event.get('candidates') or []:
            source_id = candidate.get('source_task_id')
            source = cases.get(source_id, {})
            if seeded:
                expected_related = condition == 'memory_related'
                a.require(source_id == 'prior-' + case['task_id'] and
                          candidate.get('source_chain_id') == case['chain_id'] and
                          candidate.get('related_under_automatic_causal_rule') is expected_related,
                          'SEEDED_MEMORY_SOURCE_OR_CONDITION_POLICY_MISMATCH', rid)
            else:
                a.require(candidate.get('source_chain_id') == case['chain_id'] and
                          source.get('chain_id') == case['chain_id'] and
                          source.get('observations', {}).get('checkpoint', math.inf) < case['observations']['checkpoint'] and
                          candidate.get('related_under_automatic_causal_rule') is True,
                          'MEMORY_NOT_SAME_CHAIN_PRIOR_UNDER_FROZEN_RULE', rid)
            a.require(candidate.get('label_policy_version') == 'causal_same_chain_any_prior_v1',
                      'MEMORY_POLICY_VERSION_MISMATCH', rid)


def index_boundaries(a, rid, task, unit, events, spans):
    indexes = [row for row in events if row.get('event') == 'index_operation']
    enclosing_spans = [span for span in spans if span.get('category') in ('memory', 'retrieval')]
    first_memory = min((span['start_ns'] for span in enclosing_spans), default=math.inf)
    seed_candidates = []
    condition = unit.get('intervention') or ''
    for row in indexes:
        left, right = row.get('start_ns'), row.get('end_ns')
        valid = integer(left) and integer(right) and right >= left
        a.require(valid and row.get('index_latency_ns') == right - left and integer(row.get('cpu_ns'))
                  and row.get('parent_memory_operation') in ('retrieve', 'write', 'record_feedback', 'mark_used')
                  and 'embedding, SQLite, candidate merging and reranking excluded' in row.get('boundary', ''),
                  'INDEX_ONLY_BOUNDARY_INVALID', rid)
        if not valid:
            continue
        enclosed = any(span.get('start_ns', right + 1) <= left <= right <= span.get('end_ns', -1)
                       for span in enclosing_spans)
        if not enclosed:
            precise_seed = (condition in ('memory_related', 'memory_unrelated', 'memory_stale', 'memory_wrong')
                            and task.get('memory_count_before') == 0 and task.get('memory_count_after_condition_seed') == 1
                            and row.get('parent_memory_operation') == 'write'
                            and row.get('operation') in ('search', 'add')
                            and task.get('wall_start_monotonic_ns', right + 1) <= left <= right
                            <= task.get('wall_end_monotonic_ns', -1) and right <= first_memory)
            if precise_seed:
                seed_candidates.append(row)
                a.unknown('CONDITION_SEED_LOGICAL_WRITE_SPAN_NOT_OBSERVED', rid,
                          operation=row.get('operation'), index_boundary_observed=True,
                          logical_seed_write_latency='NOT_OBSERVED',
                          blocks_claim='complete logical memory-write timing coverage including condition seeding')
            else:
                a.find('FAIL', 'INDEX_ONLY_WITHOUT_ENCLOSING_MEMORY_SPAN', rid)
    if unit['arm'].endswith('1') and not indexes:
        a.unknown('MEMORY_INDEX_OPERATION_NOT_OBSERVED', rid)
    return len(indexes), len(seed_candidates)


def audit_requests(a, rid, unit, case, starts, calls, preflight, external_bodies, budget):
    first = a.unique(starts, 'request_id', 'DUPLICATE_ATTEMPT_START')
    last = a.unique(calls, 'request_id', 'DUPLICATE_ATTEMPT_END')
    a.require(set(first) == set(last), 'UNMATCHED_PROVIDER_START_END', rid)
    is_external = unit['phase'].startswith('external_')
    boundaries = a.unique(external_bodies if is_external else preflight,
                          'request_id', 'DUPLICATE_HTTP_BOUNDARY')
    a.require(set(boundaries) <= set(last), 'HTTP_BOUNDARY_WITHOUT_ATTEMPT', rid)
    for request_id in set(first) & set(last):
        begin, end = first[request_id], last[request_id]
        a.require(request_id == rid + '-' + str(begin.get('attempt')) and
                  begin.get('attempt') == end.get('attempt') and begin.get('run_id') == end.get('run_id') == rid,
                  'ATTEMPT_IDENTITY_OR_SEQUENCE_MISMATCH', rid, request_id=request_id)
        a.require(begin.get('requested_model') == end.get('requested_model') == unit['model_id']
                  and begin.get('endpoint') == 'https://www.dmxapi.cn/v1/chat/completions'
                  and begin.get('temperature') == 0 and begin.get('max_tokens') == 1500
                  and begin.get('timeout_sec') == 60,
                  'REQUEST_FROZEN_MODEL_OR_SETTINGS_MISMATCH', rid, request_id=request_id)
        body_options = {'thinking': {'type': 'disabled'}} if unit['model'] == 'cross' else {'enable_thinking': False}
        a.require(begin.get('extra_body') == body_options, 'REQUEST_THINKING_CONFIGURATION_MISMATCH', rid)
        a.require(end.get('status') in ('OK', 'ERROR') and bool(end.get('end_utc')),
                  'ATTEMPT_NOT_TERMINAL', rid, request_id=request_id)
        a.require(sha256_string(end.get('actual_prompt_sha256')) and
                  begin.get('actual_prompt_sha256') == end.get('actual_prompt_sha256'),
                  'PROMPT_HASH_START_END_MISMATCH', rid)
        boundary = boundaries.get(request_id)
        if boundary is None:
            if end.get('status') == 'OK':
                a.find('FAIL', 'SUCCESSFUL_REQUEST_WITHOUT_ACTUAL_HTTP_BOUNDARY', rid)
            else:
                a.unknown('ERROR_ATTEMPT_HTTP_BOUNDARY_NOT_OBSERVED', rid,
                          blocks_claim='complete actual request-byte coverage for every attempted request')
        else:
            count = boundary.get('actual_utf8_bytes') if is_external else boundary.get('actual_http_body_bytes')
            body_sha = boundary.get('reconstructed_body_sha256') if is_external else boundary.get('actual_http_body_sha256')
            a.require(sha256_string(body_sha) and body_sha == end.get('actual_http_body_sha256')
                      and integer(count) and count == end.get('actual_request_utf8_bytes')
                      and 0 < count <= budget.get('max_actual_utf8_request_bytes', 0),
                      'ACTUAL_REQUEST_BODY_HASH_BYTES_JOIN_FAILED', rid, request_id=request_id)
            if is_external:
                a.require(boundary.get('headers') == 'NOT_RETAINED' and
                          boundary.get('reconstructed_body_bytes') == count and
                          end.get('actual_http_dispatches') == 1 and end.get('sdk_max_retries') == 0,
                          'EXTERNAL_SDK_BODY_OR_HIDDEN_RETRY_CONTRACT_FAILED', rid)
        audits = begin.get('fact_audit') or []
        inventory = sorted(case['observations'])
        a.require(begin.get('business_facts_equal') is True and bool(audits), 'BUSINESS_FACT_AUDIT_MISSING', rid)
        for audit in audits:
            if is_external:
                expected_sha = hashlib.sha256(canonical(case['observations']).encode()).hexdigest()
                a.require(audit.get('source_facts_equal') is True and audit.get('observation_copies') == 1
                          and audit.get('fact_inventory') == inventory and audit.get('source_facts_sha256') == expected_sha
                          and audit.get('expected_fields_present') is False
                          and audit.get('representation') == 'fixed_external_json_role_handoff_v1',
                          'EXTERNAL_FIXED_FACT_HANDOFF_AUDIT_FAILED', rid)
            else:
                a.require(audit.get('source_facts_equal') is True and audit.get('expected_fact_inventory') == inventory
                          and audit.get('inline_observation_copies', 0) + audit.get('resolved_observation_copies', 0) == 1
                          and audit.get('duplicate_task_fact_section_removed') is True
                          and audit.get('instruction_contract_unchanged') is True,
                          'NATIVE_FACT_INVENTORY_OR_SINGLE_COPY_GUARD_FAILED', rid)


def audit_resources_and_cleanup(a, rid, task, samples):
    for field in ('workers_remaining', 'shm_names_created_but_remaining'):
        if field not in task:
            a.unknown('CLEANUP_NOT_OBSERVED', rid, field=field,
                      blocks_claim='complete worker/shared-memory cleanup certification')
        else:
            a.require(task[field] == [], 'WORKER_OR_SHM_LEAK_RECORDED', rid, field=field)
    a.require(not task.get('cleanup_error'), 'SDK_CLIENT_CLEANUP_ERROR_RECORDED', rid)
    if not samples:
        a.unknown('RESOURCE_SAMPLES_NOT_OBSERVED', rid,
                  blocks_claim='sampled process-group resource peak coverage')
        return
    for sample in samples:
        a.require(sample.get('sample_interval_sec') == .2 and
                  'not proportional set size' in sample.get('rss_semantics', ''),
                  'RESOURCE_SAMPLING_SEMANTICS_MISMATCH', rid)
    rss = max(sum(value.get('rss', 0) for value in sample.get('processes', [])) for sample in samples)
    fds = max(sum(value.get('fds', 0) for value in sample.get('processes', [])) for sample in samples)
    a.require(task.get('sampled_peak_process_group_rss') == rss and
              task.get('sampled_peak_process_group_fds') == fds, 'SAMPLED_RESOURCE_PEAK_MISMATCH', rid)


def audit_native(a, rid, unit, case, task, data, cases):
    state_expected = unit['arm'] in ('SS0', 'SS1') and (case['state_spec']['applicable'] or unit['phase'].startswith('public_'))
    dimension = {'small': 192, 'medium': 768, 'large': 1536}[case['state_spec']['size_class']]
    a.require(task.get('state_exchange_enabled') is state_expected and
              task.get('state_dimension') == (dimension if state_expected else None), 'NATIVE_STATE_CONFIGURATION_MISMATCH', rid)
    state = audit_state_lifecycles(a, rid, task, data['states.jsonl'], data['spans.jsonl'])
    accounts = data['ipc_accounting.jsonl']
    if not accounts:
        a.unknown('IPC_ACCOUNTING_NOT_OBSERVED', rid, blocks_claim='physical IPC byte coverage')
    else:
        a.require(len(accounts) == 1, 'DUPLICATE_IPC_ACCOUNTING', rid)
        account = accounts[-1]
        actual = Counter()
        for event in data['ipc_socket_events.jsonl']:
            a.require(integer(event.get('successful_bytes')) and event.get('direction') in ('send', 'recv')
                      and event.get('contents') == 'NOT_RETAINED', 'SOCKET_EVENT_INVALID', rid)
            if integer(event.get('successful_bytes')):
                actual[(event.get('socket'), event.get('direction'))] += event['successful_bytes']
        endpoints = account.get('endpoints') or []
        endpoint_bytes = {(row.get('socket'), row.get('direction')): row.get('actual_bytes') for row in endpoints}
        total = sum(actual.values())
        a.require(account.get('framing_consumption_conserved') is True and dict(actual) == endpoint_bytes
                  and len(endpoint_bytes) == len(endpoints)
                  and all(row.get('actual_bytes') == row.get('framing_consumed_bytes') for row in endpoints)
                  and sum(row.get('actual_bytes', 0) for row in endpoints) == account.get('observed_socket_bytes') == total
                  and sum((account.get('by_boundary') or {}).values()) == total
                  and account.get('successful_syscalls') == len(data['ipc_socket_events.jsonl'])
                  and task.get('observable_ipc_bytes') == total, 'IPC_FRAMING_OR_TASK_ACCOUNTING_NOT_CONSERVED', rid)
    for row in data['messages.jsonl']:
        a.require(integer(row.get('physical_payload_bytes')) and
                  row.get('framed_payload_bytes') == row['physical_payload_bytes'] + 4,
                  'LOGICAL_MESSAGE_FRAME_BYTES_INVALID', rid)
    index_count, seed_count = index_boundaries(a, rid, task, unit, data['memory_events.jsonl'], data['spans.jsonl'])
    memory_policy(a, rid, unit, case, data['memory_events.jsonl'], cases)
    for identity in data['observer_identity.jsonl']:
        spec = identity.get('specification') or {}
        a.require((spec.get('consumer_clone') or {}).get('stripped_ast_identical') is True
                  and (spec.get('mapping_probe') or {}).get('constructor_code_object_identical') is True
                  and identity.get('raw_rpc_body_recorded') is False, 'OBSERVER_NATIVE_IDENTITY_GUARD_FAILED', rid)
    if not data['observer_identity.jsonl']:
        a.unknown('OBSERVER_IDENTITY_NOT_OBSERVED', rid, blocks_claim='native measurement-hook equivalence')
    summaries = data['observer_summary.jsonl']
    if not summaries:
        a.unknown('OBSERVER_CLEANUP_SUMMARY_NOT_OBSERVED', rid)
    a.require(all(row.get('all_hooks_restored') is True and row.get('no_new_mapping_is_not_zero_mapping_latency') is True
                  for row in summaries), 'OBSERVER_CLEANUP_OR_MAPPING_SEMANTICS_FAILED', rid)
    pids = task.get('worker_pids') or {}
    if unit['processes'] and task.get('success'):
        a.require(set(pids) == {'planner', 'retriever', 'executor', 'reviewer'}
                  and len(set(pids.values())) == 4 and all(integer(pid) and pid > 0 for pid in pids.values()),
                  'SUCCESSFUL_MULTIPROCESS_ROLE_PID_IDENTITIES_INVALID', rid)
    if not unit['processes']:
        a.require(not pids, 'INPROCESS_NATIVE_UNIT_RECORDED_AGENT_WORKERS', rid)
    return {**state, 'index_operations': index_count,
            'condition_seed_index_events_without_logical_write_span': seed_count}


def audit_external(a, rid, unit, task, data):
    framework = unit['phase'].removeprefix('external_')
    roles = ['planner', 'retriever', 'executor', 'reviewer']
    a.require(unit['arm'] == 'T0' and unit['processes'] is False and unit.get('intervention') is None
              and task.get('actual_topology') == 'framework_inprocess'
              and task.get('comparison_scope') == 'SYSTEM_LEVEL_REFERENCE', 'EXTERNAL_BASELINE_CONDITION_MISMATCH', rid)
    account = task.get('ipc_accounting') or {}
    a.require(task.get('observable_ipc_bytes') == 0 and account.get('observed_socket_bytes') == 0
              and account.get('successful_syscalls') == 0 and account.get('framing_consumption_conserved') is True
              and not account.get('by_boundary') and task.get('state_consumed') is False
              and task.get('memory_count_before') == task.get('memory_count_after') == 0
              and task.get('state_dimension') is None
              and task.get('local_state_materialized_bytes') == task.get('logical_state_bytes') == 0,
              'EXTERNAL_INPROCESS_NO_STATE_MEMORY_IPC_CONTRACT_FAILED', rid)
    for name in ('states.jsonl', 'memory_events.jsonl', 'observer_identity.jsonl', 'observer_summary.jsonl',
                 'ipc_accounting.jsonl', 'ipc_socket_events.jsonl', 'messages.jsonl'):
        a.require(not data[name], 'EXTERNAL_UNEXPECTED_NATIVE_INSTRUMENTATION_ROWS', rid, file=name)
    work = data['external_framework_runs.jsonl']
    events = data['external_execution_events.jsonl']
    if not work:
        a.unknown('EXTERNAL_FRAMEWORK_TERMINAL_NOT_OBSERVED', rid,
                  blocks_claim='actual framework scheduler and role-work coverage')
        return {'index_operations': 0, 'produced_plan_uris': 0, 'consumed_plan_uris': 0}
    a.require(len(work) == 1, 'DUPLICATE_EXTERNAL_FRAMEWORK_TERMINAL', rid)
    work = work[-1]
    expected_versions = {'autogen-agentchat': '0.7.5', 'langgraph': '1.2.16'}
    counts = Counter(event.get('kind') for event in events)
    a.require(work.get('framework') == task.get('framework') == framework
              and work.get('role_order') == task.get('role_order') == roles
              and work.get('logical_model_invocations') == 1
              and work.get('framework_versions') == task.get('external_framework_versions') == expected_versions
              and work.get('success') is task.get('success') and work.get('quality') == task.get('quality'),
              'EXTERNAL_ACTUAL_FRAMEWORK_RESULT_IDENTITY_MISMATCH', rid)
    wanted_scheduler = ('autogen_agentchat.teams.RoundRobinGroupChat' if framework == 'autogen' else
                        'langgraph.graph.StateGraph.compile().ainvoke')
    a.require(work.get('actual_scheduler') == wanted_scheduler and
              work.get('answer') == (task.get('outputs') or {}).get('answer'),
              'EXTERNAL_SCHEDULER_OR_SELECTED_ANSWER_MISMATCH', rid)
    a.require([event.get('role') for event in events if event.get('kind') == 'role.start'] == roles
              and counts['role.start'] == counts['role.end'] == counts['framework.agent_input'] == 4
              and counts['model.invoke'] == 1 and counts['tool.lookup'] == 2,
              'EXTERNAL_FOUR_ROLE_WORK_COUNTER_CONTRACT_FAILED', rid)
    a.require(work.get('state_exchange') == 'NOT_ENABLED_NATIVE_SS_MECHANISM_NOT_IMPLEMENTED_BY_EXTERNAL_FRAMEWORK'
              and work.get('local_serialized_handoff_bytes_are_not_ipc_bytes') is True,
              'EXTERNAL_STATE_OR_LOCAL_HANDOFF_SEMANTICS_LOST', rid)
    logical_pids = task.get('logical_role_pids') or {}
    a.require(set(logical_pids) == set(roles) and len(set(logical_pids.values())) == 1
              and not task.get('worker_pids'), 'EXTERNAL_LOGICAL_ROLE_PID_CONTRACT_FAILED', rid)
    return {'index_operations': 0, 'produced_plan_uris': 0, 'consumed_plan_uris': 0,
            'actual_external_role_starts': counts['role.start'], 'logical_model_invocations': 1}


def frozen_fact_oracle(a, snapshot, freeze):
    """Execute only hash-bound pure fixture predicates, never a runtime driver."""
    wanted = {'contract.py': '44e5658967db62bdfafe54accabddfdc187bd6a241fc80d59fef32fff4192749',
              'oracle.py': '0757cfbc5c02aa5e5b726b9937fc06667a402257655f2eba1810bb56e4ae11b3'}
    modules = {}
    for name, expected in wanted.items():
        path = snapshot / name
        if not a.require(freeze['files'].get(str(path)) == expected and digest(path) == expected,
                         'PURE_ORACLE_FROZEN_SOURCE_IDENTITY_MISMATCH', file=str(path)):
            raise RuntimeError('pure fixture oracle source is not the reviewed frozen version')
        source = path.read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(source)):
            imports = [alias.name.split('.')[0] for alias in node.names] if isinstance(node, ast.Import) else [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module else []
            if not all(module in {'__future__', 'argparse', 'hashlib', 'ipaddress', 'json', 'pathlib', 'contract'} for module in imports):
                raise RuntimeError('pure fixture predicate unexpectedly imports runtime/provider code')
        module = types.ModuleType('postrun_frozen_' + path.stem)
        module.__file__ = str(path)
        prior = sys.modules.get('contract')
        try:
            if 'contract.py' in modules:
                sys.modules['contract'] = modules['contract.py']
            exec(compile(source, str(path), 'exec'), module.__dict__)
        finally:
            if prior is None:
                sys.modules.pop('contract', None)
            else:
                sys.modules['contract'] = prior
        modules[name] = module
    return modules['oracle.py'], modules['contract.py']


def audit_fixture_truth(a, rid, task, case, oracle, truth):
    answer = (task.get('outputs') or {}).get('answer')
    selected = answer.get('action') if isinstance(answer, dict) else None
    observed = task.get('observed_fixture_check')
    selected_match = False
    if selected is not None:
        try:
            actual = oracle.check(case, selected)
            selected_match = actual['current_record_matches_check']
            if observed is not None:
                a.require(all(observed.get(key) == actual.get(key) for key in actual),
                          'RECORDED_FIXTURE_CHECK_DIFFERS_FROM_CURRENT_FACT_ORACLE', rid)
        except (ValueError, KeyError):
            a.require(task.get('success') is False, 'UNSUPPORTED_SELECTED_ACTION_SUCCEEDED', rid)
    if task.get('success'):
        a.require(isinstance(answer, dict) and answer.get('diagnosis') == truth['diagnosis']
                  and selected_match and observed is not None,
                  'SUCCESS_WITHOUT_MATCHING_FACTUAL_DIAGNOSIS_AND_READONLY_CHECK', rid)
    if answer is None:
        a.require(task.get('success') is False, 'SUCCESS_WITHOUT_SELECTED_ANSWER', rid)
    return {'factual_case_checked': True, 'selected_action_present': selected is not None,
            'selected_action_matches_current_measurements': selected_match,
            'recorded_readonly_check_present': observed is not None,
            'factual_scope': 'authored current fixture measurements, not live Linux tools'}


def verify_reused_functions(a, snapshot):
    reference = snapshot / 'public_metric_audit.py'
    a.require(digest(reference) == PUBLIC_AUDITOR_SHA256, 'PUBLIC_AUDITOR_PREIMAGE_CHANGED')
    before = ast.parse(reference.read_text(encoding='utf-8'))
    current = ast.parse(Path(__file__).read_text(encoding='utf-8'))
    left = {node.name: ast.dump(node, include_attributes=False) for node in before.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    right = {node.name: ast.dump(node, include_attributes=False) for node in current.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    a.require(all(left.get(name) == right.get(name) for name in PURE_REUSED),
              'REUSED_PUBLIC_PURE_CHECKS_CHANGED')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-out', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, default=Path('/opt/lingxi/formal-checkout'))
    parser.add_argument('--quiescence-receipt', type=Path, required=True,
                        help='new receipt from actual root process/lock observation; never synthesize from planned timing')
    parser.add_argument('--version', default='full_metric_audit_v3')
    args = parser.parse_args()
    out = args.study_out.resolve()
    if out.name != STUDY or re.fullmatch(r'full_metric_audit_v[1-9][0-9]*', args.version) is None:
        parser.error('exact corrected study and a fresh full_metric_audit_vN version are required')
    destination = out / 'analysis' / args.version
    if destination.exists():
        parser.error('audit output exists; retain it and choose a new version')
    try:
        closed = lifecycle_gate(out)
        receipt_path = args.quiescence_receipt.resolve()
        if not receipt_path.is_relative_to(out / 'analysis') or receipt_path.suffix != '.json':
            raise RuntimeError('quiescence receipt must be a JSON metadata file inside this study analysis directory')
        quiet = json.loads(args.quiescence_receipt.read_text(encoding='utf-8'))
        if (quiet.get('study_id') != STUDY or quiet.get('status') != 'ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING'
                or not quiet.get('verified_utc') or quiet.get('active_real_timing_processes') != []
                or quiet.get('active_retrieval_timing_processes') != []):
            raise RuntimeError('actual process-window quiescence has not been documented')
        verified = datetime.fromisoformat(quiet['verified_utc'].replace('Z', '+00:00'))
        finished = datetime.fromisoformat(closed['finished_utc'].replace('Z', '+00:00'))
        if verified.tzinfo is None or finished.tzinfo is None or verified < finished:
            raise RuntimeError('quiescence must be actually observed after formal sequence termination')
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    # No source plan or raw is opened until both lifecycle and live lock checks
    # succeed. This script never writes input locks or creates an execution.
    with ExitStack() as stack:
        try:
            acquire_readonly_execution_locks(out, stack)
        except (OSError, ImportError) as exc:
            parser.error('cannot obtain existing Linux execution locks: ' + type(exc).__name__)
        a = Audit(out)
        destination.mkdir(parents=True)
        db = None
        unit_stream = None
        observation_stream = None
        totals = Counter()
        phase_summary = defaultdict(Counter)
        truths = {}
        observation_count = 0
        a.evidence[str(args.quiescence_receipt.resolve())] = {
            'sha256': digest(args.quiescence_receipt), 'bytes': args.quiescence_receipt.stat().st_size}
        a.evidence[str(Path(__file__).resolve())] = {
            'sha256': digest(Path(__file__)), 'bytes': Path(__file__).stat().st_size}
        try:
            execution = a.read('formal_execution_v1/execution.json')
            registration = a.read('public_pilot_v1/registration.json')
            public_execution = a.read('public_pilot_v1/execution.json')
            freeze = a.read('manifests/formal-freeze.json')
            if digest(out / 'manifests/formal-freeze.json') != FORMAL_FREEZE_SHA256:
                a.find('FAIL', 'REGISTERED_FORMAL_FREEZE_BYTES_CHANGED')
                raise RuntimeError('formal freeze differs from the independently recorded pre-execution authority')
            plan = a.read('manifests/formal_plan.json')
            corpus = a.read('corpus/candidate_v2/tasks.jsonl', jsonl=True)
            budget = a.read('manifests/budget_limits.json')
            planned = a.unique(plan, 'run_id', 'DUPLICATE_FULL_PLAN_UNIT')
            expected = set(planned)
            cases = a.unique(corpus, 'task_id', 'DUPLICATE_CORPUS_TASK')
            a.require(len(plan) == len(expected) == 6504 and Counter(unit.get('phase') for unit in plan) == PHASE_COUNTS,
                      'FULL_FIXED_6504_PHASE_MATRIX_MISMATCH')
            a.require(execution.get('formal_freeze_sha256') == digest(out / 'manifests/formal-freeze.json')
                      and freeze.get('study_id') == STUDY and freeze.get('source_sha256') == registration.get('source_sha256'),
                      'FORMAL_LIFECYCLE_FREEZE_SOURCE_BINDING_MISMATCH')
            snapshot = Path(freeze['driver_snapshot'])
            frozen_execution_plan = a.read('formal_snapshot_v1/execution_plan.json')
            a.require([stage.get('name') for stage in frozen_execution_plan.get('stages', [])] ==
                      ['main', 'native_remaining', 'external'], 'FROZEN_STAGE_PLAN_MISMATCH')
            for actual, stage in zip(execution['stages'], frozen_execution_plan['stages']):
                a.require(actual.get('name') == stage['name'] and actual.get('command', [])[1:] ==
                          ['-B', str(snapshot / stage['script']), *stage['args']], 'FORMAL_STAGE_SNAPSHOT_COMMAND_MISMATCH')
            a.require(public_execution.get('driver_origin') == str(Path(registration['driver_snapshot']) / 'real_driver.py')
                      and public_execution.get('registration_sha256') == digest(out / 'public_pilot_v1/registration.json'),
                      'PUBLIC_LIFECYCLE_SNAPSHOT_BINDING_MISMATCH')
            a.require(set(registration['public_unit_ids']) == {unit['run_id'] for unit in plan if unit['phase'] == PHASE},
                      'PUBLIC_REGISTERED_IDS_DIFFER_FROM_FULL_PLAN')
            for name, wanted in freeze.get('files', {}).items():
                path = Path(name)
                if path.name in ('.env', 'model.env') or '/run/secrets/' in path.as_posix():
                    a.find('FAIL', 'FORBIDDEN_SECRET_IN_FREEZE_MANIFEST')
                    continue
                a.require(path.is_file() and digest(path) == wanted, 'FROZEN_INPUT_CHANGED', file=name)
            if args.runtime_root.is_dir():
                a.require(runtime_source_sha(args.runtime_root) == freeze['source_sha256'], 'BUSINESS_SOURCE_TREE_CHANGED')
            else:
                a.unknown('BUSINESS_SOURCE_TREE_NOT_AVAILABLE', blocks_claim='actual runtime source identity revalidation')
            verify_reused_functions(a, snapshot)
            for unit in plan:
                case = cases.get(unit.get('task_id'), {})
                a.require(unit.get('study_id') == STUDY and unit.get('model_id') == MODEL_IDS.get(unit.get('model'))
                          and case.get('split') == ('pilot' if unit['phase'] == PHASE else 'formal')
                          and unit.get('task_sha256') == case.get('content_sha256')
                          and sha256_string(unit.get('effective_contract_sha256')), 'FULL_PLAN_CASE_MODEL_CONTRACT_INVALID', unit.get('run_id'))
                body = {key: value for key, value in unit.items() if key not in ('run_id', 'unit_id', 'memory_namespace_sha256')}
                a.require(uuid.uuid5(uuid.NAMESPACE_URL, STUDY + '/' + json.dumps(body, sort_keys=True, separators=(',', ':'))).hex == unit['run_id'],
                          'PROSPECTIVE_UNIT_UUID_MISMATCH', unit['run_id'])
            if any(item['level'] == 'FAIL' for item in a.findings):
                raise RuntimeError('pre-raw frozen identity or plan checks failed; raw audit not entered')
            oracle, contract = frozen_fact_oracle(a, snapshot, freeze)
            contract_sha = hashlib.sha256(json.dumps(contract.LABEL_ACTIONS, sort_keys=True,
                                                     separators=(',', ':')).encode()).hexdigest()
            a.require(all(unit['effective_contract_sha256'] == contract_sha for unit in plan),
                      'PLAN_EFFECTIVE_CONTRACT_DIFFERS_FROM_FROZEN_24_PAIR_DATA')
            for task_id, case in cases.items():
                truth = oracle.classify_facts(case['domain'], case['observations'])
                truths[task_id] = truth
                a.require(truth == case.get('expected'), 'CURRENT_FACT_ORACLE_AND_FROZEN_CANONICAL_ACCEPTANCE_DIFFER',
                          task_id=task_id)
            a.require(len(truths) == 144 and len(contract.LABEL_ACTIONS) == 24, 'FULL_FACTUAL_CASE_CONTRACT_COVERAGE_MISMATCH')
            # Preserve the exact audited public prefix without treating formal
            # appends as mutation of the public registration.
            for relative, wanted in freeze.get('public_raw_prefixes', {}).items():
                value = hashlib.sha256()
                remaining = wanted['bytes']
                with (out / relative).open('rb') as stream:
                    while remaining:
                        data = stream.read(min(1024 * 1024, remaining))
                        if not data:
                            break
                        value.update(data)
                        remaining -= len(data)
                a.require(remaining == 0 and value.hexdigest() == wanted['sha256'], 'PUBLIC_RAW_AUDITED_PREFIX_CHANGED', file=relative)
            db = MetadataIndex(destination / 'metadata_index.sqlite')
            for name in FULL_RAW_NAMES:
                db.ingest(a, name, planned, cases, freeze['source_sha256'])
            terminals = db.identities(a, 'task_runs.jsonl', 'run_id', 'DUPLICATE_TERMINAL_TASK')
            starts = db.identities(a, 'task_unit_starts.jsonl', 'run_id', 'DUPLICATE_TASK_START')
            a.require(terminals == starts == expected, 'FULL_PLANNED_TASK_START_TERMINAL_SET_INCOMPLETE',
                      missing_terminal_run_ids=sorted(expected - terminals),
                      missing_task_start_run_ids=sorted(expected - starts))
            all_starts = db.identities(a, 'provider_attempt_starts.jsonl', 'request_id', 'GLOBAL_DUPLICATE_ATTEMPT_START')
            all_calls = db.identities(a, 'provider_calls.jsonl', 'request_id', 'GLOBAL_DUPLICATE_ATTEMPT_END')
            a.require(all_starts == all_calls and db.counts['provider_attempt_starts.jsonl'] <= budget.get('max_provider_attempts', 0),
                      'GLOBAL_PROVIDER_ATTEMPT_SET_OR_CEILING_FAILED')
            unit_stream = (destination / 'unit_checks.jsonl').open('x', encoding='utf-8')
            observation_stream = (destination / 'audited_observations.jsonl').open('x', encoding='utf-8')
            for rid in sorted(terminals):
                unit = planned[rid]
                before = len(a.findings)
                data = {name: db.rows(name, rid) for name in FULL_RAW_NAMES}
                task = data['task_runs.jsonl'][-1]
                calls = data['provider_calls.jsonl']
                a.require(len(calls) <= 3 and sorted(call.get('attempt', -1) for call in calls) == list(range(1, len(calls) + 1))
                          and task.get('provider_attempts') == len(calls), 'PER_TASK_ATTEMPT_SEQUENCE_INVALID', rid)
                a.require(task.get('status') in ('COMPLETED', 'FAILED', 'INCOMPLETE_BUDGET_STOP')
                          and isinstance(task.get('success'), bool)
                          and (task.get('status') == 'COMPLETED') is task.get('success')
                          and number(task.get('quality')), 'TASK_TERMINAL_STATUS_INVALID', rid)
                audit_requests(a, rid, unit, cases[unit['task_id']], data['provider_attempt_starts.jsonl'], calls,
                               data['http_request_preflight.jsonl'], data['model_io.jsonl'], budget)
                if calls:
                    complete, reported = audit_usage(a, rid, task, calls)
                else:
                    # The frozen native writer records vacuous sums as0 on a
                    # no-call path. Preserve those observations without calling
                    # the public provider/cache calibration kernel on no calls.
                    complete, reported = False, 0
                    for field in ('provider_input_tokens', 'provider_output_tokens',
                                  'provider_total_tokens', 'provider_cached_input_tokens',
                                  'reported_token_lower_bound'):
                        value = task.get(field)
                        a.require(value is None or (integer(value) and value == 0),
                                  'NO_REQUEST_HAS_NONZERO_OR_INVALID_USAGE', rid, field=field)
                    a.unknown('NO_PROVIDER_ATTEMPT_OBSERVED_USAGE_NOT_CALIBRATED', rid,
                              recorded_task_usage_complete=task.get('provider_usage_complete'),
                              task_failed=task.get('success') is False,
                              blocks_claim='real-provider usage calibration for this unit; no inferred or backfilled billed tokens')
                audit_spans(a, rid, task, data['spans.jsonl'])
                metrics = (audit_external(a, rid, unit, task, data) if unit['phase'].startswith('external_') else
                           audit_native(a, rid, unit, cases[unit['task_id']], task, data, cases))
                factual = audit_fixture_truth(a, rid, task, cases[unit['task_id']], oracle, truths[unit['task_id']])
                audit_resources_and_cleanup(a, rid, task, data['resource_samples.jsonl'])
                findings = a.findings[before:]
                item = {'run_id': rid, 'phase': unit['phase'], 'task_id': unit['task_id'], 'arm': unit['arm'],
                        'model': unit['model'], 'success': task['success'], 'task_status': task['status'],
                        'provider_attempts': len(calls), 'real_provider_usage_complete': complete,
                        'reported_total_tokens_lower_bound': reported, **metrics, **factual,
                        'audit_failures': sum(row['level'] == 'FAIL' for row in findings),
                        'unobserved_items': sum(row['level'] == 'UNKNOWN' for row in findings)}
                unit_stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + '\n')
                selected_observation = {key: task[key] for key in OBSERVATION_FIELDS if key in task}
                selected_observation.update(
                    schema_version='audited-observations-projection-v1',
                    raw_file=task['_raw_file'], raw_line=task['_raw_line'], raw_line_sha256=task['_raw_line_sha256'],
                    error_type=(task.get('error') or {}).get('type') if isinstance(task.get('error'), dict) else None,
                    provider_error_types=[call.get('error_type') for call in calls if call.get('status') == 'ERROR'],
                    absent_terminal_fields=[key for key in OBSERVATION_FIELDS if key not in task],
                    audit_failures=item['audit_failures'], unobserved_items=item['unobserved_items'],
                    source_semantics='verbatim recorded terminal projection; missing stays absent and null stays null; global audit report controls use')
                observation_stream.write(json.dumps(selected_observation, ensure_ascii=False, allow_nan=False) + '\n')
                observation_count += 1
                bucket = phase_summary[unit['phase']]
                bucket.update(terminal_units=1, successful_units=int(task['success']), provider_attempts=len(calls),
                              usage_complete_units=int(complete), factual_units_checked=1,
                              provider_errors=sum(call.get('status') != 'OK' for call in calls))
                totals.update(terminal_units=1, successful_units=int(task['success']), provider_attempts=len(calls),
                              failed_task_units=int(task['success'] is False),
                              provider_errors=sum(call.get('status') != 'OK' for call in calls),
                              factual_units_checked=1, reported_token_lower_bound=reported,
                              selected_actions_present=int(factual['selected_action_present']),
                              units_with_recorded_readonly_check=int(factual['recorded_readonly_check_present']),
                              units_without_selected_action=int(not factual['selected_action_present']))
            a.stable()
            a.require(all(Path(name).is_file() and digest(Path(name)) == value for name, value in freeze['files'].items()),
                      'FROZEN_INPUT_CHANGED_DURING_AUDIT')
        except BaseException as exc:
            a.find('FAIL', 'AUDITOR_EXCEPTION_RETAINED', error_type=type(exc).__name__,
                   message='raw/request/credential content omitted; inspect scoped finding codes')
        finally:
            if unit_stream is not None:
                unit_stream.close()
            if observation_stream is not None:
                observation_stream.close()
            if db is not None:
                db.close()
        counts = Counter(item['level'] for item in a.findings)
        status = 'FAIL_RETAINED' if counts['FAIL'] else 'INCONCLUSIVE_RETAINED' if counts['UNKNOWN'] else 'PASS_OBSERVED_FULL_RECORD_JOINS'
        report = {'status': status, 'analyzer_schema_version': SCHEMA_VERSION, 'study_id': STUDY,
                  'created_utc': datetime.now(timezone.utc).isoformat(), 'planned_units': 6504,
                  'public_units_planned': 144, 'formal_units_planned': 6360, **dict(totals),
                  'factual_cases_independently_classified': len(truths),
                  'metadata_projection_rows_by_file': dict(db.counts) if db is not None else {},
                  'audited_observations_schema': 'audited-observations-projection-v1',
                  'audited_observations_units': observation_count,
                  'phase_summary': {key: dict(value) for key, value in phase_summary.items()},
                  'finding_counts': dict(counts), 'public_and_formal_cohorts_separately_identified': True,
                  'automatic_full_validation': True, 'human_review_performed': False,
                  'prospective_random30_spotcheck_claimed': False, 'task_quality_rescored': False,
                  'old_study_results_read_or_pooled': False, 'primary_statistics_recomputed': False,
                  'holm_recomputed': False, 'primary_statistics_authority': 'saved statistical_gates.py formal output only',
                  'reused_public_auditor_sha256': PUBLIC_AUDITOR_SHA256,
                  'active_state_audit_entry': 'audit_state_lifecycles',
                  'active_state_implementation': 'new six-definition generation-aware implementation; same AST as reviewed a3771a3c proposal',
                  'state_audit_implementation_timing': 'post-collection analysis-only correction after retained v2 FAIL; before this v3 re-audit',
                  'legacy_state_ast_retained_but_not_active': True,
                  'legacy_state_auditor_preimage_sha256': '0d9a564522c8e61bf9ed19c143ad7a452415ae721d349fc62aaef79edfbc34cf',
                  'reviewed_state_proposal_sha256': 'a3771a3c964c4d94abb953391fa203ff85ac9f0cf57b92aa024654bd0be64c62',
                  'analyzer_sha256': digest(Path(__file__)), 'evidence': a.evidence,
                  'claim_scope': {
                      'canonical_fixture_facts': 'pure frozen oracle on144cases and selected-action/check joins across6504 planned units; not live Linux verification',
                      'provider_usage': 'provider-reported observed attempts only; missing/failed/no-attempt usage remains uncalibrated',
                      'condition_seed_timing': 'index spans observed inside unit wall; logical seed-write span NOT_OBSERVED blocks full logical memory-write coverage',
                      'native_state': 'per-unit URI/full-reference-hash/component-live-generation joins; dedup puts are aliases, not new physical origins; mapping/RPC/fused rules retained; no independent consumer payload rehash or separable pure-read timing',
                      'external_scope': 'same-process four logical roles; local handoff size is not interprocess IPC; SYSTEM_LEVEL_REFERENCE',
                      'statistics': 'no effects, CI, power or Holm here; read the separately saved sole-authority formal statistics',
                      'human_scope': 'automatic full audit replaces no historical human record; no invented reviewer or pre-outcome draw'},
                  'limits': ['Not a certification of all115semantic indicators or production diagnosis accuracy.',
                             'Observed task failures are retained; complete success is not an audit requirement.',
                             'Missing cached usage is null; explicit cached zero is retained as reported.',
                             'A mapped arena reused without a new mapping is not zero mapping latency.',
                             'Shared buffer-close groups are not summed as per-URI release durations.',
                             'Legacy audit_state AST is retained for reproducibility but inactive; this new state entry corrects post-collection analysis, leaves v2 FAIL/raw/quality/primary statistics intact.',
                             'RSS sums shared pages; samples establish neither PSS nor a continuous peak.',
                             'Retrieval quiescence is separately evidenced by actual caller receipt; existing real locks cover real runners.',
                             'External HTTP body JSON is decoded transiently for compact byte/hash reconstruction; request text/headers are never emitted or indexed.',
                             'Native model I/O is opaque/hash-only; actual HTTP byte/hash joins use its separate frozen preflight recorder.',
                             'Old30draw/human spotcheck and old report statistics must not be run as this study authority.']}
        with (destination / 'findings.json').open('x', encoding='utf-8') as stream:
            json.dump(a.findings, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        for name in ('unit_checks.jsonl', 'audited_observations.jsonl'):
            if not (destination / name).exists():
                with (destination / name).open('x', encoding='utf-8'):
                    pass
        report['output_sha256'] = {name: digest(destination / name) for name in
                                   ('findings.json', 'unit_checks.jsonl', 'audited_observations.jsonl')}
        with (destination / 'metric_audit.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print(json.dumps({'status': status, **dict(totals), 'finding_counts': dict(counts)}, ensure_ascii=False))
        return 1 if counts['FAIL'] else 2 if counts['UNKNOWN'] else 0


if __name__ == '__main__':
    sys.exit(main())


