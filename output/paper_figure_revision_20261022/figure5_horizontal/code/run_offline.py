"""Run only the isolated figure renderer in the established immutable openEuler image."""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import hashlib
import json
import subprocess


IMAGE='sha256:80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='v1');args=parser.parse_args()
    assert args.attempt.replace('_','').replace('-','').isalnum()
    root=Path(__file__).resolve().parent.parent;code=root/'code'
    logs=root/f'logs_{args.attempt}';out=root/f'render_{args.attempt}'
    assert not logs.exists() and not out.exists();logs.mkdir();out.mkdir()
    config={'script':'/code/render_figure5_horizontal.py','script_sha256':sha(code/'render_figure5_horizontal.py'),
        'arguments':[],'approved_offline_sources':{'/code/render_figure5_horizontal.py':sha(code/'render_figure5_horizontal.py')},
        'guard_result':'/output/guard_result.json','guard_events':'/output/guard_events.jsonl',
        'protected_inputs':['/code/'+p.name for p in code.glob('*.py')]+['/data/'+p.name for p in (root/'data').iterdir() if p.is_file()]
            +['/fonts/NotoSansSC-Regular.ttf','/run-control/guard_configuration.json'],
        'writable_roots':['/output','/tmp']}
    save(logs/'guard_configuration.json',config)
    name='lingxi-figure5-horizontal-20261022-'+args.attempt
    command=['docker','run','--pull','never','--name',name,'--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--cpus','2','--memory','2g','--pids-limit','512',
        '--tmpfs','/tmp:rw,noexec,nosuid,size=128m','--env','PYTHONDONTWRITEBYTECODE=1']
    for host,guest in ((code,'/code'),(root/'data','/data'),(root/'fonts','/fonts'),(logs,'/run-control')):
        command += ['--mount',f'type=bind,source={host.as_posix()},target={guest},readonly']
    command += ['--mount',f'type=bind,source={out.as_posix()},target=/output',
        '--entrypoint','/opt/lingxi/formal-venv/bin/python',IMAGE,'-X','utf8','-B',
        '/code/offline_analysis_runner.py','/run-control/guard_configuration.json']
    sources={str(p):sha(p) for p in code.glob('*.py')}
    data={str(p):sha(p) for p in (root/'data').iterdir() if p.is_file()}
    started=datetime.now(timezone.utc).isoformat()
    save(logs/'started.json',{'started_utc':started,'command':command,'source_sha256':sources,'data_sha256':data,'image':IMAGE})
    with (logs/'stdout.log').open('wb') as stdout,(logs/'stderr.log').open('wb') as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr)
        try:exit_code=process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker','stop','--time','5',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            exit_code=124
    unchanged=all(sha(Path(p))==digest for p,digest in {**sources,**data}.items())
    guard=json.loads((out/'guard_result.json').read_text()) if (out/'guard_result.json').is_file() else {}
    valid=exit_code==0 and unchanged and guard.get('exit_code')==guard.get('denied_count')==0
    save(logs/'execution_receipt.json',{'status':'ACTUAL_FIGURE5_RENDER_COMPLETE_QA_PENDING' if valid else 'FAILED_RETAINED',
        'actual_terminal':True,'actual_exit_code':exit_code,'started_utc':started,
        'finished_utc':datetime.now(timezone.utc).isoformat(),'command':command,'image':IMAGE,
        'source_sha256':sources,'data_sha256':data,'sources_and_data_unchanged':unchanged,
        'guard_verified':valid,'model_API_calls':0,'experiment_runs':0,'statistics_runs':0})
    print(json.dumps({'actual_exit_code':exit_code,'verified':valid,'output':str(out)}),flush=True)
    return 0 if valid else exit_code or 1


if __name__=='__main__':raise SystemExit(main())
