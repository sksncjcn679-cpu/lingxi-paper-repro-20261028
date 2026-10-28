"""Build metadata-only evidence locators. No scores, estimates, CI or tests.

Reads the saved audited projection, frozen metadata and small saved result assets.
Never opens authoritative raw payload streams, model I/O, prompts or secrets.
CSV body cells are JSON values; header cells are ordinary column names.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

STUDY = 'protocol_repair_20261020_v1'
VIRTUAL = '/evidence/' + STUDY
PRIMARY_SHA = '5a05487490072f1989a02b2bd99f39c1bfa07acba0f31af96a8579e73ddfaa2a'
FREEZE_SHA = '2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e'
PROJECTION_SHA = '1c2031eec1ad922c753199fc4cc0a7760fdeca0f110becc6d2e83db0dc12281a'
META = ('study_id phase task_id chain_id domain difficulty arm repeat model model_id '
        'topology processes intervention seed source_sha256 task_sha256 effective_contract_sha256 '
        'model_evidence unit_id run_id status success provider_usage_complete error_type provider_error_types '
        'raw_file raw_line raw_line_sha256').split()
IDENTITY = ('study_id phase task_id chain_id domain difficulty arm repeat model model_id topology '
            'processes intervention seed task_sha256 effective_contract_sha256 unit_id run_id').split()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate JSON key')
            result[k] = v
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs)


def write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def csv_json(path, rows, fields):
    with Path(path).open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical(row.get(k)) for k in fields})


def ids_sha(ids):
    # Exactly the frozen statistical entry's ID serialization.
    return hashlib.sha256(json.dumps(sorted(set(ids)), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--study-out', type=Path, required=True)
    ap.add_argument('--report-root', type=Path, required=True)
    ap.add_argument('--offline-manifest', type=Path, required=True)
    ap.add_argument('--archive-input-manifest', type=Path, required=True)
    args = ap.parse_args()
    study = args.study_out.resolve()
    if study.name != STUDY:
        raise ValueError('exact corrected study required')
    out = Path(__file__).resolve().parent
    if out != study / 'analysis/claims_raw_index_v1' or any(p.name != Path(__file__).name for p in out.iterdir()):
        raise ValueError('fresh output folder containing only this script required; retain partial failures')
    report = args.report_root.resolve()
    inputs = {}

    def bind(path, expected=None, label=None):
        path = Path(path)
        value = digest(path)
        if expected is not None and value != expected:
            raise ValueError('input identity mismatch: ' + path.name)
        inputs[label or str(path)] = {'path': str(path), 'sha256': value, 'bytes': path.stat().st_size}
        return value

    primary_path = study / 'analysis/stats/formal_primary_v1.json'
    freeze_path = study / 'manifests/formal-freeze.json'
    contract_path = study / 'analysis/stats/preformal_gate_v1/statistical_contract.json'
    plan_path = study / 'manifests/formal_plan.json'
    audit_path = study / 'analysis/full_metric_audit_v3/metric_audit.json'
    bind(primary_path, PRIMARY_SHA); primary = read(primary_path)
    bind(freeze_path, FREEZE_SHA); freeze = read(freeze_path)
    bind(contract_path, primary['statistical_contract_sha256']); contract = read(contract_path)
    assert freeze['files'][VIRTUAL + '/analysis/stats/preformal_gate_v1/statistical_contract.json'] == digest(contract_path)
    assert contract['formal_outcomes_used'] is False
    bind(plan_path, primary['input_sha256']['manifests/formal_plan.json']); plan = read(plan_path)
    bind(audit_path); audit = read(audit_path)
    assert audit['status'] == 'INCONCLUSIVE_RETAINED' and audit['finding_counts'] == {'UNKNOWN': 1547}
    assert audit['output_sha256']['audited_observations.jsonl'] == PROJECTION_SHA
    planned = {r['unit_id']: r for r in plan}
    assert len(plan) == len(planned) == 6504
    public_ids = {r['unit_id'] for r in plan if r['phase'] == 'public_calibration'}
    assert len(public_ids) == 144
    main_plan = [r for r in plan if r['phase'] == 'main']
    formal_ids = sorted({r['task_id'] for r in main_plan})
    state_ids = contract['applicable_task_ids']
    assert len(formal_ids) == 120 and len(state_ids) == 108 and set(state_ids) < set(formal_ids)
    assert ids_sha(state_ids) == contract['applicable_task_ids_sha256'] == primary['applicable_task_ids_sha256']
    assert ids_sha(formal_ids) == contract['formal_task_ids_sha256']
    assert len(main_plan) == 2160 and len({r['chain_id'] for r in main_plan}) == 20
    assert {r['repeat'] for r in main_plan} == {0, 1, 2}
    assert {(r['model'], r['model_id'], r['topology'], r['processes']) for r in main_plan} == {('main', 'qwen3.7-plus', 'runtime_uds', False)}

    projection_path = study / 'analysis/full_metric_audit_v3/audited_observations.jsonl'
    projection_start = projection_path.stat()
    h = hashlib.sha256(); observed = []; by_id = {}
    with projection_path.open('rb') as stream:
        for physical_line, line in enumerate(stream, 1):
            h.update(line)
            if not line.strip():
                raise ValueError('unexpected blank projection record')
            r = json.loads(line)
            assert r['schema_version'] == 'audited-observations-projection-v1'
            unit = r['unit_id']; p = planned[unit]
            assert unit not in by_id and all(r.get(k) == p.get(k) for k in IDENTITY)
            assert r['source_sha256'] == freeze['source_sha256']
            assert r['raw_file'] == 'task_runs.jsonl' and type(r['raw_line']) is int and r['raw_line'] > 0
            assert re.fullmatch('[0-9a-f]{64}', r['raw_line_sha256'])
            safe = {k: r.get(k) for k in META}
            safe.update(index_record_number=len(observed) + 1,
                public_calibration=unit in public_ids,
                formal_collection_member=unit not in public_ids,
                primary_inferential_eligible=r['phase'] == 'main',
                frozen_state108_task=r['task_id'] in state_ids,
                projection_physical_line=physical_line,
                projection_physical_line_sha256=hashlib.sha256(line).hexdigest(),
                audited_record_sha256=hashlib.sha256(canonical(r).encode()).hexdigest(),
                source_present_metadata_fields=[k for k in META if k in r])
            observed.append(safe); by_id[unit] = safe
    assert h.hexdigest() == PROJECTION_SHA
    assert projection_path.stat().st_size == projection_start.st_size and projection_path.stat().st_mtime_ns == projection_start.st_mtime_ns
    assert len(observed) == 6504 and set(by_id) == set(planned)
    assert {r['raw_line'] for r in observed} == set(range(1, 6505))
    inputs[str(projection_path)] = {'path': str(projection_path), 'sha256': h.hexdigest(), 'bytes': projection_start.st_size, 'read_mode': 'one streaming pass, metadata allowlist only; no raw payload files opened'}
    all_fields = list(observed[0])
    csv_json(out / 'observed_unit_raw_index.csv', observed, all_fields)

    main_rows = [r for r in observed if r['phase'] == 'main']
    assert len(main_rows) == 2160 and not any(r['public_calibration'] for r in main_rows)
    main_key = {(r['task_id'], r['repeat'], r['arm'], r['model_id'], r['topology']): r for r in main_rows}
    assert len(main_key) == 2160
    pair_definitions = []; pair_rows = []
    for ci, c in enumerate(primary['strata'][0]['comparisons']):
        pair_id = c['baseline'] + '_' + c['target']
        distribution = c['metrics']['observable_ipc_bytes']['task_pair_distribution']
        task_ids = [d['task_id'] for d in distribution]
        expected = state_ids if ci == 1 else formal_ids
        assert set(task_ids) == set(expected) and len(task_ids) == len(expected)
        assert all(d['repeat_count'] == 3 for d in distribution)
        for v in [c['quality'], *c['metrics'].values()]:
            if 'task_pair_distribution' in v:
                assert {d['task_id'] for d in v['task_pair_distribution']} == set(expected)
        assert c['planned_paired_units'] == len(expected) * 3
        pointer = '/strata/0/comparisons/' + str(ci)
        definition = {'pair_id': pair_id, 'comparison_index': ci, 'baseline': c['baseline'], 'target': c['target'],
            'primary_pointer': pointer, 'task_distribution_pointer': pointer + '/metrics/observable_ipc_bytes/task_pair_distribution',
            'quality_pointer': pointer + '/quality', 'all_metric_pointers': {k: pointer + '/metrics/' + k for k in c['metrics']},
            'task_ids': task_ids, 'planned_task_count': len(expected), 'planned_repeats': [0, 1, 2],
            'planned_paired_units': c['planned_paired_units'], 'planned_chains': c['planned_chains'],
            'phase': 'main', 'model': 'main', 'model_id': 'qwen3.7-plus', 'topology': 'runtime_uds', 'processes': False,
            'population': c['population'], 'population_selection': c['population_selection'],
            'state_population_authority': str(contract_path) + '#/applicable_task_ids' if ci == 1 else 'frozen formal main-plan task IDs',
            'token_INCOMPLETE_does_not_remove_any_unit': True, 'success_or_usage_filter': None,
            'supports': ['figure_02.csv', 'context for figure_03.csv / figure_04.csv / table_03.csv; absolute groups retain their own120-task denominators']}
        pair_definitions.append(definition)
        for ti, task in enumerate(task_ids):
            for repeat in (0, 1, 2):
                a = main_key[(task, repeat, c['baseline'], 'qwen3.7-plus', 'runtime_uds')]
                b = main_key[(task, repeat, c['target'], 'qwen3.7-plus', 'runtime_uds')]
                assert all(a[k] == b[k] for k in ['task_id', 'chain_id', 'domain', 'repeat', 'model', 'model_id', 'topology', 'processes', 'task_sha256', 'source_sha256'])
                item = {'pair_id': pair_id, 'primary_pointer': pointer, 'task_distribution_row_pointer': definition['task_distribution_pointer'] + '/' + str(ti),
                    'task_id': task, 'chain_id': a['chain_id'], 'domain': a['domain'], 'repeat': repeat,
                    'model': a['model'], 'model_id': a['model_id'], 'topology': a['topology'], 'processes': a['processes'],
                    'source_sha256': a['source_sha256'], 'task_sha256': a['task_sha256'], 'raw_file': 'raw/task_runs.jsonl',
                    'baseline_arm': a['arm'], 'target_arm': b['arm']}
                for side, row in [('baseline', a), ('target', b)]:
                    for k in ['unit_id', 'run_id', 'raw_line', 'raw_line_sha256', 'index_record_number', 'status', 'success', 'provider_usage_complete']:
                        item[side + '_' + k] = row[k]
                pair_rows.append(item)
    assert len(pair_rows) == 1764
    csv_json(out / 'primary_pair_raw_support_index.csv', pair_rows, list(pair_rows[0]))
    write(out / 'primary_pair_selectors.json', {'primary_path': str(primary_path), 'primary_sha256': PRIMARY_SHA,
        'frozen_state_contract_sha256': digest(contract_path), 'applicable_task_ids_sha256': ids_sha(state_ids),
        'formal_task_ids_sha256': ids_sha(formal_ids), 'pairs': pair_definitions,
        'scope': 'locator populations only; no estimate, quality score, CI, p, Holm or success-only analysis computed'})

    availability_path = report / 'analysis/report_availability.json'
    bind(availability_path); availability = read(availability_path)
    assert availability['status'] == 'PREPARED_REQUIRES_POSTRUN_SCHEMA_NUMERIC_AND_ACTUAL_VISUAL_QA'
    csv_assets = {}
    for rel in ['figures/figure_02.csv', 'figures/figure_03.csv', 'figures/figure_04.csv', 'figures/figure_05.csv',
                'figures/figure_06.csv', 'figures/figure_07.csv', 'figures/figure_08.csv', 'figures/figure_09.csv',
                'tables/table_03.csv', 'tables/table_05.csv', 'tables/table_06.csv']:
        bind(report / rel, availability['outputs_sha256'][rel])
        with (report / rel).open(encoding='utf-8-sig', newline='') as stream:
            csv_assets[rel] = list(csv.DictReader(stream))
    matched_path = report / 'analysis/matched_observation_index.json'
    if not matched_path.exists():
        matched_path = report.parent / 'analysis/postrun/original_reference_descriptives_v1/matched_observation_index.json'
    bind(matched_path); matched = read(matched_path)
    systems = {}
    for row in matched['rows']:
        obs = by_id[row['run_id']]
        assert row['raw_line'] == obs['raw_line'] and row['raw_line_sha256'] == obs['raw_line_sha256'] and not obs['public_calibration']
        systems.setdefault(row['system'], []).append(obs['index_record_number'])
    assert set(systems) == {'native_T0', 'autogen', 'langgraph'} or set(systems) == {'native_T0', 'AutoGen', 'LangGraph'}
    assert all(len(v) == 120 for v in systems.values())

    row_selectors = []
    for rel in ['figures/figure_02.csv', 'figures/figure_03.csv', 'figures/figure_04.csv', 'tables/table_03.csv', 'figures/figure_09.csv']:
        for ordinal, row in enumerate(csv_assets[rel], 1):
            selector = {'phase': 'main', 'model_id': 'qwen3.7-plus', 'topology': 'runtime_uds', 'public_calibration': False}
            supporting = main_rows
            note = 'all planned group units retained, including failures and unknown usage'
            if rel.endswith('figure_02.csv'):
                pair = next(d for d in pair_definitions if (d['baseline'], d['target']) == (row['baseline'], row['target']))
                selector.update(pair_id=pair['pair_id'], task_id_in=pair['task_ids'], arm_in=[pair['baseline'], pair['target']], repeats=[0, 1, 2])
                supporting = [r for r in main_rows if r['task_id'] in pair['task_ids'] and r['arm'] in selector['arm_in']]
                note = 'same full planned pair population for incomplete token endpoint; no measured/complete-case filtering'
            elif rel.endswith('figure_09.csv'):
                key = row['system']
                # Saved CSV may spell the display label differently from the metadata authority.
                normalized = {'native_t0': 'native_T0', 'autogen': 'autogen', 'langgraph': 'langgraph'}
                system_key = next(k for k in systems if k.casefold() == normalized.get(key.casefold(), key).casefold())
                record_numbers = systems[system_key]
                supporting = [r for r in observed if r['index_record_number'] in record_numbers]
                selector = {'system': system_key, 'matched_observation_authority': str(matched_path), 'public_calibration': False,
                    'exact_saved_run_ids': [r['run_id'] for r in supporting]}
                note = 'fixed60tasks/two repeats matched system reference; no success-only filtering, no IPC mechanism equivalence'
            else:
                if row.get('arm'):
                    selector['arm'] = row['arm']; supporting = [r for r in supporting if r['arm'] == row['arm']]
                if row.get('dimension') == 'domain':
                    selector['domain'] = row['level']; supporting = [r for r in supporting if r['domain'] == row['level']]
                if row.get('kind') == 'recorded_failure_type_count':
                    selector.update(success=False, error_type_or_UNKNOWN_RECORDED_ERROR_TYPE=row['error_type'])
                    supporting = [r for r in supporting if r['success'] is False and (r['error_type'] or 'UNKNOWN_RECORDED_ERROR_TYPE') == row['error_type']]
                    note = 'descriptive recorded failure-type support only; this outcome selector never defines a formal inferential population'
            assert supporting and not any(r['public_calibration'] for r in supporting)
            row_selectors.append({'asset': rel, 'saved_csv_row_number': ordinal,
                'saved_csv_string_cells_sha256': hashlib.sha256(canonical(row).encode()).hexdigest(),
                'saved_source_pointer': row.get('source_pointer') or None, 'metric_or_kind': row.get('metric') or row.get('kind'),
                'selector': selector, 'observed_index_record_numbers': [r['index_record_number'] for r in supporting],
                'supported_unit_count': len(supporting), 'primary_context_pointers': [d['primary_pointer'] for d in pair_definitions if row.get('arm') in (d['baseline'], d['target'])],
                'note': note})
    with (out / 'figure_table_row_selectors.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
        for row in row_selectors:
            stream.write(canonical(row) + '\n')

    phase_selectors = []
    for phase in sorted({r['phase'] for r in observed} - {'public_calibration'}):
        group = [r for r in observed if r['phase'] == phase]
        phase_selectors.append({'selector_id': 'phase:' + phase, 'predicate': {'phase': phase, 'public_calibration': False},
            'index_record_numbers': [r['index_record_number'] for r in group], 'unit_count': len(group),
            'task_count': len({r['task_id'] for r in group}), 'chain_count': len({r['chain_id'] for r in group}),
            'model_ids': sorted({r['model_id'] for r in group}), 'topologies': sorted({r['topology'] for r in group}),
            'arms': sorted({r['arm'] for r in group}), 'repeats': sorted({r['repeat'] for r in group}),
            'primary_saved_pointer': '/strata/0' if phase == 'main' else '/formal_phase_statistics/' + phase,
            'inference_scope': 'sole primary family only' if phase == 'main' else 'preplanned exploratory phase; not a new confirmatory family'})
    exploratory = []
    for name, comparison in primary['matched_phase_effects'].items():
        phases = [comparison['baseline'], comparison['target']]
        groups = [[r for r in observed if r['phase'] == phase] for phase in phases]
        keys = [{(r['task_id'], r['repeat'], r['model_id'], r['topology']) for r in g} for g in groups]
        assert keys[0] == keys[1] and len(keys[0]) == comparison['planned_paired_units']
        exploratory.append({'selector_id': 'matched:' + name, 'saved_primary_pointer': '/matched_phase_effects/' + name,
            'phase_in': phases, 'pair_keys': ['task_id', 'repeat', 'model_id', 'topology'],
            'baseline_index_record_numbers': [r['index_record_number'] for r in groups[0]],
            'target_index_record_numbers': [r['index_record_number'] for r in groups[1]],
            'planned_paired_units': comparison['planned_paired_units'], 'success_or_usage_filter': None,
            'scope': 'exploratory frozen-phase support; wrong/stale/unrelated and failed units retained'})

    bind(args.offline_manifest); offline = read(args.offline_manifest)
    assert offline['status'] == 'APPROVED_OFFLINE_BYTE_ASSET_MANIFEST'
    old = []
    for batch in offline['batches']:
        relevant = [a for a in batch['assets'] if a.get('target_figure') in [5, 6, 8]]
        old.append({'selector_id': 'historical:' + batch['batch_id'], 'evidence_class': batch['evidence_class'],
            'source_root': batch['source_root'], 'source_sha256': batch['original_source_sha256'],
            'raw_file_hash_manifest_entries': batch['raw_hash_references'], 'approved_assets': relevant,
            'batch_asset_selector': {'batch_id': batch['batch_id'], 'topics': sorted({a['topic'] for a in batch['assets']})},
            'saved_figure_cell_selectors': [{'report_csv': f'figures/figure_{n:02d}.csv', 'row_number': i,
                'exact_saved_cell_key': {k: row[k] for k in keys}, 'row_cells_sha256': hashlib.sha256(canonical(row).encode()).hexdigest()}
                for n, keys in [(5, ['path', 'condition', 'logical_bytes']), (6, ['dimension', 'distribution', 'precision']), (8, ['length', 'concurrency'])]
                if any(a.get('target_figure') == n for a in relevant) for i, row in enumerate(csv_assets[f'figures/figure_{n:02d}.csv'], 1)],
            'scope': batch['measurement_scope'], 'limitations': batch['limitations'],
            'granularity': 'exact approved historical raw file-set + saved CSV cell keys; no new per-raw-line join or mechanism/scientific PASS asserted'})
    bind(args.archive_input_manifest); archive_inputs = read(args.archive_input_manifest)
    capacity_path = report.parent / 'analysis/retrieval_capacity_analysis_v1/audit.json'
    bind(capacity_path); capacity = read(capacity_path)
    rag = []
    for ordinal, row in enumerate(csv_assets['figures/figure_07.csv'], 1):
        size = int(row['pool_size']); files = []
        for build in (1, 2, 3):
            name = f'raw/retrieval_n{size}_build{build}.jsonl'
            evidence = capacity['evidence'][name]
            assert archive_inputs['virtual_inputs'][VIRTUAL + '/' + name]['sha256'] == evidence['sha256']
            files.append({'relative_path': name, 'build': build, **evidence})
        rag.append({'selector_id': f'rag:{size}:{row["mode"]}', 'figure_csv_row_number': ordinal,
            'figure_csv_cells_sha256': hashlib.sha256(canonical(row).encode()).hexdigest(),
            'raw_key_predicate': {'pool_size': size, 'mode': row['mode'], 'build_in': [1, 2, 3]},
            'raw_record_identity_keys': ['pool_size', 'build', 'mode', 'query_id'],
            'exact_raw_file_hash_manifest_entries': files,
            'saved_recorded_query_units': row['recorded_query_units'], 'saved_missing_query_units': row['missing_query_units'],
            'saved_report_cell_status': row['report_cell_status'],
            'scope': 'selector/file-hash level only; missing query records are absent, not synthesized or zero-filled'})
    ancillary = {name: audit['evidence'][name] for name in ['raw/task_runs.jsonl', 'raw/provider_calls.jsonl',
        'raw/states.jsonl', 'raw/memory_events.jsonl', 'raw/resource_samples.jsonl', 'raw/spans.jsonl']}
    write(out / 'claim_source_selectors.json', {'scope': 'core quantitative claims and explicit existing evidence selectors; not every possible manuscript claim',
        'primary_file': str(primary_path), 'report_root': str(report), 'phase_selectors': phase_selectors,
        'exploratory_matched_selectors': exploratory, 'external_system_saved_run_id_selectors': systems,
        'ancillary_raw_join_rule': 'exact run_id equality to the selected observed_unit_raw_index rows; retain all events, failures and unknowns; no success-only or usage-complete restriction',
        'ancillary_exact_raw_hash_manifest': ancillary,
        'memory_event_selector': {'relative_file': 'raw/memory_events.jsonl', 'phase': 'main', 'arm_in': ['T1', 'S1', 'SS1'],
            'event': 'retrieve', 'join': 'run_id equals selected index run_id', 'saved_facts_source': str(report / 'analysis/saved_descriptive_facts.json'),
            'scope': 'existing candidate/adoption/feedback support; hit/adoption/after-adoption success are separate descriptive events, not causal benefits'},
        'historical_offline_selectors': old, 'retrieval_selectors': rag,
        'public_exclusion': {'public_unit_count': 144, 'all_primary_and_formal_phase_selectors_exclude': 'public_calibration=true',
            'public_index_rows_retained_for_audit_only': True},
        'limits': ['No new estimates, CI, p, Holm, effect ranking, quality scoring or inferred provider token values.',
            'Only terminal task raw lines are enumerated per unit. Ancillary events use exact run_id joins and audit file hashes; RAG/historical assets use exact file/cell selectors, not new raw-line verification.',
            'Raw line hashes are copied from the completed full audit, not independently re-read from raw payload files in this locator build.',
            'RAG54 missing and audit1547UNKNOWN remain. Index presence/hash/CRC is never semantic or scientific PASS.']})
    bind(Path(__file__), label='index_builder_source')
    bind(report / 'analysis/saved_descriptive_facts.json')
    write(out / 'inputs_manifest.json', {'schema_version': 'claims-raw-index-inputs-v1', 'inputs': inputs,
        'raw_payload_files_opened': 0, 'model_or_API_calls': 0, 'science_statistics_recomputed': False})
    notes = ('# 主张到原记录的定位索引\n\n'
        '本目录只链接已保存原值，不计算新估计、CI、p、Holm或质量分数。6504条终态包含144 public；primary与正式phase selector明确排除public。\n\n'
        'CSV表头为普通列名，每个正文cell是一个JSON值；用csv.DictReader后对每个cell调用json.loads。null与缺字段由source_present_metadata_fields区分。index_record_number为数据记录序号，表头不计。\n\n'
        'observed_unit_raw_index.csv列出task_runs.jsonl的1-based raw_line及已审raw_line_sha256（含原物理行换行字节的SHA），另附投影物理行SHA及完整投影record canonical SHA；只输出元数据allowlist。\n\n'
        'primary_pair_raw_support_index.csv是1764个完整预定配对（360/324/360/360/360），含双方unit/run/raw行/hash。108状态人群只用预结局冻结statistical_contract/applicable_task_ids及primary任务分布；不看观测state_consumed或success选人群。\n\n'
        'figure_table_row_selectors.jsonl逐行绑定图02/03/04/09与表03 CSV的原string-cell SHA、原source_pointer及observed索引记录序号。图03失败类型筛选仅支持描述性失败计数，绝不定义主推断人群。图03/04/表03的绝对整组与图02的配对108子集分开。\n\n'
        'claim_source_selectors.json提供其他phase、14个匹配消融/记忆/codec、外部固定60任务×2、旧offline精确文件hash/CSV cell、RAG20cells的精确raw文件和key谓词。旧offline不混入新模型或新RAG结果。\n\n'
        '本轮只读保存的45.7MB审计投影及冻结/来源小元数据；未打开task_runs/provider/model_io/prompt原始内容、未重验大raw或重跑统计。逐行raw hash来自已有实际审计，索引/哈希存在性不构成semantic或科学PASS。\n')
    (out / 'README.md').write_text(notes, encoding='utf-8')
    products = {p.name: {'bytes': p.stat().st_size, 'sha256': digest(p)} for p in sorted(out.iterdir()) if p.is_file()}
    failed = sum(r['success'] is False for r in observed)
    unknown_usage = sum(r['provider_usage_complete'] is not True for r in observed)
    write(out / 'coverage_receipt.json', {'schema_version': 'claims-raw-index-coverage-v1',
        'created_utc': datetime.now(timezone.utc).isoformat(), 'status': 'COMPLETE_METADATA_LOCATORS_NOT_SCIENTIFIC_PASS',
        'actual_terminal': True, 'script_sha256': digest(Path(__file__)), 'products': products,
        'coverage': {'observed_index_rows': 6504, 'distinct_unit_ids': 6504, 'distinct_run_ids': len({r['run_id'] for r in observed}),
            'distinct_task_run_raw_lines': 6504, 'raw_line_coverage': 'every physical line1..6504, auditor-issued SHA retained',
            'public_rows': 144, 'formal_nonpublic_rows': 6360, 'main_rows': 2160,
            'main_tasks': 120, 'main_chains': 20, 'main_repeats': [0, 1, 2],
            'main_model_id': 'qwen3.7-plus', 'main_topology': 'runtime_uds', 'main_processes': False,
            'state_frozen_tasks': 108, 'primary_pair_rows': 1764,
            'primary_pair_counts': dict(Counter(r['pair_id'] for r in pair_rows)),
            'failed_terminal_rows_retained': failed, 'usage_unknown_rows_retained': unknown_usage,
            'figure_table_selector_rows': len(row_selectors), 'phase_selectors': len(phase_selectors),
            'exploratory_matched_selectors': len(exploratory), 'retrieval_cell_selectors': len(rag), 'historical_batch_selectors': len(old),
            'public_in_primary_or_formal_selector': 0},
        'identities': {'formal_freeze_sha256': FREEZE_SHA, 'primary_sha256': PRIMARY_SHA,
            'audited_projection_sha256': PROJECTION_SHA, 'statistical_contract_sha256': digest(contract_path),
            'state_task_ids_sha256': ids_sha(state_ids), 'formal_task_ids_sha256': ids_sha(formal_ids),
            'task_runs_raw_sha256_from_completed_audit': audit['evidence']['raw/task_runs.jsonl']['sha256']},
        'no_new_estimate_CI_p_Holm_or_quality_score': True, 'no_new_model_API_benchmark_or_rebuild': True,
        'raw_payload_files_opened': 0, 'semantic_or_scientific_PASS_claimed': False,
        'limits': ['Locator granularity and limits are explicit in README/claim_source_selectors. Not a new semantic audit or independent raw line rehash.',
            'Original UNKNOWN1547/RAG54 missing/failed outcomes/usage uncertainty and noninferiority WITHHELD remain unchanged.']})
    print(json.dumps({'status': 'COMPLETE_METADATA_LOCATORS_NOT_SCIENTIFIC_PASS', 'output': str(out),
        'coverage_receipt_sha256': digest(out / 'coverage_receipt.json'), 'rows': 6504, 'pair_rows': 1764}, ensure_ascii=False))


if __name__ == '__main__':
    main()
