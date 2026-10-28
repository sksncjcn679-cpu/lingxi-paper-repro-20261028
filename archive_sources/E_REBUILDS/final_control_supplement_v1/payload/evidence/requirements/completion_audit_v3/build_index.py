"""Read saved metadata only; write a new 155-row evidence index, never science outputs."""
from __future__ import annotations

import collections
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

WORK = Path(__file__).resolve().parents[4]
NEW = WORK / 'paper_experiments_20261019_pilot/protocol_repair_20261020_v1'
OUT = NEW / 'analysis/completion_audit_v3'
B8 = Path('E:/codex_rebuilds/corrected_postrun_v2')
REF = B8 / 'reference/virtual/evidence/protocol_repair_20261020_v1'
V2 = NEW / 'analysis/completion_audit_v2'


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_json(p: Path):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def canonical(v) -> bytes:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def write_json(name: str, v):
    (OUT / name).write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


PATHS = {
    'plan': WORK / '论文实验设计方案.md',
    'v2_csv': V2 / 'requirement_evidence.csv',
    'v2_receipt': V2 / 'audit_receipt.json',
    'protocol': NEW / 'protocol.yaml',
    'freeze': NEW / 'manifests/formal-freeze.json',
    'execution': NEW / 'formal_execution_v1/execution.json',
    'budget': NEW / 'manifests/budget_limits.json',
    'preformal_budget': NEW / 'analysis/stats/preformal_gate_v1/preformal_budget_estimate.json',
    'preformal_power': NEW / 'analysis/stats/preformal_gate_v1/prospective_power.json',
    'preformal_gate': NEW / 'analysis/stats/preformal_gate_v1/gate.json',
    'automatic_authorization': WORK / 'paper_experiments_20261019_pilot/formal_20261019/manifests/protocol_changes.md',
    'shared_preflight_report': WORK / 'paper_experiments_20261019_pilot/formal_20261019/preflight_report.md',
    'shared_preflight_commands': WORK / 'paper_experiments_20261019_pilot/formal_20261019/analysis/preflight_commands.json',
    'shared_os_probe': WORK / 'paper_experiments_20261019_pilot/formal_20261019/logs/preflight/os-probe.stdout.log',
    'new_preflight': NEW / 'public_pilot_v1/preflight.json',
    'new_dependency_versions': NEW / 'public_pilot_v1/dependencies.stdout.log',
    'frozen_native_driver': NEW / 'formal_snapshot_v1/drivers/real_driver.py',
    'tasks': NEW / 'corpus/candidate_v2/tasks.jsonl',
    'queries': NEW / 'corpus/candidate_v2/queries.jsonl',
    'corpus_sources': NEW / 'corpus/candidate_v2/sources.json',
    'metric': REF / 'analysis/full_metric_audit_v3/metric_audit.json',
    'metric_findings': REF / 'analysis/full_metric_audit_v3/findings.json',
    'lifecycle_comparison': NEW / 'analysis/state_lifecycle_reaudit_comparison_v1.json',
    'primary': REF / 'analysis/stats/formal_primary_v1.json',
    'secondary': REF / 'analysis/postrun/secondary_heterogeneity_v1/receipt.json',
    'coverage': REF / 'analysis/postrun/secondary_heterogeneity_v1/coverage.json',
    'references': REF / 'analysis/postrun/original_reference_descriptives_v1/receipt.json',
    'equivalence': REF / 'analysis/external_reference_equivalence_v1/audit.json',
    'retrieval': REF / 'analysis/retrieval_postrun_analysis_v1/audit.json',
    'capacity': REF / 'analysis/retrieval_capacity_analysis_v1/audit.json',
    'format': REF / 'exports/protocol_format_v1/export_audit.json',
    'resource_schema': REF / 'exports/protocol_format_v1/resource_samples.schema.json',
    'resource_io_current_probe': WORK / '.local/resource_io_boundary_probe_v1/stdout.json',
    'resource_io_probe_receipt': WORK / '.local/resource_io_boundary_probe_v1/execution_receipt.json',
    'resource_io_boundary_review': NEW / 'analysis/resource_io_boundary_review_v1.json',
    'messages_schema': REF / 'exports/protocol_format_v1/messages.schema.json',
    'report': REF / 'report_adapter_v1/analysis/report_availability.json',
    'offline': REF / 'report_adapter_v1/analysis/approved_offline_manifest.json',
    'all14': REF / 'report_adapter_v1/appendix/all14_matched_phase_effects.json',
    'b8_execution': B8 / 'execution.json',
    'b8_comparison': B8 / 'numerical_identity.json',
    'b8_root': NEW / 'analysis/postrun_rebuild_v2_root_review_v1.json',
    'b8_terminal': NEW / 'analysis/postrun_rebuild_v2_terminal_tool_witness_v1.json',
    'b8_peer': WORK / '.local/final_scientific_review_v1/postrun_rebuild_v2_peer_review_v1.json',
    'dependency': Path('E:/codex_rebuilds/dependency_stage_v3/dependency_attestation_v1.json'),
    'input_packages': Path('E:/codex_rebuilds/delivery_packages_v2/packaging_receipt.json'),
    'package_manifest': B8 / 'archive/package_manifest.json',
    'single_qa': WORK / '.local/chinese_figure_visual_qa_v1/single/final_v6/visual_qa_receipt.json',
    'double_qa': WORK / '.local/chinese_figure_visual_qa_v1/double/final_v6/visual_qa_result.json',
    'numeric_qa': WORK / '.local/chinese_figure_presentation_v1/root_file_numeric_review_v3.json',
    'english_qa': WORK / '.local/english_figure_visual_qa_v1/visual_qa_receipt.json',
    'chinese_package': NEW / 'delivery/chinese_figures_v1/package_receipt.json',
    'publication': WORK / '.local/chinese_figure_publication_20261021_v1/publication_verification.json',
    'readme': WORK / '.local/final_closeout_draft_v3/README_reproduce.md',
    'claims': WORK / '.local/final_closeout_draft_v3/claims_to_evidence.md',
    'limitations': WORK / '.local/final_closeout_draft_v3/limitations.md',
    'claims_index_receipt': NEW / 'analysis/claims_raw_index_v1/coverage_receipt.json',
    'unit_raw_index': NEW / 'analysis/claims_raw_index_v1/observed_unit_raw_index.csv',
    'pair_raw_index': NEW / 'analysis/claims_raw_index_v1/primary_pair_raw_support_index.csv',
    'pair_selectors': NEW / 'analysis/claims_raw_index_v1/primary_pair_selectors.json',
    'claim_selectors': NEW / 'analysis/claims_raw_index_v1/claim_source_selectors.json',
    'figure_row_selectors': NEW / 'analysis/claims_raw_index_v1/figure_table_row_selectors.jsonl',
    'aux_identity': Path('E:/codex_rebuilds/both_success_auxiliary_v1/exact_identity_receipt.json'),
    'aux_results': Path('E:/codex_rebuilds/both_success_auxiliary_v1/reference/analysis/results.json'),
    'aux_summary': Path('E:/codex_rebuilds/both_success_auxiliary_v1/reference/analysis/summary.csv'),
    'aux_peer': Path('E:/codex_rebuilds/both_success_auxiliary_v1/peer_review_v1.json'),
    'quality_projection_receipt': NEW / 'analysis/automatic_quality_submetrics_v1/receipt.json',
    'quality_projection_table': NEW / 'analysis/automatic_quality_submetrics_v1/recorded_quality_fields.csv',
    'quality_storage_review': NEW / 'analysis/automatic_quality_submetrics_v1/source_storage_review_v1.json',
    'quality_scope_correction': NEW / 'analysis/automatic_quality_submetrics_v1/correction_note_v1.json',
    'response_identity': Path('E:/codex_rebuilds/original_response_format_recovery_v1/exact_identity_receipt.json'),
    'response_results': Path('E:/codex_rebuilds/original_response_format_recovery_v1/reference/analysis/results.json'),
    'response_summary': Path('E:/codex_rebuilds/original_response_format_recovery_v1/reference/analysis/summary.csv'),
    'response_flags': Path('E:/codex_rebuilds/original_response_format_recovery_v1/reference/analysis/response_format_flags.csv'),
    'response_root_review': Path('E:/codex_rebuilds/original_response_format_recovery_v1/root_review_v1.json'),
    'response_peer': Path('E:/codex_rebuilds/original_response_format_recovery_v1/peer_review_v1.json'),
    'stress_ci_review': WORK / '.local/finite_stress_ci_source_review_v1.json',
}
for n in range(1, 10):
    PATHS[f'figure{n:02d}'] = NEW / f'delivery/chinese_figures_v1/data/figure_{n:02d}.csv'
for n in range(1, 7):
    PATHS[f'table{n:02d}'] = REF / f'report_adapter_v1/tables/table_{n:02d}.csv'


