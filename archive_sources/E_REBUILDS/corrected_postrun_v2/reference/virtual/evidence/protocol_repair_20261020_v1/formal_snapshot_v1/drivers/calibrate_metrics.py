"""Unpaid openEuler measurement checks. Public inputs only, no Internet sockets."""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
sys.path.append('/drivers')
import real_driver as driver
from calibrate_contract import deny_internet
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--smoke',action='store_true');a=p.parse_args()
    if a.destination.exists():raise RuntimeError('new measurement calibration destination required')
    a.destination.mkdir(parents=True);sys.addaudithook(deny_internet)
    driver.OUT=a.destination
    source_before=driver._source_tree_sha256(driver.ROOT)
    source_hashes={path.name:sha(path) for path in Path(__file__).parent.glob('*.py')}
    candidates=driver.cases();select={'service':(1,3,5),'migration':(2,4,6),'resources':(4,6),'network_permissions':(2,5)}
    public=[r for r in candidates if r['split']=='pilot' and r['observations']['checkpoint'] in select[r['domain']]]
    assert len(public)==10
    if a.smoke:public=public[:1]
    results=[];stores={};failure=None;started=time.perf_counter()
    try:
        for processes,arms in ((False,('SS1',) if a.smoke else tuple(driver.ARMS)),(True,('SS1',) if a.smoke else ('S0','SS0','SS1'))):
            for row in public:
                for arm in arms:
                    identity=f"metric-calibration/{a.destination.name}/{row['task_id']}/{arm}/{processes}"
                    run_id=uuid.uuid5(uuid.NAMESPACE_URL,identity).hex
                    unit={'run_id':run_id,'unit_id':run_id,'phase':'public_metric_calibration',
                          'task_id':row['task_id'],'chain_id':row['chain_id'],'domain':row['domain'],'difficulty':row['difficulty'],
                          'arm':arm,'repeat':0,'model':'main','processes':processes,'intervention':None,
                          'effective_contract_sha256':driver.EFFECTIVE_CONTRACT['effective_contract_sha256'],
                          'calibration_only':True}
                    memory=None
                    if driver.ARMS[arm][2]:
                        key=(row['chain_id'],arm,processes)
                        if key not in stores:
                            cfg=driver.configuration(offline=True)
                            cfg=cfg.with_overrides(embedding=replace(cfg.embedding,dim=256))
                            stores[key]=MemoryStore(build_embedding(cfg))
                        memory=stores[key]
                    result=driver.execute(unit,row,memory,dry=True)
                    results.append(result)
                    assert result['success'],(run_id,result.get('error'))
                    assert result['ipc_accounting']['framing_consumption_conserved']
                    assert not result['workers_remaining'] and not result['shm_names_created_but_remaining']
        raw=a.destination/'calibration/recorder_dry_v1/raw'
        states=[json.loads(line) for line in (raw/'states.jsonl').read_text().splitlines()]
        memory_events=[json.loads(line) for line in (raw/'memory_events.jsonl').read_text().splitlines()]
        index=[r for r in memory_events if r['event']=='index_operation']
        produced=[r for r in states if r['event']=='state_produce' and r.get('reference',{}).get('kind')=='plan']
        reads=[r for r in states if r['event']=='state_consumer_compute']
        maps=[r for r in states if r['event']=='state_os_mapping']
        handoffs=[r for r in states if r['event']=='state_rpc_handoff']
        assert produced and reads and maps and index and handoffs
        for result in results:
            rows=[r for r in states if r['run_id']==result['run_id']]
            if result['state_exchange_enabled']:
                refs=[r for r in rows if r['event']=='state_produce' and r.get('reference',{}).get('kind')=='plan']
                assert refs and all(r['reference']['content_hash'] for r in refs)
                for item in refs:
                    assert any(r['event']=='state_consumer_compute' and r.get('uri')==item['uri'] for r in rows)
                    assert any(r['event']=='state_buffer_release' and r.get('uri')==item['uri'] and r['buffer_key_absent'] for r in rows)
                if result['processes']:
                    transfers=[r for r in rows if r['event']=='state_rpc_handoff']
                    assert transfers and all(r['reply_framed_socket_bytes']==r['reply_payload_bytes']+4 for r in transfers)
                    assert sum(r['reply_framed_socket_bytes'] for r in transfers)<=result['observable_ipc_bytes']
                    assert all(r['consumer_pid'] in result['worker_pids'].values() for r in transfers)
            assert all(r.get('end_ns',0)>=r.get('start_ns',0) for r in rows if 'end_ns' in r and 'start_ns' in r)
        identity=[json.loads(line) for line in (raw/'observer_identity.jsonl').read_text().splitlines()]
        assert all(r['specification']['consumer_clone']['stripped_ast_identical'] for r in identity)
        assert all(r['specification']['mapping_probe']['constructor_code_object_identical'] for r in identity)
        counts=Counter(r['event'] for r in states)
        assert driver._source_tree_sha256(driver.ROOT)==source_before
        report={'status':'PASS_URI_INDEX_CORRECTNESS_NOT_PROVIDER_OR_OVERHEAD_CALIBRATION',
                'public_tasks':len(public),'units':len(results),'multiprocess_units':sum(r['processes'] for r in results),
                'state_event_counts':dict(counts),'index_operations':len(index),'paid_api_calls':0,
                'native_consumer_ast_strips_to_original':True,'native_sharedmemory_constructor_code_identical':True,
                'state_per_uri_produce_consume_release_join':'PASS','framing_conservation':'PASS',
                'business_source_before_after_sha256':source_before,'helper_sha256':source_hashes,
                'wall_sec':time.perf_counter()-started,'overhead_measured':False,
                'fused_read_compute':'actual native matmul includes reading/casting; no pure-read number fabricated',
                'raw_sha256':{path.name:sha(path) for path in raw.glob('*') if path.is_file()}}
    except BaseException as exc:
        failure=exc;report={'status':'FAIL_RETAINED','finished_units':len(results),'error_type':type(exc).__name__,
                            'error':str(exc),'paid_api_calls':0,'helper_sha256':source_hashes}
    finally:
        for store in stores.values():store.close()
    (a.destination/'metric_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('helper_sha256','raw_sha256')},ensure_ascii=False))
    if failure:raise failure


if __name__=='__main__':main()
