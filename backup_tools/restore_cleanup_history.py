"""Restore the cumulative cleanup history into a NEW directory, without overwriting originals.

python restore_cleanup_history.py --manifest combined_recovery_manifest.csv --destination NEW_DIR
Optional: --sample (small file per category), or --only-sqlite --one-per-content.
"""
import argparse,csv,hashlib,json,os,zipfile
from pathlib import Path

def xp(p):return '\\\\?\\'+os.path.abspath(p).removeprefix('\\\\?\\') if os.name=='nt' else str(p)
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',required=True);ap.add_argument('--destination',required=True)
    ap.add_argument('--sample',action='store_true');ap.add_argument('--only-sqlite',action='store_true');ap.add_argument('--one-per-content',action='store_true')
    a=ap.parse_args();rows=list(csv.DictReader(open(a.manifest,encoding='utf-8-sig',newline='')))
    if len(rows)!=8309:raise RuntimeError('Expected cumulative 8,309-file recovery manifest')
    if a.only_sqlite:rows=[r for r in rows if r['decision']=='D_optional_archive_backed_derived_SQLite']
    if a.sample:
        selected={}
        for r in sorted(rows,key=lambda r:int(r['logical_bytes'])):
            if 0<int(r['logical_bytes'])<1024*1024:selected.setdefault(r['decision'],r)
        rows=list(selected.values())
    if a.one_per_content:rows=list({(r['sha256'],int(r['logical_bytes'])):r for r in rows}.values())
    dest=Path(os.path.abspath(a.destination))
    if dest.exists():raise RuntimeError('Destination must be new; no original is overwritten')
    dest.mkdir(parents=True);archives={};results=[]
    try:
        for r in rows:
            original=Path(r['path']);target=dest/original.drive[0]/Path(*original.parts[1:])
            if os.path.commonpath([os.path.abspath(target),str(dest)])!=str(dest):raise RuntimeError('Unsafe restoration destination')
            Path(xp(target.parent)).mkdir(parents=True,exist_ok=True)
            if r.get('archive_member'):
                if r['retained_source'] not in archives:archives[r['retained_source']]=zipfile.ZipFile(xp(r['retained_source']))
                stream=archives[r['retained_source']].open(r['archive_member'])
            else:stream=open(xp(r['retained_source']),'rb')
            h=hashlib.sha256();n=0
            with stream,open(xp(target),'xb') as out:
                while chunk:=stream.read(8*1024*1024):out.write(chunk);h.update(chunk);n+=len(chunk)
            if (n,h.hexdigest())!=(int(r['logical_bytes']),r['sha256']):raise RuntimeError('Restored bytes differ')
            results.append(dict(original_path=r['path'],restored_path=str(target),bytes=n,sha256=h.hexdigest(),decision=r['decision']))
        receipt=dict(status='PASS',restored_files=len(results),restored_bytes=sum(r['bytes'] for r in results),original_paths_overwritten=False,files=results)
        (dest/'RESTORE_RECEIPT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in receipt.items() if k!='files'},ensure_ascii=False))
    finally:
        for z in archives.values():z.close()
if __name__=='__main__':main()
