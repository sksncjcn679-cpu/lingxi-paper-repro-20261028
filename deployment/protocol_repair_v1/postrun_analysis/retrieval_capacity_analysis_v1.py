"""Post-timeout capacity supplement; preparation source, never a query runner.

This implementation was added after the first observed 1200-second timeout.
It preserves the exact original e73 audit, including FAIL and every finding.
Only demonstrably terminated timeouts with a valid frozen-order query prefix
can reconcile structural absence. The original statistical method is extracted
from the exact prospectively bound source; no scientific source is rewritten.
Run only after the actual final controller receipt and a new root quiet receipt.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import shutil
import sys

STUDY = 'protocol_repair_20261020_v1'
ORIGINAL_VERSION = 'retrieval_postrun_analysis_v1'
ORIGINAL_ANALYZER_SHA = 'e73e5633cac1ba3428e0d2b3d145fcff4f94417ea0eb00fb72a29d4a06103524'
ORIGINAL_BINDING_SHA = 'ef70689318eeaecd7beed4bf6c5c84dc18df206e243fc430f461f07c48c102d3'
DRIVER_SHA = 'ec5fef7c508232d7c34671b8150c6a829ca26713eb5e2df92969a4b658c14921'
FREEZE_SHA = '2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e'
INCOMPLETE = 'INCOMPLETE_CAPACITY_RECONCILED_NOT_ORIGINAL_AUDIT_PASS'
COMPLETE = 'COMPLETE_ORIGINAL_RETRIEVAL_PASSTHROUGH'
TERMINATION_VERIFICATION = 'actual complete psutil container process enumeration after terminal controller'
EXTRACT_CONSTANTS = ('STUDY', 'SIZES', 'BUILDS', 'MODES', 'SEEDS', 'NOW', 'METRICS',
                     'DRIVER_SHA', 'PARENT_DRIVER_SHA', 'OLD_ANALYZER_SHA',
                     'BOOTSTRAPS', 'BOOTSTRAP_SEED')
EXTRACT_FUNCTIONS = ('finite', 'integer', 'native_input', 'independent_gold', 'scored', 'summarize')
PROJECTION_FIELDS = ('error', 'error_phase', 'latency_ns', 'cpu_ns', 'candidate_count', 'rss_bytes',
                     'native_retrieve_attempted', 'prequery_rss_check_ns', 'prequery_rss_bytes',
                     'first_query_of_mode', 'postings_cache_state')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path, limit=16 * 1024 * 1024):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit,
            'missing, indirect, or oversized JSON authority: ' + path.name)
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def read_lines(path, maximum, line_limit=256 * 1024):
    """Future postrun execution only: finite JSON objects, at most maximum rows."""
    require(path.is_file() and not path.is_symlink(), 'missing/indirect JSONL authority: ' + path.name)
    rows = []
    with path.open('rb') as stream:
        while True:
            line = stream.readline(line_limit + 1)
            if not line:
                break
            require(len(line) <= line_limit, 'oversized native/projection JSON line')
            require(line.strip(), 'blank native/projection line is not a frozen-driver row')
            require(len(rows) < maximum, 'native/projection row bound exceeded')
            def pairs(values):
                result = {}
                for key, value in values:
                    require(key not in result, 'duplicate JSONL key')
                    result[key] = value
                return result
            row = json.loads(line.decode('utf-8'), object_pairs_hook=pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSONL')))
            require(isinstance(row, dict), 'JSONL row is not an object')
            rows.append(row)
    return rows


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'naive lifecycle timestamp')
    return result


def closed_gate(out, quiet_argument):
    """Lifecycle metadata only, before reading/hashing any raw body."""
    execution = read_json(out / 'formal_execution_v1/execution.json')
    stages = execution.get('stages') or []
    require(execution.get('status') == 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT' and
            execution.get('finished_utc') and execution.get('supervised_child_observed_running') is not True and
            [s.get('name') for s in stages] == ['main', 'native_remaining', 'external'] and
            all(s.get('status') == 'TERMINAL' and s.get('exit_code') == 0 and s.get('finished_utc') for s in stages),
            'actual real timing is not demonstrably closed')
    quiet_path = (out / quiet_argument).resolve()
    require(quiet_path.is_relative_to(out / 'analysis'), 'root quiet receipt must be inside study analysis')
    quiet = read_json(quiet_path)
    require(quiet.get('study_id') == STUDY and
            quiet.get('status') == 'ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING' and
            quiet.get('active_real_timing_processes') == [] and
            quiet.get('active_retrieval_timing_processes') == [] and
            quiet.get('all_container_processes_except_init_sleep_and_probe') == [] and
            quiet.get('retrieval_controller_session_id') == 82436 and
            type(quiet.get('retrieval_controller_exec_observed_terminal_exit_code')) is int and
            quiet['retrieval_controller_exec_observed_terminal_exit_code'] == 0 and
            timestamp(quiet['verified_utc']) >= timestamp(execution['finished_utc']),
            'new actual post-controller full-process quiet receipt is required')
    processes = read_json(out / 'analysis/retrieval_matrix_process_status.json')
    expected = {(s, b) for s in (100, 1000, 5000, 20000) for b in (1, 2, 3)}
    require(isinstance(processes, list) and len(processes) == 12 and
            all(isinstance(p, dict) for p in processes), 'final twelve-worker process receipt required')
    by_key = {(p.get('size'), p.get('build')): p for p in processes}
    require(len(by_key) == 12 and set(by_key) == expected, 'final worker identities differ')
    timeouts = {}
    for key, process in by_key.items():
        local = out / f'analysis/retrieval_n{key[0]}_build{key[1]}_process.json'
        require(read_json(local) == process, 'aggregate and individual process receipts differ')
        if process.get('timed_out') is True:
            require(process.get('returncode') is None and type(process.get('wall_sec')) in (float, int) and
                    math.isfinite(process['wall_sec']) and process['wall_sec'] >= 1200,
                    'timeout must retain the actual frozen 1200-second controller evidence')
            timeouts[key] = process
        else:
            require(process.get('timed_out') is False and type(process.get('returncode')) is int and
                    process['returncode'] == 0, 'non-timeout process error cannot be capacity-reconciled')
    checks = quiet.get('timeout_worker_termination_checks')
    require(isinstance(checks, list) and all(isinstance(c, dict) for c in checks), 'timeout termination checks absent')
    check_by_key = {(c.get('size'), c.get('build')): c for c in checks}
    require(len(check_by_key) == len(checks) == len(timeouts) and set(check_by_key) == set(timeouts),
            'termination checks must cover exactly the actual timed-out workers')
    for key, check in check_by_key.items():
        process_path = out / f'analysis/retrieval_n{key[0]}_build{key[1]}_process.json'
        require(check.get('process_receipt_sha256') == digest(process_path) and
                check.get('native_worker_processes_observed_running') == [] and
                check.get('verification') == TERMINATION_VERIFICATION,
                'timeout native child termination was not actually observed/bound')
    return execution, quiet_path, quiet, by_key, timeouts


class Authority:
    def __init__(self, out):
        self.out, self.hashes, self.absent = out, {}, set()

    def path(self, relative):
        require(isinstance(relative, str) and relative and '\\' not in relative and
                not Path(relative).is_absolute() and '..' not in Path(relative).parts,
                'authority path must be relative without traversal')
        parts = Path(relative).parts
        require(not any(p.lower() in {'.env', 'model.env', 'auth.json', 'credentials.json', 'credentials'} or
                        p.lower().startswith('.env.') for p in parts), 'secret path is not an analysis input')
        path = self.out / relative
        require(path.resolve().is_relative_to(self.out) and not path.is_symlink(), 'indirect authority path')
        return path

    def track(self, relative, wanted=None):
        path = self.path(relative)
        require(relative not in self.absent, 'originally absent artifact appeared: ' + relative)
        require(path.is_file(), 'required authority missing: ' + relative)
        record = {'sha256': digest(path), 'bytes': path.stat().st_size}
        require(wanted is None or record == wanted, 'authority bytes differ: ' + relative)
        if relative in self.hashes:
            require(record == self.hashes[relative], 'authority changed while validating: ' + relative)
        self.hashes[relative] = record
        return path

    def json(self, relative):
        return read_json(self.track(relative))

    def expect_absent(self, relative):
        require(not self.path(relative).exists(), 'artifact claimed absent actually exists: ' + relative)
        self.absent.add(relative)

    def stable(self):
        for relative, record in list(self.hashes.items()):
            self.track(relative, record)
        for relative in self.absent:
            require(not self.path(relative).exists(), 'absent original artifact appeared during analysis: ' + relative)


def original_method(source):
    require(digest(source) == ORIGINAL_ANALYZER_SHA, 'original e73 analyzer source identity differs')
    text = source.read_text(encoding='utf-8')
    tree = ast.parse(text)
    selected = []
    bindings = {}
    names = set()
    for node in tree.body:
        name = node.name if isinstance(node, ast.FunctionDef) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name in EXTRACT_CONSTANTS or name in EXTRACT_FUNCTIONS:
            require(name not in names, 'duplicate original method definition')
            names.add(name); selected.append(node)
            bindings[name] = {'ast_sha256': hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest(),
                              'source_sha256': hashlib.sha256(ast.get_source_segment(text, node).encode()).hexdigest()}
    require(names == set(EXTRACT_CONSTANTS + EXTRACT_FUNCTIONS), 'original method extraction incomplete')
    namespace = {'math': math, 're': re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), 'exec'), namespace)
    require(namespace['STUDY'] == STUDY and namespace['DRIVER_SHA'] == DRIVER_SHA and
            namespace['BOOTSTRAPS'] == 10000 and namespace['BOOTSTRAP_SEED'] == 261019 and
            namespace['SIZES'] == (100, 1000, 5000, 20000) and namespace['BUILDS'] == (1, 2, 3) and
            namespace['MODES'] == ('semantic', 'keyword', 'tag', 'hybrid', 'hybrid_feedback') and
            namespace['SEEDS'] == (26102001, 26102002, 26102003), 'prospective method constants changed')
    return namespace, {'original_analyzer_sha256': ORIGINAL_ANALYZER_SHA, 'definitions': bindings,
                       'constants': {name: namespace[name] for name in EXTRACT_CONSTANTS},
                       'extraction': 'exact hash-bound AST definitions only; no original main/analyze/import execution',
                       'method_changed': False}


def projection(raw, query, gold, metrics):
    result = {k: raw[k] for k in ('pool_size', 'build', 'mode', 'query_id')}
    result.update({k: query[k] for k in ('source_task_id', 'chain_id', 'domain')})
    result.update(observation_recorded=True, status=raw['status'], answerable_from_independent_gold=bool(gold))
    for field in (*PROJECTION_FIELDS, *metrics):
        result[field] = raw.get(field)
    return result


def validate_raw(raw, query, independent_gold, mode, position, stem, method):
    require(raw.get('experiment_id') == f'{stem}-{mode}-{query["query_id"]}' and
            all(raw.get(k) == query[k] for k in ('source_task_id', 'chain_id', 'domain', 'query_class')),
            'native raw query/source identity differs')
    native = method['native_input'](query, mode)
    require(raw.get('query') == query['text'] and raw.get('input_keywords') == (native['keywords'] or []) and
            raw.get('input_tags') == (native['tags'] or []) and raw.get('answerable') == bool(independent_gold) and
            set(raw.get('gold_memory_ids', [])) == independent_gold, 'native input or gold differs')
    hits = raw.get('hits')
    require(isinstance(hits, list) and all(isinstance(h, dict) for h in hits), 'native hits schema differs')
    ids = [h.get('memory_id') for h in hits]
    require(len(ids) <= 10 and len(ids) == len(set(ids)), 'rank duplicate or retrieval limit differs')
    complete = raw.get('status') == 'COMPLETE' and raw.get('error') is None
    require(raw.get('status') in ('COMPLETE', 'FAILED') and (raw.get('error') is None) == complete,
            'native terminal status differs')
    wanted = method['scored'](ids, independent_gold) if complete else dict.fromkeys(method['METRICS'])
    require(all(raw.get(k) == wanted[k] for k in method['METRICS']), 'native metric differs from original definition')
    require(raw.get('first_query_of_mode') == (position == 0) and raw.get('postings_cache_state') == 'NOT_OBSERVED' and
            'cold_postings_after_build_or_feedback' not in raw, 'cache observation label differs')
    integer = method['integer']; attempted = raw.get('native_retrieve_attempted')
    require(integer(raw.get('prequery_rss_check_ns')) and raw.get('prequery_rss_soft_limit_mib') == 1400 and
            raw.get('latency_boundary') == 'native MemoryStore.retrieve only; RSS precheck and output scoring excluded',
            'native timing boundary differs')
    require((attempted is True and integer(raw.get('latency_ns')) and integer(raw.get('cpu_ns'))) or
            (attempted is False and raw.get('latency_ns') is None and raw.get('cpu_ns') is None and
             raw.get('error_phase') == 'prequery_rss_check' and not complete), 'native attempt/latency schema differs')
    require((complete and raw.get('error_phase') is None) or
            (not complete and raw.get('error_phase') == ('native_retrieve' if attempted else 'prequery_rss_check')),
            'native error boundary differs')
    require((complete and integer(raw.get('candidate_count'))) or
            (not complete and raw.get('candidate_count') is None), 'native failed candidates must remain null')


def validate_inputs(a, original, method):
    """Bind every original input/output byte; reuse e73's content audit unchanged."""
    original_dir = 'analysis/' + ORIGINAL_VERSION
    required_outputs = {'independent_gold.json', 'process_and_build_evidence.json', 'cell_coverage.csv', 'query_observations.jsonl'}
    outputs = original.get('output_sha256')
    require(isinstance(outputs, dict) and required_outputs <= outputs.keys(), 'original audit outputs incomplete')
    allowed_outputs = required_outputs | {'retrieval_quality_scale_by_build.csv', 'retrieval_quality_scale.csv'}
    require(set(outputs) == (required_outputs if original.get('findings') else allowed_outputs),
            'original FAIL/complete output set differs from the exact e73 write policy')
    if original.get('findings'):
        for filename in allowed_outputs - required_outputs:
            a.expect_absent(original_dir + '/' + filename)
    for filename, wanted in outputs.items():
        require(re.fullmatch(r'[0-9a-f]{64}', wanted) is not None and '/' not in filename and '\\' not in filename,
                'invalid original output binding')
        require(digest(a.track(original_dir + '/' + filename)) == wanted, 'original output changed')
    evidence = original.get('evidence')
    require(isinstance(evidence, dict), 'original input SHA evidence missing')
    for relative, record in evidence.items():
        require(relative.startswith(('formal_execution_v1/', 'analysis/', 'manifests/', 'corpus/', 'formal_snapshot_v1/')) or
                re.fullmatch(r'raw/retrieval_n(?:100|1000|5000|20000)_build[123](?:_pool|_feedback)?\.jsonl', relative),
                'original evidence attempts to admit non-retrieval raw')
        require(isinstance(record, dict) and set(record) == {'sha256', 'bytes'}, 'original input SHA record invalid')
        a.track(relative, record)
    binding_relative = f'manifests/{ORIGINAL_VERSION}_binding.json'
    require(digest(a.track(binding_relative)) == ORIGINAL_BINDING_SHA, 'original ef70 binding changed')
    binding = a.json(binding_relative)
    registration = a.json('corpus/retrieval_pool_inputs_v1/input_registration.json')
    required_evidence = {
        'formal_execution_v1/execution.json', 'analysis/retrieval_matrix_process_status.json', binding_relative,
        'corpus/retrieval_pool_inputs_v1/input_registration.json', 'manifests/formal-freeze.json',
        'analysis/retrieval_driver_preoutcome_correction_v1/correction_receipt.json',
        'corpus/candidate_v2/tasks.jsonl', 'corpus/candidate_v2/queries.jsonl',
        'formal_snapshot_v1/drivers/contract.py', 'corpus/retrieval_pool_inputs_v1/gold_memory_sets.json',
        'corpus/retrieval_pool_inputs_v1/native_input_inventory.json',
    }
    require(binding.get('status') == 'ANALYSIS_POLICY_BOUND_BEFORE_RETRIEVAL_OUTCOMES' and
            binding.get('study_id') == STUDY and binding.get('analysis_version') == ORIGINAL_VERSION and
            binding.get('analyzer_sha256') == ORIGINAL_ANALYZER_SHA and binding.get('driver_sha256') == DRIVER_SHA and
            binding.get('input_registration_sha256') == a.hashes['corpus/retrieval_pool_inputs_v1/input_registration.json']['sha256'] and
            binding.get('planned_units') == 14400 and binding.get('planned_cells') == 60 and
            binding.get('queries_per_build_cell') == 240 and binding.get('bootstrap_seed') == 261019 and
            binding.get('bootstrap_draws') == 10000 and binding.get('retrieval_rows_present') == 0,
            'original prospective retrieval binding differs')
    require(digest(a.track('manifests/formal-freeze.json')) == FREEZE_SHA, 'original formal freeze changed')
    freeze = a.json('manifests/formal-freeze.json'); marker = '/' + STUDY + '/'
    frozen = {name.replace('\\', '/').split(marker, 1)[1]: wanted for name, wanted in freeze['files'].items()
              if marker in name.replace('\\', '/')}
    require(frozen.get('formal_snapshot_v1/drivers/retrieval_driver.py') == DRIVER_SHA and
            digest(a.track('formal_snapshot_v1/drivers/retrieval_driver.py')) == DRIVER_SHA,
            'frozen retrieval driver differs')
    require(registration.get('driver_sha256') == method['PARENT_DRIVER_SHA'] and
            registration.get('queries') == 240 and registration.get('planned_query_units') == 14400,
            'prepared-input parent identity differs')
    for filename, wanted in registration['tasks_queries_sha256'].items():
        relative = 'corpus/candidate_v2/' + filename
        required_evidence.add(relative)
        require(frozen.get(relative) == wanted == digest(a.track(relative)), 'frozen source task/query differs')
    for relative in ('corpus/retrieval_pool_inputs_v1/input_registration.json',
                     'corpus/retrieval_pool_inputs_v1/native_input_inventory.json',
                     'corpus/retrieval_pool_inputs_v1/gold_memory_sets.json',
                     'analysis/retrieval_driver_preoutcome_correction_v1/correction_receipt.json',
                     'formal_snapshot_v1/drivers/contract.py'):
        require(frozen.get(relative) == digest(a.track(relative)), 'original frozen authority differs')
    for size in method['SIZES']:
        for build in method['BUILDS']:
            filename = f'pool_n{size}_build{build}.jsonl'
            relative = 'corpus/retrieval_pool_inputs_v1/' + filename
            required_evidence.add(relative)
            require(frozen.get(relative) == registration['pool_files'][filename]['sha256'] == digest(a.track(relative)),
                    'prepared frozen pool bytes differ')
    tasks = read_lines(a.track('corpus/candidate_v2/tasks.jsonl'), 144)
    query_rows = read_lines(a.track('corpus/candidate_v2/queries.jsonl'), 240)
    queries = {q['query_id']: q for q in query_rows}
    require(len(tasks) == 144 and len(queries) == len(query_rows) == 240, 'frozen source population differs')
    contract_tree = ast.parse(a.track('formal_snapshot_v1/drivers/contract.py').read_text(encoding='utf-8'))
    capabilities = ast.literal_eval(next(n.value for n in contract_tree.body if isinstance(n, ast.Assign) and
                                        any(isinstance(t, ast.Name) and t.id == 'CAPABILITIES' for t in n.targets)))
    gold = method['independent_gold'](tasks, query_rows, capabilities)
    saved_gold = a.json(original_dir + '/independent_gold.json')
    require(saved_gold == {k: sorted(v) for k, v in gold.items()} and
            saved_gold == a.json('corpus/retrieval_pool_inputs_v1/gold_memory_sets.json') and
            sum(bool(v) for v in gold.values()) == 160 and len(set().union(*gold.values())) == 100,
            'original independent/prepared gold differs from source facts')
    return registration, query_rows, queries, gold, required_evidence


