"""Recorder overhead on fixed public fixtures; invoke only after heavy jobs stop.

No provider request is sent. Baseline calls the uninstrumented native pipeline:
no SocketObserver, Recorder, sampler, wrapped state read, or state-order recompute.
"""
from __future__ import annotations
from collections import defaultdict
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import uuid

sys.path.insert(0, '/opt/lingxi/formal-checkout/src')
import numpy as np
import psutil
from scipy.stats import t as student_t
import formal_real_driver as driver
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding
from agent_runtime.providers.base import LLMResponse
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.runtime.task import VerdictResult
from formal_fact_calibration import ARMS, make_task
from formal_fixture_checks import check
from formal_prompt_adapter import model_prompt


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def _group_cpu():
    root = psutil.Process()
    values = {}
    for process in [root] + root.children(recursive=True):
        try:
            values[process.pid] = sum(process.cpu_times()[:2])
        except psutil.Error:
            pass
    return values


def baseline(unit, row, memory, cfg):
    """Same business execution/scoring as driver.execute, without its recorder."""
    # Both paths compute the immutable native-source identity before constructing
    # the context, so source hashing is not silently removed only from baseline.
    source = driver._source_tree_sha256(driver.ROOT)
    mode, state_arm, memory_arm = ARMS[unit['arm']]
    state_on = state_arm and (row['state_spec']['applicable'] or unit['phase'].startswith('public_'))
    ctx, orch = None, None
    restore = None
    calls = 0
    result_row = {**unit, 'success': False, 'error': None, 'source_sha256': source,
                  'provider_input_tokens': None, 'provider_output_tokens': None,
                  'provider_total_tokens': None, 'real_provider_attempts': 0,
                  'model_evidence': 'deterministic_fixture'}
    try:
        ctx = build_context(cfg, mode, task_id=row['task_id'], use_memory=memory_arm,
                            use_state_exchange=state_on, use_sandbox=False, memory=memory)
        def complete(prompt, **kwargs):
            nonlocal calls
            # The same native fact guard is used in the recorder path.
            model_prompt(prompt, row['observations'])
            calls += 1
            return LLMResponse(text=json.dumps(row['expected']), prompt_tokens=0, completion_tokens=0,
                model='deterministic_calibration_fixture', metadata={'usage_source': 'offline_fixture_not_provider'})
        ctx.llm.complete = complete
        import agent_runtime.agents.model_decision as decision
        original = decision.diagnose
        restore = (decision, original)
        def diagnose(evidence, context):
            value = original(evidence, context)
            checked = check(row, value['action'])
            context.record_tool(value['probe'])
            context.blackboard['formal_fixture_check'] = checked
            context.record_event('formal.fixture_check', stage='executor', payload={**checked, 'pid': os.getpid()})
            return value
        decision.diagnose = diagnose
        task = make_task(row)
        original_verify = task.verifier
        def verify(outputs):
            verdict = original_verify(outputs)
            checked = ctx.blackboard.get('formal_fixture_check')
            valid = bool(checked and checked.get('current_record_matches_check'))
            return VerdictResult(success=verdict.success and valid, quality=float(verdict.success and valid),
                detail={'exact_contract_match': verdict.success, 'selected_fixture_check_matches': valid})
        task.verifier = verify
        orch = Orchestrator(build_default_agents(cfg))
        result = asyncio.run(orch.run_task(task, ctx))
        orch.close_agent_processes()
        result_row.update(success=result.success, quality=result.quality, error=result.error,
            outputs=result.outputs, metrics=asdict(result.metrics),
            state_consumed=bool(ctx.blackboard.get('state_consumed')),
            observed_fixture_check=ctx.blackboard.get('formal_fixture_check'))
    except Exception as error:
        text = str(error).replace(driver.SECRET, '[REDACTED]') if driver.SECRET else str(error)
        result_row['error'] = {'type': type(error).__name__, 'message': text[:800]}
    finally:
        if orch:
            orch.close_agent_processes()
        if ctx:
            ctx.llm._client.close()
        if restore:
            restore[0].diagnose = restore[1]
    result_row['deterministic_fixture_calls'] = calls
    print(json.dumps({key: result_row.get(key) for key in ('phase', 'task_id', 'arm', 'repeat', 'success')}), flush=True)
    return result_row


