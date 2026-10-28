"""Single native stage launcher, exclusive receipts, bounded actual subprocess."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
IMAGE='sha256:80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58'
def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['public','main','sdk_fixture','usage_failure']);p.add_argument('--attempt-id',default='v1');a=p.parse_args()
    assert a.attempt_id in ('v1','v2','v3')
    dest=ROOT/'logs'/(a.stage+'_'+a.attempt_id);dest.mkdir(exist_ok=False)
    if a.stage=='main':
        gate=json.loads((ROOT/'usage_preflight/final_gate.json').read_text(encoding='utf-8'))
        assert gate['status']=='PASS_FOR_BOUNDED_MAIN_WITH_UNKNOWN_USAGE_STOP'
    task={'public':['/code/native/real_driver.py','calibrate'],
          'main':['/code/native/real_driver.py','run','--phases','main'],
          'sdk_fixture':['/code/native/calibrate_sdk_overhead.py','--destination','/output/sdk_fixture_smoke_'+a.attempt_id,'--smoke'],
          'usage_failure':['/code/native/verify_unknown_usage_stop.py']}[a.stage]
    container_name='lingxi-completion-'+a.stage+'-'+a.attempt_id
    cmd=['docker','run','--rm','--name',container_name,
        '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','2','--memory','2g',
        '--pids-limit','512','--shm-size','256m','--tmpfs','/tmp:rw,nosuid,size=256m',
        '--env','PYTHONDONTWRITEBYTECODE=1','--env','TIKTOKEN_CACHE_DIR=/tiktoken_cache',
        '--mount',f'type=bind,source={ROOT/"tiktoken_cache"},target=/tiktoken_cache,readonly',
        '--mount',f'type=bind,source={ROOT},target=/output',
        '--mount',f'type=bind,source={ROOT/"code"},target=/code,readonly',
        '--mount','type=bind,source=E:/codex_rebuilds/corrected_postrun_v2/archive/virtual/opt/lingxi/formal-checkout,target=/opt/lingxi/formal-checkout,readonly']
    if a.stage in ('sdk_fixture','usage_failure'):cmd+=['--network','none']
    else:cmd+=['--mount',f'type=bind,source={ROOT.parent/".env"},target=/run/secrets/model.env,readonly']
    cmd+=['--entrypoint','/opt/lingxi/formal-venv/bin/python',IMAGE,'-X','utf8','-B',*task]
    started=datetime.now(timezone.utc).isoformat()
    with (dest/'started.json').open('x',encoding='utf-8') as f:json.dump({'command':cmd,'started_utc':started,'stdout_file':str(dest/'stdout.log'),'stderr_file':str(dest/'stderr.log')},f,ensure_ascii=False,indent=2)
    with (dest/'stdout.log').open('xb') as out,(dest/'stderr.log').open('xb') as err:
        proc=subprocess.Popen(cmd,stdout=out,stderr=err)
        with (dest/'process.json').open('x') as f:json.dump({'host_launcher_pid':__import__('os').getpid(),'docker_process_pid':proc.pid},f)
        try:code=proc.wait(timeout=18000 if a.stage=='main' else 1200)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker','stop','--time','20',container_name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            code=proc.wait();status='REGISTERED_TIMEOUT_STOP'
        else:status='ACTUAL_PROCESS_TERMINAL'
    result={'status':status,'actual_exit_code':code,'started_utc':started,'finished_utc':datetime.now(timezone.utc).isoformat(),
            'stage':a.stage,'command':cmd,'timeout_seconds':18000 if a.stage=='main' else 1200}
    with (dest/'execution_receipt.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:result[k] for k in ['stage','status','actual_exit_code']},ensure_ascii=False))
    return code
if __name__=='__main__':raise SystemExit(main())
