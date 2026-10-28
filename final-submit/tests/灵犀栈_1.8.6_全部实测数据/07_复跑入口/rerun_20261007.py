from pathlib import Path
import json, os, subprocess, sys, time, tarfile

ROOT = Path('/tmp/agent-runtime-OS-SOTA1.8.6-exp')
OUT = Path('/home/xk/verification_186_20261007')
OUT.mkdir(exist_ok=True)
env = dict(os.environ, PYTHONPATH=str(ROOT/'src'), AGENT_PROJECT_ROOT=str(ROOT),
           OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', AGENT_LLM_PROVIDER='mock')
commands = [
 ['version'], ['os-probe'], ['competition-check','--rounds','10','--require-openeuler'],
 ['contest-workload'], ['memory-efficiency','--repeat','10'],
 ['ipc-benchmark','--sizes','1024,3072,65536,1048576','--repeat','100','--warmup','10'],
 ['codec-benchmark','--repeat','10000'],
 ['state-quantization','--samples','200','--dim','768'],
 ['state-delta','--repeat','20'], ['stability-benchmark','--rounds','10'],
]
status = {'scope':'offline controlled fixtures; no live LLM API calls', 'completed':[], 'running':None}
def save():
 (OUT/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))
for cmd in commands:
 status['running']=cmd; save(); started=time.time()
 with (OUT/(cmd[0]+'.log')).open('w') as log:
  p=subprocess.run([sys.executable,str(ROOT/'scripts/rt.py'),*cmd],cwd=OUT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=900)
 status['completed'].append({'command':cmd,'returncode':p.returncode,'wall_sec':time.time()-started}); save()
status['running']=None; status['finished']=True; save()
with tarfile.open('/tmp/verification_186_20261007.tar.gz','w:gz') as tar:
 tar.add(OUT,arcname=OUT.name)
