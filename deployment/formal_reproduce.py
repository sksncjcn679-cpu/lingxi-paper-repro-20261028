"""One-command, offline-only reproduction into a NEW directory.

This command is deliberately not a model runner. It refuses incomplete or duplicate
6504-unit ledgers, copies immutable inputs with a disk-space check, and rebuilds
derived products without changing the original raw/frozen inputs or reports.
"""
from __future__ import annotations

import argparse
import ast
import csv
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import time
import traceback

DEFAULT_OUT = Path('/evidence/formal_20261019')
EXPECTED_UNITS = 6504
SOURCE_NAMES = (
    'formal_reproduce.py', 'formal_analyze_offline.py',
    'formal_retrieval_analyze.py', 'formal_stress_analyze.py',
    'formal_protocol_v3_audit.py', 'formal_report.py', 'formal_export.py',
    'formal_statistics.py', 'formal_statistics_protocol_aligned_v3.py',
    'formal_statistics_spotcheck.py', 'formal_recording.py',
    'formal_provider_usage_authority.py', 'formal_quality_appendix.py',
    'formal_retrieval_v2_analyze.py',
    'formal_state_measurement_correction_v2_analyze.py',
    'formal_state_measurement_correction_v3_analyze.py',
    'formal_state_measurement_correction_v3.py',  # source-hash proof only; never imported/invoked
    'formal_report_serialization_audit_v1.py',  # actual exception provenance only; never invoked
)
CORE_TABLES = ('task_runs', 'provider_calls', 'messages', 'states', 'memory_events', 'resource_samples')
ANALYSIS_PATTERNS = (
    'fact_equivalence_calibration.json', 'measurement_freeze_gate.json',
    'overhead_calibration.json', 'automatic_corpus_validation.json',
    'state_matrix_status.json', 'state_batch_*.json', 'quantization_status.json',
    'state_reuse_status.json', 'stress_fault_semantic_audit.json',
    'stress_fault_*_status.json', 'stress_continuous_100_status.json',
    'external_framework_equivalence_v1.json', 'external_sdk_recorder_audit_v1.json',
    'report_protocol_alignment_v3.json', 'retrieval_n*_build*_build.json',
    'retrieval_n*_build*_process.json', 'retrieval_n*_build*_status.json',
    'stats/statistical_plan*.json', 'stats/statistical_implementation_protocol_alignment_v3.json',
    'stats/state_increment_population_audit_v1.json', 'stats/automatic_spotcheck_plan.json',
    'stats/prospective_power*.json', 'stats/synthetic_invariants*.json',
    'provider_usage_authority_contract.json', 'quality_appendix_contract.json',
    'field_authority_and_join_contract.json', 'report_spotcheck_timing_correction_v2.json',
    'report_spotcheck_authority_alignment.json',
    'final_artifact_selection_v2.json',
    'final_render_selection_v3.json',
    'state_measurement_correction_v3_analyzer_correction_v1.json',
    'report_numeric_serialization_audit_v1/audit.json',
    'state_measurement_correction_v2_registration.json',
    'state_measurement_correction_v2_registration.md',
    'state_measurement_correction_v3_registration.json',
    'state_measurement_correction_v3_registration.md',
)
NEVER_PRECOPY = {'protocol_v3_audit.json', 'stats/protocol_v3_cells.csv'}
SELECTION_SHA256 = 'fc8dcde3bded0cdeb4b77c93adde80eb21b954d632a9e4459b0e90bfaf357a00'
RETRIEVAL_V2 = 'retrieval_input_correction_v2'
STATE_STAGE_ROOT = 'state_measurement_correction_v3'
FAILED_STATE_STAGE_ROOT = 'state_measurement_correction_v2'
RENDER_SELECTION_SHA256 = '8bc0747441900db0372f1a1cd81521073fcb4e3de1faf986e6d61046c20818be'
REPORT_SOURCE_SHA256 = 'fed72d9777f6eaf8c630e19eb33f182e3f8acd01423d48dc20a39c9c4c460dc3'
STATE_ANALYZER_SHA256 = '61778b7c9cbbbfe31a1054833a9cf3ac1767ea1eabc68d92f127591a37d4ecad'
STATE_CORRECTION_SHA256 = 'c1ded86b7d5927ab2883b282dcb99fc98e3b149ffa787d978c7aa7a1d7e95d52'
STATE_CORRECTION = 'analysis/state_measurement_correction_v3_analyzer_correction_v1.json'
SERIALIZATION_AUDIT = 'analysis/report_numeric_serialization_audit_v1/audit.json'
SERIALIZATION_AUDIT_SHA256 = '21043e993c4f35c0db7d0b68077fcc7cc75711a5ce0ed64f5a7b59f98bc8941b'
SERIALIZATION_SOURCE_SHA256 = '0c2bce87b9ef94340f11ba8088064154df2e2f80904719d85c42e4d34bb7660c'
ABSTRACT_PATH = 'analysis/abstract_metrics.json'


def report_numeric_inventory(report):
    """All numerical report artifacts; no value normalization or table filtering."""
    inventory={}
    for path in sorted(report.rglob('*')):
        if not (path.suffix=='.csv' or path.name=='abstract_metrics.json' or
                (path.name.startswith('statistics_') and path.suffix=='.json')):continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(report.resolve()):
            raise RuntimeError('unsafe numerical report artifact')
        inventory[path.relative_to(report).as_posix()]=digest(path)
    if not inventory:raise RuntimeError('numerical report inventory is empty')
    return inventory


