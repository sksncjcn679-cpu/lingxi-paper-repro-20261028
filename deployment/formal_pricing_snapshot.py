"""Snapshot public DMX pricing endpoints observed in its official page; no credential use."""
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import urllib.request

ROOT=Path('/evidence/formal_20261019/manifests/pricing-pages')
MODELS=['qwen3.7-plus','doubao-seed-2-0-mini-260215']
RESOURCES=['models','moxingbeilv','gudingbeilv','buquanbeilv','chuangjianhuancunbeilv','huancunmingzhongbeilv','model_prices']

def fetch(name):
    url='https://rmb.dmxapi.cn/?api='+name
    with urllib.request.urlopen(url,timeout=30) as response:
        raw=response.read();date=response.headers.get('Date')
    (ROOT/f'{name}.json').write_bytes(raw)
    value=json.loads(raw)
    hits={k:v for k,v in value.items() if k in MODELS} if isinstance(value,dict) else {}
    return name,{'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'http_date':date,
        'root_type':type(value).__name__,'selected_exact_model_entries':hits,'sample_keys':list(value)[:4] if isinstance(value,dict) else None}

if __name__=='__main__':
    ROOT.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:results=dict(pool.map(fetch,RESOURCES))
    report={'recorded_utc':datetime.now(timezone.utc).isoformat(),'public_source':'https://rmb.dmxapi.cn/',
        'resources':results,'account_specific_discount':'UNKNOWN; public prices are not an authenticated invoice',
        'source_calculation':'official calculateModelPrice: input=base*2, output=base*2*completion_multiplier; units RMB per million tokens; fixed/custom/tier overrides must be checked'}
    (ROOT/'snapshot.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
