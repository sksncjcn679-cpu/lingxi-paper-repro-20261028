"""Prepared external/native-T0 system-reference comparability audit.

Consumes the completed hash-bound full audit and frozen plan/sources only. It
does not run a framework, driver, model, statistic, bootstrap, CI or Holm family.
Raw study evidence is hashed opaquely; outcomes are consumed only through the
saved audited projection. A failed full audit can never authorize comparisons.
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

STUDY = 'protocol_repair_20261020_v1'
FREEZE_SHA256 = '2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e'
FULL_AUDITOR_SHA256 = '7c4cc24bb84274bf96a4c05a00d489491798a84455bd596702db762c4977295f'
ROLES = ['planner', 'retriever', 'executor', 'reviewer']
VERSIONS = {'autogen-agentchat': '0.7.5', 'langgraph': '1.2.16'}
PHASES = {'external_autogen': 'autogen', 'external_langgraph': 'langgraph'}
ENDPOINTS = ('quality_success', 'provider_input_tokens', 'wall_sec')
COMPARABLE = 'COMPARABLE_SYSTEM_LEVEL_REFERENCE'
NON_COMPARABLE = 'NON_COMPARABLE'
SOURCES = {
    'external_runner.py': '45da2d4ae69ea5a9b4e66c37b677d21e2723cc83fa4b7c82b5da4b2140fd9c60',
    'real_driver.py': 'd63b08e0120513944d0f0c40a4bdd221cf910518d01c446a0acc0ab1afc6c93b',
    'formal_external_adapters.py': '7057e71107947aecfd64784910ff706305b12e481ad10438e533b9dce7dc7ab5',
    'formal_fact_calibration.py': 'ae55793deecf1583b4061fa9ba2ea2e994cdbc58cd9f4ece0c4efbadd72b1b92',
    'contract.py': '44e5658967db62bdfafe54accabddfdc187bd6a241fc80d59fef32fff4192749',
    'oracle.py': '0757cfbc5c02aa5e5b726b9937fc06667a402257655f2eba1810bb56e4ae11b3',
    'bootstrap.py': '590d691d30816a6f7aae06e0177a631b9b53dd065627dfca381737cbb5c2b269',
    'prompt_adapter.py': '32d9f86cc351ac1b803fedfacb7779ec97c61cd2ac1ae8725191f517e77cc480'}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def sha256_string(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def literal_assignment(source, name):
    tree = ast.parse(source)
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    if len(values) != 1:
        raise ValueError('frozen literal assignment is not unique: ' + name)
    return ast.literal_eval(values[0])


class Auditor:
    def __init__(self, out):
        self.out, self.findings, self.inputs = out, [], {}

    def note(self, code, level='BLOCK', **details):
        self.findings.append({'code': code, 'level': level, **details})

    def require(self, condition, code, **details):
        if not condition:
            self.note(code, **details)
            raise RuntimeError(code)

    def bind(self, path, wanted=None, size=None):
        path = path.resolve()
        self.require(path.name not in ('.env', 'model.env') and '/run/secrets/' not in path.as_posix(),
                     'CREDENTIAL_PATH_NOT_AN_AUDIT_INPUT')
        value = digest(path)
        self.require(wanted is None or value == wanted, 'INPUT_HASH_MISMATCH', file=str(path))
        self.require(size is None or path.stat().st_size == size, 'INPUT_SIZE_MISMATCH', file=str(path))
        self.inputs[str(path)] = {'sha256': value, 'bytes': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns}
        return value

    def json(self, path, wanted=None):
        self.bind(path, wanted)
        return json.loads(path.read_text(encoding='utf-8'))

    def frozen_hash(self, freeze, path):
        value = freeze['files'].get(str(path))
        self.require(sha256_string(value), 'FROZEN_INPUT_HASH_AUTHORITY_MISSING', file=str(path))
        return value

    def stable(self):
        # Full evidence hashes were verified once; also reject any ordinary
        # source append/change during this short analysis without reloading raw.
        for name, before in self.inputs.items():
            path = Path(name)
            self.require(path.is_file() and path.stat().st_size == before['bytes']
                         and path.stat().st_mtime_ns == before['mtime_ns'], 'INPUT_CHANGED_DURING_AUDIT', file=name)


def timing_gate(out, quiet_path):
    closed = json.loads((out / 'formal_execution_v1/execution.json').read_text())
    if (closed.get('status') != 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT'
            or not closed.get('finished_utc') or closed.get('supervised_child_observed_running') is True
            or [stage.get('name') for stage in closed.get('stages', [])] != ['main', 'native_remaining', 'external']
            or not all(stage.get('status') == 'TERMINAL' and stage.get('exit_code') == 0
                       and stage.get('finished_utc') for stage in closed['stages'])):
        raise RuntimeError('timing is not fully terminal; no analysis is permitted')
    if not quiet_path.resolve().is_relative_to(out / 'analysis') or quiet_path.suffix != '.json':
        raise RuntimeError('actual quiescence receipt must be study analysis JSON')
    quiet = json.loads(quiet_path.read_text())
    if (quiet.get('study_id') != STUDY or quiet.get('status') != 'ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING'
            or quiet.get('active_real_timing_processes') != [] or quiet.get('active_retrieval_timing_processes') != []):
        raise RuntimeError('actual real/retrieval quiescence is not evidenced')
    stamp = datetime.fromisoformat(quiet['verified_utc'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(closed['finished_utc'].replace('Z', '+00:00'))
    if stamp.tzinfo is None or end.tzinfo is None or stamp < end:
        raise RuntimeError('quiescence receipt predates real termination')


def locks(out, stack):
    import fcntl
    for name in ('formal_sequence_execution.lock', 'real_provider_execution.lock'):
        stream = stack.enter_context((out / 'manifests' / name).open('r'))
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def source_contract(a, freeze, out):
    snapshot = Path(freeze['driver_snapshot'])
    source_text = {}
    for name, wanted in SOURCES.items():
        path = snapshot / name
        a.require(freeze['files'].get(str(path)) == wanted, 'FROZEN_SOURCE_AUTHORITY_MISMATCH', file=name)
        a.bind(path, wanted)
        source_text[name] = path.read_text(encoding='utf-8')
    mechanisms = literal_assignment(source_text['contract.py'], 'MECHANISMS')
    pairs = {label: action for group in mechanisms.values() for label, action, _ in group}
    a.require(len(pairs) == len(set(pairs.values())) == 24, 'FROZEN_OUTPUT_CONTRACT_NOT_24_PAIRS')
    contract_sha = hashlib.sha256(canonical(pairs)).hexdigest()
    a.require(literal_assignment(source_text['formal_external_adapters.py'], 'ROLES') == tuple(ROLES)
              and literal_assignment(source_text['formal_external_adapters.py'], 'PINNED_VERSIONS') == VERSIONS,
              'FROZEN_EXTERNAL_ROLE_OR_VERSION_CONTRACT_MISMATCH')
    arms = literal_assignment(source_text['formal_fact_calibration.py'], 'ARMS')
    a.require(arms.get('T0') == ('text', False, False), 'NATIVE_T0_STATE_MEMORY_CONDITION_CHANGED')
    tree = ast.parse(source_text['external_runner.py'])
    imports = {node.names[0].name: index for index, node in enumerate(tree.body) if isinstance(node, ast.Import)}
    a.require(imports['real_driver'] < imports['formal_external_adapters'] and
              'adapter.check = oracle.check' in source_text['external_runner.py'], 'EXTERNAL_BOOTSTRAP_OR_ORACLE_BINDING_CHANGED')
    config_path = out / 'manifests/native_configurations.json'
    configurations = a.json(config_path, a.frozen_hash(freeze, config_path))
    cfg = configurations['full_SS1']
    llm = cfg['llm']
    a.require(llm.get('model') == 'qwen3.7-plus' and llm.get('base_url') == 'https://www.dmxapi.cn/v1'
              and llm.get('temperature') == 0 and llm.get('max_tokens') == 1500
              and llm.get('extra_body') == {'enable_thinking': False}
              and cfg.get('llm_advisory') is False and cfg.get('llm_judge') is False
              and cfg.get('llm_coalesce') == 'off' and cfg.get('task_retry_budget') == 0,
              'SHARED_MAIN_MODEL_OR_ADVISORY_CONFIGURATION_CHANGED')
    return contract_sha


def plan_pairs(a, plan, cases, contract_sha):
    units = {unit['run_id']: unit for unit in plan}
    a.require(len(plan) == len(units) == 6504, 'FROZEN_PLAN_NOT_6504_UNIQUE')
    subset = {case['task_id'] for case in cases if case.get('split') == 'formal'
              and case['observations']['checkpoint'] in (1, 3, 5)}
    a.require(len(subset) == 60, 'PROSPECTIVE_SUBSET_RULE_NOT_60')
    native_selected = [unit for unit in plan if unit['phase'] == 'main' and unit['arm'] == 'T0'
                       and unit['repeat'] in (0, 1) and unit['task_id'] in subset]
    native = {(unit['task_id'], unit['repeat']): unit for unit in native_selected}
    a.require(len(native_selected) == len(native) == 120
              and all(unit['model'] == 'main' and unit['model_id'] == 'qwen3.7-plus' and unit['processes'] is False
              and unit.get('intervention') is None and unit['topology'] == 'runtime_uds' for unit in native.values()),
              'MATCHED_NATIVE_MAIN_T0_NOT_EXACT_60_BY_2')
    result = []
    for phase, framework in PHASES.items():
        selected = [unit for unit in plan if unit['phase'] == phase]
        keys = Counter((unit['task_id'], unit['repeat']) for unit in selected)
        a.require(len(selected) == 120 and set(keys) == set(native) and set(keys.values()) == {1},
                  'EXTERNAL_NOT_SAME_FROZEN_60_BY_2', framework=framework)
        for unit in selected:
            a.require(unit['arm'] == 'T0' and unit['model'] == 'main' and unit['model_id'] == 'qwen3.7-plus'
                      and unit['processes'] is False and unit.get('intervention') is None
                      and unit['topology'] == 'framework_inprocess', 'EXTERNAL_FROZEN_BASELINE_CONDITION_CHANGED')
            reference = native[(unit['task_id'], unit['repeat'])]
            a.require(unit['effective_contract_sha256'] == reference['effective_contract_sha256'] == contract_sha
                      and unit['task_sha256'] == reference['task_sha256'], 'MATCHED_CASE_OR_24_PAIR_CONTRACT_DIFFERS')
            result.append({'external_run_id': unit['run_id'], 'native_t0_run_id': reference['run_id'],
                           'task_id': unit['task_id'], 'repeat': unit['repeat'], 'framework': framework,
                           'external_phase': phase, 'native_phase': 'main', 'native_arm': 'T0', 'native_model': 'main'})
    a.require(len(result) == 240, 'MATCHED_PAIR_MANIFEST_NOT_240')
    return units, result


def selected_lines(a, path, wanted_sha, all_ids, selected_ids):
    a.bind(path, wanted_sha)
    selected, seen = {}, set()
    with path.open(encoding='utf-8') as stream:
        for row in map(json.loads, stream):
            rid = row.get('run_id')
            a.require(rid in all_ids and rid not in seen, 'FULL_AUDIT_OUTPUT_IDENTITY_SET_INVALID', file=path.name)
            seen.add(rid)
            if rid in selected_ids:
                selected[rid] = row
    a.require(seen == all_ids and set(selected) == selected_ids, 'FULL_AUDIT_OUTPUT_COVERAGE_INCOMPLETE', file=path.name)
    return selected


def unknown_effect(code):
    if code in {'REAL_PROVIDER_USAGE_INCOMPLETE', 'NO_PROVIDER_ATTEMPT_OBSERVED_USAGE_NOT_CALIBRATED'}:
        return {'provider_input_tokens'}
    if code in {'WALL_BOUNDARY_NOT_OBSERVED', 'EXCLUSIVE_REBUILD_THREAD_AMBIGUOUS'}:
        return {'wall_sec'}
    if code in {'RESOURCE_SAMPLES_NOT_OBSERVED', 'IPC_ACCOUNTING_NOT_OBSERVED',
                'FAILED_UNIT_PLAN_CONSUMPTION_NOT_OBSERVED', 'STATE_ENABLED_WITHOUT_OBSERVED_PLAN',
                'MEMORY_INDEX_OPERATION_NOT_OBSERVED', 'CONDITION_SEED_LOGICAL_WRITE_SPAN_NOT_OBSERVED'}:
        return set()
    # Source, identity, cleanup and unrecognized UNKNOWN cannot be dismissed.
    return set(ENDPOINTS)


def compare(a, pairs, units, observations, checks, findings, source_sha, contract_sha):
    relevant_ids = {row[key] for row in pairs for key in ('external_run_id', 'native_t0_run_id')}
    per_id = defaultdict(list)
    global_unknowns = []
    dispositions = []
    for finding in findings:
        if finding.get('level') != 'UNKNOWN':
            continue
        rid, code = finding.get('run_id'), finding.get('code')
        effects = sorted(unknown_effect(code))
        if rid is None:
            global_unknowns.append(finding)
            scope = 'GLOBAL_IDENTITY_OR_UNSCOPED_UNKNOWN'
            effects = list(ENDPOINTS)
        elif rid in relevant_ids:
            per_id[rid].append(finding)
            scope = 'MATCHED_UNIT'
        else:
            scope = 'OUTSIDE_EXACT_MATCHED_MAIN_T0_AND_EXTERNAL_IDS'
            effects = []
        dispositions.append({'code': code, 'run_id': rid, 'scope': scope,
                             'affected_selected_endpoints': effects,
                             'original_claim_limit': finding.get('blocks_claim', finding.get('consequence'))})
    result = []
    for pair in pairs:
        ext_id, native_id = pair['external_run_id'], pair['native_t0_run_id']
        ext, native = observations[ext_id], observations[native_id]
        ext_check, native_check = checks[ext_id], checks[native_id]
        semantic_reasons = []
        for rid, row, check in ((ext_id, ext, ext_check), (native_id, native, native_check)):
            if not (row.get('model_evidence') == 'real_provider' and row.get('source_sha256') == source_sha
                    and row.get('effective_contract_sha256') == contract_sha and row.get('model_id') == 'qwen3.7-plus'
                    and check.get('factual_case_checked') is True and check.get('audit_failures') == 0
                    and row.get('audit_failures') == 0):
                semantic_reasons.append('AUDITED_SOURCE_FACT_CONTRACT_OR_MODEL_NOT_VERIFIED:' + rid)
            for key in ('phase', 'task_id', 'arm', 'repeat', 'model', 'processes', 'intervention', 'topology'):
                if row.get(key) != units[rid].get(key):
                    semantic_reasons.append('EXACT_PLANNED_IDENTITY_NOT_VERIFIED:' + rid)
            if check.get('selected_action_present') and not check.get('recorded_readonly_check_present'):
                semantic_reasons.append('SELECTED_READONLY_CHECK_NOT_OBSERVED:' + rid)
            if not integer(row.get('provider_attempts')) or row['provider_attempts'] < 1:
                semantic_reasons.append('ACTUAL_MODEL_FACT_HANDOFF_NOT_OBSERVED:' + rid)
        if not (ext.get('actual_topology') == 'framework_inprocess' and ext.get('comparison_scope') == 'SYSTEM_LEVEL_REFERENCE'
                and ext.get('role_order') == ROLES and ext.get('external_framework_versions') == VERSIONS
                and ext_check.get('actual_external_role_starts') == 4 and ext_check.get('logical_model_invocations') == 1):
            semantic_reasons.append('ACTUAL_EXTERNAL_FRAMEWORK_ROLE_VERSION_OR_SCOPE_NOT_VERIFIED')
        if not (native.get('state_exchange_enabled') is False and native.get('memory_count_before') == 0
                and native.get('memory_count_after') == 0 and native.get('state_consumed') is False):
            semantic_reasons.append('NATIVE_T0_NO_STATE_NO_MEMORY_CONDITION_NOT_VERIFIED')
        endpoint_status = {}
        for endpoint in ENDPOINTS:
            reasons = list(dict.fromkeys(semantic_reasons))
            for finding in [*global_unknowns, *per_id[ext_id], *per_id[native_id]]:
                if finding.get('run_id') is None or endpoint in unknown_effect(finding['code']):
                    reasons.append('FULL_AUDIT_UNKNOWN:' + finding['code'])
            for rid, row, check in ((ext_id, ext, ext_check), (native_id, native, native_check)):
                if endpoint == 'quality_success' and not isinstance(row.get('success'), bool):
                    reasons.append('RECORDED_QUALITY_SUCCESS_NOT_OBSERVED:' + rid)
                elif endpoint == 'provider_input_tokens' and not (check.get('real_provider_usage_complete') is True
                        and row.get('provider_usage_complete') is True and integer(row.get('provider_input_tokens'))):
                    reasons.append('REAL_PROVIDER_INPUT_USAGE_NOT_COMPLETE:' + rid)
                elif endpoint == 'wall_sec':
                    partition = row.get('exclusive_spans_sec')
                    if not (finite(row.get('wall_sec')) and row['wall_sec'] >= 0
                            and finite(row.get('provider_wait_sec')) and row['provider_wait_sec'] >= 0
                            and isinstance(partition, dict) and all(finite(value) and value >= 0 for value in partition.values())
                            and abs(sum(partition.values()) - row['wall_sec']) < 1e-6):
                        reasons.append('WALL_OR_EXCLUSIVE_BOUNDARY_NOT_OBSERVED:' + rid)
            endpoint_status[endpoint] = {'status': NON_COMPARABLE if reasons else COMPARABLE,
                                         'reason_codes': sorted(set(reasons))}
        status = COMPARABLE if all(value['status'] == COMPARABLE for value in endpoint_status.values()) else NON_COMPARABLE
        result.append({**pair, 'comparison_scope': 'SYSTEM_LEVEL_REFERENCE', 'status': status,
            'semantic_status': COMPARABLE if not semantic_reasons else NON_COMPARABLE,
            'measurement_status': status, 'endpoint_comparability': endpoint_status,
            'semantic_reason_codes': sorted(set(semantic_reasons)),
            'readonly_check_external_recorded': ext_check.get('recorded_readonly_check_present'),
            'readonly_check_native_recorded': native_check.get('recorded_readonly_check_present'),
            'unreached_tool_on_retained_failure': {'external': ext.get('success') is False and not ext_check.get('selected_action_present'),
                                                 'native': native.get('success') is False and not native_check.get('selected_action_present')},
            'native_tool_calls_recorded': (native.get('metrics') or {}).get('tool_calls'),
            'external_tool_calls': 'NOT_PROJECTED_AS_SAME_METRIC; lookup/readonly intent verified by bound full-audit predicate',
            'external_scheduler_verification': 'BOUND_FULL_AUDIT_CHECKED_ACTUAL_SCHEDULER_LITERAL_AND_VERSION',
            'failure_outcomes_retained': True})
    return result, dispositions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-out', type=Path, required=True)
    parser.add_argument('--full-audit-dir', type=Path, required=True)
    parser.add_argument('--full-audit-sha256', required=True)
    parser.add_argument('--full-audit-analyzer-sha256', default=FULL_AUDITOR_SHA256)
    parser.add_argument('--quiescence-receipt', type=Path, required=True)
    parser.add_argument('--version', default='external_reference_equivalence_v1')
    args = parser.parse_args()
    out = args.study_out.resolve()
    full_dir = args.full_audit_dir.resolve()
    if (out.name != STUDY or not full_dir.is_relative_to(out / 'analysis')
            or re.fullmatch(r'external_reference_equivalence_v[1-9][0-9]*', args.version) is None
            or not sha256_string(args.full_audit_sha256) or not sha256_string(args.full_audit_analyzer_sha256)):
        parser.error('exact corrected study, its completed audit directory and a new version are required')
    destination = out / 'analysis' / args.version
    if destination.exists():
        parser.error('retain existing comparability audit; choose a new version')
    try:
        timing_gate(out, args.quiescence_receipt)
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        parser.error(type(exc).__name__ + ': timing/quiescence gate rejected analysis')
    with ExitStack() as stack:
        try:
            locks(out, stack)
        except (OSError, ImportError):
            parser.error('existing real execution locks are not available')
        a = Auditor(out)
        destination.mkdir(parents=True)
        matched, dispositions = [], []
        bindings = {'formal_freeze_sha256': FREEZE_SHA256,
                    'full_metric_audit_sha256': args.full_audit_sha256,
                    'full_metric_analyzer_sha256': args.full_audit_analyzer_sha256}
        full_status = 'NOT_READ'
        try:
            a.bind(args.quiescence_receipt)
            freeze = a.json(out / 'manifests/formal-freeze.json', FREEZE_SHA256)
            full = a.json(full_dir / 'metric_audit.json', args.full_audit_sha256)
            full_status = full.get('status')
            # Retain the actual failed/inconclusive receipt bindings even when
            # a global failure prevents opening any selected-unit projection.
            bindings.update(full_metric_output_sha256=full.get('output_sha256'),
                            full_metric_evidence_manifest_sha256=hashlib.sha256(canonical(full.get('evidence'))).hexdigest())
            # A global FAIL stops before reading any output projection. It may
            # not be diluted into a matching subset with local zero failures.
            a.require(full_status in ('PASS_OBSERVED_FULL_RECORD_JOINS', 'INCONCLUSIVE_RETAINED')
                      and full.get('finding_counts', {}).get('FAIL', 0) == 0,
                      'GLOBAL_FULL_AUDIT_FAIL_OR_INVALID_STATUS')
            a.require(full.get('analyzer_sha256') == args.full_audit_analyzer_sha256
                      and full.get('planned_units') == full.get('terminal_units') == full.get('audited_observations_units') == 6504
                      and full.get('study_id') == STUDY, 'ACTUAL_COMPLETED_FULL_AUDIT_AUTHORITY_MISMATCH')
            analyzer_evidence = []
            for name, evidence in full['evidence'].items():
                path = Path(name) if Path(name).is_absolute() else out / name
                a.require(path.resolve().is_relative_to(out) or (path.name == 'full_metric_audit.py'
                          and evidence['sha256'] == args.full_audit_analyzer_sha256), 'FOREIGN_EVIDENCE_PATH_NOT_PERMITTED')
                a.bind(path, evidence['sha256'], evidence['bytes'])
                if path.name == 'full_metric_audit.py' and evidence['sha256'] == args.full_audit_analyzer_sha256:
                    analyzer_evidence.append(str(path.resolve()))
            a.require(len(analyzer_evidence) == 1, 'ACTUAL_FULL_AUDITOR_SOURCE_EVIDENCE_NOT_UNIQUE')
            outputs = full['output_sha256']
            for name in ('findings.json', 'unit_checks.jsonl', 'audited_observations.jsonl'):
                a.bind(full_dir / name, outputs[name])
            findings = a.json(full_dir / 'findings.json', outputs['findings.json'])
            a.require(not any(row.get('level') == 'FAIL' for row in findings), 'FAIL_FINDING_CANNOT_BE_BYPASSED')
            plan_path = out / 'manifests/formal_plan.json'
            corpus_path = out / 'corpus/candidate_v2/tasks.jsonl'
            plan_sha = a.frozen_hash(freeze, plan_path)
            corpus_sha = a.frozen_hash(freeze, corpus_path)
            bindings.update(formal_plan_sha256=plan_sha, corpus_sha256=corpus_sha,
                            source_sha256=freeze['source_sha256'])
            plan = a.json(plan_path, plan_sha)
            a.bind(corpus_path, corpus_sha)
            cases = [json.loads(line) for line in corpus_path.read_text(encoding='utf-8').splitlines()]
            contract_sha = source_contract(a, freeze, out)
            units, pairs = plan_pairs(a, plan, cases, contract_sha)
            selected_ids = {pair[key] for pair in pairs for key in ('external_run_id', 'native_t0_run_id')}
            observed = selected_lines(a, full_dir / 'audited_observations.jsonl', outputs['audited_observations.jsonl'], set(units), selected_ids)
            checks = selected_lines(a, full_dir / 'unit_checks.jsonl', outputs['unit_checks.jsonl'], set(units), selected_ids)
            matched, dispositions = compare(a, pairs, units, observed, checks, findings, freeze['source_sha256'], contract_sha)
            bindings['effective_contract_sha256'] = contract_sha
            a.stable()
        except BaseException as exc:
            a.note('AUDIT_BLOCKED_OR_EXCEPTION_RETAINED', error_type=type(exc).__name__)
        endpoint_summary = {}
        for framework in PHASES.values():
            group = [row for row in matched if row['framework'] == framework]
            endpoint_summary[framework] = {}
            for endpoint in ENDPOINTS:
                bad = [row for row in group if row['endpoint_comparability'][endpoint]['status'] != COMPARABLE]
                comparable = len(group) == 120 and not bad and not a.findings
                endpoint_summary[framework][endpoint] = {'status': COMPARABLE if comparable else NON_COMPARABLE,
                    'matched_pairs_planned': 120, 'matched_pairs_observed': len(group), 'non_comparable_pairs': len(bad),
                    'reason_codes': sorted({reason for row in bad for reason in row['endpoint_comparability'][endpoint]['reason_codes']})
                                    if not a.findings else sorted({row['code'] for row in a.findings})}
        status = COMPARABLE if len(matched) == 240 and not a.findings and all(row['status'] == COMPARABLE for row in matched) else NON_COMPARABLE
        report = {'status': status, 'comparison_scope': 'SYSTEM_LEVEL_REFERENCE',
                  'study_id': STUDY, 'created_utc': datetime.now(timezone.utc).isoformat(),
                  'full_metric_status_retained': full_status, 'bindings': bindings,
                  'matched_pairs_planned': 240, 'matched_pairs_observed': len(matched),
                  'native_T0_unique_ids_planned': 120, 'endpoint_summary': endpoint_summary,
                  'unknown_dispositions': dispositions, 'inputs': a.inputs,
                  'analyzer_sha256': digest(Path(__file__)), 'no_old_real_or_retrieval_results_read': True,
                  'statistics_produced': False, 'mean_ci_p_or_holm_produced': False,
                  'inference': 'audited shared facts/contract/model/intent and measurement boundaries support only a bundled system-level reference',
                  'delegated_observation_evidence': 'hash-bound full-audit predicates verified actual fact inventories, HTTP boundaries, fixture checks, scheduler/version, role and lookup counters; literals are not newly measured here',
                  'disclosed_differences': [
                      'Native T0 uses native lean inbound wire; both external frameworks use their fixed JSON handoff. Prompt strings/representation are not assumed identical to native.',
                      'AutoGen broadcasts role output/transcript; LangGraph merges typed graph state without checkpointer. Framework local state is not native SS state exchange.',
                      'Native T0 uses runtime UDS control with state/memory disabled; external roles share one process with no native interprocess role transport.',
                      'Native execution can stop on executor/model failure; external deterministic reviewer still receives retained executor failures. Actual tool/counter definitions and failure routing are not equated.',
                      'Provider retries/usage/wait and framework setup/cleanup are included as actually recorded; differences are not attributed to one mechanism.',
                      'A tool not reached on a retained failed model output is not recorded as a zero tool-cost measurement. No native/external tool-count equality is invented.',
                      'No task-quality failure is repaired, dropped or required to become success. Missing cost/usage or applicable semantic evidence is NON_COMPARABLE, not zero.',
                      'Cached usage, resources, IPC decomposition, seed write timing and other unrequested measures retain their own full-audit limits; this audit covers only the three original reference endpoints.',
                      'No causal superiority, equal prompt serialization, matched scheduler overhead, quality noninferiority, mean/CI/p or second Holm family is claimed.']}
        with (destination / 'matched_units.jsonl').open('x', encoding='utf-8') as stream:
            for row in matched:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        with (destination / 'findings.json').open('x', encoding='utf-8') as stream:
            json.dump(a.findings, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        report['output_sha256'] = {name: digest(destination / name) for name in ('matched_units.jsonl', 'findings.json')}
        with (destination / 'audit.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print(json.dumps({'status': status, 'matched_pairs_observed': len(matched), 'blocked_findings': len(a.findings)}))
        return 0 if status == COMPARABLE else 2


if __name__ == '__main__':
    raise SystemExit(main())