def reconcile(a, original, method, processes, timeouts, registration, query_rows, queries, gold, required_evidence):
    sizes, builds, modes = (method[k] for k in ('SIZES', 'BUILDS', 'MODES'))
    expected = {(s, b, m, q) for s in sizes for b in builds for m in modes for q in queries}
    original_dir = 'analysis/' + ORIGINAL_VERSION
    observations = read_lines(a.track(original_dir + '/query_observations.jsonl'), 14400)
    require(len(observations) == 14400, 'original planned observation projection is incomplete')
    planned = sorted(expected)
    projected = {}
    actual = {}
    fields = (*PROJECTION_FIELDS, *method['METRICS'])
    for key, row in zip(planned, observations):
        q = queries[key[3]]
        require(tuple(row.get(k) for k in ('pool_size', 'build', 'mode', 'query_id')) == key and
                all(row.get(k) == q[k] for k in ('source_task_id', 'chain_id', 'domain')) and
                row.get('answerable_from_independent_gold') == bool(gold[key[3]]),
                'original planned identity/gold projection differs')
        require(type(row.get('observation_recorded')) is bool, 'observation-recorded flag is not explicit')
        if row['observation_recorded']:
            require(row.get('status') in ('COMPLETE', 'FAILED'), 'recorded query lacks native terminal status')
            actual[key] = row
        else:
            require(row.get('status') == 'NOT_RECORDED' and all(row.get(k) is None for k in fields),
                    'missing observation has fabricated values')
        projected[key] = row
    require(original.get('observed_query_units') == len(actual) and
            original.get('execution_failures') == sum(r['status'] != 'COMPLETE' for r in actual.values()),
            'original observation/failure total differs')
    finding_rows = original.get('findings')
    require(isinstance(finding_rows, list) and all(isinstance(f, dict) for f in finding_rows), 'original findings invalid')
    finding_set = Counter(canonical(f) for f in finding_rows)
    expected_findings = []
    def issue(code, **details):
        item = {'code': code, **details}
        if item not in expected_findings:
            expected_findings.append(item)
    def missing(relative):
        issue('INPUT_MISSING', file=relative)
        require(relative not in original['evidence'], 'missing artifact contradicts original input evidence')
        a.expect_absent(relative)
    worker_evidence = a.json(original_dir + '/process_and_build_evidence.json')
    require(worker_evidence.get('processes') == [processes[(s, b)] for s in sizes for b in builds],
            'saved original worker process evidence differs')
    pools = {(r['size'], r['build']): r for r in worker_evidence.get('pools', [])}
    require(len(pools) == len(worker_evidence.get('pools', [])) == 12 and set(pools) == set(processes),
            'saved pool evidence identities differ')
    require(isinstance(worker_evidence.get('builds'), list) and len(worker_evidence['builds']) == 12,
            'saved original build evidence incomplete')
    worker_checks = []
    all_raw_keys = set()
    for worker_index, (size, build) in enumerate((s, b) for s in sizes for b in builds):
        key = (size, build); stem = f'retrieval_n{size}_build{build}'; timed_out = key in timeouts
        for relative in (f'analysis/{stem}_process.json', f'analysis/{stem}_build.json',
                         f'analysis/{stem}_status.json', f'raw/{stem}.jsonl',
                         f'raw/{stem}_pool.jsonl', f'raw/{stem}_feedback.jsonl'):
            if a.path(relative).is_file():
                required_evidence.add(relative)
        raw_relative = f'raw/{stem}.jsonl'; raw_path = a.path(raw_relative)
        rows = read_lines(a.track(raw_relative), 1200) if raw_path.is_file() else []
        if not raw_path.is_file():
            require(timed_out, 'non-timeout worker has missing native query stream'); missing(raw_relative)
        order = list(query_rows); random.Random(method['SEEDS'][build - 1]).shuffle(order)
        frozen_order = [(mode, q['query_id']) for mode in modes for q in order]
        require([(r.get('mode'), r.get('query_id')) for r in rows] == frozen_order[:len(rows)],
                'native query stream is not an exact frozen-order complete-JSON prefix')
        selected = {k for k in actual if k[:2] == key}
        raw_keys = set()
        for index, row in enumerate(rows):
            unit = (row.get('pool_size'), row.get('build'), row.get('mode'), row.get('query_id'))
            require(unit in expected and unit[:2] == key and unit not in all_raw_keys,
                    'native query duplicate, wrong worker, or unexpected identity')
            all_raw_keys.add(unit); raw_keys.add(unit)
            query = queries[unit[3]]
            validate_raw(row, query, gold[unit[3]], unit[2], index % 240, stem, method)
            require(projection(row, query, gold[unit[3]], method['METRICS']) == projected[unit],
                    'original planned projection differs from native query bytes')
        require(raw_keys == selected, 'original projection and native worker identities differ')
        if timed_out:
            issue('RETRIEVAL_WORKER_FAILED_OR_TIMED_OUT', size=size, build=build)
        else:
            require(len(rows) == 1200, 'non-timeout worker is not complete')
        if len(rows) != 1200:
            require(timed_out, 'native prefix absence has no actual timeout')
            issue('REGISTERED_QUERY_OR_MODE_ORDER_CHANGED', size=size, build=build)
        build_relative = f'analysis/{stem}_build.json'; build_path = a.path(build_relative)
        built = a.json(build_relative) if build_path.is_file() else {}
        require(worker_evidence['builds'][worker_index] == built, 'original build evidence differs')
        if not build_path.is_file():
            require(timed_out and not rows, 'missing build cannot precede recorded query rows')
            missing(build_relative); issue('ACTUAL_POOL_BUILD_INCOMPLETE', size=size, build=build)
        else:
            require(built.get('count') == size and built.get('gold_prefilter') is False and
                    built.get('size') == size and built.get('build') == build and
                    built.get('seed') == method['SEEDS'][build - 1], 'actual build metadata anomaly')
        pool_relative = f'raw/{stem}_pool.jsonl'; pool_path = a.path(pool_relative)
        registered = registration['pool_files'][f'pool_n{size}_build{build}.jsonl']
        if pool_path.is_file():
            require(digest(a.track(pool_relative)) == registered['sha256'], 'actual copied pool bytes differ')
        else:
            require(timed_out and not built and not rows, 'missing copied pool has no prebuild timeout')
            missing(pool_relative)
        if not built:
            issue('ACTUAL_POOL_COPY_CHANGED', size=size, build=build)
        else:
            require(pool_path.is_file() and built.get('pool_sha256') == registered['sha256'], 'actual pool/build SHA differs')
        policy_count = sum(registered['conditions'].get(c, 0) for c in ('stale', 'wrong_conflict'))
        pool = pools[key]
        require(pool.get('records') == size and pool.get('source_memories') == 100 and
                pool.get('authored_feedback_records') == policy_count and
                pool.get('expected_feedback_events') == 2 * policy_count, 'original feedback population evidence differs')
        feedback_relative = f'raw/{stem}_feedback.jsonl'; feedback_path = a.path(feedback_relative)
        coverage_finding = {'code': 'FEEDBACK_AUTHORED_DISTRACTOR_COVERAGE_INCOMPLETE', 'size': size, 'build': build}
        feedback_incomplete = canonical(coverage_finding) in finding_set
        feedback_rows_used = sum(r['mode'] == 'hybrid_feedback' for r in rows)
        if not feedback_path.is_file():
            require(timed_out and not feedback_rows_used and len(rows) <= 960,
                    'missing feedback cannot support feedback query observations')
            missing(feedback_relative)
            if policy_count:
                issue('FEEDBACK_AUTHORED_DISTRACTOR_COVERAGE_INCOMPLETE', size=size, build=build)
            if size == 100:
                issue('POOL100_FEEDBACK_NOT_EMPTY', build=build)
        else:
            require(len(rows) >= 960, 'feedback file exists before its frozen mode boundary')
            a.track(feedback_relative)
            if size == 100:
                require(feedback_path.stat().st_size == 0, 'pool100 feedback is not empty')
            if feedback_incomplete:
                require(timed_out and policy_count > 0 and not feedback_rows_used and len(rows) == 960,
                        'incomplete feedback cannot be used by any feedback query')
                issue('FEEDBACK_AUTHORED_DISTRACTOR_COVERAGE_INCOMPLETE', size=size, build=build)
        status_relative = f'analysis/{stem}_status.json'; status_path = a.path(status_relative)
        if not status_path.is_file():
            require(timed_out, 'non-timeout worker lacks native terminal receipt')
            missing(status_relative)
            issue('WORKER_TERMINAL_COUNTS_CHANGED', size=size, build=build)
            issue('WORKER_FEEDBACK_EVENT_COUNT_MISMATCH', size=size, build=build)
        else:
            status = a.json(status_relative)
            require(len(rows) == 1200 and status.get('status') == 'COMPLETE' and status.get('rows') == 1200 and
                    status.get('failures') == sum(r['status'] != 'COMPLETE' for r in rows) and
                    status.get('feedback_events') == 2 * policy_count and status.get('real_provider_calls') == 0,
                    'native terminal metadata anomaly cannot be reconciled')
        worker_checks.append({'size': size, 'build': build, 'timed_out': timed_out,
                              'planned_query_units': 1200, 'observed_query_units': len(rows),
                              'missing_query_units': 1200 - len(rows),
                              'exact_frozen_order_prefix': True, 'native_terminal_receipt_present': status_path.is_file(),
                              'feedback_coverage_incomplete': feedback_incomplete or (not feedback_path.is_file() and policy_count > 0),
                              'feedback_query_observations': feedback_rows_used,
                              'feedback_content_validation': 'unchanged exact e73 audit, no content/identity exceptions'})
    require(all_raw_keys == set(actual), 'native/projection complete identity mismatch')
    require(set(original['evidence']) == required_evidence,
            'original input inventory differs from the exact e73 existing-artifact read scope')
    if set(actual) != expected:
        issue('FULL14400_MATRIX_INCOMPLETE'); issue('FIVE_METHOD_QUERY_PAIRING_INCOMPLETE')
    require(finding_set == Counter(canonical(f) for f in expected_findings),
            'original findings differ from the exact timeout-prefix/missing-terminal structural set; no bypass')
    require((bool(timeouts) and original.get('status') == 'FAIL_RETAINED_NO_INFERENTIAL_TABLES') or
            (not timeouts and not finding_rows and original.get('status') in
             ('PASS_COMPLETE_NEW_RETRIEVAL_MATRIX', 'COMPLETE_WITH_EXECUTION_FAILURES_AND_WITHHELD_METRICS')),
            'original audit status cannot be promoted/reconciled')
    cells = Counter(k[:3] for k in actual)
    coverage = [{'pool_size': s, 'build': b, 'mode': m, 'planned': 240, 'recorded': cells[(s, b, m)],
                 'failed': sum(r['status'] != 'COMPLETE' for k, r in actual.items() if k[:3] == (s, b, m)),
                 'missing': 240 - cells[(s, b, m)]} for s in sizes for b in builds for m in modes]
    with a.track(original_dir + '/cell_coverage.csv').open(encoding='utf-8', newline='') as stream:
        original_coverage = list(csv.DictReader(stream))
    require(original_coverage == [{k: str(v) for k, v in row.items()} for row in coverage],
            'original cell coverage differs from fixed planned identity set')
    paired = defaultdict(set)
    for size, build, mode, qid in actual:
        paired[(size, build, qid)].add(mode)
    require(original.get('paired_query_bundles') == len(paired), 'original pairing total differs')
    return expected, actual, coverage, worker_checks


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def write_csv(path, rows):
    require(rows, 'supplement must always retain planned summary rows')
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-out', type=Path, required=True)
    parser.add_argument('--quiescence-receipt', type=Path, required=True)
    parser.add_argument('--original-analyzer-source', type=Path,
                        default=Path(__file__).with_name('retrieval_postrun_analysis_v1.py'))
    parser.add_argument('--version', default='retrieval_capacity_analysis_v1')
    args = parser.parse_args(); out = args.study_out.resolve()
    require(out.name == STUDY and re.fullmatch(r'retrieval_capacity_analysis_v[1-9][0-9]*', args.version),
            'exact study root and fresh retrieval_capacity_analysis_vN required')
    destination = out / 'analysis' / args.version
    require(not destination.exists(), 'capacity supplement destination exists; never replace')
    execution, quiet_path, quiet, processes, timeouts = closed_gate(out, args.quiescence_receipt)
    a = Authority(out)
    a.track('formal_execution_v1/execution.json')
    a.track(quiet_path.relative_to(out).as_posix())
    a.track('analysis/retrieval_matrix_process_status.json')
    method, method_binding = original_method(args.original_analyzer_source.resolve())
    original_relative = 'analysis/' + ORIGINAL_VERSION + '/audit.json'
    original = a.json(original_relative)
    require(original.get('study_id') == STUDY and original.get('analyzer_sha256') == ORIGINAL_ANALYZER_SHA and
            original.get('planned_query_units') == 14400 and original.get('planned_cells') == 60 and
            original.get('queries_per_build_cell') == 240 and original.get('bootstrap_draws') == 10000 and
            original.get('bootstrap_seed') == 261019 and original.get('new_hypothesis_tests') == 0 and
            original.get('new_holm_families') == 0 and original.get('formal_outcome_streams_read') is False and
            original.get('raw_or_prepared_inputs_modified') is False and original.get('real_model_calls') == 0,
            'exact completed original analysis receipt is required')
    require(timestamp(original['created_utc']) >= timestamp(quiet['verified_utc']),
            'original e73 analysis was not executed after the actual final quiet observation')
    registration, query_rows, queries, gold, required_evidence = validate_inputs(a, original, method)
    expected, actual, coverage, worker_checks = reconcile(a, original, method, processes, timeouts,
                                                        registration, query_rows, queries, gold, required_evidence)
    summaries = []; pooled = []
    if timeouts:
        import numpy as np
        # Exact original loop order and exact extracted summarize; no alternate estimator.
        for size in method['SIZES']:
            for mode in method['MODES']:
                for build in method['BUILDS']:
                    keys = sorted(k for k in expected if k[:3] == (size, build, mode))
                    summaries.append(method['summarize'](np, keys, actual, queries, gold,
                                                        {'pool_size': size, 'mode': mode}, build))
                keys = sorted(k for k in expected if k[0] == size and k[2] == mode)
                pooled.append(method['summarize'](np, keys, actual, queries, gold, {'pool_size': size, 'mode': mode}))
        require(len(summaries) == 60 and len(pooled) == 20, 'planned output summary count changed')
    else:
        require(len(actual) == 14400, 'non-timeout passthrough requires complete original matrix')
        for filename, count in (('retrieval_quality_scale_by_build.csv', 60), ('retrieval_quality_scale.csv', 20)):
            path = a.track('analysis/' + ORIGINAL_VERSION + '/' + filename)
            require(filename in original['output_sha256'], 'original summary lacks original SHA binding')
            with path.open(encoding='utf-8', newline='') as stream:
                require(len(list(csv.DictReader(stream))) == count, 'original summary row count differs')
    a.stable()
    require(digest(args.original_analyzer_source.resolve()) == ORIGINAL_ANALYZER_SHA, 'original method source changed')
    report = {
        'status': INCOMPLETE if timeouts else COMPLETE, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'study_id': STUDY, 'analysis_version': args.version, 'capacity_helper_sha256': digest(Path(__file__).resolve()),
        'implementation_timing': 'added after first observed 1200-second timeout; not claimed prospectively bound',
        'original_statistical_policy_remains_prospectively_bound': True,
        'original_audit_pass_claimed': False if timeouts else original['status'] == 'PASS_COMPLETE_NEW_RETRIEVAL_MATRIX',
        'original_audit_status': original['status'], 'original_audit_sha256': a.hashes[original_relative]['sha256'],
        'original_audit_findings': original['findings'], 'noncapacity_findings': [],
        'capacity_reconciliation_validation': 'EXACT_TIMEOUT_PREFIX_STRUCTURAL_FINDINGS_ONLY' if timeouts else 'EXACT_ORIGINAL_COMPLETE_PASSTHROUGH',
        'original_binding_sha256': ORIGINAL_BINDING_SHA, 'original_analyzer_sha256': ORIGINAL_ANALYZER_SHA,
        'original_driver_sha256': DRIVER_SHA, 'formal_freeze_sha256': FREEZE_SHA,
        'planned_query_units': 14400, 'observed_query_units': len(actual), 'missing_query_units': 14400 - len(actual),
        'planned_cells': 60, 'summary_cells': 20, 'queries_per_build_cell': 240,
        'pooled_build_cells': 20, 'paired_query_bundles': original['paired_query_bundles'],
        'completed_query_units': sum(r['status'] == 'COMPLETE' for r in actual.values()),
        'planned_matrix_execution_complete': not timeouts and len(actual) == 14400,
        'execution_failures': sum(r['status'] != 'COMPLETE' for r in actual.values()),
        'timeout_worker_count': len(timeouts), 'timeout_workers': [timeouts[k] for k in sorted(timeouts)],
        'worker_capacity_validation': worker_checks, 'method_ast_binding': method_binding,
        'bootstrap_draws': 10000, 'bootstrap_seed': 261019, 'new_hypothesis_tests': 0, 'new_holm_families': 0,
        'real_model_calls': 0, 'formal_outcome_streams_read': False, 'raw_or_prepared_inputs_modified': False,
        'query_observations_policy': 'exact original planned projection byte copy; NOT_RECORDED stays absent from actual; no raw repair or backfill',
        'original_input_sha256': original['evidence'], 'original_output_sha256': original['output_sha256'],
        'evidence': a.hashes,
        'missing_original_artifacts': sorted(a.absent),
        'quiescence_receipt_relative': quiet_path.relative_to(out).as_posix(),
        'quiescence_receipt_sha256': a.hashes[quiet_path.relative_to(out).as_posix()]['sha256'],
        'limits': original['limits'] + [
            'Original e73 integrity audit status/findings are preserved, including FAIL; this supplement never makes it PASS.',
            'Only timeout-caused exact ordered native prefixes and missing phase/terminal artifacts can be reconciled.',
            'Affected original quality/cost estimates and intervals remain null under the exact bound summarize method.',
            'Complete cells retain their original planned statistics; no success-only denominator, extrapolation or zero fill.',
            'Whole-matrix execution remains incomplete whenever any worker timed out, even if all query rows happened to serialize.',
        ],
    }
    # No output directory exists until all validation/statistics/stability checks pass.
    destination.mkdir(parents=True)
    if timeouts:
        write_csv(destination / 'retrieval_quality_scale_by_build.csv', summaries)
        write_csv(destination / 'retrieval_quality_scale.csv', pooled)
    else:
        for filename in ('retrieval_quality_scale_by_build.csv', 'retrieval_quality_scale.csv'):
            shutil.copyfile(a.path('analysis/' + ORIGINAL_VERSION + '/' + filename), destination / filename)
    for filename in ('cell_coverage.csv', 'query_observations.jsonl', 'independent_gold.json', 'process_and_build_evidence.json'):
        shutil.copyfile(a.path('analysis/' + ORIGINAL_VERSION + '/' + filename), destination / filename)
    shutil.copyfile(a.path(original_relative), destination / 'original_audit.json')
    for filename in ('cell_coverage.csv', 'query_observations.jsonl', 'independent_gold.json', 'process_and_build_evidence.json'):
        require(digest(destination / filename) == original['output_sha256'][filename], 'original output byte copy differs')
    require(digest(destination / 'original_audit.json') == report['original_audit_sha256'], 'original FAIL receipt copy differs')
    a.stable()
    require(digest(args.original_analyzer_source.resolve()) == ORIGINAL_ANALYZER_SHA,
            'original method source changed before completed supplement receipt')
    report['output_sha256'] = {p.name: digest(p) for p in destination.iterdir() if p.is_file()}
    write_json(destination / 'audit.json', report)
    print(json.dumps({k: report[k] for k in ('status', 'observed_query_units', 'missing_query_units', 'timeout_worker_count')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
