"""Prepared postrun controller. Do not run until the main coordinator confirms exit."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import subprocess

IMAGE='sha256:80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58'
NAME='lingxi-completion-main-postrun-20261021-v1'
RAW=('task_runs.jsonl','task_unit_starts.jsonl','provider_attempt_starts.jsonl',
     'provider_calls.jsonl','provider_accounting.jsonl','model_io.jsonl','http_request_preflight.jsonl')


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(path,value):
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--study',required=True,type=Path);ap.add_argument('--native',required=True,type=Path)
    ap.add_argument('--completion-receipt',required=True,type=Path)
    ap.add_argument('--out',required=True,type=Path)
    ap.add_argument('--confirmed-main-terminal',action='store_true')
    args=ap.parse_args()
    # Check this BEFORE any active raw existence/read/hash operation.
    if not args.confirmed_main_terminal:
        ap.error('Coordinator actual main terminal confirmation is required; do not analyze a running collection.')
    study,native,out=args.study.resolve(),args.native.resolve(),args.out.resolve()
    control=Path(__file__).resolve().parent
    if out.exists() or not out.parent.is_dir():raise ValueError('Fresh exclusive analysis output required.')
    if not args.completion_receipt.is_file():raise ValueError('Save actual terminal evidence before analysis.')
    witness=json.loads(args.completion_receipt.read_text(encoding='utf-8'))
    if not isinstance(witness,dict) or witness.get('actual_terminal') is not True:
        raise ValueError('The actual coordinator receipt must establish main terminal state.')
    exits=[witness[field] for field in ('actual_exit_code','exit_code','returncode') if field in witness]
    if not exits or any(type(value) is not int or value!=0 for value in exits):
        raise ValueError('Actual successful process exit must be recorded; retain failures without analysis rerun.')
    original=[]
    for name in RAW:original.append((study/'raw'/name,'/study/raw/'+name))
    for name in ['formal_plan.json','replication_design.json','formal-freeze.json']:
        original.append((study/'manifests'/name,'/study/manifests/'+name))
    original.append((study/'corpus/tasks.jsonl','/study/corpus/tasks.jsonl'))
    for name in ['formal_statistics.py','formal_statistics_protocol_aligned_v3.py','provider_accounting.py']:
        original.append((native/name,'/native/'+name))
    files={};metadata={}
    for host,container in original:
        exists=host.is_file()
        if not exists and host.name not in RAW[2:]:raise ValueError('Required postrun input missing: '+host.name)
        files[container]={'exists':exists,'bytes':host.stat().st_size if exists else 0,
                          'sha256':sha(host) if exists else None,'original_host_path':str(host)}
        metadata[str(host)]=(host.stat().st_size,host.stat().st_mtime_ns) if exists else None
    out.mkdir();dynamic=out/'run_control';dynamic.mkdir();outputs=out/'outputs';outputs.mkdir()
    with (dynamic/'completion_receipt.json').open('xb') as f:f.write(args.completion_receipt.read_bytes())
    source=control/'analyze_completed_main_v1.py'
    manifest={'schema_version':'new-main-postrun-input-manifest-v1','postrun_execution_explicitly_confirmed':True,
        'created_utc':datetime.now(timezone.utc).isoformat(),'image':IMAGE,
        'completion_receipt_sha256':sha(args.completion_receipt),
        'analysis_source_sha256':sha(source),'accounting_source_sha256':sha(native/'provider_accounting.py'),
        'identity_binding_sha256':sha(control/'identity_binding.json'),
        'files':files,'scope':'Only the separate new main; public controls and mock/fixtures excluded from science.'}
    save(dynamic/'inputs.json',manifest)
    config={'script':'/control/analyze_completed_main_v1.py','script_sha256':sha(source),
        'arguments':['--manifest','/run-control/inputs.json','--out','/output/analysis'],
        'approved_offline_sources':{'/control/analyze_completed_main_v1.py':sha(source)},
        'guard_result':'/output/guard_result.json','guard_events':'/output/guard_events.jsonl',
        'protected_inputs':list(files)+['/control/analyze_completed_main_v1.py','/control/offline_analysis_runner.py',
             '/control/identity_binding.json',
             '/run-control/inputs.json','/run-control/completion_receipt.json','/run-control/guard_configuration.json'],
        'writable_roots':['/output','/tmp']}
    save(dynamic/'guard_configuration.json',config)
    command=['docker','run','--pull','never','--name',NAME,'--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--cpus','2','--memory','2g','--pids-limit','512',
        '--tmpfs','/tmp:rw,noexec,nosuid,size=128m','--env','PYTHONDONTWRITEBYTECODE=1',
        '--mount',f'type=bind,source={control.as_posix()},target=/control,readonly',
        '--mount',f'type=bind,source={dynamic.as_posix()},target=/run-control,readonly',
        '--mount',f'type=bind,source={(study/"raw").as_posix()},target=/study/raw,readonly',
        '--mount',f'type=bind,source={(study/"manifests").as_posix()},target=/study/manifests,readonly',
        '--mount',f'type=bind,source={(study/"corpus").as_posix()},target=/study/corpus,readonly',
        '--mount',f'type=bind,source={native.as_posix()},target=/native,readonly',
        '--mount',f'type=bind,source={outputs.as_posix()},target=/output',
        '--entrypoint','/opt/lingxi/formal-venv/bin/python',IMAGE,'-X','utf8','-B',
        '/control/offline_analysis_runner.py','/run-control/guard_configuration.json']
    before={p.name:sha(p) for p in control.iterdir() if p.is_file()}
    started=datetime.now(timezone.utc).isoformat();timed_out=False
    with (out/'stdout.log').open('wb') as stdout,(out/'stderr.log').open('wb') as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr)
        try:code=process.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            timed_out=True
            subprocess.run(['docker','stop','--time','5',NAME],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            code=124
    metadata_unchanged=all(((Path(p).stat().st_size,Path(p).stat().st_mtime_ns) if Path(p).is_file() else None)==v for p,v in metadata.items())
    control_unchanged=before=={p.name:sha(p) for p in control.iterdir() if p.is_file()}
    receipt={'status':'ACTUAL_NEW_MAIN_POSTRUN_EXIT_OBSERVED','actual_terminal':True,'actual_exit_code':code,
        'started_utc':started,'finished_utc':datetime.now(timezone.utc).isoformat(),'host_timeout':timed_out,
        'command':command,'image':IMAGE,'raw_input_hash_manifest_sha256':sha(dynamic/'inputs.json'),
        'input_sizes_and_mtimes_unchanged_after_readonly_run':metadata_unchanged,
        'input_hashes_checked_before_and_during_single_parse':True,'control_sources_unchanged':control_unchanged,
        'postrun_input_hash_third_full_scan_performed':False,
        'stdout_sha256':sha(out/'stdout.log'),'stderr_sha256':sha(out/'stderr.log'),
        'new_model_API_benchmark_calls':0,'historical_inputs_or_results_modified':False}
    save(out/'execution_receipt.json',receipt)
    if code!=0 or not metadata_unchanged or not control_unchanged:return code or 1
    guard=json.loads((outputs/'guard_result.json').read_text(encoding='utf-8'))
    analysis=json.loads((outputs/'analysis/analysis_receipt.json').read_text(encoding='utf-8'))
    valid=guard['exit_code']==guard['denied_count']==0 and analysis['one_postrun_boundary_check_passed'] is True
    save(out/'verification_receipt.json',{'status':'ACTUAL_NEW_MAIN_POSTRUN_VERIFIED' if valid else 'REVIEW_REQUIRED',
        'execution_receipt_sha256':sha(out/'execution_receipt.json'),'guard_result_sha256':sha(outputs/'guard_result.json'),
        'analysis_receipt_sha256':sha(outputs/'analysis/analysis_receipt.json'),
        'new_model_API_benchmark_calls':0,'old_statistics_or_raw_changed':False})
    print(json.dumps({'actual_exit_code':code,'verified':valid,'analysis_directory':str(outputs/'analysis')}),flush=True)
    return 0 if valid else 1


if __name__=='__main__':raise SystemExit(main())
