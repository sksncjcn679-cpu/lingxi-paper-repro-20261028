from pathlib import Path
import os, sys, json, time, tarfile, traceback
ROOT=Path('/tmp/agent-runtime-OS-SOTA1.8.6-exp')
session_audit='--session-audit' in sys.argv
OUT=Path('/home/xk/external_session186_20261007' if session_audit else '/home/xk/external_full186_20261007'); OUT.mkdir(exist_ok=True)
private=json.loads(Path('/home/xk/.version_proof_private.json').read_text())
os.environ.update(private)
os.environ.update(AGENT_LLM_PROVIDER='openai',PYTHONPATH=str(ROOT/'src'),AGENT_PROJECT_ROOT=str(ROOT),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
sys.path.insert(0,str(ROOT/'src')); os.chdir(OUT)
from agent_runtime.evaluation.runner import _source_tree_sha256
status={'source_sha':_source_tree_sha256(ROOT),'scope':'22 external LLM variants, 1 repeat each; single-repeat effects are descriptive','running':True}
def save(): (OUT/'progress.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))
save()
import agent_runtime.runtime.builder as builder
original=builder.build_llm
class Recorder:
 def __init__(self,p):
  self.p=p; self.name=p.name; self.model=p.model; self.usage_present=None
  if hasattr(p,'_create_negotiated'):
   old=p._create_negotiated
   def observed(*a,**kw):
    response=old(*a,**kw); self.usage_present=response.usage is not None; return response
   p._create_negotiated=observed
 def __getattr__(self,key): return getattr(self.p,key)
 def complete(self,prompt,**kwargs):
  start=time.perf_counter()
  r=self.p.complete(prompt,**kwargs)
  with (OUT/'provider_calls.jsonl').open('a') as f:
   f.write(json.dumps({'model':r.model,'provider_usage_present':self.usage_present,'prompt_tokens':r.prompt_tokens,'completion_tokens':r.completion_tokens,'latency_sec':time.perf_counter()-start})+'\n')
  return r
builder.build_llm=lambda cfg:Recorder(original(cfg))
import agent_runtime.providers.factory as factory
factory.build_llm=builder.build_llm
try:
 from agent_runtime.evaluation.external_llm_benchmark import run_external_llm_benchmark,write_external_llm_results
 def progress(v): status['last_progress']=str(v); save()
 variants=['T_coalesce','T_memory_pair_coalesce','SS_coalesce','FULL_coalesce','T_group','SS_group','FULL_group','T_related_group','FULL_related_group','T_group_sv','SS_group_sv','FULL_group_sv'] if session_audit else None
 if session_audit: status['scope']='12 coalesced/session variants, 1 repeat each; provider usage instrumented at factory and builder'; save()
 payload=run_external_llm_benchmark(repeat=1,allow_mock=False,variants=variants,checkpoint=OUT/'checkpoint.jsonl',order='balanced',progress=progress)
 out=write_external_llm_results(payload)
 status['result_dir']=str(out); status['finished']=True
except Exception as e:
 status['failure_type']=type(e).__name__; status['finished']=True
status['running']=False; save()
with tarfile.open('/tmp/'+OUT.name+'.tar.gz','w:gz') as tar: tar.add(OUT,arcname=OUT.name)
