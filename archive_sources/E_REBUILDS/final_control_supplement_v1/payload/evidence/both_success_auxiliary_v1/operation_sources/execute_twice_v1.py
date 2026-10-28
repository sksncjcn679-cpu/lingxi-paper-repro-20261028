"""Actual postrun execution of original-protocol 6.5 descriptive supplement.

Retains all earlier evidence. No model calls, primary-statistic edits or new tests.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path('F:/codex/多agent系统架构-软件学报')
STUDY = ROOT/'paper_experiments_20261019_pilot/protocol_repair_20261020_v1'
POST = ROOT/'deployment/protocol_repair_v1/postrun_analysis'
BASE = Path('E:/codex_rebuilds/both_success_auxiliary_v1')
IMAGE = 'sha256:80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58'
SOURCE_SHA = 'dc3c8e4afb91ab991f3c0467a6801e9971525126f798cf84ea527c3cf514df97'
GUARD_SHA = '7a82d8b4263b127b938958bc6bca74d8db50f0517e2654e94547bd4c67f77e2d'
FILES = {
 'observations': ('analysis/full_metric_audit_v3/audited_observations.jsonl', 'audited_observations.jsonl', '1c2031eec1ad922c753199fc4cc0a7760fdeca0f110becc6d2e83db0dc12281a',45655316),
 'primary': ('analysis/stats/formal_primary_v1.json','formal_primary_v1.json','5a05487490072f1989a02b2bd99f39c1bfa07acba0f31af96a8579e73ddfaa2a',7269836),
 'kernel': ('analysis/stats/preformal_gate_v1/sources/formal_statistics.py','formal_statistics.py','7e201b28e8a85e712cdace33951e900ad47e6c8dcd8f4554763cccd69870e373',16535),
 'contract': ('analysis/stats/preformal_gate_v1/statistical_contract.json','statistical_contract.json','5ec70ab8b2d87fd248f92a13ff604bb2cd449b4b99e40e38aff6afd3a43bbbe8',7948),
 'formal_plan': ('manifests/formal_plan.json','formal_plan.json','3944e9ae3b5de942dd6e463d7b241fb3d1b3f363415249a52b893ad1b2d3a159',5311350),
 'claims_pair_index': ('analysis/claims_raw_index_v1/primary_pair_raw_support_index.csv','primary_pair_raw_support_index.csv','efb1fb78b4e09c3b05c932c0676da9f686c9e3c47fa7ee8346dc4700db1f7133',1438330),
 'original_protocol': ('manifests/original_protocol.md','original_protocol.md','ca5602a6f8fcb852f5824bf6c4fd7e71074b280c35ba5705676806d17d593843',33970)
}

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()

def write(path,obj):
 with path.open('x',encoding='utf-8',newline='\n') as f:
  json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False); f.write('\n')

def main():
 assert not BASE.exists(), 'fresh directory required'
 source=ROOT/'.local/both_success_auxiliary_v1/both_success_auxiliary_v1.py'
 guard=POST/'offline_analysis_runner.py'
 assert sha(source)==SOURCE_SHA and sha(guard)==GUARD_SHA
 for relative,_,expected,size in FILES.values():
  p=STUDY/relative
  assert p.stat().st_size==size and sha(p)==expected
 BASE.mkdir(); inp=BASE/'input'; ctrl=BASE/'control'; inp.mkdir();ctrl.mkdir()
 manifest={'schema_version':'both-success-auxiliary-inputs-v1','study_id':STUDY.name,
  'adapter_source_sha256':SOURCE_SHA,'files':{}}
 for key,(relative,name,expected,size) in FILES.items():
  shutil.copyfile(STUDY/relative,inp/name); assert sha(inp/name)==expected
  manifest['files'][key]={'path':'/input/'+name,'sha256':expected,'bytes':size}
 write(inp/'inputs.json',manifest)
 shutil.copyfile(source,ctrl/source.name);shutil.copyfile(guard,ctrl/guard.name)
 config={'script':'/control/'+source.name,'script_sha256':SOURCE_SHA,
  'arguments':['--inputs','/input/inputs.json','--out','/output/analysis'],
  'approved_offline_sources':{'/control/'+source.name:SOURCE_SHA},
  'guard_result':'/output/guard_result.json','guard_events':'/output/guard_events.jsonl',
  'protected_inputs':['/input/'+p.name for p in inp.iterdir()]+['/control/'+source.name,'/control/'+guard.name,'/control/guard_configuration.json'],
  'writable_roots':['/output','/tmp']}
 write(ctrl/'guard_configuration.json',config)
 write(BASE/'execution_scope.json',{'status':'REVIEWED_OPERATIONAL_POSTRUN_SUPPLEMENT_NOT_PROSPECTIVE_FREEZE',
  'original_protocol_section':'6.5','input_manifest_sha256':sha(inp/'inputs.json'),
  'adapter_sha256':SOURCE_SHA,'guard_sha256':GUARD_SHA,'image':IMAGE,
  'review':'Root read full adapter and frozen _chain_arrays; selects both recorded successes within frozen populations; copies20 old dictionaries; only5 descriptive token rows; whole subset unknown policy; no new CI/p/Holm.',
  'original_primary_or_B8_241_identity_modified':False,'new_model_calls_or_benchmarks':False})
 runs=[]
 for name in ('reference','independent_rebuild'):
  output=BASE/name;output.mkdir()
  command=['docker','run','--name','lingxi-both-success-aux-v1-'+name.replace('_','-'),
   '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
   '--cpus','2','--memory','2g','--pids-limit','512','--tmpfs','/tmp:rw,noexec,nosuid,size=128m',
   '--env','PYTHONDONTWRITEBYTECODE=1','--mount',f'type=bind,source={inp.as_posix()},target=/input,readonly',
   '--mount',f'type=bind,source={ctrl.as_posix()},target=/control,readonly',
   '--mount',f'type=bind,source={output.as_posix()},target=/output',
   '--entrypoint','/opt/lingxi/formal-venv/bin/python',IMAGE,'-X','utf8','-B',
   '/control/offline_analysis_runner.py','/control/guard_configuration.json']
  start=datetime.now(timezone.utc).isoformat()
  with (output/'stdout.log').open('x',encoding='utf-8') as out,(output/'stderr.log').open('x',encoding='utf-8') as err:
   done=subprocess.run(command,stdout=out,stderr=err,check=False)
  receipt={'started_utc':start,'finished_utc':datetime.now(timezone.utc).isoformat(),
   'actual_exit_code':done.returncode,'command':command,'image':IMAGE,
   'stdout_sha256':sha(output/'stdout.log'),'stderr_sha256':sha(output/'stderr.log')}
  write(output/'execution_receipt.json',receipt)
  print(json.dumps({'run':name,'actual_exit_code':done.returncode}),flush=True)
  assert done.returncode==0, 'retain failed evidence and stop'
  g=json.loads((output/'guard_result.json').read_text(encoding='utf-8'))
  assert g['denied_count']==0 and g['exit_code']==0
  runs.append({'name':name,'execution_receipt_sha256':sha(output/'execution_receipt.json'),
   'guard_receipt_sha256':sha(output/'guard_result.json'),'guard':g})
 names=['results.json','summary.csv','append_summary.md','feishu.xml']
 identities=[]
 for name in names:
  a=BASE/'reference/analysis'/name;b=BASE/'independent_rebuild/analysis'/name
  assert a.read_bytes()==b.read_bytes()
  identities.append({'path':name,'bytes':a.stat().st_size,'reference_sha256':sha(a),'independent_sha256':sha(b),'exact_bytes_equal':True})
 for key,(_,name,expected,size) in FILES.items():
  assert (inp/name).stat().st_size==size and sha(inp/name)==expected
 assert sha(ctrl/source.name)==SOURCE_SHA and sha(ctrl/guard.name)==GUARD_SHA
 write(BASE/'exact_identity_receipt.json',{'status':'ACTUAL_TWO_INDEPENDENT_OFFLINE_RUNS_EXACT_FOUR_FILE_IDENTITY',
  'runs':runs,'files':identities,'separate_from_original_B8_241_record_identity':True,
  'input_manifest_sha256':sha(inp/'inputs.json'),'image':IMAGE,
  'original_inputs_or_primary_modified':False,'new_CI_p_Holm_or_family':False,
  'provider_calls_or_benchmark_queries':0})
 print(json.dumps({'status':'ACTUAL_TWO_INDEPENDENT_OFFLINE_RUNS_EXACT_FOUR_FILE_IDENTITY','base':str(BASE)}),flush=True)

if __name__=='__main__': main()
