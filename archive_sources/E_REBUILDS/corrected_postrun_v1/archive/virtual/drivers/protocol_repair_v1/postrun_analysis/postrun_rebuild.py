"""Archive, postprocess and independently rebuild the corrected study offline.

Source preparation only until root executes after genuine real/retrieval timing
closure. Scientific sources/freeze/registration/binding bytes are never adapted.
Fresh containers bind independent archive copies at their ORIGINAL virtual paths.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import uuid

STUDY = 'protocol_repair_20261020_v1'
VSTUDY = '/evidence/' + STUDY
FREEZE_SHA = '2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e'
PRIMARY_SHA = 'd87bb161630560f4fe7c62e061a167e36ba5d6c93a9c7a5dbd2e4d12c5240251'
RETRIEVAL_SHA = 'e73e5633cac1ba3428e0d2b3d145fcff4f94417ea0eb00fb72a29d4a06103524'
RETRIEVAL_BINDING_SHA = 'ef70689318eeaecd7beed4bf6c5c84dc18df206e243fc430f461f07c48c102d3'
CAPACITY_SHA = 'e7acca9bd698b55215690e13f5dcf3fcf04777d513397ea090422e94e37299a8'
FORMAT_SHA = '9d6ccc5ae632bc5a803d18e4a21c6671b7c41bdc754c52882c219f82701ba229'
FORMAT_TABLES = ('task_runs', 'provider_calls', 'messages', 'states', 'memory_events', 'resource_samples')
HELPER_ROOT = '/drivers/protocol_repair_v1/postrun_analysis/'
HELPERS = ('full_metric_audit.py', 'secondary_heterogeneity.py', 'retrieval_postrun_analysis_v1.py',
    'original_reference_descriptives.py', 'external_reference_equivalence.py', 'saved_report_adapter.py',
    'pure_report_plots.py', 'offline_analysis_runner.py', 'postrun_rebuild.py', 'protocol_format_export.py',
    'retrieval_capacity_analysis_v1.py')
VERSIONS = {'primary': 'formal_primary_v1', 'metric': 'full_metric_audit_v3',
    'secondary': 'secondary_heterogeneity_v1', 'retrieval': 'retrieval_postrun_analysis_v1',
    'reference': 'original_reference_descriptives_v1', 'equivalence': 'external_reference_equivalence_v1',
    'report': 'report_adapter_v1', 'format': 'protocol_format_v1', 'capacity': 'retrieval_capacity_analysis_v1'}
SAFE_ENV = {'PATH', 'HOME', 'LANG', 'LANGUAGE', 'TZ', 'TERM', 'VIRTUAL_ENV', 'LD_LIBRARY_PATH',
    'PYTHONUNBUFFERED', 'PYTHONDONTWRITEBYTECODE', 'PYTHONHASHSEED', 'DEBIAN_FRONTEND'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def read(path):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def virtual(value):
    path = PurePosixPath(str(value))
    if not path.is_absolute() or '..' in path.parts or '\\' in str(value):
        raise ValueError('canonical virtual path must be absolute POSIX without traversal')
    return path.as_posix()


def at(root, name):
    return root / Path(*PurePosixPath(virtual(name)).parts[1:])


def safe_file(path):
    forbidden = {'.env', 'model.env', 'auth.json', 'credentials', 'credentials.json', 'id_rsa', 'id_ed25519'}
    if path.is_symlink() or not path.is_file() or any(part.lower() in forbidden or part.lower().startswith('.env.') for part in path.parts):
        raise ValueError('only explicit regular non-secret files may be archived')


def copy_stable(source, target, expected=None):
    safe_file(source); before = source.stat(); value = sha(source)
    if expected is not None and value != expected:
        raise ValueError('source differs from its approved/frozen SHA: ' + source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha(target) != value:
            raise ValueError('two virtual inputs conflict')
    else:
        shutil.copyfile(source, target)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or sha(target) != value:
        raise RuntimeError('input changed during independent archive copy')
    return {'sha256': value, 'bytes': before.st_size}


def resolve_source(job, name):
    name = virtual(name)
    mappings = sorted(job['path_mappings'], key=lambda row: len(row['virtual_prefix']), reverse=True)
    for item in mappings:
        prefix = virtual(item['virtual_prefix']).rstrip('/')
        if name == prefix or name.startswith(prefix + '/'):
            root = Path(item['host_prefix']).resolve()
            candidate = (root / Path(*PurePosixPath(name[len(prefix):].lstrip('/')).parts)).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError('virtual source mapping escaped its host root')
            return candidate
    raise ValueError('no explicit source mapping for frozen path: ' + name)


def closed_gate(study, quiet_relative):
    # Small lifecycle records only, before any raw body is opened/copied/hashed.
    execution = read(study / 'formal_execution_v1/execution.json')
    stages = execution.get('stages') or []
    if (execution.get('status') != 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT' or
        not execution.get('finished_utc') or execution.get('supervised_child_observed_running') is True or
        [row.get('name') for row in stages] != ['main', 'native_remaining', 'external'] or
        any(row.get('status') != 'TERMINAL' or row.get('exit_code') != 0 or not row.get('finished_utc') for row in stages)):
        raise RuntimeError('original actual real lifecycle is not demonstrably closed')
    quiet_path = (study / quiet_relative).resolve()
    if not quiet_path.is_relative_to(study / 'analysis'):
        raise ValueError('actual root quiescence receipt must be in the study analysis tree')
    quiet = read(quiet_path)
    if (quiet.get('study_id') != STUDY or quiet.get('status') != 'ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING' or
        quiet.get('active_real_timing_processes') != [] or quiet.get('active_retrieval_timing_processes') != [] or
        datetime.fromisoformat(quiet['verified_utc'].replace('Z', '+00:00')) <
        datetime.fromisoformat(execution['finished_utc'].replace('Z', '+00:00'))):
        raise RuntimeError('quiescence is not an actual post-termination observation')
    retrieval_process_gate(study, quiet)
    return execution


def retrieval_process_gate(study, quiet):
    if (quiet.get('all_container_processes_except_init_sleep_and_probe') != [] or
        quiet.get('retrieval_controller_session_id') != 82436 or
        type(quiet.get('retrieval_controller_exec_observed_terminal_exit_code')) is not int or
        quiet['retrieval_controller_exec_observed_terminal_exit_code'] != 0):
        raise RuntimeError('complete actual post-retrieval process enumeration and controller exit0 required')
    processes = read(study / 'analysis/retrieval_matrix_process_status.json')
    expected = {(size, build) for size in (100, 1000, 5000, 20000) for build in (1, 2, 3)}
    if not isinstance(processes, list) or len(processes) != 12 or {(row.get('size'), row.get('build')) for row in processes} != expected:
        raise RuntimeError('retrieval has no unique final12-worker process receipt')
    checks = quiet.get('timeout_worker_termination_checks')
    if not isinstance(checks, list):
        raise RuntimeError('explicit timeout-worker termination observations required, including an empty list')
    indexed = {}
    for check in checks:
        key = (check.get('size'), check.get('build'))
        if key not in expected or key in indexed:
            raise RuntimeError('timeout termination observation identity is duplicated/unexpected')
        indexed[key] = check
    timeouts = set()
    for row in processes:
        key = (row['size'], row['build'])
        path = study / f'analysis/retrieval_n{key[0]}_build{key[1]}_process.json'
        if canonical(read(path)) != canonical(row):
            raise RuntimeError('aggregate and per-worker process receipt differ')
        if row.get('timed_out') is False and type(row.get('returncode')) is int and row['returncode'] == 0:
            continue
        wall = row.get('wall_sec')
        if not (row.get('timed_out') is True and row.get('returncode') is None and
                type(wall) in (int, float) and math.isfinite(wall) and wall >= 1200):
            raise RuntimeError('only the original1200sec deadline with actual timeout may be capacity-limited')
        check = indexed.get(key) or {}
        if (check.get('process_receipt_sha256') != sha(path) or
            check.get('native_worker_processes_observed_running') != [] or
            check.get('verification') != 'actual complete psutil container process enumeration after terminal controller'):
            raise RuntimeError('timed-out native worker has not been actually observed terminated')
        timeouts.add(key)
    if set(indexed) != timeouts:
        raise RuntimeError('timeout termination observations must exactly cover the actual timeout workers')
    return processes


def collect_inputs(job):
    study = Path(job['study_out']).resolve()
    if study.name != STUDY or resolve_source(job, VSTUDY) != study:
        raise ValueError('exact new-study host/virtual mapping required')
    closed_gate(study, job['quiescence_relative'])
    freeze_path = study / 'manifests/formal-freeze.json'
    if sha(freeze_path) != FREEZE_SHA:
        raise ValueError('original formal freeze bytes changed')
    freeze = read(freeze_path); files = {}

    def add(name, expected=None, source=None):
        name = virtual(name); path = Path(source) if source else resolve_source(job, name)
        safe_file(path)
        if name in files and files[name]['source'] != path:
            raise ValueError('conflicting input mapping')
        if name in files:
            prior = files[name]['expected']
            if prior is not None and expected is not None and prior != expected:
                raise ValueError('conflicting frozen/approved input SHA')
            expected = prior if prior is not None else expected
        files[name] = {'source': path, 'expected': expected}

    for name, expected in freeze['files'].items():
        add(name, expected)
    add(VSTUDY + '/manifests/formal-freeze.json', FREEZE_SHA)
    for name in ('formal_execution_v1/execution.json', job['quiescence_relative'],
        'manifests/retrieval_postrun_analysis_v1_binding.json', 'analysis/retrieval_matrix_plan.json',
        'analysis/retrieval_matrix_process_status.json',
        'analysis/operational_observations/host_power_events_v1.json',
        'analysis/operational_observations/host_power_events_v2.json',
        'analysis/operational_observations/clock_observation_20261020_0549.json',
        'analysis/operational_observations/quota_event_v1.json',
        'analysis/operational_observations/audit_v1_index_stop_receipt.json',
        'analysis/retrieval_driver_preoutcome_correction_v1/correction_receipt.json'):
        add(VSTUDY + '/' + name)
    binding = read(study / 'manifests/retrieval_postrun_analysis_v1_binding.json')
    if (sha(study / 'manifests/retrieval_postrun_analysis_v1_binding.json') != RETRIEVAL_BINDING_SHA or
        binding.get('analyzer_sha256') != RETRIEVAL_SHA or binding.get('analysis_version') != job['versions']['retrieval']):
        raise ValueError('reuse the exact original pre-outcome retrieval binding/version; never rebind')
    for path in (study / 'raw').iterdir():
        if not path.is_file() or path.suffix != '.jsonl':
            raise ValueError('unexpected non-JSONL/indirect raw artifact')
        add(VSTUDY + '/raw/' + path.name)
    for size in (100, 1000, 5000, 20000):
        for build in (1, 2, 3):
            for suffix in ('build', 'process', 'status'):
                relative = f'analysis/retrieval_n{size}_build{build}_{suffix}.json'
                if suffix in ('build', 'status') and not (study / relative).exists():
                    process = read(study / f'analysis/retrieval_n{size}_build{build}_process.json')
                    if process.get('timed_out') is True and process.get('returncode') is None:
                        continue  # Recorded absence is inventoried; never synthesize a terminal.
                add(VSTUDY + '/' + relative)
    approved = job['approved_offline_source_sha256']
    for name in HELPERS:
        key = HELPER_ROOT + name
        if key not in approved:
            raise ValueError('postrun entry needs explicit source approval: ' + name)
        add(key, approved[key])
    add('/drivers/formal_report.py', 'fed72d9777f6eaf8c630e19eb33f182e3f8acd01423d48dc20a39c9c4c460dc3')
    if approved[HELPER_ROOT + 'retrieval_postrun_analysis_v1.py'] != RETRIEVAL_SHA:
        raise ValueError('registered retrieval analyzer identity changed')
    if approved[HELPER_ROOT + 'protocol_format_export.py'] != FORMAT_SHA:
        raise ValueError('reviewed lossless-format exporter identity changed')
    if approved[HELPER_ROOT + 'retrieval_capacity_analysis_v1.py'] != CAPACITY_SHA:
        raise ValueError('reviewed capacity supplement identity changed')
    runtime = virtual(job['runtime_virtual_root']); runtime_host = resolve_source(job, runtime)
    add(runtime + '/pyproject.toml')
    for folder in ('src', 'scenarios', 'scripts'):
        for path in (runtime_host / folder).rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.sh', '.json', '.npz'}:
                add(runtime + '/' + path.relative_to(runtime_host).as_posix())
    # Only dedicated approved legacy assets and small opaque proof metadata.
    offline = read(job['offline_manifest'])
    if offline.get('status') != 'APPROVED_OFFLINE_BYTE_ASSET_MANIFEST':
        raise ValueError('prepared offline metadata is not an approved quiet-window verification')
    path_map = read(job['offline_path_map'])
    if path_map.get('schema_version') != 'explicit-offline-path-map-v1':
        raise ValueError('explicit offline path map required')
    for batch in offline['batches']:
        if batch.get('evidence_class') != 'OFFLINE_MECHANISM_BATCH':
            raise ValueError('legacy batch is not offline-only')
        for item in batch['assets'] + batch.get('independent_rebuild_evidence', []):
            root = batch['source_root'] if 'source_root' not in item else item['source_root']
            recorded = root.rstrip('/\\') + '/' + item['relative_path'].replace('\\', '/')
            matches = [m for m in path_map['mappings'] if recorded.casefold().startswith(m['recorded_prefix'].rstrip('/\\').replace('\\', '/').casefold() + '/')]
            if not matches:
                raise ValueError('every offline artifact/proof needs explicit recorded-to-canonical mapping')
            match = max(matches, key=lambda row: len(row['recorded_prefix']))
            prefix = match['recorded_prefix'].rstrip('/\\').replace('\\', '/')
            target = virtual(match['actual_prefix'].rstrip('/') + '/' + recorded[len(prefix):].lstrip('/'))
            if '/raw/' in target or any(token in item['relative_path'].lower() for token in ('task_runs', 'provider_calls', 'table_06.csv', 'retrieval')):
                raise ValueError('old real/retrieval/raw are excluded from archive metrics')
            source = Path(root) / Path(*PurePosixPath(item['relative_path']).parts)
            add(target, item['sha256'], source=source)
    return files


def verify_controller(job, manifest=None):
    name = HELPER_ROOT + 'postrun_rebuild.py'
    wanted = job['approved_offline_source_sha256'].get(name)
    actual = sha(Path(__file__).resolve())
    if actual != wanted or (manifest is not None and manifest['virtual_inputs'][name]['sha256'] != actual):
        raise ValueError('executing host controller differs from explicitly approved/archive source')
    return actual


def command(argv, log=None, stdout_file=None):
    result = subprocess.run(argv, stdout=stdout_file or subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if log:
        write(log, {'command': argv, 'exit_code': result.returncode,
                    'stdout': None if stdout_file else result.stdout.decode('utf-8', errors='replace'),
                    'stderr': result.stderr.decode('utf-8', errors='replace')})
    if result.returncode:
        raise RuntimeError('local offline tool failed; inspect retained command record')
    return '' if stdout_file else result.stdout.decode('utf-8', errors='strict').strip()


def archive_inputs(job, archive, logs, configuration_source):
    verify_controller(job)
    files = collect_inputs(job)
    absent = {}
    study = Path(job['study_out']).resolve()
    for process in read(study / 'analysis/retrieval_matrix_process_status.json'):
        if process.get('timed_out') is True:
            stem = f"retrieval_n{process['size']}_build{process['build']}"
            for relative in (f'analysis/{stem}_build.json', f'analysis/{stem}_status.json',
                             f'raw/{stem}.jsonl', f'raw/{stem}_pool.jsonl', f'raw/{stem}_feedback.jsonl'):
                if not (study / relative).exists():
                    absent[VSTUDY + '/' + relative] = {'original_host_path': str(study / relative),
                        'status': 'NOT_RECORDED_NOT_SYNTHESIZED', 'size': process['size'], 'build': process['build']}
    dependency = job['dependency']; image = dependency['image_id']
    if re.fullmatch(r'sha256:[0-9a-f]{64}', image) is None:
        raise ValueError('dependency image must be a local immutable image ID, never a tag/pull')
    attestation = read(dependency['attestation'])
    if (attestation.get('schema_version') != 'sanitized-local-analysis-dependency-v1' or
        attestation.get('image_id') != image or attestation.get('secret_free') is not True or
        attestation.get('no_secret_mounts') is not True):
        raise ValueError('dependency snapshot needs actual root-reviewed secret-free provenance')
    image_id = command(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])
    if image_id != image:
        raise ValueError('local dependency image identity differs')
    # Ask Docker only for variable NAMES; credential values are never retrieved.
    names = command(['docker', 'image', 'inspect', '--format', '{{range .Config.Env}}{{println (index (split . "=") 0)}}{{end}}', image]).splitlines()
    if any(name not in SAFE_ENV and not name.startswith('LC_') for name in names):
        raise ValueError('analysis image ENV is not a credential-excluding allowlist; supply a sanitized local snapshot')
    if dependency.get('venv_archives'):
        raise ValueError('the approved local analysis image must contain its exact venv; an unarchived overlay is not supported')
    image_bytes = int(command(['docker', 'image', 'inspect', '--format', '{{.Size}}', image]))
    required = 6 * sum(row['source'].stat().st_size for row in files.values()) + 3 * image_bytes + 3 * 1024**3
    if shutil.disk_usage(archive.parent).free < required:
        raise RuntimeError('insufficient space for independent copies and outputs')
    inventory = {}
    for name, row in sorted(files.items()):
        record = copy_stable(row['source'], at(archive / 'virtual', name), row['expected'])
        inventory[name] = {**record, 'original_host_path': str(row['source'])}
    controls = archive / 'control'; controls.mkdir(parents=True)
    original_controls = {}
    for source, name in ((job['offline_manifest'], 'approved_offline_manifest.json'),
                         (job['offline_path_map'], 'offline_path_map.json'),
                         (dependency['attestation'], 'dependency_attestation.json'),
                         (str(configuration_source), 'original_job_bytes.json')):
        record = copy_stable(Path(source), controls / name)
        original_controls[name] = {**record, 'original_host_path': str(Path(source).resolve())}
    write(controls / 'job.json', job)
    dep = archive / 'dependencies'; dep.mkdir()
    command(['docker', 'image', 'save', '--output', str(dep / 'image.tar'), image], logs / 'dependency_image_save.json')
    input_manifest = {'status': 'INDEPENDENT_ARCHIVE_INPUTS_COMPLETE', 'created_utc': utc(),
        'virtual_inputs': inventory, 'absent_virtual_inputs': absent,
        'formal_freeze_sha256': FREEZE_SHA, 'dependency_image_id': image,
        'control_sha256': {path.name: sha(path) for path in controls.iterdir()},
        'original_controls': original_controls,
        'dependency_image_archive_sha256': sha(dep / 'image.tar'),
        'dependency_attestation_sha256': sha(controls / 'dependency_attestation.json'),
        'offline_manifest_sha256': sha(controls / 'approved_offline_manifest.json'),
        'offline_path_map_sha256': sha(controls / 'offline_path_map.json'),
        'input_copy_mode': 'regular independent copies, no hardlinks; original source paths never mounted into child containers',
        'raw_read_policy': 'new-study JSONL copied/hash-bound opaque; no old real/retrieval/raw admitted'}
    write(archive / 'input_manifest.json', input_manifest)
    return input_manifest


def stable_archive(archive, manifest):
    for name, row in manifest['virtual_inputs'].items():
        path = at(archive / 'virtual', name)
        if path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
            raise RuntimeError('archive input changed')
    if sha(archive / 'dependencies/image.tar') != manifest['dependency_image_archive_sha256']:
        raise RuntimeError('dependency archive changed')
    for name, wanted in manifest['control_sha256'].items():
        if sha(archive / 'control' / name) != wanted:
            raise RuntimeError('archived control input changed')
    for name in manifest.get('absent_virtual_inputs', {}):
        if at(archive / 'virtual', name).exists():
            raise RuntimeError('originally absent capacity input was synthesized in archive')


def stable_view(view, manifest):
    for name, row in manifest['virtual_inputs'].items():
        path = at(view, name)
        if path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
            raise RuntimeError('fresh-view immutable input changed')
    for name in manifest.get('absent_virtual_inputs', {}):
        if at(view, name).exists():
            raise RuntimeError('originally absent capacity input was synthesized in fresh view')


def stage_plan(job):
    versions = job['versions']; quiet = VSTUDY + '/' + job['quiescence_relative']
    metric = 'analysis/' + versions['metric']; secondary = 'analysis/postrun/' + versions['secondary']
    retrieval = 'analysis/' + versions['retrieval']; reference = 'analysis/postrun/' + versions['reference']
    primary = 'analysis/stats/' + versions['primary'] + '.json'
    def helper(name):
        return HELPER_ROOT + name
    return [
        ('metric', helper('full_metric_audit.py'), ['--study-out', VSTUDY, '--runtime-root', job['runtime_virtual_root'],
            '--quiescence-receipt', quiet, '--version', versions['metric']], metric + '/metric_audit.json'),
        ('primary', VSTUDY + '/formal_snapshot_v1/drivers/statistical_gates.py', ['formal', '--version', versions['primary']], primary),
        ('secondary', helper('secondary_heterogeneity.py'), ['--version', versions['secondary']], secondary + '/receipt.json'),
        ('retrieval', helper('retrieval_postrun_analysis_v1.py'), ['analyze', '--study-out', VSTUDY, '--version', versions['retrieval']], retrieval + '/audit.json'),
        ('capacity', helper('retrieval_capacity_analysis_v1.py'), ['--study-out', VSTUDY,
            '--quiescence-receipt', quiet, '--original-analyzer-source', helper('retrieval_postrun_analysis_v1.py'),
            '--version', versions['capacity']], 'analysis/' + versions['capacity'] + '/audit.json'),
        ('reference', helper('original_reference_descriptives.py'), ['--study-out', VSTUDY, '--quiescence-receipt', quiet,
            '--metric-audit-dir', metric, '--primary', primary, '--secondary-dir', secondary,
            '--method-source', '/drivers/formal_report.py', '--version', versions['reference']], reference + '/receipt.json'),
        ('equivalence', helper('external_reference_equivalence.py'), ['--study-out', VSTUDY, '--full-audit-dir', VSTUDY + '/' + metric,
            '--full-audit-sha256', '@ACTUAL_FULL_AUDIT_SHA@', '--quiescence-receipt', quiet,
            '--version', versions['equivalence']], 'analysis/' + versions['equivalence'] + '/audit.json'),
        ('report', helper('saved_report_adapter.py'), ['--study-out', VSTUDY, '--primary', primary,
            '--secondary-dir', secondary, '--metric-audit-dir', metric, '--retrieval-dir', retrieval, '--reference-dir', reference,
            '--retrieval-capacity-dir', 'analysis/' + versions['capacity'],
            '--external-equivalence', 'analysis/' + versions['equivalence'] + '/audit.json',
            '--offline-manifest', '/repro/control/approved_offline_manifest.json',
            '--offline-path-map', '/repro/control/offline_path_map.json', '--version', versions['report'], '--render'],
            versions['report'] + '/analysis/report_availability.json'),
        ('format', helper('protocol_format_export.py'), ['--study-out', VSTUDY,
            '--quiescence-receipt', quiet, '--version', versions['format']],
            'exports/' + versions['format'] + '/export_audit.json')]


def format_gate(record):
    if (record.get('status') != 'FORMAT_ROW_TYPE_PRESENCE_PASS' or record.get('study_id') != STUDY or
        record.get('exporter_source_sha256') != FORMAT_SHA):
        return False
    tables = record.get('exports')
    if (not isinstance(tables, list) or len(tables) != 6 or
        any(not isinstance(row, dict) for row in tables) or
        {row.get('table') for row in tables} != set(FORMAT_TABLES)):
        return False
    for row in tables:
        decoded = row.get('readback')
        if (row.get('status') != 'PASS' or type(row.get('rows')) is not int or row['rows'] <= 0 or
            not isinstance(decoded, dict) or decoded.get('status') != 'PASS' or
            type(decoded.get('rows')) is not int or decoded['rows'] != row['rows']):
            return False
        hashes = [decoded.get(key) for key in
                  ('source_decoded_sha256', 'csv_reconstructed_sha256', 'parquet_reconstructed_sha256')]
        if any(not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None for value in hashes):
            return False
        if len(set(hashes)) != 1 or any(not isinstance(row.get(key), str) or
            re.fullmatch(r'[0-9a-f]{64}', row[key]) is None for key in ('raw_sha256', 'csv_sha256', 'parquet_sha256')):
            return False
    task = next(row for row in tables if row['table'] == 'task_runs')
    closure = record.get('closure_gate') or {}
    return (task['rows'] == 6504 and closure.get('planned_ids') == 6504 and
        closure.get('terminal_ids') == 6504 and closure.get('formal_freeze_sha256') == FREEZE_SHA and
        closure.get('task_runs_raw_sha256') == task['raw_sha256'])


def provisional_capacity_failure(study, job, record):
    # This permits ONLY the immediately following independent reconciliation.
    # It never converts the frozen analyzer's FAIL into scientific PASS.
    closed_gate(study, job['quiescence_relative'])
    processes = read(study / 'analysis/retrieval_matrix_process_status.json')
    timeouts = {(row['size'], row['build']) for row in processes if row.get('timed_out') is True}
    if (not timeouts or record.get('study_id') != STUDY or
        record.get('status') != 'FAIL_RETAINED_NO_INFERENTIAL_TABLES' or
        record.get('analyzer_sha256') != RETRIEVAL_SHA or
        record.get('planned_query_units') != 14400 or record.get('planned_cells') != 60):
        return False
    findings = record.get('findings')
    if not isinstance(findings, list) or not findings:
        return False
    local_codes = {'RETRIEVAL_WORKER_FAILED_OR_TIMED_OUT', 'REGISTERED_QUERY_OR_MODE_ORDER_CHANGED',
                   'WORKER_TERMINAL_COUNTS_CHANGED', 'WORKER_FEEDBACK_EVENT_COUNT_MISMATCH',
                   'ACTUAL_POOL_BUILD_INCOMPLETE', 'ACTUAL_POOL_COPY_CHANGED',
                   'FEEDBACK_AUTHORED_DISTRACTOR_COVERAGE_INCOMPLETE'}
    global_codes = {'FULL14400_MATRIX_INCOMPLETE', 'FIVE_METHOD_QUERY_PAIRING_INCOMPLETE'}
    absent_candidates = {relative for size, build in timeouts for relative in (
        f'analysis/retrieval_n{size}_build{build}_build.json', f'analysis/retrieval_n{size}_build{build}_status.json',
        f'raw/retrieval_n{size}_build{build}.jsonl', f'raw/retrieval_n{size}_build{build}_pool.jsonl',
        f'raw/retrieval_n{size}_build{build}_feedback.jsonl') if not (study / relative).exists()}
    for finding in findings:
        code = finding.get('code')
        if code in local_codes and (finding.get('size'), finding.get('build')) in timeouts:
            stem = f"retrieval_n{finding['size']}_build{finding['build']}"
            if code in ('ACTUAL_POOL_BUILD_INCOMPLETE', 'ACTUAL_POOL_COPY_CHANGED') and f'analysis/{stem}_build.json' not in absent_candidates:
                return False
            if code in ('WORKER_TERMINAL_COUNTS_CHANGED', 'WORKER_FEEDBACK_EVENT_COUNT_MISMATCH') and f'analysis/{stem}_status.json' not in absent_candidates:
                return False
            continue
        if code in global_codes:
            continue
        if code == 'INPUT_MISSING' and finding.get('file') in absent_candidates:
            continue
        if (code == 'POOL100_FEEDBACK_NOT_EMPTY' and (100, finding.get('build')) in timeouts and
            f"raw/retrieval_n100_build{finding['build']}_feedback.jsonl" in absent_candidates):
            continue
        return False
    expected = {'independent_gold.json', 'process_and_build_evidence.json', 'cell_coverage.csv', 'query_observations.jsonl'}
    outputs = record.get('output_sha256') or {}
    if set(outputs) != expected:
        return False
    folder = study / 'analysis' / job['versions']['retrieval']
    return all((folder / name).is_file() and sha(folder / name) == wanted for name, wanted in outputs.items())


def capacity_gate(record):
    observed = record.get('observed_query_units'); missing = record.get('missing_query_units')
    timeouts = record.get('timeout_workers'); count = record.get('timeout_worker_count')
    if (record.get('study_id') != STUDY or record.get('planned_query_units') != 14400 or
        type(observed) is not int or type(missing) is not int or min(observed, missing) < 0 or observed + missing != 14400 or
        record.get('planned_cells') != 60 or record.get('summary_cells') != 20 or
        record.get('original_analyzer_sha256') != RETRIEVAL_SHA or
        record.get('original_binding_sha256') != RETRIEVAL_BINDING_SHA or
        record.get('noncapacity_findings') != [] or not record.get('method_ast_binding') or
        not isinstance(timeouts, list) or type(count) is not int or count != len(timeouts)):
        return False
    if count:
        return (record.get('status') == 'INCOMPLETE_CAPACITY_RECONCILED_NOT_ORIGINAL_AUDIT_PASS' and
                record.get('original_audit_status') == 'FAIL_RETAINED_NO_INFERENTIAL_TABLES')
    return (record.get('status') == 'COMPLETE_ORIGINAL_RETRIEVAL_PASSTHROUGH' and observed == 14400 and missing == 0 and
            record.get('original_audit_status') in ('PASS_COMPLETE_NEW_RETRIEVAL_MATRIX',
                                                  'COMPLETE_WITH_EXECUTION_FAILURES_AND_WITHHELD_METRICS'))


def capacity_files_gate(study, job, record):
    folder = study / 'analysis' / job['versions']['capacity']
    expected = {'retrieval_quality_scale_by_build.csv', 'retrieval_quality_scale.csv', 'cell_coverage.csv',
                'query_observations.jsonl', 'independent_gold.json', 'process_and_build_evidence.json', 'original_audit.json'}
    outputs = record.get('output_sha256') or {}
    original = study / 'analysis' / job['versions']['retrieval'] / 'audit.json'
    return (set(outputs) == expected and record.get('original_audit_sha256') == sha(original) and
        record.get('capacity_helper_sha256') == job['approved_offline_source_sha256'][HELPER_ROOT + 'retrieval_capacity_analysis_v1.py'] and
        record.get('bootstrap_seed') == 261019 and record.get('bootstrap_draws') == 10000 and
        record.get('new_hypothesis_tests') == 0 and record.get('new_holm_families') == 0 and
        all((folder / name).is_file() and sha(folder / name) == wanted for name, wanted in outputs.items()) and
        sha(folder / 'original_audit.json') == sha(original))


def semantic_gate(stage, record):
    if stage == 'metric':
        return record.get('status') in ('PASS_OBSERVED_FULL_RECORD_JOINS', 'INCONCLUSIVE_RETAINED') and record.get('terminal_units') == 6504 and not record.get('finding_counts', {}).get('FAIL', 0)
    if stage == 'primary':
        return record.get('collection_status') == 'COMPLETE' and record.get('primary_family_test_count') == 10 and record.get('active_confirmatory_families') == 1
    if stage == 'secondary':
        return record.get('status') == 'COMPLETE_DESCRIPTIVE_ANALYSIS'
    if stage == 'retrieval':
        return record.get('observed_query_units') == 14400 and not record.get('findings')
    if stage == 'capacity':
        return capacity_gate(record)
    if stage == 'reference':
        return record.get('status') == 'COMPLETE_SAVED_ORIGINAL_METHOD_DESCRIPTIVES' and record.get('absolute_quality_rows') == 30 and record.get('matched_reference_rows') == 9
    if stage == 'equivalence':
        return record.get('status') in ('COMPARABLE_SYSTEM_LEVEL_REFERENCE', 'NON_COMPARABLE') and record.get('matched_pairs_observed') == 240
    if stage == 'report':
        return record.get('status') == 'PREPARED_REQUIRES_POSTRUN_SCHEMA_NUMERIC_AND_ACTUAL_VISUAL_QA' and record.get('tables') == [1, 2, 3, 4, 5, 6]
    if stage == 'format':
        return format_gate(record)
    return False


def run_pipeline(archive, destination, job, manifest):
    if destination.exists():
        raise RuntimeError('fresh calculation destination already exists')
    destination.mkdir(parents=True)
    view = destination / 'virtual'; shutil.copytree(archive / 'virtual', view)
    study = at(view, VSTUDY)
    logs = destination / 'logs'; logs.mkdir()
    cache = destination / 'cache'; cache.mkdir()
    locks = destination / 'locks'; locks.mkdir()
    for name in ('formal_sequence_execution.lock', 'real_provider_execution.lock'):
        (locks / name).write_bytes(b'')
        (study / 'manifests' / name).touch(exist_ok=True)
    runtime = virtual(job['runtime_virtual_root'])
    if runtime != '/opt/lingxi/formal-checkout':
        raise ValueError('archive runtime source must use its frozen canonical leaf path')
    if any(not (name.startswith('/evidence/') or name.startswith('/drivers/') or
                name.startswith(runtime + '/')) for name in manifest['virtual_inputs']):
        raise ValueError('unrecognized input mount root; do not mask dependency/OS directories')
    stable_view(view, manifest)
    image = manifest['dependency_image_id']; python = job['dependency']['python']
    if not python.startswith('/'):
        raise ValueError('absolute Python path inside the archived local analysis image required')
    allowed = dict(job['approved_offline_source_sha256'])
    allowed[VSTUDY + '/formal_snapshot_v1/drivers/statistical_gates.py'] = PRIMARY_SHA
    results = []
    for stage, script, arguments, receipt in stage_plan(job):
        stage_dir = logs / stage; stage_dir.mkdir()
        if stage == 'equivalence':
            arguments = [sha(study / 'analysis' / job['versions']['metric'] / 'metric_audit.json') if arg == '@ACTUAL_FULL_AUDIT_SHA@' else arg for arg in arguments]
        config = {'script': script, 'script_sha256': allowed[script], 'arguments': arguments,
            'approved_offline_sources': allowed, 'protected_inputs': list(manifest['virtual_inputs']),
            'scratch_locks': [VSTUDY + '/manifests/' + name for name in ('formal_sequence_execution.lock', 'real_provider_execution.lock')],
            'writable_roots': [VSTUDY, '/repro/logs', '/repro/cache', '/tmp'],
            'guard_result': '/repro/logs/guard_result.json', 'guard_events': '/repro/logs/guard_events.jsonl'}
        write(stage_dir / 'configuration.json', config)
        name = 'corrected-offline-' + uuid.uuid4().hex[:18]
        argv = ['docker', 'run', '--rm', '--pull', 'never', '--name', name, '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--memory', '2g',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m', '--env', 'HOME=/repro/cache/home',
            '--env', 'MPLCONFIGDIR=/repro/cache/matplotlib', '--env', 'XDG_CACHE_HOME=/repro/cache',
            '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'MPLBACKEND=Agg', '--env', 'HF_HUB_OFFLINE=1', '--env', 'TRANSFORMERS_OFFLINE=1']
        for root in ('drivers', 'evidence'):
            argv += ['--mount', 'type=bind,src=' + str(view / root) + ',dst=/' + root + (',readonly' if root != 'evidence' else '')]
        # Bind only the business-source leaf. Mounting /opt would hide the
        # dependency image's separate /opt/lingxi/formal-venv executable.
        argv += ['--mount', 'type=bind,src=' + str(at(view, runtime)) + ',dst=' + runtime + ',readonly']
        for relative in ('raw', 'corpus', 'public_pilot_v1', 'formal_snapshot_v1'):
            argv += ['--mount', 'type=bind,src=' + str(study / relative) + ',dst=' + VSTUDY + '/' + relative + ',readonly']
        for lock in locks.iterdir():
            argv += ['--mount', 'type=bind,src=' + str(lock) + ',dst=' + VSTUDY + '/manifests/' + lock.name]
        argv += ['--mount', 'type=bind,src=' + str(archive / 'control') + ',dst=/repro/control,readonly',
            '--mount', 'type=bind,src=' + str(stage_dir) + ',dst=/repro/logs',
            '--mount', 'type=bind,src=' + str(cache) + ',dst=/repro/cache',
            '--entrypoint', python, image, '-I', '-B', HELPER_ROOT + 'offline_analysis_runner.py', '/repro/logs/configuration.json']
        started = utc(); process = None
        write(stage_dir / 'started.json', {'started_utc': started, 'container': name,
            'command': argv, 'status': 'OWNED_OFFLINE_CHILD_LAUNCH_INTENT', 'paid_or_benchmark_entry': False})
        try:
            with (stage_dir / 'stdout.log').open('x') as stdout, (stage_dir / 'stderr.log').open('x') as stderr:
                process = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
                write(stage_dir / 'launched.json', {'container': name, 'host_docker_cli_pid': process.pid})
                code = process.wait(timeout=job.get('step_timeout_seconds'))
        except BaseException:
            # Owned containers contain analysis only. Never address the live study
            # or a paid-run container; retain uncertain liveness, do not relaunch.
            live = None
            try:
                subprocess.run(['docker', 'stop', '--time', '5', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                probe = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', name], capture_output=True, timeout=10)
                if probe.returncode == 0 and probe.stdout.strip() in (b'true', b'false'):
                    live = probe.stdout.strip() == b'true'
                elif probe.returncode != 0 and b'no such object' in probe.stderr.lower():
                    live = False
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process is not None and process.poll() is None and live is False:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    live = None
            write(stage_dir / 'interruption.json', {'status': 'OWNED_ANALYSIS_CHILD_STOPPED' if live is False else 'CHILD_RUNNING_OR_LIVENESS_UNKNOWN_DO_NOT_RELAUNCH',
                'container': name, 'observed_running': live, 'original_paid_containers_addressed': False})
            raise
        guard = read(stage_dir / 'guard_result.json')
        actual_receipt = study / receipt
        result = {'stage': stage, 'started_utc': started, 'finished_utc': utc(), 'exit_code': code,
            'container': name, 'script': script, 'source_sha256': allowed[script], 'receipt_sha256': sha(actual_receipt),
            'guard_sha256': sha(stage_dir / 'guard_result.json'), 'status': 'FAILED_RETAINED'}
        record = read(actual_receipt)
        result['status'] = 'COMPLETED_WITH_RETAINED_SCOPE' if code in (0, 2) and guard.get('denied_count') == 0 and semantic_gate(stage, record) else 'FAILED_RETAINED'
        if stage == 'capacity' and code != 0:
            result['status'] = 'FAILED_RETAINED'
        if stage == 'retrieval' and code == 1 and guard.get('denied_count') == 0 and provisional_capacity_failure(study, job, record):
            result['status'] = 'ORIGINAL_FAIL_RETAINED_PENDING_STRICT_CAPACITY_RECONCILIATION_NOT_PASS'
            result['original_scientific_audit_status'] = record['status']
        if stage == 'capacity' and result['status'] == 'COMPLETED_WITH_RETAINED_SCOPE':
            if not capacity_files_gate(study, job, record):
                result['status'] = 'FAILED_RETAINED'
            result.update(capacity_analysis_status=record.get('status'),
                original_audit_status_retained=record.get('original_audit_status'),
                retrieval_matrix_execution_complete=record.get('planned_matrix_execution_complete'),
                missing_query_units=record.get('missing_query_units'))
        write(stage_dir / 'result.json', result); results.append(result)
        if result['status'] == 'FAILED_RETAINED':
            raise RuntimeError('actual postrun stage failed/denied/incomplete; retain outputs and choose a new run')
    stable_view(view, manifest)
    write(destination / 'execution.json', {'status': 'ACTUAL_OFFLINE_STAGES_COMPLETED_REQUIRES_NUMERIC_AND_VISUAL_QA',
        'retrieval_scope_status': next(row for row in results if row['stage'] == 'capacity')['capacity_analysis_status'],
        'original_retrieval_FAIL_never_promoted_to_PASS': True, 'steps': results,
        'finished_utc': utc(), 'api_or_query_collection_commands': 0})
    return study


def projected_json_hash(path, excluded=()):
    value = read(path)
    for pointer in excluded:
        node = value; parts = pointer.strip('/').split('/')
        for part in parts[:-1]:
            node = node.get(part, {}) if isinstance(node, dict) else {}
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def png_encoding(path):
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('invalid exported PNG')
    offset = 8; chunks = []
    while offset < len(data):
        size = struct.unpack('>I', data[offset:offset+4])[0]; kind = data[offset+4:offset+8]
        payload = data[offset+8:offset+8+size]
        if kind in (b'IHDR', b'PLTE', b'IDAT', b'tRNS', b'gAMA', b'cHRM', b'sRGB', b'iCCP', b'eXIf'):
            chunks.append(kind + payload)
        offset += size + 12
    return hashlib.sha256(b''.join(chunks)).hexdigest()


def format_export_manifest(study, version):
    relative = 'exports/' + version
    folder = study / relative
    audit_path = folder / 'export_audit.json'
    audit = read(audit_path)
    if not format_gate(audit):
        raise RuntimeError('lossless-format receipt failed six-table/6504/decoded identity gate')
    records = {}; identities = {}; excluded = {}
    audit_comparison = read(audit_path)
    for item in audit_comparison['exports']:
        item.pop('csv_sha256'); item.pop('parquet_sha256')
    audit_comparison['lifecycle_gate'].pop('quiescence_receipt', None)

    def add(path, comparison=None, rule=None):
        name = path.relative_to(study).as_posix()
        records[name] = {'sha256': sha(path), 'bytes': path.stat().st_size,
            'comparison_sha256': hashlib.sha256(canonical(comparison).encode()).hexdigest() if comparison is not None else sha(path)}
        if rule:
            records[name]['comparison'] = rule

    add(audit_path, audit_comparison, 'EXACT_TYPED_AUDIT_EXCLUDING_FORMAT_BYTE_HASHES_AND_QUIET_PATH_ONLY')
    excluded[relative + '/export_audit.json'] = [
        '/exports/*/csv_sha256', '/exports/*/parquet_sha256', '/lifecycle_gate/quiescence_receipt']
    by_name = {item['table']: item for item in audit['exports']}
    for name in FORMAT_TABLES:
        item = by_name[name]; decoded = item['readback']
        source = study / 'raw' / (name + '.jsonl')
        if sha(source) != item['raw_sha256']:
            raise RuntimeError('format export source raw SHA differs from the actual fresh-view input')
        schema = folder / (name + '.schema.json')
        storage = folder / (name + '.storage.json')
        storage_record = read(storage)
        if (storage_record.get('study_id') != STUDY or storage_record.get('table') != name or
            storage_record.get('source') != VSTUDY + '/raw/' + name + '.jsonl' or
            storage_record.get('rows') != item['rows'] or storage_record.get('source_sha256') != item['raw_sha256'] or
            storage_record.get('raw_json_schema_file') != name + '.schema.json'):
            raise RuntimeError('format storage metadata differs from its exact source/table/rows/raw identity')
        identities[name] = {'rows': item['rows'], 'raw_sha256': item['raw_sha256'],
            **{key: decoded[key] for key in
               ('source_decoded_sha256', 'csv_reconstructed_sha256', 'parquet_reconstructed_sha256')}}
        add(schema, read(schema), 'EXACT_TYPED_OBSERVED_SCHEMA')
        storage_record.pop('source')
        add(storage, storage_record, 'EXACT_TYPED_STORAGE_METADATA_EXCLUDING_CANONICAL_SOURCE_PATH_ONLY')
        excluded[relative + '/' + storage.name] = ['/source']
        for extension, key in (('csv', 'csv_sha256'), ('parquet', 'parquet_sha256')):
            path = folder / (name + '.' + extension)
            actual = sha(path)
            if actual != item[key]:
                raise RuntimeError('actual format byte hash differs from its executed readback receipt')
            records[relative + '/' + path.name] = {'sha256': actual, 'bytes': path.stat().st_size,
                'comparison_sha256': decoded['source_decoded_sha256'],
                'comparison': 'CANONICAL_ROW_VALUE_TYPE_PRESENCE_SHA256_NOT_CSV_OR_PARQUET_FILE_ENCODING'}
    return records, identities, excluded


def capacity_audit_comparison(value, original_relative):
    # Only generated UTC and the byte-receipt edges derived from that UTC.
    # Actual edges were checked before use; all statuses/findings/data remain.
    value.pop('created_utc', None)
    value.pop('original_audit_sha256', None)
    value['output_sha256'].pop('original_audit.json')
    value['evidence'].pop(original_relative)
    return value


def numerical_manifest(study, versions):
    # Strict typed JSON/byte comparisons; no numeric tolerance/row filtering.
    records = {}; exclusions = {}
    def add(relative, omitted=()):
        path = study / relative
        records[relative] = {'sha256': sha(path), 'comparison_sha256': projected_json_hash(path, omitted) if omitted else sha(path), 'bytes': path.stat().st_size}
        if omitted:
            exclusions[relative] = list(omitted)
    add('analysis/stats/' + versions['primary'] + '.json', ('/created_utc',))
    metric = study / 'analysis' / versions['metric']
    for name in ('findings.json', 'unit_checks.jsonl', 'audited_observations.jsonl'):
        add('analysis/' + versions['metric'] + '/' + name)
    add('analysis/' + versions['metric'] + '/metric_audit.json', ('/created_utc',))
    secondary = 'analysis/postrun/' + versions['secondary']
    for name in ('heterogeneity.jsonl', 'paired_heterogeneity.jsonl', 'measurement_events.jsonl', 'secondary_units.jsonl', 'coverage.json'):
        add(secondary + '/' + name)
    add(secondary + '/receipt.json', ('/created_utc',))
    retrieval = study / 'analysis' / versions['retrieval']
    for path in sorted(retrieval.iterdir()):
        if path.is_file():
            add(path.relative_to(study).as_posix(), ('/created_utc',) if path.name == 'audit.json' else ())
    capacity = study / 'analysis' / versions['capacity']
    capacity_record = read(capacity / 'audit.json')
    if not capacity_gate(capacity_record):
        raise RuntimeError('capacity metadata is not a completed strict reconciliation')
    original_relative = 'analysis/' + versions['retrieval'] + '/audit.json'
    if (capacity_record.get('original_audit_sha256') != sha(study / original_relative) or
        capacity_record.get('output_sha256', {}).get('original_audit.json') != sha(study / original_relative)):
        raise RuntimeError('capacity numerical provenance differs from actual preserved original audit')
    for path in sorted(capacity.iterdir()):
        if not path.is_file():
            continue
        relative = path.relative_to(study).as_posix()
        if path.name == 'audit.json':
            projected = capacity_audit_comparison(read(path), original_relative)
            records[relative] = {'sha256': sha(path), 'bytes': path.stat().st_size,
                'comparison_sha256': hashlib.sha256(canonical(projected).encode()).hexdigest()}
            exclusions[relative] = ['/created_utc', '/original_audit_sha256',
                '/output_sha256/original_audit.json', '/evidence/' + original_relative.replace('/', '~1')]
        else:
            add(relative, ('/created_utc',) if path.name == 'original_audit.json' else ())
    reference = 'analysis/postrun/' + versions['reference']
    metadata_edges = ('/source_primary_sha256', '/source_secondary_receipt_sha256', '/source_metric_audit_sha256')
    for name in ('absolute_quality.json', 'matched_external_summary.json'):
        add(reference + '/' + name, metadata_edges)
    add(reference + '/matched_observation_index.json')
    add(reference + '/receipt.json', ('/created_utc', '/files', '/input_sha256', *metadata_edges))
    equivalent = 'analysis/' + versions['equivalence']
    for name in ('matched_units.jsonl', 'findings.json'):
        add(equivalent + '/' + name)
    add(equivalent + '/audit.json', ('/created_utc', '/bindings/full_metric_audit_sha256', '/inputs'))
    report = study / versions['report']
    report_metadata = {
        'primary_authority.json': ('/created_utc',),
        'full_audit_authority.json': ('/created_utc',),
        'retrieval_authority.json': ('/created_utc',),
        'retrieval_original_audit_preserved.json': ('/created_utc',),
        'original_reference_descriptives_receipt.json': ('/created_utc', '/files', '/input_sha256', *metadata_edges),
        'original_absolute_quality_authority.json': metadata_edges,
        'external_saved_matched_summary.json': metadata_edges,
        'external_equivalence_gate.json': ('/created_utc', '/bindings/full_metric_audit_sha256', '/inputs'),
        'saved_descriptive_facts.json': ('/authority_metric_audit_sha256',),
        'report_availability.json': ('/created_utc', '/source_sha256', '/outputs_sha256'),
    }
    for path in sorted(report.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(study).as_posix()
        if path.suffix in ('.csv', '.jsonl', '.json'):
            if path.name == 'retrieval_capacity_authority.json':
                projected = capacity_audit_comparison(read(path), original_relative)
                records[relative] = {'sha256': sha(path), 'bytes': path.stat().st_size,
                    'comparison_sha256': hashlib.sha256(canonical(projected).encode()).hexdigest()}
                exclusions[relative] = ['/created_utc', '/original_audit_sha256',
                    '/output_sha256/original_audit.json', '/evidence/' + original_relative.replace('/', '~1')]
            else:
                add(relative, report_metadata.get(path.name, ()))
        if path.suffix == '.png':
            records[relative] = {'sha256': sha(path), 'comparison_sha256': png_encoding(path), 'bytes': path.stat().st_size, 'comparison': 'PNG_IMAGE_AND_COLOR_ENCODING_EXACT_NOT_VISUAL_QA'}
    format_records, format_identity, format_excluded = format_export_manifest(study, versions['format'])
    records.update(format_records); exclusions.update(format_excluded)
    return {'records': records, 'format_table_identity': format_identity,
        'explicit_nonscientific_metadata_pointers_excluded': exclusions,
        'rule': 'all other typed values/array order and numerical report CSV/JSONL bytes exact; six format CSV/Parquet exports compare canonical decoded values/types/presence and retain actual byte hashes; derived receipt hash edges validated by each consumer; raw/source/freeze/dependency hashes never normalized',
        'render_pdf_svg': 'whole-file hashes retained in actual report availability; byte or layout identity not inferred from numerical identity; actual visual QA required'}


def check_original_sources(manifest):
    changed = []
    for name, row in {**manifest['virtual_inputs'], **manifest['original_controls']}.items():
        path = Path(row['original_host_path'])
        if not path.is_file() or path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
            changed.append(name)
    if changed:
        raise RuntimeError('original inputs changed during postprocessing/rebuild; no success receipt')
    if any(Path(row['original_host_path']).exists() for row in manifest.get('absent_virtual_inputs', {}).values()):
        raise RuntimeError('originally absent capacity input appeared during postprocessing/rebuild')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run'); run.add_argument('--configuration', type=Path, required=True); run.add_argument('--destination', type=Path, required=True)
    rebuild = sub.add_parser('rebuild'); rebuild.add_argument('--archive', type=Path, required=True); rebuild.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(); destination = args.destination.resolve()
    if destination.exists():
        parser.error('entirely fresh destination required; failures/previous runs are never overwritten')
    destination.mkdir(parents=True); logs = destination / 'controller_logs'; logs.mkdir()
    status = {'status': 'RUNNING_OFFLINE_RECOMPUTATION', 'started_utc': utc(), 'action': args.action,
              'executing_controller_sha256': sha(Path(__file__).resolve()), 'steps': []}
    def interrupted(signum, frame):
        raise InterruptedError('host offline controller interrupted')
    previous_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.action == 'run':
            job = read(args.configuration)
            if job.get('schema_version') != 'corrected-postrun-rebuild-job-v1':
                raise ValueError('unrecognized explicit postrun/rebuild job')
            job['versions'] = {**VERSIONS, **job.get('versions', {})}
            if set(job['versions']) != set(VERSIONS) or any(re.fullmatch(r'[a-z][a-z0-9_]{1,80}', value) is None for value in job['versions'].values()):
                raise ValueError('explicit output versions must be simple fresh names')
            verify_controller(job)
            study_source = Path(job['study_out']).resolve()
            if destination == study_source or destination.is_relative_to(study_source) or study_source.is_relative_to(destination):
                raise ValueError('fresh run must be outside the original study/archive tree')
            archive = destination / 'archive'; archive.mkdir()
            inputs = archive_inputs(job, archive, logs, args.configuration.resolve())
            reference_study = run_pipeline(archive, destination / 'reference', job, inputs)
            reference = numerical_manifest(reference_study, job['versions'])
            write(archive / 'reference_numeric_manifest.json', reference)
            write(archive / 'package_manifest.json', {'status': 'ACTUAL_REFERENCE_RECOMPUTED_ARCHIVE_READY_FOR_INDEPENDENT_REBUILD',
                'input_manifest_sha256': sha(archive / 'input_manifest.json'), 'reference_numeric_manifest_sha256': sha(archive / 'reference_numeric_manifest.json'),
                'control_sha256': {path.name: sha(path) for path in (archive / 'control').iterdir()}, 'created_utc': utc()})
        else:
            archive = args.archive.resolve(); package = read(archive / 'package_manifest.json')
            if destination == archive or destination.is_relative_to(archive) or archive.is_relative_to(destination):
                raise ValueError('independent rebuild destination must be outside its archived inputs')
            if (package.get('status') != 'ACTUAL_REFERENCE_RECOMPUTED_ARCHIVE_READY_FOR_INDEPENDENT_REBUILD' or
                sha(archive / 'input_manifest.json') != package.get('input_manifest_sha256') or
                sha(archive / 'reference_numeric_manifest.json') != package.get('reference_numeric_manifest_sha256')):
                raise ValueError('archive has no actual reference computation/hash-bound complete inputs')
            for name, wanted in package['control_sha256'].items():
                if sha(archive / 'control' / name) != wanted:
                    raise ValueError('archived controller/configuration changed')
            job = read(archive / 'control/job.json'); inputs = read(archive / 'input_manifest.json')
            verify_controller(job, inputs)
            reference = read(archive / 'reference_numeric_manifest.json')
        stable_archive(archive, inputs)
        command(['docker', 'image', 'load', '--input', str(archive / 'dependencies/image.tar')], logs / 'dependency_image_load.json')
        if command(['docker', 'image', 'inspect', '--format', '{{.Id}}', inputs['dependency_image_id']]) != inputs['dependency_image_id']:
            raise ValueError('loaded archived dependency image differs')
        rebuilt_study = run_pipeline(archive, destination / 'independent_rebuild', job, inputs)
        actual = numerical_manifest(rebuilt_study, job['versions'])
        keys_equal = set(actual['records']) == set(reference['records'])
        format_equal = canonical(actual['format_table_identity']) == canonical(reference['format_table_identity'])
        differences = [{'file': name, 'expected': reference['records'].get(name), 'actual': actual['records'].get(name)}
            for name in sorted(set(actual['records']) | set(reference['records']))
            if actual['records'].get(name, {}).get('comparison_sha256') != reference['records'].get(name, {}).get('comparison_sha256')]
        write(destination / 'numerical_identity.json', {'status': 'PASS_EXACT_TYPED_NUMERICAL_AND_PNG_ENCODING_IDENTITY' if keys_equal and format_equal and not differences else 'FAIL_RETAINED',
            'same_file_set': keys_equal, 'differences': differences, 'expected': reference, 'actual': actual,
            'same_six_table_rows_raw_SHA_and_three_decoded_SHA': format_equal,
            'visual_qa': 'NOT_PERFORMED_REQUIRES_ACTUAL_REVIEW'})
        stable_archive(archive, inputs)
        if args.action == 'run':
            check_original_sources(inputs)
        if not keys_equal or not format_equal or differences:
            raise RuntimeError('independent recomputation differs; retain every mismatch, do not normalize/reseed')
        status.update(status='ACTUALLY_RECOMPUTED_AND_REBUILT_REQUIRES_VISUAL_QA', finished_utc=utc(),
            archive=str(archive), numerical_identity_sha256=sha(destination / 'numerical_identity.json'),
            actual_stage_runs=18 if args.action == 'run' else 9, paid_or_benchmark_commands=0,
            scientific_source_or_freeze_bytes_modified=False, visual_qa='NOT_PERFORMED_REQUIRES_ACTUAL_REVIEW')
        retained_capacity = read(rebuilt_study / 'analysis' / job['versions']['capacity'] / 'audit.json')
        status.update(retrieval_matrix_execution_complete=retained_capacity['planned_matrix_execution_complete'],
            retrieval_capacity_analysis_status=retained_capacity['status'],
            original_retrieval_audit_status_retained=retained_capacity['original_audit_status'],
            missing_query_units=retained_capacity['missing_query_units'])
    except BaseException as exc:
        status.update(status='FAILED_RETAINED_NO_COMPLETION_CLAIM', controller_finished_utc=utc(), error_type=type(exc).__name__,
                      error_detail='body/credential content omitted; inspect source guards and retained stage metadata')
        write(destination / 'execution.json', status)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_term)
    write(destination / 'execution.json', status)
    print(json.dumps({'status': status['status'], 'destination': str(destination), 'paid_or_benchmark_commands': 0}))


if __name__ == '__main__':
    main()