def analyze(rows):
    paired = defaultdict(dict)
    for row in rows:
        paired[(row['task_id'], row['arm'], row['repeat'])][row['condition']] = row
    summaries = []
    for arm in ARMS:
        entries = [conditions for (task, group, repeat), conditions in paired.items() if group == arm]
        summary = {'arm': arm, 'planned_pairs': 24, 'observed_pairs': len(entries), 'unique_public_tasks': 8,
                   'public_chains': 4, 'matched_repeats': 3, 'independent_task_count': 8,
                   'provider_tokens': None, 'real_provider_calls': 0, 'metrics': {}}
        for metric in ('outer_wall_sec', 'outer_runtime_cpu_sec', 'observable_live_process_group_cpu_sec'):
            per_repeat = defaultdict(list)
            per_domain = defaultdict(list)
            diffs, base, recorded = [], [], []
            for conditions in entries:
                if set(conditions) != {'direct_native', 'full_recorder'}:
                    continue
                a, b = conditions['direct_native'], conditions['full_recorder']
                difference = b[metric] - a[metric]
                per_repeat[a['repeat']].append(difference)
                per_domain[a['domain']].append(difference)
                diffs.append(difference)
                base.append(a[metric])
                recorded.append(b[metric])
            repeats = [float(np.mean(v)) for _, v in sorted(per_repeat.items())]
            point = float(np.mean(diffs)) if diffs else None
            if len(repeats) >= 2:
                half = float(student_t.ppf(.975, len(repeats) - 1) * np.std(repeats, ddof=1) / np.sqrt(len(repeats)))
                conditional_ci = [float(np.mean(repeats) - half), float(np.mean(repeats) + half)]
            else:
                conditional_ci = None
            summary['metrics'][metric] = {'direct_native_mean': float(np.mean(base)) if base else None,
                'full_recorder_mean': float(np.mean(recorded)) if recorded else None,
                'paired_overhead_mean': point,
                'relative_overhead': (float(np.mean(recorded)) / float(np.mean(base)) - 1) if base and np.mean(base) else None,
                'conditional_repeat_mean_ci95': conditional_ci,
                'conditional_ci_scope': 'paired t interval across three matched complete passes, df=2; conditional on these fixed eight public tasks; not task-population inference',
                'repeat_block_means': repeats,
                'domain_stratified_chain_bootstrap_draws': 10000,
                'domain_stratified_chain_ci95': [point, point] if point is not None else None,
                'domain_stratified_chain_ci_limitation': 'one public chain per domain: stratified chain resampling is degenerate; not used to claim certain overhead',
                'per_domain_paired_mean': {k: float(np.mean(v)) for k, v in per_domain.items()}}
        summaries.append(summary)
    return {'scope': 'recorder measurement overhead on deterministic fixed public fixtures, not model quality/token/latency evidence',
        'status': 'COMPLETED' if len(rows) == 288 else 'INCOMPLETE', 'planned_rows': 288, 'observed_rows': len(rows),
        'model_tokens': None, 'real_provider_calls': 0, 'summary_by_arm': summaries,
        'quality_mismatches': [{'task_id': k[0], 'arm': k[1], 'repeat': k[2]}
            for k, v in paired.items() if set(v) == {'direct_native', 'full_recorder'} and
                (v['direct_native']['success'] != v['full_recorder']['success'] or
                 v['direct_native'].get('outputs') != v['full_recorder'].get('outputs'))],
        'all_fixture_tasks_success': all(row['success'] for row in rows),
        'span_accounting': 'outer wall is independently timed; never added to nested driver spans or summed with native metrics latency',
        'cpu_scope': 'runtime CPU includes sampler/logging threads; live process-group counter delta includes surviving router/helpers; exited child CPU is explicitly incomplete'}


