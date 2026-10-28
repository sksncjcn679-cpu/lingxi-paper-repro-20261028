"""Finite read-only dependency review; no build/install/approval attestation."""
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tarfile
import time

STAGE = Path(r'E:\codex_rebuilds\dependency_stage_v1')
OUT = STAGE/'review_evidence'
MANIFEST_SHA = 'ef49e417f35a1cde24d7910d870b1c0519fe4e2032adbe86b73e10b145751529'
BASE = 'openeuler/openeuler@sha256:03b3f365bbba13e9f90c70b08bd9aa0002bc69db858c2808cfab8a55d6e184e3'


def utc(): return datetime.now(timezone.utc).isoformat()
def sha_bytes(value): return hashlib.sha256(value).hexdigest()
def sha(path): return sha_bytes(path.read_bytes())
def dump(name, value):
    with (OUT/name).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def command(name, args):
    started, tick = utc(), time.perf_counter()
    process = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    (OUT/(name+'.stdout.txt')).write_bytes(process.stdout)
    (OUT/(name+'.stderr.txt')).write_bytes(process.stderr)
    record = {'command': args, 'started_utc': started, 'finished_utc': utc(),
              'wall_sec': time.perf_counter()-tick, 'exit_code': process.returncode,
              'stdout_sha256': sha_bytes(process.stdout), 'stderr_sha256': sha_bytes(process.stderr)}
    dump(name+'.command.json', record)
    return process


def rpm_header(data, offset):
    if data[offset:offset+4] != b'\x8e\xad\xe8\x01': raise ValueError('RPM header magic')
    count, size = struct.unpack_from('>II', data, offset+8)
    start = offset+16+count*16
    tags = {}
    for index in range(count):
        tag, kind, rel, number = struct.unpack_from('>IIII', data, offset+16+16*index)
        value = data[start+rel:start+size]
        if kind == 6: tags[tag] = value.split(b'\0', 1)[0].decode('utf-8')
        elif kind == 4 and number == 1: tags[tag] = struct.unpack_from('>I', value)[0]
    return tags, start+size


