"""Build a new, secret-scanned source bundle only after all experiment writers close.

This is NOT the raw experiment archive. Raw bytes stay in their original location;
the bundle supplies a complete independent inventory/checksum join to that archive.
No provider call, Docker export, environment dump, or workspace-wide copy occurs.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile

DEFAULT_OUT=Path('/evidence/formal_20261019')
TEXT_STATIC={'.py','.sh','.md','.txt','.toml','.json','.jsonl','.yaml','.yml','.csv','.html','.css','.js','.svg'}
STATIC=TEXT_STATIC|{'.npz','.npy','.typed','.png','.jpg','.jpeg'}
EXCLUDED_PARTS={'__pycache__','.pytest_cache','.git','.venv','venv','results','logs','history','verification_logs','.agent_data'}
ROOT_FILES=('pyproject.toml','README.md','LICENSE','SECURITY.md','lingxi_code.py')
MANIFESTS=('formal_plan.json','formal-freeze.json','source_identity.json','source-files.json',
    'environment.json','environment_visibility_during_main.json','models.json','native_configurations.json',
    'budget_limits.json','dependencies.txt','original_protocol.md','protocol_alignment.json',
    'protocol_alignment_before_user_exception.json','protocol_changes.md','protocol_microbenchmark_plan.json',
    'protocol_microbenchmark_v2_plan.json','quantization_plan.json','state_matrix_plan.json','state_reuse_plan.json')
ANALYSIS_PATTERNS=('stats/statistical_plan*.json','stats/statistical_plan*.md',
    'stats/statistical_implementation_protocol_alignment_v3.json','stats/state_increment_population_audit_v1.*',
    'stats/automatic_spotcheck_plan.json','stats/prospective_power*.json',
    'report_protocol_alignment_v3.json','report_spotcheck_timing_correction_v2.json',
    'report_spotcheck_authority_alignment.json','*_contract.json','field_authority_and_join_contract.json',
    'automatic_corpus_validation.json','stress_fault_semantic_audit.json','reproduce_noncomparable_terminal_alignment.json',
    'preflight_test_path_audit_v1.json','report_style_alignment_v1.json','reproduce_contract_metadata_alignment.json',
    'schema_validation_contract_v2.json','report_qa_contract_v2.json','schema_validation_contract_v3.json',
    'schema_validation_contract_v4.json','schema_validation_path_contract_correction_v4.json',
    'report_qa_contract_v3.json','report_qa_contract_v4.json','report_numeric_serialization_audit_v1/audit.json',
    'report_qa_v4/visual_and_numeric_findings.json','report_qa_v4/execution_receipt.json',
    'report_qa_v4/automated_qa.json','report_qa_v4/timestamp_metadata_correction.json',
    'report_qa_v4/preparation_versions/visual_and_numeric_findings_before_timestamp_disambiguation.json',
    'claims_to_evidence_audit_v1.json','claims_to_evidence_audit_v2.json',
    'claims_to_evidence_audit_v3_provenance.json','claims_to_evidence_audit_v1.reviewed.md',
    'protocol_completion_audit_v1/C004_ancestral_scope_correction_addendum_v3.json',
    'protocol_completion_audit_v1/C004_ancestral_scope_correction_addendum_v3.md',
    'report_recovery_v3/manifest.json','report_recovery_v3/numerical_identity.json',
    'report_qa_v2/visual_and_numeric_findings.json','report_qa_v2/execution_receipt.json',
    'report_recovery_v2/manifest.json',
    'schema_validation_execution_v1/manifest.json','schema_validation_execution_v1/schema_validation_v1.stdout.log',
    'schema_validation_execution_v1/schema_validation_v1.stderr.log',
    'schema_validation_v1/schema_validation_audit.json','schema_validation_v1/validation_failures.jsonl',
    'final_render_selection_v3.json','state_measurement_correction_v3_analyzer_correction_v1.json',
    'state_measurement_correction_v2_registration.json','state_measurement_correction_v2_registration.md',
    'state_measurement_correction_v3_registration.json','state_measurement_correction_v3_registration.md')
FINAL_SELECTION_SHA256='fc8dcde3bded0cdeb4b77c93adde80eb21b954d632a9e4459b0e90bfaf357a00'
RETRIEVAL_V2='retrieval_input_correction_v2'
STATE_STAGE_ROOT='state_measurement_correction_v3'
FAILED_STATE_STAGE_ROOT='state_measurement_correction_v2'
SCHEMA_CORRECTION_SHA256='e9fd1531732040467c048f83df7e2ad88c2f36088b0789c41bd1a8bdb1dd4970'
SCHEMA_CONTRACT_SHA256='3db924512dd2c2f529f2f361fb550509bfcc18e5087c19ed90ec12157632726d'
SCHEMA_VALIDATOR_SHA256='ade0d73293cf4d0d4a191c2987b7340e921618163b36e856064005d92fbf554c'
SCHEMA_CONTROLLER_SHA256='fe128d2bfdb0d85340b7472c5d2a72d92974fb2d812489174fb7a038f6c6e3d8'
ARCHIVE_ROOTS=('raw',RETRIEVAL_V2+'/raw',FAILED_STATE_STAGE_ROOT+'/raw',STATE_STAGE_ROOT+'/raw',FAILED_STATE_STAGE_ROOT+'/logs')
SENSITIVE_NAME=re.compile(r'(?i)(?:^\.env(?:\.|$)|\.(?:pem|key|p12|pfx)$|^(?:id_rsa|id_ed25519|credentials)(?:\.|$))')
SENSITIVE_BYTES={
    'provider_key':re.compile(rb'\bsk-[A-Za-z0-9_\-]{20,}\b'),
    'private_key_block':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
    'literal_bearer_value':re.compile(rb'(?i)\bBearer[ \t]+[A-Za-z0-9_\-./+=]{24,}'),
    'url_embedded_credentials':re.compile(rb'(?i)https?://[^\s/@:\x22\x27]{1,80}:[^\s/@\x22\x27]{4,160}@'),
    'long_literal_credential':re.compile(rb'(?i)[\x22\x27]?(?:api_?key|api_?token|access_token|password|secret)[\x22\x27]?\s*[:=]\s*[\x22\x27][A-Za-z0-9_/+=.\-]{24,}[\x22\x27]'),
}


def utc():return datetime.now(timezone.utc).isoformat()


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def fingerprint(path):
    value=path.stat()
    return {'device':value.st_dev,'inode':value.st_ino,'bytes':value.st_size,'mtime_ns':value.st_mtime_ns}


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while data:=stream.read(1024*1024):value.update(data)
    return value.hexdigest()


def safe_relative(value):
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '\\' in value:raise RuntimeError('unsafe relative inventory path')
    return Path(*path.parts)


def allowed(path,root):
    relative=path.relative_to(root)
    return (not any(part in EXCLUDED_PARTS or part.endswith('.egg-info') for part in relative.parts)
        and not any(SENSITIVE_NAME.search(part) for part in relative.parts)
        and (path.suffix.lower() in STATIC or path.name in {'LICENSE','SHA256SUMS'}))


def selected_inputs(out,runtime,drivers,tests):
    """An explicit tree/extension whitelist, never an arbitrary workspace walk."""
    selected={}
    def add(source,target,required=False):
        if not source.exists():
            if required:raise RuntimeError('required public input is missing: '+str(source))
            return
        if source.is_symlink() or not source.is_file():raise RuntimeError('non-regular public input: '+str(source))
        if any(SENSITIVE_NAME.search(part) for part in source.parts):raise RuntimeError('sensitive filename refused: '+str(source))
        if target in selected and selected[target]!=source:raise RuntimeError('two sources map to one package member')
        selected[target]=source
    for name in ROOT_FILES:add(runtime/name,'runtime/'+name,required=name=='pyproject.toml')
    for folder in ('src','scenarios','scripts','dashboard','config','configs','docs'):
        root=runtime/folder
        if folder in ('src','scenarios','scripts') and not root.is_dir():raise RuntimeError('runtime source folder missing: '+folder)
        if root.is_symlink():raise RuntimeError('runtime input subtree symlink refused: '+str(root))
        for path in sorted(root.rglob('*')):
            if path.is_symlink():raise RuntimeError('runtime symlink requires explicit review: '+str(path))
            if path.is_file() and allowed(path,root):add(path,'runtime/'+path.relative_to(runtime).as_posix())
    # Tests retain their root-relative src/scripts/dashboard lookups after relocation.
    for path in sorted(tests.glob('*.py')):add(path,'runtime/tests/'+path.name)
    add(tests/'conftest.py','runtime/tests/conftest.py',required=True)
    for folder in ('unit','integration','fixtures','data'):
        root=tests/folder
        if folder in ('unit','integration') and not root.is_dir():raise RuntimeError('actual preflight test section missing: '+folder)
        for path in sorted(root.rglob('*')):
            if path.is_symlink():raise RuntimeError('test fixture symlink requires explicit review: '+str(path))
            if path.is_file() and allowed(path,root):add(path,'runtime/tests/'+path.relative_to(tests).as_posix())
    # test_sota1_governor_billing::test_archive_fit_and_holdout uses this exact
    # deployment-provided numerical regression fixture. It is historical test
    # input, never a formal study result or a replacement for missing usage.
    add(runtime/'evidence/real_llm_rerun_20260924/cells.csv',
        'runtime/evidence/real_llm_rerun_20260924/cells.csv',
        required=(tests/'unit/test_sota1_governor_billing.py').exists())
    for path in sorted(drivers.iterdir()):
        if ((path.name.startswith('formal_') and path.suffix in {'.py','.sh'})
                or path.name.startswith('test_formal') and path.suffix=='.py'
                or path.name in {'bootstrap_openeuler.sh','requirements-windows.lock','formal_schema_analysis_requirements_v1.txt'}):
            add(path,'drivers/'+path.name)
    # Retain actual archived helpers and revision metadata as separate paths,
    # rather than substituting current postrun code for earlier execution bytes.
    for root,prefix in ((out/'drivers','archived_drivers'),(drivers/'preparation_versions','drivers/preparation_versions')):
        for path in sorted(root.rglob('*')):
            if path.is_symlink():raise RuntimeError('archived helper symlink refused')
            if path.is_file() and allowed(path,root):add(path,prefix+'/'+path.relative_to(root).as_posix())
    for name in ('formal_reproduce.py','formal_postrun_verify.py','formal_package_source.py'):
        if 'drivers/'+name not in selected:raise RuntimeError('required formal delivery helper missing: '+name)
    for name in MANIFESTS:add(out/'manifests'/name,'inputs/manifests/'+name,required=name in {
        'formal_plan.json','formal-freeze.json','source_identity.json','source-files.json','environment.json',
        'models.json','native_configurations.json','budget_limits.json','dependencies.txt','original_protocol.md'})
    for name in ('protocol.yaml','README_reproduce.md','preflight_report.md','claims_to_evidence.md','limitations.md'):
        add(out/name,'inputs/'+name,required=name=='protocol.yaml')
    root=out/'corpus/automatic_validated_v1'
    for name in ('tasks.jsonl','queries.jsonl','labels.jsonl','SHA256SUMS'):
        add(root/name,'inputs/corpus/automatic_validated_v1/'+name,required=True)
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise RuntimeError('frozen corpus symlink refused')
        if path.is_file() and allowed(path,root):add(path,'inputs/corpus/automatic_validated_v1/'+path.relative_to(root).as_posix())
    for pattern in ANALYSIS_PATTERNS:
        for path in sorted((out/'analysis').glob(pattern)):
            add(path,'inputs/analysis/'+path.relative_to(out/'analysis').as_posix())
    add(out/'analysis/final_artifact_selection_v2.json','inputs/analysis/final_artifact_selection_v2.json',required=True)
    # Independently installed schema environment: exact requirements/freeze/check,
    # installer and manifest contain wheel SHAs; large wheel bytes stay external.
    environment=out/'analysis/schema_environment_v1'
    for name in ('manifest.json','formal_schema_analysis_requirements_v1.txt','formal_schema_environment_install.py',
        '04_freeze.stdout.log','05_check.stdout.log','06_import.stdout.log'):
        add(environment/name,'inputs/analysis/schema_environment_v1/'+name,required=True)
    revision=out/'analysis/final_artifact_pointer_helper_revision_v2'
    for path in sorted(revision.glob('*')):
        if path.is_file() and (path.suffix=='.json' or path.name in {'formal_package_source.py.before','formal_reproduce.py.before'}):
            add(path,'inputs/analysis/final_artifact_pointer_helper_revision_v2/'+path.name)
    # Corrected public inputs remain separate from the original frozen corpus.
    corrected=out/RETRIEVAL_V2
    for folder in ('corpus','drivers'):
        for path in sorted((corrected/folder).rglob('*')):
            if path.is_symlink():raise RuntimeError('corrected retrieval input symlink refused')
            if path.is_file() and allowed(path,corrected/folder):
                add(path,'inputs/'+RETRIEVAL_V2+'/'+path.relative_to(corrected).as_posix())
    for name in ('README.md','checksums_inputs.sha256'):
        add(corrected/name,'inputs/'+RETRIEVAL_V2+'/'+name,required=True)
    for name in ('before_new_outcome_registration.json','query_diffs.json','query_semantic_validation.json',
        'native_input_inventory.json','input_inventory_sha256.json','root_input_review_v1.json',
        'retrieval_v2_execution.json','retrieval_v2_analysis.json','retrieval_quality_scale_audit.json'):
        add(corrected/'analysis'/name,'inputs/'+RETRIEVAL_V2+'/analysis/'+name,required=True)
    for pattern in ('retrieval_n*_build*_pool_identity.json','retrieval_n*_build*_build.json',
        'retrieval_n*_build*_process.json','retrieval_n*_build*_status.json'):
        for path in sorted((corrected/'analysis').glob(pattern)):
            add(path,'inputs/'+RETRIEVAL_V2+'/analysis/'+path.name)
    for state_name in (FAILED_STATE_STAGE_ROOT,STATE_STAGE_ROOT):
        state=out/state_name
        for name in ('manifest.json','prospective_registration.json'):
            add(state/name,'inputs/'+state_name+'/'+name,required=True)
        for pattern in ('execution.json','batch_*.json','child_*.json','*.schema.json',
            'recomputed_v1/execution_failure.json','recomputed_v2/stage_cells.csv',
            'recomputed_v2/stage_join_audit.json','recomputed_v2/matrix_audit.json'):
            for path in sorted((state/'analysis').glob(pattern)):
                add(path,'inputs/'+state_name+'/analysis/'+path.relative_to(state/'analysis').as_posix())
    return selected


def scanned_identity(path):
    """Only path/count/hash may escape this function; no matched value is returned."""
    before=fingerprint(path);value=hashlib.sha256();carry=b'';offset=0;hits=set()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024):
            value.update(block);data=carry+block;base=offset-len(carry)
            for name,pattern in SENSITIVE_BYTES.items():
                for match in pattern.finditer(data):hits.add((name,base+match.start()))
            offset+=len(block);carry=data[-1024:]
    after=fingerprint(path)
    if before!=after:raise RuntimeError('input changed while hashing/scanning: '+str(path))
    result={'sha256':value.hexdigest(),'bytes':after['bytes'],'fingerprint':after,
        'hit_counts':dict(Counter(name for name,_ in hits))}
    # Exact public offline-test fixture adjudication, never a broad key exemption.
    # The immutable source installs httpx.MockTransport and uses this deliberately
    # offline literal; no private key is read here or included in the bundle.
    if path.name=='formal_external_runner_audit.py' and \
       result['sha256']=='524caaf6da8add74d756405b68f1e1497840b7b8e5a971be26b6547b7afc2f32' and \
       result['hit_counts']=={'long_literal_credential':1}:
        matches=list(SENSITIVE_BYTES['long_literal_credential'].finditer(path.read_bytes()))
        if len(matches)==1 and hashlib.sha256(matches[0].group()).hexdigest()== \
                'f86c00261884445d8488e8f76e90b4f4da2827498d87264b2c3b7a8a7c12a2f2':
            result['reviewed_synthetic_fixture']={'classification':'PUBLIC_OFFLINE_MOCK_TRANSPORT_CREDENTIAL',
                'source_sha256':result['sha256'],'pattern':'long_literal_credential','original_hit_count':1,
                'match_sha256':'f86c00261884445d8488e8f76e90b4f4da2827498d87264b2c3b7a8a7c12a2f2',
                'rule':'Exact frozen file plus exact matched bytes only; all other matches still block; no credential value emitted'}
            result['hit_counts']={}
    return result


def source_tree_sha(root):
    """Byte-for-byte algorithm from the actual frozen runtime runner."""
    candidates=[root/'pyproject.toml']
    for name in ('src','scenarios','scripts'):
        candidates.extend(p for p in (root/name).rglob('*') if p.is_file() and p.suffix in {'.py','.sh','.json','.npz'})
    value=hashlib.sha256()
    for path in sorted(candidates,key=lambda item:item.relative_to(root).as_posix()):
        value.update(path.relative_to(root).as_posix().encode());value.update(b'\0')
        with path.open('rb') as stream:
            while data:=stream.read(1024*1024):value.update(data)
        value.update(b'\0')
    return value.hexdigest()


def frozen_file_path(original,out,drivers):
    if original.startswith('/evidence/formal_20261019/'):return out/safe_relative(original.removeprefix('/evidence/formal_20261019/'))
    if original.startswith('/drivers/'):return drivers/safe_relative(original.removeprefix('/drivers/'))
    raise RuntimeError('frozen path lies outside declared input roots')


def verify_source_identity(out,runtime,drivers):
    freeze=json.loads((out/'manifests/formal-freeze.json').read_text())
    identity=json.loads((out/'manifests/source_identity.json').read_text())
    expected=freeze['source_sha256'];actual=source_tree_sha(runtime)
    if actual!=expected or identity['source_sha256']!=expected:raise RuntimeError('formal runtime aggregate SHA differs from frozen source')
    manifest_path=out/'manifests/source-files.json'
    environment=json.loads((out/'manifests/environment.json').read_text())
    if digest(manifest_path)!=environment['source_file_manifest_sha256']:raise RuntimeError('formal per-file source manifest identity differs')
    entries=json.loads(manifest_path.read_text());listed=set()
    for row in entries:
        relative=safe_relative(row['path']);listed.add(relative.as_posix())
        if digest(runtime/relative)!=row['sha256']:raise RuntimeError('formal per-file source SHA differs: '+relative.as_posix())
    actual_python={p.relative_to(runtime).as_posix() for p in (runtime/'src').rglob('*.py')}
    if listed!=actual_python:raise RuntimeError('formal src Python per-file manifest coverage differs')
    for original,expected_sha in freeze['files'].items():
        if digest(frozen_file_path(original,out,drivers))!=expected_sha:raise RuntimeError('frozen input changed: '+original)
    protocol=(out/'protocol.yaml').read_text()
    expected_protocol=re.findall(r'^protocol_sha256:\s*([0-9a-f]{64})\s*$',protocol,re.MULTILINE)
    if len(expected_protocol)!=1 or digest(out/'manifests/original_protocol.md')!=expected_protocol[0]:
        raise RuntimeError('original protocol does not match frozen protocol hash')
    return {'aggregate_sha256':actual,'src_python_manifest_entries':len(entries),'frozen_files_verified':len(freeze['files']),
        'algorithm_scope':'pyproject.toml plus .py/.sh/.json/.npz under src/scenarios/scripts; root-relative sorted names and NUL separators',
        'manifest_scope':'source-files.json registers src/*.py only; full runtime and static resources are additionally packaged and checksummed',
        'freeze_utc':freeze.get('frozen_at_utc')},freeze


def raw_files(root):
    files=[]
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise RuntimeError('raw inventory contains symlink; explicit resolution required')
        if path.is_file():
            if any(SENSITIVE_NAME.search(part) for part in path.relative_to(root).parts):raise RuntimeError('sensitive raw filename requires private review: '+str(path.relative_to(root)))
            files.append(path)
    if not files:raise RuntimeError('raw archive is empty')
    return files


def archive_raw_files(out):
    return [path for relative in ARCHIVE_ROOTS for path in raw_files(out/relative)]


def visible_archive_writers(out):
    combined={'visible_writers':[],'unreadable_process_scopes':[],
        'scope':'current proc-visible writable FDs under four raw roots plus the specifically authorized closed failed-state-v2 diagnostic logs; excludes Windows-host handles'}
    for relative in ARCHIVE_ROOTS:
        probe=visible_raw_writers(out/relative)
        for key in ('visible_writers','unreadable_process_scopes'):
            combined[key].extend(dict(raw_root=relative,**row) for row in probe[key])
    return combined


def visible_raw_writers(root):
    writers=[];unreadable=[]
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:
            for fd in (process/'fd').iterdir():
                try:
                    target=os.readlink(fd).removesuffix(' (deleted)')
                    if not target.startswith('/'):continue
                    path=Path(target)
                    if not path.is_relative_to(root):continue
                    lines=(process/'fdinfo'/fd.name).read_text().splitlines()
                    flags=next(int(line.split()[1],8) for line in lines if line.startswith('flags:'))
                    if flags&os.O_ACCMODE:writers.append({'pid':int(process.name),'fd':fd.name,'path':str(path.relative_to(root))})
                except (FileNotFoundError,ProcessLookupError):pass
                except PermissionError:unreadable.append({'pid':int(process.name),'scope':'raw-target fdinfo'})
        except (FileNotFoundError,ProcessLookupError):pass
        except PermissionError:unreadable.append({'pid':int(process.name),'scope':'process fd directory'})
    return {'visible_writers':writers,'unreadable_process_scopes':unreadable,
        'scope':'current /proc-visible open writable FDs; not Windows-host handles or a universal future-writer exclusion'}


def load_helper(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module


def closure_gate(out,drivers,verification_dir):
    sequence=json.loads((out/'logs/mechanisms/formal_complete_sequence_v1.result.json').read_text())
    if sequence.get('exit_code')!=0 or sequence.get('timed_out'):raise RuntimeError('existing full sequence is not closed successfully')
    verification=json.loads((verification_dir/'manifest.json').read_text())
    # The fixed selection keeps actual step12 plotting failure in the original
    # verifier; its eleven successful predecessors are not relabelled or rerun.
    steps=verification.get('steps',[])
    if len(steps)!=12 or any(s.get('status')!='PASS' or s.get('exit_code')!=0 for s in steps[:11]) or \
        steps[11].get('name')!='12_formal_report' or steps[11].get('status')!='FAILED_EXIT' or steps[11].get('exit_code')!=1:
        raise RuntimeError('original postrun evidence must retain actual01-11PASS/12FAIL')
    selection_path=out/'analysis/final_artifact_selection_v2.json'
    if digest(selection_path)!=FINAL_SELECTION_SHA256:raise RuntimeError('before-v2-outcome artifact selection changed')
    selection=json.loads(selection_path.read_text())
    if verification_dir.resolve()!=(out/'analysis/postrun_verification_v1').resolve():
        raise RuntimeError('prior verification directory differs from fixed evidence selection')
    recovery=out/safe_relative(selection['report_recovery_root'])/'manifest.json'
    if json.loads(recovery.read_text()).get('status')!='PASS':raise RuntimeError('selected report recovery has not actually passed')
    availability=out/safe_relative(selection['report_root'])/'analysis/report_availability.json'
    availability_status=json.loads(availability.read_text()).get('status')
    if availability_status not in {'COMPLETE_DATA_REQUIRES_VISUAL_QA','INCOMPLETE'}:
        raise RuntimeError('selected report has no recognized executed availability record')
    schema_correction_path=out/'analysis/schema_validation_path_contract_correction_v4.json'
    if digest(schema_correction_path)!=SCHEMA_CORRECTION_SHA256:
        raise RuntimeError('versioned schema path-correction identity changed')
    schema_correction=json.loads(schema_correction_path.read_text())
    schema_contract=out/'analysis/schema_validation_contract_v4.json'
    schema=out/safe_relative(schema_correction['new_validation_destination'])/'schema_validation_audit.json'
    schema_execution_path=out/safe_relative(schema_correction['new_execution_destination'])/'manifest.json'
    schema_execution=json.loads(schema_execution_path.read_text())
    schema_result=json.loads(schema.read_text())
    if digest(schema_contract)!=SCHEMA_CONTRACT_SHA256 or digest(drivers/'formal_schema_validation.py')!=SCHEMA_VALIDATOR_SHA256 or \
        digest(drivers/'formal_schema_validation_execute_v2.py')!=SCHEMA_CONTROLLER_SHA256 or \
        schema_execution.get('status')!='PASS' or schema_execution.get('source_sha256')!=SCHEMA_CONTROLLER_SHA256 or \
        schema_execution.get('validator_sha256')!=SCHEMA_VALIDATOR_SHA256 or schema_execution.get('contract_sha256')!=SCHEMA_CONTRACT_SHA256 or \
        schema_execution.get('audit_sha256')!=digest(schema) or not schema_execution.get('finished_utc') or \
        schema_result.get('module_sha256')!=SCHEMA_VALIDATOR_SHA256:
        raise RuntimeError('selected schema v4 contract/controller/source/actual audit identity differs')
    schema_steps=schema_execution.get('steps',[])
    if len(schema_steps)!=1 or schema_steps[0].get('status')!='PASS' or schema_steps[0].get('exit_code')!=0:
        raise RuntimeError('selected schema actual execution did not pass')
    scope=schema_result.get('raw_scope_selection',{})
    recorded_out=Path(scope.get('file','')).parent.parent
    if scope.get('sha256')!=FINAL_SELECTION_SHA256 or \
        scope.get('raw_roots')!=['raw',RETRIEVAL_V2+'/raw',STATE_STAGE_ROOT+'/raw',FAILED_STATE_STAGE_ROOT+'/raw'] or \
        schema_result.get('input_sha256',{}).get(str(recorded_out/'analysis/schema_validation_contract_v4.json'))!=SCHEMA_CONTRACT_SHA256:
        raise RuntimeError('selected schema actual input contract/four-root scope differs')
    if schema_result.get('status')!='PASS_SCHEMA_READBACK_SEMANTIC115_NOT_CERTIFIED':
        raise RuntimeError('selected independent schema/readback has not actually passed')
    environment=out/safe_relative(selection['schema_environment'])/'manifest.json'
    if json.loads(environment.read_text()).get('status')!='PASS':raise RuntimeError('independent schema environment did not close')
    postcheck=out/safe_relative(selection['source_postcheck_root'])/'source_test_postcheck.json'
    check=json.loads(postcheck.read_text())
    if not check.get('finished_utc') or check.get('import_scope',{}).get('status')!='PROVEN_OBSERVED_MODULES_FORMAL' or \
        not check.get('source_stable') or not check.get('tests_stable') or \
        not all(check['source_stable'].values()) or not all(check['tests_stable'].values()) or \
        not check.get('generated_dashboard_restoration',{}).get('restored'):
        raise RuntimeError('selected source postcheck has unresolved closure/source scope')
    # Retain actual WSL/native-Linux failure/namespace skip; archive closure is
    # not a certificate that the original preflight or scientific goal passed.
    if check.get('status') not in {'POSTHOC_TESTS_PASS_SOURCE_SCOPE_VERIFIED','POSTHOC_TEST_FAILURE_OR_SCOPE_ISSUE_RETAINED'}:
        raise RuntimeError('source postcheck is not in an executed terminal state')
    if check['status']=='POSTHOC_TEST_FAILURE_OR_SCOPE_ISSUE_RETAINED':
        junit=check.get('junit',{})
        if junit.get('testcases')!=653 or junit.get('failures')!=1 or junit.get('errors')!=0 or junit.get('skipped')!=1 or \
            junit.get('failure_identities')!=[{'classname':'tests.unit.test_competition_scorecard',
                'name':'test_competition_scorecard_passes_all_non_environment_requirements'}] or \
            check.get('pytest',{}).get('exit_code')!=1 or check.get('pytest',{}).get('timed_out'):
            raise RuntimeError('source postcheck failure differs from the retained single native-Linux/WSL boundary')
    completion=json.loads((verification_dir/'complete_matrix_audit.json').read_text())
    if completion.get('status')!='PASS' or completion.get('terminal_units')!=6504:raise RuntimeError('all6504 completion/provenance audit must pass')
    reproduce=load_helper(drivers/'formal_reproduce.py','source_package_terminal_gate')
    reproduce.real_terminal_gate(out)
    retrieval=reproduce.selected_retrieval_gate(out)
    state_stage=reproduce.selected_state_stage_gate(out)
    render=reproduce.selected_render_gate(out)
    postrun=load_helper(drivers/'formal_postrun_verify.py','source_package_pressure_gate')
    pressure=postrun.pressure_gate(out)
    if pressure.get('status')!='GATE_COMPLETE':raise RuntimeError('complete pressure/continuous/fault closure gate did not pass')
    writers=visible_archive_writers(out)
    if writers['visible_writers'] or writers['unreadable_process_scopes']:raise RuntimeError('raw writers exist or current writer visibility is incomplete')
    return {'sequence_sha256':digest(out/'logs/mechanisms/formal_complete_sequence_v1.result.json'),
        'verification_manifest_sha256':digest(verification_dir/'manifest.json'),
        'final_artifact_selection_sha256':digest(selection_path),'selected_retrieval':retrieval,'state_stage_supplement':state_stage,
        'publication_render':render,'original_data_report_recovery_sha256':digest(recovery),
        'original_data_report_availability_sha256':digest(availability),
        'original_data_report_actual_availability_status':availability_status,'numeric_visual_qa':'NOT_CERTIFIED_BY_PACKAGING',
        'schema_validation_sha256':digest(schema),'schema_execution_sha256':digest(schema_execution_path),
        'schema_contract_sha256':digest(schema_contract),'schema_path_correction_sha256':digest(schema_correction_path),
        'source_postcheck_sha256':digest(postcheck),
        'source_postcheck_actual_status':check['status'],'source_postcheck_actual_pytest':check.get('pytest'),
        'completion_audit_sha256':digest(verification_dir/'complete_matrix_audit.json'),
        'provider_and_verification_locks':'held exclusively by this packager','writer_probe':writers,
        'planned_real_terminal_units':6504,'pressure_counts':pressure['counts'],
        'limits':'archive closure does not certify scientific conclusions, visual QA, or completion of the paper'},completion,pressure


def layout_probe(tests,runtime):
    result=[]
    for path in [Path('/opt/lingxi/tests'),Path('/opt/lingxi/src'),Path('/opt/lingxi/scripts'),
        tests/'conftest.py',tests/'unit/test_source_identity.py',tests/'unit/test_docsem_embedding.py',runtime/'tests']:
        row={'path':str(path),'exists':path.exists(),'is_symlink':path.is_symlink(),'resolved':str(path.resolve())}
        if path.is_symlink():row['readlink']=os.readlink(path)
        if path.is_file() and path.name.startswith('test_'):row['resolved_parents_2']=str(path.resolve().parents[2])
        result.append(row)
    return {'sampled_utc':utc(),'phase':'post-closure packaging; not prefreeze evidence','paths':result,
        'original_preflight_boundary':'pytest ../tests/unit ../tests/integration collected /opt/lingxi/checkout/tests through /opt/lingxi/tests; conftest prepends legacy checkout/src. Filesystem and possibly import assertions were not all targeted at formal-checkout.',
        'formal_runtime_identity':'independently recorded formal-checkout aggregate/source hashes; source equivalence and postrun test results are separate evidence',
        'bundle_layout':'actual legacy test files are copied to runtime/tests so parents[2] resolves the bundled runtime; this is a reproduction path relocation, not a claim about the original preflight'}


def readonly(root):
    """Verify actual reported modes; a successful chmod call is insufficient."""
    records=[]
    paths=sorted(root.rglob('*'),key=lambda path:len(path.parts),reverse=True)+[root]
    for path in paths:
        if path.is_symlink():raise RuntimeError('refuse chmod through archive symlink')
        before=stat.S_IMODE(path.stat().st_mode);error=None
        try:path.chmod(before&~0o222)
        except OSError as exception:error=type(exception).__name__
        after=stat.S_IMODE(path.stat().st_mode)
        records.append({'path':'.' if path==root else path.relative_to(root).as_posix(),
            'mode_before':oct(before),'mode_after':oct(after),'write_bits_cleared':after&0o222==0,
            'effective_process_can_write':os.access(path,os.W_OK),'error_type':error})
    supported=all(row['write_bits_cleared'] and row['error_type'] is None for row in records)
    return {'status':'READONLY_CONVENTION' if supported else 'UNSUPPORTED','paths':records,
        'limits':['stat-mode/read-only convention only; not a cryptographic or hardware write barrier',
            'root/CAP_DAC_OVERRIDE, Windows-host ACLs/bind mounts or later chmod may still permit writes; no write probe or host ACL test was performed']}


def make_portable_lock(source,target):
    lines=[];rewritten=0
    for line in source.read_text().splitlines():
        if line.strip().startswith('-e '):
            if line.strip() not in {'-e /opt/lingxi/checkout','-e /opt/lingxi/formal-checkout'}:
                raise RuntimeError('unrecognized editable dependency path; do not silently rewrite')
            line='-e ./runtime';rewritten+=1
        lines.append(line)
    target.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {'original_unchanged':True,'editable_paths_rewritten':rewritten,'invocation_cwd':'bundle root',
        'limits':'version snapshot, not a wheel/sdist hash lock or a promise of future package-index availability'}


def failure_index(verification,bundle,inventory,driver_sha):
    """Project safe identities/flags/hashes from the existing quality audit only."""
    root=verification/'quality_appendix';report_path=root/'quality_appendix_audit.json'
    report=json.loads(report_path.read_text())
    if report.get('status')!='PASS_DESCRIPTIVE_APPENDIX' or report.get('planned_units')!=6504:
        raise RuntimeError('quality appendix must close before failure/malformed source index')
    if report.get('module_sha256')!=driver_sha:raise RuntimeError('quality appendix helper identity changed')
    source=root/'quality_failures_timeouts.jsonl'
    table=next(item for item in report['tables'] if item['table']=='quality_failures_timeouts')
    expected=table['sha256'][source.name]
    if digest(source)!=expected:raise RuntimeError('quality failure index source hash differs from its audit')
    for path,sha256 in report['source_sha256'].items():
        matched=re.search(r'/raw/([^/]+)$',path)
        if matched and inventory['raw/'+matched.group(1)]['sha256']!=sha256:
            raise RuntimeError('quality appendix raw provenance differs from closed inventory')
    fields=('run_id','phase','task_id','arm','repeat','model_id','terminal_status','independent_task_success',
        'json_syntax_valid','decision_contract_valid','decision_answer_complete','explicit_timeout_marker_observed',
        'automatic_error_classification','response_selection_status','response_text_status','parse_error_type',
        'json_syntax_error_type','selected_request_id','source_task_line','source_task_sha256',
        'selected_response_line','selected_response_sha256','response_text_sha256')
    count=0;seen=set();target=bundle/'metadata/failure_malformed_raw_join_index.jsonl'
    with source.open(encoding='utf-8') as stream,target.open('x',encoding='utf-8') as output:
        for line in stream:
            if not line.strip():continue
            row=json.loads(line);identity=row['run_id']
            if identity in seen:raise RuntimeError('duplicate quality failure index ID')
            seen.add(identity);count+=1
            output.write(json.dumps({key:row.get(key) for key in fields},ensure_ascii=False,sort_keys=True)+'\n')
    if count!=table['rows'] or digest(source)!=expected:raise RuntimeError('quality failure index coverage/source changed')
    scanned=scanned_identity(target)
    if scanned['hit_counts']:raise RuntimeError('safe failure index unexpectedly contains sensitive-looking bytes')
    contract={'rows':count,'source_path':str(source),'source_sha256':expected,'quality_audit_sha256':digest(report_path),
        'original_raw_task_sha256':inventory['raw/task_runs.jsonl']['sha256'],
        'original_raw_model_io_sha256':inventory['raw/model_io.jsonl']['sha256'],
        'safe_fields':fields,'omitted':['raw error text','provider response text','model messages','private RPC bodies'],
        'scope':'all existing quality-failure/timeout/invalid-contract rows projected without selecting successes; original raw remains complete and unchanged'}
    dump(bundle/'metadata/failure_malformed_raw_join_index_contract.json',contract)
    return contract


def package(args):
    out=args.out.resolve();runtime=args.runtime_root.resolve();drivers=args.drivers_root.resolve();tests=args.tests_root.resolve()
    target=args.destination.resolve();verification=(args.verification_dir or out/'analysis/postrun_verification_v1').resolve()
    for root in (out,runtime,drivers,tests):
        if target.is_relative_to(root) or root.is_relative_to(target):raise RuntimeError('new package must lie outside all authoritative input trees')
    if target.exists():raise RuntimeError('destination exists; choose a new version, never overwrite')
    target.mkdir(parents=True);bundle=target/'bundle'
    audit={'status':'CHECKING_CLOSURE','started_utc':utc(),'source_helper_sha256':digest(Path(__file__)),
        'paid_api_calls':0,'raw_bytes_copied':0,'destination':str(target),'seal_raw_requested':args.seal_raw,
        'scope':'source/driver/public-input bundle plus independent raw inventory; full experiment records remain separate',
        'goal_completion':'NOT_CERTIFIED','scan_findings':[]}
    save=lambda:dump(target/'source_package_audit.json',audit)
    save()
    try:
        with ExitStack() as stack:
            for name in ('analysis/postrun_verification.lock','manifests/real_provider_execution.lock'):
                lock=stack.enter_context((out/name).open('r+'))
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            closed,completion,pressure=closure_gate(out,drivers,verification)
            audit['closure']=closed;dump(target/'pressure_closure_gate.json',pressure);save()
            identity,freeze=verify_source_identity(out,runtime,drivers);audit['runtime_identity']=identity
            selected=selected_inputs(out,runtime,drivers,tests)
            required_frozen={str(frozen_file_path(name,out,drivers).resolve()) for name in freeze['files']}
            if not required_frozen<={str(path.resolve()) for path in selected.values()}:
                raise RuntimeError('source bundle whitelist omits a frozen public input')
            needed=2*sum(path.stat().st_size for path in selected.values())+256*1024**2
            audit['space']={'estimated_bytes':needed,'free_bytes':shutil.disk_usage(target).free}
            if audit['space']['free_bytes']<needed:raise RuntimeError('insufficient free space for source bundle and tar')
            bundle.mkdir();records={}
            frozen_paths={str(frozen_file_path(name,out,drivers).resolve()):sha256 for name,sha256 in freeze['files'].items()}
            for relative,path in selected.items():
                item=scanned_identity(path)
                if item.get('reviewed_synthetic_fixture'):
                    audit.setdefault('reviewed_public_test_fixtures',[]).append(
                        {'member':relative,**item['reviewed_synthetic_fixture']})
                if item['hit_counts']:
                    audit['scan_findings'].append({'path':str(path),'sha256':item['sha256'],'hit_counts':item['hit_counts']});save()
                    raise RuntimeError('sensitive-looking public input blocked; inspect path/count/hash audit without exposing values')
                destination=bundle/safe_relative(relative);destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,destination)
                if digest(destination)!=item['sha256'] or fingerprint(path)!=item['fingerprint']:raise RuntimeError('snapshot copy identity changed: '+relative)
                absolute=str(path.resolve())
                classification=('FROZEN_MATCH' if absolute in frozen_paths else
                    'FROZEN_RUNTIME_AGGREGATE_MEMBER' if path.is_relative_to(runtime)
                    and (path==runtime/'pyproject.toml' or path.parts[len(runtime.parts)] in ('src','scenarios','scripts'))
                    and path.suffix in {'.py','.sh','.json','.npz'} else
                    'HISTORICAL_REGRESSION_FIXTURE_ONLY' if relative=='runtime/evidence/real_llm_rerun_20260924/cells.csv' else
                    'POST_FREEZE_HELPER_OR_PUBLIC_DOCUMENT_SNAPSHOT')
                if absolute in frozen_paths and item['sha256']!=frozen_paths[absolute]:raise RuntimeError('copy differs from frozen file identity')
                item.update(source_path=str(path),copied_at_utc=utc(),freeze_classification=(
                    classification),
                    source_mtime_utc=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat())
                records[relative]=item
            if source_tree_sha(bundle/'runtime')!=identity['aggregate_sha256']:raise RuntimeError('copied complete runtime aggregate hash differs')
            dump(bundle/'metadata/source_input_inventory.json',records)
            dump(bundle/'metadata/source_identity_verification.json',identity)
            dump(bundle/'metadata/preflight_path_boundary.json',layout_probe(tests,runtime))
            visibility=json.loads((out/'manifests/environment_visibility_during_main.json').read_text())
            image=visibility['docker_selected_resources']
            if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}',image['configured_image']):raise RuntimeError('no pinned container image digest evidence')
            dump(bundle/'metadata/container_image_identity.json',{
                'configured_image':image['configured_image'],'image_id':image['image_id'],
                'evidence':'inputs/manifests/environment_visibility_during_main.json','sample_utc':visibility.get('sample_start_utc'),
                'scope':'observed during main; no Docker export or new image resolution performed'})
            dump(bundle/'metadata/portable_dependency_lock.json',make_portable_lock(
                out/'manifests/dependencies.txt',bundle/'requirements-portable.lock'))
            schema_environment=out/'analysis/schema_environment_v1'
            schema_manifest=json.loads((schema_environment/'manifest.json').read_text())
            if schema_manifest.get('status')!='PASS' or \
                digest(schema_environment/'formal_schema_analysis_requirements_v1.txt')!=schema_manifest['requirements_sha256'] or \
                digest(schema_environment/'formal_schema_environment_install.py')!=schema_manifest['source_sha256']:
                raise RuntimeError('independent schema dependency provenance does not match actual successful installation')
            dump(bundle/'metadata/schema_analysis_dependency_lock.json',{
                'status':'EXACT_REQUIREMENTS_FREEZE_CHECK_AND_WHEEL_SHA_REFERENCES_RETAINED',
                'manifest_sha256':digest(schema_environment/'manifest.json'),
                'source_archive_wheelhouse':str(schema_environment/'wheels'),
                'wheel_sha256':schema_manifest['wheels'],'wheel_bytes_in_bundle':0,
                'requirements':'inputs/analysis/schema_environment_v1/formal_schema_analysis_requirements_v1.txt',
                'freeze':'inputs/analysis/schema_environment_v1/04_freeze.stdout.log',
                'scope':'independent post-measurement schema analysis environment; formal measurement venv unchanged; large public wheels remain referenced by exact original file names and SHA256'})
            inventory={};original_raw=archive_raw_files(out)
            for path in original_raw:
                item=scanned_identity(path);relative=path.relative_to(out).as_posix()
                if item['hit_counts']:
                    audit['scan_findings'].append({'path':relative,'sha256':item['sha256'],'hit_counts':item['hit_counts']});save()
                    raise RuntimeError('sensitive-looking original raw requires private review; raw bytes were not copied')
                item['mode_before_seal']=oct(stat.S_IMODE(path.stat().st_mode));inventory[relative]=item
            for name,record in completion['source_files'].items():
                if inventory['raw/'+name+'.jsonl']['sha256']!=record['sha256']:raise RuntimeError('raw differs from completed matrix audit: '+name)
            writers=visible_archive_writers(out)
            if writers['visible_writers'] or writers['unreadable_process_scopes']:raise RuntimeError('raw writer exclusion no longer holds')
            if archive_raw_files(out)!=original_raw:raise RuntimeError('raw inventory changed while packaging')
            for relative,item in inventory.items():
                if fingerprint(out/relative)!=item['fingerprint']:raise RuntimeError('raw changed before seal: '+relative)
            for relative,path in selected.items():
                if digest(path)!=records[relative]['sha256']:raise RuntimeError('source/helper/public input changed during packaging: '+relative)
            if selected_inputs(out,runtime,drivers,tests)!=selected:raise RuntimeError('public source inventory changed during packaging')
            verify_source_identity(out,runtime,drivers)
            if args.seal_raw:
                roots={relative:readonly(out/relative) for relative in ARCHIVE_ROOTS}
                seal={'status':'UNSUPPORTED' if any(item['status']=='UNSUPPORTED' for item in roots.values()) else 'READONLY_CONVENTION',
                    'roots':roots,'limits':['Only four closed raw roots and exact closed failed-state-v2/logs; original OUT/analysis and all other logs remain writable. Mode-bit convention is not immutable storage.']}
            else:seal={'status':'NOT_REQUESTED','limits':['archive raw and exact failed-state diagnostic permissions were not changed']}
            audit['original_raw_readonly']=seal
            for relative,item in inventory.items():
                if digest(out/relative)!=item['sha256']:raise RuntimeError('raw bytes changed across closure/seal: '+relative)
                item['mode_after_seal']=oct(stat.S_IMODE((out/relative).stat().st_mode))
            dump(bundle/'metadata/original_raw_inventory.json',{'original_out':str(out),'raw_bytes_in_bundle':0,
                'files':inventory,'read_only_status':seal['status'],'meaning':'all raw files including failed/malformed records retained; opaque byte inventory, no record filtering',
                'private_rpc_policy':'no raw message/model/AgentHost RPC body is included in this source package'})
            dump(bundle/'metadata/raw_readonly_verification.json',seal)
            (bundle/'metadata/original_raw_checksums.sha256').write_text(''.join(
                item['sha256']+'  '+relative+'\n' for relative,item in inventory.items()),encoding='utf-8')
            audit['failure_malformed_index']=failure_index(verification,bundle,inventory,
                records['drivers/formal_quality_appendix.py']['sha256'])
            readme='''# Formal study source and public-input bundle

This bundle contains the actual formal runtime, static resources, actual preflight test source, all formal execution/analysis helpers, dependency snapshot, pinned image evidence, original protocol and its authorized automatic-review exception, frozen synthetic corpus, and analysis registrations/contracts. It does NOT contain the full experiment records. Use it together with the separately retained original raw archive. metadata/original_raw_inventory.json and original_raw_checksums.sha256 identify every original raw file byte-for-byte, including failures and malformed/preparation records. No private RPC body or model request/response body is copied here.

The fixed before-new-outcome analysis/final_artifact_selection_v2.json identifies corrected retrieval inputs/results and the original report_v2 data output. Its bytes are unchanged. The separate final_render_selection_v3.json selects report_v3 as publication rendering after actual report_v2 visual QA found three single-column label defects. Only figure01/06/07 single-column wrapping changed. Actual recovery_v3 retained its exact-byte guard failure:41 CSV/statistics files match exact bytes; only abstract_metrics.json object ordering differs. The independent report_numeric_serialization_audit_v1 proves57798 typed nodes with zero value/type differences, one object-order difference and identical canonical JSON. The original failed controller/byte guard remain failed and preserved; fresh rebuilds require exact CSV/statistics bytes and typed canonical abstract identity. This is a postrun layout/serialization disposition, not preregistration or effect-based data selection. The report_v2 output, failed visual QA, original postrun01-11 successes and step12 plotting failure remain unchanged. The separate schema_validation_path_contract_correction_v4.json binds actual schema_validation_v2/execution_v2 and the v4 contract; the old schema_v1 startup failure and fc8 selection remain historical evidence. Source postcheck_v4 retains its actual native-Linux/WSL failure and namespace skip; packaging is archive closure, not a preflight PASS certificate. Original retrieval14400 is input-defect archive_only; corrected retrieval14400 is separate and is never pooled. The corrected public corpus/registration/algorithms are included under inputs/retrieval_input_correction_v2; its raw bytes remain in the separately checksummed archive. All raw roots are covered by the opaque inventory and optional mode-bit sealing.

The separately prospective-registered selected state_measurement_correction_v3 contributes the authoritative phase supplement. Its25200 measured and2520 warmup transfers and producer/child/resource streams are retained outside this source-only bundle; registration/schemas/native source identities, execution metadata and the actual analysis/recomputed_v2/stage_cells.csv join/matrix audits are included as public evidence. The registered670cd analyzer remains intact; its actual recomputed_v1 TypeError is preserved, and executed61778 analyzer/correction receipt separately bind the postrun parameter-name repair. Neither prospective source identity nor failed output is relabelled. Failed state_measurement_correction_v2 remains archive_only, including its successful first-batch rows, failure metadata and closed empty worker stdout/stderr; no completion gate or row pooling is invented for it. All four raw roots plus that exact closed failed-v2/logs diagnostic root have independent opaque checksums and optional readonly verification; diagnostic log bodies are not copied into this bundle. Pure offline reproduction re-runs only the selected corrected analyzer, never a timing driver. These phase rows are an independent table4 supplement: original full-path state numbers and figure5 remain valid, are not replaced, and are never pooled or retrospectively decomposed. Final ROOT README/claims/limitations index this supplement separately; no new main-report CLI or research condition is introduced.

metadata/schema_analysis_dependency_lock.json preserves the independent schema environment requirements, freeze/check/import and exact wheel SHA references. Large downloaded public wheel bytes are not duplicated here; retain the original referenced wheelhouse when an offline installation is required. Current postrun helpers and archived execution/revision helpers occupy different package paths with explicit snapshot time/hash boundaries.

metadata/failure_malformed_raw_join_index.jsonl preserves the complete existing quality-failure/timeout/invalid-contract index as safe run IDs, flags, source line numbers and hashes. It omits error/message/response text, retains actual negative records, and does not alter the all-planned quality denominator.

The one explicitly named runtime/evidence/real_llm_rerun_20260924/cells.csv is historical numerical input required by an existing billing regression test. It is labelled HISTORICAL_REGRESSION_FIXTURE_ONLY and is excluded from all formal metrics; no historical results directory is copied.

Preserve the original read-only archive. Extract/copy this bundle into a NEW writable work directory before installing dependencies or generating test output. From that bundle root, create a Python3.11 virtual environment and install requirements-portable.lock. The original dependencies.txt is unchanged; only its known local editable path was relocated in the portable lock. The snapshot has exact installed versions but no wheel hashes and cannot promise future index availability. The observed Docker image is recorded in metadata/container_image_identity.json; openEuler userspace shared a WSL2 kernel.

Preflight path boundary: the original command ran ../tests through /opt/lingxi/tests -> /opt/lingxi/checkout/tests, and conftest prepended that legacy checkout/src. Some filesystem and import assertions therefore targeted the original deployment checkout. This package preserves those ACTUAL test bytes in runtime/tests, with src/scripts/dashboard lookups pointing to the bundled runtime when rerun. A rerun command is `cd runtime && python -m pytest tests/unit tests/integration -q --tb=short`; do not retroactively claim that this relocation was the original formal-checkout preflight. Formal runtime source identity and current source equivalence are separate evidence.

For pure offline rebuilding, use the preserved openEuler environment and run `python /drivers/formal_reproduce.py --out ORIGINAL_FORMAL_OUT --destination NEW_REBUILD_DIRECTORY`; see the full study README and the recorded input/hash contracts. The original raw archive, necessary original metadata, and this bundle must be available together. No provider call is needed for rebuilding. Replaying paid experiments is not part of packaging or offline rebuilding.

metadata/source_input_inventory.json distinguishes frozen drivers from post-freeze helpers/documents, records snapshot times and original mtimes, and verifies the full frozen runtime aggregate separately from the135-file src Python manifest. Snapshot times are not backdated to freeze. Missing cache/provider usage stays unknown; no source bundle certifies scientific claims, numerical/visual QA, or the active goal.

Readonly means verified mode-bit convention only. A successful chmod call is insufficient: actual modes and effective write visibility are recorded. If a Windows bind mount does not honor mode changes, status is UNSUPPORTED. Root/CAP_DAC_OVERRIDE, Windows-host ACLs or later chmod may still write; this is not immutable storage. Only original raw (when --seal-raw is selected) and new package files are sealed; the original OUT/analysis remains writable for QA and evidence indexes. Security scanning reports paths/counts/hashes only, never credential values.
'''
            (bundle/'README.md').write_text(readme,encoding='utf-8')
            dump(bundle/'metadata/security_scan.json',{'status':'NO_UNREVIEWED_PATTERN_HITS','patterns':list(SENSITIVE_BYTES),
                'scanned_public_files':len(records),'scanned_opaque_raw_files':len(inventory),
                'hit_counts':{},'reviewed_public_test_fixtures':audit.get('reviewed_public_test_fixtures',[]),
                'exclusions':['all .env files and key mounts','workspace-wide copies','existing archives','raw bodies'],
                'limits':'pattern scan plus explicit source whitelist, not a universal secret-absence proof'})
            checks=[]
            for path in sorted(bundle.rglob('*')):
                if path.is_symlink():raise RuntimeError('unexpected bundle symlink')
                if path.is_file():checks.append(digest(path)+'  '+path.relative_to(bundle).as_posix()+'\n')
            (bundle/'checksums.sha256').write_text(''.join(checks),encoding='utf-8')
            audit['bundle_readonly']=readonly(bundle);audit['source_files']=len(records);audit['raw_inventory_files']=len(inventory)
            if not args.no_tar:
                archive=target/'source_bundle.tar.gz'
                with tarfile.open(archive,'x:gz',format=tarfile.PAX_FORMAT) as stream:
                    for path in [bundle,*sorted(bundle.rglob('*'))]:
                        if path.is_symlink():raise RuntimeError('unexpected archive symlink')
                        stream.add(path,arcname=path.relative_to(target).as_posix(),recursive=False)
                audit['archive']={'path':archive.name,'bytes':archive.stat().st_size,'sha256':digest(archive),'raw_bytes_included':0}
                (target/'source_bundle.tar.gz.sha256').write_text(audit['archive']['sha256']+'  '+archive.name+'\n')
            if archive_raw_files(out)!=original_raw:raise RuntimeError('raw inventory changed after seal/package creation')
            for relative,item in inventory.items():
                if fingerprint(out/relative)!=item['fingerprint']:raise RuntimeError('raw changed after seal/package creation: '+relative)
            if selected_inputs(out,runtime,drivers,tests)!=selected:raise RuntimeError('public input inventory changed after packaging')
            for relative,path in selected.items():
                if digest(path)!=records[relative]['sha256']:raise RuntimeError('source/public input changed after packaging: '+relative)
            unsupported=audit['bundle_readonly']['status']=='UNSUPPORTED' or args.seal_raw and seal['status']=='UNSUPPORTED'
            audit.update(status='SOURCE_BUNDLE_CREATED_READONLY_UNSUPPORTED' if unsupported else 'SOURCE_BUNDLE_CREATED_REQUIRES_RAW_ARCHIVE_AND_FINAL_QA',
                finished_utc=utc(),source_and_raw_bytes_unchanged=True,paid_api_calls=0,goal_completion='NOT_CERTIFIED')
            save()
            # Detailed bundle/raw permission results are already persisted. Seal
            # the outer diagnostic container last; return its actual verification
            # through stdout because the audit file itself is now read-only.
            final_permissions=readonly(target)
            return audit,final_permissions
    except BaseException as error:
        audit.update(status='ERROR_RETAINED_NO_SOURCE_DELETION',finished_utc=utc(),
            error_type=type(error).__name__,error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
        save();raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    parser.add_argument('--runtime-root',type=Path,default=Path('/opt/lingxi/formal-checkout'))
    parser.add_argument('--drivers-root',type=Path,default=Path('/drivers'))
    parser.add_argument('--tests-root',type=Path,default=Path('/opt/lingxi/tests'))
    parser.add_argument('--verification-dir',type=Path)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--seal-raw',action='store_true',help='Only closed original raw; verify actual mode changes and report UNSUPPORTED honestly.')
    parser.add_argument('--no-tar',action='store_true',help='Keep the new expanded source bundle without creating a tar archive.')
    args=parser.parse_args()
    receipt=args.destination.resolve().with_name(args.destination.name+'.readonly_verification.json')
    if receipt.exists():raise RuntimeError('outer permission receipt already exists; choose a new package version')
    audit,permissions=package(args)
    with receipt.open('x',encoding='utf-8') as stream:
        json.dump({'package_directory':str(args.destination.resolve()),'verified_utc':utc(),
            'package_audit_sha256':digest(args.destination/'source_package_audit.json'),
            'outer_package_readonly':permissions},stream,ensure_ascii=False,indent=2)
    receipt.chmod(0o444)
    status='SOURCE_BUNDLE_CREATED_READONLY_UNSUPPORTED' if permissions['status']=='UNSUPPORTED' else audit['status']
    print(json.dumps({'status':status,'output':str(args.destination),'paid_api_calls':0,
        'outer_package_readonly_status':permissions['status'],'permission_paths_checked':len(permissions['paths']),
        'outer_permission_receipt':str(receipt),'goal_completion':'NOT_CERTIFIED'},ensure_ascii=False),flush=True)
    raise SystemExit(2 if permissions['status']=='UNSUPPORTED' or 'UNSUPPORTED' in audit['status'] else 0)


if __name__=='__main__':main()
