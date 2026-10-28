#!/usr/bin/env python3
"""Verify this checkout's code on the current host, independently of presentation artifacts."""
from pathlib import Path
import argparse, json, os, subprocess, sys, time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from agent_runtime.evaluation.runner import _env_info, _write_checksum_manifest, _allocate_result_dir

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openeuler',action='store_true')
    args=parser.parse_args()
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'AGENT_PROJECT_ROOT':str(ROOT)}
    out=_allocate_result_dir(str(ROOT/'results'),'code_verification')
    steps=[]
    if args.openeuler:
        text=Path('/etc/os-release').read_text() if Path('/etc/os-release').exists() else ''
        import platform
        ok=all(s in text.lower() for s in ('openeuler','24.03','lts-sp3')) and platform.machine()=='x86_64'
        steps.append({'label':'target_os','exit_code':0 if ok else 1})
    commands=[('source_identity',[sys.executable,'scripts/verify_source_identity.py']),
              ('compile',[sys.executable,'-m','compileall','-q','src','scenarios']),
              ('tests',[sys.executable,'-m','pytest','-q','-o','addopts=']),
              ('demo',[sys.executable,'scripts/rt.py','demo']),
              ('contest_workload',[sys.executable,'scripts/rt.py','contest-workload']),
              ('candidate_workflow',[sys.executable,'scripts/rt.py','candidate-workflow','--repeat','5'])]
    for label,cmd in commands:
        start=time.perf_counter()
        with (out/f'{label}.log').open('w',encoding='utf-8') as log:
            result=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        row={'label':label,'exit_code':result.returncode,'wall_sec':time.perf_counter()-start}
        steps.append(row); print(json.dumps(row),flush=True)
    payload={'suite':'code_verification','env':_env_info(),'steps':steps,
             'all_passed':all(s['exit_code']==0 for s in steps),'scope':'code, tests and task paths; presentation excluded; sandbox containment evaluated separately'}
    (out/'summary.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    _write_checksum_manifest(out)
    print('Results:',out)
    return 0 if payload['all_passed'] else 1
if __name__=='__main__': raise SystemExit(main())