def canonical_json_sha(path):
    def reject(value):raise ValueError('nonfinite JSON is not a serialization-order exception')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate JSON key is not a serialization-order exception')
            result[key]=value
        return result
    value=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pairs,parse_constant=reject)
    # JSON scalars retain their types (including int/float/bool), and arrays
    # retain order. Only object-member ordering and whitespace are canonicalized.
    encoded=json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def rebuilt_report_numeric_gate(report,reference,source=None,input_view=None):
    actual=report_numeric_inventory(report)
    expected=reference['numeric_reference_sha256']
    same_paths=set(actual)==set(expected)
    normalized_paths={}
    exact_or_provenance=same_paths
    for name in expected:
        if name==ABSTRACT_PATH or actual.get(name)==expected[name]:continue
        column={'tables/table_01.csv':'source','tables/table_06.csv':'equivalence_audit'}.get(name)
        if not column or source is None or input_view is None:
            exact_or_provenance=False;continue
        with (report/name).open(encoding='utf-8',newline='') as stream:
            reader=csv.DictReader(stream);fields=reader.fieldnames;rows=list(reader)
        changes=[]
        for ordinal,row in enumerate(rows,1):
            value=row.get(column,'')
            if not value:continue
            path=Path(value)
            if not path.is_relative_to(input_view):continue
            relative=path.relative_to(input_view)
            original=source/relative
            if '..' in relative.parts or path.is_symlink() or not path.is_file() or \
                    not original.is_file() or digest(path)!=digest(original):
                raise RuntimeError('relocated CSV evidence is not an identical independent source input')
            row[column]=str(Path(reference.get('recorded_source_archive',str(DEFAULT_OUT)))/relative)
            changes.append({'row':ordinal,'column':column,'source_relative':relative.as_posix(),
                            'identical_input_sha256':digest(original)})
        buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=fields)
        writer.writeheader();writer.writerows(rows)
        restored_sha=hashlib.sha256(buffer.getvalue().encode('utf-8')).hexdigest()
        normalized_paths[name]={'status':'PASS_EXACT_AFTER_PROVENANCE_RELOCATION' if restored_sha==expected[name] else 'FAIL',
                                'restored_sha256':restored_sha,'expected_sha256':expected[name],'changes':changes}
        if restored_sha!=expected[name]:exact_or_provenance=False
    canonical=canonical_json_sha(report/ABSTRACT_PATH)
    passed=exact_or_provenance and canonical==reference['abstract_canonical_sha256']
    return {'status':'PASS_VALUES_WITH_VERIFIED_PROVENANCE_RELOCATION' if passed else 'FAIL_NUMERICAL_IDENTITY',
        'numeric_files':len(actual),'expected_sha256':expected,'rebuilt_sha256':actual,
        'same_relative_file_set':same_paths,'exact_byte_files':sum(actual.get(k)==v for k,v in expected.items()),
        'nonabstract_exact_or_verified_provenance':exact_or_provenance,'provenance_relocations':normalized_paths,
        'abstract_canonical_sha256':canonical,'expected_abstract_canonical_sha256':reference['abstract_canonical_sha256'],
        'sole_allowed_raw_serialization_difference':ABSTRACT_PATH,
        'serialization_audit_sha256':SERIALIZATION_AUDIT_SHA256,
        'normalization':'Abstract JSON object order/whitespace only; two named provenance columns may relocate only to byte-identical verified input copies. Restored CSV bytes must equal fixed historical SHA; no data/numeric/row normalization.'}


def selected_render_gate(source):
    """Validate postrun layout authority, preserving the unchanged v2 data choice."""
    path=source/'analysis/final_render_selection_v3.json'
    if digest(path)!=RENDER_SELECTION_SHA256:raise RuntimeError('final layout overlay identity differs')
    overlay=json.loads(path.read_text(encoding='utf-8'))
    if overlay['data_selection_sha256']!=SELECTION_SHA256 or digest(source/overlay['data_selection'])!=SELECTION_SHA256 or \
        overlay['publication_report_source_sha256']!=REPORT_SOURCE_SHA256 or \
        digest(Path(__file__).with_name('formal_report.py'))!=REPORT_SOURCE_SHA256:
        raise RuntimeError('final layout overlay changed source or v2 data authority')
    for field in ('original_visual_qa','original_qa_execution_receipt','original_report_recovery_manifest'):
        if digest(source/overlay[field])!=overlay[field+'_sha256']:
            raise RuntimeError('original report/failed-QA evidence changed')
    recovery_path=source/overlay['report_recovery_root']/'manifest.json'
    recovery=json.loads(recovery_path.read_text(encoding='utf-8'))
    audit_path=source/SERIALIZATION_AUDIT
    if digest(audit_path)!=SERIALIZATION_AUDIT_SHA256 or \
        digest(Path(__file__).with_name('formal_report_serialization_audit_v1.py'))!=SERIALIZATION_SOURCE_SHA256:
        raise RuntimeError('independent postrun serialization-audit identity differs')
    audit=json.loads(audit_path.read_text(encoding='utf-8'))
    steps=recovery.get('steps',[])
    byte_failure_path=recovery_path.with_name('numerical_identity.json')
    byte_failure=json.loads(byte_failure_path.read_text(encoding='utf-8'))
    if recovery.get('status')!='ERROR_RETAINED_FOR_DIAGNOSIS' or len(steps)!=1 or \
        steps[0].get('status')!='PASS' or steps[0].get('exit_code')!=0 or steps[0].get('timed_out') or \
        recovery.get('final_render_selection_sha256')!=RENDER_SELECTION_SHA256 or recovery.get('report_source_sha256')!=REPORT_SOURCE_SHA256 or \
        byte_failure.get('status')!='FAIL_EXACT_BYTES' or recovery.get('numerical_identity',{}).get('status')!='FAIL_EXACT_BYTES' or \
        audit.get('status')!='PASS_NUMERICAL_IDENTITY_WITH_RETAINED_BYTE_GUARD_FAILURE' or \
        audit.get('source_sha256')!=SERIALIZATION_SOURCE_SHA256 or audit.get('final_render_selection_sha256')!=RENDER_SELECTION_SHA256 or \
        audit.get('report_recovery_manifest_sha256')!=digest(recovery_path) or audit.get('retained_exact_byte_failure_sha256')!=digest(byte_failure_path) or \
        audit.get('numeric_file_count')!=42 or audit.get('exact_byte_identical_files')!=41 or audit.get('sole_serialization_difference')!=ABSTRACT_PATH or \
        audit.get('abstract_comparison',{}).get('typed_value_differences')!=0 or audit.get('abstract_comparison',{}).get('object_member_order_differences')!=1:
        raise RuntimeError('actual rendered child/retained byte failure/precise independent exception evidence differs')
    for relative,expected_sha in audit['evidence_sha256'].items():
        path=source/relative
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()) or digest(path)!=expected_sha:
            raise RuntimeError('serialization-audit bound historical evidence changed')
    expected=overlay['numeric_reference_sha256']
    if expected!=audit['original_numeric_sha256']:
        raise RuntimeError('serialization audit changed original numerical reference')
    for field,inventory_key in (('original_data_report_root','original_numeric_sha256'),('report_root','publication_numeric_sha256')):
        report=source/overlay[field]
        if report_numeric_inventory(report)!=audit[inventory_key] or canonical_json_sha(report/ABSTRACT_PATH)!=audit['abstract_canonical_sha256']:
            raise RuntimeError('actual v2/v3 numerical bytes or typed canonical abstract differ from independent audit')
    nonabstract={k:v for k,v in expected.items() if k!=ABSTRACT_PATH}
    if nonabstract!={k:v for k,v in audit['publication_numeric_sha256'].items() if k!=ABSTRACT_PATH}:
        raise RuntimeError('CSV/statistics changed beyond the exact abstract serialization exception')
    availability=source/overlay['report_root']/'analysis/report_availability.json'
    status=json.loads(availability.read_text(encoding='utf-8')).get('status')
    if status not in {'COMPLETE_DATA_REQUIRES_VISUAL_QA','INCOMPLETE'}:
        raise RuntimeError('publication report has no executed availability record')
    return {'status':'PASS_NUMERICAL_IDENTITY_WITH_RETAINED_BYTE_GUARD_FAILURE','overlay_sha256':RENDER_SELECTION_SHA256,'publication_report_root':overlay['report_root'],
        'data_report_root':overlay['original_data_report_root'],'report_recovery_sha256':digest(recovery_path),
        'report_availability_sha256':digest(availability),'report_actual_availability_status':status,
        'numeric_files':len(expected),'numeric_reference_sha256':expected,'abstract_canonical_sha256':audit['abstract_canonical_sha256'],
        'serialization_audit_sha256':digest(audit_path),'retained_controller_status':recovery['status'],
        'visual_qa':'NOT_CERTIFIED_BY_THIS_GATE',
        'scope':'postrun label wrapping only; original data selection and failed QA retained, not a preregistration'}


