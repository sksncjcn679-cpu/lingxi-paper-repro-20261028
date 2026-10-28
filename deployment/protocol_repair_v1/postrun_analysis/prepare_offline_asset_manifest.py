"""Prepare historical offline byte-asset scope; verify opaque bytes after quiet.

prepare reads bounded old metadata, never raw bodies or new formal outcomes.
verify hashes only selected historical offline files after actual timing closure.
Neither entry imports a report, benchmark, model, SDK or numerical analyzer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

STUDY = 'protocol_repair_20261020_v1'
OLD_SOURCE = 'a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562'
REPORT_SOURCE = 'fed72d9777f6eaf8c630e19eb33f182e3f8acd01423d48dc20a39c9c4c460dc3'
LIMIT = 1024 * 1024
REPORT = 'analysis/postrun_verification_v1/report_v3'
TOPICS = {'protocol', 'state', 'quantization', 'reuse', 'stress', 'continuous', 'fault', 'state_stage_supplement'}
FAULTS = ('corrupt_state', 'drop_ack', 'duplicate_message', 'router_crash', 'timeout', 'wrong_memory')
BASE_LIMITS = [
    'Historical offline fixture batch under source a040; not measured new24-contract/corpus workload or new real/retrieval performance.',
    'Never pool old real/provider/retrieval values with corrected study metrics. No mechanism attribution from a historical byte copy.',
    'Original QA/rebuild status and limitations remain attached. PREPARED selection or failed archive evidence is never promoted to an executed PASS.',
    'Opaque hash verification establishes retained byte identity only; it does not rerun timing, analysis, semantic checks or visual inspection.']
FORBIDDEN = ('retrieval', 'provider_calls', 'task_runs', 'statistics_main', 'table_06.csv')


def utc():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha_file(path):
    before = path.stat()
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(LIMIT), b''):
            value.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError('input changed while hashing: ' + str(path))
    return value.hexdigest()


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def safe_relative(value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or any(token in value.lower() for token in FORBIDDEN):
        raise ValueError('foreign or forbidden old data path')
    return path


def dedicated_asset(relative):
    name = Path(relative).name
    return bool(re.fullmatch(r'figure_0[568](?:_(?:single|double))?\.(?:csv|pdf|svg|png)', name)
        or name in {'table_04.csv', 'state_paths.csv', 'quantization.csv', 'stress_chain_concurrency.csv',
            'stress_growth_slopes.csv', 'stress_fault_recovery.csv', 'stress_fault_semantic_audit.json',
            'state_matrix_plan.json', 'state_matrix_status.json', 'quantization_plan.json', 'quantization_status.json',
            'state_reuse_plan.json', 'state_reuse_status.json', 'stress_matrix_plan.json', 'stress_matrix_audit.json',
            'stress_fault_plan.json', 'stress_continuous_100_status.json', 'protocol_v3_cells.csv',
            'protocol_v3_audit.json', 'protocol_v3_plan.json', 'protocol_v3_status.json'}
        or re.fullmatch(r'(?:state_batch_[123]|protocol_v3_batch_[123]|stress_v3_L\d+_K\d+_(?:status|process)|stress_fault_[a-z_]+_(?:status|process))\.json', name)
        or (relative.startswith('state_measurement_correction_v3/') and name in {
            'stage_cells.csv', 'matrix_audit.json', 'stage_join_audit.json', 'manifest.json',
            'prospective_registration.json', 'execution.json'}))


class Metadata:
    def __init__(self):
        self.records = {}

    def bytes(self, path, reason):
        path = path.resolve()
        if 'raw' in path.parts or path.suffix not in ('.json', '.sha256', '.py', '.txt') or path.stat().st_size > LIMIT:
            raise ValueError('prepare permits bounded metadata only: ' + str(path))
        before = path.stat()
        body = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError('metadata changed during preparation')
        self.records[str(path)] = {'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body),
                                  'scope': reason, 'preparation_read': 'BOUNDED_OLD_METADATA_ONLY'}
        return body

    def json(self, path, reason):
        return json.loads(self.bytes(path, reason).decode('utf-8'))


def checksums(body):
    result = {}
    for line in body.decode('utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})\s+\*?(.+)', line)
        if not match or match[2] in result:
            raise ValueError('checksum metadata is malformed or duplicate')
        result[match[2]] = match[1]
    return result


def prepare(args):
    workspace = args.workspace_root.resolve()
    base = workspace / 'paper_experiments_20261019_pilot'
    old = base / 'formal_20261019'
    bundle = base / 'source_package_formal_20261019_v2/bundle'
    rebuild = base / 'rebuild_formal_20261019_v2'
    meta = Metadata()
    inputs = meta.json(bundle / 'metadata/source_input_inventory.json', 'original packaged source/input inventory')
    raw_inventory = meta.json(bundle / 'metadata/original_raw_inventory.json', 'stored opaque raw hashes; bodies not opened')['files']
    report_checks = checksums(meta.bytes(old / REPORT / 'checksums.sha256', 'original report-v3 asset checksums'))
    rebuild_checks = checksums(meta.bytes(rebuild / 'report/checksums.sha256', 'existing independent rebuild asset checksums'))
    availability = meta.json(old / REPORT / 'analysis/report_availability.json', 'mixed metadata: offline selectors/hashes only')
    source = meta.json(old / 'manifests/source_identity.json', 'historical source identity')
    source_verified = meta.json(bundle / 'metadata/source_identity_verification.json', 'historical source-package identity proof')
    qa = meta.json(old / 'analysis/report_qa_v4/visual_and_numeric_findings.json', 'original report-v3 QA-v4 status and selected offline-page scope')
    qa_execution = meta.json(old / 'analysis/report_qa_v4/execution_receipt.json', 'original actual render receipt; select only05/06/08 PDFs')
    selected = meta.json(old / 'analysis/final_render_selection_v3.json', 'historical PREPARED selection retained as such')
    old_qa = meta.json(old / 'analysis/report_qa_v2/visual_and_numeric_findings.json', 'archive-only original failed QA-v2 state')
    rebuilt = meta.json(rebuild / 'rebuild_manifest.json', 'existing no-provider rebuild receipt; offline subset proof only')
    pixel = meta.json(rebuild / 'pdf_pixel_qa_v1/audit.json', 'existing actual rebuilt PDF pixel comparison; select05/06/08 only')
    copies = meta.json(rebuild / 'logs/input_copy_inventory.json', 'existing rebuild copied-raw hash inventory; offline subset only')
    adapter_path = workspace / 'deployment/protocol_repair_v1/postrun_analysis/saved_report_adapter.py'
    meta.bytes(adapter_path, 'prepared consumer source identity; no execution/import')
    if source.get('source_sha256') != OLD_SOURCE or source_verified.get('aggregate_sha256') != OLD_SOURCE:
        raise ValueError('historical source identity differs from a040')
    if qa.get('report_source_sha256') != REPORT_SOURCE or qa.get('report') != REPORT:
        raise ValueError('original report-v3 visual proof identity differs')
    if qa.get('status') != 'VISUAL_LAYOUT_PASS_AUTOMATED_LIMITATIONS_RETAINED':
        raise ValueError('original visual layout proof has a different retained status')
    if pixel.get('status') != 'PASS_ALL18_REBUILT_PDF_PIXELS_MATCH_VIEWED_PUBLICATION':
        raise ValueError('actual independent rebuild pixel proof is unavailable')
    source_hashes = {name.removeprefix('/evidence/formal_20261019/'): value
                     for name, value in availability['source_sha256'].items() if name.startswith('/evidence/formal_20261019/')}
    batches = []

    def batch(suffix, topic, scope, limits):
        value = {'batch_id': 'formal_20261019_' + suffix, 'evidence_class': 'OFFLINE_MECHANISM_BATCH',
            'source_root': str(old).replace('\\', '/'), 'original_source_sha256': OLD_SOURCE,
            'measurement_scope': scope, 'limitations': BASE_LIMITS + limits,
            'assets': [], 'raw_hash_references': [], 'independent_rebuild_evidence': []}
        batches.append(value)
        return value

    def asset(target, topic, relative, role, **extra):
        safe_relative(relative)
        if topic not in TOPICS or not dedicated_asset(relative) or 'raw' in Path(relative).parts:
            raise ValueError('asset not accepted by the dedicated consumer scope: ' + relative)
        path = old / relative
        authority = None
        if relative.startswith(REPORT + '/'):
            wanted = report_checks[relative.removeprefix(REPORT + '/')]
            authority = 'report_v3/checksums.sha256'
        elif relative in source_hashes:
            wanted = source_hashes[relative]
            authority = 'report_v3/analysis/report_availability.json.source_sha256'
        elif 'inputs/' + relative in inputs:
            wanted = inputs['inputs/' + relative]['sha256']
            authority = 'source_package_v2/source_input_inventory.json'
        elif path.suffix == '.json':
            meta.json(path, 'dedicated old offline plan/status/audit byte authority')
            wanted = meta.records[str(path.resolve())]['sha256']
            authority = 'bounded_original_metadata_bytes_at_preparation'
        elif relative in {'tables/stress_growth_slopes.csv', 'analysis/stats/protocol_v3_cells.csv'} and args.allow_small_unindexed_csv_bytes:
            # This explicit opt-in is restricted to690 and17545 original bytes;
            # no CSV parsing or numeric computation is performed.
            maximum = {'tables/stress_growth_slopes.csv': 690, 'analysis/stats/protocol_v3_cells.csv': 17545}[relative]
            if path.stat().st_size != maximum:
                raise ValueError('unindexed dedicated CSV size changed')
            wanted = sha_file(path)
            authority = 'PREPARATION_SMALL_ARTIFACT_HASH; no original registered hash asserted; not fresh postrun verification'
        else:
            raise ValueError('no stored hash authority for asset: ' + relative)
        if not valid_sha(wanted):
            raise ValueError('invalid stored asset SHA')
        target['assets'].append({'topic': topic, 'relative_path': relative, 'sha256': wanted,
            'bytes': path.stat().st_size, 'role': role, 'hash_authority': authority, **extra})

    def raw(target, topic, paths):
        for relative in sorted(paths):
            safe_relative(relative)
            record = raw_inventory[relative]
            if not valid_sha(record['sha256']) or not isinstance(record['bytes'], int):
                raise ValueError('stored raw authority invalid')
            copied = copies.get(relative)
            if not copied or copied.get('sha256') != record['sha256'] or copied.get('bytes') != record['bytes']:
                raise ValueError('old offline raw is not joined to existing independent rebuild: ' + relative)
            target['raw_hash_references'].append({'topic': topic, 'relative_path': relative,
                'sha256': record['sha256'], 'bytes': record['bytes'],
                'hash_authority': 'source_package_formal_20261019_v2/bundle/metadata/original_raw_inventory.json.files',
                'bodies_read_at_preparation': False, 'bodies_read_by_report_adapter': False,
                'existing_rebuild_relative_path': 'input_view/' + relative,
                'existing_rebuild_sha256': copied['sha256'], 'existing_rebuild_bytes': copied['bytes']})

    def figure(target, topic, number):
        asset(target, topic, f'{REPORT}/figures/figure_{number:02}.csv', 'original_figure_data', target_figure=number)
        for width in ('single', 'double'):
            for kind in ('pdf', 'svg', 'png'):
                asset(target, topic, f'{REPORT}/figures/figure_{number:02}_{width}.{kind}', 'original_exact_byte_export', target_figure=number)

    state = batch('state', 'state', 'historical AF_UNIX state paths plus original dedicated state/quant table04; no model latency',
        ['Three independent receiver process batches; RTT includes nested receiver work. Logical/materialized/socket bytes differ; pooled p50/p95 do not imply a CI.',
         'Table04 remains the original212-row dedicated offline table. Its quantization rows also require the separate quantization raw hash.'])
    figure(state, 'state', 5)
    asset(state, 'state', REPORT + '/tables/table_04.csv', 'original_dedicated_offline_table', target_table=4)
    for path in ['tables/state_paths.csv', 'manifests/state_matrix_plan.json', 'analysis/state_matrix_status.json',
                 *[f'analysis/state_batch_{i}.json' for i in (1, 2, 3)]]:
        asset(state, 'state', path, 'dedicated_old_offline_appendix')
    raw(state, 'state', [f'raw/state_paths_batch_{i}.jsonl' for i in (1, 2, 3)])
    raw(state, 'quantization', ['raw/quantization.jsonl'])
    quant = batch('quantization', 'quantization', 'historical actual native8-candidate ranking preservation; not model accuracy',
        ['Wilson CI is ranking preservation only; adaptive/float precision does not establish new24-contract diagnosis accuracy.'])
    figure(quant, 'quantization', 6)
    for path in ('tables/quantization.csv', 'manifests/quantization_plan.json', 'analysis/quantization_status.json'):
        asset(quant, 'quantization', path, 'dedicated_old_offline_appendix')
    raw(quant, 'quantization', ['raw/quantization.jsonl'])
    stress = batch('stress', 'stress', 'historical SS1 four-role chain/concurrency synthetic fixture;30 batches per cell',
        ['Handshake exclusion differs from new real IPC boundaries. Proxy tokens are not provider usage.',
         'All30 *_batches streams are separately bound because the old stress audit.sources omitted them.'])
    figure(stress, 'stress', 8)
    for path in ('tables/stress_chain_concurrency.csv', 'tables/stress_growth_slopes.csv',
                 'analysis/stress_matrix_plan.json', 'analysis/stress_matrix_audit.json'):
        asset(stress, 'stress', path, 'dedicated_old_offline_appendix')
    for length in (1, 2, 4, 8, 16, 32):
        for concurrency in (1, 2, 4, 8, 16):
            stem = f'stress_v3_L{length}_K{concurrency}'
            for suffix in ('status', 'process'):
                asset(stress, 'stress', f'analysis/{stem}_{suffix}.json', 'dedicated_old_offline_execution_metadata')
    raw(stress, 'stress', [name for name in raw_inventory if re.fullmatch(r'raw/stress_v3_L(?:1|2|4|8|16|32)_K(?:1|2|4|8|16)(?:_batches\.jsonl|_warmup\.json|\.jsonl)', name)]
        + ['raw/resources_stress_matrix_v3.jsonl'])
    fault = batch('fault', 'fault', 'historical dedicated fault fixture table, separated from old real external rows',
        ['corrupt_state/wrong_memory semantic limits are retained with the dedicated audit; fixture success is not model robustness.',
         'Mixed report table06 is forbidden; only stress_fault_recovery.csv is eligible for the fault table section.'])
    asset(fault, 'fault', 'tables/stress_fault_recovery.csv', 'original_dedicated_offline_fault_table', target_table=6)
    for path in ('analysis/stress_fault_semantic_audit.json', 'analysis/stress_fault_plan.json'):
        asset(fault, 'fault', path, 'dedicated_old_offline_semantics')
    for mode in FAULTS:
        for suffix in ('status', 'process'):
            asset(fault, 'fault', f'analysis/stress_fault_{mode}_{suffix}.json', 'dedicated_old_offline_execution_metadata')
    raw(fault, 'fault', [f'raw/stress_fault_{mode}.jsonl' for mode in FAULTS] + ['raw/resources_stress_faults_v1.jsonl'])
    protocol = batch('protocol_v3', 'protocol', 'historical protocol-v3 independent producer/consumer batches only',
        ['Original protocol-v2 and its failures remain archive_only and cannot supply these values. No new protocol timing is run.'])
    for path in ('analysis/stats/protocol_v3_cells.csv', 'analysis/protocol_v3_audit.json',
                 'analysis/protocol_v3_plan.json', 'analysis/protocol_v3_status.json',
                 *[f'analysis/protocol_v3_batch_{i}.json' for i in (1, 2, 3)]):
        asset(protocol, 'protocol', path, 'independent_oldbatch_appendix')
    raw(protocol, 'protocol', [f'raw/protocol_v3_batch_{i}.jsonl' for i in (1, 2, 3)] + ['raw/resources_protocol_v3.jsonl'])
    reuse = batch('state_reuse', 'reuse', 'historical state-reuse loop fixture, independent oldbatch appendix',
        ['Three loop batches are not three independent process batches; no model/IPC generalization is added.'])
    for path in ('manifests/state_reuse_plan.json', 'analysis/state_reuse_status.json'):
        asset(reuse, 'reuse', path, 'independent_oldbatch_appendix')
    raw(reuse, 'reuse', ['raw/state_reuse.jsonl'])
    continuous = batch('continuous100', 'continuous', 'historical100-round SS1 fixed fixture, independent oldbatch appendix',
        ['100 rounds do not establish unbounded stability. Preserve FD first/last log-open boundary and steady delta; no new continuous stress is run.'])
    asset(continuous, 'continuous', 'analysis/stress_continuous_100_status.json', 'independent_oldbatch_appendix')
    raw(continuous, 'continuous', ['raw/stress_continuous_100.jsonl'])
    stage = batch('state_phase_v3', 'state_stage_supplement', 'historical corrected state-phase-v3 stage supplement, independent from original figure05',
        ['Registered and executed analyzer hashes/correction receipt remain distinct. Nested inclusive phases remain nested and cannot split old RTT into mutually exclusive costs.',
         'Original failed v2 and v3 recomputed_v1 are retained archive_only; recomputed_v2 is the separately executed supplement.'])
    for path in ('manifest.json', 'prospective_registration.json', 'analysis/execution.json',
                 'analysis/recomputed_v2/stage_cells.csv', 'analysis/recomputed_v2/matrix_audit.json',
                 'analysis/recomputed_v2/stage_join_audit.json'):
        asset(stage, 'state_stage_supplement', 'state_measurement_correction_v3/' + path, 'independent_oldbatch_stage_supplement')
    raw(stage, 'state_stage_supplement', [name for name in raw_inventory
        if re.fullmatch(r'state_measurement_correction_v3/raw/(?:producer|child_stage|resources)_batch_[123]\.jsonl', name)])
    stage_audit = meta.json(old / 'state_measurement_correction_v3/analysis/recomputed_v2/matrix_audit.json', 'actual stage-v3 analysis receipt with registered/executed identities')
    stage_correction = meta.json(old / 'analysis/state_measurement_correction_v3_analyzer_correction_v1.json', 'original PREPARED correction receipt retained beside actual executed stage audit')
    stage['analyzer_identity'] = {key: stage_audit.get(key) for key in ('status', 'registered_analyzer_sha256',
        'registered_analyzer_source_sha256', 'executed_analyzer_sha256', 'analyzer_correction_receipt_sha256')}
    stage['analyzer_identity']['original_correction_receipt_status'] = stage_correction.get('status')

    # Store source/QA/rebuild proof paths as metadata references only. They may
    # contain mixed historical fields; only the filtered offline proof below is
    # a numerical/visual authority, never old real/retrieval values.
    proof_paths = [old / 'analysis/report_qa_v4/visual_and_numeric_findings.json',
                   old / 'analysis/report_qa_v4/execution_receipt.json',
                   rebuild / 'rebuild_manifest.json', rebuild / 'pdf_pixel_qa_v1/audit.json',
                   rebuild / 'logs/input_copy_inventory.json']
    selected_figure_names = {f'figure_{number:02}_{width}' for number in (5, 6, 8) for width in ('single', 'double')}
    selected_pixels = [row for row in pixel['rendered'] if row['name'] in selected_figure_names]
    if len(selected_pixels) != 6 or not all(row.get('equal_pixels') is True for row in selected_pixels):
        raise ValueError('six offline rebuilt figure pixel proofs are not complete')
    numeric = rebuilt['report_numerical_identity']
    selected_numeric = {}
    for relative in ('figures/figure_05.csv', 'figures/figure_06.csv', 'figures/figure_08.csv', 'tables/table_04.csv'):
        expected, actual = numeric['expected_sha256'][relative], numeric['rebuilt_sha256'][relative]
        if expected != actual or expected != report_checks[relative] or actual != rebuild_checks[relative]:
            raise ValueError('selected original/rebuilt CSV hashes do not join')
        selected_numeric[relative] = {'original_sha256': expected, 'rebuilt_sha256': actual}
    for value in batches:
        value['independent_rebuild_evidence'] = [{'path': str(path.resolve()).replace('\\', '/'),
            'source_root': str((old if path.is_relative_to(old) else rebuild).resolve()).replace('\\', '/'),
            'relative_path': path.relative_to(old if path.is_relative_to(old) else rebuild).as_posix(),
            **meta.records[str(path.resolve())],
            'applicable_scope': 'selected05/06/08/table04 proof plus opaque offline input inventory; not old real/retrieval values',
            'interpretation': 'existing historical proof; not a new corrected-study rebuild'} for path in proof_paths]
    existing_rebuild_proof = {'rebuild_root': str(rebuild.resolve()).replace('\\', '/'),
        'rebuild_status_retained': rebuilt['status'], 'original_input_integrity': rebuilt.get('original_input_integrity'),
        'numeric_identity_status_retained': numeric['status'], 'selected_csv_hashes': selected_numeric,
        'pixel_status_retained': pixel['status'], 'selected_pixel_proof': [{key: row.get(key) for key in
            ('name', 'equal_pixels', 'pdf_sha256', 'png_sha256', 'reference_png_sha256', 'rgba_sha256', 'reference_rgba_sha256')} for row in selected_pixels],
        'selected_rebuilt_file_hashes': {relative: rebuild_checks[relative] for relative in rebuild_checks
            if re.fullmatch(r'figures/figure_0[568](?:_(?:single|double))?\.(?:csv|pdf|svg|png)|tables/table_04\.csv', relative)},
        'original_rendered_pdf_hashes': {Path(row['pdf'].replace('\\', '/')).name: row['pdf_sha256']
            for row in qa_execution['rendered'] if Path(row['pdf'].replace('\\', '/')).stem in selected_figure_names},
        'selected_offline_analysis_steps': [{key: step.get(key) for key in
            ('step', 'source_sha256', 'start_utc', 'end_utc', 'status', 'exit_code', 'derived_audit')}
            for step in rebuilt.get('steps', []) if step.get('step') in {
                '01_state_quantization_and_legacy_audit', '03_stress_faults', '03b_state_stage_supplement', '04_protocol_v3_audit'}],
        'proof_scope': '05/06/08/table04 numeric/pixel proof and four saved offline analysis-step receipts only; pixel equality is not PDF byte equality; no old real/retrieval numbers projected'}
    archive_metadata = []
    for relative in ('state_measurement_correction_v2/manifest.json', 'state_measurement_correction_v2/analysis/execution.json',
                     'state_measurement_correction_v3/analysis/recomputed_v1/execution_failure.json'):
        value = meta.json(old / relative, 'archive-only original failed-stage metadata; no metric authorization')
        archive_metadata.append({'relative_path': relative, 'sha256': meta.records[str((old / relative).resolve())]['sha256'],
                                 'status_retained': value.get('status'), 'role': 'archive_only'})
    archive_only = {'policy': 'REFERENCES_ONLY_NOT_APPROVED_AS_ASSETS_OR_NEW_METRICS',
        'original_qa_v2_status': old_qa.get('status'), 'selection_status_retained': selected.get('status'),
        'original_protocol_v2_raw': {name: {key: item[key] for key in ('sha256', 'bytes')} for name, item in raw_inventory.items()
            if re.fullmatch(r'raw/protocol_v2_batch_[123]\.jsonl', name)},
        'original_state_v2_raw': {name: {key: item[key] for key in ('sha256', 'bytes')} for name, item in raw_inventory.items()
            if name.startswith('state_measurement_correction_v2/raw/')},
        'stage_v3_first_failed_analysis': 'state_measurement_correction_v3/analysis/recomputed_v1/execution_failure.json',
        'archive_metadata': archive_metadata, 'archive_raw_rehashed_by_verify': False}
    manifest = {'schema_version': 'historical-offline-byte-assets-v1', 'status': 'PREPARED_NOT_REHASHED',
        'verification_status': 'NOT_EXECUTED', 'prepared_utc': utc(), 'helper_sha256': sha_file(Path(__file__)),
        'workspace_root': str(workspace).replace('\\', '/'), 'batches': batches,
        'metadata_references': meta.records, 'original_report_version': 'report_v3', 'source_package_version': 'source_package_formal_20261019_v2',
        'old_runtime': rebuilt.get('runtime'), 'historical_source_identity': source,
        'original_qa_state': {'status': qa['status'], 'report_source_sha256': qa['report_source_sha256'],
            'boundaries': qa.get('boundaries'), 'original_execution_status': qa_execution.get('status')},
        'existing_independent_rebuild_proof': existing_rebuild_proof, 'archive_only': archive_only,
        'unindexed_small_csv_opaque_preparation_enabled': args.allow_small_unindexed_csv_bytes,
        'do_not_copy': list(FORBIDDEN) + ['old real figures02/03/04/09', 'old retrieval figure07', 'old abstract or main statistics'],
        'approval_scope': 'Only a future successful quiet-window verify may produce an APPROVED manifest. Prepared metadata is not fresh raw verification or rebuild.',
        'raw_bodies_read_at_preparation': 0, 'new_formal_outcomes_read': False, 'model_or_benchmark_calls': 0}
    args.destination.mkdir(parents=True, exist_ok=False)
    path = args.destination / 'prepared_manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    receipt = {'status': 'PREPARED_NOT_REHASHED', 'manifest_sha256': sha_file(path), 'helper_sha256': manifest['helper_sha256'],
        'batches': len(batches), 'assets': sum(len(value['assets']) for value in batches),
        'raw_references': sum(len(value['raw_hash_references']) for value in batches),
        'unique_offline_raw_files': len({row['relative_path'] for value in batches for row in value['raw_hash_references']}),
        'raw_bodies_read': 0, 'heavy_or_model_tasks_executed': False}
    (args.destination / 'preparation_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt))
    return 0


def quiet_gate(out, path):
    if out.name != STUDY or not path.resolve().is_relative_to(out / 'analysis'):
        raise ValueError('quiescence must belong to the corrected study analysis directory')
    execution = json.loads((out / 'formal_execution_v1/execution.json').read_text(encoding='utf-8'))
    quiet = json.loads(path.read_text(encoding='utf-8'))
    if (execution.get('status') != 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT'
            or execution.get('supervised_child_observed_running') is True
            or [row.get('name') for row in execution.get('stages', [])] != ['main', 'native_remaining', 'external']
            or not all(row.get('status') == 'TERMINAL' and row.get('exit_code') == 0 and row.get('finished_utc') for row in execution['stages'])
            or quiet.get('study_id') != STUDY or quiet.get('status') != 'ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING'
            or quiet.get('active_real_timing_processes') != [] or quiet.get('active_retrieval_timing_processes') != []):
        raise RuntimeError('actual timing/quiescence gate rejected opaque verification')
    verified = datetime.fromisoformat(quiet['verified_utc'].replace('Z', '+00:00'))
    finished = datetime.fromisoformat(execution['finished_utc'].replace('Z', '+00:00'))
    if verified.tzinfo is None or finished.tzinfo is None or verified < finished:
        raise RuntimeError('actual quiescence receipt predates termination')


def verify(args):
    out = args.study_out.resolve()
    quiet_gate(out, args.quiescence_receipt)
    if args.destination.exists() or not valid_sha(args.prepared_sha256) or sha_file(args.prepared_manifest) != args.prepared_sha256:
        raise ValueError('fresh destination and exact reviewed prepared SHA required')
    manifest = json.loads(args.prepared_manifest.read_text(encoding='utf-8'))
    if manifest.get('status') != 'PREPARED_NOT_REHASHED' or manifest.get('helper_sha256') != sha_file(Path(__file__)):
        raise ValueError('prepared source/manifest identity changed')
    args.destination.mkdir(parents=True, exist_ok=False)
    rows, failures = [], []
    checked = {}
    before_quiet_sha = sha_file(args.quiescence_receipt)
    def check(path, expected, size, scope):
        path = path.resolve()
        if path in checked:
            if checked[path] != (expected, size):
                raise ValueError('same historical path has conflicting authorities')
            return
        if not valid_sha(expected) or path.name in ('.env', 'model.env') or '/run/secrets/' in path.as_posix():
            raise ValueError('not an opaque offline verification input')
        actual = sha_file(path)
        okay = actual == expected and path.stat().st_size == size
        rows.append({'path': str(path).replace('\\', '/'), 'expected_sha256': expected,
                     'actual_sha256': actual, 'expected_bytes': size, 'actual_bytes': path.stat().st_size,
                     'scope': scope, 'status': 'VERIFIED' if okay else 'FAIL_RETAINED'})
        if not okay:
            failures.append({'code': 'OPAQUE_BYTE_IDENTITY_MISMATCH', 'path': str(path)})
        checked[path] = (expected, size)
    try:
        for name, record in manifest['metadata_references'].items():
            check(Path(name), record['sha256'], record['bytes'], 'historical bounded metadata/proof/source identity')
        rebuild = Path(manifest['existing_independent_rebuild_proof']['rebuild_root'])
        for batch in manifest['batches']:
            root = Path(batch['source_root']).resolve()
            if root.name != 'formal_20261019' or batch.get('original_source_sha256') != OLD_SOURCE or batch.get('evidence_class') != 'OFFLINE_MECHANISM_BATCH':
                raise ValueError('historical batch/source identity differs')
            for asset in batch['assets']:
                relative = safe_relative(asset['relative_path'])
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or not dedicated_asset(relative.as_posix()) or 'raw' in relative.parts or asset['topic'] not in TOPICS:
                    raise ValueError('asset escaped dedicated historical scope')
                check(path, asset['sha256'], asset['bytes'], 'historical dedicated offline asset only')
            for reference in batch['raw_hash_references']:
                relative = safe_relative(reference['relative_path'])
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or not ('raw' in relative.parts):
                    raise ValueError('opaque raw reference escaped historical root')
                check(path, reference['sha256'], reference['bytes'], 'opaque offline raw; not parsed or copied into report')
                copy_path = (rebuild / safe_relative(reference['existing_rebuild_relative_path'])).resolve()
                if not copy_path.is_relative_to(rebuild):
                    raise ValueError('existing rebuild raw path escaped')
                check(copy_path, reference['existing_rebuild_sha256'], reference['existing_rebuild_bytes'], 'opaque existing offline rebuild raw copy')
        proof = manifest['existing_independent_rebuild_proof']
        for relative, expected in proof['selected_rebuilt_file_hashes'].items():
            safe_relative(relative)
            path = rebuild / 'report' / relative
            # Existing checksums supply SHA; prepared metadata supplies exact
            # target byte identity only at verification, not an invented size.
            check(path, expected, path.stat().st_size, 'existing rebuilt05/06/08/table04 asset')
        quiet_gate(out, args.quiescence_receipt)
        if sha_file(args.quiescence_receipt) != before_quiet_sha or sha_file(args.prepared_manifest) != args.prepared_sha256:
            raise RuntimeError('quiescence/prepared authority changed during verification')
    except Exception as exc:
        failures.append({'code': 'VERIFICATION_EXCEPTION_RETAINED', 'error_type': type(exc).__name__})
    receipt = {'status': 'VERIFIED' if not failures else 'FAIL_RETAINED', 'created_utc': utc(),
        'helper_sha256': sha_file(Path(__file__)), 'prepared_manifest_sha256': args.prepared_sha256,
        'quiescence_receipt_sha256': before_quiet_sha, 'scope': 'HISTORICAL_OFFLINE_BYTE_ASSETS_AND_OPAQUE_HASH_LINEAGE_ONLY',
        'files_checked': len(rows), 'failures': failures, 'file_checks': rows,
        'raw_json_parsed': False, 'new_formal_outcomes_read': False, 'model_or_benchmark_or_analysis_runs': 0,
        'new_rebuild_performed': False, 'archive_only_inputs_revalidated': False,
        'limitations': BASE_LIMITS + ['This host-side verifier consumes actual root-observed quiescence; it does not itself inspect live processes or acquire container Linux locks.']}
    receipt_path = args.destination / 'verification_receipt.json'
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not failures:
        manifest.update(status='APPROVED_OFFLINE_BYTE_ASSET_MANIFEST', verification_status='VERIFIED',
            verified_utc=receipt['created_utc'], verification_receipt=str(receipt_path.resolve()).replace('\\', '/'),
            verification_receipt_sha256=sha_file(receipt_path),
            verification_scope=receipt['scope'], prepared_manifest_sha256=args.prepared_sha256,
            report_authorization='copy exact dedicated historical offline byte assets only; never new real/retrieval metrics')
        path = args.destination / 'approved_offline_manifest.json'
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': receipt['status'], 'files_checked': len(rows), 'failures': len(failures)}))
    return 0 if not failures else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--workspace-root', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--allow-small-unindexed-csv-bytes', action='store_true')
    v = commands.add_parser('verify')
    v.add_argument('--prepared-manifest', type=Path, required=True)
    v.add_argument('--prepared-sha256', required=True)
    v.add_argument('--study-out', type=Path, required=True)
    v.add_argument('--quiescence-receipt', type=Path, required=True)
    v.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    return prepare(args) if args.operation == 'prepare' else verify(args)


if __name__ == '__main__':
    raise SystemExit(main())
