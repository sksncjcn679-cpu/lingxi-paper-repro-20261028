"""Exact DMX header-to-billing check. System token is ephemeral terminal input."""
from pathlib import Path
from datetime import datetime, timezone
import getpass
import hashlib
import json
import re
import urllib.request
import urllib.parse

ROOT=Path(__file__).resolve().parents[1]
ENDPOINT='https://www.dmxapi.cn/api/log/self'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def main():
    calibration=json.loads((ROOT/'usage_preflight/result.json').read_text(encoding='utf-8'))
    identity=calibration['response_id_headers']['x-oneapi-request-id']
    expected=calibration['usage_raw']
    assert calibration['usage_valid'] and re.fullmatch(r'[A-Za-z0-9_-]{12,128}',identity)
    secret=getpass.getpass('SYSTEM_TOKEN (ephemeral, no echo): ')
    if not re.fullmatch(r'nap_[A-Za-z0-9_-]{16,}',secret):
        raise RuntimeError('INVALID_SYSTEM_TOKEN_FORMAT')
    params={'p':1,'page_size':-1,'type':0,'request_id':identity}
    req=urllib.request.Request(ENDPOINT+'?'+urllib.parse.urlencode(params),
        headers={'Authorization':'Bearer '+secret,'Accept':'application/json'})
    receipt={'endpoint':ENDPOINT,'method':'GET','params':params,'started_utc':datetime.now(timezone.utc).isoformat(),
             'credential_persisted':False,'new_model_calls':0}
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=45) as res:
            raw=res.read(4*1024*1024+1); receipt['http_status']=res.status
        if len(raw)>4*1024*1024 or secret.encode() in raw or re.search(rb'(?:sk-|nap_)[A-Za-z0-9_-]{16,}',raw):
            raise RuntimeError('UNSAFE_OR_OVERSIZED_BILLING_RESPONSE_NOT_SAVED')
        data=json.loads(raw)
        assert data.get('success') is True
        rows=data['data']['items']
        private=Path('E:/codex_rebuilds/completion_20261021_v1/private_billing')
        private.mkdir(exist_ok=False)
        dest=private/'preflight.original.json'
        with dest.open('xb') as f:f.write(raw)
        selected=[r for r in rows if r.get('request_id')==identity]
        assert len(selected)==1 and data['data']['total']==len(rows)==1
        row=selected[0]
        assert row['model_name']==calibration['requested_model']
        assert type(row['prompt_tokens']) is int and type(row['completion_tokens']) is int
        assert row['prompt_tokens']==expected['prompt_tokens'] and row['completion_tokens']==expected['completion_tokens']
        receipt.update(status='EXACT_HTTP_HEADER_TO_ORIGINAL_BILLING_ID_AND_USAGE_VERIFIED',
            log_request_id=row['request_id'],header_name='x-oneapi-request-id',
            model_name=row['model_name'],prompt_tokens=row['prompt_tokens'],completion_tokens=row['completion_tokens'],
            original_response_private_path=str(dest),original_response_sha256=hashlib.sha256(raw).hexdigest(),
            matched_rows=1,original_item_count=len(rows),model_response_total_tokens=expected['total_tokens'])
    except Exception as error:
        receipt.update(status='BILLING_BRIDGE_NOT_VERIFIED',error_type=type(error).__name__)
    receipt['finished_utc']=datetime.now(timezone.utc).isoformat()
    with (ROOT/'usage_preflight/billing_bridge_receipt.json').open('x',encoding='utf-8') as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    print(json.dumps(receipt,ensure_ascii=False))
    return 0 if receipt['status']=='EXACT_HTTP_HEADER_TO_ORIGINAL_BILLING_ID_AND_USAGE_VERIFIED' else 2

if __name__=='__main__':raise SystemExit(main())
