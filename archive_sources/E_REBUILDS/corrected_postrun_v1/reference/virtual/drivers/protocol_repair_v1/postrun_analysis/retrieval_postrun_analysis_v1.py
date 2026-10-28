"""New-study retrieval audit/analysis; no query, runtime or provider execution.

Bind before retrieval outcomes, then analyze only after real timing and all
retrieval workers close. Formal task/provider outcome streams are never read.
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
import sys

STUDY = 'protocol_repair_20261020_v1'
SIZES = (100, 1000, 5000, 20000)
BUILDS = (1, 2, 3)
MODES = ('semantic', 'keyword', 'tag', 'hybrid', 'hybrid_feedback')
SEEDS = (26102001, 26102002, 26102003)
NOW = 1792458000.0
METRICS = ('p_at_1', 'recall_at_3', 'recall_at_10', 'mrr', 'ndcg_at_10', 'false_retrieval', 'refusal')
DRIVER_SHA = 'ec5fef7c508232d7c34671b8150c6a829ca26713eb5e2df92969a4b658c14921'
PARENT_DRIVER_SHA = '1b7c50ee7a1fc7a90e5c2962a23bd12d0fe941d01262f53f922482164c91175a'
OLD_ANALYZER_SHA = 'aecd4220c2037537ada77db17eabfcf86cfa4cd7d381ae80aebc46e40422a87c'
BOOTSTRAPS = 10000
BOOTSTRAP_SEED = 261019  # Exact retained retrieval-analysis seed, not provider sampling.


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def write_csv(path, rows):
    if not rows:
        return
    with path.open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class Inputs:
    def __init__(self, out):
        self.out, self.hashes, self.findings = out, {}, []

    def issue(self, code, **details):
        self.findings.append({'code': code, **details})

    def check(self, condition, code, **details):
        if not condition:
            self.issue(code, **details)
        return bool(condition)

    def track(self, relative):
        path = self.out / relative
        if not path.is_file():
            self.issue('INPUT_MISSING', file=relative)
            return None
        self.hashes[relative] = {'sha256': sha(path), 'bytes': path.stat().st_size}
        return path

    def json(self, relative):
        path = self.track(relative)
        return json.loads(path.read_text(encoding='utf-8')) if path else {}

    def lines(self, relative):
        path = self.track(relative)
        if not path:
            return
        with path.open(encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                if line.strip():
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError('object required')
                        yield row
                    except (ValueError, UnicodeError) as exc:
                        self.issue('RAW_JSON_INVALID', file=relative, line=line_no,
                                   error_type=type(exc).__name__)

    def stable(self):
        for relative, wanted in self.hashes.items():
            path = self.out / relative
            self.check(path.is_file() and sha(path) == wanted['sha256'],
                       'INPUT_CHANGED_DURING_ANALYSIS', file=relative)


def native_input(query, mode):
    tokens = sorted(set(re.findall(r'[a-zA-Z][a-zA-Z0-9_]+', query['text'].lower())))
    return {'query': query['text'] if mode in ('semantic', 'hybrid', 'hybrid_feedback') else None,
            'keywords': tokens if mode in ('keyword', 'hybrid', 'hybrid_feedback') else None,
            'tags': [query['domain']] if mode in ('tag', 'hybrid', 'hybrid_feedback') else None,
            'top_k': 10, 'now': NOW, 'memory_type': None, 'include_low_quality': False,
            'include_quarantined': False, 'current_task_id': None,
            'bm25_rerank': mode in ('hybrid', 'hybrid_feedback')}


def independent_gold(tasks, queries, capabilities):
    """Rebuild from all formal source facts and compatibility, never saved gold IDs."""
    formal = [t for t in tasks if t['split'] == 'formal']
    gold = {}
    for q in queries:
        gold[q['query_id']] = {
            'source:' + t['task_id'] for t in formal
            if t['chain_id'] == q['chain_id'] and
            t['observations']['checkpoint'] < q['checkpoint'] and
            set(q['required_capabilities']) <= set(capabilities[t['domain']])}
    return gold


def scored(ids, gold):
    # Same retained retrieval metric definitions. Called only for completed queries.
    if not gold:
        return {'false_retrieval': bool(ids), 'refusal': not bool(ids),
                'p_at_1': None, 'recall_at_3': None, 'recall_at_10': None,
                'mrr': None, 'ndcg_at_10': None}
    ranks = [i + 1 for i, value in enumerate(ids) if value in gold]
    dcg = sum(1 / math.log2(rank + 1) for rank in ranks)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(10, len(gold))))
    return {'p_at_1': float(bool(ids) and ids[0] in gold),
            'recall_at_3': sum(x in gold for x in ids[:3]) / len(gold),
            'recall_at_10': sum(x in gold for x in ids[:10]) / len(gold),
            'mrr': 1 / min(ranks) if ranks else 0.0, 'ndcg_at_10': dcg / ideal,
            'false_retrieval': None, 'refusal': None}


def bind(args, out, binding_path):
    if binding_path.exists():
        raise RuntimeError('analysis binding exists; never replace')
    if any((out / 'raw').glob('retrieval_n*')) or any((out / 'analysis').glob('retrieval_n*')):
        raise RuntimeError('retrieval outcomes already exist; do not claim prospective binding')
    registration_path = out / 'corpus/retrieval_pool_inputs_v1/input_registration.json'
    registration = json.loads(registration_path.read_text(encoding='utf-8'))
    driver = args.driver_source.resolve()
    if sha(driver) != DRIVER_SHA or registration['driver_sha256'] != PARENT_DRIVER_SHA:
        raise RuntimeError('unexpected corrected driver or prepared-input parent')
    value = {'status': 'ANALYSIS_POLICY_BOUND_BEFORE_RETRIEVAL_OUTCOMES', 'created_utc': now(),
             'study_id': STUDY, 'analysis_version': args.version,
             'analyzer_sha256': sha(Path(__file__)), 'driver_sha256': DRIVER_SHA,
             'input_registration_sha256': sha(registration_path),
             'planned_cells': 60, 'queries_per_build_cell': 240, 'planned_units': 14400,
             'independent_gold': 'formal source facts + same chain/prior checkpoint/capability compatibility',
             'bootstrap_draws': BOOTSTRAPS, 'bootstrap_seed': BOOTSTRAP_SEED,
             'method': 'retained domain-stratified whole-chain cluster bootstrap; all task queries/build repeats stay in chain',
             'retained_retrieval_analyzer_sha256': OLD_ANALYZER_SHA,
             'primary_missing_policy': 'full planned denominator retained; unknown quality/cost prevents primary point estimate and CI; no success-only substitution',
             'method_comparison': 'paired identity coverage only; no new retrieval hypothesis tests/p values/Holm family',
             'formal_outcome_streams_read': False, 'retrieval_rows_present': 0,
             'real_model_calls': 0}
    write_json(binding_path, value)
    print(json.dumps({'status': value['status'], 'binding': str(binding_path)}))


def summarize(np, expected, actual, queries, gold, metadata, build=None):
    """Same original ratio/quantile chain bootstrap, with explicit missing gate."""
    chains = sorted({(q['domain'], q['chain_id']) for q in queries.values()})
    index = {ch: i for i, ch in enumerate(chains)}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = []
    for domain in sorted({d for d, _ in chains}):
        indices = np.array([i for i, (d, _) in enumerate(chains) if d == domain])
        draws.append(rng.choice(indices, size=(BOOTSTRAPS, len(indices)), replace=True))
    picks = np.concatenate(draws, axis=1)
    rows = [actual[k] for k in expected if k in actual]
    failures = sum(r['status'] != 'COMPLETE' for r in rows)
    missing = len(expected) - len(rows)
    row = {**metadata, 'build': build if build is not None else 'ALL3',
           'planned_query_units': len(expected), 'recorded_query_units': len(rows),
           'missing_query_units': missing, 'execution_failures': failures,
           'completed_query_units': len(rows) - failures,
           'completion_denominator': len(expected), 'distinct_queries_planned': 240,
           'independent_source_tasks': 120, 'independent_chains': len(chains),
           'failure_rate_of_planned': failures / len(expected),
           'missing_rate_of_planned': missing / len(expected),
           'cost_inference_status': 'COMPLETE' if not failures and not missing else 'WITHHELD_FAILED_OR_MISSING_PLANNED_UNIT'}
    for metric in METRICS:
        relevant = [k for k in expected if bool(gold[k[3]]) == (metric not in ('false_retrieval', 'refusal'))]
        values = [(k, actual[k].get(metric)) for k in relevant if k in actual and
                  actual[k]['status'] == 'COMPLETE' and actual[k].get(metric) is not None]
        complete = len(values) == len(relevant) and all(finite(v) or isinstance(v, bool) for _, v in values)
        row.update({metric: None, metric + '_ci95_low': None, metric + '_ci95_high': None,
                    metric + '_denominator': len(relevant), metric + '_observed_values': len(values),
                    metric + '_unknown_values': len(relevant) - len(values)})
        if complete and relevant:
            sums = np.zeros(len(chains)); counts = np.zeros(len(chains))
            for key, value in values:
                q = queries[key[3]]; i = index[(q['domain'], q['chain_id'])]
                sums[i] += value; counts[i] += 1
            denominator = counts[picks].sum(axis=1)
            if np.all(denominator > 0):
                replicates = sums[picks].sum(axis=1) / denominator
                row[metric] = float(sum(v for _, v in values) / len(relevant))
                row[metric + '_ci95_low'], row[metric + '_ci95_high'] = map(float, np.quantile(replicates, [.025, .975]))
    valid_cost = not failures and not missing and all(finite(r.get('latency_ns')) and
                     finite(r.get('candidate_count')) for r in rows)
    row.update(latency_p50_ms=None, latency_p95_ms=None, latency_p50_ci95_low_ms=None,
               latency_p50_ci95_high_ms=None, latency_p95_ci95_low_ms=None,
               latency_p95_ci95_high_ms=None, mean_candidates=None)
    if valid_cost:
        latencies = [r['latency_ns'] / 1e6 for r in rows]
        row['latency_p50_ms'], row['latency_p95_ms'] = map(float, np.quantile(latencies, [.5, .95]))
        row['mean_candidates'] = float(np.mean([r['candidate_count'] for r in rows]))
        clusters = [[r['latency_ns'] / 1e6 for r in rows if
                     (r['domain'], r['chain_id']) == chain] for chain in chains]
        if len({len(v) for v in clusters}) == 1:
            sampled = np.asarray(clusters)[picks].reshape(BOOTSTRAPS, -1)
            for quantile, label in ((.5, 'p50'), (.95, 'p95')):
                replicates = np.quantile(sampled, quantile, axis=1)
                row['latency_' + label + '_ci95_low_ms'], row['latency_' + label + '_ci95_high_ms'] = map(float, np.quantile(replicates, [.025, .975]))
            del sampled
    rss = [r['rss_bytes'] for r in rows if finite(r.get('rss_bytes'))]
    row['observed_peak_query_rss_mib'] = max(rss) / 1048576 if rss else None
    precheck = [r['prequery_rss_check_ns'] / 1e6 for r in rows if finite(r.get('prequery_rss_check_ns'))]
    row['rss_precheck_observed_values'] = len(precheck)
    row['rss_precheck_p50_ms'] = float(np.quantile(precheck, .5)) if precheck else None
    row['rss_precheck_p95_ms'] = float(np.quantile(precheck, .95)) if precheck else None
    return row


def analyze(args, out, binding_path):
    destination = out / 'analysis' / args.version
    if destination.exists():
        raise RuntimeError('analysis destination exists; choose a new version and binding')
    # Lifecycle metadata only: never open task_runs/provider streams of formal work.
    execution = json.loads((out / 'formal_execution_v1/execution.json').read_text(encoding='utf-8'))
    if not execution.get('finished_utc') or execution.get('supervised_child_observed_running') is True or execution.get('status') not in (
            'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT', 'ERROR_RETAINED_NO_AUTOMATIC_REPLAY'):
        raise RuntimeError('real timing is not demonstrably closed by controller receipt')
    if not (out / 'analysis/retrieval_matrix_process_status.json').is_file():
        raise RuntimeError('retrieval controller has no final process receipt; do not analyze a running matrix')
    a = Inputs(out)
    a.track('formal_execution_v1/execution.json')
    aggregate_process = a.json('analysis/retrieval_matrix_process_status.json')
    a.check(isinstance(aggregate_process, list) and len(aggregate_process) == 12,
            'FINAL_RETRIEVAL_PROCESS_RECEIPT_NOT_12')
    aggregate_by_key = {}
    for process in aggregate_process if isinstance(aggregate_process, list) else []:
        key = (process.get('size'), process.get('build')) if isinstance(process, dict) else None
        if not a.check(key in {(s, b) for s in SIZES for b in BUILDS} and key not in aggregate_by_key,
                       'AGGREGATE_PROCESS_IDENTITY_DUPLICATE_OR_UNEXPECTED', key=key):
            continue
        aggregate_by_key[key] = process
    a.check(set(aggregate_by_key) == {(s, b) for s in SIZES for b in BUILDS},
            'AGGREGATE_PROCESS_IDENTITIES_NOT_ALL12')
    binding = a.json(str(binding_path.relative_to(out)))
    registration = a.json('corpus/retrieval_pool_inputs_v1/input_registration.json')
    a.check(binding.get('study_id') == STUDY and binding.get('analysis_version') == args.version and
            binding.get('analyzer_sha256') == sha(Path(__file__)) and binding.get('driver_sha256') == DRIVER_SHA and
            binding.get('input_registration_sha256') == a.hashes['corpus/retrieval_pool_inputs_v1/input_registration.json']['sha256'],
            'ANALYSIS_BINDING_CHANGED')
    a.check(registration.get('driver_sha256') == PARENT_DRIVER_SHA and registration.get('queries') == 240 and
            registration.get('planned_query_units') == 14400, 'PREPARED_INPUT_PARENT_CHANGED')
    for filename, wanted in registration.get('tasks_queries_sha256', {}).items():
        relative = 'corpus/candidate_v2/' + filename
        path = a.track(relative)
        a.check(path is not None and a.hashes[relative]['sha256'] == wanted,
                'REGISTERED_TASK_QUERY_BYTES_CHANGED', file=relative)
    freeze = a.json('manifests/formal-freeze.json')
    marker = '/' + STUDY + '/'
    frozen_relative = {name.replace('\\', '/').split(marker, 1)[1]: wanted
                       for name, wanted in freeze.get('files', {}).items()
                       if marker in name.replace('\\', '/')}
    a.check(frozen_relative.get('formal_snapshot_v1/drivers/retrieval_driver.py') == DRIVER_SHA,
            'CORRECTED_RETRIEVAL_DRIVER_NOT_FORMALLY_BOUND')
    correction = a.json('analysis/retrieval_driver_preoutcome_correction_v1/correction_receipt.json')
    a.check(correction.get('new_driver_sha256') == DRIVER_SHA and correction.get('old_driver_sha256') == PARENT_DRIVER_SHA,
            'MEASUREMENT_CORRECTION_IDENTITY_CHANGED')
    tasks = list(a.lines('corpus/candidate_v2/tasks.jsonl'))
    query_rows = list(a.lines('corpus/candidate_v2/queries.jsonl'))
    queries = {q['query_id']: q for q in query_rows}; by_task = {t['task_id']: t for t in tasks}
    formal_tasks = [t for t in tasks if t.get('split') == 'formal']
    chain_sizes = Counter((t['domain'], t['chain_id']) for t in formal_tasks)
    a.check(len(tasks) == len(by_task) == 144 and len(formal_tasks) == 120 and
            len(chain_sizes) == 20 and set(chain_sizes.values()) == {6} and
            len({q['source_task_id'] for q in query_rows}) == 120,
            'NEW_INPUT_TASK_CHAIN_POPULATION_CHANGED')
    a.check(len(queries) == len(query_rows) == 240 and len({q['text'] for q in query_rows}) == 240,
            'QUERY240_ID_OR_TEXT_NOT_UNIQUE')
    a.check(Counter(q['domain'] for q in query_rows) == {'service': 60, 'migration': 60, 'resources': 60, 'network_permissions': 60},
            'QUERY_DOMAIN_ALLOCATION_CHANGED')
    contract_path = a.track('formal_snapshot_v1/drivers/contract.py')
    tree = ast.parse(contract_path.read_text(encoding='utf-8'))
    capabilities = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign) and
        any(isinstance(t, ast.Name) and t.id == 'CAPABILITIES' for t in n.targets)))
    gold = independent_gold(tasks, query_rows, capabilities)
    saved_gold = a.json('corpus/retrieval_pool_inputs_v1/gold_memory_sets.json')
    sources = set().union(*gold.values())
    a.check(len(sources) == 100 and sum(bool(v) for v in gold.values()) == 160,
            'INDEPENDENT_GOLD_POPULATION_CHANGED')
    for q in query_rows:
        task = by_task[q['source_task_id']]
        a.check(task['split'] == 'formal' and task['chain_id'] == q['chain_id'] and task['domain'] == q['domain'] and
                task['observations']['checkpoint'] == q['checkpoint'], 'QUERY_SOURCE_NOT_NEW_FORMAL_INPUT', query_id=q['query_id'])
        a.check(gold[q['query_id']] == {'source:' + x for x in q['relevant_memory_ids']} == set(saved_gold.get(q['query_id'], [])) and
                q['no_answer'] == (not gold[q['query_id']]), 'INDEPENDENT_GOLD_MISMATCH', query_id=q['query_id'])
    inventory = a.json('corpus/retrieval_pool_inputs_v1/native_input_inventory.json')
    native = {(r['query_id'], r['mode']): r for r in inventory}
    a.check(len(native) == len(inventory) == 1200, 'NATIVE_INVENTORY_NOT_1200')
    for q in query_rows:
        for mode in MODES:
            item = native.get((q['query_id'], mode), {})
            value = native_input(q, mode)
            a.check(item.get('input') == value and item.get('sha256') == hashlib.sha256(canonical(value).encode()).hexdigest(),
                    'NATIVE_INPUT_INVENTORY_MISMATCH', query_id=q['query_id'], mode=mode)
    for relative in ('corpus/retrieval_pool_inputs_v1/input_registration.json',
                     'corpus/retrieval_pool_inputs_v1/native_input_inventory.json',
                     'corpus/retrieval_pool_inputs_v1/gold_memory_sets.json',
                     'analysis/retrieval_driver_preoutcome_correction_v1/correction_receipt.json',
                     'formal_snapshot_v1/drivers/contract.py',
                     *('corpus/candidate_v2/' + n for n in registration.get('tasks_queries_sha256', {}))):
        a.check(frozen_relative.get(relative) == a.hashes.get(relative, {}).get('sha256'),
                'USED_INPUT_NOT_MATCHING_FORMAL_FREEZE', file=relative)
    # Exact original inputs are streamed after timing, never regenerated or pooled.
    pool_ids = {}; pool_rows = []; feedback_policies = {}
    for size in SIZES:
        for build in BUILDS:
            filename = f'pool_n{size}_build{build}.jsonl'
            relative = 'corpus/retrieval_pool_inputs_v1/' + filename
            ids = set(); positive = {}; conditions = Counter(); count = 0; feedback_policy = {}
            for record in a.lines(relative):
                mid = record.get('memory_id'); count += 1
                a.check(mid not in ids, 'DUPLICATE_POOL_MEMORY_ID', pool=filename)
                ids.add(mid); payload = record.get('payload') or {}
                conditions[payload.get('fixture_condition', 'causal_source')] += 1
                if isinstance(mid, str) and mid.startswith('source:'):
                    positive[mid] = payload
                condition = payload.get('fixture_condition')
                if condition in ('stale', 'wrong_conflict'):
                    good = record.get('effective_reuse_count'); bad = record.get('harmful_reuse_count')
                    quarantined = record.get('quarantined')
                    valid = integer(good) and integer(bad) and isinstance(quarantined, bool)
                    a.check(valid, 'PREPARED_FEEDBACK_INITIAL_COUNTER_SCHEMA_INVALID', pool=filename, memory_id=mid)
                    if valid:
                        # Native MemoryStore.record_feedback(False) twice: bad+=2;
                        # quarantine after >=2 harmful observations and posterior
                        # reliability (good+1)/(good+bad+2) below0.45. Existing
                        # quarantine is sticky. No query label is consulted.
                        after_bad = bad + 2
                        expected_quarantine = quarantined or (
                            good + after_bad >= 2 and after_bad >= 2 and
                            (good + 1.0) / (good + after_bad + 2.0) < .45)
                        feedback_policy[mid] = {'fixture_condition': condition,
                                                'quarantined': expected_quarantine}
            wanted = registration.get('pool_files', {}).get(filename, {})
            a.check(count == len(ids) == size and positive.keys() == sources and
                    dict(conditions) == wanted.get('conditions') and
                    a.hashes.get(relative, {}).get('sha256') == wanted.get('sha256') == frozen_relative.get(relative),
                    'PREPARED_POOL_IDENTITY_CHANGED', pool=filename)
            for q in query_rows:
                reconstructed = {mid for mid, p in positive.items() if p.get('chain_id') == q['chain_id'] and
                    p.get('checkpoint', math.inf) < q['checkpoint'] and p.get('trusted') is True and
                    p.get('validity') == 'current_procedure_v1' and set(q['required_capabilities']) <= set(p.get('capabilities', []))}
                a.check(reconstructed == gold[q['query_id']], 'POOL_GOLD_COMPATIBILITY_MISMATCH', pool=filename, query_id=q['query_id'])
            pool_ids[(size, build)] = ids
            feedback_policies[(size, build)] = feedback_policy
            pool_rows.append({'size': size, 'build': build, 'records': count, 'source_memories': len(positive),
                              'authored_feedback_records': len(feedback_policy),
                              'expected_feedback_events': 2 * len(feedback_policy)})
    expected = {(size, build, mode, qid) for size in SIZES for build in BUILDS for mode in MODES for qid in queries}
    actual = {}; cells = Counter(); process_rows = []; build_rows = []
    for size in SIZES:
        for build in BUILDS:
            stem = f'retrieval_n{size}_build{build}'
            process = a.json(f'analysis/{stem}_process.json'); process_rows.append(process)
            a.check(process.get('size') == size and process.get('build') == build and process.get('returncode') == 0 and
                    process.get('timed_out') is False, 'RETRIEVAL_WORKER_FAILED_OR_TIMED_OUT', size=size, build=build)
            a.check(canonical(aggregate_by_key.get((size, build))) == canonical(process),
                    'AGGREGATE_PROCESS_CONTENT_DIFFERS_FROM_WORKER_RECEIPT', size=size, build=build)
            built = a.json(f'analysis/{stem}_build.json'); build_rows.append(built)
            a.check(built.get('count') == size and built.get('gold_prefilter') is False,
                    'ACTUAL_POOL_BUILD_INCOMPLETE', size=size, build=build)
            pool_copy = a.track(f'raw/{stem}_pool.jsonl')
            registered = registration['pool_files'][f'pool_n{size}_build{build}.jsonl']['sha256']
            a.check(pool_copy is not None and sha(pool_copy) == registered == built.get('pool_sha256'),
                    'ACTUAL_POOL_COPY_CHANGED', size=size, build=build)
            feedback_seen = set(); policy = feedback_policies[(size, build)]
            for feedback in a.lines(f'raw/{stem}_feedback.jsonl'):
                mid = feedback.get('memory_id')
                if not a.check(mid in policy and mid not in feedback_seen,
                               'FEEDBACK_AUTHORED_DISTRACTOR_IDENTITY_INVALID', size=size, build=build, memory_id=mid):
                    continue
                feedback_seen.add(mid)
                a.check(set(feedback) == {'memory_id', 'fixture_condition', 'effective', 'observations', 'quarantined', 'basis'} and
                        feedback.get('fixture_condition') == policy[mid]['fixture_condition'] and
                        feedback.get('effective') is False and feedback.get('observations') == 2 and
                        isinstance(feedback.get('quarantined'), bool) and
                        feedback.get('quarantined') is policy[mid]['quarantined'] and
                        feedback.get('basis') == 'authored known stale/wrong archival fixture, not current-query labels',
                        'FEEDBACK_COUNTER_QUARANTINE_OR_NO_QUERY_LABEL_CONTRACT_FAILED', size=size, build=build, memory_id=mid)
            a.check(feedback_seen == set(policy), 'FEEDBACK_AUTHORED_DISTRACTOR_COVERAGE_INCOMPLETE', size=size, build=build)
            if size == 100:
                a.check(not policy and a.hashes.get(f'raw/{stem}_feedback.jsonl', {}).get('bytes') == 0,
                        'POOL100_FEEDBACK_NOT_EMPTY', build=build)
            order = list(query_rows); random.Random(SEEDS[build - 1]).shuffle(order)
            position = {q['query_id']: i for i, q in enumerate(order)}
            file_sequence = []
            for row in a.lines(f'raw/{stem}.jsonl'):
                key = (row.get('pool_size'), row.get('build'), row.get('mode'), row.get('query_id'))
                if not a.check(key in expected and key not in actual, 'UNEXPECTED_OR_DUPLICATE_QUERY_UNIT', key=list(key)):
                    continue
                actual[key] = row; cells[key[:3]] += 1
                file_sequence.append((key[2], key[3]))
                q = queries[key[3]]; mode = key[2]; query_input = native_input(q, mode)
                a.check(key[:2] == (size, build) and row.get('experiment_id') == f'{stem}-{mode}-{q["query_id"]}' and
                        all(row.get(k) == q[k] for k in ('source_task_id', 'chain_id', 'domain', 'query_class')),
                        'QUERY_RAW_IDENTITY_CHANGED', key=list(key))
                a.check(row.get('query') == q['text'] and row.get('input_keywords') == (query_input['keywords'] or []) and
                        row.get('input_tags') == (query_input['tags'] or []), 'ACTUAL_NATIVE_INPUT_CHANGED', key=list(key))
                a.check(row.get('answerable') == bool(gold[key[3]]) and set(row.get('gold_memory_ids', [])) == gold[key[3]],
                        'RAW_GOLD_MISMATCH', key=list(key))
                ids = [h.get('memory_id') for h in row.get('hits', [])]
                a.check(len(ids) <= 10 and len(ids) == len(set(ids)) and set(ids) <= pool_ids[(size, build)],
                        'HIT_OUTSIDE_ACTUAL_POOL_OR_RANK_DUPLICATE', key=list(key))
                complete = row.get('status') == 'COMPLETE' and row.get('error') is None
                a.check(row.get('status') in ('COMPLETE', 'FAILED') and
                        (row.get('error') is None) == (row.get('status') == 'COMPLETE'),
                        'QUERY_TERMINAL_STATUS_INVALID', key=list(key))
                wanted_metrics = scored(ids, gold[key[3]]) if complete else dict.fromkeys(METRICS)
                a.check(all(row.get(m) == wanted_metrics[m] for m in METRICS),
                        'QUERY_METRIC_OR_FAILED_REFUSAL_MISMATCH', key=list(key))
                a.check(row.get('first_query_of_mode') == (position[q['query_id']] == 0) and
                        row.get('postings_cache_state') == 'NOT_OBSERVED' and
                        'cold_postings_after_build_or_feedback' not in row,
                        'UNOBSERVED_CACHE_MISLABELLED', key=list(key))
                attempted = row.get('native_retrieve_attempted')
                a.check(integer(row.get('prequery_rss_check_ns')) and row.get('prequery_rss_soft_limit_mib') == 1400 and
                        row.get('latency_boundary') == 'native MemoryStore.retrieve only; RSS precheck and output scoring excluded',
                        'RETRIEVAL_TIMING_BOUNDARY_INVALID', key=list(key))
                a.check((attempted is True and integer(row.get('latency_ns')) and integer(row.get('cpu_ns'))) or
                        (attempted is False and row.get('latency_ns') is None and row.get('cpu_ns') is None and
                         row.get('error_phase') == 'prequery_rss_check' and not complete),
                        'PRECHECK_FAILURE_FABRICATED_NATIVE_LATENCY', key=list(key))
                a.check((complete and row.get('error_phase') is None) or
                        (not complete and row.get('error_phase') == ('native_retrieve' if attempted else 'prequery_rss_check')),
                        'FAILED_QUERY_ERROR_BOUNDARY_INVALID', key=list(key))
                a.check((complete and integer(row.get('candidate_count'))) or
                        (not complete and row.get('candidate_count') is None), 'FAILURE_CANDIDATE_COUNT_NOT_NULL', key=list(key))
            a.check(file_sequence == [(mode, q['query_id']) for mode in MODES for q in order],
                    'REGISTERED_QUERY_OR_MODE_ORDER_CHANGED', size=size, build=build)
            terminal = a.json(f'analysis/{stem}_status.json')
            selected = [r for k, r in actual.items() if k[:2] == (size, build)]
            a.check(terminal.get('rows') == len(selected) == 1200 and terminal.get('failures') == sum(r['status'] != 'COMPLETE' for r in selected),
                    'WORKER_TERMINAL_COUNTS_CHANGED', size=size, build=build)
            a.check(terminal.get('feedback_events') == 2 * len(policy),
                    'WORKER_FEEDBACK_EVENT_COUNT_MISMATCH', size=size, build=build)
    a.check(set(actual) == expected and len(cells) == 60 and set(cells.values()) == {240}, 'FULL14400_MATRIX_INCOMPLETE')
    paired = defaultdict(set)
    for size, build, mode, qid in actual:
        paired[(size, build, qid)].add(mode)
    a.check(len(paired) == 2880 and all(v == set(MODES) for v in paired.values()), 'FIVE_METHOD_QUERY_PAIRING_INCOMPLETE')
    a.stable()
    # No statistics are generated from a failed integrity audit. A capacity failure
    # remains a planned missing unit, with its exact process evidence preserved.
    summaries = []; pooled = []
    if not a.findings:
        import numpy as np
        for size in SIZES:
            for mode in MODES:
                for build in BUILDS:
                    keys = sorted(k for k in expected if k[:3] == (size, build, mode))
                    summaries.append(summarize(np, keys, actual, queries, gold, {'pool_size': size, 'mode': mode}, build))
                keys = sorted(k for k in expected if k[0] == size and k[2] == mode)
                pooled.append(summarize(np, keys, actual, queries, gold, {'pool_size': size, 'mode': mode}))
    a.stable()
    if a.findings:
        summaries = []; pooled = []
    coverage = [{'pool_size': size, 'build': build, 'mode': mode, 'planned': 240,
                 'recorded': cells[(size, build, mode)],
                 'failed': sum(r['status'] != 'COMPLETE' for k, r in actual.items() if k[:3] == (size, build, mode)),
                 'missing': 240 - cells[(size, build, mode)]}
                for size in SIZES for build in BUILDS for mode in MODES]
    failures = sum(r['status'] != 'COMPLETE' for r in actual.values())
    report = {'status': 'FAIL_RETAINED_NO_INFERENTIAL_TABLES' if a.findings else
                       'COMPLETE_WITH_EXECUTION_FAILURES_AND_WITHHELD_METRICS' if failures else 'PASS_COMPLETE_NEW_RETRIEVAL_MATRIX',
              'created_utc': now(), 'study_id': STUDY, 'analyzer_sha256': sha(Path(__file__)),
              'planned_query_units': 14400, 'observed_query_units': len(actual), 'planned_cells': 60,
              'queries_per_build_cell': 240, 'pooled_build_cells': 20, 'paired_query_bundles': len(paired),
              'execution_failures': failures, 'planned_pools': 12, 'planned_pool_records': 78300,
              'independently_rebuilt_gold_queries': len(gold), 'independent_source_tasks': 120,
              'independent_chains': 20, 'original_method_sha256': OLD_ANALYZER_SHA,
              'bootstrap_draws': BOOTSTRAPS, 'bootstrap_seed': BOOTSTRAP_SEED,
              'method': 'retained domain-stratified whole-chain bootstrap; query/build repeats remain inside chain',
              'new_hypothesis_tests': 0, 'new_holm_families': 0, 'formal_outcome_streams_read': False,
              'real_model_calls': 0, 'raw_or_prepared_inputs_modified': False,
              'limits': [
                  'Tag-only has4 unique native inputs;240 query labels do not create240 distinct tag requests.',
                  'All planned denominators retained; failed/missing metric values stay unknown, so affected primary point estimates and CIs are null.',
                  'No-answer failure is not successful native refusal; candidate retrieval is not memory adoption or causal benefit.',
                  'Native failed-attempt times remain raw observations; they never masquerade as efficiency gains.',
                  'RSS is a query snapshot and1400MiB is a prequery soft check, not a continuous peak bound.',
                  'Postings cache state is NOT_OBSERVED; first_query_of_mode is an order flag only.',
                  'Size100 builds share the same100 source records; three rebuilds are not new independent cases.',
                  'Automatic causal/capability gold labels are not human annotation or unconstrained procedure validation.',
                  'No historical retrieval/real-model outcomes are read, pooled, replayed or rescored.',
              ], 'evidence': a.hashes, 'findings': a.findings}
    destination.mkdir(parents=True)
    write_json(destination / 'independent_gold.json', {k: sorted(v) for k, v in gold.items()})
    write_json(destination / 'process_and_build_evidence.json', {'processes': process_rows, 'builds': build_rows, 'pools': pool_rows})
    write_csv(destination / 'cell_coverage.csv', coverage)
    write_csv(destination / 'retrieval_quality_scale_by_build.csv', summaries)
    write_csv(destination / 'retrieval_quality_scale.csv', pooled)
    # Analysis-only planned rows, explicitly flagging absent observations. This is
    # not a repaired/backfilled raw stream. No query text or model content copied.
    with (destination / 'query_observations.jsonl').open('x', encoding='utf-8') as f:
        for size, build, mode, qid in sorted(expected):
            row = actual.get((size, build, mode, qid)); q = queries[qid]
            value = {'pool_size': size, 'build': build, 'mode': mode, 'query_id': qid,
                     'source_task_id': q['source_task_id'], 'chain_id': q['chain_id'], 'domain': q['domain'],
                     'observation_recorded': row is not None,
                     'status': row['status'] if row else 'NOT_RECORDED',
                     'answerable_from_independent_gold': bool(gold[qid])}
            for field in ('error', 'error_phase', 'latency_ns', 'cpu_ns', 'candidate_count', 'rss_bytes',
                          'native_retrieve_attempted', 'prequery_rss_check_ns', 'prequery_rss_bytes',
                          'first_query_of_mode', 'postings_cache_state', *METRICS):
                value[field] = row.get(field) if row else None
            f.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
    report['output_sha256'] = {p.name: sha(p) for p in destination.iterdir() if p.is_file()}
    # A completed audit receipt is written only after every output has serialized.
    write_json(destination / 'audit.json', report)
    print(json.dumps({k: report[k] for k in ('status', 'observed_query_units', 'execution_failures', 'paired_query_bundles')}))
    return 1 if a.findings else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('bind', 'analyze'))
    p.add_argument('--study-out', required=True, type=Path)
    p.add_argument('--version', default='retrieval_postrun_analysis_v1')
    p.add_argument('--driver-source', type=Path, default=Path(__file__).resolve().parents[1] / 'retrieval_driver.py')
    args = p.parse_args(); out = args.study_out.resolve()
    if out.name != STUDY or re.fullmatch(r'retrieval_postrun_analysis_v[1-9][0-9]*', args.version) is None:
        p.error('exact new-study root and new retrieval_postrun_analysis_vN version required')
    binding = out / 'manifests' / (args.version + '_binding.json')
    if args.command == 'bind':
        bind(args, out, binding)
        return 0
    return analyze(args, out, binding)


if __name__ == '__main__':
    sys.exit(main())
