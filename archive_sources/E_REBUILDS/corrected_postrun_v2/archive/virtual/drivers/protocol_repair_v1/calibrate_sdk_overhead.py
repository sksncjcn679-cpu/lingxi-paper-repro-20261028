"""SDK request/readback and recording-overhead calibration with local responses.

No private key is read. IPv4/IPv6 socket creation is rejected. Fake usage values
exercise parsing only, are explicitly tagged, and never enter real-study raw.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import uuid

sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
sys.path.append('/drivers')
import httpx
from openai import OpenAI
import real_driver as driver
from calibrate_contract import deny_internet
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--destination',required=True,type=Path)
    p.add_argument('--smoke',action='store_true');a=p.parse_args()
    if a.destination.exists():raise RuntimeError('new SDK/overhead calibration version required')
    a.destination.mkdir(parents=True);(a.destination/'manifests').mkdir()
    sys.addaudithook(deny_internet)
    driver.OUT=a.destination;driver._BUDGET_LEDGER=None
    (a.destination/'manifests/budget_limits.json').write_text(json.dumps({
        'max_provider_attempts':1300,'observed_reported_total_tokens_stop':200000,
        'max_actual_utf8_request_bytes':131072,'scope':'local SDK fixtures only, actual paid requests=0'}))
    source_before=driver._source_tree_sha256(driver.ROOT)
    source_hashes={path.name:sha(path) for path in Path(__file__).parent.glob('*.py')}
    select={'service':(1,3,5),'migration':(2,4,6),'resources':(4,6),'network_permissions':(2,5)}
    public=[r for r in driver.cases() if r['split']=='pilot' and r['observations']['checkpoint'] in select[r['domain']]]
    assert len(public)==10
    if a.smoke:public=public[:1]
    stores={};pairs=[];bodies=[];all_results=[];failure=None;started=time.perf_counter()
    output=(a.destination/'paired_overhead_units.jsonl').open('x')
    try:
        for processes,arms,repeats in ((False,('SS1',) if a.smoke else tuple(driver.ARMS),1 if a.smoke else 3),
                                        (True,('SS1',) if a.smoke else ('S0','SS0','SS1'),1)):
            for repeat in range(repeats):
                for index,row in enumerate(public):
                    for arm in arms:
                        paired={}
                        order=(True,False) if (index+repeat)%2==0 else (False,True)
                        for instrument in order:
                            identity=f"sdk-overhead/{a.destination.name}/{row['task_id']}/{arm}/{processes}/{repeat}/{instrument}"
                            run_id=uuid.uuid5(uuid.NAMESPACE_URL,identity).hex
                            unit={'run_id':run_id,'unit_id':run_id,'phase':'public_sdk_calibration',
                                  'task_id':row['task_id'],'chain_id':row['chain_id'],'domain':row['domain'],'difficulty':row['difficulty'],
                                  'arm':arm,'repeat':repeat,'model':'main','processes':processes,'intervention':None,
                                  'effective_contract_sha256':driver.EFFECTIVE_CONTRACT['effective_contract_sha256'],
                                  'calibration_only':True}
                            memory=None
                            if driver.ARMS[arm][2]:
                                key=(row['chain_id'],arm,processes,repeat,instrument)
                                if key not in stores:
                                    cfg=driver.configuration(offline=True)
                                    cfg=cfg.with_overrides(embedding=replace(cfg.embedding,dim=256))
                                    stores[key]=MemoryStore(build_embedding(cfg))
                                memory=stores[key]
                            def factory(cfg):
                                def handler(request):
                                    body=json.loads(request.content)
                                    assert body['model']==cfg.llm.model and body['temperature']==0 and body['max_tokens']==1500
                                    assert body['enable_thinking'] is False
                                    assert len(request.content)<=131072
                                    assert request.headers['Idempotency-Key']==run_id
                                    bodies.append({'run_id':run_id,'request_id':request.headers['X-Client-Request-Id'],
                                                   'bytes':len(request.content),'sha256':hashlib.sha256(request.content).hexdigest()})
                                    return httpx.Response(200,json={
                                        'id':'local-'+run_id,'object':'chat.completion','created':1792458000,'model':cfg.llm.model,
                                        'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(row['expected'])},'finish_reason':'stop'}],
                                        'usage':{'prompt_tokens':42,'completion_tokens':18,'total_tokens':60,
                                                 'prompt_tokens_details':{'cached_tokens':4},
                                                 'completion_tokens_details':{'reasoning_tokens':0}}})
                                return OpenAI(api_key='offline',base_url=cfg.llm.base_url,max_retries=0,
                                              http_client=httpx.Client(transport=httpx.MockTransport(handler),trust_env=False))
                            result=driver.execute(unit,row,memory,instrument=instrument,_sdk_fixture_factory=factory)
                            all_results.append(result);paired[instrument]=result
                            assert result['success'],(run_id,result.get('error'))
                            assert result['provider_attempts']==1 and not result['provider_usage_complete']
                            assert result['model_evidence']=='offline_sdk_transport_fixture_not_provider'
                            assert result['provider_input_tokens'] is None and result['provider_total_tokens'] is None
                            assert not result['workers_remaining'] and not result['shm_names_created_but_remaining']
                            usage=result['fixture_usage_for_calibration'];assert len(usage)==1
                            assert (usage[0]['prompt_tokens'],usage[0]['completion_tokens'],usage[0]['total_tokens'])==(42,18,60)
                            assert usage[0]['prompt_tokens_details']['cached_tokens']==4
                            if instrument:assert result['ipc_accounting']['framing_consumption_conserved']
                        on,off=paired[True],paired[False]
                        assert on['outputs']==off['outputs'] and on['quality']==off['quality']
                        for field in ('llm_calls','tool_calls','message_count'):
                            if field in on['metrics']:assert on['metrics'][field]==off['metrics'][field],field
                        entry={'task_id':row['task_id'],'chain_id':row['chain_id'],'domain':row['domain'],'arm':arm,
                               'processes':processes,'repeat':repeat,'on_run_id':on['run_id'],'off_run_id':off['run_id'],
                               'on_wall_sec':on['wall_sec'],'off_wall_sec':off['wall_sec'],
                               'increment_sec':on['wall_sec']-off['wall_sec'],'outputs_equal':True,
                               'native_work_counters_equal':True,'usage_fixture_only':True}
                        pairs.append(entry);output.write(json.dumps(entry)+'\n');output.flush()
        calls=[json.loads(s) for s in (a.destination/'raw/provider_calls.jsonl').read_text().splitlines()]
        preflight=[json.loads(s) for s in (a.destination/'raw/http_request_preflight.jsonl').read_text().splitlines()]
        starts=[json.loads(s) for s in (a.destination/'raw/provider_attempt_starts.jsonl').read_text().splitlines()]
        enabled={r['run_id'] for r in all_results if r['instrumentation_enabled']}
        assert len(calls)==len(preflight)==len(starts)==len(enabled)
        actual={r['request_id']:r for r in bodies}
        assert len(actual)==len(all_results)
        for row in calls:
            assert row['usage_source']=='offline_sdk_transport_fixture' and row['status']=='OK'
            assert row['business_facts_equal'] and row['actual_request_utf8_bytes']==actual[row['request_id']]['bytes']
            assert row['actual_http_body_sha256']==actual[row['request_id']]['sha256']
        assert {r['request_id'] for r in calls}=={r['request_id'] for r in starts}=={r['request_id'] for r in preflight}
        assert driver._source_tree_sha256(driver.ROOT)==source_before
        summary=[]
        for processes in (False,True):
            for arm in sorted({r['arm'] for r in pairs if r['processes']==processes}):
                group=[r for r in pairs if r['processes']==processes and r['arm']==arm]
                summary.append({'processes':processes,'arm':arm,'paired_observations':len(group),
                                'independent_public_tasks':len({r['task_id'] for r in group}),
                                'mean_on_sec':statistics.mean(r['on_wall_sec'] for r in group),
                                'mean_off_sec':statistics.mean(r['off_wall_sec'] for r in group),
                                'mean_increment_sec':statistics.mean(r['increment_sec'] for r in group),
                                'min_increment_sec':min(r['increment_sec'] for r in group),
                                'max_increment_sec':max(r['increment_sec'] for r in group)})
        report={'status':'PASS_LOCAL_SDK_USAGE_CONTRACT_AND_PAIRED_RECORDING_OVERHEAD',
                'public_tasks':len(public),'task_units':len(all_results),'paired_observations':len(pairs),
                'local_http_fixture_responses':len(bodies),'paid_api_calls':0,'real_provider_usage_calibrated':False,
                'request_preflight_start_end_and_body_sha_join':'PASS','outputs_and_work_counters_equal':'PASS',
                'summary':summary,'business_source_before_after_sha256':source_before,'source_sha256':source_hashes,
                'overhead_scope':'incremental experiment Recorder/SocketObserver/ResourceSampler/per-URI/index hooks including local SDK durable logging; native runtime counters, event journal and mandatory fixture validation remain common',
                'limits':['local response service has no real network/provider wait; this is not end-to-end model timing',
                          'whole task means include setup, local SDK construction and cleanup; no overhead subtraction from later formal units',
                          'repeated pairs are not new independent cases; raw paired distributions retained; no confirmatory test added'],
                'wall_sec':time.perf_counter()-started,
                'raw_sha256':{path.name:sha(path) for path in (a.destination/'raw').glob('*') if path.is_file()}}
    except BaseException as exc:
        failure=exc;report={'status':'FAIL_RETAINED','finished_units':len(all_results),'error_type':type(exc).__name__,
                            'error':str(exc),'paid_api_calls':0,'source_sha256':source_hashes}
    finally:
        output.close()
        for store in stores.values():store.close()
    (a.destination/'metric_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_sha256','raw_sha256','summary')},ensure_ascii=False))
    if failure:raise failure


if __name__=='__main__':main()
