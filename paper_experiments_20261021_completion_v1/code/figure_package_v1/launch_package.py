"""One finite offline package execution, preserving all failed or completed outputs."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')


def main():
    control = Path(__file__).resolve().parent; new = control.parents[1]
    logs = new/'logs/figure_package_v1'; output = new/'figure_package_v1'
    assert not logs.exists() and not output.exists()
    logs.mkdir(); output.mkdir(); dynamic = logs/'run_control'; dynamic.mkdir()
    source = control/'run_package.py'; sources = [p for p in control.iterdir() if p.is_file()]
    snapshot = {str(p): sha(p) for p in sources}
    rendered = new/'figures_v3'; qa = rendered/'qa'
    prior = json.loads((new/'logs/figure_link_v3/render_started.json').read_text(encoding='utf-8'))
    command = prior['command'].copy()
    command[command.index('--name')+1] = 'lingxi-completion-figure-package-20261021-v1'
    for index, token in enumerate(command):
        if token.startswith('type=bind,') and 'target=/output' in token:
            command[index] = f'type=bind,source={output.as_posix()},target=/output'
        elif token.startswith('type=bind,') and 'target=/run-control' in token:
            command[index] = f'type=bind,source={dynamic.as_posix()},target=/run-control,readonly'
    at = command.index('--entrypoint')
    extra = []
    for host, guest in ((control,'/package-code'),(rendered,'/rendered'),(qa,'/qa')):
        extra += ['--mount',f'type=bind,source={host.as_posix()},target={guest},readonly']
    command[at:at] = extra
    configuration = {'script': '/package-code/run_package.py','script_sha256':sha(source),'arguments':[],
        'approved_offline_sources':{'/package-code/run_package.py':sha(source)},
        'guard_result':'/output/guard_result.json','guard_events':'/output/guard_events.jsonl',
        'protected_inputs':['/package-code/run_package.py','/figure-code/package_completion_figures.py',
             '/figure-code/prepare_figure_data.py','/qa/visual_qa_single.json','/qa/visual_qa_double.json',
             '/rendered/results/data/figure_data.json','/rendered/results/render/render_receipt.json',
             '/inputs/old_delivery/manifest.json','/run-control/guard_configuration.json'],
        'writable_roots':['/output','/tmp']}
    save(dynamic/'guard_configuration.json',configuration)
    started = datetime.now(timezone.utc).isoformat()
    save(logs/'started.json',{'status':'ACTUAL_OFFLINE_PACKAGE_STARTED','started_utc':started,'command':command,
         'source_snapshot':snapshot,'guard_configuration_sha256':sha(dynamic/'guard_configuration.json'),
         'actual_visual_qa_sha256':{p.name:sha(p) for p in qa.glob('visual_qa_*.json')},
         'model_calls':0,'statistical_runs':0})
    name = command[command.index('--name')+1]
    with (logs/'stdout.log').open('wb') as stdout,(logs/'stderr.log').open('wb') as stderr:
        process = subprocess.Popen(command,stdout=stdout,stderr=stderr)
        try: code=process.wait(timeout=1200)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker','stop','--time','5',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            code=124
    unchanged = all(sha(Path(p))==digest for p,digest in snapshot.items())
    guard = json.loads((output/'guard_result.json').read_text()) if (output/'guard_result.json').is_file() else {}
    valid = code==0 and unchanged and guard.get('exit_code')==guard.get('denied_count')==0
    save(logs/'execution_receipt.json',{'status':'ACTUAL_OFFLINE_PACKAGE_COMPLETED' if valid else 'FAILED_RETAINED_NO_REPLAY',
        'actual_terminal':True,'actual_exit_code':code,'started_utc':started,
        'finished_utc':datetime.now(timezone.utc).isoformat(),'command':command,
        'source_snapshot':snapshot,'source_unchanged':unchanged,'guard_verified':valid,
        'stdout_sha256':sha(logs/'stdout.log'),'stderr_sha256':sha(logs/'stderr.log'),
        'model_calls':0,'statistical_runs':0,'old_assets_modified':False})
    print(json.dumps({'actual_exit_code':code,'verified':valid,'output':str(output)}),flush=True)
    return 0 if valid else code or 1


if __name__=='__main__': raise SystemExit(main())