def selected_state_stage_gate(source):
    """Read completed independent phase evidence; never execute a timed driver."""
    root=source/STATE_STAGE_ROOT
    registration_path=source/'analysis/state_measurement_correction_v3_registration.json'
    registration=json.loads(registration_path.read_text(encoding='utf-8'))
    if registration.get('relative_output_root')!=STATE_STAGE_ROOT or registration.get('preformal_registration_claim') is not False:
        raise RuntimeError('unexpected independent state-stage registration scope')
    registration_sha=digest(registration_path)
    if digest(root/'prospective_registration.json')!=registration_sha:
        raise RuntimeError('actual state-stage registration bytes differ from prospective registration')
    for name,field in [('formal_state_measurement_correction_v3.py','driver_sha256'),
        ('formal_state_measurement_correction_v2_analyze.py','analyzer_sha256')]:
        if digest(Path(__file__).resolve().parent/name)!=registration[field]:
            raise RuntimeError('registered state-stage helper identity differs: '+name)
    correction_path=source/STATE_CORRECTION
    correction=json.loads(correction_path.read_text(encoding='utf-8'))
    if digest(correction_path)!=STATE_CORRECTION_SHA256 or \
        digest(Path(__file__).with_name('formal_state_measurement_correction_v3_analyze.py'))!=STATE_ANALYZER_SHA256 or \
        correction.get('corrected_analyzer_sha256')!=STATE_ANALYZER_SHA256 or \
        correction.get('registered_analyzer_sha256')!=registration['analyzer_sha256'] or \
        correction.get('prospective_registration_sha256')!=registration_sha or \
        digest(source/correction['original_failed_analysis'])!=correction['original_failed_analysis_sha256']:
        raise RuntimeError('state analyzer postrun correction/registered identity/failure evidence differs')
    for relative,expected in registration['registered_input_sha256'].items():
        path=source/relative
        if not path.resolve().is_relative_to(source.resolve()) or digest(path)!=expected:
            raise RuntimeError('registered original state-stage input changed: '+relative)
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    execution_path=root/'analysis/execution.json'
    execution=json.loads(execution_path.read_text(encoding='utf-8'))
    if manifest.get('status')!='COMPLETED' or execution.get('status')!='COMPLETED' or \
        manifest.get('registration_sha256')!=registration_sha or execution.get('prospective_registration_sha256')!=registration_sha or \
        manifest.get('execution_sha256')!=digest(execution_path) or execution.get('batches')!=3 or \
        (execution.get('measured_rows'),execution.get('warmup_rows'),execution.get('total_transfers'))!=(25200,2520,27720):
        raise RuntimeError('independent full25200 state-stage matrix has not closed')
    steps=execution.get('steps',[])
    if len(steps)!=3 or any(step.get('status')!='PASS' or step.get('exit_code')!=0 for step in steps):
        raise RuntimeError('three actual state-stage batches did not pass')
    tracker_source=registration['post_failure_lifecycle_correction']['stdlib_resource_tracker_sha256']
    for batch in (1,2,3):
        metadata=json.loads((root/f'analysis/batch_{batch}.json').read_text(encoding='utf-8'))
        stop=metadata.get('owned_resource_tracker_stop',{})
        if stop.get('status')!='STOPPED_AND_REAPED' or stop.get('ownership_verified') is not True or \
            stop.get('resource_tracker_source_sha256')!=tracker_source or stop.get('post_stop_waitpid')!='ECHILD_ALREADY_REAPED' or \
            stop.get('wait_status','MISSING') is not None or stop.get('tracker_pid_after_stop','MISSING') is not None or \
            stop.get('tracker_fd_after_stop','MISSING') is not None or stop.get('tracker_process_after_stop','MISSING') is not None or \
            stop.get('tracker_fd_present_after_stop') is not False or stop.get('active_child_pids_before_stop')!=[] or \
            stop.get('shm_residue_before_stop')!=[] or stop.get('consumer_exit_before_stop')!=0 or \
            stop.get('tracker_process_before_stop',{}).get('parent_pid')!=metadata.get('producer_pid') or \
            stop.get('scope',{}).get('worker_pid')!=metadata.get('producer_pid'):
            raise RuntimeError('selected state batch lacks actual owned-resource-tracker stop/reap proof')
    analysis=root/'analysis/recomputed_v2'
    matrix_path=analysis/'matrix_audit.json';matrix=json.loads(matrix_path.read_text(encoding='utf-8'))
    join_path=analysis/'stage_join_audit.json';join=json.loads(join_path.read_text(encoding='utf-8'))
    if matrix.get('status')!='PASS' or join.get('status')!='PASS' or \
        (matrix.get('planned_cells'),matrix.get('exported_cells'),matrix.get('observed_measured_rows'),matrix.get('observed_warmup_rows'))!=(126,126,25200,2520) or \
        (join.get('producer_rows'),join.get('consumer_rows'),join.get('joined_rows'))!=(27720,27720,27720) or \
        matrix.get('analyzer_sha256')!=STATE_ANALYZER_SHA256 or \
        matrix.get('executed_analyzer_sha256')!=STATE_ANALYZER_SHA256 or \
        matrix.get('registered_analyzer_sha256')!=registration['analyzer_sha256'] or \
        matrix.get('registered_analyzer_source_sha256')!=registration['analyzer_sha256'] or \
        matrix.get('analyzer_correction_receipt_sha256')!=STATE_CORRECTION_SHA256 or \
        digest(analysis/'stage_cells.csv')!=matrix.get('stage_cells_sha256'):
        raise RuntimeError('state-stage raw join/fullmatrix/table audit has not actually passed')
    expected_raw={str((root/'raw'/f'{kind}_batch_{batch}.jsonl').resolve()) for batch in (1,2,3)
        for kind in ('producer','child_stage','resources')}
    actual_raw=set()
    recorded_root=Path(matrix['source_root'])
    for name,record in matrix['source_files'].items():
        original=Path(name)
        if original==recorded_root.parent/STATE_CORRECTION:
            path=correction_path
        elif original.is_relative_to(recorded_root):path=root/original.relative_to(recorded_root)
        else:raise RuntimeError('state-stage provenance escapes root or exact registered correction receipt')
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()) or digest(path)!=record['sha256']:
            raise RuntimeError('state-stage source evidence changed after its actual audit')
        if path.resolve().is_relative_to((root/'raw').resolve()):actual_raw.add(str(path.resolve()))
    if actual_raw!=expected_raw:raise RuntimeError('state-stage audited raw coverage differs from all nine actual streams')
    return {'status':'PASS','registration_sha256':registration_sha,'matrix_audit_sha256':digest(matrix_path),
        'stage_join_audit_sha256':digest(join_path),'stage_cells_sha256':matrix['stage_cells_sha256'],
        'actual_measured_rows':25200,'actual_warmup_rows':2520,'actual_transfers':27720,
        'raw_streams':9,'authority_root':STATE_STAGE_ROOT,'failed_state_v2_policy':'archive_only; never resumed or pooled',
        'registered_analyzer_sha256':registration['analyzer_sha256'],'executed_analyzer_sha256':STATE_ANALYZER_SHA256,
        'postrun_analyzer_correction_sha256':STATE_CORRECTION_SHA256,
        'recorded_provenance_root':str(recorded_root),'current_archive_root':str(root),
        'scope':'Independent table4 phase supplement only; old full-path state matrix/figure5 remain valid and are never pooled or retrospectively split.'}


