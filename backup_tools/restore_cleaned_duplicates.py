"""Restore cleaned duplicate paths from retained originals/ZIP into a fresh folder.

python restore_cleaned_duplicates.py --manifest approved_manifest.json --destination NEW_FOLDER
Use --sample to recover one small nonempty file from each recovery category.
The original experiment paths are recorded in restored_paths.json; originals are never overwritten.
"""
import argparse,hashlib,json,os,zipfile
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',required=True)
    ap.add_argument('--destination',required=True)
    ap.add_argument('--sample',action='store_true')
    args=ap.parse_args()
    m=json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
    if m['status']!='PASS' or m['files']!=8306:raise RuntimeError('Unrecognized verified cleanup manifest')
    dest=Path(args.destination).resolve()
    if dest.exists():raise RuntimeError('A new destination directory is required')
    rows=m['entries']
    if args.sample:
        selected={}
        for r in sorted(rows,key=lambda r:int(r['logical_bytes'])):
            if 0<int(r['logical_bytes'])<1024*1024:selected.setdefault(r['decision'],r)
        rows=list(selected.values())
    dest.mkdir(parents=True)
    archives={};results=[]
    try:
        for r in rows:
            p=Path(r['path']);relative=Path(p.drive[0])/Path(*p.parts[1:])
            output=dest/relative
            if os.path.commonpath([os.path.abspath(output),str(dest)])!=str(dest):raise RuntimeError('Unsafe recovery path')
            Path('\\\\?\\'+str(output.parent)).mkdir(parents=True,exist_ok=True)
            if r.get('archive_member'):
                z=archives.setdefault(r['retained_source'],zipfile.ZipFile(r['retained_source'])) if r['retained_source'] not in archives else archives[r['retained_source']]
                stream=z.open(r['archive_member'])
            else:
                stream=open('\\\\?\\'+os.path.abspath(r['retained_source']),'rb')
            h=hashlib.sha256();count=0
            with stream,open('\\\\?\\'+str(output),'xb') as f:
                while chunk:=stream.read(8*1024*1024):f.write(chunk);h.update(chunk);count+=len(chunk)
            if h.hexdigest()!=r['sha256'] or count!=int(r['logical_bytes']):raise RuntimeError('Restored content differs')
            results.append(dict(original_path=r['path'],restored_path=str(output),sha256=h.hexdigest(),bytes=count,decision=r['decision']))
        receipt=dict(status='PASS',restored_files=len(results),original_paths_overwritten=False,files=results)
        (dest/'restored_paths.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(status='PASS',restored_files=len(results),original_paths_overwritten=False),ensure_ascii=False))
    finally:
        for z in archives.values():z.close()

if __name__=='__main__':main()
