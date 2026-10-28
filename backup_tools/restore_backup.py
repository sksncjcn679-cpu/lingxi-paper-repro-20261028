"""Restore/verify the complete AES-256 encrypted content-addressed archive.

Install dependency: python -m pip install pyzipper
Verify: python restore_backup.py --parts DIR --key-file KEY --verify-only
Restore: python restore_backup.py --parts DIR --key-file KEY --destination NEW_DIR
"""
import argparse, hashlib, json, os, sys, time, io, bisect
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
try:
    import pyzipper
except ImportError:
    local_tools=Path('E:/codex_uploads/tools/pyzipper')
    if local_tools.exists():sys.path.insert(0,str(local_tools))
    import pyzipper

def filehash(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        while b:=f.read(8*1024*1024):h.update(b)
    return h.hexdigest()

def storage_file(root,name):
    """Optional local-only storage map supports validation across several drives."""
    root=Path(root);p=root/name
    if p.exists():return p
    mapping=root/'LOCAL_STORAGE_MAP.json'
    if mapping.exists():
        local=json.loads(mapping.read_text(encoding='utf-8'))
        if name in local:return Path(local[name])
    return p

class SegmentedReader(io.RawIOBase):
    """Seekable logical ZIP backed by independently uploaded small binary segments."""
    def __init__(self,root,segments):
        self.root=Path(root);self.segments=segments;self.ends=[];n=0
        for r in segments:n+=r['bytes'];self.ends.append(n)
        self.length=n;self.position=0;self.current=None;self.current_index=-1
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        p=offset if whence==0 else self.position+offset if whence==1 else self.length+offset
        if p<0:raise ValueError('Negative logical seek')
        self.position=p;return p
    def read(self,size=-1):
        if self.position>=self.length:return b''
        left=min(self.length-self.position,size if size>=0 else self.length-self.position)
        chunks=[]
        while left:
            index=bisect.bisect_right(self.ends,self.position)
            start=self.ends[index-1] if index else 0
            if self.current_index!=index:
                if self.current:self.current.close()
                self.current=open(storage_file(self.root,self.segments[index]['name']),'rb');self.current_index=index
            offset=self.position-start;self.current.seek(offset)
            take=min(left,self.ends[index]-self.position);chunk=self.current.read(take)
            if len(chunk)!=take:raise RuntimeError('Truncated transfer segment')
            chunks.append(chunk);self.position+=take;left-=take
        return b''.join(chunks)
    def close(self):
        if self.current:self.current.close()
        super().close()

def part_stream(parts,name):
    p=storage_file(parts,name)
    if p.exists():return p.open('rb')
    manifest=Path(parts)/'TRANSFER_MANIFEST.json'
    if not manifest.exists():raise FileNotFoundError(str(p))
    d=json.loads(manifest.read_text(encoding='utf-8'))
    item=next((r for r in d['logical_parts'] if r['name']==name),None)
    if item is None:raise RuntimeError('Logical archive part is missing: '+name)
    return SegmentedReader(parts,item['segments'])

@contextmanager
def open_archive(parts,name):
    with part_stream(parts,name) as stream:
        with pyzipper.AESZipFile(stream) as archive:yield archive

def load(parts,key_file):
    parts=Path(parts)
    meta=json.loads((parts/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
    key=json.loads(Path(key_file).read_text(encoding='utf-8'))
    if key['snapshot_id']!=meta['snapshot_id']:raise RuntimeError('Key belongs to a different archive')
    for r in meta['parts']:
        h=hashlib.sha256();n=0
        with part_stream(parts,r['name']) as stream:
            while b:=stream.read(8*1024*1024):h.update(b);n+=len(b)
        if n!=r['bytes'] or h.hexdigest()!=r['sha256']:raise RuntimeError('Encrypted part differs: '+r['name'])
    password=key['archive_password'].encode('ascii')
    with open_archive(parts,'part-000-control.zip') as z:
        z.setpassword(password)
        files=[json.loads(line) for line in z.read('snapshot/files.jsonl').decode('utf-8').splitlines()]
        roots=json.loads(z.read('snapshot/roots.json'))
        directories=json.loads(z.read('snapshot/directories.json'))
    return meta,password,files,roots,directories

def safe_relative(value):
    p=PurePosixPath(value.replace('\\','/'))
    if p.is_absolute() or '..' in p.parts or any(':' in part for part in p.parts):raise RuntimeError('Unsafe path in archive')
    return p.parts if str(p)!='.' else ()

def verify(parts,key_file,receipt=None):
    parts=Path(parts);meta,password,files,roots,directories=load(parts,key_file)
    expected={r['sha256']:r['bytes'] for r in files}
    verified={};last=time.monotonic()
    for part in meta['parts']:
        if part['name']=='part-000-control.zip':continue
        with open_archive(parts,part['name']) as z:
            z.setpassword(password)
            for info in z.infolist():
                if not info.filename.startswith('blobs/') or info.is_dir():raise RuntimeError('Unexpected encrypted member')
                digest=info.filename.split('/')[-1]
                if digest in verified or digest not in expected:raise RuntimeError('Duplicate or unlisted content blob')
                h=hashlib.sha256();n=0
                with z.open(info) as src:
                    while b:=src.read(8*1024*1024):h.update(b);n+=len(b)
                if h.hexdigest()!=digest or n!=expected[digest]:raise RuntimeError('Decoded content differs: '+digest)
                verified[digest]=n
                now=time.monotonic()
                if now-last>15:
                    print(json.dumps(dict(stage='verify_decoded',blobs=len(verified),GB=round(sum(verified.values())/1e9,3))),flush=True);last=now
    if set(expected)!=set(verified):raise RuntimeError('Missing payload blobs')
    destinations=set()
    for r in files:
        relative=safe_relative(r['relative']);safe_relative(r['root_id'])
        dest=(r['root_id'],relative)
        if dest in destinations:raise RuntimeError('Conflicting restoration paths')
        destinations.add(dest)
    result=dict(status='PASS',snapshot_id=meta['snapshot_id'],encrypted_parts=len(meta['parts']),verified_unique_blobs=len(verified),covered_files=len(files),covered_logical_bytes=sum(r['bytes'] for r in files),decoded_unique_bytes=sum(verified.values()),all_original_paths_covered=True,all_encrypted_parts_sha256_pass=True,all_decoded_blob_sha256_pass=True,source_files_deleted=False)
    if receipt:Path(receipt).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

def restore(parts,key_file,destination,only_prefix=None):
    destination=Path(destination)
    if destination.exists():raise RuntimeError('Destination must not exist; original files are never overwritten')
    meta,password,files,roots,directories=load(parts,key_file)
    selected=[r for r in files if not only_prefix or (r['root_id']+'/'+r['relative']).startswith(only_prefix)]
    if not selected:raise RuntimeError('No files match the requested restoration scope')
    by_hash={}
    for r in selected:by_hash.setdefault(r['sha256'],[]).append(r)
    destination.mkdir(parents=True)
    for d in directories:
        relative='/'.join([d['root_id'],d['relative']]).replace('\\','/')
        if not only_prefix or relative.startswith(only_prefix):
            (destination.joinpath(*safe_relative(d['root_id']),*safe_relative(d['relative']))).mkdir(parents=True,exist_ok=True)
    restored=0
    for part in meta['parts']:
        if part['name']=='part-000-control.zip':continue
        with open_archive(parts,part['name']) as z:
            z.setpassword(password)
            for info in z.infolist():
                digest=info.filename.split('/')[-1]
                records=by_hash.get(digest)
                if not records:continue
                first=None
                for r in records:
                    target=destination.joinpath(*safe_relative(r['root_id']),*safe_relative(r['relative']))
                    if os.name=='nt':target=Path('\\\\?\\'+str(target.resolve()))
                    target.parent.mkdir(parents=True,exist_ok=True)
                    h=hashlib.sha256();n=0
                    with (open(first,'rb') if first else z.open(info)) as src, open(target,'xb') as out:
                        while b:=src.read(8*1024*1024):out.write(b);h.update(b);n+=len(b)
                    if h.hexdigest()!=r['sha256'] or n!=r['bytes']:raise RuntimeError('Restored file differs')
                    os.utime(target,ns=(r['mtime_ns'],r['mtime_ns']))
                    first=target;restored+=1
    if restored!=len(selected):raise RuntimeError('Incomplete restoration')
    return dict(status='PASS',restored_files=restored,destination=str(destination),scope=only_prefix or 'ALL')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parts',required=True);p.add_argument('--key-file',required=True)
    p.add_argument('--verify-only',action='store_true');p.add_argument('--destination')
    p.add_argument('--only-prefix');p.add_argument('--receipt')
    a=p.parse_args()
    if a.verify_only:result=verify(a.parts,a.key_file,a.receipt)
    elif a.destination:result=restore(a.parts,a.key_file,a.destination,a.only_prefix)
    else:p.error('Choose --verify-only or --destination')
    print(json.dumps(result,ensure_ascii=False),flush=True)
