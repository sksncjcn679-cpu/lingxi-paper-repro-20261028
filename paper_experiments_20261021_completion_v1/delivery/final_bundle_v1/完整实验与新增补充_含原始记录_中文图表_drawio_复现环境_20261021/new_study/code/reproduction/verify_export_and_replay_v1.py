"""Verify the real offline replay and the exported fixed OCI image; no model calls."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import tarfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
IMAGE = '80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main():
    original = ROOT / 'main_batch/analysis/main_postrun_v1'
    replay = ROOT / 'analysis/reproduction_check_v1'
    for base in (original, replay):
        receipt = json.loads((base / 'execution_receipt.json').read_text(encoding='utf-8'))
        assert receipt['actual_terminal'] is True and type(receipt['actual_exit_code']) is int
        assert receipt['actual_exit_code'] == 0
        guard = json.loads((base / 'outputs/guard_result.json').read_text(encoding='utf-8'))
        assert guard['exit_code'] == 0 and guard['denied_count'] == 0
    names = ['normalized_task_runs.jsonl', 'attempt_acceptance.jsonl',
             'derived_aggregation_changes.json', 'formal_primary_v3.json',
             'figure_04.csv', 'observed_by_arm.csv']
    comparisons = []
    for name in names:
        a, b = original / 'outputs/analysis' / name, replay / 'outputs/analysis' / name
        x, y = digest(a), digest(b)
        assert x == y, name
        comparisons.append({'file': name, 'original_sha256': x,
                            'reproduced_sha256': y, 'byte_identical': True})
    save(replay / 'saved_analysis_reproduction_identity.json', {
        'status': 'ACTUAL_OFFLINE_REPLAY_SIX_OUTPUTS_BYTE_IDENTICAL',
        'verified_utc': datetime.now(timezone.utc).isoformat(),
        'comparisons': comparisons, 'model_calls': 0,
        'scope': 'Identical frozen postprocessing of captured records; not a second real-model study.'})

    archive = ROOT / 'delivery/runtime_v1/openeuler_dependency_image_80471.tar'
    known_key = None
    for line in (ROOT.parent / '.env').read_text(encoding='utf-8-sig').splitlines():
        if line.startswith('VLM_API_KEY='):
            candidate = line.split('=', 1)[1].strip().strip('\"').strip("'")
            if len(candidate) >= 12:
                known_key = candidate.encode()
    assert known_key, 'Known credential must be available privately for exclusion check'
    checks, secret_matches = [], 0
    with tarfile.open(archive, 'r:') as tar:
        members = tar.getmembers()
        assert all(not n.name.startswith('/') and '..' not in PurePosixPath(n.name).parts for n in members)
        index = json.load(tar.extractfile('index.json'))
        assert any(n['digest'] == 'sha256:' + IMAGE for n in index['manifests'])
        for member in members:
            if not member.isfile():
                continue
            h, tail = hashlib.sha256(), b''
            stream = tar.extractfile(member)
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(chunk)
                secret_matches += (tail + chunk).count(known_key)
                tail = chunk[-len(known_key):]
            actual = h.hexdigest()
            if member.name.startswith('blobs/sha256/'):
                assert member.name.rsplit('/', 1)[1] == actual
                checks.append({'member': member.name, 'bytes': member.size, 'sha256': actual})
        manifest = json.load(tar.extractfile('manifest.json'))
        assert len(manifest) == 1
        assert all(name in tar.getnames() for name in manifest[0]['Layers'])
        config = json.load(tar.extractfile(manifest[0]['Config']))
        assert not any(x.split('=', 1)[0].lower().endswith(('api_key', 'token', 'password', 'secret'))
                       for x in config.get('config', {}).get('Env', []))
    assert secret_matches == 0, 'Credential exclusion failed; never package this export'
    known_key = None
    save(archive.parent / 'image_export_verification.json', {
        'status': 'ACTUAL_DOCKER_SAVE_OCI_INDEX_AND_ALL_BLOBS_VERIFIED',
        'verified_utc': datetime.now(timezone.utc).isoformat(),
        'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': digest(archive),
        'fixed_image_reference': 'sha256:' + IMAGE,
        'reference_type': 'OCI image index, not the child image configuration digest',
        'members': len(members), 'blob_checks': checks,
        'known_live_VLM_key_exact_matches_in_exported_member_bytes': secret_matches,
        'secret_named_config_environment_entries': 0,
        'scope': 'Content addressing, member paths, config and known-key bytes; no credential values retained.',
        'docker_save_actual_exit_code_observed_by_coordinator': 0})
    print(json.dumps({'offline_replay_identical_outputs': len(comparisons),
                      'fixed_OCI_blobs_verified': len(checks), 'credentials_written': 0}))


if __name__ == '__main__':
    main()
