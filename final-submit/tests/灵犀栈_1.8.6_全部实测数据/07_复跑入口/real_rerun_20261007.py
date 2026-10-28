from pathlib import Path
import os, sys, json, subprocess, platform, time, tarfile

ROOT=Path('/tmp/agent-runtime-OS-SOTA1.8.6-exp')
OUT=Path('/home/xk/real_186_20261007')
OUT.mkdir(exist_ok=True)
private=json.loads(Path('/home/xk/.version_proof_private.json').read_text())
env={**os.environ,**private,'PYTHONPATH':str(ROOT/'src'),'AGENT_PROJECT_ROOT':str(ROOT),
     'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','AGENT_LLM_PROVIDER':'openai'}
sys.path.insert(0,str(ROOT/'src'))
from agent_runtime.evaluation.runner import _source_tree_sha256
status={'source_sha':_source_tree_sha256(ROOT),'completed':[],'running':None}
def save(): (OUT/'progress.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))
rows=[]
for arm in ('T','S','SS'):
 status['running']='advisory.'+arm; save()
 p=subprocess.run([sys.executable,'/home/xk/version_proof_1718b_20261005/probe.py','real_advisory',arm,'0'],cwd=OUT,env=env,capture_output=True,text=True,timeout=600)
 item={'experiment':'advisory','arm':arm,'returncode':p.returncode}
 if p.returncode==0:
  row=json.loads(p.stdout.strip().splitlines()[-1]); row['source_tree_sha256']=status['source_sha']
  (OUT/(arm+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)); rows.append(row)
 else:
  # Export only status codes and exception class names, never provider exceptions or credentials.
  import re
  item['exception_classes']=re.findall(r'([A-Za-z]+Error):',p.stderr)[-3:]
 status['completed'].append(item); save()
 if not rows and p.returncode:
  status['blocked']='first real advisory request failed'; break
summary={'source_sha':status['source_sha'],'platform':platform.platform(),'scope':'real advisory T/S/SS, one repeat per arm; not a model decision quality claim','arms':[]}
for r in rows:
 summary['arms'].append({'arm':r['arm'],'model':r['model'],'calls':len(r['calls']),
 'provider_usage_calls':sum(c['provider_usage_present'] is True for c in r['calls']),
 'prompt_tokens':sum(c['prompt_tokens'] for c in r['calls']),
 'completion_tokens':sum(c['completion_tokens'] for c in r['calls']),
 'metrics':r['row']})
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
if len(rows)==3:
 status['running']='consumed_decisions'; save()
 # Real outputs enter executor decisions; four arms, same eight related incidents.
 with (OUT/'decision.log').open('w') as log:
  p=subprocess.run([sys.executable,str(ROOT/'scripts/rt.py'),'decision-workload','--real','--repeat','1','--checkpoint',str(OUT/'decision-checkpoint.jsonl')],cwd=OUT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
 status['completed'].append({'experiment':'consumed_decisions','returncode':p.returncode})
status['running']=None; status['finished']=True; save()
with tarfile.open('/tmp/real_186_20261007.tar.gz','w:gz') as tar: tar.add(OUT,arcname=OUT.name)
