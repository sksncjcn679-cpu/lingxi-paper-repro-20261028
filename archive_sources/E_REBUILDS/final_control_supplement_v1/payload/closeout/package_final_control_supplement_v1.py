"""Copy/hash a finite approved metadata set; never invoke experiments, Docker or APIs.

The three v2 input packages and B8 archive are read-only. Final 155-matrix hashes
are mandatory CLI inputs, so a pending matrix cannot silently be packaged.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
NEW = ROOT / 'paper_experiments_20261019_pilot/protocol_repair_20261020_v1'
B8 = Path('E:/codex_rebuilds/corrected_postrun_v2')
INPUT = Path('E:/codex_rebuilds/delivery_packages_v2')
DOCS = Path(__file__).resolve().parent
REF = B8 / 'reference/virtual/evidence/protocol_repair_20261020_v1'
IND = B8 / 'independent_rebuild/virtual/evidence/protocol_repair_20261020_v1'
AUX = Path('E:/codex_rebuilds/both_success_auxiliary_v1')
RECOVERY = Path('E:/codex_rebuilds/original_response_format_recovery_v1')
MAX_SOURCE_BYTES = 2_000_000
PACKAGE_PINS = {
    'raw_data.zip': {'bytes': 206350327, 'sha256': 'cddcf0646fde92456de0a51c2c77f446e93b8229aa47da4f3c0a83d505dda76c', 'index_sha256': 'bc329ba5ef996be09cbcdbb83b3f15fee3f810103c74108f72331507aaeaa1ff'},
    'source_control.zip': {'bytes': 3571621, 'sha256': '456442204609e41b76ee02fd1cbd4a8f6160014a3ffde3bf5ac289dcf186a78b', 'index_sha256': 'f09189624ad1cf5544f260ff7580df21b297284df278152443ed84f90f470897'},
    'dependency_image.tar': {'bytes': 354260480, 'sha256': '086c5b13fa7735a2546fedcae540e97b3f05af10a234b30c8047455f015b0198'},
}
PINNED = {
    'input': '289905ac165e5e2bc3cae4fdea15e3327443bf0eb8a4547f66120780aadc30ea',
    'package': '2c31d20757ccc5e90293bfc8bfd1210fbbe87015b0e33af67a248e1b1cd6b4bd',
    'reference': '5d900d51828e0273f6d2e8336dd98803736518e58e726655978920590d77850d',
    'execution': '1c61b7a5edd15ed5835365c2871724a9c454ef48942b8e650ad1870daa51778e',
    'numerical': '00165adb368ba108a0a3315491e72b5984401c52dc26f8ebef1916308a2e2a86',
    'root_review': 'b39ccdb0266f10d87dbf65395734416d8213123fd6db9b96180222f43f2d5542',
    'terminal_witness': '60a7f0d3cf3a610542908c16bb28b39fb9c19451178b3fb1d9b1d9bb3d06a7e2',
    'peer': '53e51abfce0006be61c25aa5d47a7ce2c2da58b38ddbd03c13916f2a6b8787f5',
    'source': 'cf875813878baac140d35cae222bad90c5caa8b577d618e7f79847276522eba5',
    'dependency': '6a2be7ca585ab1e0bda267bde6e01d0b70c51e85fbbea84453f198958e202bb5',
    'authorization': 'fca6661ddf8487da48cb7e36f99f9f009f3bf75f84cef44d3fc780c6ab9d2659',
    'english': '109b84e2e3dfebe46ce9594774842f02e8417e06c98bbf8dc44805ee4fd27cdf',
    'single': 'd6224fd7577109336c3ee261e72917e75b8c073e67ece6856ad668fa9fa3fc65',
    'double': 'c63f25fa8e3d17110b818110ee4e3b8046498a2a7c7155b3196d715bb8a4ed59',
    'root_numeric': '98c3ae4c878371c383f472a9875b735b6970a4c11f7a8e3ae3baeff0df5d22f7',
    'chinese_receipt': '4bcfe689b0dcebcdf911821b907954f6471017b0805ab74d51fd27be7682031d',
    'chinese_manifest': '80b1369d197ebc77b54113e7d4ae7bc85d00eda042c0b15f1dae197fb32ee84c',
    'publication': 'c85f81e3ac270ea41273f153ce73676b41f5fcc2d8d1661aae04ebaa753ffafc',
}


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def regular_small(path):
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f'Not a regular file: {path}')
    approved_larger_metadata = {
        NEW/'analysis/claims_raw_index_v1/observed_unit_raw_index.csv': 9_000_000,
        AUX/'reference/analysis/results.json': 3_000_000,
        RECOVERY/'reference/analysis/response_format_flags.csv': 5_000_000,
    }
    size_limit = approved_larger_metadata.get(path, MAX_SOURCE_BYTES)
    if path.stat().st_size > size_limit:
        raise ValueError(f'Outside small-control scope: {path}')
    if any(part.lower() in {'.env', 'secrets', 'credentials', 'mpl_cache'} for part in path.parts):
        raise ValueError(f'Forbidden input path: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--matrix-sha256', required=True)
    parser.add_argument('--matrix-receipt-sha256', required=True)
    parser.add_argument('--quality-directory', type=Path, required=True)
    parser.add_argument('--quality-receipt-sha256', required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists():
        raise ValueError('Entirely new output directory required; never overwrite snapshots.')
    for protected in [ROOT.resolve(), B8.resolve(), INPUT.resolve()]:
        if destination == protected or destination.is_relative_to(protected):
            raise ValueError('Output must be outside B8, input packages and the scientific workspace.')
    matrix_dir = NEW / 'analysis/completion_audit_v3'
    matrix = matrix_dir / 'requirement_evidence.csv'
    matrix_receipt = matrix_dir / 'audit_receipt.json'
    if sha(matrix) != args.matrix_sha256 or sha(matrix_receipt) != args.matrix_receipt_sha256:
        raise ValueError('Final matrix is not the explicitly supplied stable snapshot.')
    original = ROOT / 'paper_experiments_20261019_pilot/formal_20261019/analysis/protocol_requirement_evidence_matrix_precompletion.csv'
    with original.open(encoding='utf-8-sig', newline='') as f:
        old_rows = list(csv.DictReader(f))
    with matrix.open(encoding='utf-8-sig', newline='') as f:
        new_rows = list(csv.DictReader(f))
    # Preserve all authority columns that exist in both snapshots, especially ID,
    # original requirement and original location. No status totals become percent.
    if len(old_rows) != 155 or len(new_rows) != 155:
        raise ValueError('Expected all original 155 requirement rows.')
    authority_columns = [k for k in old_rows[0] if k.lower() in {
        'requirement_id', 'id', 'section', 'protocol_section', 'source_section', 'source_location',
        'source_lines', 'requirement', 'original_requirement', 'original_text',
        '原方案位置', '原方案原文', '要求编号'} and k in new_rows[0]]
    if not authority_columns:
        raise ValueError('No original matrix authority columns recognized; inspect rather than guess.')
    for old, new in zip(old_rows, new_rows):
        for key in authority_columns:
            if old[key] != new[key]:
                raise ValueError(f'Original matrix authority/order changed: {key}')

    # A structural fresh-directory assembly proof, without extraction or another
    # rebuild. Reuse actual prior member decode SHA/CRC receipts, compare the
    # current ZIP central directories, and rehash package bytes (not 2.4GB raw).
    inputs = read(B8/'archive/input_manifest.json')
    archived_package = read(B8/'archive/package_manifest.json')
    delivery = read(INPUT/'delivery_manifest.json')
    packaging = read(INPUT/'packaging_receipt.json')
    if sha(B8/'archive/input_manifest.json') != PINNED['input']:
        raise ValueError('Assembly manifest changed.')
    expected_archive = {'virtual/'+name.lstrip('/'): info['sha256'] for name, info in inputs['virtual_inputs'].items()}
    expected_archive.update({'control/'+name: digest for name, digest in inputs['control_sha256'].items()})
    if inputs['control_sha256'] != archived_package['control_sha256']:
        raise ValueError('Five original controls disagree across immutable manifests.')
    expected_archive['dependencies/image.tar'] = inputs['dependency_image_archive_sha256']
    for name, key in [('input_manifest.json','input'), ('package_manifest.json','package'), ('reference_numeric_manifest.json','reference')]:
        expected_archive[name] = PINNED[key]
        if sha(B8/'archive'/name) != PINNED[key]:
            raise ValueError('Three supplement archive manifests are not pinned bytes.')
    if archived_package['input_manifest_sha256'] != PINNED['input'] or archived_package['reference_numeric_manifest_sha256'] != PINNED['reference']:
        raise ValueError('Package manifest does not bind the actual input/reference manifests.')
    assembly_members = {}
    package_checks = []
    if len(delivery['packages']) != 3 or {item['filename'] for item in delivery['packages']} != set(PACKAGE_PINS):
        raise ValueError('Exactly the three originally approved v2 packages are required.')
    for package_info in delivery['packages']:
        package_path = INPUT/package_info['filename']
        pin = PACKAGE_PINS[package_info['filename']]
        if package_info['bytes'] != pin['bytes'] or package_info['sha256'] != pin['sha256']:
            raise ValueError('Current delivery metadata differs from original pinned package identity.')
        if package_path.stat().st_size != pin['bytes'] or sha(package_path) != pin['sha256']:
            raise ValueError('An original v2 package byte identity changed.')
        package_checks.append({'filename': package_info['filename'], 'bytes': package_path.stat().st_size, 'current_sha256': package_info['sha256'], 'original_actual_sha_unchanged': True})
        if package_info['filename'] == 'dependency_image.tar':
            if package_info['sha256'] != expected_archive['dependencies/image.tar']:
                raise ValueError('Dependency tar is not manifest-bound.')
            assembly_members['dependencies/image.tar'] = {'sha256': package_info['sha256'], 'source': 'dependency_image.tar'}
            continue
        index_path = INPUT/package_info['members_index']['filename']
        if package_info['members_index']['sha256'] != pin['index_sha256'] or sha(index_path) != pin['index_sha256']:
            raise ValueError('Previously actual decoded member proof changed.')
        with index_path.open(encoding='utf-8-sig', newline='') as f:
            index = list(csv.DictReader(f))
        by_name = {row['member']: row for row in index}
        if len(index) != package_info['regular_members'] or len(by_name) != len(index):
            raise ValueError('Member index count or uniqueness differs.')
        with zipfile.ZipFile(package_path) as z:
            infos = z.infolist()
            if len(infos) != len(index) or set(z.namelist()) != set(by_name):
                raise ValueError('Current ZIP central members differ from actual decoded proof.')
            for info in infos:
                member = info.filename; rel = PurePosixPath(member)
                if info.is_dir() or rel.is_absolute() or '..' in rel.parts or '\\' in member or ':' in member:
                    raise ValueError('Unsafe/nonregular assembly member.')
                row = by_name[member]
                if info.file_size != int(row['bytes']) or f'{info.CRC:08x}' != row['crc32'].lower() or row['source_manifest_sha256'] != row['decoded_readback_sha256']:
                    raise ValueError('Central size/CRC and actual decoded proof disagree.')
                if member in assembly_members or expected_archive.get(member) != row['decoded_readback_sha256']:
                    raise ValueError('Duplicate/extra input member or manifest fingerprint mismatch.')
                assembly_members[member] = {'sha256': row['decoded_readback_sha256'], 'source': package_info['filename'], 'actual_prior_decoded_SHA_CRC_proof': True}
    for name, key in [('input_manifest.json','input'), ('package_manifest.json','package'), ('reference_numeric_manifest.json','reference')]:
        if name in assembly_members:
            raise ValueError('Supplement archive member overlaps original packages.')
        assembly_members[name] = {'sha256': PINNED[key], 'source': 'final_control_supplement_v1.zip:archive/'+name}
    if len(expected_archive) != 551 or len(assembly_members) != 551 or set(expected_archive) != set(assembly_members):
        raise ValueError('Fresh assembled archive is not exactly 542+5+1+3=551 files.')
    if len({name.casefold() for name in assembly_members}) != 551:
        raise ValueError('Case-insensitive destination member collision.')
    if any(assembly_members[name]['sha256'] != expected_archive[name] for name in expected_archive):
        raise ValueError('Assembled archive SHA union is not exactly expected.')
    absent = {'virtual/'+name.lstrip('/') for name in inputs['absent_virtual_inputs']}
    if absent.intersection(assembly_members):
        raise ValueError('An originally absent status file was synthesized.')
    assembly_receipt = {'status': 'ACTUAL_DISTRIBUTION_PATH_SHA_UNION_551_EXACT_NO_SECOND_REBUILD', 'verified_utc': utc(), 'input_manifest_sha256': PINNED['input'], 'package_manifest_sha256': PINNED['package'], 'reference_numeric_manifest_sha256': PINNED['reference'], 'counts': {'raw_ZIP_members': 339, 'source_control_ZIP_members': 208, 'virtual_inputs': 542, 'original_controls': 5, 'dependency_tar': 1, 'supplement_archive_manifests': 3, 'exact_fresh_archive_files': 551}, 'duplicates': [], 'missing': [], 'extra': [], 'unsafe_or_case_colliding_paths': [], 'five_control_SHA_agree_with_both_manifests': True, 'three_meta_SHA_match_pinned_actual_archive': True, 'current_package_byte_checks': package_checks, 'prior_actual_decoded_ZIP_SHA_CRC_proofs_reused': True, 'current_ZIP_central_member_size_CRC_checked': True, 'decoded_raw_or_export_payload_read_or_rehashed': False, 'second_rebuild_or_analysis_executed': False, 'absent_status_files_not_synthesized': sorted(absent), 'member_fingerprints': assembly_members, 'scope': 'Structural and byte-identity distribution assembly proof only; original compressed ZIP/tar bytes were hashed, but decoded2.4GB raw was not read again. No extraction, Docker/model/API, analysis stage or fresh rebuild was executed.'}

    sources = []
    def add(src, rel, expected=None):
        src = Path(src)
        regular_small(src)
        relpath = PurePosixPath(rel)
        if relpath.is_absolute() or '..' in relpath.parts:
            raise ValueError(f'Unsafe package member: {rel}')
        digest = sha(src)
        if expected and digest != expected:
            raise ValueError(f'Pinned source SHA mismatch: {src}')
        sources.append((src, str(relpath), digest, src.stat().st_size))

    for name, key in [('input_manifest.json', 'input'), ('package_manifest.json', 'package'), ('reference_numeric_manifest.json', 'reference')]:
        add(B8/'archive'/name, 'archive/'+name, PINNED[key])
    for filename in ['README_reproduce.md', 'claims_to_evidence.md', 'limitations.md']:
        add(DOCS/filename, 'closeout/'+filename)
    add(Path(__file__), 'closeout/package_final_control_supplement_v1.py')
    add(DOCS/'verify_control_supplement.py', 'verify_control_supplement.py')
    add(matrix, 'evidence/requirements/completion_audit_v3/requirement_evidence.csv', args.matrix_sha256)
    add(matrix_receipt, 'evidence/requirements/completion_audit_v3/audit_receipt.json', args.matrix_receipt_sha256)
    final_matrix_receipt = read(matrix_receipt)
    for filename, expected in final_matrix_receipt['output_sha256'].items():
        if filename != 'requirement_evidence.csv':
            add(matrix_dir/filename, 'evidence/requirements/completion_audit_v3/'+filename, expected)
    index_dir = NEW/'analysis/claims_raw_index_v1'
    index_receipt_sha = 'd7a1e8b8be87b1ebf74ce89fd3c84f3382e1a07990233b6a805b5653dd1d5784'
    add(index_dir/'coverage_receipt.json', 'evidence/claims_raw_index_v1/coverage_receipt.json', index_receipt_sha)
    index_receipt = read(index_dir/'coverage_receipt.json')
    if not index_receipt.get('actual_terminal') or index_receipt['coverage']['observed_index_rows'] != 6504 or index_receipt['coverage']['primary_pair_rows'] != 1764 or index_receipt['coverage']['public_in_primary_or_formal_selector'] != 0:
        raise ValueError('Claim/raw locator index is not the actual terminal scoped artifact.')
    for filename, info in index_receipt['products'].items():
        add(index_dir/filename, 'evidence/claims_raw_index_v1/'+filename, info['sha256'])
    quality_dir = args.quality_directory.resolve()
    quality_identity_sha = '3fca8c4163a126bf91a40644038df9ecf45ec0ea87f7035fb0d0dc8243035b8b'
    if quality_dir != RECOVERY.resolve() or args.quality_receipt_sha256 != quality_identity_sha:
        raise ValueError('M002 requires the explicitly approved actual two-run recovery checkpoint.')
    add(quality_dir/'exact_identity_receipt.json', 'evidence/original_response_format_recovery_v1/exact_identity_receipt.json', quality_identity_sha)
    add(quality_dir/'root_review_v1.json', 'evidence/original_response_format_recovery_v1/root_review_v1.json', '6f72e04080285f7c0ffb8799d449e8062c39f3329053730ae9f7c3fd2c301163')
    add(quality_dir/'peer_review_v1.json', 'evidence/original_response_format_recovery_v1/peer_review_v1.json', 'a24c6c60a532c297f15649665bb368694b41133a1bc10ddea455e36bab0277e0')
    add(quality_dir/'Invoke-OriginalResponseFormatRecovery.ps1', 'evidence/original_response_format_recovery_v1/Invoke-OriginalResponseFormatRecovery.ps1', '6f2a8196fb0e9edbd13768baefe48856de5111d95edfbba1ecde7b2b55f3aa96')
    add(quality_dir/'supplemental_replay_commands_v2.md', 'evidence/original_response_format_recovery_v1/supplemental_replay_commands_v2.md', 'cb155a1e60081d70586738217fab8109ebea5671caf1f76a3ba307eeb8f0ab34')
    add(quality_dir/'supplemental_replay_commands.md', 'evidence/original_response_format_recovery_v1/history/superseded_replay_commands_v1.md', 'e17a09eadf727b8e6bb2afa914d4b90c37de33d765fbfc1f98b9ff74dd116ff0')
    add(ROOT/'.local/original_response_format_static_peer_v1.json', 'evidence/original_response_format_recovery_v1/static_peer_v1.json', '70b03c190f6bf8c423a9e6a09de31e24fa4e42645dbe0df8bae7dc14d0622165')
    quality_identity = read(quality_dir/'exact_identity_receipt.json')
    if quality_identity['status'] != 'ACTUAL_TWO_OFFLINE_RESPONSE_RECOVERY_RUNS_EXACT_FOUR_FILE_IDENTITY' or quality_identity['model_calls_or_benchmarks'] != 0 or quality_identity['request_or_response_text_emitted'] is not False:
        raise ValueError('M002 recovery must retain exact actual bounded execution/non-disclosure.')
    for run in quality_identity['runs']:
        side=run['name']
        if run['guard']['exit_code'] != 0 or run['guard']['denied_count'] != 0:
            raise ValueError('M002 run is not actualguard0/deny0.')
        add(quality_dir/side/'execution_receipt.json', f'evidence/original_response_format_recovery_v1/{side}/execution_receipt.json', run['execution_receipt_sha256'])
        add(quality_dir/side/'guard_result.json', f'evidence/original_response_format_recovery_v1/{side}/guard_result.json', run['guard_receipt_sha256'])
        for filename in ['guard_events.jsonl','stderr.log','stdout.log']:
            add(quality_dir/side/filename, f'evidence/original_response_format_recovery_v1/{side}/'+filename)
    for item in quality_identity['files']:
        if not item['exact_bytes_equal'] or item['reference_sha256'] != item['independent_sha256']:
            raise ValueError('M002 four-output identity is not exact.')
        add(quality_dir/'reference/analysis'/item['path'], 'evidence/original_response_format_recovery_v1/results/'+item['path'], item['reference_sha256'])
    for filename, expected in [('recover_original_response_format_v1.py','96c0d206ea09ebf7f03849a8e521c692bf148b3fe6cd1133747b4ace32f93754'),('offline_analysis_runner.py','7a82d8b4263b127b938958bc6bca74d8db50f0517e2654e94547bd4c67f77e2d')]:
        add(quality_dir/'control'/filename, 'evidence/original_response_format_recovery_v1/control/'+filename, expected)
    add(quality_dir/'control/guard_configuration.json', 'evidence/original_response_format_recovery_v1/control/guard_configuration.json')
    add(quality_dir/'input/inputs.json', 'evidence/original_response_format_recovery_v1/input/inputs.json', 'f7dfc16b4dd9042682572e444171fec271b04b4727a1ec30b2744a6e548d202b')
    recovery_inputs=read(quality_dir/'input/inputs.json')
    for name, info in recovery_inputs['files'].items():
        if name not in {'model_io','provider_calls','observations','formal_plan'}:
            filename=PurePosixPath(info['path']).name
            add(quality_dir/'input'/filename, 'evidence/original_response_format_recovery_v1/input/'+filename, info['sha256'])
    add(quality_dir/'execution_scope.json', 'evidence/original_response_format_recovery_v1/execution_scope.json')
    for filename in ['execute_response_recovery_twice_v1.py','root_response_recovery_review_v1.py']:
        add(ROOT/'.local/both_success_auxiliary_v1'/filename, 'evidence/original_response_format_recovery_v1/operation_sources/'+filename)
    old_quality_dir = NEW/'analysis/automatic_quality_submetrics_v1'
    add(old_quality_dir/'correction_note_v1.json', 'evidence/automatic_quality_submetrics_history/correction_note_v1.json', '5b190717fbac1aed1fce3f0df21b86f6434316a24225c6279418bf03013ba00f')
    correction = read(old_quality_dir/'correction_note_v1.json')
    for filename, expected in correction['existing_five_files_unchanged_sha256'].items():
        add(old_quality_dir/filename, 'evidence/automatic_quality_submetrics_history/'+filename, expected)
    add(old_quality_dir/'source_storage_review_v1.json', 'evidence/automatic_quality_submetrics_history/source_storage_review_v1.json', '5e0e0c54cef6533570504477c5f4267aed5163d6fba5b57ff5f60482f14c7dc9')
    add(ROOT/'.local/finite_stress_ci_source_review_v1.json', 'evidence/finite_source_reviews/stress_ci_source_review_v1.json')
    frozen_drivers = ROOT/'paper_experiments_20261019_pilot/source_package_formal_20261019_v2/bundle/archived_drivers/frozen'
    for filename, expected in [('formal_stress_analyze.py','4d3c08bb6aaee5afd932ff806af349e561f189cdf411dfd1b95e3b880e0f22b1'),('formal_stress_matrix.py','f8653b1ae11df11cad8b1d5e172f9a8b84d9ce9db4495cf3f7959511909d8aab')]:
        add(frozen_drivers/filename, 'evidence/finite_source_reviews/frozen_stress_sources/'+filename, expected)
    add(original, 'evidence/requirements/original_requirement_matrix_155.csv')
    add(ROOT/'论文实验设计方案.md', 'evidence/authority/论文实验设计方案.md')
    add(ROOT/'paper_experiments_20261019_pilot/formal_20261019/manifests/protocol_changes.md', 'evidence/authority/protocol_changes.md', PINNED['authorization'])
    for src, rel, key in [
        (B8/'execution.json', 'evidence/B8/execution.json', 'execution'),
        (B8/'numerical_identity.json', 'evidence/B8/numerical_identity.json', 'numerical'),
        (NEW/'analysis/postrun_rebuild_v2_root_review_v1.json', 'evidence/B8/root_review.json', 'root_review'),
        (NEW/'analysis/postrun_rebuild_v2_terminal_tool_witness_v1.json', 'evidence/B8/terminal_tool_witness.json', 'terminal_witness'),
        (ROOT/'.local/final_scientific_review_v1/postrun_rebuild_v2_peer_review_v1.json', 'evidence/B8/peer_review.json', 'peer'),
        (NEW/'manifests/postrun_source_identities_v4.json', 'evidence/authority/postrun_source_identities_v4.json', 'source'),
        (B8/'archive/control/dependency_attestation.json', 'evidence/dependency/dependency_attestation.json', 'dependency'),
        (ROOT/'.local/english_figure_visual_qa_v1/visual_qa_receipt.json', 'evidence/QA/english/visual_qa_receipt.json', 'english'),
        (ROOT/'.local/chinese_figure_visual_qa_v1/single/final_v6/visual_qa_receipt.json', 'evidence/QA/chinese/single_final_v6.json', 'single'),
        (ROOT/'.local/chinese_figure_visual_qa_v1/double/final_v6/visual_qa_result.json', 'evidence/QA/chinese/double_final_v6.json', 'double'),
        (ROOT/'.local/chinese_figure_presentation_v1/root_file_numeric_review_v3.json', 'evidence/QA/chinese/root_file_numeric_review_v3.json', 'root_numeric'),
        (NEW/'delivery/chinese_figures_v1/package_receipt.json', 'evidence/chinese_delivery/package_receipt.json', 'chinese_receipt'),
        (NEW/'delivery/chinese_figures_v1/manifest.json', 'evidence/chinese_delivery/manifest.json', 'chinese_manifest'),
        (ROOT/'.local/chinese_figure_publication_20261021_v1/publication_verification.json', 'evidence/publication/chinese_rev20_verification.json', 'publication'),
    ]:
        add(src, rel, PINNED[key])
    execution = read(B8/'execution.json')
    numeric = read(B8/'numerical_identity.json')
    peer = read(ROOT/'.local/final_scientific_review_v1/postrun_rebuild_v2_peer_review_v1.json')
    if execution['actual_stage_runs'] != 18 or execution['paid_or_benchmark_commands'] != 0:
        raise ValueError('B8 is not the expected actual finite offline run.')
    if numeric.get('differences') or not peer.get('actual_terminal') or peer.get('new_source_or_numerical_mismatch_count') != 0:
        raise ValueError('B8 identity/peer is not the reviewed terminal snapshot.')
    for src in [
        ROOT/'.local/abstract_update_20261021/publication_verification.json',
        ROOT/'.local/statistical_report_publication_20261021_v1/publication_verification.json',
        ROOT/'.local/statistical_report_publication_20261021_v1/manifest.json',
    ]:
        add(src, 'evidence/publication/'+src.parent.name+'/'+src.name)
    for filename, expected in [
        ('probe.py', '3b0831fb7107547b26f4792f68fe4c6f633cd99a78f2da72283043fd675dc34b'),
        ('stdout.json', '8db5045412956b6b87471a64af58e3833ef8ccb661f5c87e6d5a0fadd1957844'),
        ('execution_receipt.json', 'f85fd6152db1cc2408ae48f721841751906ed46e228b70ded75cfc9e4f69cd02'),
        ('kernel_boundary_v2.py', 'a1966ecbe4c78d9cd780bf1cc357b2d2be23395bd5a8ae70903306179c0b0cbc'),
        ('kernel_stdout_v2.json', '04375f3024b40cdf8954c93d0121a4ed6e5039201ea15915c80ec69aef9e0748'),
        ('kernel_execution_receipt_v2.json', '55629bd822cb7800284c5aa7297db30b10699bcd5a335c76c57b42d58e94c43d'),
        ('docker_masked_paths_v2.json', 'ded8304039bc3a12a45a2b6958e8089e314e2fee517a68945d425233b004325a'),
    ]:
        add(ROOT/'.local/resource_io_boundary_probe_v1'/filename, 'evidence/resource_io_boundary/'+filename, expected)
    add(NEW/'analysis/resource_io_boundary_review_v1.json', 'evidence/resource_io_boundary/root_review.json', '9d1a50a3e8f1091610f2f78653df5439c1391c515733baacc3c1b4d4ef0f1e2e')
    # Publication readbacks are small bounded text snapshots, not API requests.
    for parent in ['statistical_report_publication_20261021_v1', 'chinese_figure_publication_20261021_v1']:
        for filename in ['readback.json', 'fragment.xml']:
            add(ROOT/'.local'/parent/filename, 'evidence/publication/'+parent+'/'+filename)
    for filename in ['abstract_metrics.json', 'manifest.json', 'primary_cost_effects.csv', 'quality_effects.csv']:
        add(NEW/'analysis/abstract_metrics_v1'/filename, 'evidence/abstract/projection/'+filename)
    for filename in ['results_abstract_zh.md', 'number_checklist.md', 'verification.json']:
        add(ROOT/'.local/final_abstract_draft_v2'/filename, 'evidence/abstract/wording_v2/'+filename)
    add(ROOT/'.local/final_abstract_draft_v1/verification.json', 'evidence/abstract/original_projection_verification.json')
    # Original section6.5 supplement: two completed read-only runs are separate
    # from the B8 checkpoint. Copy one set of equal results and both receipts;
    # do not duplicate observations45MB, primary7MB or formal_plan5MB.
    aux_identity = read(AUX/'exact_identity_receipt.json')
    add(AUX/'exact_identity_receipt.json', 'evidence/both_success_auxiliary_v1/exact_identity_receipt.json', 'ca7d773b0e9f8b3ca5836fbbde1edf4cc07a38ed834a119552094cbf56ea300d')
    add(AUX/'root_review_v1.json', 'evidence/both_success_auxiliary_v1/root_review_v1.json', '12bd2fdffdc32845a3d65795abbf113bed34ff3e7220b64b0c11df0982edf33f')
    add(AUX/'peer_review_v1.json', 'evidence/both_success_auxiliary_v1/peer_review_v1.json', 'cf3ec20fec8d76b67db233980a704032b6f50564c70cde0b64c14f684302c3c7')
    add(AUX/'supplemental_replay_commands.md', 'evidence/both_success_auxiliary_v1/supplemental_replay_commands.md', 'd2484bfb0ff911787dc79b95a74cbbf6871d3ab9a11020efdef89071435ed3b6')
    add(AUX/'Invoke-BothSuccessAuxiliary.ps1', 'evidence/both_success_auxiliary_v1/Invoke-BothSuccessAuxiliary.ps1', 'e16249576592969ccab29c7071fdbcfd5c41564f806c345ba8db68816c118e3a')
    for filename in ['execute_twice_v1.py', 'root_result_review_v1.py']:
        add(ROOT/'.local/both_success_auxiliary_v1'/filename, 'evidence/both_success_auxiliary_v1/operation_sources/'+filename)
    if aux_identity['status'] != 'ACTUAL_TWO_INDEPENDENT_OFFLINE_RUNS_EXACT_FOUR_FILE_IDENTITY' or not aux_identity['separate_from_original_B8_241_record_identity'] or aux_identity['provider_calls_or_benchmark_queries'] != 0:
        raise ValueError('Auxiliary outputs are not the independent actual finite supplement.')
    for filename, expected in [
        ('control/both_success_auxiliary_v1.py', 'dc3c8e4afb91ab991f3c0467a6801e9971525126f798cf84ea527c3cf514df97'),
        ('control/offline_analysis_runner.py', '7a82d8b4263b127b938958bc6bca74d8db50f0517e2654e94547bd4c67f77e2d'),
        ('control/guard_configuration.json', '8281454ca3e3bc4954337572fcbd1532fa27bdf4521de067ecc8a64d554ce410'),
        ('input/inputs.json', 'db6264cd8b87ef1ec25a9e4181e3a77d5b59e85445798e0c23d4c72a32406e19'),
        ('input/formal_statistics.py', '7e201b28e8a85e712cdace33951e900ad47e6c8dcd8f4554763cccd69870e373'),
        ('input/statistical_contract.json', '5ec70ab8b2d87fd248f92a13ff604bb2cd449b4b99e40e38aff6afd3a43bbbe8'),
        ('input/original_protocol.md', 'ca5602a6f8fcb852f5824bf6c4fd7e71074b280c35ba5705676806d17d593843'),
        ('execution_scope.json', '87ec498e38c5f34d077cfbea85dab54a04831c7f7fd36bd27822b86a89cfadfd'),
    ]:
        add(AUX/filename, 'evidence/both_success_auxiliary_v1/'+filename, expected)
    for run in aux_identity['runs']:
        side = run['name']
        add(AUX/side/'execution_receipt.json', f'evidence/both_success_auxiliary_v1/{side}/execution_receipt.json', run['execution_receipt_sha256'])
        add(AUX/side/'guard_result.json', f'evidence/both_success_auxiliary_v1/{side}/guard_result.json', run['guard_receipt_sha256'])
        if run['guard']['exit_code'] != 0 or run['guard']['denied_count'] != 0:
            raise ValueError('Auxiliary run is not actualguard0/deny0.')
        for filename in ['guard_events.jsonl', 'stderr.log', 'stdout.log']:
            add(AUX/side/filename, f'evidence/both_success_auxiliary_v1/{side}/'+filename)
    for item in aux_identity['files']:
        if not item['exact_bytes_equal'] or item['reference_sha256'] != item['independent_sha256']:
            raise ValueError('Auxiliary four-output identity is not exact.')
        add(AUX/'reference/analysis'/item['path'], 'evidence/both_success_auxiliary_v1/results/'+item['path'], item['reference_sha256'])
    published = read(ROOT/'.local/statistical_report_publication_20261021_v1/manifest.json')['files']
    for filename in ['report_body.md', 'detailed_saved_values_appendix.md', 'source_snapshot.json']:
        add(ROOT/'.local/final_report_20261021_draft_v3'/filename, 'evidence/published_report_rev18/'+filename, published[filename]['sha256'])
    for filename in ['delivery_manifest.json', 'packaging_receipt.json', 'reuse_comparison.json', 'raw_data.zip.members.csv', 'source_control.zip.members.csv', 'checksums.sha256', 'README_packages.md']:
        add(INPUT/filename, 'evidence/input_packages_v2/'+filename)
    # Both actual sides: terminal metadata/configuration only. No SQLite, raw,
    # payload JSONL, generated CSV/Parquet tables or rendered figures are read.
    for side, study in [('reference', REF), ('independent_rebuild', IND)]:
        add(B8/side/'execution.json', f'evidence/B8/{side}/execution.json')
        for stage in ['metric', 'primary', 'secondary', 'retrieval', 'capacity', 'reference', 'equivalence', 'report', 'format']:
            log = B8/side/'logs'/stage
            for filename in ['configuration.json', 'started.json', 'launched.json', 'result.json', 'guard_result.json', 'guard_events.jsonl', 'stderr.log', 'stdout.log']:
                add(log/filename, f'evidence/B8/{side}/logs/{stage}/{filename}')
        for filename in ['export_audit.json'] + [f'{table}.{kind}.json' for table in ['task_runs', 'provider_calls', 'messages', 'states', 'memory_events', 'resource_samples'] for kind in ['schema', 'storage']]:
            add(study/'exports/protocol_format_v1'/filename, f'evidence/B8/{side}/format/{filename}')
    # Reference summaries carry the completed scientific scope; no primary 7MB
    # JSON duplication is needed because its whole-SHA is in terminal manifests.
    for rel in ['analysis/full_metric_audit_v3/metric_audit.json', 'analysis/retrieval_postrun_analysis_v1/audit.json', 'analysis/retrieval_capacity_analysis_v1/audit.json', 'analysis/retrieval_capacity_analysis_v1/retrieval_quality_scale.csv', 'analysis/retrieval_capacity_analysis_v1/retrieval_quality_scale_by_build.csv', 'analysis/external_reference_equivalence_v1/audit.json', 'report_adapter_v1/analysis/report_availability.json', 'report_adapter_v1/analysis/saved_descriptive_facts.json']:
        add(REF/rel, 'evidence/reference_summaries/'+rel)
    for number in range(1, 7):
        add(REF/f'report_adapter_v1/tables/table_{number:02}.csv', f'evidence/reference_summaries/tables/table_{number:02}.csv')
    for number in range(1, 10):
        add(REF/f'report_adapter_v1/figures/figure_{number:02}.csv', f'evidence/reference_summaries/figure_data/figure_{number:02}.csv')

    names = [record[1] for record in sources]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate ZIP member before copying.')
    destination.mkdir(parents=True)
    payload = destination/'payload'
    payload.mkdir()
    write(destination/'started.json', {'status': 'ACTUAL_SMALL_CONTROL_COPY_STARTED', 'started_utc': utc(), 'approved_members': len(sources), 'existing_v2_packages_modified': False, 'new_rebuild_model_benchmark_or_api_calls': 0})
    records = []
    for src, rel, digest, size in sources:
        target = payload/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        if sha(src) != digest or sha(target) != digest or target.stat().st_size != size:
            raise ValueError(f'Copy source/destination changed: {src}')
        records.append({'member': rel, 'source_path': str(src), 'bytes': size, 'sha256': digest})
    write(payload/'evidence/distribution_assembly_receipt.json', assembly_receipt)
    assembly_path = payload/'evidence/distribution_assembly_receipt.json'
    records.append({'member': 'evidence/distribution_assembly_receipt.json', 'source_path': 'actual finite structural assembly verification in this packaging execution', 'bytes': assembly_path.stat().st_size, 'sha256': sha(assembly_path)})
    manifest = {'schema_version': 'final-control-supplement-v1', 'status': 'ACTUAL_COPIED_STABLE_SMALL_CONTROL_INPUTS', 'created_utc': utc(), 'members': records, 'archive_restore_members': ['archive/input_manifest.json', 'archive/package_manifest.json', 'archive/reference_numeric_manifest.json'], 'three_v2_input_packages_unchanged': True, 'new_model_benchmark_rebuild_or_API_calls': 0, 'matrix_original_rows': 155, 'authority_columns_checked': authority_columns, 'scope': 'Offline reconstruction inputs plus bounded terminal/report/QA/publication evidence. Exact identity reproduces retained missing/FAIL/UNKNOWN; no all-science-PASS claim. Large raw/image/SQLite/new exported formats/figures excluded.'}
    write(payload/'control_supplement_manifest.json', manifest)
    checksum_members = sorted(path for path in payload.rglob('*') if path.is_file())
    (payload/'checksums.sha256').write_text(''.join(f'{sha(path)}  {path.relative_to(payload).as_posix()}\n' for path in checksum_members), encoding='utf-8')
    zip_path = destination/'final_control_supplement_v1.zip'
    expected = {path.relative_to(payload).as_posix(): {'bytes': path.stat().st_size, 'sha256': sha(path)} for path in payload.rglob('*') if path.is_file()}
    with zipfile.ZipFile(zip_path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        for member in sorted(expected):
            z.write(payload/member, member)
    readback = []
    with zipfile.ZipFile(zip_path) as z:
        if len(z.namelist()) != len(expected) or set(z.namelist()) != set(expected):
            raise ValueError('ZIP members differ from finite copied payload.')
        for info in z.infolist():
            h = hashlib.sha256(); size = 0
            with z.open(info) as f:
                for block in iter(lambda: f.read(1024*1024), b''):
                    h.update(block); size += len(block)
            item = expected[info.filename]
            if size != item['bytes'] or h.hexdigest() != item['sha256']:
                raise ValueError(f'Decoded ZIP identity mismatch: {info.filename}')
            readback.append({'member': info.filename, 'bytes': size, 'sha256': h.hexdigest(), 'CRC32': f'{info.CRC:08x}', 'decoded_sha_crc': 'PASS'})
    with (destination/'zip_readback_members.csv').open('w', encoding='utf-8', newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['member','bytes','sha256','CRC32','decoded_sha_crc']); writer.writeheader(); writer.writerows(readback)
    receipt = {'status': 'ACTUAL_FINAL_SMALL_CONTROL_ZIP_COPIED_DECODED_SHA_CRC_VERIFIED', 'finished_utc': utc(), 'zip_path': str(zip_path), 'ZIP_members': len(expected), 'zip_bytes': zip_path.stat().st_size, 'zip_sha256': sha(zip_path), 'manifest_sha256': sha(payload/'control_supplement_manifest.json'), 'archive_input_manifest_sha256': PINNED['input'], 'matrix_sha256': args.matrix_sha256, 'matrix_receipt_sha256': args.matrix_receipt_sha256, 'B8_execution_sha256': PINNED['execution'], 'B8_numeric_sha256': PINNED['numerical'], 'chinese_ZIP_sha256': 'd00c9c24b46dbb3fef3e862e5d164b93967a3e8f71e78941844934c424d13083', 'auxiliary_four_output_identity_receipt_sha256': 'ca7d773b0e9f8b3ca5836fbbde1edf4cc07a38ed834a119552094cbf56ea300d', 'auxiliary_results_sha256': '9bed3f4d9639521ef766d3220f61e09d9a99870b4e44751395920b9c61ac64fd', 'auxiliary_separate_from_B8_241_record_identity': True, 'response_recovery_four_output_identity_receipt_sha256': quality_identity_sha, 'response_recovery_results_sha256': '13d3ed7304918a2a8ff7ac6482caedc70ab718c34bd7821d5a22581c10a3fbee', 'response_recovery_separate_from_B8_241_record_identity': True, 'locked_matrix_D006_pending_closed_by_this_external_actual_receipt': True, 'publication_rev': 20, 'existing_v2_input_packages_changed': False, 'new_model_benchmark_rebuild_API_or_render_calls': 0, 'all_original_experiment_requirements_declared_complete': False}
    write(destination/'package_receipt.json', receipt)
    (destination/'checksums.sha256').write_text(''.join(f'{sha(path)}  {path.name}\n' for path in [zip_path, destination/'package_receipt.json', destination/'zip_readback_members.csv']), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
