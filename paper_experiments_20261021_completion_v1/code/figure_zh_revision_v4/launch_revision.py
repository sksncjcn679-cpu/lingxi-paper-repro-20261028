"""Finite single offline Chinese-label rerender; no collection or statistics."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import subprocess


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')


def main():
    entry=Path(__file__).resolve().parent;new=entry.parents[1]
    revision=new/'figures_v4_zh_revision';logs=revision/'logs';logs.mkdir();dynamic=logs/'run_control';dynamic.mkdir()
    out=revision/'render_run';out.mkdir()
    prior=json.loads((new/'logs/figure_link_v3/render_started.json').read_text())
    command=prior['command'].copy();name='lingxi-completion-figures-zh-revision-v4'
    command[command.index('--name')+1]=name
    for i,item in enumerate(command):
        if item.startswith('type=bind,'):
            if 'target=/output' in item:command[i]=f'type=bind,source={out.as_posix()},target=/output'
            elif 'target=/run-control' in item:command[i]=f'type=bind,source={dynamic.as_posix()},target=/run-control,readonly'
            elif 'target=/figure-code' in item:command[i]=f'type=bind,source={(revision/"code").as_posix()},target=/figure-code,readonly'
    at=command.index('--entrypoint');command[at:at]=['--mount',f'type=bind,source={entry.as_posix()},target=/revision-code,readonly',
        '--mount',f'type=bind,source={(revision/"data").as_posix()},target=/frozen-data,readonly']
    script=entry/'render_frozen_data.py'
    config={'script':'/revision-code/render_frozen_data.py','script_sha256':sha(script),'arguments':[],
        'approved_offline_sources':{'/revision-code/render_frozen_data.py':sha(script)},
        'guard_result':'/output/guard_result.json','guard_events':'/output/guard_events.jsonl',
        'protected_inputs':['/revision-code/render_frozen_data.py','/figure-code/render_completion_figures.py',
            '/figure-code/base_renderer_v8.py','/figure-code/prepare_figure_data.py',
            '/run-control/guard_configuration.json']+['/frozen-data/'+p.name for p in (revision/'data').iterdir() if p.is_file()],
        'writable_roots':['/output','/tmp']}
    save(dynamic/'guard_configuration.json',config)
    sources=list(entry.glob('*.py'))+list((revision/'code').glob('*.py'))
    snapshot={str(p):sha(p) for p in sources};data={p.name:sha(p) for p in (revision/'data').iterdir() if p.is_file()}
    started=datetime.now(timezone.utc).isoformat()
    save(logs/'started.json',{'status':'ACTUAL_CHINESE_ONLY_RERENDER_STARTED','command':command,'source_sha256':snapshot,
        'frozen_data_sha256':data,'started_utc':started,'model_calls':0,'statistical_runs':0})
    with (logs/'stdout.log').open('wb') as stdout,(logs/'stderr.log').open('wb') as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr)
        try:code=process.wait(timeout=1200)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker','stop','--time','5',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            code=124
    same_sources=all(sha(Path(p))==digest for p,digest in snapshot.items())
    same_data=all(sha(revision/'data'/p)==digest for p,digest in data.items())
    guard=json.loads((out/'guard_result.json').read_text()) if (out/'guard_result.json').is_file() else {}
    valid=code==0 and same_sources and same_data and guard.get('exit_code')==guard.get('denied_count')==0
    save(logs/'execution_receipt.json',{'status':'ACTUAL_CHINESE_RENDER_COMPLETE_QA_PENDING' if valid else 'FAILED_RETAINED',
        'actual_terminal':True,'actual_exit_code':code,'started_utc':started,'finished_utc':datetime.now(timezone.utc).isoformat(),
        'command':command,'source_sha256':snapshot,'frozen_data_sha256':data,'data_unchanged':same_data,
        'sources_unchanged':same_sources,'guard_verified':valid,'model_calls':0,'statistical_runs':0,
        'prior_deliveries_modified':False,'visual_QA':'NOT_YET_PERFORMED'})
    print(json.dumps({'actual_exit_code':code,'verified':valid,'output':str(out)}),flush=True)
    return 0 if valid else code or 1


if __name__=='__main__':raise SystemExit(main())
