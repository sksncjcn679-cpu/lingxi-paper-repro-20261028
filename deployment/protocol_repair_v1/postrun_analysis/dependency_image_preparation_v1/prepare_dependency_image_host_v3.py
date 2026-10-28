"""Prepared Windows-host collection/build steps. Do not execute during timing.

collect stops for actual root RPM/tar/key review. build consumes that review,
builds offline and records a smoke result; it NEVER writes secret_free attestation
or launches B8. Existing download script and Dockerfile are hash-bound copies.
Process/lock probes are snapshots. Root must keep the actual postrun window
exclusive; the retrieval protocol provides no global lock to hold here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import tarfile
import uuid

STUDY = 'protocol_repair_20261020_v1'
BASE = 'openeuler/openeuler@sha256:03b3f365bbba13e9f90c70b08bd9aa0002bc69db858c2808cfab8a55d6e184e3'
PINNED = {
    'rpm_specs.txt': 'c3eeca1bb30e3ef201e5e52e2f4270d5ed80e91711e579219892e921aa58471e',
    'download_exact_rpms.sh': 'b5f3b70711f55f5a5adf8a5304af82d4b144297c8d40392a5999ac58fa6b9d36',
    'Dockerfile': '603d6aef98a8228ad05e60247cfc7e1a325573935aea82387cd5afe8560d1ae9',
}
SAFE_ENV = {'PATH', 'HOME', 'LANG', 'LANGUAGE', 'TZ', 'TERM', 'VIRTUAL_ENV', 'LD_LIBRARY_PATH',
    'PYTHONUNBUFFERED', 'PYTHONDONTWRITEBYTECODE', 'PYTHONHASHSEED', 'DEBIAN_FRONTEND'}
PROBE = r'''
import fcntl,json,os
from pathlib import Path
names={'formal_sequence.py','real_driver.py','external_runner.py','retrieval_driver.py',
       'formal_real_driver.py','formal_external_runner.py','formal_retrieval_v2_run.py',
       'formal_stress_run_all.py','formal_overhead_calibration.py'}
active=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit() or int(p.name)==os.getpid():continue
    try:args=(p/'cmdline').read_bytes().split(b'\0')
    except (FileNotFoundError,PermissionError,ProcessLookupError):continue
    matches=sorted({Path(a.decode(errors='replace')).name for a in args if a and Path(a.decode(errors='replace')).name in names})
    if matches:active.append({'pid':int(p.name),'scripts':matches})
handles=[]
try:
    for name in ('formal_sequence_execution.lock','real_provider_execution.lock'):
        f=(Path('/evidence/protocol_repair_20261020_v1/manifests')/name).open('r');handles.append(f)
        fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    print(json.dumps({'active_timing_processes':active,'existing_real_locks_free':True}))
finally:
    for f in handles:f.close()
'''


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def read_small(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024:
        raise RuntimeError('expected small metadata, refusing large input: ' + path.name)
    return path.read_text(encoding='utf-8-sig')


def command(logs, label, argv, output=None, timeout=21600):
    log = logs / (label + '-' + uuid.uuid4().hex[:8])
    log.mkdir(parents=True)
    target = Path(output) if output else log / 'stdout.bin'
    record = {'argv': argv, 'started_utc': utc(), 'stdout_file': str(target)}
    try:
        with target.open('xb') as stdout, (log / 'stderr.bin').open('xb') as stderr:
            result = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=timeout, check=False)
        record['exit_code'] = result.returncode
        if result.returncode:
            raise RuntimeError(label + ' failed; retain command logs')
        return target
    finally:
        record['finished_utc'] = utc()
        save(log / 'command.json', record)


def quiet_gate(args, logs):
    study = args.study_out.resolve()
    quiet_path = args.quiescence_receipt.resolve()
    if (study.name != STUDY or quiet_path != study / 'analysis/postrun_window_v2.json'):
        raise ValueError('exact corrected study and actual analysis/postrun_window_v2.json required')
    quiet = json.loads(read_small(quiet_path))
    execution = json.loads(read_small(study / 'formal_execution_v1/execution.json'))
    stages = execution.get('stages') or []
    if (execution.get('status') != 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT' or
        execution.get('supervised_child_observed_running') is True or
        [s.get('name') for s in stages] != ['main', 'native_remaining', 'external'] or
        any(s.get('status') != 'TERMINAL' or type(s.get('exit_code')) is not int or s['exit_code'] != 0 or not s.get('finished_utc') for s in stages)):
        raise RuntimeError('actual formal stages are not terminal exit0')
    if (quiet.get('study_id') != STUDY or quiet.get('status') != 'ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING' or
        quiet.get('active_real_timing_processes') != [] or quiet.get('active_retrieval_timing_processes') != []):
        raise RuntimeError('root actual quietv2 observation is absent or nonquiet')
    verified = datetime.fromisoformat(quiet['verified_utc'].replace('Z', '+00:00'))
    finished = datetime.fromisoformat(execution['finished_utc'].replace('Z', '+00:00'))
    if verified.tzinfo is None or finished.tzinfo is None or not finished <= verified <= datetime.now(timezone.utc):
        raise RuntimeError('quietv2 is not a timezone-aware actual post-termination observation')
    result = json.loads(read_small(command(logs, 'actual-process-lock-probe',
        ['docker', 'exec', args.container, '/usr/bin/python3', '-B', '-c', PROBE])))
    if result.get('active_timing_processes') != [] or result.get('existing_real_locks_free') is not True:
        raise RuntimeError('a timing process or occupied real lock blocks preparation')
    return {'quiescence_receipt': str(quiet_path), 'quiescence_sha256': digest(quiet_path),
        'verified_utc': quiet['verified_utc'], 'actual_process_lock_probe': result}


def guarded_member(member, top):
    name = PurePosixPath(member.name)
    if (name.is_absolute() or not name.parts or name.parts[0] != top or '..' in name.parts
            or '\\' in member.name or ':' in member.name):
        raise RuntimeError('archive member escaped selected root')
    denied = {'.env', '.ssh', '.aws', '.git', '.netrc', 'id_rsa', 'id_ed25519', 'evidence', 'results', 'raw'}
    if any(part.casefold() in denied or part.casefold().startswith('.env.') for part in name.parts):
        raise RuntimeError('secret/evidence path found; preserve archive and stop: ' + member.name)
    if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
        raise RuntimeError('unsupported archive member type: ' + member.name)
    return name


def inspect_venv(archive, inventory):
    findings = []
    links = {}; regular = set(); members = set()
    with tarfile.open(archive, 'r:') as tar, inventory.open('x', encoding='utf-8') as stream:
        for member in tar:
            name = guarded_member(member, 'formal-venv')
            if str(name) in members:
                raise RuntimeError('duplicate archive member requires root review: ' + str(name))
            members.add(str(name))
            record = {'path': member.name, 'bytes': member.size, 'type': member.type.decode('ascii'), 'link': member.linkname}
            if member.issym() or member.islnk():
                if '\\' in member.linkname or ':' in member.linkname or '..' in PurePosixPath(member.linkname).parts:
                    raise RuntimeError('venv link needs explicit root handling; retain tar: ' + member.name)
                target = member.linkname if member.linkname.startswith('/') else posixpath.normpath(str(name.parent / member.linkname))
                if member.islnk():
                    target = posixpath.normpath(member.linkname)
                internal = target == 'formal-venv' or target.startswith('formal-venv/')
                interpreter = member.issym() and str(name).startswith('formal-venv/bin/') and target in {'/usr/bin/python3.11', '/usr/bin/python3', '/usr/bin/python'}
                if not (internal or interpreter):
                    raise RuntimeError('unapproved venv link target; root review required: ' + member.name)
                links[str(name)] = {'target': target, 'hardlink': member.islnk()}
            elif member.isfile():
                regular.add(str(name))
            if name.suffix.casefold() in {'.key', '.pem', '.p12', '.pfx', '.jks', '.crt'} or name.suffix == '.pth':
                findings.append(record)
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    for name in members:
        if any(str(parent) in links for parent in PurePosixPath(name).parents):
            raise RuntimeError('archive member appears below a link; retain tar for root: ' + name)
    for name, link in links.items():
        if any(str(parent) in links for parent in PurePosixPath(link['target']).parents):
            raise RuntimeError('venv link target has a link ancestor; retain tar for root: ' + name)
        seen = {name}; target = link['target']
        while target in links:
            if target in seen:
                raise RuntimeError('venv link cycle; retain tar for root: ' + name)
            seen.add(target); target = links[target]['target']
        if link['hardlink'] and target not in regular:
            raise RuntimeError('venv hardlink does not resolve to a regular archive member: ' + name)
    return findings


def collect(args, stage, logs):
    gate = quiet_gate(args, logs)
    context = stage / 'context'; context.mkdir()
    runtime = stage / 'runtime_source'; runtime.mkdir()
    archives = stage / 'source_archives'; archives.mkdir()
    for name, expected in PINNED.items():
        source = Path(__file__).parent / name
        if digest(source) != expected:
            raise RuntimeError('reviewed preparation source changed: ' + name)
        shutil.copyfile(source, context / name)
    base = read_small(command(logs, 'official-base-id', ['docker', 'image', 'inspect', '--format', '{{.Id}}', BASE])).strip()
    digests = json.loads(read_small(command(logs, 'official-base-repodigests',
        ['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', BASE])))
    if BASE not in (digests or []):
        raise RuntimeError('actual local official base repository digest differs')
    container = read_small(command(logs, 'source-container-identity',
        ['docker', 'inspect', '--format', '{{.Id}} {{.Image}}', args.container])).strip()
    command(logs, 'venv-binary-tar', ['docker', 'cp', args.container + ':/opt/lingxi/formal-venv', '-'], context / 'formal-venv.tar')
    findings = inspect_venv(context / 'formal-venv.tar', stage / 'venv_member_inventory.jsonl')
    save(stage / 'venv_sensitive_names_and_pth_for_root_review.json', findings)
    for item in ('pyproject.toml', 'src', 'scenarios', 'scripts'):
        archive = archives / (item + '.tar')
        command(logs, 'source-' + item, ['docker', 'cp', args.container + ':/opt/lingxi/formal-checkout/' + item, '-'], archive)
        with tarfile.open(archive, 'r:') as tar:
            for member in tar:
                name = guarded_member(member, item)
                if member.issym() or member.islnk():
                    raise RuntimeError('source link cannot be recreated silently on Windows; retain archive for root')
                if '__pycache__' in name.parts or name.suffix == '.pyc':
                    continue
                if name.suffix.casefold() in {'.key', '.pem', '.p12', '.pfx', '.jks'}:
                    raise RuntimeError('potential source key file; do not copy into runtime staging')
                target = runtime / Path(*name.parts)
                if not target.resolve().is_relative_to(runtime.resolve()):
                    raise RuntimeError('source member escaped Windows runtime staging')
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output)
    quiet_gate(args, logs)
    owned = 'dep-download-' + uuid.uuid4().hex[:16]
    save(stage / 'owned_download_container.json', {'name': owned})
    try:
        command(logs, 'exact-official-rpm-download', ['docker', 'run', '--rm', '--pull', 'never', '--name', owned,
            '--network', 'bridge', '--mount', 'type=bind,src=' + str(context) + ',dst=/stage',
            '--entrypoint', '/bin/sh', BASE, '/stage/download_exact_rpms.sh'])
    except BaseException:
        try:
            command(logs, 'stop-owned-download-on-failure', ['docker', 'stop', '--time', '10', owned], timeout=60)
        except Exception:
            pass  # command.json preserves cleanup failure; retain the original error.
        raise
    inputs = {}
    for root in (context, runtime, archives):
        for path in sorted(root.rglob('*')):
            if path.is_file():
                inputs[path.relative_to(stage).as_posix()] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
    save(stage / 'collection_manifest.json', {'status': 'COLLECTED_AWAIT_ACTUAL_ROOT_RPM_TAR_KEY_SECRET_REVIEW',
        'created_utc': utc(), 'official_base': BASE, 'actual_base_config_id': base,
        'actual_base_repo_digests': digests, 'source_container_identity': container, 'gate': gate,
        'inputs': inputs, 'runtime_source_root': str(runtime), 'source_items': ['pyproject.toml', 'src', 'scenarios', 'scripts'],
        'sensitive_name_pth_findings': len(findings), 'preparation_source_sha256': digest(Path(__file__)),
        'review_receipt_required_before_build': True, 'secret_free_attestation': 'NOT_WRITTEN_REQUIRES_ACTUAL_REVIEW'})


def build(args, stage, logs):
    quiet_gate(args, logs)
    manifest_path = stage / 'collection_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    review = json.loads(read_small(args.review_receipt))
    required = ('rpm_signature_and_official_key_provenance_reviewed', 'venv_members_links_pth_reviewed',
        'secret_exclusion_reviewed', 'approved_for_offline_build')
    if (review.get('schema_version') != 'dependency-staging-root-review-v1' or
        review.get('collection_manifest_sha256') != digest(manifest_path) or
        any(review.get(field) is not True for field in required) or not review.get('reviewed_utc')):
        raise RuntimeError('actual root review must bind the collected bytes before offline build')
    for relative, record in manifest['inputs'].items():
        path = (stage / relative).resolve()
        if not path.is_relative_to(stage) or path.stat().st_size != record['bytes'] or digest(path) != record['sha256']:
            raise RuntimeError('collected input changed; no build permitted')
    expected_context = {key[len('context/'):] for key in manifest['inputs'] if key.startswith('context/')}
    actual_context = {path.relative_to(stage / 'context').as_posix() for path in (stage / 'context').rglob('*') if path.is_file()}
    if actual_context != expected_context:
        raise RuntimeError('context file set changed after review; no added/unreviewed input is permitted')
    for name, expected in PINNED.items():
        if digest(stage / 'context' / name) != expected:
            raise RuntimeError('reviewed Docker/download/spec source changed')
    save(stage / 'actual_prebuild_root_review.json', review)
    snapshot = stage / 'reviewed_build_context'; snapshot.mkdir()
    for relative in sorted(expected_context):
        source = stage / 'context' / relative
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        expected = manifest['inputs']['context/' + relative]['sha256']
        if digest(source) != expected or digest(target) != expected:
            raise RuntimeError('reviewed build snapshot differs from reviewed input')
    iid = stage / 'image.iid'
    command(logs, 'offline-image-build', ['docker', 'build', '--network=none', '--pull=false',
        '--tag', args.tag, '--iidfile', str(iid), '--file', str(snapshot / 'Dockerfile'), str(snapshot)])
    image = read_small(iid).strip()
    if re.fullmatch(r'sha256:[0-9a-f]{64}', image) is None:
        raise RuntimeError('build did not return an immutable image ID')
    metadata = {'image_id': image}
    for key, template in {'actual_id': '{{.Id}}', 'layers': '{{json .RootFS.Layers}}',
        'repo_digests': '{{json .RepoDigests}}', 'env_names': '{{range .Config.Env}}{{println (index (split . "=") 0)}}{{end}}'}.items():
        text = read_small(command(logs, 'safe-image-' + key, ['docker', 'image', 'inspect', '--format', template, image])).strip()
        metadata[key] = text.splitlines() if key == 'env_names' else json.loads(text) if key in {'layers', 'repo_digests'} else text
    if metadata['actual_id'] != image or any(name not in SAFE_ENV and not name.startswith('LC_') for name in metadata['env_names']):
        raise RuntimeError('image identity or credential-excluding ENV name policy failed')
    save(stage / 'safe_image_metadata.json', metadata)
    quiet_gate(args, logs)
    command(logs, 'actual-dependency-smoke', ['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
        '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m', '--env', 'HOME=/tmp',
        '--env', 'MPLCONFIGDIR=/tmp/matplotlib', '--entrypoint', '/opt/lingxi/formal-venv/bin/python', image,
        '-I', '-B', '-c', "import numpy,scipy,matplotlib,pyarrow,pyarrow.parquet; assert pyarrow.Codec.is_available('zstd'); print(numpy.__version__,scipy.__version__,matplotlib.__version__,pyarrow.__version__)"])
    save(stage / 'build_result.json', {'status': 'ACTUAL_IMAGE_BUILT_AND_SMOKE_COMPLETED_AWAIT_INDEPENDENT_SECRET_FREE_REVIEW',
        'finished_utc': utc(), 'image_id': image, 'collection_manifest_sha256': digest(manifest_path),
        'root_review_sha256': digest(stage / 'actual_prebuild_root_review.json'),
        'runtime_source_root': manifest['runtime_source_root'], 'secret_free_attestation': 'NOT_WRITTEN',
        'next': 'root reviews actual provenance/records and writes genuine dependency attestation before approved B8; this script never runs B8'})


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='build review JSON: schema_version=dependency-staging-root-review-v1; collection_manifest_sha256=actual hash; reviewed_utc=actual time; rpm_signature_and_official_key_provenance_reviewed, venv_members_links_pth_reviewed, secret_exclusion_reviewed, approved_for_offline_build must each represent actual true review.')
    parser.add_argument('action', choices=('collect', 'build'))
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--study-out', type=Path, required=True)
    parser.add_argument('--quiescence-receipt', type=Path, required=True)
    parser.add_argument('--container', default='lingxi-openeuler-2403')
    parser.add_argument('--review-receipt', type=Path)
    parser.add_argument('--tag', default='lingxi-corrected-analysis:dependency-v1')
    args = parser.parse_args()
    stage = args.destination.resolve()
    if stage.is_relative_to(args.study_out.resolve()):
        parser.error('dependency staging must stay outside the authoritative study/evidence tree')
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.container) is None or ',' in str(stage):
        parser.error('explicit container name/ID and an unambiguous Docker mount path are required')
    if args.action == 'collect':
        if stage.exists():
            parser.error('retain existing directory and choose a fresh destination')
        stage.mkdir(parents=True)
    elif not args.review_receipt or not stage.is_dir():
        parser.error('build requires the existing collected stage and an actual root review receipt')
    logs = stage / ('logs-' + args.action + '-' + uuid.uuid4().hex[:8]); logs.mkdir()
    save(logs / 'host_disk_before.json', {'stage': str(stage), 'free_bytes': shutil.disk_usage(stage).free, 'observed_utc': utc()})
    try:
        (collect if args.action == 'collect' else build)(args, stage, logs)
    except BaseException as error:
        save(logs / 'FAILED_RETAINED.json', {'status': 'FAILED_RETAINED_NO_AUTOMATIC_RETRY', 'finished_utc': utc(),
            'error_type': type(error).__name__, 'error': str(error), 'secret_free_attestation': 'NOT_WRITTEN'})
        raise


if __name__ == '__main__':
    main()
