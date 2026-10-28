"""Bind format-validation scope to all selected and failed-state raw roots.

Preparation reads only source and identity metadata. It does not validate raw.
"""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


out = Path('paper_experiments_20261019_pilot/formal_20261019')
source = Path('deployment/formal_schema_validation.py')
target = out / 'analysis/schema_validation_contract_v3.json'
receipt = out / 'analysis/schema_validation_preparation_v3.json'
if target.exists() or receipt.exists():
    raise RuntimeError('new scope-contract outputs required')
previous = out / 'analysis/schema_validation_contract_v2.json'
if sha(previous) != '8667bb32e75dc8836b1a3a6335a23a16c362fe567185bbee6554c776f47cff91':
    raise RuntimeError('prior contract identity changed')
rules = json.loads(previous.read_text(encoding='utf-8'))
ast.parse(source.read_text(encoding='utf-8'))
registration = out / 'analysis/state_measurement_correction_v3_registration.json'
failed_registration = out / 'analysis/state_measurement_correction_v2_registration.json'
if sha(registration) != 'fe077ee74b936db979afe0a4e9c1212bee845eb8e617a945e278583834082e53' or \
        sha(failed_registration) != '86bba62d5b05760493f9e33458c144e578e589633aaefff4299e8b778809974f':
    raise RuntimeError('prospective state registrations changed')
authority = out / rules['required_field_contract']
if sha(authority) != rules['required_field_contract_sha256']:
    raise RuntimeError('115-field authority changed')
environment = out / 'analysis/schema_environment_v1/manifest.json'
if json.loads(environment.read_text())['status'] != 'PASS':
    raise RuntimeError('analysis-only dependency installation is not closed')
rules.update(schema_version=3, status='PREPARED_NOT_EXECUTED',
             created_utc=datetime.now(timezone.utc).isoformat(), implementation_sha256=sha(source),
             previous_contract={'path': str(previous.relative_to(out)), 'sha256': sha(previous)},
             validation_performed='Source AST and metadata SHA only; no raw/schema/readback audit executed by preparation',
             preparation_helper_sha256=sha(Path(__file__)),
             registration_timing='Postrun format-validation scope revision, not a pre-experiment freeze; state schemas are separately prospectively registered')
rules['state_scope_selection'] = {
    'registration': str(registration.relative_to(out)), 'registration_sha256': sha(registration),
    'relative_output_root': 'state_measurement_correction_v3',
    'all_raw_roots': ['raw', 'retrieval_input_correction_v2/raw', 'state_measurement_correction_v3/raw', 'state_measurement_correction_v2/raw'],
    'archived_state_roots': [{
        'registration': str(failed_registration.relative_to(out)),
        'registration_sha256': sha(failed_registration),
        'relative_output_root': 'state_measurement_correction_v2',
        'required_closed_status': 'FAILED_RETAINED_NO_RETRY',
        'policy': 'Retain all failed-run records as observed auxiliary schemas; no complete-matrix inference'}]}
rules['validation_scope'].append('Selected state-v3 rows validate against the exact prospective producer/child/resource schemas. Closed failed state-v2 raw is retained and observed-shape validated independently; four raw roots have separate before/after inventories.')
rules['dependency_plan'].update(status='ACTUAL_ANALYSIS_ONLY_INSTALLATION_MANIFEST_PASS',
                                actual_manifest=str(environment.relative_to(out)), actual_manifest_sha256=sha(environment))
rules['schema_validation_executed'] = False
with target.open('x', encoding='utf-8') as stream:
    json.dump(rules, stream, ensure_ascii=False, indent=2)
shutil.copyfile(source, out / 'drivers/formal_schema_validation.py')
evidence = {'status': 'PREPARED_NOT_EXECUTED', 'created_utc': datetime.now(timezone.utc).isoformat(),
            'helper_sha256': sha(source), 'contract_sha256': sha(target),
            'source_preimage': 'manifests/preparation_versions/schema_validation_before_state_scope_v3',
            'old_contract_and_115_authority_unchanged': sha(previous) == '8667bb32e75dc8836b1a3a6335a23a16c362fe567185bbee6554c776f47cff91' and sha(authority) == rules['required_field_contract_sha256'],
            'raw_read': False, 'audit_executed': False, 'formal_environment_modified': False,
            'scope': rules['state_scope_selection']}
with receipt.open('x', encoding='utf-8') as stream:
    json.dump(evidence, stream, ensure_ascii=False, indent=2)
print(json.dumps({'status': evidence['status'], 'helper_sha256': sha(source), 'contract_sha256': sha(target)}))
