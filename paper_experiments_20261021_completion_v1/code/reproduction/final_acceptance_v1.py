"""Inspect actual artifacts before closing the full recorded experimental goal."""
from pathlib import Path
import csv
import hashlib
import json
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT.parent
OUT = ROOT / 'delivery/final_bundle_v1'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def main():
    pack = load(OUT / 'package_receipt.json')
    assert pack['status'] == 'ACTUAL_FULL_BUNDLE_ALL_MEMBERS_DECODE_CRC_SHA_VERIFIED'
    assert pack['original_sources_modified'] is False and pack['known_credential_matches'] == 0
    archive = Path(pack['path'])
    assert archive.is_file() and archive.stat().st_size == pack['bytes']
    doc = load(ROOT / 'delivery/manuscript_append_v1/final_delivery_receipt.json')
    assert doc['status'] == 'COMPLETE_APPEND_ONLY_MANUSCRIPT_AND_ALL_PAGE_VISUAL_QA'
    assert digest(doc['docx_path']) == doc['sha256']
    assert doc['original_137_blocks_unchanged'] is True
    qa = load(doc['visual_qa_receipt_path'])
    assert qa['status'] == 'PASS_ALL_24_PAGES_ACTUALLY_VIEWED'
    assert qa['all_pages_actually_viewed'] is True and qa['page_count'] == 24
    assert qa['unresolved_P1_P2'] == []
    preserved = load(ROOT / 'delivery/manuscript_append_v3/preservation_receipt.json')
    assert preserved['original_137_blocks_unchanged'] is True
    assert preserved['original_nodes_and_final_sectPr_canonical_order_verified'] is True
    assert preserved['appended_text_table_values_order_and_PNG_bytes_preserved'] is True

    base = ROOT / 'main_batch/analysis/main_postrun_v1'
    accepted = load(base / 'outputs/analysis/acceptance.json')
    assert accepted['status'] == 'MAIN2160_EXACT_JOIN_USAGE_COMPLETE'
    assert (accepted['planned_main_units'], accepted['terminal_main_units'], accepted['formal_unique_tasks'],
            accepted['chains'], accepted['state_applicable'], accepted['provider_attempt_rows'],
            accepted['unknown_task_usage']) == (2160, 2160, 120, 20, 108, 2160, 0)
    assert accepted['quality_successes'] + accepted['quality_failures'] == 2160
    primary = load(base / 'outputs/analysis/formal_primary_v3.json')
    assert primary['primary_family_test_count'] == 10 and primary['active_confirmatory_families'] == 1
    assert primary['missing_units'] == 0 and primary['quality_noninferiority_wording'] == 'WITHHELD_BY_PROSPECTIVE_GATE'
    pairs = primary['strata'][0]['comparisons']
    assert [(c['baseline'], c['target'], c['planned_unique_tasks']) for c in pairs] == [
        ('T0', 'S0', 120), ('S0', 'SS0', 108), ('T0', 'T1', 120), ('S0', 'S1', 120), ('SS0', 'SS1', 120)]
    assert all(c['metrics'][m]['status'] == 'COMPLETE' for c in pairs for m in ('provider_input_tokens', 'observable_ipc_bytes', 'wall_sec'))
    replay = load(ROOT / 'analysis/reproduction_check_v1/saved_analysis_reproduction_identity.json')
    assert replay['status'] == 'ACTUAL_OFFLINE_REPLAY_SIX_OUTPUTS_BYTE_IDENTICAL'
    assert len(replay['comparisons']) == 6 and all(v['byte_identical'] for v in replay['comparisons'])
    for name in ('visual_qa_single.json', 'visual_qa_double.json'):
        graph_qa = load(ROOT / 'figures_v3/qa' / name)
        assert graph_qa['actual_new_views'] == [10, 11, 12, 13] and not graph_qa['unresolved_P1_P2']
    graph_pack = load(ROOT / 'delivery/figure_package_v2/package_receipt.json')
    assert graph_pack['status'] == 'ACTUAL_ZIP_DECODE_CRC_AND_ALL_SHA_VERIFIED'
    assert graph_pack['members'] == 247 and graph_pack['prior_figure_members_byte_identical'] == 144
    diagram = load(ROOT / 'delivery/drawio_framework_original_v1/新增收录核对.json')
    assert diagram['status'] == 'EXISTING_DRAWIO_AND_PREVIEWS_ADDED_BYTE_IDENTICAL'
    assert len(diagram['drawio_xml_pages']) == 2 and all(c['source_sha256'] == c['copy_sha256'] for c in diagram['files'])

    review_root = ROOT / 'analysis/final_goal_reconciliation_v1'
    before = load(review_root / 'final_before_package_addendum.json')
    assert before['no_additional_unrun_required_experiment_outside_allowed_branches_found'] is True
    assert [x['id'] for x in before['remaining_pending']] == ['FINAL_BUNDLE']
    assert before['D008']['primary_sources_read'] == 6
    original_csv = PROJECT / 'paper_experiments_20261019_pilot/protocol_repair_20261020_v1/analysis/completion_audit_v3/requirement_evidence.csv'
    with original_csv.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f); original_fields = reader.fieldnames; original_rows = list(reader)
    with (review_root / 'requirement_reconciliation.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    assert len(original_rows) == len(rows) == 155
    assert all(all(a[k] == b[k] for k in original_fields) for a, b in zip(original_rows, rows))
    with zipfile.ZipFile(archive) as z:
        manifest = json.load(z.open('完整文件清单_SHA256.json'))['members_excluding_manifest_itself']
        frozen_inputs = load(base / 'run_control/inputs.json')
        bound_raw = []
        for path, item in frozen_inputs['files'].items():
            if path.startswith('/study/raw/') and item['exists']:
                key = 'new_study/main_batch/raw/' + path.rsplit('/', 1)[1]
                assert manifest[key]['sha256'] == item['sha256']
                bound_raw.append(key)
        assert len(bound_raw) == 7
        original_plan = manifest['original_protocol/论文实验设计方案.md']
        assert original_plan['sha256'] == 'ca5602a6f8fcb852f5824bf6c4fd7e71074b280c35ba5705676806d17d593843'
        assert manifest['original_manuscript/面向多智能体协作_原排版复核稿.docx']['sha256'] == '18ca54fe5a69f8abeb3c20644bcad55cf6a19798afd0059d88c0fd1000d30289'
        final_doc_name = 'new_study/' + Path(doc['docx_path']).relative_to(ROOT).as_posix()
        assert manifest[final_doc_name]['sha256'] == doc['sha256']
        assert any(k.endswith('/多智能体协作框架_可编辑.drawio') for k in manifest)
        assert any(k.startswith('original_corrected_study/raw/') for k in manifest)
        assert any(k.startswith('offline_seed_write_v3/') for k in manifest)
        assert any(k.startswith('offline_stress_ci/') for k in manifest)
    result = {
        'status': 'FULL_AUTHORIZED_EXPERIMENTAL_WORK_AND_APPEND_ONLY_DELIVERY_ACTUALLY_ACCEPTED',
        'verified_utc': datetime.now(timezone.utc).isoformat(), 'original_requirement_rows_reconciled': 155,
        'original_requirement_cells_unchanged': True, 'planned_main_units': 2160,
        'unknown_new_main_usage': 0, 'only_pending_bundle_now_actually_closed': True,
        'actual_document': doc, 'actual_package_receipt': pack,
        'raw_files_bound_to_postrun_freeze_inside_package': bound_raw,
        'required_execution_analysis_figures_records_reproduction_and_append_delivery': 'COMPLETE_WITH_RECORDED_PROTOCOL_ALLOWED_FAILURES_AND_LIMITATIONS',
        'scientific_all_PASS_claim': False, 'algorithm_novelty_or_priority_certified': False,
        'historical_usage_or_timing_backfilled': False,
        'retained_scientific_limits': before['all_scientific_limitations_unchanged'],
        'future_new_experiments_required_by_this_finite_work': [],
        'scope': 'Closes the original protocol and user-authorized amendments as experimental work; does not certify every scientific hypothesis, platform counter, old bill or publication acceptance.'}
    path = OUT / 'final_goal_acceptance.json'
    with path.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n')
    print(json.dumps({'status': result['status'], 'requirements': 155, 'document_pages': 24,
                      'new_main': 2160, 'bundle_members': pack['members'], 'pending': []}, ensure_ascii=False))


if __name__ == '__main__':
    main()
