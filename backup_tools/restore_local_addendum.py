"""Verify or restore the encrypted local-retention addendum into a fresh directory.

python restore_local_addendum.py --parts DIR --key-file separately_saved_key.json --verify-only
python restore_local_addendum.py --parts DIR --key-file separately_saved_key.json --destination NEW_DIR
Requires pyzipper; the original archive includes offline Windows wheels.
"""
import argparse,bisect,hashlib,io,json,os,sys
from pathlib import Path,PurePosixPath
try:import pyzipper
except ImportError:
    sys.path.insert(0,'E:/codex_uploads/tools/pyzipper');import pyzipper
class Reader(io.RawIOBase):
    def __init__(self,root,parts):
        self.root=Path(root);self.parts=parts;self.ends=[];self.position=0;self.file=None;self.index=-1;n=0
        for p in parts:n+=p['bytes'];self.ends.append(n)
        self.length=n
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,n,whence=0):
        self.position=n if whence==0 else self.position+n if whence==1 else self.length+n
        if self.position<0:raise ValueError('Negative position')
        return self.position
    def read(self,size=-1):
        left=min(self.length-self.position,size if size>=0 else self.length-self.position);out=[]
        while left>0:
            i=bisect.bisect_right(self.ends,self.position);start=self.ends[i-1] if i else 0
            if self.index!=i:
                if self.file:self.file.close()
                self.file=open(self.root/self.parts[i]['name'],'rb');self.index=i
            self.file.seek(self.position-start);n=min(left,self.ends[i]-self.position);b=self.file.read(n)
            if len(b)!=n:raise RuntimeError('Truncated segment')
            out.append(b);left-=n;self.position+=n
        return b''.join(out)
    def close(self):
        if self.file:self.file.close()
        super().close()
def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        while b:=f.read(8*1024*1024):h.update(b)
    return h.hexdigest()
def safe(p):
    q=PurePosixPath(p)
    if q.is_absolute() or '..' in q.parts or any(':' in s for s in q.parts):raise RuntimeError('Unsafe archive path')
    return q.parts
def run(parts,key_file,destination=None):
    root=Path(parts);index=json.loads((root/'LOCAL_RETAINED_ADDENDUM_20261028.json').read_text(encoding='utf-8'))
    key=json.loads(Path(key_file).read_text(encoding='utf-8-sig'))
    if key['snapshot_id']!=index['base_snapshot_id']:raise RuntimeError('Key belongs to another snapshot')
    for p in index['segments']:
        f=root/p['name']
        if f.stat().st_size!=p['bytes'] or sha(f)!=p['sha256']:raise RuntimeError('Transfer segment differs')
    if destination:
        dest=Path(os.path.abspath(destination))
        if dest.exists():raise RuntimeError('A fresh destination is required; originals are never overwritten')
        dest.mkdir(parents=True)
    count=0;total=0
    with Reader(root,index['segments']) as stream,pyzipper.AESZipFile(stream) as z:
        z.setpassword(key['archive_password'].encode('ascii'))
        manifest=json.loads(z.read('MANIFEST.json'))
        for r in manifest['payload_files']:
            name='payload/'+r['root_id']+'/'+r['relative'];safe(name)
            h=hashlib.sha256();n=0;out=None
            if destination:
                target=dest.joinpath(*safe(r['root_id']),*safe(r['relative']))
                if os.name=='nt':target=Path('\\\\?\\'+str(target))
                target.parent.mkdir(parents=True,exist_ok=True);out=open(target,'xb')
            try:
                with z.open(name) as src:
                    while b:=src.read(8*1024*1024):h.update(b);n+=len(b);out.write(b) if out else None
            finally:
                if out:out.close()
            if (n,h.hexdigest())!=(r['bytes'],r['sha256']):raise RuntimeError('Decoded content differs')
            count+=1;total+=n
        if count!=index['payload_files']:raise RuntimeError('Incomplete supplemental contents')
    return dict(status='PASS',verified_files=count,verified_bytes=total,all_segment_sha256_pass=True,all_AES_plaintext_sha256_pass=True,destination=destination)
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--parts',required=True);ap.add_argument('--key-file',required=True);ap.add_argument('--verify-only',action='store_true');ap.add_argument('--destination')
    a=ap.parse_args()
    if not a.verify_only and not a.destination:ap.error('Choose --verify-only or --destination')
    print(json.dumps(run(a.parts,a.key_file,a.destination),ensure_ascii=False))
