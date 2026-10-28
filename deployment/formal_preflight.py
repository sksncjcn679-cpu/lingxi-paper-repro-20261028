"""Execute exactly the protocol's first preflight commands in the isolated formal environment."""
from pathlib import Path
from datetime import datetime, timezone
import json
import subprocess
import sys
import time
import shutil

OUT=Path('/evidence/formal_20261019')
WORK=Path('/opt/lingxi/formal-checkout')
PY='/opt/lingxi/formal-venv/bin/python'

def main():
    commands=[('install',[PY,'-m','pip','install','-e','.[dev,llm]']),
        ('source-identity',[PY,'scripts/verify_source_identity.py']),('version',[PY,'scripts/rt.py','version']),
        ('os-probe',[PY,'scripts/rt.py','os-probe']),
        ('pytest',[PY,'-m','pytest','../tests/unit','../tests/integration','-q','--tb=short',f'--junitxml={OUT}/logs/preflight/pytest.xml']),
        ('uname',['uname','-a']),('distribution',['cat','/etc/os-release']),('python',[PY,'--version']),
        ('dependency-freeze',[PY,'-m','pip','freeze','--all']),('dependency-check',[PY,'-m','pip','check'])]
    records=[]
    for name,command in commands:
        log=OUT/'logs/preflight'/name
        if log.with_suffix('.result.json').exists():
            previous=json.loads(log.with_suffix('.result.json').read_text())
            if previous['command']!=command:raise RuntimeError(f'preflight command identity changed: {name}')
            records.append(previous)
            continue
        if log.with_suffix('.stdout.log').exists():raise RuntimeError(f'preflight log already exists: {name}')
        start=time.perf_counter()
        print(json.dumps({'starting':name,'utc':datetime.now(timezone.utc).isoformat()}),flush=True)
        try:
            result=subprocess.run(command,cwd=WORK,text=True,capture_output=True,timeout=900)
            stdout,stderr,code=result.stdout,result.stderr,result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout,stderr,code=str(exc.stdout or ''),str(exc.stderr or ''),124
        log.with_suffix('.stdout.log').write_text(stdout)
        log.with_suffix('.stderr.log').write_text(stderr)
        row={'name':name,'command':command,'cwd':str(WORK),'exit_code':code,'wall_sec':time.perf_counter()-start,
             'finished_utc':datetime.now(timezone.utc).isoformat()}
        records.append(row)
        log.with_suffix('.result.json').write_text(json.dumps(row,indent=2))
    sys.path.insert(0,str(WORK/'src'))
    from agent_runtime.evaluation.runner import _source_tree_sha256
    (OUT/'manifests/source_identity.json').write_text(json.dumps({'source_sha256':_source_tree_sha256(WORK),
        'working_directory':str(WORK),'runtime_business_changes_since_deployment':False},indent=2))
    (OUT/'manifests/dependencies.txt').write_text((OUT/'logs/preflight/dependency-freeze.stdout.log').read_text())
    (OUT/'analysis/preflight_commands.json').write_text(json.dumps(records,indent=2))
    shutil.copy2(__file__,OUT/'drivers/formal_preflight.py')
    print(json.dumps({'commands':len(records),'failed_commands':[r['name'] for r in records if r['exit_code']]}),flush=True)

if __name__=='__main__':main()