def main():
    if (OUT / 'audit_receipt.json').exists():
        preserved = read_json(OUT / 'preclosure_snapshot_v1/preservation_receipt.json')
        if preserved['status'] != 'BYTE_EXACT_PRE_CLOSURE_CANDIDATE_PRESERVED_NOT_FINAL_PACKAGE_INPUT':
            raise RuntimeError('Refuse to overwrite a noncandidate receipt.')
        for name, meta in preserved['files'].items():
            if name != 'build_index.py':
                if digest(OUT / name) != meta['sha256'] or digest(OUT / 'preclosure_snapshot_v1' / name) != meta['sha256']:
                    raise RuntimeError('Refuse to replace changed or unpreserved candidate outputs.')
    old_rows = list(csv.DictReader((V2 / 'requirement_evidence.csv').open(encoding='utf-8-sig', newline='')))
    if len(old_rows) != 155 or len({r['id'] for r in old_rows}) != 155:
        raise AssertionError('Original anchor is not unique155.')
    evidence = {}
    for key, p in PATHS.items():
        if not p.is_file():
            raise FileNotFoundError(p)
        evidence[key] = {'path': str(p), 'sha256': digest(p), 'bytes': p.stat().st_size,
                         'read_scope': 'saved JSON/CSV/text metadata; no raw/model/runtime execution'}
    assert evidence['v2_csv']['sha256'] == 'eae81e5aa3c232693e982766eda25231d303fc2fb4580f43f9d9560ad35cb06a'
    assert evidence['plan']['sha256'] == 'ca5602a6f8fcb852f5824bf6c4fd7e71074b280c35ba5705676806d17d593843'
    metric = read_json(PATHS['metric'])
    secondary = read_json(PATHS['secondary'])
    root_review = read_json(PATHS['b8_root'])
    fmt = read_json(PATHS['format'])
    eq = read_json(PATHS['equivalence'])
    pub = read_json(PATHS['publication'])
    pkg = read_json(PATHS['chinese_package'])
    findings = read_json(PATHS['metric_findings'])
    counts = collections.Counter((x['level'], x['code']) for x in findings)
    assert metric['status'] == 'INCONCLUSIVE_RETAINED'
    assert metric['terminal_units'] == metric['factual_units_checked'] == 6504
    assert counts == {('UNKNOWN', 'CONDITION_SEED_LOGICAL_WRITE_SPAN_NOT_OBSERVED'): 1440,
                      ('UNKNOWN', 'REAL_PROVIDER_USAGE_INCOMPLETE'): 107}
    assert root_review['actual_stage_runs'] == 18 and root_review['record_pairs'] == 241
    assert root_review['record_comparison_differences'] == 0 and root_review['format_table_pairs'] == 6
    assert collections.Counter(x['exit_code'] for x in root_review['stage_checks']) == {0: 14, 1: 2, 2: 2}
    assert all(x['guard_denied_count'] == 0 for x in root_review['stage_checks'])
    assert fmt['status'] == 'FORMAT_ROW_TYPE_PRESENCE_PASS' and len(fmt['exports']) == 6
    for x in fmt['exports']:
        rb = x['readback']
        assert rb['status'] == 'PASS'
        assert rb['source_decoded_sha256'] == rb['csv_reconstructed_sha256'] == rb['parquet_reconstructed_sha256']
    assert eq['status'] == 'COMPARABLE_SYSTEM_LEVEL_REFERENCE'
    assert eq['matched_pairs_observed'] == eq['matched_pairs_planned'] == 240
    assert pub['revision_id'] == 20 and pub['new_images'] == 9
    assert pkg['all_54_figures_and_9_original_CSVs_verified'] is True
    tasks = [json.loads(s) for s in PATHS['tasks'].read_text(encoding='utf-8').splitlines() if s.strip()]
    queries = [json.loads(s) for s in PATHS['queries'].read_text(encoding='utf-8').splitlines() if s.strip()]
    formal = [t for t in tasks if t['split'] == 'formal']
    chains = collections.defaultdict(list)
    for t in tasks:
        chains[t['chain_id']].append(t)
    required_fields = ['task_id', 'chain_id', 'domain', 'difficulty', 'parent_ids', 'observations', 'procedure',
                       'expected', 'acceptable_alternatives', 'refusal_conditions', 'reference_evidence', 'scorer_version', 'content_sha256', 'origin']
    metadata = {
        'tasks': len(tasks), 'unique_task_ids': len({t['task_id'] for t in tasks}),
        'chains': len(chains), 'chain_lengths': dict(collections.Counter(map(len, chains.values()))),
        'split': dict(collections.Counter(t['split'] for t in tasks)),
        'formal_domain_difficulty': [{'domain': d, 'difficulty': k, 'tasks': v} for (d, k), v in sorted(collections.Counter((t['domain'], t['difficulty']) for t in formal).items())],
        'formal_state_size': [{'applicable': a, 'size_class': s, 'tasks': n} for (a, s), n in sorted(collections.Counter((t['state_spec']['applicable'], t['state_spec']['size_class']) for t in formal).items())],
        'required_top_level_fields_missing_rows': {k: sum(k not in t for t in tasks) for k in required_fields},
        'parent_ids_not_in_corpus': sum(any(p not in {x['task_id'] for x in tasks} for p in t['parent_ids']) for t in tasks),
        'within_chain_expected_duplicate_pairs': sum(sum(x['expected'] == y['expected'] for j, x in enumerate(ts) for y in ts[j+1:]) for ts in chains.values()),
        'query_count': len(queries), 'query_unique_ids': len({q['query_id'] for q in queries}),
        'query_unique_texts': len({q['text'] for q in queries}), 'query_independent_source_tasks': len({q['source_task_id'] for q in queries}),
        'query_domains': dict(collections.Counter(q['domain'] for q in queries)),
        'query_classes': dict(collections.Counter(q['query_class'] for q in queries)),
        'query_no_answer': {str(k): v for k, v in collections.Counter(q['no_answer'] for q in queries).items()},
        'scope': 'Structural metadata counts only; no rescoring, provider call, query execution, statistical estimator or live Linux check.'
    }
    assert len(tasks) == 144 and len(formal) == 120 and len(chains) == 24
    assert len(queries) == metadata['query_unique_ids'] == metadata['query_unique_texts'] == 240
    assert metadata['within_chain_expected_duplicate_pairs'] == 0
    write_json('corpus_metadata_review.json', metadata)
    evidence['corpus_metadata'] = {'path': str(OUT / 'corpus_metadata_review.json'), 'sha256': digest(OUT / 'corpus_metadata_review.json'), 'bytes': (OUT / 'corpus_metadata_review.json').stat().st_size,
                                  'read_scope': 'new structural metadata audit only'}

    index_receipt = read_json(PATHS['claims_index_receipt'])
    assert index_receipt['actual_terminal'] is True and index_receipt['status'] == 'COMPLETE_METADATA_LOCATORS_NOT_SCIENTIFIC_PASS'
    for name, meta in index_receipt['products'].items():
        p = PATHS['claims_index_receipt'].parent / name
        assert p.stat().st_size == meta['bytes'] and digest(p) == meta['sha256']
    def typed_csv(p):
        return [{k: json.loads(v) for k, v in row.items()} for row in csv.DictReader(p.open(encoding='utf-8-sig', newline=''))]
    ui = typed_csv(PATHS['unit_raw_index'])
    pi = typed_csv(PATHS['pair_raw_index'])
    ps = read_json(PATHS['pair_selectors'])
    assert len(ui) == 6504 and len({x['unit_id'] for x in ui}) == len({x['run_id'] for x in ui}) == 6504
    assert {x['raw_line'] for x in ui} == set(range(1, 6505))
    assert sum(x['public_calibration'] for x in ui) == 144
    assert sum(not x['success'] for x in ui) == 2231
    assert sum(not x['provider_usage_complete'] for x in ui) == 107
    assert not any(x['public_calibration'] and (x['formal_collection_member'] or x['primary_inferential_eligible']) for x in ui)
    assert ps['primary_sha256'] == evidence['primary']['sha256']
    by_number = {x['index_record_number']: x for x in ui}
    pair_counts = collections.Counter(x['pair_id'] for x in pi)
    assert dict(pair_counts) == {'T0_S0': 360, 'S0_SS0': 324, 'T0_T1': 360, 'S0_S1': 360, 'SS0_SS1': 360}
    for spec in ps['pairs']:
        expected_tasks = {t['task_id'] for t in formal if spec['pair_id'] != 'S0_SS0' or t['state_spec']['applicable']}
        selected = [x for x in pi if x['pair_id'] == spec['pair_id']]
        assert set(spec['task_ids']) == expected_tasks
        assert {(x['task_id'], x['repeat']) for x in selected} == {(task, repeat) for task in expected_tasks for repeat in (0, 1, 2)}
        for x in selected:
            assert x['model_id'] == 'qwen3.7-plus' and x['topology'] == 'runtime_uds' and x['processes'] is False
            for side in ('baseline', 'target'):
                u = by_number[x[f'{side}_index_record_number']]
                assert u['phase'] == 'main' and not u['public_calibration']
                assert u['task_id'] == x['task_id'] and u['repeat'] == x['repeat'] and u['arm'] == x[f'{side}_arm']
                for field in ('model', 'model_id', 'topology', 'processes', 'source_sha256', 'task_sha256'):
                    assert u[field] == x[field]
                for field in ('unit_id', 'run_id', 'raw_line', 'raw_line_sha256'):
                    assert u[field] == x[f'{side}_{field}']
    write_json('claims_index_review.json', {'status': 'ACTUAL_INDEX_PRODUCTS_AND_6504_LOCATORS_1764_PAIRS_METADATA_LINKS_VERIFIED',
        'claims_index_receipt_sha256': evidence['claims_index_receipt']['sha256'], 'all_eight_products_hash_and_size_match': True,
        'unit_locators': len(ui), 'pair_rows': len(pi), 'pair_counts': dict(pair_counts),
        'all_pair_task_sets_equal_frozen_corpus_formal120_or_state108': True,
        'repeats_model_topology_and_both_raw_locators_checked': True, 'public_in_formal_or_primary': 0,
        'raw_line_SHA_scope': 'auditor-issued values verified for cross-index equality; underlying raw lines not rehashed here',
        'new_effect_CI_p_Holm_or_quality_computations': 0})
    evidence['claims_index_review'] = {'path': str(OUT / 'claims_index_review.json'), 'sha256': digest(OUT / 'claims_index_review.json'),
                                      'bytes': (OUT / 'claims_index_review.json').stat().st_size, 'read_scope': 'new pure metadata locator linkage audit only'}

    aux_id = read_json(PATHS['aux_identity'])
    aux = read_json(PATHS['aux_results'])
    aux_root = PATHS['aux_identity'].parent
    for run in aux_id['runs']:
        folder = aux_root / run['name']
        assert digest(folder / 'execution_receipt.json') == run['execution_receipt_sha256']
        assert digest(folder / 'guard_result.json') == run['guard_receipt_sha256']
        assert read_json(folder / 'execution_receipt.json')['actual_exit_code'] == 0
        guard = read_json(folder / 'guard_result.json')
        assert guard['exit_code'] == guard['denied_count'] == 0
    for file in aux_id['files']:
        for side, field in [('reference', 'reference_sha256'), ('independent_rebuild', 'independent_sha256')]:
            p = aux_root / side / 'analysis' / file['path']
            assert p.stat().st_size == file['bytes'] and digest(p) == file[field]
        assert file['reference_sha256'] == file['independent_sha256'] and file['exact_bytes_equal']
    assert aux['coverage']['summary_rows'] == 25 and aux['coverage']['new_token_descriptive_rows'] == 5
    assert aux['coverage']['saved_auxiliary_projection_rows'] == 20
    assert aux['primary_input_tokens_still_INCOMPLETE'] is True
    assert aux['new_CI_p_Holm_or_hypothesis_family'] is False and aux['within_existing_B8_241_record_identity'] is False
    primary = read_json(PATHS['primary'])
    def pointer(root, path):
        value = root
        for key in path.strip('/').split('/'):
            key = key.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value
    copied = 0
    token_statuses = {}
    for row in aux['rows']:
        if row['auxiliary_origin'] == 'VERBATIM_SAVED_AUXILIARY_DICT_PROJECTION':
            assert canonical(pointer(primary, row['auxiliary_source_pointer'])) == canonical(row['auxiliary'])
            copied += 1
        if row['metric'] == 'provider_input_tokens':
            assert row['all_planned_status'] == 'INCOMPLETE'
            token_statuses[row['pair_id']] = row['auxiliary']['status']
            if row['pair_id'] != 'T0_T1':
                assert all(row['auxiliary'][k] is None for k in ['baseline_mean', 'target_mean', 'difference', 'relative_change'])
    assert copied == 20 and len(token_statuses) == 5
    save_aux = {'status': 'ACTUAL_TWO_RUN_FOUR_FILE_IDENTITY_AND_LITERAL_AUXILIARY_PROJECTION_REVIEWED',
        'identity_receipt_sha256': evidence['aux_identity']['sha256'], 'rows': 25, 'twenty_copied_dicts_exact_to_primary': True,
        'token_auxiliary_statuses': token_statuses, 'only_T0_T1_has_conditional_descriptive_points': True,
        'all_five_primary_token_endpoints_INCOMPLETE': True, 'no_new_means_CI_p_Holm_computed_by_this_review': True,
        'outside_original_B8_241_record_identity': True}
    write_json('both_success_supplement_review.json', save_aux)
    evidence['aux_review'] = {'path': str(OUT / 'both_success_supplement_review.json'), 'sha256': digest(OUT / 'both_success_supplement_review.json'),
                              'bytes': (OUT / 'both_success_supplement_review.json').stat().st_size, 'read_scope': 'actual receipt and saved-value identity checks; no estimator executed'}

    response_id = read_json(PATHS['response_identity'])
    response = read_json(PATHS['response_results'])
    response_root = PATHS['response_identity'].parent
    assert response_id['status'] == 'ACTUAL_TWO_OFFLINE_RESPONSE_RECOVERY_RUNS_EXACT_FOUR_FILE_IDENTITY'
    assert len(response_id['runs']) == 2 and len(response_id['files']) == 4
    for run in response_id['runs']:
        folder = response_root / run['name']
        assert digest(folder / 'execution_receipt.json') == run['execution_receipt_sha256']
        assert digest(folder / 'guard_result.json') == run['guard_receipt_sha256']
        assert read_json(folder / 'execution_receipt.json')['actual_exit_code'] == 0
        guard = read_json(folder / 'guard_result.json')
        assert guard['exit_code'] == guard['denied_count'] == 0
    for file in response_id['files']:
        for side, field in [('reference', 'reference_sha256'), ('independent_rebuild', 'independent_sha256')]:
            p = response_root / side / 'analysis' / file['path']
            assert p.stat().st_size == file['bytes'] and digest(p) == file[field]
        assert file['reference_sha256'] == file['independent_sha256'] and file['exact_bytes_equal']
    rc = response['coverage']
    assert {k: rc[k] for k in ('planned_tasks', 'formal_nonpublic_tasks', 'public_tasks', 'actual_provider_attempts',
        'actual_returned_response_rows', 'response_unique_request_ids', 'no_response_tasks', 'no_response_failed_tasks',
        'no_response_attempts', 'multiple_response_tasks')} == {
        'planned_tasks': 6504, 'formal_nonpublic_tasks': 6360, 'public_tasks': 144, 'actual_provider_attempts': 6578,
        'actual_returned_response_rows': 6414, 'response_unique_request_ids': 6414, 'no_response_tasks': 90,
        'no_response_failed_tasks': 90, 'no_response_attempts': 164, 'multiple_response_tasks': 0}
    assert response['new_model_API_system_action_or_scientific_statistics'] is False
    assert response['primary_or_existing_M002_projection_modified'] is False
    assert response['within_existing_B8_241_record_identity'] is False
    assert response['M002_open_explanation_semantic_completeness_closed'] is False
    flags = ['frozen_normalized_json_decoder_accepted', 'decoded_is_object', 'required_diagnosis_action_keys_present',
             'required_keys_exact', 'frozen_parser_accepted']
    groups = response['summary_by_phase_arm_model_topology']
    main_groups = [g for g in groups if g['phase'] == 'main']
    totals = {}
    for name, selected, planned, returned, missing in [('all', groups, 6504, 6414, 90), ('main', main_groups, 2160, 2090, 70)]:
        assert sum(g['planned_task_denominator'] for g in selected) == planned
        assert sum(g['actual_returned_response_row_denominator'] for g in selected) == returned
        assert sum(g['no_response_tasks'] for g in selected) == missing
        totals[name] = {}
        for flag in flags:
            values = {unit: {state: sum(g[f'{flag}_{unit}_{state}'] for g in selected)
                            for state in ('true', 'false', 'unknown')} for unit in ('response', 'unique_task')}
            assert values['response'] == {'true': returned, 'false': 0, 'unknown': 0}
            assert values['unique_task'] == {'true': returned, 'false': 0, 'unknown': missing}
            totals[name][flag] = values
    # These five pre-correction files remain evidence of the original metadata projection, including its withdrawn scope sentence.
    original_quality_hashes = {
        'project_recorded_fields.py': 'dc8da75ea8fb852b0ea8732d6fbf66ce3da0aedad9f1997fd76d4fbfa78f0ff4',
        'recorded_quality_fields.csv': 'd1328c3e5665a4c4a0bff2feb3a452efb8dd3f1eaa66e8c5ffc52ece4958d4ab',
        'metadata_field_inventory.json': '586c5276ec4bba897c54243fbe4fcc3943c48297448468d13e229461c39e3eab',
        'method_scope.json': 'e327f9c470575b1a1aa2ea8e7d1b368ab852edd46f0485415292f856393aa4f4',
        'receipt.json': '86ca8235d4754cb0997e3390d099c9874740167eb5038dadb586ac55ba2e29be'}
    for name, sha in original_quality_hashes.items():
        assert digest(PATHS['quality_scope_correction'].parent / name) == sha
    save_response = {'status': 'ACTUAL_TWO_RUN_FOUR_FILE_FORMAT_IDENTITY_AND_SAVED_COUNTERS_REVIEWED',
        'identity_receipt_sha256': evidence['response_identity']['sha256'],
        'root_review_sha256': evidence['response_root_review']['sha256'], 'peer_review_sha256': evidence['response_peer']['sha256'],
        'coverage': rc, 'saved_flag_totals': totals,
        'original_five_metadata_projection_files_unchanged': original_quality_hashes,
        'withdrawn_hash_only_scope_claim': 'Use correction_note_v1 and source_storage_review_v1; SDK response.model_dump was persisted.',
        'no_response_is_unknown_not_invalid': True, 'format_is_not_action_correctness_or_open_explanation_semantics': True,
        'new_means_scores_CI_p_Holm_parser_or_raw_body_reads_by_this_review': 0,
        'outside_original_B8_241_record_identity': True}
    write_json('response_format_recovery_review.json', save_response)
    evidence['response_review'] = {'path': str(OUT / 'response_format_recovery_review.json'),
        'sha256': digest(OUT / 'response_format_recovery_review.json'),
        'bytes': (OUT / 'response_format_recovery_review.json').stat().st_size,
        'read_scope': 'actual run receipts/output identity and saved count conservation only; no body read/parser/quality estimator run'}

    updates = {}

    def set_rows(ids, status, observed, limit, keys, action='NONE_WITHIN_STATED_SCOPE', delivery='ACTUAL_SAVED_EVIDENCE_REVIEWED', science='NO_NEW_SCIENTIFIC_PASS_CLAIM'):
        for rid in ids.split():
            if rid in updates:
                raise AssertionError(('duplicate update', rid))
            updates[rid] = (status, observed, limit, keys.split(), action, delivery, science)

    # Retain directly documented older preflight and independently approved offline batches.
    inherit = 'P001 P002 P003 P004 P005 P006 P007 P008 O001 O002 O003 O004 O007 O010 O011 O012 O013 O014 O015 O016 O017 O018 O019 O020 D001 D002 T011'
    old_by_id = {r['id']: r for r in old_rows}
    for rid in inherit.split():
        x = old_by_id[rid]
        action = 'EXISTING_EVIDENCE_NOT_FULLY_VERIFIED_THIS_AUDIT' if x['status'] == 'UNVERIFIED' else 'REPORTABLE_BOUNDARY_NO_RERUN_REQUIRED'
        set_rows(rid, x['status'], x['observed_completion'], x['remaining_gate_or_limit'],
                 'v2_csv v2_receipt offline report', action, 'INDEPENDENT_HISTORICAL_OR_PREFLIGHT_EVIDENCE_RETAINED')

    set_rows('G001', 'COMPLETE_SCOPED', '本批独立新root、冻结身份和不可变输入归档可定位；B8从归档独立重建，旧批仍独立保留。', '范围为有身份绑定的归档/分析；未做所有历史目录磁盘写入的全局取证。', 'protocol freeze input_packages b8_root lifecycle_comparison')
    set_rows('G002', 'SCOPED_LIMITATION', '6504真实终态、6578真实API尝试及逐次usage来源已审；质量/时延来自新provider批次，未用mock或旧real替代。', '107任务的provider usage不完整；五主input-token比较INCOMPLETE。未知/失败返回不归因模型、不补0、不据token下界推账单。', 'metric primary format', 'CLAIM_LIMIT_NO_AUTOMATIC_MODEL_RERUN', science='INCONCLUSIVE_RETAINED; FIVE_TOKEN_COMPARISONS_INCOMPLETE')
    set_rows('G003', 'COMPLETE_SCOPED', '已保留失败、缺失、NOT_APPLICABLE、检索原FAIL与容量补充INCOMPLETE；六表回读保留missing/null/空串差异。', '完成的是如实保留和限制主张，不是失败或未知被改为科学PASS。', 'metric retrieval capacity format')
    set_rows('G004', 'COMPLETE_SCOPED', '六表/schema实际回读；严格claims_raw_index已真实生成并核对全部8产品SHA/size、6504唯一unit/run/raw行定位与1764主pair双方定位。主5pair人群120/108/120/120/120×3，与冻结任务/模型/runtime_uds一致；public144不进入正式/主要selector。', '索引是metadata locators，不重算科学值，也没有在本次重新读取原raw行；其余RQ/图表有逐行selector和独立来源边界。', 'metric format claims limitations claims_index_receipt unit_raw_index pair_raw_index pair_selectors claim_selectors figure_row_selectors claims_index_review')
    set_rows('E001 E002', 'COMPLETE_SCOPED', '源a040、正式freeze2c654、逐单元source/contract身份与归档驱动可连接；新后处理修订保留preimage和旧回执，未合并不同业务版本。', '生命周期修订属于采后分析连接纠错，不是业务执行重跑；旧raw/质量/主统计数值不变。', 'protocol freeze metric lifecycle_comparison b8_root package_manifest dependency')
    set_rows('E003', 'COMPLETE_SCOPED', '依赖v3实际build和network-none smoke已终态通过并有root attestation6a2；B8实际使用批准镜像完成两侧九阶段。此前pyparsing失败镜像仍保留。', '只认证已审锁定依赖和离线分析可运行；不声称全部可选动态导入或镜像普遍安全。', 'dependency b8_root b8_peer input_packages')
    set_rows('E004 E009', 'SCOPED_LIMITATION', '环境表记录openEuler用户态、WSL2/Docker、Python版本与2CPU/2GiB配额；不是原生openEuler内核VM。', '宿主CPU绑定/频率、后台负载/抢占等未获得完整受控证明；不得把环境描述当隔离性能证书。', 'protocol table01 limitations', 'EXISTING_ENVIRONMENT_OBSERVABILITY_LIMIT')
    set_rows('E005 E006 E007', 'COMPLETE_SCOPED', '正式前freeze绑定输入/任务/模型/输出契约/角色/参数/分析；实际全量计划身份审计和独立重建已完成。provider_seed明确NOT_SENT。', '本地seed不控制供应商采样；不支持的参数和未观测实际能力不伪称已控制。', 'protocol freeze metric primary b8_root')
    set_rows('E008', 'SCOPED_LIMITATION', '协议和实际生命周期将真实模型与离线压力/分析放在不同窗口；B8是采集结束后的离线重建。', '不能据锁/窗口推出Windows所有后台负载停止；性能范围保留为已观察VM条件。', 'protocol execution b8_root limitations', 'EXISTING_ENVIRONMENT_OBSERVABILITY_LIMIT')
    set_rows('E010', 'UNVERIFIED', '宿主休眠/恢复和时钟恢复观察已保留，图表明确guest单调时钟/SDK等待边界。', '无独立网络RTT、完整时间同步/抢占控制实测；不拆queue/推理/网络，不扣休眠时长、不重写行。', 'report limitations table01', 'EXISTING_ENVIRONMENT_OBSERVABILITY_LIMIT')
    set_rows('P009', 'COMPLETE_SCOPED', '批准的独立历史三路径矩阵和新正式native/multiprocess状态/URI/映射/RPC连接均有真实证据。', '只支持实际Linux兼容路径；多进程由Runtime读SHM再经RPC到child，不声称child直接SHM映射。', 'offline metric protocol', 'REPORTABLE_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('P010', 'SCOPED_LIMITATION', '已读同源共享预检原报告与os-probe原stdout：namespace EPERM/不支持；cgroup可见/配额可读，未证明子cgroup强制；Landlock无单独已验证强制证据。', '按原§2.2保留实际不支持/未知及兼容降级，不要求把不可用能力补成成功，不外推沙箱强制隔离。', 'shared_preflight_report shared_os_probe new_preflight protocol', 'REPORTABLE_PLATFORM_CAPABILITY_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('P011', 'COMPLETE_SCOPED', '同源共享preflight_report.md、逐命令stdout/stderr/退出码已有真实记录，新批preflight.json另给pip/source及本批校准边界；原WSL失败/namespace skip和测试路径补正均公开保留。', '共享旧报告中的旧公开141/144等仅属其原批，不迁移为新公开109/144或当前任务证据；当前新任务/实际计量另接新freeze和audit。', 'shared_preflight_report shared_preflight_commands new_preflight protocol metric', 'NONE_WITHIN_SHARED_PREFLIGHT_RECORD_SCOPE')
    set_rows('C001 C002 C003 C004 C005 C006 C007', 'COMPLETE_SCOPED', '144唯一任务/24条六事件链；24公开+120正式/20链；每域三难度各10正式任务；108状态适用小中大各36、12 NA。必需身份/事实/答案/替代/拒答/参考/评分/内容SHA字段均存在，链内expected重复对0；全量事实核验另有6504连接。', '这是作者构造canonical fixture的结构和确定性规则核验；字段存在不扩为自然生产任务效度或开放解释质量。', 'tasks corpus_metadata protocol metric', 'NONE_WITHIN_STATED_SCOPE')
    set_rows('C008', 'COMPLETE_SCOPED', '144任务origin均注明新作者合成记录、项目自编许可/改写、场景和source_ids；sources说明只参考公开机制语义、无复制真实事故日志。', '不称脱敏真实事故样本；本轮没有重新验证外部网页当前内容或授予外部材料新许可。', 'tasks corpus_sources corpus_metadata')
    set_rows('C009 C011 C012', 'COMPLETE_SCOPED', '独立冻结oracle对144案例分类，6504计划单元事实/动作/只读check/契约/隔离和因果顺序连接已实际审计，失败原样留在分母。', '固定域canonical diagnosis与作者只读动作fixture；不是live Linux修复或所有合理替代答案的开放语义评分。', 'metric protocol tasks', 'REPORTABLE_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('C010 M003', 'COMPLETE_SCOPED', '用户原件fca666授权“采用自动验证，取消人工评分并记录协议变更”；本批继承授权，6504单元全量自动核验已实际完成。', '两位人工评审、盲评分、agreement/裁决为NOT_COLLECTED；不列等待人工授权/补评分行动，不虚构人工记录。', 'automatic_authorization protocol metric', 'AUTHORIZED_AUTOMATIC_SUBSTITUTION_COMPLETED', science='HUMAN_RATINGS_NOT_COLLECTED_BY_AUTHORIZED_AMENDMENT')
    set_rows('C013 C014', 'COMPLETE_SCOPED', '240不同query每域60、160有答案/80无答案；冻结query_class和可接受采用范围/required_capabilities可定位，独立自动gold已重建。', '同task派生query及三建库不增加独立样本量；标签是公开规则范围的自动gold，不称双人自然语言相关性标注。', 'queries corpus_metadata retrieval capacity', 'REPORTABLE_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('C015', 'COMPLETE_SCOPED', '人工标注要求已获用户授权改自动；240相关集合/可接受范围的独立自动gold与实际分析绑定已保存。', '自动规则有效域与tag/能力语义限制保留；没有人工agreement，不因此要求未授权补人评。', 'automatic_authorization protocol retrieval capacity', 'AUTHORIZED_AUTOMATIC_SUBSTITUTION_COMPLETED')
    set_rows('C016 O008 O009', 'SCOPED_LIMITATION', '四池五法三建库按登记运行；20k两worker在固定1200s上限终止，14346/14400、缺54（14+40）。原e73 FAIL和54missing未改；capacity实际完成20汇总/60分建库的严格前缀解释。', '原方案允许预注册容量/超时边界；此为可报告边界，非必须无限补跑。受影响点估计/CI仍null，科学矩阵仍INCOMPLETE，不能声称14400完整或原审计PASS。', 'retrieval capacity report b8_root', 'REPORTABLE_PROTOCOL_CAPACITY_BOUNDARY_NO_RERUN_REQUIRED', 'ACTUAL_TERMINAL_CAPACITY_ANALYSIS_COMPLETED', 'FAIL_RETAINED_NO_INFERENTIAL_TABLES; INCOMPLETE_CAPACITY_RECONCILED_NOT_ORIGINAL_AUDIT_PASS')
    set_rows('C017', 'COMPLETE_SCOPED', '有答案P@1/Recall/MRR/nDCG和无答案误检/拒用各自分母在20汇总/60建库表中分开；计划分母/缺失计数保留。', '某些20k单元缺失使相应估计null，未把失败当成功拒用或0成本。', 'capacity table05', 'REPORTABLE_PROTOCOL_CAPACITY_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('R001 R002', 'COMPLETE_SCOPED', '六组定义、lean完整事实、统一角色/输出契约/评测器与全量事实可访问核验已有实际证据。', '输出质量/失败/实际步骤若变化必须连同成本报告；不据固定任务图断言所有失败单元工作量相同。', 'protocol metric table02')
    set_rows('R003', 'COMPLETE_SCOPED', '新状态审核按run/URI/full-reference hash/活组件代次连接生产→引用→消费→释放；真实consume/排序及进程RPC检查已全量完成。旧180误报归零而科学数值不变。', '没有额外独立consumer payload重hash或可分离纯read时延；不由metadata连接声称这些未测能力。', 'metric lifecycle_comparison protocol', 'REPORTABLE_MEASUREMENT_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('R004 R005 R006 R012 R013', 'COMPLETE_SCOPED', 'main2160=120任务×6组×3轮已真实终态；正式6360+公开144分开；计划ID/平衡顺序/隔离/先后依赖全量连接。质量独立样本120任务/20链，状态pair108；预先合法选择不扩240且放弃NI。', '执行完整不代表成功率100%、usage全、NI成立或全矩阵所有指标已测；3次重复不增独立n。', 'execution protocol metric primary secondary format')
    set_rows('R007', 'COMPLETE_SCOPED', '第二家族固定模型doubao-seed-2-0-mini-260215在预抽60任务×6×2完成720单元；按phase/model/topology匹配统计已保存。', '只为这两个固定模型/样本的复核；跨模型usage部分缺失，不推普遍模型泛化或将两个模型直接当因果随机组。', 'protocol metric primary secondary table01', 'REPORTABLE_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('R008', 'COMPLETE_SCOPED', 'multiprocess同60任务S0/SS0/SS1×2完成360；实际agent_processes_uds、4worker及退出清理审计可定位。', 'main/cross是runtime_uds且processes=false；跨拓扑字节不可直接拼池，child经RPC消费而非直接映射SHM。', 'metric protocol secondary', 'REPORTABLE_MEASUREMENT_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('R009 R010 R011', 'COMPLETE_SCOPED', '固定AutoGen/LangGraph各60×2=120，四逻辑角色/输入/输出和实际框架执行有记录；外部240匹配对三endpoint已通过受限系统参考等价审计。', '仅quality/input-token/wall三endpoint的SYSTEM_LEVEL_REFERENCE；同进程框架调度、序列化、失败路由不同，不能归因单机制或叫所有计量普遍等价。', 'metric references equivalence table06', 'REPORTABLE_SYSTEM_REFERENCE_BOUNDARY_NO_RERUN_REQUIRED', science='COMPARABLE_SYSTEM_LEVEL_REFERENCE_ONLY')
    set_rows('R014 R015', 'SCOPED_LIMITATION', '正式前公开实际144单元曾估算请求/token/guest时间并登记尝试/字节/timeout/断点保护；全账6578attempt记录和幂等标识保留。', '价格、折扣、货币上限/失败计费未知，不能将技术token阈值称货币预算核验；provider幂等支持不假定。不得为补账单重发已计费请求。', 'protocol execution metric budget preformal_budget preformal_gate', 'MISSING_BILLING_EVIDENCE_NO_AUTOMATIC_REPLAY', science='PRICE_AND_CURRENCY_BUDGET_UNKNOWN')
    set_rows('O005 O006', 'COMPLETE_SCOPED', '独立历史量化4精度×4维度×4分布、12800记录/3200唯一状态已完成；table04与中文图06已真实输出并QA。', '实际决策为8候选排序；embedding+scale字节非全部socket成本，不推模型诊断准确率。', 'offline table04 figure06 chinese_package', 'REPORTABLE_INDEPENDENT_OFFLINE_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('A001 A002 A003 A004 A005 A006 A007 A008 A009 A010 A011', 'COMPLETE_SCOPED', '8项关闭、2codec、4记忆条件和匹配baseline均完成预抽60任务×3；14组matched-phase探索统计和所有planned分母已保存并独立重建。', '这是执行/分析已完；unknown成本、失败质量差和wrong1/180保留，不是消融机制必有收益或质量不变。无新确认族。', 'metric primary all14 report', 'REPORTABLE_NEGATIVE_OR_MISSING_RESULT_NO_RERUN_REQUIRED')
    set_rows('A012', 'SCOPED_LIMITATION', '同冻结60任务/20链/同baseline的14 matched-phase连接和配对结果已保存；统一事实和任务图可审。', '失败、工具次数/质量/步骤变化不被强称同工作量；效率比较应标注适用边界，不将早失败少用量解释为改进。', 'protocol metric all14 table02 limitations', 'REPORTABLE_WORKLOAD_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('A013', 'COMPLETE_SCOPED', '独立历史状态尺寸冷/热路径及小载荷反例已保存，中文图05和table04实际输出。', '不混同新模型wall或以旧微基准吞吐替主性能；嵌套span不重复加。', 'offline figure05 table04 chinese_package', 'REPORTABLE_INDEPENDENT_OFFLINE_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('M001', 'COMPLETE_SCOPED', '6504计划/终态闭合，失败2231未删除；main及每phase成功率按各计划分母报告。', '6504为public+多phase混合账，4273/6504不得当主模型准确率；canonical fixture范围。', 'metric primary format table03')
    set_rows('M002', 'COMPLETE_SCOPED', '原有动作bool投影与冻结JSON格式子项均实际完成：main动作1561 true/599 false；2090实际响应五formatflags全true/0false，70无响应task均unknown。全6504计划保留6414实际响应及90无响应unknown，两次格式恢复actual exit0/guard deny0、四输出字节等，并有独立metadata/39group守恒peer。', '格式仅原parse_decision归一化/default JSON decoder、对象/diagnosis+action字段/精确键和原label成员契约，不等于动作正确或开放解释语义完整，不将无响应记invalid。旧五文件中hash-only句明确撤回并附纠正/源码存储收据，原五文件不改；不虚构取消的人评分。', 'metric figure03 report automatic_authorization quality_projection_receipt quality_projection_table quality_scope_correction quality_storage_review response_identity response_results response_summary response_flags response_root_review response_peer response_review', 'AUTHORIZED_FIXED_CONTRACT_SUBMETRICS_COMPLETE_OPEN_EXPLANATION_NOT_CLAIMED', 'ACTUAL_RECORDED_ACTION_AND_TWO_RUN_FROZEN_FORMAT_RECOVERY_COMPLETED', 'FORMAT_VALIDITY_SEPARATE_FROM_ACTION_ACCURACY_NO_RESPONSE_UNKNOWN')
    set_rows('M004', 'INCOMPLETE', '逐attempt6578及usage_raw/来源/重试/参数/响应hash导出回读已完；缺失usage如实保留。', '107任务usage不完整（这是保存数据可观测性，未把原因归模型）；无法由现有证据构造完整输入/输出/缓存账，不补0、不重发请求来“补齐”。', 'metric metric_findings format primary', 'UNOBSERVED_USAGE_NOT_REPAIRABLE_FROM_SAVED_DATA', 'ACTUAL_FORMAT_AND_AUDIT_COMPLETED', 'REAL_PROVIDER_USAGE_INCOMPLETE')
    set_rows('M005', 'COMPLETE_SCOPED', '原usage、明确零、missing与null在原raw与规范六表中保持区分，五token主要比较无CI/p。', '通信代理token/成功子集/总token下界均不填provider未知量或货币金额。', 'metric primary format')
    set_rows('M006', 'SCOPED_LIMITATION', '冻结native/external writer将SDK response.model_dump()和request messages写入原model_io；Recorder整行JSONL持久化并做配置secret子串脱敏。旧审计投影的opaque/hash-only是分析处理边界，非原始存储策略。', 'SDK对象不等于原始HTTP response wire bytes；failed/no-response记录和unknown保留。明确撤回此前把metadata没正文推成原档无正文的错误，纠正另附且旧5文件不可变。', 'quality_storage_review quality_scope_correction metric format protocol', 'RECORDED_SDK_IO_BOUNDARY_NO_NEW_PROVIDER_CALLS')
    set_rows('M007', 'COMPLETE_SCOPED', 'messages schema保存communication_tokens、semantics、tokenizer；冻结driver逐消息记录实际tokenizer名称，backend登记tiktoken；新公开dependency日志1fb9（preflight绑定）记录tiktoken==0.14.0，provider usage单独分账。', 'binary的通信token是文本视图代理，非provider计费量；版本通过同环境依赖日志绑定，不虚构每消息另有一个tokenizer_version列。', 'messages_schema format protocol new_preflight new_dependency_versions frozen_native_driver', 'NONE_WITHIN_RECORDED_COMMUNICATION_PROXY_SCOPE')
    set_rows('M008 M009 M010', 'COMPLETE_SCOPED', '同拓扑实际payload/framing/ACK/RPC可观测IPC、本地物化、逻辑载荷分别审计/统计；main/cross与multiprocess及external拓扑分开。', '主main为runtime_uds、非每Agent独立进程；external native IPC0不代表无handoff成本；旧strict混合指标不叫物理总线。', 'metric protocol primary secondary table03')
    set_rows('M011', 'SCOPED_LIMITATION', '互斥任务分区与嵌套trace分开；图04/table04展示wall、SDK等待、状态/检索/工具等实际保存范围，不重复叠加。', '1440 seed逻辑write span未观测；fused读算不可拆纯读，index/RPC子span不与外层相加；SDK等待不等于queue或纯推理。', 'metric secondary figure04 table04', 'UNOBSERVED_SEED_WRITE_SPAN_AND_NONSEPARABLE_SPANS')
    set_rows('M012 T006', 'COMPLETE_SCOPED', '原主链配对guest wall CI及次要均值/p50/p95/p99、SDK等待占比、CPU/观察吞吐的已测值已保存并独立重建。', '五主wall CI均跨0；仅guest单调时钟/SDK观测，不称显著提速、完整用户wall或纯服务推理。', 'primary secondary figure04 table03', 'CLAIM_LIMIT_NO_NEW_DATA_REQUIRED')
    set_rows('M013', 'COMPLETE_SCOPED', '候选有无/自动相关候选、eligible nonempty、实际采用和采用后成功分母分开，table05及附录已保存。', '候选命中≠采用≠记忆导致成功；无答案查询/缺失检索独立保留，不把失败当拒用。', 'metric secondary table05 report')
    set_rows('M014', 'COMPLETE_SCOPED', '三模式M0↔M1主配对、四记忆条件匹配及wrong/stale负结果均实际保存；采用后成功另列。', '观察到质量损失/不确定性，不能据采用后成功宣称因果改善、普遍迁移或鲁棒。', 'primary all14 table05 limitations', 'REPORTABLE_NEGATIVE_RESULT_NO_RERUN_REQUIRED')
    set_rows('M015 S007', 'SCOPED_LIMITATION', 'resource_samples80342行、200ms登记采样、PID/角色/RSS/CPU/FD/loadavg等已回读。当前同容器/冻结helper probe验证self/1的io为ENOENT、stat/status/fd可读、TASKSTATS禁用且非Docker masked io；当前uname与原public六字段一致。', '历史processes[].io只有null；当前验证环境capability边界与历史null一致，但不伪造逐历史样本errno或称字段采齐，不武断叫记录器遗漏/强制新增实验。RSS共享页重复计且仅采样，非PSS/连续峰值。', 'resource_schema format secondary resource_io_current_probe resource_io_probe_receipt resource_io_boundary_review', 'REPORTABLE_CURRENT_ENVIRONMENT_IO_CAPABILITY_BOUNDARY_HISTORY_ERRNO_UNOBSERVED', 'ACTUAL_FORMAT_AND_DESCRIPTIVE_ANALYSIS_COMPLETED', 'IO_NULL_SAMPLED_RSS_ONLY')
    set_rows('S001', 'COMPLETE_SCOPED', '六表CSV/Parquet/schema/storage已实际完成，两侧按每行值/类型/presence回读且identity相同；task_runs6504。', '完整observed Draft2020-12 schema不是原方案所有应测字段的语义完备证书；missing/null原样。', 'format b8_root b8_peer')
    set_rows('S002 S003 S004 S005', 'SCOPED_LIMITATION', '规范task/provider/messages/states六表的行/值/类型/字段presence实际无损回读已通过，并可回连原JSONL身份；通信token名称和同环境tiktoken0.14.0依赖身份已记录。', '语义计量边界仍分别存在：provider未知usage/cache/queue、binary通信token为文本视图代理、fused读算/独立payload重hash未观测；不能把observed schema称全部必需字段已测。', 'format metric resource_schema messages_schema secondary new_dependency_versions', 'OBSERVED_SCHEMA_COMPLETE_SCIENTIFIC_FIELDS_LIMITED')
    set_rows('S006', 'INCOMPLETE', 'memory_events28173行的写/查/采用/feedback/index记录已导出回读；索引操作与外层内存操作分开分析。', '1440条condition seed逻辑write span缺失，只有相关index边界。不能从index时间补逻辑write，也不能由schema回读补出采集时未测值；保留NOT_OBSERVED。', 'format metric_findings secondary', 'UNOBSERVED_LOGICAL_SEED_WRITE_SPAN_NOT_REPAIRABLE_FROM_SAVED_DATA', 'ACTUAL_FORMAT_AND_DESCRIPTIVE_ANALYSIS_COMPLETED', 'CONDITION_SEED_LOGICAL_WRITE_SPAN_NOT_OBSERVED')
    set_rows('S008', 'COMPLETE_SCOPED', 'formal freeze、执行/环境/模型/依赖/配置/排除/原raw及派生结果SHA链、规范表storage和B8归档manifest已保存。', '版本/身份可追溯不证明所有科学端点完整；价格/货币预算未知单列。', 'freeze protocol execution dependency format b8_root package_manifest')
    set_rows('S009 D005', 'SCOPED_LIMITATION', '原raw只追加保留，339raw分发包字节指纹复用、SHA/CRC证据及受限secret排除/只读重建输入已保存。', '本轮没有对每个宿主raw文件的OS只读属性做全局取证；实际checksum以manifest形式交付，不把包身份核验扩为全磁盘不可变或普遍安全认证。', 'input_packages dependency package_manifest b8_peer', 'ARCHIVE_ATTRIBUTE_SCOPE_NOT_UNIVERSAL_FORENSICS')
    set_rows('T001 T003 T004 T010', 'COMPLETE_SCOPED', '唯一冻结d87实际主统计及14matched-phase结果已保存、两侧重建一致；同任务/模型/轮次，重复先按任务平均，域内整链10000重抽/精确20链signflip；质量保守风险界保存，旧real/pilot不拼池。', '完成的是固定分析，不要求负结果变正；token不全、选择偏差、质量损失及CI不确定性均保持，不新增Holm/条件。', 'primary all14 b8_root b8_peer protocol', 'REPORTABLE_INFERENCE_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('T007', 'COMPLETE_SCOPED', '五主要配对全部预定结果与双方成功辅助成本25行已并列：5token按冻结_chain_arrays/mean补描述，20旧aux字典逐值投影；独立两次actual exit0/guard deny0、四文件逐字节等且peer核对。', '仅T0_T1辅助265对/96任务有描述点，其余4token整个双方成功子集有unknown故全点null；五主token仍INCOMPLETE。选择偏差明确，不重评分/新CI/p/Holm；补表是原B8之后独立追加，不扩大241身份范围。', 'primary aux_identity aux_results aux_summary aux_peer aux_review', 'REPORTABLE_SUCCESS_SELECTION_BOUNDARY_NO_RERUN_REQUIRED', 'ACTUAL_TWO_RUN_DESCRIPTIVE_AUXILIARY_DELIVERY_COMPLETED', 'ALL_PRIMARY_TOKEN_INCOMPLETE_FOUR_AUX_TOKEN_INCOMPLETE')
    set_rows('T002', 'INCOMPLETE', '十成本终点唯一Holm族身份/方法及可测IPC五项原结果完整保存并重建；未另开族。', '五input-token终点仍INCOMPLETE且CI/p为null；这是不可填的主端点数据缺失，不是改p值/换种子/筛成功子集或自动补调用的理由。', 'primary b8_root protocol', 'CLAIM_WITHHELD_DUE_TO_UNOBSERVED_USAGE', 'ACTUAL_REGISTERED_ANALYSIS_COMPLETED', 'FIVE_INPUT_TOKEN_ENDPOINTS_INCOMPLETE')
    set_rows('T005', 'COMPLETE_SCOPED', '仅公开144真实单元做正式前功效/预算门禁，预先选择保留120并放弃质量不劣措辞；正式108/120人群固定。', '原§6.3允许的合法分支已执行；不因未获得NI结论要求扩240或事后恢复不劣措辞。', 'protocol freeze primary preformal_power preformal_gate', 'PROTOCOL_ALLOWED_NI_WITHHELD_BRANCH_COMPLETED', science='WITHHELD_PROSPECTIVELY')
    set_rows('T008', 'COMPLETE_SCOPED', '次要异质性真实完成6360formal、21 strata/2277完整边际覆盖cells；域/难度/规范事实UTF8输入长度quartile/状态大小/链位置/model均覆盖，state108与12 NA另facet。', '仅描述/探索，无新p/Holm；planned-empty/null保留，不挖联合有利子组或把不同拓扑/模型拼为因果对照。', 'secondary coverage report b8_root', 'REPORTABLE_EXPLORATORY_BOUNDARY_NO_RERUN_REQUIRED')
    set_rows('T009', 'SCOPED_LIMITATION', '规模实点/原始序列及分建库描述已保存；压力吞吐已有seed261019/10000的batch均值CI，未强拟合拐点。', '有限源码核查确认p95是pooled task分位点、通信/斜率只有点，未见对应已冻结CI方法。raw task wall/bytes仍在，不称历史数据不可恢复；补区间需另定采样/estimand方法并明确采后操作化，不能冒称已预注册或自设CI关账。', 'figure08 offline capacity report stress_ci_review', 'OFFLINE_ANALYSIS_METHOD_AND_DELIVERY_GAP_NOT_UNRUN_EXPERIMENT')
    set_rows('F001 F003 F005 F006 F009 F010 F012 F014 F016 F018 F019 F020 F021', 'COMPLETE_SCOPED', 'B8已实际生成9图/6表/CSV/脚本与附录；独立中文finalv6单/双栏各9图PDF/SVG/约600dpi PNG完成，实际数值/字体/可检索/灰阶/裁切/legend/null与CI可见性QA及94成员ZIP/云端9图回读已完成。', '各图严格保留其数据来源：图5/6/8独立历史离线，图9仅三endpoint系统参考。英文18PDF旧QA问题仍保留；中文展示通过不认证科学端点完整。', 'report numeric_qa single_qa double_qa chinese_package publication offline equivalence', 'NONE_WITHIN_PRESENTATION_SCOPE', 'ACTUAL_CHINESE_9X2_QA_AND_54_ASSET_DELIVERY_COMPLETED', 'ORIGINAL_INCONCLUSIVE_FAIL_MISSING_AND_NEGATIVE_RESULTS_RETAINED')
    set_rows('F002 F004 F007 F008 F011 F013 F015 F017', 'SCOPED_LIMITATION', '相应图/表/附录与9份原CSV均已实际生成、数值绑定并通过中文单双栏QA/ZIP及云端回读；交付不再pending。', '图2/表3五input-token仍缺；图4为实测互斥/嵌套边界且seed逻辑write未测；图7/表5检索缺54；图8尾分位/通信非全CI；表1环境可见性受限；附录保留这些缺口，不能由展示完成声称方案所有数据完整。', 'report numeric_qa single_qa double_qa chinese_package publication table01 table03 table05 figure08 metric', 'PRESENTATION_COMPLETE_ORIGINAL_DATA_OR_SCOPE_GAPS_RETAINED', 'ACTUAL_CHINESE_9X2_QA_AND_54_ASSET_DELIVERY_COMPLETED', 'ORIGINAL_INCONCLUSIVE_FAIL_MISSING_AND_NEGATIVE_RESULTS_RETAINED')
    set_rows('D003', 'COMPLETE_SCOPED', '人工已授权改自动；6504跨域计划单元全量自动审计已真实完成，覆盖超过30单元。', '不虚构两位评审或正式前预抽30；全量自动不等于所有115语义指标PASS，UNKNOWN1547继续保留。', 'automatic_authorization protocol metric', 'AUTHORIZED_AUTOMATIC_SUBSTITUTION_COMPLETED')
    set_rows('D004', 'COMPLETE_SCOPED', 'B8v2从只读归档完成reference9+independent9共18阶段；241比较0diff、六表typed identity一致；最终中文另受限渲染/实际QA。随后6.5辅助表和原格式恢复各另有两次actual运行、各四文件字节身份。', '18阶段含14exit0、2metric exit2、2retrieval exit1，guard denied0；两个后补恢复不倒写入原241身份。重建相同缺失/失败不叫所有审计PASS，英文旧视觉缺陷保留。', 'b8_execution b8_comparison b8_root b8_terminal b8_peer format single_qa double_qa aux_identity aux_peer response_identity response_root_review response_review', 'NONE_WITHIN_STATED_REPRODUCTION_IDENTITY_SCOPES', 'ACTUAL_18_STAGE_REBUILD_AND_SEPARATE_AUXILIARY_AND_FORMAT_REPLAYS_COMPLETED', 'INCONCLUSIVE_AND_FAIL_REPRODUCED_NOT_PROMOTED')
    set_rows('D006', 'SCOPED_LIMITATION', 'raw339成员/source-control208成员/依赖tar三输入包v2已有实际核验；B8归档三清单和中文图ZIP已完成。最终README/claims/limitations v3已落盘。', '小final_control_supplement_v1需在本155 v3固定后实际打包并解码核验；为避免循环，本索引不猜其终态，后续仅增独立补充receipt，不改已锁CSV/旧包。', 'input_packages dependency package_manifest b8_root chinese_package readme claims limitations', 'CONTROL_ASSEMBLY_PENDING', 'INPUT_AND_CHINESE_PACKAGES_COMPLETE_FINAL_CONTROL_PENDING')
    set_rows('D007', 'COMPLETE_SCOPED', 'claims_to_evidence/limitations v3已实际落盘，并由严格raw索引提供6504定位与主5pair支持列表/图表逐行selector；Feishu原报告rev18和中文rev20均实际append-only回读。', '结论索引/发布不能消除缺失token、1440seed span、I/O null或容量边界；不以包/图数量声明整个方案全覆盖。', 'claims limitations readme publication report b8_root claims_index_receipt claims_index_review')
    set_rows('D008', 'UNVERIFIED', '原方案§9–10要求相关工作与实质机制创新定位独立完成；本次没有新的相关工作评审证据。', '需论文写作阶段独立论证；已有IPC/检索/序列化原语及实验数量不自动构成新算法。此为写作证据门禁，不派生新实验。', 'plan limitations', 'PAPER_WRITING_EVIDENCE_REQUIRED_NO_NEW_EXPERIMENT')
    set_rows('D009', 'SCOPED_LIMITATION', '可用摘要主结果：120任务/20链/6组/3次2160主运行；T0→S0运行时IPC-59.8817941%且质量-2.2222pp；state108 S0→SS0 IPC-94.7277726%且质量-8.6419753pp，已有保存CI。', '五token变化不能填节省值，NI WITHHELD，wall CI跨0；不能写质量不变、显著提速、通用Linux准确率或混合6504成功率。', 'primary claims limitations table03 publication', 'ABSTRACT_WORDING_WITHHELD_WHERE_UNSUPPORTED', science='TOKEN_INCOMPLETE; NI_WITHHELD; WALL_CI_CROSSES_ZERO')
    assert set(updates) == set(old_by_id), (set(old_by_id) - set(updates), set(updates) - set(old_by_id))
    fields = list(old_rows[0]) + ['previous_v2_status', 'previous_v2_row_sha256', 'v3_change_reason', 'delivery_or_processing_status', 'scientific_data_or_claim_status', 'remaining_action_class']
    rows, changes = [], []
    for old in old_rows:
        rid = old['id']
        status, obs, limit, keys, action, delivery, science = updates[rid]
        keys = ['plan', 'v2_csv', *dict.fromkeys(keys)]
        keys = list(dict.fromkeys(keys))
        row = dict(old)
        reason = f"原要求“{old['requirement']}”按实际保存证据复核：{obs} 保留边界：{limit}"
        row.update(status=status, evidence_scope=delivery, authoritative_evidence='; '.join(str(PATHS[k]) if k in PATHS else evidence[k]['path'] for k in keys),
                   evidence_keys=json.dumps(keys, ensure_ascii=False), evidence_sha256=json.dumps({k: evidence[k]['sha256'] for k in keys}, ensure_ascii=False),
                   evidence_pointers=json.dumps({'current_keys': keys, 'row_id': rid, 'original_section': old['protocol_section'], 'inherited_preflight_or_offline': rid in inherit.split()}, ensure_ascii=False),
                   observed_completion=obs, review_basis='原方案全文、原155行ID/顺序/文本、当前实际保存JSON/CSV/receipt；未运行模型/统计/基准/网络。',
                   remaining_gate_or_limit=limit, previous_v2_status=old['status'], previous_v2_row_sha256=hashlib.sha256(canonical(old)).hexdigest(),
                   v3_change_reason=reason, delivery_or_processing_status=delivery, scientific_data_or_claim_status=science, remaining_action_class=action)
        rows.append(row)
        changes.append({'id': rid, 'original_order_index': len(changes), 'previous_status': old['status'], 'status': status,
                        'status_changed': status != old['status'], 'reason': reason, 'evidence_keys': keys,
                        'remaining_action_class': action, 'delivery_or_processing_status': delivery,
                        'scientific_data_or_claim_status': science, 'previous_row_sha256': row['previous_v2_row_sha256']})
    with (OUT / 'requirement_evidence.csv').open('w', encoding='utf-8-sig', newline='') as fh:
        wr = csv.DictWriter(fh, fieldnames=fields)
        wr.writeheader()
        wr.writerows(rows)
    reread = list(csv.DictReader((OUT / 'requirement_evidence.csv').open(encoding='utf-8-sig', newline='')))
    invariant_columns = ['id', 'protocol_section', 'requirement', 'historical_status_not_migrated', 'historical_evidence_not_migrated']
    assert all(all(a[k] == b[k] for k in invariant_columns) for a, b in zip(old_rows, reread))
    write_json('changes.json', {'all155_rows_in_original_order': changes, 'no_overall_completion_percentage_calculated': True})
    write_json('evidence_files.json', evidence)
    actions = {
        'finite_remaining_delivery_action': ['D006: final control supplement实际打包/解码核验；本CSV锁定后补独立receipt，避免互相等待。'],
        'unobserved_original_measurements': ['M004/T002: 107任务provider usage不完整，原五input-token终点保持INCOMPLETE/null；当前绑定证据不提供完整usage，不能归因模型或重发计费请求。', 'S006/M011: 1440条condition seed逻辑write span finding未采，不等于1440独立task；index时延不能代填逻辑write。'],
        'finite_offline_analysis_method_gap': ['T009/F008: 吞吐已有冻结batch均值CI；p95/通信/斜率只有点，未见适用的冻结CI算法。raw task wall/bytes仍在，属采后方法/交付决策，既不是raw丢失也不是未跑实验；本次不自设CI。'],
        'existing_evidence_or_writing_requires_review': ['E004/E009/E010: CPU/宿主负载/RTT/时间同步/抢占可见性受限，论文描述范围需明确。', 'D008: 相关工作与创新定位仍需独立论文写作证据，非新增实验条件。'],
        'protocol_allowed_boundaries_not_new_run_demands': ['C016/O008/O009: 固定20k deadline终止、缺54和原FAIL如实保留，原§4.2允许容量停止；不要求无限补跑。', 'T005: 公开前瞻决定保留120并放弃NI为原§6.3允许分支；不要求扩240。', 'C010/C015/M003/D003: 用户已授权自动替代人工，自动full6504/independent gold已做；不等待人工授权/评分。', 'S007/M015: I/O实际null；当前同内核/容器probe验证/proc/io不可用，与历史null一致。保留capability边界，不补历史errno、不称数值采齐或强制新实验。', '质量下降、wrong1/180及wall CI跨0是可报告负结果，不构成须变正的未完成任务。'],
        'recoverable_original_submetrics_actually_closed': ['M002: main原动作明确bool1561true/599false，格式五flags在2090实际响应上全true，70无响应task为unknown；全账6414响应/90无响应task分别报告。两次actual格式恢复/四文件身份已验证，不以格式替代准确或开放解释。', 'T007: 25双方成功成本辅助行实际两次重建；20旧字典逐值投影，5token描述仅T0_T1有完整子集点，其余4有unknown全null。主token仍INCOMPLETE。'],
        'original_required_experiment_cells_identified_as_not_run_outside_allowed_stops': [],
        'presentation_and_reproduction_actually_closed': ['两侧九阶段18stage已终态，241identity0diff与六表lossless已验；14exit0+2metric2+2retrieval1，不叫全科学PASS。后补aux/format各有独立2-run身份，不扩大原241范围。', '中文54图/9原CSV/94ZIP成员，两侧各9实际QA，rev20云端追加9图+ZIP已真实回读。', 'README/claims/limitations v3已落盘；英文旧QA问题独立留存。'],
        'scope': 'No completion percentage; missing measurements cannot be retroactively repaired by a format export, permitted stop is not an instruction for new experiments.'
    }
    write_json('remaining_actions.json', actions)
    hard_gaps = {
        'schema_version': 'finite-original-goal-hard-gaps-v1',
        'status': 'FINITE_ACTION_AND_MEASUREMENT_GAPS_NOT_A_NEW_EXPERIMENT_AUTHORIZATION',
        'basis': 'Original plan full text plus this original155 evidence index; allowed stops, negative results and authorized amendments distinguished from missing hard fields.',
        'offline_closeout_actions': [
            {'ids': ['D006'], 'action': 'CONTROL_ASSEMBLY_PENDING: assemble/decode-check final control supplement after this immutable CSV/receipt, then issue an external actual completion receipt.',
             'data_available': True, 'new_experiment_required': False},
            {'ids': ['T009', 'F008'], 'action': 'Original scale-curve CI requirement is partial: throughput CI exists, p95/communication/slope lack applicable frozen CI method in the inspected analyzer. Decide and disclose a finite postcollection estimand/resampling method or retain this specific delivery gap; this audit does not invent one.',
             'raw_task_wall_and_bytes_retained': True, 'historical_raw_irrecoverable_claim': False, 'new_experiment_required': False,
             'evidence_keys': ['stress_ci_review', 'figure08', 'offline']}
        ],
        'historical_measurement_gaps_requiring_protocol_or_claim_decision_if_full_target_is_insisted_on': [
            {'ids': ['S006', 'M011'], 'missing': '1440 condition-seed logical-write-span findings, not 1440 independent tasks.',
             'saved_substitute_rejected': 'Index-only timing cannot reconstruct the unobserved outer logical-write span.',
             'next_decision': 'Retain NOT_OBSERVED/limited timing claim or explicitly authorize a future changed measurement protocol; do not retrospectively fill or automatically rerun.',
             'evidence_keys': ['metric_findings', 'secondary']},
            {'ids': ['M004', 'T002'], 'missing': '107 task units with incomplete provider usage; all five primary input-token endpoints remain INCOMPLETE with null CI/p.',
             'saved_substitute_rejected': 'Zero, token lower bounds, both-success subsets and format recovery do not recover complete provider accounting.',
             'next_decision': 'Retain incomplete endpoints; any provider/billing evidence recovery requires actual original-request evidence. No automatic paid-request replay or attribution of missingness to the model.',
             'evidence_keys': ['metric_findings', 'primary', 'aux_results']}
        ],
        'genuinely_unrun_original_experiment_cells_found_outside_protocol_allowed_stops': [],
        'empty_unrun_list_scope': 'Review found no additional unrun required matrix cell outside the explicitly allowed resource-stop and NI branches: 6360 formal units terminal; public144 separate; approved historical offline matrices remain separate. This is not a claim that every required measurement was captured.',
        'not_hard_unrun_experiment_gaps': [
            {'ids': ['C016', 'O008', 'O009'], 'fact': '54/14400 retrieval observations missing after registered 1200s capacity stops; original FAIL and null estimates retained.', 'reason': 'Original §4.2 permits declared capacity/time boundary; no infinite rerun demand.'},
            {'ids': ['T005'], 'fact': 'NI WITHHELD_PROSPECTIVELY; keep120, state108 applicable.', 'reason': 'Original §6.3 permits this branch; no mandatory expansion240.'},
            {'ids': ['C010', 'C015', 'M003', 'D003'], 'fact': 'Human ratings/agreement not collected.', 'reason': 'Direct user-authorized automatic amendment applies; not awaiting new human permission.'},
            {'ids': ['M002'], 'fact': 'Action bools and frozen format submetrics now actually recovered, no-response tasks remain unknown.', 'reason': 'Original recoverable subitems closed; frozen output has no required open-explanation semantic rubric, so do not invent one.'},
            {'ids': ['M015', 'S007'], 'fact': 'Historical I/O null; current same-uname capability check has /proc/io ENOENT and TASKSTATS disabled.', 'reason': 'Report capability boundary and unrecorded historical errno; no asserted recorder omission and no forced new experiment.'},
            {'ids': ['R014', 'R015'], 'fact': 'Prices/currency budget/provider idempotency support unknown.', 'reason': 'Unknown is reportable, not a fabricated monetary-budget PASS or reason to replay paid calls.'},
            {'ids': ['O020'], 'fact': '3/6/8-agent expansion not run.', 'reason': 'Conditional optional extension, original four-role boundary documented; no claim of agent-count scaling.'},
            {'ids': ['D009'], 'fact': 'Quality losses, wall-CI crossing zero, wrong-memory negative outcomes retained.', 'reason': 'Unfavorable evidence is not unfinished execution and need not be made favorable.'}
        ],
        'writing_or_environment_claim_boundaries': [
            {'ids': ['D008'], 'status': 'UNVERIFIED', 'fact': 'Related-work and mechanism novelty evidence remains a separate writing-stage gate; no new research/experiment launched here.'},
            {'ids': ['E004', 'E009', 'E010'], 'fact': 'Host CPU/load/RTT/time-sync/preemption visibility and guest-clock/SDK waiting scope remain limited; do not claim complete host user wall or service inference isolation.'}
        ],
        'no_overall_completion_percentage': True, 'goal_complete_not_inferred_from_packages': True,
        'no_new_scientific_method_experiment_or_scoring': True
    }
    write_json('finite_hard_gaps.json', hard_gaps)
    receipt = {
        'schema_version': 'original155-requirement-evidence-index-v3',
        'status': 'READ_ONLY_REVIEW_COMPLETE_PARTIAL_SCIENTIFIC_EVIDENCE_NOT_ALL_REQUIREMENTS_PASS',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'study_id': 'protocol_repair_20261020_v1', 'requirements': 155,
        'original_ids_order_requirement_sections_and_historical_columns_byte_value_preserved': True,
        'invariant_columns': invariant_columns, 'previous_csv_sha256': evidence['v2_csv']['sha256'],
        'original_plan_sha256': evidence['plan']['sha256'],
        'change_receipt_includes_all155_old_new_status_and_reason': True,
        'no_overall_completion_percentage_calculated': True,
        'scientific_FAIL_INCONCLUSIVE_UNKNOWN_NOT_PROMOTED_TO_PASS': True,
        'original_scientific_status_snapshot': {'metric': metric['status'], 'FAIL': 0, 'UNKNOWN': 1547,
            'seed_logical_write_unknown': 1440, 'usage_incomplete_task_units': 107,
            'retrieval': 'FAIL_RETAINED_NO_INFERENTIAL_TABLES', 'retrieval_missing': 54,
            'capacity': 'INCOMPLETE_CAPACITY_RECONCILED_NOT_ORIGINAL_AUDIT_PASS',
            'input_token_primary': 'ALL_FIVE_INCOMPLETE', 'noninferiority': 'WITHHELD_PROSPECTIVELY'},
        'actual_closed_processing': {'b8_stage_runs': 18, 'child_exits': {'0': 14, '1': 2, '2': 2},
            'guard_denied_count': 0, 'comparison_records': 241, 'comparison_differences': 0, 'format_table_pairs': 6,
            'formal_secondary_units': secondary['summary']['terminal_formal_units'],
            'heterogeneity_coverage_cells': secondary['summary']['coverage_cells'],
            'chinese_assets': 54, 'chinese_original_CSVs': 9, 'chinese_ZIP_members': 94, 'Feishu_revision': 20,
            'separate_auxiliary_actual_runs': 2, 'auxiliary_rows': 25, 'separate_format_recovery_actual_runs': 2,
            'returned_response_format_denominator': 6414, 'no_response_task_format_unknown': 90,
            'main_returned_response_format_denominator': 2090, 'main_no_response_task_format_unknown': 70,
            'separate_supplements_not_added_to_B8_241_identity': True},
        'status_meanings': read_json(PATHS['v2_receipt'])['status_meanings'],
        'interpretation': 'row status describes requirement evidence within stated scope; processing/delivery status is separate from scientific data/claim status; missing favorable evidence is not an instruction to run until favorable.',
        'remaining_actions_file': 'remaining_actions.json',
        'finite_hard_gap_classification_file': 'finite_hard_gaps.json',
        'preclosure_candidate_retained': 'preclosure_snapshot_v1/preservation_receipt.json',
        'erroneous_hash_only_scope_sentence_explicitly_withdrawn': evidence['quality_scope_correction'],
        'finite_final_control_supplement_status': 'PENDING_THIS_LOCKED_INDEX; record actual completion in a new supplemental receipt without mutating these files',
        'work_performed': {'original_plan_full_text_read': True, 'original155_rows_reviewed': True,
            'saved_metadata_and_corpus_structural_counts_only': True, 'model_or_API_calls': 0, 'network_calls': 0,
            'docker_or_runtime_commands': 0, 'statistical_or_benchmark_runs': 0, 'full_raw_reads': 0,
            'science_source_raw_original_plan_v2_modified': False},
        'output_sha256': {n: digest(OUT / n) for n in ['requirement_evidence.csv', 'changes.json', 'evidence_files.json', 'corpus_metadata_review.json', 'claims_index_review.json', 'both_success_supplement_review.json', 'response_format_recovery_review.json', 'remaining_actions.json', 'finite_hard_gaps.json', 'build_index.py']}
    }
    write_json('audit_receipt.json', receipt)
    print(json.dumps({'status': receipt['status'], 'rows': len(rows), 'CSV_sha256': digest(OUT/'requirement_evidence.csv'), 'receipt_sha256': digest(OUT/'audit_receipt.json'), 'status_changes': sum(x['status_changed'] for x in changes)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
