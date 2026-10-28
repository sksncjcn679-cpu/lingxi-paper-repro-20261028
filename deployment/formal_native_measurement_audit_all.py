"""Audit every native real unit in bounded, disjoint phase/repeat views.

The frozen native auditor loads tables in memory. Partitioning reduces its working
set; the container remains the only enforced 2GiB ceiling. Peak fit is not assumed.
No original JSONL bytes are rewritten, dropped from the ledger, or used for selection.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

DEFAULT_OUT=Path('/evidence/formal_20261019')
TABLES=('task_unit_starts','task_runs','provider_attempt_starts','provider_calls','model_io',
        'messages','states','execution_events','memory_events','ipc_socket_events',
        'ipc_accounting','spans','resource_samples')


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while data:=stream.read(1024*1024):result.update(data)
    return result.hexdigest()


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def run(out,destination):
    out=Path(out).resolve();destination=Path(destination).resolve()
    if destination.exists():raise RuntimeError('native audit destination exists; choose a new version')
    for protected in ('raw','corpus','manifests','drivers'):
        if destination.is_relative_to(out/protected):raise RuntimeError('audit output cannot be inside an authoritative input root')
    plan_path=out/'manifests/formal_plan.json'
    plan=json.loads(plan_path.read_text())
    ids={u['run_id'] for u in plan}
    with (out/'raw/task_runs.jsonl').open() as stream:terminals=Counter(json.loads(line)['run_id'] for line in stream if line.strip())
    if len(plan)!=6504 or len(ids)!=6504 or set(terminals)!=ids or any(n!=1 for n in terminals.values()):
        raise RuntimeError('all 6504 unique closed units required before native measurement audit')
    native={u['run_id']:u for u in plan if not u['phase'].startswith('external_')}
    if len(native)!=6264 or len(plan)-len(native)!=240:raise RuntimeError('expected6264 native and240 external planned units')
    groups={run_id:f"{u['phase']}__repeat_{u['repeat']}" for run_id,u in native.items()}
    group_counts=Counter(groups.values())
    sources={name:out/'raw'/f'{name}.jsonl' for name in TABLES}
    missing=[name for name,path in sources.items() if not path.is_file()]
    if missing:raise RuntimeError('required native raw files missing: '+','.join(missing))
    auditor_original=Path(__file__).with_name('formal_measurement_audit.py')
    corpus_original=out/'corpus/automatic_validated_v1'
    additional=[plan_path,Path(__file__),auditor_original,*sorted(p for p in corpus_original.rglob('*') if p.is_file())]
    needed=sum(p.stat().st_size for p in sources.values())+sum(p.stat().st_size for p in additional)+256*1024**2
    space_root=destination.parent
    while not space_root.exists():space_root=space_root.parent
    if shutil.disk_usage(space_root).free<needed:raise RuntimeError('insufficient free space for disjoint audit copies')
    initial={name:digest(path) for name,path in sources.items()}
    additional_initial={str(path):digest(path) for path in additional}
    destination.mkdir(parents=True)
    counts={};reports=[]
    metadata={'status':'RUNNING','created_utc':datetime.now(timezone.utc).isoformat(),
        'native_planned_units':len(native),'external_units_excluded':len(plan)-len(native),
        'partition':'frozen phase and repeat; every native ID exactly once, no outcome-based selection',
        'scope':'one frozen native auditor replay per disjoint view; no external prompt checks and no provider calls',
        'source_sha256':initial,'wrapper_sha256':digest(Path(__file__)),
        'auditor_sha256':digest(auditor_original),'additional_source_sha256':additional_initial,
        'groups':dict(group_counts),'memory_boundary':'container2GiB enforced externally; partitioning reduces working set but no per-group peak has been measured',
        'disk_estimated_bytes':needed,'disk_check_path':str(space_root)}
    write(destination/'native_measurement_audit_all.json',metadata)
    try:
        for name,path in sources.items():
            table_counts=Counter()
            with ExitStack() as stack:
                writers={}
                for group in sorted(group_counts):
                    target=destination/'views'/group/'raw'/f'{name}.jsonl'
                    target.parent.mkdir(parents=True,exist_ok=True)
                    writers[group]=stack.enter_context(target.open('xb'))
                with path.open('rb') as stream:
                    for line in stream:
                        if not line.strip():continue
                        item=json.loads(line)
                        if item.get('run_id') not in ids:raise RuntimeError('orphan recorder row in '+name)
                        group=groups.get(item.get('run_id'))
                        if group is not None:
                            writers[group].write(line);table_counts[group]+=1
            counts[name]=dict(table_counts)
        for path in (destination/'views').rglob('*.jsonl'):path.chmod(0o444)
        # The auditor resolves the immutable corpus from an ancestor of its view.
        # A portable corpus copy allows this destination outside the original tree.
        shutil.copytree(corpus_original,destination/'corpus/automatic_validated_v1')
        for original in corpus_original.rglob('*'):
            if original.is_file() and digest(original)!=digest(destination/'corpus/automatic_validated_v1'/original.relative_to(corpus_original)):
                raise RuntimeError('copied corpus hash differs')
        for path in (destination/'corpus').rglob('*'):
            if path.is_file():path.chmod(0o444)
        auditor=destination/'drivers/formal_measurement_audit.py'
        auditor.parent.mkdir()
        shutil.copy2(auditor_original,auditor);auditor.chmod(0o444)
        if digest(auditor)!=metadata['auditor_sha256']:raise RuntimeError('auditor snapshot hash differs')
        for group,expected in sorted(group_counts.items()):
            result_path=destination/'reports'/f'{group}.json'
            result_path.parent.mkdir(exist_ok=True)
            command=[sys.executable,str(auditor),'--out',str(destination/'views'/group),'--output',str(result_path)]
            log=destination/'logs'/group;log.parent.mkdir(exist_ok=True)
            with log.with_suffix('.stdout.log').open('x') as stdout,log.with_suffix('.stderr.log').open('x') as stderr:
                completed=subprocess.run(command,stdout=stdout,stderr=stderr,timeout=1800,
                    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            report=json.loads(result_path.read_text()) if result_path.exists() else {'status':'MISSING_AUDIT_OUTPUT'}
            record={'group':group,'planned_units':expected,'terminal_units':report.get('terminal_units'),
                'exit_code':completed.returncode,'status':report.get('status'),
                'measurement_errors':len(report.get('measurement_errors',[])),
                'report':str(result_path.relative_to(destination)),
                'report_sha256':digest(result_path) if result_path.exists() else None}
            reports.append(record);metadata.update(group_reports=reports,copied_table_rows=counts)
            write(destination/'native_measurement_audit_all.json',metadata)
            if completed.returncode or record['status']!='PASS' or record['terminal_units']!=expected:
                raise RuntimeError('native measurement audit failed; preserve group report: '+group)
        current={name:digest(path) for name,path in sources.items()}
        if current!=initial:raise RuntimeError('original raw files changed during native audit')
        if {str(path):digest(path) for path in additional}!=additional_initial:
            raise RuntimeError('plan, corpus, helper or auditor source changed during native audit')
        if sum(r['terminal_units'] for r in reports)!=len(native):raise RuntimeError('native unit coverage not conserved')
        metadata.update(status='PASS',finished_utc=datetime.now(timezone.utc).isoformat(),
            original_raw_sha256_unchanged=True,audited_units=sum(r['terminal_units'] for r in reports),
            model_quality_failures_policy='retained in original ledger and per-group audits; not measurement errors',
            limits=['bounded group replay; cross-group covariance or causal effects are not estimated here',
                'raw task cached-token defaults remain raw provenance, not proof of explicit provider cache reporting; see separate usage-authority audit',
                'bounded helper FD checks replay within each phase/repeat view; global source coverage is independently reconciled'])
    except BaseException as error:
        metadata.update(status='FAIL',finished_utc=datetime.now(timezone.utc).isoformat(),
            error_type=type(error).__name__,error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
        write(destination/'native_measurement_audit_all.json',metadata)
        raise
    write(destination/'native_measurement_audit_all.json',metadata)
    return metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    parser.add_argument('--destination',type=Path,required=True);args=parser.parse_args()
    result=run(args.out,args.destination)
    print(json.dumps({'status':result['status'],'native_units':result.get('audited_units'),'groups':len(result.get('group_reports',[]))}))
