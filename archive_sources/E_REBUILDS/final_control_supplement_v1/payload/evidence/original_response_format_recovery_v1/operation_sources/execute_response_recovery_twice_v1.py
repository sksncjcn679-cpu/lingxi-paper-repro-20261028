"""Execute reviewed original-response format projection, twice, offline only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path('F:/codex/多agent系统架构-软件学报')
NEW=ROOT/'paper_experiments_20261019_pilot/protocol_repair_20261020_v1'
POST=ROOT/'deployment/protocol_repair_v1/postrun_analysis'
SRC=NEW/'analysis/original_response_format_recovery_v1/source'
B8=Path('E:/codex_rebuilds/corrected_postrun_v2')
BASE=Path('E:/codex_rebuilds/original_response_format_recovery_v1')
IMAGE='sha256:80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58'
ADAPTER='recover_original_response_format_v1.py'
ADAPTER_SHA='96c0d206ea09ebf7f03849a8e521c692bf148b3fe6cd1133747b4ace32f93754'
GUARD_SHA='7a82d8b4263b127b938958bc6bca74d8db50f0517e2654e94547bd4c67f77e2d'
RUNTIME=B8/'archive/virtual/opt/lingxi/formal-checkout/src/agent_runtime'
DRIVERS=NEW/'formal_snapshot_v1/drivers'
SOURCES={
 'model_io':NEW/'raw/model_io.jsonl',
 'provider_calls':NEW/'raw/provider_calls.jsonl',
 'observations':NEW/'analysis/full_metric_audit_v3/audited_observations.jsonl',
 'formal_plan':NEW/'manifests/formal_plan.json',
 'native_configurations':NEW/'manifests/native_configurations.json',
 'decision_source':RUNTIME/'agents/model_decision.py',
 'contract_source':DRIVERS/'contract.py',
 'bootstrap_source':DRIVERS/'bootstrap.py',
 'real_driver_source':DRIVERS/'real_driver.py',
 'external_runner_source':DRIVERS/'external_runner.py',
 'recorder_source':DRIVERS/'formal_recording.py',
 'external_adapter_source':B8/'archive/virtual/drivers/formal_external_adapters.py',
 'executor_source':RUNTIME/'agents/codeact_executor.py',
 'context_source':RUNTIME/'runtime/context.py',
 'gateway_source':RUNTIME/'runtime/llm_gateway.py',
 'provider_source':RUNTIME/'providers/openai_provider.py'
}

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(path,value):
 with path.open('x',encoding='utf-8',newline='\n') as f:
  json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')

def main():
 assert not BASE.exists(),'fresh directory required'
 manifest=read(SRC/'inputs.template.json')
 assert manifest['adapter_source_sha256']==sha(SRC/ADAPTER)==ADAPTER_SHA
 assert sha(POST/'offline_analysis_runner.py')==GUARD_SHA
 assert set(manifest['files'])==set(SOURCES)
 for key,source in SOURCES.items():
  pin=manifest['files'][key]
  assert source.stat().st_size==pin['bytes'] and sha(source)==pin['sha256'],key
 BASE.mkdir();control=BASE/'control';inputs=BASE/'input';control.mkdir();inputs.mkdir()
 for key,source in SOURCES.items():
  name=Path(manifest['files'][key]['path']).name
  shutil.copyfile(source,inputs/name)
  assert sha(inputs/name)==manifest['files'][key]['sha256']
 shutil.copyfile(SRC/'inputs.template.json',inputs/'inputs.json')
 shutil.copyfile(SRC/ADAPTER,control/ADAPTER)
 shutil.copyfile(POST/'offline_analysis_runner.py',control/'offline_analysis_runner.py')
 config={'script':'/control/'+ADAPTER,'script_sha256':ADAPTER_SHA,
  'arguments':['--inputs','/input/inputs.json','--out','/output/analysis'],
  'approved_offline_sources':{'/control/'+ADAPTER:ADAPTER_SHA},
  'guard_result':'/output/guard_result.json','guard_events':'/output/guard_events.jsonl',
  'protected_inputs':['/input/'+p.name for p in inputs.iterdir()]+['/control/'+ADAPTER,'/control/offline_analysis_runner.py','/control/guard_configuration.json'],
  'writable_roots':['/output','/tmp']}
 save(control/'guard_configuration.json',config)
 save(BASE/'execution_scope.json',{'status':'REVIEWED_POSTRUN_ORIGINAL_METRIC_RECOVERY_NOT_PROSPECTIVE_FREEZE',
  'original_requirement':'protocol5.1 M002 original JSON decoding/required-field format',
  'adapter_source_sha256':ADAPTER_SHA,'input_manifest_sha256':sha(inputs/'inputs.json'),
  'guard_sha256':GUARD_SHA,'image':IMAGE,
  'root_review':'Full adapter read. Original pure parser AST and exact normalization prefix, installed24, independent memberships, default decoder semantics preserved. Request bodies not JSON-decoded; response text transient only; metadata-only four outputs. Actual-response, attempt and planned-task denominators retained, missing/multiple responses unknown. No new oracle scoring, method, CI, p, Holm, primary edits or SDK import.',
  'responses_read_only_in_isolated_analysis':True,'request_or_response_text_emitted':False,
  'model_calls_or_benchmarks':0,'within_existing_B8_241_identity':False})
 runs=[]
 for name in ('reference','independent_rebuild'):
  out=BASE/name;out.mkdir()
  cmd=['docker','run','--pull','never','--name','lingxi-response-format-v1-'+name.replace('_','-'),
   '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
   '--cpus','2','--memory','2g','--pids-limit','512','--tmpfs','/tmp:rw,noexec,nosuid,size=128m',
   '--env','PYTHONDONTWRITEBYTECODE=1','--mount',f'type=bind,source={inputs.as_posix()},target=/input,readonly',
   '--mount',f'type=bind,source={control.as_posix()},target=/control,readonly',
   '--mount',f'type=bind,source={out.as_posix()},target=/output',
   '--entrypoint','/opt/lingxi/formal-venv/bin/python',IMAGE,'-X','utf8','-B',
   '/control/offline_analysis_runner.py','/control/guard_configuration.json']
  start=datetime.now(timezone.utc).isoformat()
  with (out/'stdout.log').open('x',encoding='utf-8') as stdout,(out/'stderr.log').open('x',encoding='utf-8') as stderr:
   done=subprocess.run(cmd,stdout=stdout,stderr=stderr,check=False)
  save(out/'execution_receipt.json',{'started_utc':start,'finished_utc':datetime.now(timezone.utc).isoformat(),
   'actual_exit_code':done.returncode,'command':cmd,'image':IMAGE,
   'stdout_sha256':sha(out/'stdout.log'),'stderr_sha256':sha(out/'stderr.log')})
  print(json.dumps({'run':name,'actual_exit_code':done.returncode}),flush=True)
  assert done.returncode==0,'retain failed output'
  guard=read(out/'guard_result.json')
  assert guard['denied_count']==guard['exit_code']==0
  runs.append({'name':name,'execution_receipt_sha256':sha(out/'execution_receipt.json'),
   'guard_receipt_sha256':sha(out/'guard_result.json'),'guard':guard})
 files=[]
 for name in ['response_format_flags.csv','summary.csv','results.json','append_summary.md']:
  a=BASE/'reference/analysis'/name;b=BASE/'independent_rebuild/analysis'/name
  assert a.read_bytes()==b.read_bytes()
  files.append({'path':name,'bytes':a.stat().st_size,'reference_sha256':sha(a),'independent_sha256':sha(b),'exact_bytes_equal':True})
 for key,item in manifest['files'].items():assert sha(inputs/Path(item['path']).name)==item['sha256']
 assert sha(control/ADAPTER)==ADAPTER_SHA and sha(control/'offline_analysis_runner.py')==GUARD_SHA
 save(BASE/'exact_identity_receipt.json',{'status':'ACTUAL_TWO_OFFLINE_RESPONSE_RECOVERY_RUNS_EXACT_FOUR_FILE_IDENTITY',
  'runs':runs,'files':files,'image':IMAGE,'adapter_sha256':ADAPTER_SHA,
  'input_manifest_sha256':sha(inputs/'inputs.json'),'request_or_response_text_emitted':False,
  'original_inputs_or_primary_modified':False,'within_existing_B8_241_identity':False,
  'new_quality_scores_CI_p_Holm_or_family':False,'model_calls_or_benchmarks':0})
 print(json.dumps({'status':'ACTUAL_TWO_OFFLINE_RESPONSE_RECOVERY_RUNS_EXACT_FOUR_FILE_IDENTITY','base':str(BASE)}),flush=True)

if __name__=='__main__':main()