def rpm_review(manifest):
    context = STAGE/'context'
    specs = set((context/'rpm_specs.txt').read_text().splitlines())
    listed = set((context/'rpm_nevra.txt').read_text().splitlines())
    logged_sha = {Path(line.split('  ', 1)[1]).name: line.split('  ', 1)[0]
                  for line in (context/'rpm_sha256.txt').read_text().splitlines()}
    exits = dict(line.split('\t') for line in (context/'rpm_checksig_exit_codes.tsv').read_text().splitlines())
    signature_text = (context/'rpm_checksig.txt').read_text()
    repos = {}
    for name in ['download_install.log', 'download_reinstall.log']:
        for number, line in enumerate((context/name).read_text().splitlines(), 1):
            match = re.match(r'^\s+(\S+)\s+(noarch|x86_64)\s+(\S+)\s+(OS|update)\s+', line)
            if match:
                package, arch, version, repo = match.groups()
                repos[package] = {'repo': repo, 'version_in_transaction': version, 'source': name, 'line': number}
    results = []
    for path in sorted((context/'rpms').glob('*.rpm')):
        data = path.read_bytes()
        if data[:4] != b'\xed\xab\xee\xdb': raise ValueError('RPM lead magic')
        _, end = rpm_header(data, 96)
        tags, _ = rpm_header(data, (end+7)//8*8)
        nevra = f'{tags[1000]}-{tags.get(1003,0)}:{tags[1001]}-{tags[1002]}.{tags[1022]}'
        digest = sha_bytes(data)
        results.append({'file': path.name, 'actual_header_nevra': nevra, 'actual_sha256': digest,
                        'matches_requested_and_nevra_log': nevra in specs and nevra in listed,
                        'matches_sha_log_and_collection': digest == logged_sha[path.name] == manifest['inputs']['context/rpms/'+path.name]['sha256'],
                        'recorded_checksig_exit': int(exits[path.name]),
                        'recorded_checksig_OK': f'/stage/rpms/{path.name}: digests signatures OK' in signature_text,
                        'actual_transaction_repository': repos.get(tags[1000])})
    audit = {'rpm_files': len(results), 'requested_specs': len(specs), 'logged_nevra': len(listed),
             'all14_exact': len(results)==14 and specs==listed=={row['actual_header_nevra'] for row in results},
             'all14_sha_match': all(row['matches_sha_log_and_collection'] for row in results),
             'all14_logged_signature_checks_exit0_OK': all(row['recorded_checksig_exit']==0 and row['recorded_checksig_OK'] for row in results),
             'all14_transaction_repo_attributed': all(row['actual_transaction_repository'] for row in results),
             'packages': results,
             'repository_log_sha256': sha(context/'enabled_repositories.txt'),
             'trust_limitation': 'Recorded download-container checksig is reviewed, not rerun. Initial DNF log imported key via HTTP; pinned-base existing public key/trust evidence is separately read below.'}
    dump('rpm_actual_identity_and_recorded_signatures.json', audit)
    return audit


def packets(binary):
    position = 0
    while position < len(binary):
        first = binary[position]; position += 1
        if not first & 128: raise ValueError('OpenPGP packet header')
        if first & 64:
            tag = first & 63; size = binary[position]; position += 1
            if 192 <= size < 224:
                size = ((size-192)<<8)+binary[position]+192; position += 1
            elif size == 255:
                size = int.from_bytes(binary[position:position+4], 'big'); position += 4
            elif size >= 224: raise ValueError('partial OpenPGP length unsupported')
        else:
            tag = (first>>2)&15; kind = first&3
            if kind == 3: size = len(binary)-position
            else:
                width = (1,2,4)[kind]; size = int.from_bytes(binary[position:position+width], 'big'); position += width
        body = binary[position:position+size]; position += size
        yield tag, body


def base_trust_review():
    script = '''set -eu
printf 'TRUST_DB\n'
rpm -qa 'gpg-pubkey*' --qf '%{NAME}-%{VERSION}-%{RELEASE}\\n'
printf 'TRUST_DETAILS\n'
rpm -qi 'gpg-pubkey*' || true
printf 'KEYFILES\n'
for key in /etc/pki/rpm-gpg/*; do
  [ -f "$key" ] || continue
  printf 'BEGIN_KEYFILE %s\\n' "$key"
  sha256sum "$key"
  cat "$key"
  printf '\\nEND_KEYFILE\\n'
done
printf 'REPO_CONFIG\n'
for repo in /etc/yum.repos.d/*.repo; do
  [ -f "$repo" ] || continue
  printf 'FILE %s\\n' "$repo"
  grep -E '^([[]|gpgcheck=|repo_gpgcheck=|gpgkey=|enabled=|baseurl=|metalink=|mirrorlist=)' "$repo" || true
done
'''
    args = ['docker','run','--rm','--pull=never','--network','none','--read-only','--cap-drop','ALL',
            '--security-opt','no-new-privileges:true','--pids-limit','64','--memory','128m','--cpus','1',
            '--entrypoint','/bin/sh',BASE,'-c',script]
    result = command('base_public_trust_read', args)
    text = result.stdout.decode('utf-8', errors='replace')
    keys = []
    for armor in re.findall(r'-----BEGIN PGP PUBLIC KEY BLOCK-----.*?-----END PGP PUBLIC KEY BLOCK-----', text, re.S):
        lines = armor.splitlines()[1:-1]
        payload = ''.join(line.strip() for line in lines if line.strip() and not ':' in line and not line.startswith('='))
        binary = base64.b64decode(payload)
        for tag, body in packets(binary):
            if tag not in (6,14): continue
            if body[0] != 4: raise ValueError('public key fingerprint version unsupported')
            fingerprint = hashlib.sha1(b'\x99'+len(body).to_bytes(2,'big')+body).hexdigest().upper()
            keys.append({'packet_tag': tag, 'version':4, 'fingerprint':fingerprint,
                         'key_id':fingerprint[-16:], 'rpm_key_id':fingerprint[-8:],
                         'public_packet_sha256':sha_bytes(body)})
    record = {'base':BASE,'actual_command_exit':result.returncode,'network':'none','workspace_or_secret_mounts':0,
              'public_key_fingerprints':keys,'no_key_import_install_or_download':True,
              'matches_collector_import_fingerprint':any(row['fingerprint']=='8AA16BF9F2CA5244010DCA963B477C60B675600B' for row in keys),
              'trust_details_output_sha256':sha(OUT/'base_public_trust_read.stdout.txt'),
              'boundary':'Raw output contains only this pinned official base public RPM trust DB/key files/repo configuration. A key file being present is distinct from an imported RPM trust DB key.'}
    dump('pinned_base_public_key_identity.json', record)
    return record


def venv_targeted_review():
    inventory = [json.loads(line) for line in (STAGE/'venv_member_inventory.jsonl').open(encoding='utf-8')]
    requested = json.loads((STAGE/'venv_sensitive_names_and_pth_for_root_review.json').read_text())
    paths = {row['path'] for row in requested}
    pth = [row for row in inventory if row['path'].endswith('.pth')]
    if len(pth)!=3 or {row['path'] for row in pth} != {name for name in paths if name.endswith('.pth')}:
        raise ValueError('inventory no longer selects exactly three pth members')
    inspected, cert = [], None
    with tarfile.open(STAGE/'context/formal-venv.tar', 'r:') as archive:
        for name in sorted(paths):
            member = archive.getmember(name)
            if not member.isfile(): raise ValueError('selected member is not regular')
            data = archive.extractfile(member).read()
            if name.endswith('.pth'):
                inspected.append({'path':name,'bytes':len(data),'sha256':sha_bytes(data),'content':data.decode('utf-8'),
                                  'executed':False})
            else:
                blocks = re.findall(rb'-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----',data,re.S)
                cert = {'path':name,'identity':'certifi public CA trust bundle in the collected Python3.11 venv',
                        'bytes':len(data),'sha256':sha_bytes(data),'public_certificate_count':len(blocks),
                        'public_DER_sha256':[sha_bytes(base64.b64decode(block)) for block in blocks],
                        'private_key_marker_count':len(re.findall(rb'-----BEGIN [^-]*PRIVATE KEY-----',data))}
    record = {'targeted_member_payloads_read':len(paths),'pth_count':len(inspected),'pth':inspected,'certifi_public_identity':cert,
              'other_tar_member_payloads_read':0,'pth_executed':False,'venv_activated':False,
              'tar_sha256_authority':'collection_manifest input digest; tar not fully rehashed in this bounded targeted review'}
    dump('targeted_venv_pth_and_public_cert_identity.json', record)
    return record


def finite_secret_scan():
    root = STAGE/'runtime_source'
    names = re.compile(r'(?i)(^\.env(?:\.|$)|\.(pem|key|p12|pfx)$|^(id_rsa|id_ed25519|credentials|\.netrc|\.pypirc)(\.|$))')
    patterns = [rb'\bsk-[A-Za-z0-9_-]{20,}\b',rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----',
                rb'(?i)\bBearer[ \t]+[A-Za-z0-9_\-./+=]{24,}',
                rb'(?i)["\x27]?(?:api_?key|api_?token|access_token|password|secret)["\x27]?\s*[:=]\s*["\x27][A-Za-z0-9_/+=.\-]{24,}["\x27]']
    files=[]
    for item in ('pyproject.toml','src','scenarios','scripts'):
        path = root/item
        files.extend([path] if path.is_file() else [p for p in path.rglob('*') if p.is_file()])
    name_paths, literal_paths = [], []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if any(names.search(part) for part in path.relative_to(root).parts): name_paths.append(relative)
        data=path.read_bytes()
        count=sum(len(re.findall(pattern,data)) for pattern in patterns)
        if count:literal_paths.append({'path':relative,'suspected_literal_count':count})
    record={'files_scanned':len(files),'sensitive_filename_count':len(name_paths),'sensitive_filename_paths':name_paths,
            'suspected_hardcoded_literal_count':sum(row['suspected_literal_count'] for row in literal_paths),
            'suspected_hardcoded_literal_paths':literal_paths}
    dump('runtime_source_finite_name_literal_counts.json',record)
    return record


def main():
    started=utc();manifest_path=STAGE/'collection_manifest.json'
    if sha(manifest_path)!=MANIFEST_SHA:raise ValueError('collection manifest identity differs')
    manifest=json.loads(manifest_path.read_text())
    rpm=rpm_review(manifest)
    trust=base_trust_review()
    venv=venv_targeted_review()
    scan=finite_secret_scan()
    summary={'status':'FINITE_READ_ONLY_REVIEW_COMPLETED_NOT_AN_APPROVAL','started_utc':started,'finished_utc':utc(),
             'source_sha256':sha(Path(__file__)),'actual_command':[sys.executable,*sys.argv],
             'collection_manifest_sha256_before':MANIFEST_SHA,'collection_manifest_sha256_after':sha(manifest_path),
             'rpm_all14_exact':rpm['all14_exact'],'rpm_all14_sha_match':rpm['all14_sha_match'],
             'rpm_all14_recorded_signature_exit0_OK':rpm['all14_logged_signature_checks_exit0_OK'],
             'base_public_key_matches_download_import':trust['matches_collector_import_fingerprint'],
             'targeted_tar_payloads_read':venv['targeted_member_payloads_read'],'source_scan_counts':scan,
             'build_install_download_calls_by_reviewer':0,'model_calls':0,
             'root_review_or_secret_free_attestation_written':False,
             'limitations':['No complete package payload security audit or whole venv secret-free certificate.',
                            'RPM cryptographic checks are the actual collection logs, not a fresh reviewer verification.',
                            'Base public key/trust output must be distinguished from HTTP first-import in collector logs.',
                            'Three .pth contents are read statically, never executed; editable absolute target must exist in eventual image.']}
    dump('finite_review_summary.json',summary)
    print(json.dumps({key:summary[key] for key in ['status','rpm_all14_exact','rpm_all14_sha_match','rpm_all14_recorded_signature_exit0_OK','base_public_key_matches_download_import','targeted_tar_payloads_read','source_scan_counts']},ensure_ascii=False))


if __name__=='__main__':main()
