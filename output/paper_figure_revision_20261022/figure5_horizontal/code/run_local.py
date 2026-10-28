"""Finite local offline layout-only render; hashes protect all frozen inputs."""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import hashlib
import json
import subprocess
import sys


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',default='local_v1');args=parser.parse_args()
    assert args.attempt.replace('_','').replace('-','').isalnum()
    root=Path(__file__).resolve().parent.parent
    logs=root/f'logs_{args.attempt}';out=root/f'render_{args.attempt}'
    assert not logs.exists() and not out.exists();logs.mkdir();out.mkdir()
    data={str(p):sha(p) for p in (root/'data').iterdir() if p.is_file()}
    data[str(root/'fonts/NotoSansSC-Regular.ttf')]=sha(root/'fonts/NotoSansSC-Regular.ttf')
    code=root/'code/render_figure5_horizontal.py';source_sha=sha(code)
    command=[sys.executable,'-X','utf8','-B',str(code),'--data-dir',str(root/'data'),
             '--font',str(root/'fonts/NotoSansSC-Regular.ttf'),'--out',str(out)]
    started=datetime.now(timezone.utc).isoformat()
    save(logs/'started.json',{'started_utc':started,'command':command,'data_sha256':data,'source_sha256':source_sha,
        'purpose':'Layout only, no experiment/statistics/provider SDK/network entry.'})
    with (logs/'stdout.log').open('wb') as stdout,(logs/'stderr.log').open('wb') as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr)
        try:exit_code=process.wait(timeout=180)
        except subprocess.TimeoutExpired:process.kill();process.wait();exit_code=124
    unchanged=all(sha(Path(p))==value for p,value in data.items()) and sha(code)==source_sha
    valid=exit_code==0 and unchanged
    save(logs/'execution_receipt.json',{'status':'ACTUAL_LOCAL_RENDER_COMPLETE_QA_PENDING' if valid else 'FAILED_RETAINED',
        'actual_terminal':True,'actual_exit_code':exit_code,'started_utc':started,
        'finished_utc':datetime.now(timezone.utc).isoformat(),'command':command,'source_sha256':source_sha,
        'frozen_input_sha256':data,'sources_and_frozen_inputs_unchanged':unchanged,
        'model_API_calls':0,'experiment_runs':0,'statistical_runs':0,'prior_figures_or_deliveries_modified':False,
        'docker_started_or_repaired':False})
    print(json.dumps({'actual_exit_code':exit_code,'verified':valid,'output':str(out)}),flush=True)
    return 0 if valid else exit_code or 1


if __name__=='__main__':raise SystemExit(main())
