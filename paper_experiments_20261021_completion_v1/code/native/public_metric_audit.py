"""Post-run, read-only audit of the newly registered real PUBLIC cohort.

Standard library only. Never imports a runtime/driver, reads credentials, calls a
provider, resumes a task, or combines the public cohort with formal/old outcomes.
The lifecycle receipt must already be terminal before any raw file is read.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

STUDY = 'protocol_repair_20261020_v1'
PHASE = 'public_calibration'
ARMS = {'T0', 'S0', 'SS0', 'T1', 'S1', 'SS1'}
SCHEMA_VERSION = 'public-metric-audit-v2-resource-process-list'
RAW_NAMES = (
    'task_unit_starts.jsonl', 'task_runs.jsonl',
    'provider_attempt_starts.jsonl', 'provider_calls.jsonl',
    'http_request_preflight.jsonl', 'messages.jsonl', 'ipc_socket_events.jsonl',
    'ipc_accounting.jsonl', 'states.jsonl', 'memory_events.jsonl', 'spans.jsonl',
    'resource_samples.jsonl', 'observer_identity.jsonl', 'observer_summary.jsonl',
    'execution_events.jsonl',
)


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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study-out', required=True, type=Path)
    p.add_argument('--version', default='public_metric_audit_v2')
    args = p.parse_args()
    out = args.study_out.resolve()
    if out.name != STUDY or re.fullmatch(r'public_metric_audit_v[1-9][0-9]*', args.version) is None:
        p.error('exact new study directory and a public_metric_audit_vN version are required')
    destination = out / 'analysis' / args.version
    if destination.exists():
        p.error('audit destination exists; use a new version, never replace evidence')
    execution_path = out / 'public_pilot_v1/execution.json'
    # This small receipt is the only live-state input touched before the timing gate.
    execution = json.loads(execution_path.read_text(encoding='utf-8'))
    if execution.get('status') == 'RUNNING' or not execution.get('finished_utc'):
        p.error('real timing has not finished; raw analysis is forbidden')
    a = Audit(out)
    execution = a.read('public_pilot_v1/execution.json')
    registration = a.read('public_pilot_v1/registration.json')
    plan = a.read('manifests/formal_plan.json')
    corpus = a.read('corpus/candidate_v2/tasks.jsonl', jsonl=True)
    budget = a.read('manifests/budget_limits.json')
    public = [u for u in plan if u.get('phase') == PHASE]
    planned = a.unique(public, 'run_id', 'DUPLICATE_PUBLIC_PLAN_UNIT')
    expected = set(planned)
    cases = {r.get('task_id'): r for r in corpus}
    a.require(len(public) == 144 and len(expected) == 144 and
              len({u.get('task_id') for u in public}) == 24 and
              set(u.get('arm') for u in public) == ARMS and
              set(Counter(u.get('arm') for u in public).values()) == {24}, 'PUBLIC_MATRIX_NOT_144')
    a.require(all(u.get('study_id') == STUDY and u.get('model') == 'main' and
                  cases.get(u.get('task_id'), {}).get('split') == 'pilot' for u in public),
              'PUBLIC_PLAN_FOREIGN_MODEL_STUDY_OR_SPLIT')
    a.require(set(registration.get('public_unit_ids', [])) == expected and
              registration.get('not_a_formal_freeze') is True, 'REGISTRATION_PUBLIC_IDS_MISMATCH')
    # Revalidate every registered file belonging to the evidence tree, including the
    # sealed driver snapshot; external runtime files retain the preflight gate scope.
    marker = '/' + STUDY + '/'
    for original, wanted in registration.get('files', {}).items():
        normalized = original.replace('\\', '/')
        if marker not in normalized:
            continue
        relative = normalized.split(marker, 1)[1]
        path = out / relative
        if path.is_file():
            a.evidence.setdefault(relative, {'sha256': digest(path), 'bytes': path.stat().st_size})
        a.require(path.is_file() and digest(path) == wanted, 'REGISTERED_INPUT_CHANGED', file=relative)
    data = {name: a.read('raw/' + name, jsonl=True,
                         required=name in ('task_unit_starts.jsonl', 'task_runs.jsonl')) for name in RAW_NAMES}
    foreign_rejected = {}
    for name, rows in data.items():
        for row in rows:
            rid = row.get('run_id')
            a.require(rid in expected and row.get('phase') == PHASE,
                      'FOREIGN_OR_FORMAL_RAW_REJECTED', rid, file=name)
            if rid in planned:
                unit = planned[rid]
                identity_fields = ('task_id', 'arm', 'repeat', 'model')
                if name == 'resource_samples.jsonl':
                    # ResourceSampler.add uses "processes" for its measured process
                    # list. Recorder merges {**common, **row}, so that list replaces
                    # the unit's process-mode boolean by design. The unchanged
                    # run_id/other common fields join this sample to its planned
                    # unit; task_runs independently checks the process-mode flag.
                    measured = row.get('processes')
                    a.require(isinstance(measured, list) and bool(measured) and
                              all(isinstance(v, dict) and integer(v.get('pid')) and v['pid'] > 0 and
                                  integer(v.get('rss')) and integer(v.get('fds')) and
                                  number(v.get('cpu_sec')) and v['cpu_sec'] >= 0
                                  for v in measured),
                              'RESOURCE_PROCESS_MEASUREMENT_SCHEMA_INVALID', rid, file=name)
                else:
                    identity_fields += ('processes',)
                a.require(all(row.get(k) == unit.get(k) for k in identity_fields),
                          'RAW_UNIT_IDENTITY_MISMATCH', rid, file=name)
                if 'source_sha256' in row:
                    a.require(row['source_sha256'] == registration.get('source_sha256'),
                              'RAW_BUSINESS_SOURCE_MISMATCH', rid, file=name)
                if 'model_evidence' in row:
                    a.require(row['model_evidence'] == 'real_provider', 'OFFLINE_FIXTURE_IN_REAL_RAW', rid, file=name)
                if 'effective_contract_sha256' in row:
                    a.require(row['effective_contract_sha256'] == unit.get('effective_contract_sha256'),
                              'RAW_OUTPUT_CONTRACT_MISMATCH', rid, file=name)
                if 'task_sha256' in row:
                    a.require(row['task_sha256'] == cases.get(unit.get('task_id'), {}).get('content_sha256'),
                              'RAW_TASK_CONTENT_IDENTITY_MISMATCH', rid, file=name)
        foreign_rejected[name] = sum(r.get('run_id') not in expected or r.get('phase') != PHASE for r in rows)
        # Preserve/hash the complete raw file and hard-fail any contamination, but
        # never put foreign/formal rows into public cost or quality calculations.
        data[name] = [r for r in rows if r.get('run_id') in expected and r.get('phase') == PHASE]
    tasks = a.unique(data['task_runs.jsonl'], 'run_id', 'DUPLICATE_TERMINAL_TASK')
    task_starts = a.unique(data['task_unit_starts.jsonl'], 'run_id', 'DUPLICATE_TASK_START')
    a.require(set(tasks) == expected and set(task_starts) == expected, 'PUBLIC_TASK_START_TERMINAL_SET_INCOMPLETE')
    starts = a.unique(data['provider_attempt_starts.jsonl'], 'request_id', 'DUPLICATE_ATTEMPT_START')
    calls = a.unique(data['provider_calls.jsonl'], 'request_id', 'DUPLICATE_ATTEMPT_END')
    preflight = a.unique(data['http_request_preflight.jsonl'], 'request_id', 'DUPLICATE_REQUEST_PREFLIGHT')
    a.require(set(starts) == set(calls), 'UNMATCHED_PROVIDER_START_END')
    a.require(set(preflight) <= set(calls), 'PREFLIGHT_WITHOUT_ATTEMPT')
    a.require(0 < len(starts) <= 432, 'PUBLIC_ATTEMPT_COUNT_OUT_OF_BOUNDS')
    for request_id in set(starts) & set(calls):
        first, call, body = starts[request_id], calls[request_id], preflight.get(request_id)
        rid = first.get('run_id')
        a.require(call.get('run_id') == rid and call.get('attempt') == first.get('attempt'),
                  'ATTEMPT_JOIN_IDENTITY_MISMATCH', rid, request_id=request_id)
        a.require(first.get('requested_model') == planned.get(rid, {}).get('model_id'),
                  'REQUEST_MODEL_MISMATCH', rid, request_id=request_id)
        a.require(call.get('status') in ('OK', 'ERROR') and bool(call.get('end_utc')),
                  'ATTEMPT_NOT_TERMINAL', rid, request_id=request_id)
        a.require(first.get('actual_prompt_sha256') == call.get('actual_prompt_sha256') and
                  sha256_string(call.get('actual_prompt_sha256')), 'PROMPT_HASH_START_END_MISMATCH', rid)
        if body is None:
            if call.get('status') == 'OK':
                a.find('FAIL', 'SUCCESSFUL_REQUEST_WITHOUT_HTTP_PREFLIGHT', rid, request_id=request_id)
            else:
                a.unknown('ERROR_ATTEMPT_HTTP_BOUNDARY_NOT_OBSERVED', rid, request_id=request_id)
        else:
            a.require(body.get('run_id') == rid and sha256_string(body.get('actual_http_body_sha256')) and
                      body.get('actual_http_body_sha256') == call.get('actual_http_body_sha256') and
                      integer(body.get('actual_http_body_bytes')) and
                      body.get('actual_http_body_bytes') == call.get('actual_request_utf8_bytes') and
                      0 < body['actual_http_body_bytes'] <= budget.get('max_actual_utf8_request_bytes', 0),
                      'ACTUAL_REQUEST_BODY_HASH_BYTES_JOIN_FAILED', rid, request_id=request_id)
        audits = first.get('fact_audit') or []
        inventory = sorted(cases.get(first.get('task_id'), {}).get('observations', {}))
        a.require(first.get('business_facts_equal') is True and bool(audits) and
                  all(x.get('source_facts_equal') is True and x.get('expected_fact_inventory') == inventory and
                      x.get('inline_observation_copies', 0) + x.get('resolved_observation_copies', 0) == 1 and
                      x.get('duplicate_task_fact_section_removed') is True and
                      x.get('instruction_contract_unchanged') is True for x in audits),
                  'BUSINESS_FACT_INVENTORY_OR_SINGLE_COPY_GUARD_FAILED', rid, request_id=request_id)
    indexed = {name: groups(rows) for name, rows in data.items()}
    all_complete = set(tasks) == expected and bool(tasks)
    for rid, task in tasks.items():
        if rid not in expected:
            continue
        before = len(a.findings)
        unit_calls = indexed['provider_calls.jsonl'][rid]
        attempts = sorted(c.get('attempt', -1) for c in unit_calls)
        a.require(len(unit_calls) <= 3 and attempts == list(range(1, len(unit_calls) + 1)) and
                  task.get('provider_attempts') == len(unit_calls), 'PER_TASK_ATTEMPT_SEQUENCE_INVALID', rid)
        a.require(task.get('status') in ('COMPLETED', 'FAILED', 'INCOMPLETE_BUDGET_STOP') and
                  isinstance(task.get('success'), bool) and
                  (task.get('status') == 'COMPLETED') == task.get('success'), 'TASK_TERMINAL_STATUS_INVALID', rid)
        complete, reported = audit_usage(a, rid, task, unit_calls)
        all_complete = all_complete and complete
        state = audit_state(a, rid, task, indexed['states.jsonl'][rid], indexed['spans.jsonl'][rid])
        audit_spans(a, rid, task, indexed['spans.jsonl'][rid])
        accounts = indexed['ipc_accounting.jsonl'][rid]
        if not accounts:
            a.unknown('IPC_ACCOUNTING_NOT_OBSERVED', rid)
        else:
            a.require(len(accounts) == 1, 'DUPLICATE_IPC_ACCOUNTING', rid)
            account = accounts[-1]
            endpoints = account.get('endpoints') or []
            socket_events = indexed['ipc_socket_events.jsonl'][rid]
            actual_by_socket = Counter()
            for event in socket_events:
                a.require(integer(event.get('successful_bytes')) and event.get('direction') in ('send', 'recv') and
                          event.get('contents') == 'NOT_RETAINED', 'SOCKET_EVENT_INVALID', rid)
                actual_by_socket[(event.get('socket'), event.get('direction'))] += event.get('successful_bytes', 0)
            endpoint_bytes = {(e.get('socket'), e.get('direction')): e.get('actual_bytes') for e in endpoints}
            socket_bytes = sum(actual_by_socket.values())
            a.require(account.get('framing_consumption_conserved') is True and
                      all(e.get('actual_bytes') == e.get('framing_consumed_bytes') for e in endpoints) and
                      len(endpoint_bytes) == len(endpoints) and dict(actual_by_socket) == endpoint_bytes and
                      sum(e.get('actual_bytes', 0) for e in endpoints) == account.get('observed_socket_bytes') == socket_bytes and
                      sum((account.get('by_boundary') or {}).values()) == socket_bytes and
                      account.get('successful_syscalls') == len(socket_events),
                      'IPC_FRAMING_OR_ENDPOINT_ACCOUNTING_NOT_CONSERVED', rid)
            if 'observable_ipc_bytes' in task:
                a.require(task['observable_ipc_bytes'] == socket_bytes,
                          'TASK_IPC_DIFFERS_FROM_SOCKET_EVENTS', rid)
        messages = indexed['messages.jsonl'][rid]
        for message in messages:
            a.require(integer(message.get('physical_payload_bytes')) and
                      message.get('framed_payload_bytes') == message['physical_payload_bytes'] + 4,
                      'LOGICAL_MESSAGE_FRAME_BYTES_INVALID', rid)
        index_rows = [r for r in indexed['memory_events.jsonl'][rid] if r.get('event') == 'index_operation']
        for index in index_rows:
            a.require(integer(index.get('start_ns')) and integer(index.get('end_ns')) and
                      index.get('index_latency_ns') == index['end_ns'] - index['start_ns'] >= 0 and
                      integer(index.get('cpu_ns')) and index.get('parent_memory_operation') in
                      ('retrieve', 'write', 'record_feedback', 'mark_used') and
                      'embedding, SQLite, candidate merging and reranking excluded' in index.get('boundary', ''),
                      'INDEX_ONLY_BOUNDARY_INVALID', rid)
            enclosing = [s for s in indexed['spans.jsonl'][rid] if s.get('category') in ('retrieval', 'memory') and
                         s.get('start_ns', index['end_ns'] + 1) <= index['start_ns'] <= index['end_ns'] <= s.get('end_ns', -1)]
            a.require(bool(enclosing), 'INDEX_ONLY_WITHOUT_ENCLOSING_MEMORY_SPAN', rid)
        if task.get('arm', '').endswith('1') and not index_rows:
            a.unknown('MEMORY_INDEX_OPERATION_NOT_OBSERVED', rid)
        for event in indexed['memory_events.jsonl'][rid]:
            if event.get('event') != 'retrieve':
                continue
            current = cases.get(task.get('task_id'), {})
            for candidate in event.get('candidates') or []:
                source = cases.get(candidate.get('source_task_id'), {})
                a.require(candidate.get('source_chain_id') == current.get('chain_id') and
                          source.get('chain_id') == current.get('chain_id') and
                          source.get('observations', {}).get('checkpoint', math.inf) <
                          current.get('observations', {}).get('checkpoint', -1) and
                          candidate.get('related_under_automatic_causal_rule') is True,
                          'PUBLIC_MEMORY_NOT_SAME_CHAIN_PRIOR', rid)
        for identity in indexed['observer_identity.jsonl'][rid]:
            spec = identity.get('specification') or {}
            a.require((spec.get('consumer_clone') or {}).get('stripped_ast_identical') is True and
                      (spec.get('mapping_probe') or {}).get('constructor_code_object_identical') is True and
                      identity.get('raw_rpc_body_recorded') is False,
                      'OBSERVER_NATIVE_IDENTITY_GUARD_FAILED', rid)
        if not indexed['observer_identity.jsonl'][rid]:
            a.unknown('OBSERVER_IDENTITY_NOT_OBSERVED', rid)
        summaries = indexed['observer_summary.jsonl'][rid]
        if not summaries:
            a.unknown('OBSERVER_CLEANUP_SUMMARY_NOT_OBSERVED', rid)
        a.require(all(s.get('all_hooks_restored') is True and
                      s.get('no_new_mapping_is_not_zero_mapping_latency') is True for s in summaries),
                  'OBSERVER_CLEANUP_OR_MAPPING_SEMANTICS_FAILED', rid)
        for field in ('workers_remaining', 'shm_names_created_but_remaining'):
            if field not in task:
                a.unknown('CLEANUP_NOT_OBSERVED', rid, field=field)
            else:
                a.require(task[field] == [], 'WORKER_OR_SHM_LEAK_RECORDED', rid, field=field)
        samples = indexed['resource_samples.jsonl'][rid]
        if not samples:
            a.unknown('RESOURCE_SAMPLES_NOT_OBSERVED', rid)
        else:
            for sample in samples:
                a.require(sample.get('sample_interval_sec') == .2 and
                          'not proportional set size' in sample.get('rss_semantics', ''),
                          'RESOURCE_SAMPLING_SEMANTICS_MISMATCH', rid)
            peak_rss = max(sum(x.get('rss', 0) for x in s.get('processes', [])) for s in samples)
            peak_fds = max(sum(x.get('fds', 0) for x in s.get('processes', [])) for s in samples)
            a.require(task.get('sampled_peak_process_group_rss') == peak_rss and
                      task.get('sampled_peak_process_group_fds') == peak_fds,
                      'SAMPLED_RESOURCE_PEAK_MISMATCH', rid)
        a.units.append({'run_id': rid, 'task_id': task.get('task_id'), 'arm': task.get('arm'),
                        'success': task.get('success'), 'task_status': task.get('status'),
                        'provider_attempts': len(unit_calls), 'provider_usage_complete': complete,
                        'reported_total_tokens_lower_bound': reported, **state,
                        'index_operations': len(index_rows), 'resource_samples': len(samples),
                        'audit_failures': sum(f['level'] == 'FAIL' for f in a.findings[before:]),
                        'unobserved_items': sum(f['level'] == 'UNKNOWN' for f in a.findings[before:])})
    a.stable()
    counts = Counter(f['level'] for f in a.findings)
    status = 'FAIL_RETAINED' if counts['FAIL'] else 'INCONCLUSIVE_RETAINED' if counts['UNKNOWN'] else 'PASS_OBSERVED_PUBLIC_METRICS'
    report = {'status': status, 'created_utc': datetime.now(timezone.utc).isoformat(),
              'analyzer_schema_version': SCHEMA_VERSION,
              'study_id': STUDY, 'phase': PHASE, 'planned_units': len(public),
              'terminal_units': len(set(tasks) & expected),
              'foreign_terminal_units_rejected': len(set(tasks) - expected),
              'successful_units': sum(t.get('success') is True for rid, t in tasks.items() if rid in expected),
              'provider_attempts': len(starts), 'provider_errors': sum(c.get('status') != 'OK' for c in calls.values()),
              'real_usage_complete_for_all_public_units': all_complete,
              'finding_counts': dict(counts), 'formal_units_analyzed': 0,
              'foreign_rows_rejected_by_file': foreign_rejected,
              'formal_freeze': False, 'quality_all_correct_required': False,
              'formal_ready_by_this_audit_alone': False,
              'scope': 'new registered public144 only; failed planned units retained; no effect estimates or overhead subtraction',
              'schema_corrections': [
                  {'file': 'resource_samples.jsonl', 'field': 'processes',
                   'native_schema': 'measured process resource list, not unit agent_processes boolean',
                   'attribution': 'unchanged run_id/task_id/arm/repeat/model join public plan; task_runs verifies process mode',
                   'prior_v1': '1328 RAW_UNIT_IDENTITY_MISMATCH false positives; original FAIL receipt and source preimage retained'},
              ],
              'limits': [
                  'Observed field-level and lifecycle joins do not prove all 115 semantic indicators.',
                  'Provider usage is provider-reported, not independently verified against billing.',
                  'Missing measurements remain UNKNOWN/null; unsupported measurements are not zero.',
                  'Native read/matmul/cast is fused; pure payload-read latency is not separately identified.',
                  'Index-only spans exclude embedding/SQLite/merging/reranking and overlap enclosing memory spans.',
                  'Shared buffer-close timing is one group; per-URI shared timings are never added.',
                  'RSS sums shared pages repeatedly; samples do not establish PSS or continuous peaks.',
                  'Public multiprocess coverage is only what this cohort actually observes; prior offline tests are separate.',
                  'No model I/O, RPC content, Authorization, credential file, or old/formal raw body is read.',
                  'Power, quality-safety and budget decisions remain separate preformal gates.',
              ],
              'analyzer_sha256': digest(Path(__file__)), 'evidence': a.evidence}
    destination.mkdir(parents=True)
    for filename, value in [('metric_audit.json', report), ('findings.json', a.findings)]:
        with (destination / filename).open('x', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
    with (destination / 'unit_checks.jsonl').open('x', encoding='utf-8') as f:
        for row in a.units:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps({k: report[k] for k in ('status', 'terminal_units', 'successful_units', 'provider_attempts', 'finding_counts')}))
    return 1 if counts['FAIL'] else 2 if counts['UNKNOWN'] else 0


if __name__ == '__main__':
    sys.exit(main())
