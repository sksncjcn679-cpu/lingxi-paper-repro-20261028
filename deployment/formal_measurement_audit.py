"""Independent measurement audit. Does not mutate driver files or raw evidence.

audit(out) reads recorder data and returns a JSON-compatible report. Model wrong
answers remain observations; measurement inconsistencies are errors and block
the calibration gate. Empty or unfinished calibration remains INCOMPLETE.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import math


ARMS = {'T0', 'S0', 'SS0', 'T1', 'S1', 'SS1'}
TABLES = ['task_unit_starts', 'task_runs', 'dry_task_runs', 'provider_attempt_starts',
          'provider_calls', 'model_io', 'dry_prompts', 'messages', 'states',
          'execution_events', 'memory_events', 'ipc_socket_events', 'ipc_accounting',
          'spans', 'resource_samples']


def sha(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(value).hexdigest()


def _root(path):
    for candidate in [path, *path.parents]:
        if (candidate / 'corpus/automatic_validated_v1/tasks.jsonl').exists():
            return candidate
    return path


def _load(path, errors):
    if not path.exists():
        return []
    rows = []
    with path.open(encoding='utf-8') as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                errors.append({'code': 'INVALID_JSONL', 'table': path.name, 'line': line_number})
    return rows


def _groups(rows, key='run_id'):
    groups = defaultdict(list)
    for row in rows:
        groups[row.get(key)].append(row)
    return groups


def _fact_view(prompt, expected):
    """Parse the actual saved provider prompt independently of the adapter."""
    if not isinstance(prompt, str) or not prompt.startswith('Inbound message:\n'):
        raise ValueError('native inbound prefix missing')
    wire, tail = prompt[len('Inbound message:\n'):].split('\n\nTask: ', 1)
    if wire.lstrip().startswith('{'):
        frame = json.loads(wire)
        parameters = frame.get('ip', frame.get('input_parameters', {}))
        representation = 'native_structured'
    else:
        parameters, end = json.JSONDecoder().raw_decode(wire.split(' | input=', 1)[1])
        if wire.split(' | input=', 1)[1][end:].strip():
            raise ValueError('unexpected lean input suffix')
        representation = 'native_lean_text'
    evidence = parameters.get('evidence', {})
    inline = evidence.get('observations') if isinstance(evidence, dict) else None
    resolved_marker = '\nResolved reference observations: '
    if inline is not None:
        observations = inline
        if resolved_marker in tail or '\nObservations: ' in tail:
            raise ValueError('duplicate inline/resolved business facts')
        source = 'inline'
    else:
        if not any(k in parameters for k in ('evidence_ref', 'facts_ref')):
            raise ValueError('facts absent and no actual reference')
        serialized = tail.split(resolved_marker, 1)[1]
        observations, end = json.JSONDecoder().raw_decode(serialized)
        if serialized[end:].strip() or '\nObservations: ' in tail:
            raise ValueError('invalid resolved reference suffix')
        source = 'resolved_reference'
    if observations != expected:
        raise ValueError('actual business observations differ from frozen corpus')
    instructions = tail.split('\nProcedure: ', 1)[0].split('\nAdopted procedure: ', 1)[0].split(resolved_marker, 1)[0]
    return {'representation': representation, 'source': source,
            'facts_sha256': sha(observations), 'instructions_sha256': sha(instructions)}


def _partition(spans, start, end):
    points = sorted({start, end, *[max(start, min(end, span[key]))
                    for span in spans for key in ('start_ns', 'end_ns')]})
    totals = defaultdict(float)
    for left, right in zip(points, points[1:]):
        covering = [s for s in spans if s['start_ns'] <= left and s['end_ns'] >= right]
        chosen = max(covering, key=lambda s: (s['depth'], s['span_id'])) if covering else None
        totals[chosen['category'] if chosen else 'runtime_and_recording_other'] += (right - left) / 1e9
    return dict(totals)


def _audit(out):
    out = Path(out)
    root = _root(out)
    errors, warnings = [], []
    raw = out / 'raw'
    data = {name: _load(raw / (name + '.jsonl'), errors) for name in TABLES}
    dry = bool(data['dry_task_runs']) and not data['task_runs']
    tasks = data['dry_task_runs'] if dry else data['task_runs']
    corpus_rows = _load(root / 'corpus/automatic_validated_v1/tasks.jsonl', errors)
    corpus = {r['task_id']: r for r in corpus_rows}
    by = {name: _groups(rows) for name, rows in data.items()}
    terminals = _groups(tasks)
    starts = _groups(data['provider_attempt_starts'], 'request_id')
    calls = _groups(data['provider_calls'], 'request_id')
    io = _groups(data['model_io'], 'request_id')
    observed_model_failures, representations = [], Counter()
    coverage = Counter()
    fact_groups = defaultdict(list)
    unit_reports = []

    def issue(code, run_id=None, **details):
        errors.append({'code': code, **({'run_id': run_id} if run_id else {}), **details})

    if not tasks:
        warnings.append({'code': 'NO_TERMINAL_UNITS_YET'})
    for run_id, rows in terminals.items():
        if len(rows) != 1:
            issue('DUPLICATE_TASK_TERMINAL', run_id, count=len(rows))
    for request_id in set(starts) | set(calls):
        if len(starts[request_id]) != 1 or len(calls[request_id]) != 1:
            issue('PROVIDER_ATTEMPT_NOT_ONE_START_ONE_END', request_id=request_id,
                  starts=len(starts[request_id]), ends=len(calls[request_id]))
        associated = (starts[request_id] or calls[request_id])[0].get('run_id')
        if len(terminals.get(associated, [])) != 1:
            issue('PROVIDER_ATTEMPT_WITHOUT_UNIQUE_TASK_TERMINAL', associated, request_id=request_id)
    if not dry:
        task_starts = by['task_unit_starts']
        for run_id in set(task_starts) | set(terminals):
            if len(task_starts[run_id]) != 1 or len(terminals[run_id]) != 1:
                issue('TASK_START_TERMINAL_NOT_ONE_TO_ONE', run_id,
                      starts=len(task_starts[run_id]), terminals=len(terminals[run_id]))
    for name, groups in by.items():
        if name in ('task_runs', 'dry_task_runs', 'task_unit_starts'):
            continue
        for run_id in groups:
            if run_id not in terminals:
                issue('ORPHAN_RECORDER_ROWS', run_id, table=name, rows=len(groups[run_id]))

    for row in tasks:
        run_id = row.get('run_id')
        coverage[(row.get('model_id'), row.get('topology'), row.get('arm'), row.get('domain'))] += 1
        if not row.get('success'):
            observed_model_failures.append({'run_id': run_id, 'task_id': row.get('task_id'),
                                            'arm': row.get('arm'), 'status': row.get('status')})
        for field in ('source_sha256', 'task_sha256', 'chain_id', 'domain', 'arm', 'model_id', 'topology'):
            if row.get(field) is None:
                issue('TASK_IDENTITY_FIELD_MISSING', run_id, field=field)
        fixture = corpus.get(row.get('task_id'))
        if fixture is None:
            issue('TASK_NOT_IN_FROZEN_CORPUS', run_id)
        elif fixture.get('content_sha256') != row.get('task_sha256'):
            issue('TASK_CORPUS_HASH_MISMATCH', run_id)
        task_calls = by['provider_calls'][run_id]
        if row.get('provider_attempts') != len(task_calls):
            issue('TASK_PROVIDER_ATTEMPT_COUNT', run_id, reported=row.get('provider_attempts'), observed=len(task_calls))
        if not dry and row.get('model_evidence') != 'real_provider':
            issue('REAL_TABLE_CONTAINS_NONREAL_EVIDENCE', run_id)
        if dry and (task_calls or row.get('provider_input_tokens') is not None):
            issue('DRY_TABLE_CLAIMS_PROVIDER_EVIDENCE', run_id)
        request_attempts = []
        usage_totals = Counter()
        all_usage = bool(task_calls)
        for call in task_calls:
            request_id = call.get('request_id')
            request_attempts.append(call.get('attempt'))
            if request_id != f'{run_id}-{call.get("attempt")}':
                issue('NONDETERMINISTIC_REQUEST_ID', run_id, request_id=request_id)
            if call.get('endpoint') != 'https://www.dmxapi.cn/v1/chat/completions':
                issue('PROVIDER_ENDPOINT_MISMATCH', run_id, request_id=request_id)
            if call.get('temperature') != 0 or call.get('max_tokens') != 1500 or call.get('timeout_sec') != 60:
                issue('PROVIDER_FROZEN_PARAMETER_MISMATCH', run_id, request_id=request_id)
            if call.get('requested_model') != row.get('model_id'):
                issue('PROVIDER_MODEL_ID_MISMATCH', run_id, request_id=request_id)
            req = [item for item in io[request_id] if item.get('direction') == 'request']
            res = [item for item in io[request_id] if item.get('direction') == 'response']
            if len(req) != 1:
                issue('REQUEST_IO_NOT_UNIQUE', run_id, request_id=request_id, count=len(req))
            else:
                actual_hash = sha(req[0].get('messages'))
                if actual_hash != call.get('actual_prompt_sha256') or actual_hash != call.get('prompt_sha256'):
                    issue('ACTUAL_REQUEST_HASH_MISMATCH', run_id, request_id=request_id)
                if len(starts.get(request_id, [])) == 1 and starts[request_id][0].get('actual_prompt_sha256') != actual_hash:
                    issue('ATTEMPT_START_REQUEST_HASH_MISMATCH', run_id, request_id=request_id)
                for message in req[0].get('messages', []):
                    if message.get('role') == 'user' and fixture:
                        try:
                            view = _fact_view(message.get('content'), fixture['observations'])
                            representations[view['representation']] += 1
                            fact_groups[(row['task_id'], row.get('repeat'), row.get('model_id'), row.get('topology'))].append({**view, 'arm': row['arm']})
                        except (ValueError, KeyError, TypeError, IndexError):
                            issue('ACTUAL_PROVIDER_FACT_EQUIVALENCE_FAILED', run_id, request_id=request_id)
            if call.get('status') == 'OK':
                if len(res) != 1:
                    issue('SUCCESS_RESPONSE_IO_NOT_UNIQUE', run_id, request_id=request_id)
                elif sha(res[0].get('response')) != call.get('response_sha256'):
                    issue('ACTUAL_RESPONSE_HASH_MISMATCH', run_id, request_id=request_id)
                usage = call.get('usage_raw')
                if not isinstance(usage, dict) or call.get('usage_source') != 'provider_reported':
                    issue('SUCCESS_PROVIDER_USAGE_MISSING', run_id, request_id=request_id)
                    all_usage = False
                else:
                    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                        if not isinstance(usage.get(key), int) or usage[key] < 0:
                            issue('PROVIDER_USAGE_INVALID', run_id, request_id=request_id, field=key)
                        else:
                            usage_totals[key] += usage[key]
                    if all(isinstance(usage.get(k), int) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')) and usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens']:
                        issue('PROVIDER_USAGE_NOT_CONSERVED', run_id, request_id=request_id)
                    if res and res[0].get('response', {}).get('usage') != usage:
                        issue('RESPONSE_AND_CALL_USAGE_DIFFER', run_id, request_id=request_id)
                    cached = (usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0)
                    if not isinstance(cached, int) or cached < 0 or cached > usage.get('prompt_tokens', 0):
                        issue('PROVIDER_CACHE_USAGE_INVALID', run_id, request_id=request_id)
                    else:
                        usage_totals['cached_tokens'] += cached
            else:
                all_usage = False
                if res:
                    issue('ERROR_ATTEMPT_HAS_SUCCESS_RESPONSE_IO', run_id, request_id=request_id)
                if call.get('usage_raw') is not None:
                    warnings.append({'code': 'ERROR_ATTEMPT_REPORTED_USAGE_REQUIRES_RECONCILIATION', 'run_id': run_id})
        if request_attempts and sorted(request_attempts) != list(range(1, len(request_attempts) + 1)):
            issue('PROVIDER_ATTEMPT_SEQUENCE', run_id)
        if len(task_calls) > 3:
            issue('MAXIMUM_THREE_ATTEMPTS_EXCEEDED', run_id)
        if not dry:
            if row.get('provider_usage_complete') != all_usage:
                issue('TASK_USAGE_COMPLETENESS_FLAG', run_id)
            for key, token_key in [('provider_input_tokens', 'prompt_tokens'), ('provider_output_tokens', 'completion_tokens'),
                                   ('provider_total_tokens', 'total_tokens'), ('provider_cached_input_tokens', 'cached_tokens')]:
                expected_value = usage_totals[token_key] if all_usage else None
                if row.get(key) != expected_value:
                    issue('TASK_PROVIDER_USAGE_SUM_MISMATCH', run_id, field=key)
        else:
            for prompt in by['dry_prompts'][run_id]:
                if fixture:
                    try:
                        view = _fact_view(prompt.get('prompt'), fixture['observations'])
                        representations[view['representation']] += 1
                        fact_groups[(row['task_id'], row.get('repeat'), row.get('model_id'), row.get('topology'))].append({**view, 'arm': row['arm']})
                    except (ValueError, KeyError, TypeError, IndexError):
                        issue('DRY_FACT_EQUIVALENCE_FAILED', run_id)

        accounting_rows = by['ipc_accounting'][run_id]
        accounting = row.get('ipc_accounting')
        if len(accounting_rows) != 1 or not accounting:
            issue('IPC_ACCOUNTING_NOT_UNIQUE', run_id)
        else:
            independent = Counter()
            for event in by['ipc_socket_events'][run_id]:
                if event.get('boundary') != 'Runtime endpoint observed once' or event.get('contents') != 'NOT_RETAINED':
                    issue('SOCKET_BOUNDARY_OR_SECRET_POLICY', run_id)
                size = event.get('successful_bytes')
                if not isinstance(size, int) or size < 0:
                    issue('SOCKET_EVENT_BYTES_INVALID', run_id)
                    continue
                independent[(event.get('socket'), event.get('direction'))] += size
            saved = accounting_rows[0]
            for field in ('observed_socket_bytes', 'by_boundary', 'successful_syscalls', 'framing_consumption_conserved', 'endpoints'):
                if saved.get(field) != accounting.get(field):
                    issue('IPC_TERMINAL_AND_FINAL_ACCOUNTING_DIFFER', run_id, field=field)
            endpoints = accounting.get('endpoints', [])
            endpoint_totals = Counter()
            categories = Counter()
            for endpoint in endpoints:
                key = (endpoint.get('socket'), endpoint.get('direction'))
                endpoint_totals[key] += endpoint.get('actual_bytes', 0)
                categories[endpoint.get('kind')] += endpoint.get('actual_bytes', 0)
                if endpoint.get('actual_bytes') != endpoint.get('framing_consumed_bytes'):
                    issue('SOCKET_FRAMING_CONSUMPTION_MISMATCH', run_id, direction=endpoint.get('direction'))
                if endpoint.get('kind') not in ('channel_uds', 'agent_host_rpc'):
                    issue('UNCLASSIFIED_IPC_ENDPOINT', run_id)
            if independent != endpoint_totals:
                issue('SOCKET_EVENT_ENDPOINT_BYTES_DIFFER', run_id)
            if sum(independent.values()) != row.get('observable_ipc_bytes') or sum(independent.values()) != accounting.get('observed_socket_bytes'):
                issue('TASK_SOCKET_TOTAL_NOT_CONSERVED', run_id)
            if dict(categories) != accounting.get('by_boundary') or not accounting.get('framing_consumption_conserved'):
                issue('SOCKET_CATEGORY_OR_CONSUMPTION_FLAG', run_id)
            if len(by['ipc_socket_events'][run_id]) != accounting.get('successful_syscalls'):
                issue('SOCKET_SUCCESSFUL_SYSCALL_COUNT', run_id)
            metrics = row.get('metrics') or {}
            channel_minimum = metrics.get('ipc_framed_bytes', 0) + metrics.get('ipc_ack_bytes', 0)
            if categories['channel_uds'] < channel_minimum:
                issue('CHANNEL_OBSERVATION_SMALLER_THAN_NATIVE_FRAMES_ACK', run_id)
            if row.get('processes') and not categories['agent_host_rpc']:
                issue('MULTIPROCESS_RPC_BOUNDARY_NOT_OBSERVED', run_id)

        spans = by['spans'][run_id]
        span_index = {s.get('span_id'): s for s in spans}
        if len(span_index) != len(spans):
            issue('DUPLICATE_SPAN_ID', run_id)
        begin, end = row.get('wall_start_monotonic_ns'), row.get('wall_end_monotonic_ns')
        if not isinstance(begin, int) or not isinstance(end, int) or end < begin:
            issue('MONOTONIC_WALL_BOUNDARIES_MISSING', run_id)
        else:
            if abs((end - begin) / 1e9 - row.get('wall_sec', -1)) > 1e-8:
                issue('WALL_DURATION_NOT_RECOMPUTABLE', run_id)
            for span in spans:
                if not (begin <= span.get('start_ns', -1) <= span.get('end_ns', -1) <= end):
                    issue('SPAN_OUTSIDE_UNIT_WALL', run_id, span_id=span.get('span_id'))
                if abs((span['end_ns'] - span['start_ns']) / 1e9 - span.get('inclusive_sec', -1)) > 1e-8:
                    issue('SPAN_DURATION_NOT_CONSERVED', run_id, span_id=span.get('span_id'))
                parent = span_index.get(span.get('parent_span_id'))
                if span.get('parent_span_id') is not None and (not parent or not parent['start_ns'] <= span['start_ns'] <= span['end_ns'] <= parent['end_ns']):
                    issue('SPAN_PARENT_INTERVAL_INVALID', run_id, span_id=span.get('span_id'))
            if spans:
                main_thread = Counter(s['thread'] for s in spans).most_common(1)[0][0]
                independent_partition = _partition([s for s in spans if s['thread'] == main_thread], begin, end)
                reported_partition = row.get('exclusive_spans_sec') or {}
                for category in set(independent_partition) | set(reported_partition):
                    if abs(independent_partition.get(category, 0) - reported_partition.get(category, 0)) > 1e-7:
                        issue('EXCLUSIVE_SPAN_PARTITION_DIFFER', run_id, category=category)
                if abs(sum(independent_partition.values()) - row['wall_sec']) > 1e-7:
                    issue('EXCLUSIVE_SPAN_SUM_EXCEEDS_OR_MISSES_WALL', run_id)
            wait = sum(s['inclusive_sec'] for s in spans if s.get('category') == 'provider_wait')
            if abs(wait - row.get('provider_wait_sec', -1)) > 1e-7:
                issue('PROVIDER_WAIT_SPAN_SUM', run_id)
            if len([s for s in spans if s.get('category') == 'provider_wait']) != len(task_calls):
                issue('PROVIDER_WAIT_SPAN_ATTEMPT_COUNT', run_id)

        state_on = row.get('state_exchange_enabled', row.get('state_applicable') and row.get('arm') in ('SS0', 'SS1'))
        if row.get('public_state_calibration_override') and row.get('phase') not in ('public_calibration', 'public_process_calibration'):
            issue('PUBLIC_STATE_OVERRIDE_APPLIED_OUTSIDE_PUBLIC_CALIBRATION', run_id)
        consumers = [e for e in by['execution_events'][run_id] if e.get('event') == 'formal.state_consumer']
        get_states = [s for s in by['states'][run_id] if s.get('event') == 'get_plan_state' and 'receiver_decision_order' in s]
        if state_on:
            if not row.get('state_consumed') or not consumers or not get_states:
                issue('STATE_ENABLED_WITHOUT_ACTUAL_CONSUMPTION', run_id)
            runtime_pid = spans[0].get('pid') if spans else None
            for consumer in consumers:
                payload = consumer.get('payload') or {}
                expected_pid = row.get('worker_pids', {}).get('retriever') if row.get('processes') else runtime_pid
                if payload.get('pid') != expected_pid:
                    issue('ACTUAL_STATE_CONSUMER_PID_MISMATCH', run_id)
                matching = [s for s in get_states if s.get('uri') == payload.get('state_uri')]
                if not matching or matching[-1].get('receiver_decision_order') != payload.get('ordered_operands'):
                    issue('ACTUAL_STATE_CONSUMER_COMPUTATION_ORDER_MISMATCH', run_id)
                if row.get('processes') and matching and matching[-1].get('pid') == payload.get('pid'):
                    issue('PROCESS_STATE_PATH_MISREPRESENTED_AS_DIRECT_CHILD_MAPPING', run_id)
        elif row.get('state_consumed') or get_states:
            issue('STATE_DISABLED_BUT_CONSUMED', run_id)
        if row.get('workers_remaining'):
            issue('WORKER_PROCESS_REMAINS', run_id, count=len(row['workers_remaining']))
        if row.get('shm_names_created_but_remaining'):
            issue('SHM_NAMES_REMAIN', run_id, count=len(row['shm_names_created_but_remaining']))
        before = set((row.get('fd_targets_before') or {}).values())
        after = set((row.get('fd_targets_after') or {}).values())
        if not row.get('fd_targets_before') or not row.get('fd_targets_after'):
            issue('FD_TARGET_IDENTITY_MISSING', run_id)
        unit_new = after - before
        unexpected_fd = [target for target in unit_new if not (target.startswith('pipe:[') or
                         (target.startswith('/proc/') and target.endswith('/statm')))]
        if unexpected_fd:
            issue('UNEXPECTED_UNIT_OWNED_FD_REMAINS', run_id, target_kinds=[t.split(':', 1)[0] for t in unexpected_fd])
        if unit_new:
            warnings.append({'code': 'PERSISTENT_HELPER_FD_INITIALIZATION', 'run_id': run_id,
                             'new_targets': sorted(unit_new),
                             'scope': 'router/process helpers and psutil cache; bounded growth audited separately; not claimed zero FDs'})
        if not by['resource_samples'][run_id]:
            issue('RESOURCE_SAMPLES_MISSING', run_id)
        if row.get('success') and not row.get('observed_fixture_check', {}).get('current_record_matches_check'):
            issue('SUCCESS_WITHOUT_ACTUAL_READ_ONLY_FIXTURE_CHECK', run_id)
        unit_reports.append({'run_id': run_id, 'task_id': row.get('task_id'), 'arm': row.get('arm'),
            'success': row.get('success'), 'provider_attempts': len(task_calls),
            'provider_usage_complete': row.get('provider_usage_complete'), 'state_enabled': bool(state_on),
            'actual_state_consumers': len(consumers), 'new_persistent_fd_targets': len(unit_new),
            'processes': bool(row.get('processes')), 'worker_roles': sorted(row.get('worker_pids', {}))})

    for key, views in fact_groups.items():
        if len({view['facts_sha256'] for view in views}) > 1 or len({view['instructions_sha256'] for view in views}) > 1:
            issue('CROSS_ARM_BUSINESS_FACT_OR_INSTRUCTION_MISMATCH', task_id=key[0])
    # Repeated fresh pipe initialization signals a leak; normal first router and
    # first SHM resource tracker account for a bounded number of helper pipes.
    fd_initializations = [w for w in warnings if w['code'] == 'PERSISTENT_HELPER_FD_INITIALIZATION']
    by_runtime = defaultdict(set)
    for warning in fd_initializations:
        run_id = warning['run_id']
        span_rows = by['spans'][run_id]
        pid = span_rows[0].get('pid') if span_rows else None
        by_runtime[pid].update(warning['new_targets'])
    for pid, targets in by_runtime.items():
        pipe_count = sum(t.startswith('pipe:[') for t in targets)
        if pipe_count > 5:
            issue('UNBOUNDED_UNIT_HELPER_FD_GROWTH', runtime_pid=pid, distinct_new_helper_pipes=pipe_count)
    files = {}
    for name in TABLES:
        path = raw / (name + '.jsonl')
        if path.exists():
            files[name] = {'rows': len(data[name]), 'sha256': sha(path.read_bytes())}
    return {'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'out': str(out), 'mode': 'deterministic_recorder_dry' if dry else 'real_provider',
        'status': 'FAIL' if errors else 'PASS' if tasks else 'INCOMPLETE',
        'measurement_errors': errors, 'warnings': warnings,
        'terminal_units': len(tasks), 'real_provider_attempts': len(data['provider_calls']),
        'model_quality_failures_retained': observed_model_failures,
        'native_representation_counts': dict(representations),
        'coverage': [{'model_id': k[0], 'topology': k[1], 'arm': k[2], 'domain': k[3], 'units': n}
                     for k, n in sorted(coverage.items(), key=lambda item: str(item[0]))],
        'unit_audits': unit_reports, 'file_integrity': files,
        'limits': ['socket observation covers declared Runtime endpoint only, excludes provider HTTP and kernel bookkeeping',
                   'state order audit matches separately recomputed decode monitor against actual consumer execution event',
                   'helper FDs and scoped memory SQLite handles may persist by design; no claim of absolute zero FD count',
                   'model wrong answers do not become measurement errors; all remain in quality denominator']}


def audit(out):
    """Invalid schemas fail closed and return a report instead of passing silently."""
    try:
        return _audit(out)
    except Exception as error:
        return {'schema_version': 1, 'status': 'FAIL', 'out': str(out), 'mode': 'UNREADABLE',
                'measurement_errors': [{'code': 'AUDIT_INPUT_SCHEMA_OR_EXECUTION_ERROR',
                                        'error_type': type(error).__name__,
                                        'error_text_sha256': sha(str(error))}],
                'warnings': [], 'unit_audits': [], 'terminal_units': 0,
                'real_provider_attempts': 0, 'native_representation_counts': {},
                'model_quality_failures_retained': []}


def calibration_gate(out, process_dry_out=None):
    report = audit(out)
    root = _root(Path(out))
    plan_path = root / 'manifests/formal_plan.json'
    plan = json.loads(plan_path.read_text()) if plan_path.exists() else []
    expected = {u['run_id'] for u in plan if u['phase'] == 'public_calibration'}
    real_units = {r['run_id'] for r in report['unit_audits']}
    reasons = []
    if report['status'] != 'PASS':
        reasons.append('REAL_PUBLIC_MEASUREMENT_AUDIT_NOT_PASS')
    if report['mode'] != 'real_provider':
        reasons.append('PUBLIC_GATE_REQUIRES_REAL_PROVIDER')
    if len(expected) != 144 or real_units != expected:
        reasons.append('PUBLIC_PLAN_144_UNITS_NOT_COMPLETELY_ACCOUNTED')
    if report['real_provider_attempts'] < 144:
        reasons.append('NOT_ALL_PUBLIC_UNITS_HAVE_REAL_PROVIDER_ATTEMPTS')
    if set(report['native_representation_counts']) != {'native_lean_text', 'native_structured'}:
        reasons.append('NATIVE_REPRESENTATION_CONTRAST_NOT_OBSERVED')
    if any(not r['provider_usage_complete'] for r in report['unit_audits']):
        reasons.append('PUBLIC_USAGE_INCOMPLETE')
    process_report = audit(process_dry_out) if process_dry_out else None
    if process_report is None:
        reasons.append('MULTIPROCESS_RECORDER_AUDIT_PENDING')
    else:
        if process_report['status'] != 'PASS':
            reasons.append('MULTIPROCESS_RECORDER_AUDIT_NOT_PASS')
        if not any(u.get('processes') and u['state_enabled'] and u['actual_state_consumers']
                   and set(u.get('worker_roles', [])) >= {'planner', 'retriever', 'executor', 'reviewer'}
                   for u in process_report['unit_audits']):
            reasons.append('MULTIPROCESS_STATE_CONSUMPTION_NOT_OBSERVED')
    return {'status': 'PASS' if not reasons else 'BLOCK_FREEZE', 'freeze_allowed': not reasons,
            'reasons': reasons, 'public': report, 'multiprocess': process_report,
            'public_model_quality_failures_retained': len(report['model_quality_failures_retained'])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='/evidence/formal_20261019')
    parser.add_argument('--output', required=True)
    parser.add_argument('--gate', action='store_true')
    parser.add_argument('--process-dry-out')
    args = parser.parse_args()
    report = calibration_gate(args.out, args.process_dry_out) if args.gate else audit(args.out)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'status': report['status'], 'output': str(target),
                      'measurement_errors': len(report.get('measurement_errors', [])),
                      'reasons': report.get('reasons', [])}))
    raise SystemExit(report['status'] not in ('PASS', 'INCOMPLETE'))


if __name__ == '__main__':
    main()
