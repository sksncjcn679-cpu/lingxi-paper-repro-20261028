"""Local synthetic fixture only. No SDK/httpx/network/models/credentials."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from provider_accounting import AccountingStop, ProviderAccounting


class Headers:
    def __init__(self, values): self.values = values
    def keys(self): return self.values.keys()
    def get_list(self, key):
        assert key.lower() not in ('authorization', 'set-cookie', 'x-token-request-id')
        value = self.values[key]
        return value if isinstance(value, list) else [value]


class RequestHeaders:
    def __init__(self, identity): self.identity = identity
    def get(self, key):
        assert key == 'x-client-request-id'
        return self.identity


def setup(identity='fixture-client-0001', body=None, status=200):
    accounting = ProviderAccounting(); accounting.begin(identity, 'FIXTURE_MODEL')
    request = SimpleNamespace(headers=RequestHeaders(identity))
    accounting.request_hook(request)
    headers = Headers({'x-request-id':'fixture-sdk-UUID', 'x-oneapi-request-id':'fixture-DMX-ID',
                       'x-extra-request-id':['fixture-extra-1','fixture-extra-2'],
                       'authorization':'DO_NOT_READ_AUTHORIZATION', 'set-cookie':'DO_NOT_READ_COOKIE',
                       'x-token-request-id':'DO_NOT_READ_SENSITIVE_HEADER'})
    response = SimpleNamespace(request=request, headers=headers, status_code=status,
                               json=lambda: copy.deepcopy(body))
    accounting.response_hook(response)
    return accounting, request, response


def sdk(usage):
    return SimpleNamespace(_request_id='fixture-sdk-UUID', id='chatcmpl-fixture',
                           usage=None if usage is None else SimpleNamespace(model_dump=lambda:copy.deepcopy(usage)))


def blocked(accounting):
    try: accounting.begin('fixture-next-call','FIXTURE_MODEL')
    except AccountingStop: return
    raise AssertionError('subsequent call was not blocked')


def main():
    passed=[]
    usage={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0,'prompt_tokens_details':{'cached_tokens':None}}
    body={'id':'chatcmpl-fixture','usage':usage,'choices':[{'message':{'content':'DO_NOT_RETAIN_ANSWER'}}]}
    a,_,_=setup(body=body); row=a.finish_success(sdk(usage))
    assert row['usage_complete'] and row['usage_raw']==usage and row['usage_raw']['prompt_tokens_details']['cached_tokens'] is None
    assert row['response_ID_headers']['x-oneapi-request-id']==['fixture-DMX-ID']
    assert row['response_ID_headers']['x-extra-request-id']==['fixture-extra-1','fixture-extra-2']
    serialized=json.dumps(row)
    assert all(value not in serialized for value in ('DO_NOT_READ','DO_NOT_RETAIN','authorization','set-cookie','x-token-request-id'))
    a.begin('fixture-next-call','FIXTURE_MODEL'); passed.append('explicit_zero_full_usage_null_and_duplicate_header_values_preserved')
    for label,value in [('missing',None),('boolean',{'prompt_tokens':True,'completion_tokens':0,'total_tokens':1}),
                        ('negative',{'prompt_tokens':-1,'completion_tokens':1,'total_tokens':0}),
                        ('missing_member',{'prompt_tokens':1,'completion_tokens':1}),
                        ('inconsistent_total',{'prompt_tokens':1,'completion_tokens':1,'total_tokens':3})]:
        a,_,_=setup(body={'usage':value}); row=a.finish_success(sdk(value)); assert not row['usage_complete']; blocked(a); passed.append(label+'_stops_new_calls')
    error_body={'error':{'code':'insufficient_user_quota','type':'api_error',
                         'message':'DO_NOT_RETAIN_ERROR (request id: fixture-explicit-error-id)'},'usage':None}
    a,_,response=setup(body=error_body,status=403)
    error=SimpleNamespace(response=response,request_id='fixture-sdk-status-error')
    row=a.finish_error(error)
    assert row['APIStatusError_request_id']=='fixture-sdk-status-error' and row['http_status']==403
    assert row['safe_error_body_projection']['/error/code']=='insufficient_user_quota'
    assert row['safe_error_body_projection']['message_explicit_request_IDs']==['fixture-explicit-error-id']
    assert 'DO_NOT_RETAIN_ERROR' not in json.dumps(row); blocked(a); passed.append('HTTP_error_header_body_code_and_explicit_ID_without_message')
    a=ProviderAccounting(); a.begin('fixture-network','FIXTURE_MODEL')
    row=a.finish_error(ConnectionError())
    assert row['usage_raw'] is None and row['transport_outcome']=='NETWORK_FAILURE_SERVER_RECEIPT_UNCONFIRMED'
    blocked(a); passed.append('network_error_unknown_not_zero')
    a,request,_=setup(body=body)
    try: a.request_hook(request)
    except AccountingStop: pass
    else: raise AssertionError('duplicate dispatch not blocked')
    blocked(a); passed.append('second_physical_dispatch_blocked')
    root=Path(__file__).resolve().parents[1]; out=root/'usage_accounting_fixture_receipt.json'
    assert not out.exists()
    result={'status':'LOCAL_SYNTHETIC_FIXTURE_PASS_NOT_REAL_USAGE_OR_BILLING_PROOF','actual_terminal':True,
            'non_experimental':True,'network_or_model_calls':0,'fixture_cases':passed,
            'source_sha256':hashlib.sha256((Path(__file__).parent/'provider_accounting.py').read_bytes()).hexdigest(),
            'fixture_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    out.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__': main()
