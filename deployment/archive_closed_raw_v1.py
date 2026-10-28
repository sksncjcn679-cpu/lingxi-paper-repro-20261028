"""Opaque archive of the exact closed 239-file inventory; never parses log bodies."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import stat
import tarfile

SCOPES=('raw','retrieval_input_correction_v2/raw','state_measurement_correction_v2/raw',
        'state_measurement_correction_v3/raw','state_measurement_correction_v2/logs')


def utc():return datetime.now(timezone.utc).isoformat()


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def inventory(root):
    result={}
    for scope in SCOPES:
        base=root/scope
        if not base.is_dir():raise RuntimeError('registered raw scope absent')
        for path in base.rglob('*'):
            if path.is_symlink():raise RuntimeError('archive source symlink refused')
            if path.is_file():result[path.relative_to(root).as_posix()]=path
    return result


def fingerprint(path):
    x=path.stat();return x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns


class HashedReader:
    def __init__(self,raw):self.raw=raw;self.hash=hashlib.sha256();self.bytes=0
    def read(self,size=-1):
        data=self.raw.read(size);self.hash.update(data);self.bytes+=len(data);return data


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--inventory',required=True,type=Path);parser.add_argument('--destination',required=True,type=Path)
    args=parser.parse_args();root=args.out.resolve();dest=args.destination.resolve()
    if root==dest or root in dest.parents or dest in root.parents:raise RuntimeError('archive destination must be outside original OUT')
    if dest.exists():raise RuntimeError('archive destination already exists; preserve it')
    source_hash=sha(Path(__file__));inventory_hash=sha(args.inventory)
    original=json.loads(args.inventory.read_text());files=original['files']
    if original['raw_bytes_in_bundle']!=0:raise RuntimeError('unexpected source package inventory contract')
    for relative in files:
        pure=PurePosixPath(relative)
        if pure.is_absolute() or '..' in pure.parts or '\\' in relative:raise RuntimeError('unsafe inventory member')
        if not any(relative.startswith(scope+'/') for scope in SCOPES):raise RuntimeError('unregistered raw scope')
    actual=inventory(root)
    if set(actual)!=set(files):raise RuntimeError('actual raw file set differs from closed inventory')
    total=sum(item['bytes'] for item in files.values())
    if shutil.disk_usage(dest.parent).free<2*total+64*1024**2:raise RuntimeError('insufficient measured free disk space')
    dest.mkdir();shutil.copyfile(Path(__file__),dest/'helper.py');shutil.copyfile(args.inventory,dest/'input_inventory.json')
    manifest={'status':'RUNNING','started_utc':utc(),'original_out':str(root),'source_inventory':str(args.inventory),
              'inventory_sha256':inventory_hash,'helper_sha256':source_hash,'planned_files':len(files),'planned_bytes':total,
              'paid_api_calls':0,'body_handling':'opaque binary streams only; no JSON/pickle/log interpretation or body output',
              'raw_scope':SCOPES,'files_copied':0,'source_files_modified':False,'goal_completion':'NOT_CERTIFIED'}
    def save():
        (dest/'archive_audit.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    save();checks=[];before={k:fingerprint(v) for k,v in actual.items()}
    partial=dest/'original_raw.tar.gz.partial'
    try:
        with tarfile.open(partial,'x:gz',compresslevel=3,format=tarfile.PAX_FORMAT) as archive:
            for relative,path in sorted(actual.items()):
                expected=files[relative]
                if path.stat().st_size!=expected['bytes']:raise RuntimeError('source size differs: '+relative)
                if stat.S_IMODE(path.stat().st_mode)&0o222:raise RuntimeError('source write mode unexpectedly enabled: '+relative)
                item=tarfile.TarInfo('formal_20261019/'+relative);item.size=expected['bytes'];item.mode=0o444
                item.uid=item.gid=0;item.mtime=path.stat().st_mtime
                with path.open('rb') as raw:
                    reader=HashedReader(raw);archive.addfile(item,reader)
                if reader.hash.hexdigest()!=expected['sha256'] or reader.bytes!=expected['bytes']:
                    raise RuntimeError('source SHA/size differs: '+relative)
                if fingerprint(path)!=before[relative]:raise RuntimeError('source changed during read: '+relative)
                manifest['files_copied']+=1
                if manifest['files_copied']%20==0:save()
        # Independently decompress every member and compare bytes to the closed inventory.
        seen=set()
        with tarfile.open(partial,'r:gz') as archive:
            for member in archive:
                if not member.isfile() or not member.name.startswith('formal_20261019/'):
                    raise RuntimeError('unexpected archive member type/path')
                relative=member.name.removeprefix('formal_20261019/')
                if relative in seen or relative not in files:raise RuntimeError('duplicate or unexpected archive member')
                seen.add(relative);h=hashlib.sha256();length=0
                with archive.extractfile(member) as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk);length+=len(chunk)
                expected=files[relative]
                if h.hexdigest()!=expected['sha256'] or length!=expected['bytes']:raise RuntimeError('archive readback differs: '+relative)
                checks.append({'path':relative,'bytes':length,'sha256':h.hexdigest(),'archive_readback':'PASS'})
        if seen!=set(files) or set(inventory(root))!=set(files):raise RuntimeError('archive/source inventory mismatch after readback')
        for relative,path in actual.items():
            if fingerprint(path)!=before[relative] or sha(path)!=files[relative]['sha256']:
                raise RuntimeError('original source changed after archive: '+relative)
        if sha(args.inventory)!=inventory_hash:raise RuntimeError('source inventory changed')
        archive=dest/'original_raw.tar.gz';partial.rename(archive)
        archive_sha=sha(archive)
        (dest/'original_raw.tar.gz.sha256').write_text(archive_sha+'  original_raw.tar.gz\n')
        (dest/'member_readback.json').write_text(json.dumps({'status':'PASS_ALL_MEMBERS','files':checks},indent=2)+'\n')
        manifest.update(status='PASS_OPAQUE_RAW_ARCHIVE_AND_FULL_READBACK',finished_utc=utc(),
                        source_and_archive_files=len(checks),uncompressed_bytes=total,archive_bytes=archive.stat().st_size,
                        archive_sha256=archive_sha,original_source_hashes_unchanged=True,
                        package_scope='raw records only; source bundle and study metadata are separate required artifacts',
                        readonly_meaning='mode-bit convention; root/Windows host/later chmod can override; not immutable storage')
        save()
        for path in dest.iterdir():
            if path.is_file():path.chmod(0o444)
        dest.chmod(0o555)
        permission_ok=all(not(stat.S_IMODE(p.stat().st_mode)&0o222) for p in [dest,*dest.iterdir()])
        receipt=dest.with_name(dest.name+'.readonly_verification.json')
        receipt.write_text(json.dumps({'status':'READONLY_CONVENTION' if permission_ok else 'UNSUPPORTED',
                                      'archive_audit_sha256':sha(dest/'archive_audit.json'),
                                      'archive_sha256':archive_sha,'not_immutable':True},indent=2)+'\n')
        print(json.dumps({k:manifest[k] for k in ('status','source_and_archive_files','uncompressed_bytes','archive_bytes','archive_sha256','paid_api_calls')}),flush=True)
    except BaseException as exc:
        manifest.update(status='FAIL_RETAINED',finished_utc=utc(),error_type=type(exc).__name__,error=str(exc));save();raise


if __name__=='__main__':main()
