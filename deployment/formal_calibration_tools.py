"""Public-only recorder calibration; never enters the formal quality denominator."""
from pathlib import Path
import argparse
import asyncio
import json
import time
import uuid
import sys
sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
from agent_runtime.runtime.builder import build_context,build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.providers.base import LLMResponse
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding
from formal_fact_calibration import make_task,ARMS
from formal_fixture_checks import check
from formal_prompt_adapter import model_prompt
from formal_recording import Recorder,sha
import formal_real_driver as driver

OUT=driver.OUT


def archive_development():
    destination=OUT/'calibration/recording_development_v1/raw'
    destination.mkdir(parents=True,exist_ok=True)
    names=['dry_task_runs.jsonl','messages.jsonl','states.jsonl','memory_events.jsonl',
           'resource_samples.jsonl','ipc_socket_events.jsonl','ipc_accounting.jsonl','spans.jsonl',
           'execution_events.jsonl','dry_prompts.jsonl']
    moved=[]
    if (OUT/'raw/provider_attempt_starts.jsonl').exists():
        raise RuntimeError('paid recording has begun; development archive cannot relocate files')
    for name in names:
        source=OUT/'raw'/name
        if source.exists():
            target=destination/name
            if target.exists():raise RuntimeError('never overwrite previous development evidence')
            digest=sha(source.read_bytes())
            source.rename(target)
            moved.append({'name':name,'sha256':digest})
    (destination.parent/'archive_manifest.json').write_text(json.dumps({'scope':'initial six synthetic recorder-development units; no provider calls',
        'files_moved_without_modifying_bytes':moved},indent=2))
    print(json.dumps({'archived_development_files':len(moved)}))


def process_dry():
    rows=[r for r in driver.cases() if r['split']=='pilot' and r['observations']['checkpoint']<=2]
    assert len(rows)==8
    records=[];stores={}
    try:
        for row in rows:
            for arm in ['S0','SS0','SS1']:
                unit={'phase':'public_process_calibration','task_id':row['task_id'],
                    'chain_id':row['chain_id'],'domain':row['domain'],'difficulty':row['difficulty'],
                    'arm':arm,'repeat':0,'model':'main','processes':True,'topology':'agent_processes_uds','intervention':None}
                unit['run_id']=unit['unit_id']='process-dry-'+uuid.uuid5(uuid.NAMESPACE_URL,json.dumps(unit,sort_keys=True)).hex
                memory=None
                if arm=='SS1':
                    key=(arm,row['chain_id'])
                    if key not in stores:stores[key]=MemoryStore(build_embedding(driver.configuration()),bm25_rerank=True)
                    memory=stores[key]
                records.append(driver.execute(unit,row,memory,dry=True))
    finally:
        for memory in stores.values():memory.close()
    failures=[{'run_id':r['run_id'],'error':r.get('error')} for r in records if not r['success'] or r.get('workers_remaining') or r.get('shm_names_created_but_remaining')]
    report={'scope':'public deterministic fixture; not provider/model evidence','units':len(records),
        'successes':sum(r['success'] for r in records),'failures':failures,
        'ipc_conservation_all':all(r.get('ipc_accounting',{}).get('framing_consumption_conserved') for r in records),
        'rpc_boundary_recorded_all':all('agent_host_rpc' in r.get('ipc_accounting',{}).get('by_boundary',{}) for r in records),
        'state_consumer_path':'parent runtime read, ndarray by value via worker RPC; worker computes ranking'}
    (OUT/'analysis/process_recording_calibration.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
    if failures or not report['ipc_conservation_all'] or not report['rpc_boundary_recorded_all']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['archive-development','process-dry'])
    args=parser.parse_args()
    archive_development() if args.command=='archive-development' else process_dry()
