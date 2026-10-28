"""One local SDK missing-usage response proves persistence and no later dispatch."""
import json
from pathlib import Path
import httpx
from openai import OpenAI
import real_driver as driver

def main():
    out=Path('/output/usage_missing_response_fixture');out.mkdir(exist_ok=False)
    (out/'manifests').mkdir()
    (out/'manifests/budget_limits.json').write_text(json.dumps({'max_provider_attempts':1,'observed_reported_total_tokens_stop':20000,'max_actual_utf8_request_bytes':131072}))
    driver.OUT=out;driver._BUDGET_LEDGER=None
    row=driver.cases()[0]
    unit=json.loads(Path('/output/main_batch/manifests/formal_plan.json').read_text())[0]
    unit={**unit,'run_id':'local-missing-usage-fixture','unit_id':'local-missing-usage-fixture','phase':'public_local_accounting_boundary'}
    dispatched=[]
    def handler(request):
        dispatched.append(str(request.url))
        return httpx.Response(200,headers={'x-request-id':'fixture-upstream-id','x-oneapi-request-id':'fixture-billing-id'},
            json={'id':'chatcmpl-fixture','object':'chat.completion','created':0,'model':'qwen3.7-plus',
                  'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(row['expected'])},'finish_reason':'stop'}]})
    def factory(cfg):
        return OpenAI(api_key='offline',base_url='https://local-fixture.invalid/v1',max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler),trust_env=False))
    result=driver.execute(unit,row,None,_sdk_fixture_factory=factory)
    accounting=driver.read_rows('provider_accounting.jsonl')
    responses=[x for x in driver.read_rows('model_io.jsonl') if x['direction']=='response']
    assert len(dispatched)==len(accounting)==len(responses)==1
    assert responses[0]['response']['usage'] is None
    assert accounting[0]['usage_raw'] is None and not accounting[0]['usage_complete']
    assert driver.budget_ledger().accounting.halted
    try:driver.budget_ledger().check()
    except driver.BudgetStop:pass
    else:raise AssertionError('later attempt not blocked')
    assert not result.get('provider_usage_complete')
    receipt={'status':'PASS_ONE_LOCAL_MISSING_USAGE_RESPONSE_PERSISTED_AND_NEXT_ATTEMPT_BLOCKED',
             'actual_http_fixture_dispatches':1,'model_network_calls':0,'full_response_records':1,
             'usage_null_preserved':True,'next_attempt_blocked':True,'not_scientific_evidence':True}
    with (out/'verification.json').open('x') as f:json.dump(receipt,f,indent=2)
    print(json.dumps(receipt))
if __name__=='__main__':main()