def run(out):
    out = Path(out)
    if out.exists():
        raise RuntimeError('overhead output already exists; use explicit new version, never replay or overwrite')
    out.mkdir(parents=True)
    (out / 'raw').mkdir()
    rows = [r for r in driver.cases() if r['split'] == 'pilot' and r['observations']['checkpoint'] <= 2]
    assert len(rows) == 8
    plan = {'created_utc': datetime.now(timezone.utc).isoformat(), 'source_sha256': driver._source_tree_sha256(driver.ROOT),
        'public_tasks': [r['task_id'] for r in rows], 'arms': list(ARMS), 'matched_repeats': 3,
        'conditions': ['direct_native', 'full_recorder'], 'planned_measurements': 288,
        'order': 'alternate condition order by task index + fixed arm index + repeat; rotate arm order by task index + repeat',
        'memory': 'independent initially empty SQLite stores per condition/arm/repeat/chain; causal checkpoint order; close all stores after pass',
        'provider': 'same deterministic fixture and identical fact adapter in both conditions; zero HTTP calls; provider token fields null',
        'configuration': 'identical native config and journal enabled; only output storage paths isolated by condition and pass',
        'full_recorder': 'formal_real_driver.execute(dry=True,instrument=True), same actual SocketObserver/state monitors/resource sampler/flush',
        'direct_native': 'build_context + ordinary four-role Orchestrator + same read-only fixture tool/verifier; no recorder/socket observer/resource sampler/state monitoring wrapper',
        'wall_boundary': 'whole per-unit call including source identity/context construction, teardown and record flush; group memory store setup/close excluded equally',
        'ci': 'report original stratified chain bootstrap degenerate CI plus three-pass conditional paired-t CI, df2; no repeated-task sample inflation',
        'precondition': 'operator must confirm heavy offline jobs have stopped; real-model timing runs must be separate',
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write(out / 'plan.json', plan)
    original_out, original_configuration = driver.OUT, driver.configuration
    driver.OUT = out / 'recorder_evidence'
    output_rows, stores = [], {}
    log = (out / 'driver.stdout.log').open('x')
    raw = (out / 'raw/overhead_units.jsonl').open('x')
    try:
        for repeat in range(3):
            stores = {}
            for task_index, row in enumerate(rows):
                arm_order = list(ARMS)
                shift = (task_index + repeat) % len(arm_order)
                for arm in arm_order[shift:] + arm_order[:shift]:
                    arm_index = list(ARMS).index(arm)
                    conditions = ['direct_native', 'full_recorder']
                    if (task_index + arm_index + repeat) % 2:
                        conditions.reverse()
                    for condition in conditions:
                        cfg = original_configuration('main', False, None, row['state_spec']['size_class'])
                        cfg = cfg.with_overrides(data_dir=str(Path('/opt/lingxi/overhead-journal') / out.name / str(repeat) / condition))
                        driver.configuration = lambda *args, _cfg=cfg, **kwargs: _cfg
                        unit = {'phase': 'public_overhead_calibration', 'task_id': row['task_id'],
                            'chain_id': row['chain_id'], 'domain': row['domain'], 'difficulty': row['difficulty'],
                            'arm': arm, 'repeat': repeat, 'model': 'main', 'processes': False, 'topology': 'runtime_uds',
                            'intervention': None, 'condition': condition}
                        unit['run_id'] = uuid.uuid5(uuid.NAMESPACE_URL, str(out) + json.dumps(unit, sort_keys=True)).hex
                        unit['unit_id'] = unit['run_id']
                        memory = None
                        if ARMS[arm][2]:
                            key = (condition, arm, repeat, row['chain_id'])
                            if key not in stores:
                                path = out / 'memory' / f'{repeat}_{condition}_{arm}_{row["chain_id"]}.sqlite'
                                path.parent.mkdir(exist_ok=True)
                                mc = cfg.with_overrides(embedding=replace(cfg.embedding, dim=256))
                                stores[key] = MemoryStore(build_embedding(mc), db_path=str(path), bm25_rerank=True)
                            memory = stores[key]
                        before_group = _group_cpu()
                        start_wall, start_cpu = time.perf_counter_ns(), time.process_time_ns()
                        with redirect_stdout(log):
                            result = (driver.execute(unit, row, memory, dry=True, instrument=True)
                                      if condition == 'full_recorder' else baseline(unit, row, memory, cfg))
                        end_cpu, end_wall = time.process_time_ns(), time.perf_counter_ns()
                        after_group = _group_cpu()
                        delta = sum(max(0, value - before_group.get(pid, 0)) for pid, value in after_group.items())
                        item = {**unit, 'success': result.get('success'), 'outputs': result.get('outputs'), 'error': result.get('error'),
                            'outer_wall_sec': (end_wall - start_wall) / 1e9,
                            'outer_runtime_cpu_sec': (end_cpu - start_cpu) / 1e9,
                            'observable_live_process_group_cpu_sec': delta,
                            'exited_child_pids_cpu_not_observed': sorted(set(before_group) - set(after_group)),
                            'loadavg_before_next_unit': os.getloadavg(),
                            'native_tool_calls': (result.get('metrics') or {}).get('tool_calls'),
                            'native_journal_enabled': cfg.event_journal_enabled,
                            'state_consumed': result.get('state_consumed'),
                            'provider_input_tokens': None, 'provider_output_tokens': None, 'provider_total_tokens': None,
                            'real_provider_calls': 0, 'model_evidence': 'deterministic_fixture',
                            'source_sha256': plan['source_sha256']}
                        raw.write(json.dumps(item, ensure_ascii=False) + '\n')
                        raw.flush()
                        os.fsync(raw.fileno())
                        output_rows.append(item)
            for store in stores.values():
                store.close()
            stores = {}
            print(json.dumps({'repeat': repeat, 'completed_measurements': len(output_rows)}), flush=True)
    finally:
        for store in stores.values():
            store.close()
        raw.close()
        log.close()
        driver.OUT, driver.configuration = original_out, original_configuration
    report = analyze(output_rows)
    report['raw_sha256'] = hashlib.sha256((out / 'raw/overhead_units.jsonl').read_bytes()).hexdigest()
    write(out / 'overhead_analysis.json', report)
    print(json.dumps({'status': report['status'], 'rows': len(output_rows),
                      'quality_mismatches': len(report['quality_mismatches']), 'real_provider_calls': 0}))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='/evidence/formal_20261019/calibration/measurement_overhead_v1')
    args = parser.parse_args()
    report = run(args.out)
    raise SystemExit(report['status'] != 'COMPLETED' or bool(report['quality_mismatches']) or not report['all_fixture_tasks_success'])