def selected_retrieval_gate(source):
    selection_path=source/'analysis/final_artifact_selection_v2.json'
    if digest(selection_path)!=SELECTION_SHA256:
        raise RuntimeError('fixed before-new-outcome artifact selection identity differs')
    selection=json.loads(selection_path.read_text(encoding='utf-8'))
    if selection['authoritative_retrieval_root']!=RETRIEVAL_V2:
        raise RuntimeError('unexpected retrieval authority path')
    root=source/RETRIEVAL_V2
    registration=root/'analysis/before_new_outcome_registration.json'
    queries=root/'corpus/queries.jsonl'
    if digest(queries)!=selection['authoritative_queries_sha256']:
        raise RuntimeError('selected corrected query identity differs')
    prepared=json.loads(registration.read_text(encoding='utf-8'))
    if prepared['queries_v2_sha256']!=digest(queries) or not prepared['before_new_outcome']:
        raise RuntimeError('corrected input registration identity differs')
    inventory_path=root/'analysis/input_inventory_sha256.json'
    if digest(inventory_path)!=prepared['input_inventory_sha256']:raise RuntimeError('registered corrected inventory differs')
    registered=json.loads(inventory_path.read_text(encoding='utf-8'))
    for name,expected in registered.items():
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or digest(path)!=expected:
            raise RuntimeError('registered corrected public input/helper changed: '+name)
    result=json.loads((root/'analysis/retrieval_v2_analysis.json').read_text(encoding='utf-8'))
    if result.get('status')!='COMPLETE_REQUIRES_REPORT_NUMERIC_AND_VISUAL_QA' or result.get('observed_query_units')!=14400 or \
        result.get('cells')!=60 or result.get('actual_native_unique_text_input_count_per_semantic_hybrid_cell')!=240:
        raise RuntimeError('selected v2 full14400/240distinct matrix has not closed')
    if len(result.get('raw_sha256',{}))!=12 or digest(root/'tables/retrieval_quality_scale.csv')!=result['table_sha256']:
        raise RuntimeError('selected v2 raw inventory/table identity differs from executed analysis')
    for name,expected in result['raw_sha256'].items():
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or digest(path)!=expected:
            raise RuntimeError('selected v2 raw source identity differs')
    return {'status':'PASS','selection_sha256':digest(selection_path),
        'registration_sha256':digest(registration),'analysis_sha256':digest(root/'analysis/retrieval_v2_analysis.json'),
        'authority_root':RETRIEVAL_V2,'old_original_retrieval':'archive_only; never pooled'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def runtime_versions():
    values = {}
    for name in ('numpy', 'scipy', 'matplotlib', 'pyarrow', 'psutil'):
        try:
            values[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            values[name] = 'NOT_INSTALLED'
    return values


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data := stream.read(1024 * 1024):
            value.update(data)
    return value.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def json_rows(path):
    with path.open(encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (ValueError, TypeError) as error:
                raise RuntimeError(f'invalid or unfinished JSONL: {path.name}:{number}') from error
            if not isinstance(row, dict):
                raise RuntimeError(f'non-object record: {path.name}:{number}')
            yield row


def real_terminal_gate(source):
    """Identity/completion checks only; no selection using formal effect sizes."""
    plan_path = source / 'manifests/formal_plan.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    planned = Counter(row['run_id'] for row in plan)
    if len(plan) != EXPECTED_UNITS or len(planned) != EXPECTED_UNITS or any(n != 1 for n in planned.values()):
        raise RuntimeError('exactly 6504 unique frozen planned units are required')
    expected = {row['run_id']: row for row in plan}
    terminals = Counter()
    terminal_statuses = {'COMPLETED', 'FAILED', 'INCOMPLETE_BUDGET_STOP', 'UNSUPPORTED', 'NON_COMPARABLE'}
    for row in json_rows(source / 'raw/task_runs.jsonl'):
        identity = row.get('run_id')
        terminals[identity] += 1
        if identity not in expected:
            raise RuntimeError('terminal run ID is absent from frozen plan')
        if row.get('status') not in terminal_statuses or not row.get('end_utc') or type(row.get('success')) is not bool:
            raise RuntimeError(f'run has no closed terminal record: {identity}')
        if row.get('model_evidence') != 'real_provider':
            raise RuntimeError('a mock/offline row is present in the real terminal ledger')
        if row.get('phase') != expected[identity]['phase']:
            raise RuntimeError('terminal phase differs from its frozen planned phase')
    if terminals != planned:
        missing = len(set(planned) - set(terminals))
        duplicated = sum(n > 1 for n in terminals.values())
        raise RuntimeError(f'all 6504 planned terminal records required; missing={missing}, duplicated={duplicated}')
    # An unmatched started request must never be treated as a finished provider attempt.
    def attempts(name):
        values = Counter()
        for row in json_rows(source / 'raw' / name):
            if row.get('run_id') not in expected:
                raise RuntimeError('provider attempt refers to an unplanned terminal unit')
            values[row.get('request_id')] += 1
        return values
    attempt_starts = attempts('provider_attempt_starts.jsonl')
    attempt_ends = attempts('provider_calls.jsonl')
    if None in attempt_starts or None in attempt_ends or attempt_starts != attempt_ends:
        raise RuntimeError('provider attempt starts/ends are unresolved or inconsistent')
    if any(n != 1 for n in attempt_starts.values()):
        raise RuntimeError('duplicate provider attempt identity; no silent deduplication')
    return {'status': 'PASS', 'planned_units': EXPECTED_UNITS, 'terminal_units': sum(terminals.values()),
            'provider_attempts': len(attempt_starts), 'phase_counts': dict(Counter(row['phase'] for row in plan)),
            'quality_filter': 'NONE: successes and failures retained', 'plan_sha256': digest(plan_path)}


def source_files(source):
    """Explicit input inventory; original derived reports/tables are not imported."""
    files = {}
    for name in ('raw', 'manifests', 'corpus'):
        root = source / name
        if not root.is_dir():
            raise RuntimeError(f'missing authoritative input directory: {name}')
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise RuntimeError(f'source symlink requires explicit archive resolution first: {path.relative_to(source)}')
            if path.is_file():
                files[path.relative_to(source).as_posix()] = path
    analysis = source / 'analysis'
    for pattern in ANALYSIS_PATTERNS:
        for path in sorted(analysis.glob(pattern)):
            relative = path.relative_to(analysis).as_posix()
            if relative in NEVER_PRECOPY:
                continue
            if path.is_symlink():
                raise RuntimeError(f'analysis metadata symlink is not copied implicitly: {relative}')
            if path.is_file():
                files['analysis/' + relative] = path
    # Full corrected inputs/raw plus only metadata needed to regenerate its table.
    # Prior corrected tables and analysis outputs are intentionally not copied.
    v2=source/RETRIEVAL_V2
    for name in ('raw','corpus','drivers'):
        for path in sorted((v2/name).rglob('*')):
            if path.is_symlink():raise RuntimeError('corrected retrieval input symlink refused')
            if name=='drivers' and ('__pycache__' in path.parts or path.suffix!='.py'):continue
            if path.is_file():files[path.relative_to(source).as_posix()]=path
    for pattern in ('before_new_outcome_registration.json','input_inventory_sha256.json','query_diffs.json',
        'query_semantic_validation.json','native_input_inventory.json','retrieval_n*_build*_build.json',
        'retrieval_n*_build*_process.json','retrieval_n*_build*_status.json','retrieval_n*_build*_pool_identity.json'):
        for path in sorted((v2/'analysis').glob(pattern)):
            if path.is_symlink():raise RuntimeError('corrected retrieval metadata symlink refused')
            if path.is_file():files[path.relative_to(source).as_posix()]=path
    for state_name in (FAILED_STATE_STAGE_ROOT,STATE_STAGE_ROOT):
        state=source/state_name
        for path in sorted((state/'raw').rglob('*')):
            if path.is_symlink():raise RuntimeError('state-stage raw symlink refused')
            if path.is_file():files[path.relative_to(source).as_posix()]=path
        for relative in ('manifest.json','prospective_registration.json'):
            files[(state/relative).relative_to(source).as_posix()]=state/relative
        for pattern in ('execution.json','batch_*.json','child_*.json','*.schema.json'):
            for path in sorted((state/'analysis').glob(pattern)):
                if path.is_symlink():raise RuntimeError('state-stage metadata symlink refused')
                if path.is_file():files[path.relative_to(source).as_posix()]=path
    for path in sorted((source/FAILED_STATE_STAGE_ROOT/'logs').rglob('*')):
        if path.is_symlink():raise RuntimeError('closed failed state diagnostic symlink refused')
        if path.is_file():files[path.relative_to(source).as_posix()]=path
    historical_failure=source/STATE_STAGE_ROOT/'analysis/recomputed_v1/execution_failure.json'
    if historical_failure.is_symlink() or not historical_failure.is_file():
        raise RuntimeError('registered prior state analyzer failure evidence missing')
    files[historical_failure.relative_to(source).as_posix()]=historical_failure
    # Explicitly protect the exclusive-create outputs of the original v3 auditor.
    if 'analysis/protocol_v3_audit.json' in files or 'analysis/stats/protocol_v3_cells.csv' in files:
        raise RuntimeError('v3 auditor output was incorrectly included as an input')
    return files


def require_space(destination, files):
    input_bytes = sum(path.stat().st_size for path in files.values())
    core_bytes = sum(path.stat().st_size for key, path in files.items()
                     if key in {f'raw/{name}.jsonl' for name in CORE_TABLES})
    # Includes copies, CSV/Parquet expansion and plotting/cache headroom.
    estimated = input_bytes + 3 * core_bytes + 1024 ** 3
    free = shutil.disk_usage(destination).free
    if free < estimated:
        raise RuntimeError(f'insufficient free space for isolated copies/exports: required_estimate={estimated}, free={free}')
    return {'input_copy_bytes': input_bytes, 'core_jsonl_bytes': core_bytes,
            'required_estimated_bytes': estimated, 'free_bytes_at_check': free,
            'estimate_policy': 'input copies + 3x core JSONL bytes for exports + 1GiB headroom'}


def copy_one(source, target):
    before = source.stat()
    source_hash = digest(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target_hash = digest(target)
    after = source.stat()
    if source_hash != target_hash or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f'input changed during snapshot copy: {source.name}')
    return {'bytes': after.st_size, 'sha256': source_hash, 'original_size': after.st_size,
            'original_mtime_ns': after.st_mtime_ns}


def protect_copy(root):
    # These are independent copies, never hard links. chmod cannot change the archive.
    for path in root.rglob('*'):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    for path in sorted((p for p in root.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        path.chmod(0o555)
    root.chmod(0o555)


RUNNER = r'''"""Generated offline subprocess boundary; never imports a model driver."""
import importlib.util
import json
import runpy
import socket
import sys
from pathlib import Path

def denied(*args, **kwargs):
    raise RuntimeError('network access is disabled in offline reproduction')

OriginalSocket = socket.socket
class OfflineSocket(OriginalSocket):
    def __init__(self, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, fileno=None):
        if family in (socket.AF_INET, socket.AF_INET6):
            denied()
        super().__init__(family, type, proto, fileno=fileno)
socket.socket = OfflineSocket
socket.create_connection = denied
socket.getaddrinfo = denied

config=json.loads(Path(sys.argv[1]).read_text())
script=Path(config['script'])
sys.path.insert(0,str(script.parent))
sys.argv=[str(script),*config.get('arguments',[])]
if config['action']=='script':
    runpy.run_path(str(script),run_name='__main__')
else:
    spec=importlib.util.spec_from_file_location(script.stem,script)
    module=importlib.util.module_from_spec(spec)
    sys.modules[script.stem]=module
    spec.loader.exec_module(module)
    module.OUT=Path(config['out'])
    module.main()
'''


def adapted_protocol(source, target, view):
    original = source.read_bytes()
    old = b"OUT = Path('/evidence/formal_20261019')"
    new = ('OUT = Path(' + repr(str(view)) + ')').encode('utf-8')
    if original.count(old) != 1:
        raise RuntimeError('v3 audit OUT declaration changed; refusing a broad source rewrite')
    adapted = original.replace(old, new, 1)
    expected = ast.parse(original.decode('utf-8'))
    actual = ast.parse(adapted.decode('utf-8'))
    matches = [node for node in expected.body if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == 'OUT' for t in node.targets)]
    if len(matches) != 1:
        raise RuntimeError('v3 audit OUT assignment is ambiguous')
    matches[0].value.args[0].value = str(view)
    if ast.dump(expected, include_attributes=False) != ast.dump(actual, include_attributes=False):
        raise RuntimeError('adaptation would change more than the OUT path')
    target.write_bytes(adapted)
    return {'original_source': str(source), 'original_source_sha256': hashlib.sha256(original).hexdigest(),
            'adapted_source': str(target), 'adapted_source_sha256': hashlib.sha256(adapted).hexdigest(),
            'change': 'only the OUT Path literal; AST equivalence after that replacement certified',
            'original_frozen_source_modified': False}


def offline_environment(destination):
    env = dict(os.environ)
    for name in list(env):
        upper = name.upper()
        if any(word in upper for word in ('API_KEY', 'API_TOKEN', 'ACCESS_TOKEN', 'AUTH_TOKEN', 'PASSWORD', 'SECRET')):
            env.pop(name, None)
    env.update(PYTHONDONTWRITEBYTECODE='1', MPLBACKEND='Agg',
               MPLCONFIGDIR=str(destination / 'cache/matplotlib'), XDG_CACHE_HOME=str(destination / 'cache'),
               TMPDIR=str(destination / 'tmp'), TEMP=str(destination / 'tmp'), TMP=str(destination / 'tmp'),
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    return env


def run_step(name, script, destination, view, action='module', arguments=(), timeout=None):
    config = {'action': action, 'script': str(script), 'out': str(view), 'arguments': list(arguments)}
    config_path = destination / 'logs' / f'{name}.config.json'
    write_json(config_path, config)
    command = [sys.executable, str(destination / 'drivers/offline_step_runner.py'), str(config_path)]
    result = {'step': name, 'script': str(script), 'source_sha256': digest(script),
              'command': command, 'start_utc': utc(), 'network_policy': 'IPv4/IPv6 socket creation and DNS denied'}
    result['configuration_sha256'] = digest(config_path)
    tick = time.perf_counter()
    with (destination / 'logs' / f'{name}.stdout.log').open('x') as stdout, \
         (destination / 'logs' / f'{name}.stderr.log').open('x') as stderr:
        try:
            process = subprocess.run(command, cwd=destination / 'drivers',
                                     env=offline_environment(destination), stdout=stdout, stderr=stderr, timeout=timeout)
            result.update(exit_code=process.returncode, status='EXIT_OK' if process.returncode == 0 else 'FAILED')
        except subprocess.TimeoutExpired:
            result.update(exit_code=None, status='TIMED_OUT')
        except Exception as error:
            result.update(exit_code=None, status='FAILED_TO_EXECUTE', error_type=type(error).__name__, error=str(error))
    result.update(end_utc=utc(), wall_sec=time.perf_counter() - tick)
    semantic_files = {
        '01_state_quantization_and_legacy_audit': view / 'analysis/offline_data_audit.json',
        '02_retrieval': view / RETRIEVAL_V2 / 'analysis/retrieval_quality_scale_audit.json',
        '03_stress_faults': view / 'analysis/stress_matrix_audit.json',
        '03b_state_stage_supplement': view / STATE_STAGE_ROOT / 'analysis/recomputed_v2/matrix_audit.json',
        '04_protocol_v3_audit': view / 'analysis/protocol_v3_audit.json',
        '05_provider_usage_authority': destination / 'provider_usage_authority/provider_usage_authority_audit.json',
        '06_quality_appendix': destination / 'quality_appendix/quality_appendix_audit.json',
        '07_formal_report': destination / 'report/analysis/report_availability.json',
        '08_lossless_exports': destination / 'exports/export_audit.json',
    }
    artifact = semantic_files[name]
    if artifact.exists():
        try:
            record = json.loads(artifact.read_text())
            result['derived_audit'] = {'path': str(artifact), 'sha256': digest(artifact),
                                      'status': record.get('status', 'SEE_DECLARED_COUNTS_AND_SCOPE'),
                                      'complete': record.get('complete'),
                                      'interpretation': 'artifact status is distinct from process exit; no visual or full semantic certification'}
        except (ValueError, OSError) as error:
            result.update(status='FAILED_ARTIFACT_VALIDATION', artifact_error=str(error))
    write_json(destination / 'logs' / f'{name}.result.json', result)
    return result


def unchanged_inputs(source, inventory):
    if set(source_files(source)) != set(inventory):
        raise RuntimeError('authoritative input inventory changed during reproduction')
    changes = []
    for relative, record in inventory.items():
        path = source / relative
        if not path.is_file() or digest(path) != record['sha256']:
            changes.append(relative)
    if changes:
        raise RuntimeError('original input content changed during rebuild: ' + ', '.join(changes[:10]))
    return {'status': 'PASS', 'original_input_files_unchanged': len(inventory),
            'method': 'original SHA256 checked again after all offline steps; access time is not a content change'}


def snapshot_readme(destination):
    (destination / 'README_rebuild.md').write_text(
        '# Offline reproduction output\n\n'
        'Material Passport: independently rebuilt derivatives; original raw/frozen inputs and original reports remain unchanged.\n\n'
        '`input_view/raw`, `input_view/manifests` and `input_view/corpus` are independent verified read-only copies. '
        'Only necessary analysis metadata was copied. Derived tables/figures started empty; the original v3 audit outputs '
        '`analysis/protocol_v3_audit.json` and `analysis/stats/protocol_v3_cells.csv` were intentionally not pre-copied.\n\n'
        'The original offline state/quantization helper still audits its historical v2 protocol inputs. The separately '
        'regenerated v3 auditor is authoritative for the three independent producer/consumer batches.\n\n'
        'The fixed before-new-outcome final_artifact_selection_v2.json selects corrected retrieval inputs. '
        'Original retrieval14400 remains archive-only for its210-distinct-query defect. The independent corrected '
        'retrieval_input_correction_v2 subtree is copied and its full14400/240-distinct inputs are verified; '
        'its unchanged analyzer rebuilds a fresh separate table before report receives explicit --retrieval-root. '
        'No corrected table is projected onto the original OUT or mixed with old query results.\n\n'
        'The separately registered selected state_measurement_correction_v3 raw and metadata are copied read-only. '
        'Its corrected pure offline analyzer regenerates analysis/recomputed_v2/stage_cells.csv and the full27720 '
        'producer/consumer join audit (25200 measured+2520 warmups). This is an independent table4 '
        'phase supplement; the main report keeps its original full-path state table/figure5. '
        'No native measurement matrix is rerun, no old span is retrospectively split, and new/old state rows are never pooled.\n\n'
        'Failed state_measurement_correction_v2 raw and its closed diagnostic logs remain archive-only copies; '
        'no completion gate is fabricated for its partial matrix and no rows are pooled into selected v3. '
        'The v3 timing driver is retained only as a registered source-hash proof and is never imported/invoked.\n\n'
        'The registered670cd analyzer and actual61778 corrected analyzer are both retained with their immutable '
        'postrun correction receipt; the failed recomputed_v1 analysis remains diagnostic history. '
        'No prospective registration or original analyzer byte is relabelled.\n\n'
        'final_render_selection_v3.json selects a postrun label-wrapping correction for publication. '
        'The v2 data report and its failed visual QA remain unchanged. CSV/statistics files must match '
        'the fixed v2/v3 numerical SHA inventory byte for byte. The independently audited sole '
        'abstract_metrics.json member-order difference is accepted only with typed canonical JSON identity; '
        'the actual recovery_v3 exact-byte guard failure stays failed and retained. '
        'Original numerical outputs supply validation hashes only and are never copied into rebuilt tables.\n\n'
        'Steps run serially in fresh subprocesses. Network access is denied; no provider/model driver is invoked. '
        'Each step retains source SHA, command, stdout, stderr, exit code and duration under logs/. '
        'The only protocol auditor adaptation changes its OUT path in a separate copied file, with both source hashes retained.\n\n'
        'Paper report: report/. Lossless exports and schemas: exports/. '
        'Explicit provider-usage authority: provider_usage_authority/. Quality and heterogeneity: quality_appendix/. '
        'Inspect rebuild_manifest.json and report/analysis/report_availability.json. '
        'A successfully rebuilt numerical report is NOT a visual-QA certificate, a schema-semantic certificate or a claim that missing measurements exist. '
        'Rendered PDF/SVG/PNG still require independent layout, fonts, searchable-text, caption and numeric consistency review.\n\n'
        'Dependencies: the archived Python environment with numpy, scipy, matplotlib, pyarrow and psutil. '
        'No dependency installation, credential loading or API access occurs. Each analyzer runs in a new subprocess, '
        'but it may load raw rows in memory; the current 2GiB container remains a capacity limit, not a promised measured peak. '
        'An OOM/nonzero exit retains failure logs rather than reducing the dataset. '
        'Disk preflight reserves input copies + 3x six core JSONL bytes + 1GiB; this is an estimate and later ENOSPC remains a recorded failure.\n\n'
        'Audit scope: all6504 planned/terminal identities and provider-attempt starts/ends are reconciled; '
        'protocol-v3 rows are re-audited, report30-unit checks and full export readbacks are regenerated. '
        'This does not automatically rerun the separate native/external full usage/measurement-semantic auditors or SDK fixture tests. '
        'Existing preparation audit metadata retains its original scope; neither copying it nor rebuilding charts certifies a new full measurement audit.\n\n'
        'A failed destination is retained for diagnosis. Never reuse it; choose a new --destination for another attempt.\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--step-timeout-seconds', type=float, default=None)
    args = parser.parse_args()
    source = args.out.resolve()
    destination = args.destination.resolve()
    if destination.exists():
        raise RuntimeError('destination must be entirely new; original and previous reports are never overwritten')
    if destination == source or source in destination.parents or destination in source.parents:
        raise RuntimeError('new rebuild directory must be outside the original archive tree')
    if args.step_timeout_seconds is not None and args.step_timeout_seconds <= 0:
        raise ValueError('step timeout must be positive')
    destination.mkdir(parents=True)
    for name in ('logs', 'drivers', 'cache', 'tmp'):
        (destination / name).mkdir()
    manifest = {'material_passport': {'artifact_type': 'isolated_offline_reproduction', 'human_review': False},
                'started_utc': utc(), 'source_archive': str(source), 'destination': str(destination),
                'status': 'RUNNING', 'never_calls_model_api': True,
                'original_reports_used_as_inputs': False,
                'original_report_reference_policy':'Only fixed numerical validation SHA inventory; never table/figure inputs',
                'visual_qa': 'NOT_PERFORMED_REQUIRES_INDEPENDENT_REVIEW',
                'audit_scope': {'real_units': 'all6504 planned IDs and closed terminal rows; no duplicate acceptance',
                                'provider_attempts': 'all recorded starts/ends identity reconciliation; not usage-value semantic validation',
                                'protocol_v3': 'full raw hash-chain, count, process and byte audit regenerated',
                                'report_spotcheck': 'same automatic30-unit report helper, not a human review',
                                'exports': 'all six tables with schemas and lossless readback checks',
                                'full_native_external_usage_semantics': 'NOT_AUTOMATICALLY_REEXECUTED; separate applicable auditors required',
                                'visual_qa': 'NOT_PERFORMED'},
                'memory_boundary': 'fresh serial subprocesses prevent cross-step retained heaps; analyzers can still exceed available RAM; no sampling peak claimed before execution',
                'runtime': {'python': sys.version, 'platform': platform.platform(),
                            'libraries': runtime_versions()},
                'steps': []}
    inventory = {}
    locks=[]
    try:
        for relative in ('analysis/postrun_verification.lock','manifests/real_provider_execution.lock'):
            lock=(source/relative).open('r+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(lock)
        manifest['terminal_gate'] = real_terminal_gate(source)
        manifest['selected_retrieval_gate'] = selected_retrieval_gate(source)
        manifest['selected_state_stage_gate'] = selected_state_stage_gate(source)
        manifest['selected_render_gate'] = selected_render_gate(source)
        files = source_files(source)
        manifest['space_check'] = require_space(destination, files)
        view = destination / 'input_view'
        for name in ('analysis/stats', 'tables', 'figures'):
            (view / name).mkdir(parents=True, exist_ok=True)
        for relative, path in files.items():
            inventory[relative] = copy_one(path, view / relative)
        for name in ('raw', 'manifests', 'corpus'):
            protect_copy(view / name)
        for name in ('raw','corpus','drivers'):
            protect_copy(view / RETRIEVAL_V2 / name)
        for state_name in (FAILED_STATE_STAGE_ROOT,STATE_STAGE_ROOT):protect_copy(view/state_name/'raw')
        protect_copy(view/FAILED_STATE_STAGE_ROOT/'logs')
        write_json(destination / 'logs/input_copy_inventory.json', inventory)
        manifest['input_copy_mode'] = 'independent copies; immutable roots chmod444/555; no hardlinks or original permission changes'
        manifest['original_input_inventory_sha256'] = digest(destination / 'logs/input_copy_inventory.json')
        drivers = Path(__file__).resolve().parent
        source_identities = {}
        for name in SOURCE_NAMES:
            path = drivers / name
            if not path.is_file():
                raise RuntimeError(f'required reproduction source missing: {name}')
            source_identities[name] = copy_one(path, destination / 'drivers' / name)
        runner = destination / 'drivers/offline_step_runner.py'
        runner.write_text(RUNNER, encoding='utf-8')
        ast.parse(RUNNER)
        manifest['offline_boundary_source_sha256'] = digest(runner)
        protocol_script = destination / 'drivers/protocol_v3_audit_adapted.py'
        manifest['protocol_audit_adaptation'] = adapted_protocol(destination / 'drivers/formal_protocol_v3_audit.py', protocol_script, view)
        manifest['sources'] = source_identities
        protect_copy(destination / 'drivers')
        write_json(destination / 'rebuild_manifest.json', manifest)
        snapshot_readme(destination)
        stages = [
            ('01_state_quantization_and_legacy_audit', 'formal_analyze_offline.py', 'module', ()),
            ('02_retrieval', 'formal_retrieval_v2_analyze.py', 'script',
                ('--out',str(view),'--destination',str(view/RETRIEVAL_V2))),
            ('03_stress_faults', 'formal_stress_analyze.py', 'module', ()),
            ('03b_state_stage_supplement','formal_state_measurement_correction_v3_analyze.py','script',
                ('--root',str(view/STATE_STAGE_ROOT),'--destination',str(view/STATE_STAGE_ROOT/'analysis/recomputed_v2'))),
            ('04_protocol_v3_audit', 'protocol_v3_audit_adapted.py', 'script', ()),
            ('05_provider_usage_authority', 'formal_provider_usage_authority.py', 'script',
                ('--out', str(view), '--destination', str(destination / 'provider_usage_authority'))),
            ('06_quality_appendix', 'formal_quality_appendix.py', 'script',
                ('--out', str(view), '--destination', str(destination / 'quality_appendix'))),
            ('07_formal_report', 'formal_report.py', 'script', ('--out', str(view), '--report-dir', str(destination / 'report'),
                '--retrieval-root',str(view/RETRIEVAL_V2))),
            ('08_lossless_exports', 'formal_export.py', 'module', ('--destination', str(destination / 'exports'))),
        ]
        for name, filename, action, arguments in stages:
            result = run_step(name, destination / 'drivers' / filename, destination, view, action, arguments, args.step_timeout_seconds)
            manifest['steps'].append(result)
            write_json(destination / 'rebuild_manifest.json', manifest)
            if result['status'] != 'EXIT_OK':
                raise RuntimeError(f'offline rebuild step failed: {name}; all logs and partial outputs retained')
        identity=rebuilt_report_numeric_gate(destination/'report',manifest['selected_render_gate'],source,view)
        manifest['report_numerical_identity']=identity
        write_json(destination/'rebuild_manifest.json',manifest)
        if identity['status']!='PASS_VALUES_WITH_VERIFIED_PROVENANCE_RELOCATION':
            raise RuntimeError('rebuilt numerical report differs beyond the independently audited abstract serialization exception')
        manifest['original_input_integrity'] = unchanged_inputs(source, inventory)
        # Recheck the actual original report references as well as all copied inputs.
        manifest['selected_render_gate_after']=selected_render_gate(source)
        availability = json.loads((destination / 'report/analysis/report_availability.json').read_text())
        export_audit = json.loads((destination / 'exports/export_audit.json').read_text())
        manifest['report_availability_status'] = availability.get('status')
        manifest['export_status'] = export_audit.get('status')
        manifest['visual_qa'] = 'NOT_PERFORMED_REQUIRES_INDEPENDENT_REVIEW'
        manifest['status'] = ('REBUILT_REQUIRES_INDEPENDENT_QA'
                              if availability.get('status') == 'COMPLETE_DATA_REQUIRES_VISUAL_QA' and export_audit.get('status') == 'PASS'
                              else 'REBUILT_WITH_INCOMPLETE_REQUIREMENTS')
        manifest['finished_utc'] = utc()
        write_json(destination / 'rebuild_manifest.json', manifest)
        print(json.dumps({'status': manifest['status'], 'destination': str(destination),
                          'visual_qa': manifest['visual_qa'], 'api_calls': 0}, ensure_ascii=False))
    except BaseException as error:
        manifest.update(status='FAILED_RETAINED_FOR_DIAGNOSIS', finished_utc=utc(),
                        error={'type': type(error).__name__, 'message': str(error)})
        write_json(destination / 'rebuild_manifest.json', manifest)
        (destination / 'logs/failure.traceback.log').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        for lock in reversed(locks):lock.close()


if __name__ == '__main__':
    main()
