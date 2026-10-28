"""One real, bounded provider request; credentials are read only from secret mount."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

import httpx
import openai

OUT = Path('/output/usage_preflight')
ENDPOINT = 'https://www.dmxapi.cn/v1'
MODEL = 'qwen3.7-plus'

def now(): return datetime.now(timezone.utc).isoformat()
def sha(b): return hashlib.sha256(b).hexdigest()
def save(name, value):
    with (OUT/name).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')

def main():
    secret = {}
    for line in Path('/run/secrets/model.env').read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            k,v=line.split('=',1); secret[k.strip()]=v.strip().strip('\"\'')
    key=secret.get('VLM_API_KEY') or secret.get('AGENT_LLM_API_KEY')
    if not key: raise RuntimeError('MISSING_MODEL_CREDENTIAL')
    client_id='completion-preflight-'+str(uuid.uuid4())
    state={'client_request_id':client_id,'request_started_utc':now(),
           'endpoint':ENDPOINT+'/chat/completions','requested_model':MODEL,
           'max_attempts_including_first':1,'is_formal_unit':False,
           'credential_values_retained':False,'sdk_version':openai.__version__}
    def request_hook(req):
        state['actual_request_bytes']=len(req.content)
        state['actual_request_sha256']=sha(req.content)
        assert len(req.content)<=131072
        assert req.url.host=='www.dmxapi.cn' and req.url.scheme=='https'
        state['actual_client_id_header']=req.headers.get('x-client-request-id')
        save('attempt_started.json',dict(state))
    def response_hook(res):
        res.read()
        ids={}
        for k,v in res.headers.items():
            compact=re.sub('[^a-z]','',k.lower())
            if ('requestid' in compact or 'traceid' in compact) and len(v)<=256:
                if key in v or re.search(r'(?:sk-|nap_)[A-Za-z0-9_-]{16,}',v):
                    raise RuntimeError('UNSAFE_HEADER_VALUE_NOT_SAVED')
                ids[k]=v
        state.update(http_status=res.status_code,response_id_headers=ids,
                     response_header_names=sorted(res.headers.keys()),
                     http_response_bytes=len(res.content),http_response_sha256=sha(res.content))
        # Synthetic diagnostic content only; no request/auth headers are saved.
        if key.encode() in res.content or re.search(rb'(?:sk-|nap_)[A-Za-z0-9_-]{16,}',res.content):
            raise RuntimeError('UNSAFE_RESPONSE_NOT_SAVED')
        try: body=res.json()
        except ValueError: body=None
        save('http_response.json',{'body':body,**state})
    started=time.monotonic()
    with httpx.Client(timeout=60,follow_redirects=False,trust_env=False,
                      event_hooks={'request':[request_hook],'response':[response_hook]}) as http:
        with openai.OpenAI(api_key=key,base_url=ENDPOINT,timeout=60,max_retries=0,http_client=http) as client:
            try:
                response=client.chat.completions.create(model=MODEL,temperature=0,max_tokens=1500,
                    messages=[{'role':'user','content':'This is a usage-accounting calibration. Reply with exactly OK.'}],
                    extra_body={'enable_thinking':False},
                    extra_headers={'X-Client-Request-Id':client_id,'Idempotency-Key':client_id})
                usage=response.usage.model_dump() if response.usage is not None else None
                valid=isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0
                    for k in ('prompt_tokens','completion_tokens','total_tokens'))
                if valid: valid=usage['total_tokens']==usage['prompt_tokens']+usage['completion_tokens']
                state.update(status='PROVIDER_USAGE_CAPTURED_AWAIT_EXACT_BILLING_BRIDGE' if valid else 'FAIL_MISSING_OR_INVALID_USAGE',
                    response_body_id=response.id,sdk_request_id=getattr(response,'_request_id',None),
                    returned_model=response.model,usage_raw=usage,usage_source='provider_reported',
                    response=response.model_dump(),usage_valid=valid)
            except Exception as error:
                state.update(status='FAIL_REAL_REQUEST',error_type=type(error).__name__,
                             error_request_id=getattr(error,'request_id',None),usage_raw=None)
    state.update(request_finished_utc=now(),wall_seconds=time.monotonic()-started)
    save('result.json',state)
    print(json.dumps({k:state.get(k) for k in ['status','response_id_headers','response_body_id','sdk_request_id','usage_raw','wall_seconds']},ensure_ascii=False))
    return 0 if state.get('usage_valid') else 2

if __name__=='__main__': raise SystemExit(main())
