"""Posthoc source-scope verification; never substitutes for a preformal gate.

Do not run until the existing full real/pressure sequence and postrun controller
have exited. This helper never waits, installs packages, or calls a provider.
"""
from __future__ import annotations
import argparse
import ast
from contextlib import ExitStack
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

OUT=Path('/evidence/formal_20261019')
FORMAL=Path('/opt/lingxi/formal-checkout')
LEGACY=Path('/opt/lingxi/checkout')
PYTHON=Path('/opt/lingxi/formal-venv/bin/python')
TERMINAL={'COMPLETED','FAILED','UNSUPPORTED','NON_COMPARABLE','INCOMPLETE','INCOMPLETE_BUDGET_STOP'}
IGNORE_PARTS={'__pycache__','.pytest_cache'}
POSTCHECK_HELPER_VERSION=4


def utc():return datetime.now(timezone.utc).isoformat()


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024):h.update(chunk)
    return h.hexdigest()


def save(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def _source_tree_sha256(root: Path) -> str:
    """Hash runtime, scenarios, model/dataset files, run scripts and packaging config."""
    candidates = [root / "pyproject.toml"]
    for folder in (root / "src", root / "scenarios", root / "scripts"):
        if folder.is_dir():
            candidates.extend(sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix in {".py", ".sh", ".json", ".npz"}))
    h = hashlib.sha256()
    for path in sorted(candidates, key=lambda p: p.relative_to(root).as_posix()):
        rel = str(path.relative_to(root)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def algorithm_ast(path):
    tree=ast.parse(Path(path).read_text(encoding='utf-8'))
    functions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='_source_tree_sha256']
    if len(functions)!=1:raise ValueError('source identity algorithm missing or ambiguous')
    return hashlib.sha256(ast.dump(functions[0],include_attributes=False).encode()).hexdigest()


def within(path,root):
    return path==root or root in path.parents


def validate_destination(destination,out,formal,legacy):
    resolved=destination.resolve()
    protected=[formal.resolve(),legacy.resolve(),(out/'raw').resolve(),(out/'corpus').resolve(),
               (out/'inputs').resolve(),(out/'input_view').resolve(),(out/'manifests').resolve()]
    if resolved==out.resolve() or any(within(resolved,root) for root in protected):
        raise ValueError('destination must not be a source/raw/input/manifests path')
    if destination.exists():raise ValueError('new immutable destination required')
    return resolved


def file_manifest(root,*,tests=False):
    base=root/'tests' if tests else root
    if tests:
        paths=sorted(p for p in base.rglob('*') if (p.is_file() or p.is_symlink()) and not (set(p.relative_to(base).parts)&IGNORE_PARTS))
    else:
        paths=[root/'pyproject.toml']
        for folder in (root/'src',root/'scenarios',root/'scripts'):
            if folder.is_dir():paths.extend(p for p in folder.rglob('*') if p.is_file() and p.suffix in {'.py','.sh','.json','.npz'})
        paths=sorted(paths,key=lambda p:p.relative_to(root).as_posix())
    rows=[]
    for path in paths:
        relative=path.relative_to(base).as_posix();resolved=path.resolve()
        row={'path':relative,'is_symlink':path.is_symlink(),'resolved_path':str(resolved)}
        if path.is_symlink():row['symlink_target']=os.readlink(path)
        if path.is_file():row.update(size_bytes=path.stat().st_size,sha256=digest(path))
        else:row.update(size_bytes=None,sha256=None)
        rows.append(row)
    canonical=json.dumps([{key:row[key] for key in ('path','size_bytes','sha256')} for row in rows],sort_keys=True,separators=(',',':'))
    return {'root':str(base),'resolved_root':str(base.resolve()),'files':rows,
        'byte_manifest_sha256':hashlib.sha256(canonical.encode()).hexdigest(),
        'excluded_cache_components':sorted(IGNORE_PARTS) if tests else [],'file_count':len(rows)}


def compare(left,right):
    a={row['path']:row for row in left['files']};b={row['path']:row for row in right['files']}
    missing_left=sorted(set(b)-set(a));missing_right=sorted(set(a)-set(b))
    different=sorted(path for path in a.keys()&b.keys() if (a[path]['size_bytes'],a[path]['sha256'])!=(b[path]['size_bytes'],b[path]['sha256']))
    return {'same_bytes_and_relative_paths':not(missing_left or missing_right or different),
            'missing_in_formal':missing_left,'missing_in_legacy':missing_right,'different_files':different}


def snapshot_tests(root,manifest,destination):
    copied=[];skipped=[]
    for row in manifest['files']:
        path=root/'tests'/row['path'];relative=Path(row['path'])
        # Do not dereference outside-tree symlinks or archive credential material.
        if not within(path.resolve(),(root/'tests').resolve()) or row['is_symlink'] or \
           path.name=='.env' or path.name.startswith('.env.') or path.suffix.lower() in {'.key','.pem'}:
            skipped.append({'path':row['path'],'reason':'SYMLINK_EXTERNAL_OR_CREDENTIAL_BASENAME_BYTES_NOT_ARCHIVED'});continue
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
        actual=digest(target)
        if actual!=row['sha256']:raise ValueError('test snapshot byte identity changed')
        copied.append({'path':row['path'],'sha256':actual,'size_bytes':row['size_bytes']})
    return {'copied_files':copied,'skipped_files':skipped,'executed_test_location':str(root/'tests'),
            'snapshot_is_execution_location':False}


# Automatically loaded in pytest and ordinary Python children. Audit hooks cannot
# be removed through Python APIs; AF_UNIX/loopback remain available to local tests.
SITECUSTOMIZE=r'''
import errno,hashlib,ipaddress,json,os,pathlib,socket,sys,time
_log=os.environ['SOURCE_POSTCHECK_NETWORK_LOG']
_protected=[pathlib.Path(p).resolve() for p in json.loads(os.environ['SOURCE_POSTCHECK_PROTECTED_PATHS'])]
_dashboard=pathlib.Path(os.environ['SOURCE_POSTCHECK_DASHBOARD_FILE'])
_generated_log=os.environ['SOURCE_POSTCHECK_GENERATED_WRITE_LOG']
def _local(host):
    if host in (None,'','localhost',b'localhost'):return True
    try:
        if isinstance(host,bytes):host=host.decode('ascii')
        address=ipaddress.ip_address(str(host).split('%',1)[0])
        return address.is_loopback or address.is_unspecified
    except (ValueError,UnicodeError):return False
def _location(value,dir_fd=None):
    try:
        # Preserve the original integer-FD policy. Pipes/sockets/anon_inode are
        # not relative filenames and must never be resolved beneath source cwd.
        if isinstance(value,int):return None
        path=pathlib.Path(os.fsdecode(value))
        if not path.is_absolute():
            base=pathlib.Path(os.readlink('/proc/self/fd/'+str(dir_fd))) if isinstance(dir_fd,int) and dir_fd>=0 else pathlib.Path.cwd()
            path=base/path
        return path
    except (ValueError,TypeError,OSError):return None

def _protected_path(value,dir_fd=None):
    path=_location(value,dir_fd)
    if path is None:return False
    try:path=path.resolve()
    except (ValueError,OSError):return False
    return any(path==root or root in path.parents for root in _protected)

def _protected_entry(value,dir_fd=None):
    # rename/unlink/symlink mutate directory entries, not an existing symlink's
    # target. Resolve the parent only; still protect each root entry itself.
    path=_location(value,dir_fd)
    if path is None:return False
    try:entry=path.parent.resolve()/path.name
    except (ValueError,OSError):return False
    return any(entry==root or root in entry.parents for root in _protected)

def _dashboard_write(value):
    path=_location(value)
    if path is None:return False
    try:return path.resolve()==_dashboard and not _dashboard.is_symlink()
    except (ValueError,OSError):return False
def _event(name,args):
    denied=False
    if name=='socket.getaddrinfo':denied=not _local(args[0])
    elif name in ('socket.connect','socket.sendto'):
        sock,address=args[0],args[1]
        denied=sock.family in (socket.AF_INET,socket.AF_INET6) and not _local(address[0])
    if denied:
        entry={'pid':os.getpid(),'event':name,'monotonic_ns':time.monotonic_ns(),
               'destination_sha256':hashlib.sha256(repr(args[1] if len(args)>1 else args[0]).encode()).hexdigest(),
               'policy':'DENIED_NON_LOOPBACK_PYTHON_NETWORK'}
        with open(_log,'a',encoding='utf-8') as f:f.write(json.dumps(entry)+'\n')
        raise OSError(errno.ENETUNREACH,'External networking disabled by posthoc source verifier')
    if name=='open':
        path,mode,flags=args
        writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or \
                (isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)))
        if writing and _protected_path(path):
            if not _dashboard_write(path):raise PermissionError('Source/raw/input writes disabled by posthoc verifier')
            with open(_generated_log,'a',encoding='utf-8') as stream:
                stream.write(json.dumps({'pid':os.getpid(),'event':'open','path':str(_dashboard),
                    'policy':'EXACT_GENERATED_DASHBOARD_INDEX_ONLY_RESTORED_BY_PARENT'})+'\n')
    elif name in ('os.remove','os.rmdir','os.mkdir'):
        dir_fd=args[2] if name=='os.mkdir' and len(args)>2 else args[1] if len(args)>1 else None
        if _protected_entry(args[0],dir_fd):raise PermissionError('Source/raw/input entry mutation disabled by posthoc verifier')
    elif name=='os.rename':
        if _protected_entry(args[0],args[2] if len(args)>2 else None) or _protected_entry(args[1],args[3] if len(args)>3 else None):
            raise PermissionError('Source/raw/input entry mutation disabled by posthoc verifier')
    elif name=='os.symlink':
        if _protected_entry(args[1],args[2] if len(args)>2 else None):
            raise PermissionError('Source/raw/input symlink entry mutation disabled by posthoc verifier')
    elif name=='os.link':
        # A hardlink can expose the same protected inode for later writes.
        if _protected_path(args[0],args[2] if len(args)>2 else None) or _protected_entry(args[1],args[3] if len(args)>3 else None):
            raise PermissionError('Hardlink to protected source/raw/input disabled by posthoc verifier')
    elif name in ('os.chmod','os.chown','os.truncate','os.utime'):
        position=2 if name=='os.chmod' else 3 if name in ('os.chown','os.utime') else None
        dir_fd=args[position] if position is not None and len(args)>position else None
        if _protected_path(args[0],dir_fd):raise PermissionError('Source/raw/input target mutation disabled by posthoc verifier')
sys.addaudithook(_event)
sys._source_postcheck_guard_sha256=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
'''


PLUGIN=r'''
import hashlib,importlib.util,json,os,pathlib,sys,time
if getattr(sys,'_source_postcheck_guard_sha256',None)!=os.environ.get('SOURCE_POSTCHECK_GUARD_SHA256'):
    raise RuntimeError('Mandatory posthoc Python network/source guard was not loaded')
ROOT=pathlib.Path(os.environ['SOURCE_POSTCHECK_FORMAL_ROOT']).resolve()
LOG=pathlib.Path(os.environ['SOURCE_POSTCHECK_IMPORT_LOG'])
def _snapshot(stage,session=None):
    loaded={name:str(pathlib.Path(module.__file__).resolve()) for name,module in list(sys.modules.items())
            if (name=='agent_runtime' or name.startswith('agent_runtime.')) and getattr(module,'__file__',None)}
    try:
        spec=importlib.util.find_spec('agent_runtime');origin=spec.origin if spec else None
    except (ImportError,ValueError,AttributeError):origin=None
    conftests=sorted({str(pathlib.Path(module.__file__).resolve()) for module in list(sys.modules.values())
                     if getattr(module,'__file__',None) and pathlib.Path(str(module.__file__)).name=='conftest.py'})
    row={'stage':stage,'pid':os.getpid(),'monotonic_ns':time.monotonic_ns(),'cwd':os.getcwd(),
         'sys_path':list(sys.path),'find_spec_origin':origin,'loaded_runtime_modules':loaded,'loaded_conftests':conftests,
         'startup_guard_sha256':getattr(sys,'_source_postcheck_guard_sha256',None)}
    if session is not None:
        row['pytest_rootpath']=str(pathlib.Path(str(session.config.rootpath)).resolve())
        row['collection_items']=[{'nodeid':item.nodeid,'path':str(pathlib.Path(str(item.path)).resolve())} for item in session.items]
    with LOG.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
def pytest_sessionstart(session):_snapshot('sessionstart_after_initial_conftests',session)
def pytest_collection_finish(session):_snapshot('collection_finish',session)
def pytest_sessionfinish(session,exitstatus):_snapshot('sessionfinish',session)
'''


def gate(out):
    sequence=out/'logs/mechanisms/formal_complete_sequence_v1.result.json'
    if not sequence.is_file():return {'status':'GATE_SEQUENCE_NOT_FINISHED'}
    value=json.loads(sequence.read_text())
    if value.get('exit_code')!=0 or value.get('timed_out'):return {'status':'GATE_SEQUENCE_NOT_SUCCESSFUL','sequence_sha256':digest(sequence)}
    plan_path=out/'manifests/formal_plan.json';plan=json.loads(plan_path.read_text());ids=[u['run_id'] for u in plan]
    tasks=[];task_path=out/'raw/task_runs.jsonl';before=digest(task_path)
    with task_path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                row=json.loads(line);tasks.append((row.get('run_id'),row.get('status'),row.get('model_evidence')))
    valid=len(ids)==6504 and len(set(ids))==6504 and len(tasks)==6504 and {r[0] for r in tasks}==set(ids) and \
          all(r[1] in TERMINAL and r[2]=='real_provider' for r in tasks)
    return {'status':'GATE_COMPLETE' if valid and before==digest(task_path) else 'GATE_6504_INCOMPLETE_OR_CHANGED',
            'sequence_sha256':digest(sequence),'plan_sha256':digest(plan_path),'task_runs_sha256':before,
            'planned_units':len(ids),'terminal_records':len(tasks)}


def cleaned_environment(destination,formal,legacy,out,dashboard):
    env=dict(os.environ);removed=[]
    for key in list(env):
        upper=key.upper()
        if any(word in upper for word in ('API_KEY','API_TOKEN','ACCESS_TOKEN','AUTH_TOKEN','PASSWORD','SECRET','CREDENTIAL')) or \
           upper in {'PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTHONPATH','PYTHONSTARTUP','PYTHONHOME','COVERAGE_PROCESS_START'}:
            removed.append(key);env.pop(key,None)
    # Pytest capture uses anonymous temporary files. The evidence bind mount
    # cannot ftruncate those files; keep fixtures/capture on native /tmp.
    # The independent two-filesystem probe and first failed run are preserved.
    temp=Path(tempfile.mkdtemp(prefix='lx-pc-',dir='/tmp'))
    guard=destination/'guard';guard.mkdir()
    (guard/'sitecustomize.py').write_text(SITECUSTOMIZE,encoding='utf-8')
    (guard/'source_scope_probe.py').write_text(PLUGIN,encoding='utf-8')
    protected=[formal.resolve(),legacy.resolve(),(out/'raw').resolve(),(out/'corpus').resolve(),
               (out/'inputs').resolve(),(out/'input_view').resolve(),(out/'manifests').resolve()]
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',PYTHONPATH=os.pathsep.join((str(guard),str(formal/'src'))),
        PYTEST_PLUGINS='source_scope_probe',PYTEST_ADDOPTS='-p no:cacheprovider --basetemp='+str(temp/'pytest'),
        TMPDIR=str(temp),TMP=str(temp),TEMP=str(temp),XDG_CACHE_HOME=str(temp/'cache'),
        MPLCONFIGDIR=str(temp/'matplotlib-cache'),MPLBACKEND='Agg',
        SOURCE_POSTCHECK_FORMAL_ROOT=str(formal),SOURCE_POSTCHECK_IMPORT_LOG=str(destination/'import_sources.jsonl'),
        SOURCE_POSTCHECK_NETWORK_LOG=str(destination/'blocked_network.jsonl'),
        SOURCE_POSTCHECK_DASHBOARD_FILE=str(dashboard),
        SOURCE_POSTCHECK_GENERATED_WRITE_LOG=str(destination/'generated_dashboard_writes.jsonl'),
        SOURCE_POSTCHECK_PROTECTED_PATHS=json.dumps([str(p) for p in protected]),
        SOURCE_POSTCHECK_GUARD_SHA256=digest(guard/'sitecustomize.py'),
        HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',ALL_PROXY='http://127.0.0.1:9',
        NO_PROXY='localhost,127.0.0.1,::1',http_proxy='http://127.0.0.1:9',https_proxy='http://127.0.0.1:9',
        all_proxy='http://127.0.0.1:9',no_proxy='localhost,127.0.0.1,::1')
    return env,{'removed_environment_key_names':sorted(removed),'credential_values_recorded':False,
        'temporary_directory':str(temp),
        'temporary_storage_scope':'native container /tmp for anonymous capture files and local fixture sockets; authoritative stdout/stderr/JUnit/import/source evidence remains under destination',
        'python_network_guard':'Python audit hook denies non-loopback IPv4/IPv6 connect/sendto/DNS; AF_UNIX/local fixture servers allowed',
        'non_python_network_boundary':'Proxy variables deny common external HTTP clients; this is not a kernel network namespace claim',
        'protected_write_policy':'Follow-target writes and hardlinks to protected source/raw/input remain denied; entry-only operations resolve parent, not unmodified symlink target. Exact formal/dashboard/index.html open-write only is backed up in memory and restored by parent.',
        'guard_sha256':digest(guard/'sitecustomize.py'),'probe_sha256':digest(guard/'source_scope_probe.py')}


def backup_dashboard(formal):
    path=formal.resolve()/'dashboard/index.html'
    if path.parent.resolve()!=formal.resolve()/'dashboard' or path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError('generated dashboard exception must be the exact non-symlink regular-file entry')
    original=path.read_bytes() if path.is_file() else None
    before={'path':str(path),'existed':original is not None,'bytes':len(original) if original is not None else None,
            'sha256':hashlib.sha256(original).hexdigest() if original is not None else None,
            'backup_storage':'parent process memory only; no backup bytes exported',
            'permission_scope':'open-write exact index.html only; no sibling, source, raw or freeze exception'}
    return path,original,before


def restore_dashboard(path,original,before):
    result={'before':before,'restoration_attempted_in_finally':True}
    try:
        result['generated_file_sha256_before_restore']=digest(path) if path.is_file() and not path.is_symlink() else None
        result['unexpected_symlink_before_restore']=path.is_symlink()
        if path.is_symlink():path.unlink()
        if original is None:
            if path.exists():path.unlink()
        else:path.write_bytes(original)
        after=digest(path) if path.is_file() and not path.is_symlink() else None
        result.update(after_exists=path.exists(),after_sha256=after,
            restored=(after==before['sha256'] and path.exists()==before['existed'] and not path.is_symlink()))
    except Exception as error:
        result.update(restored=False,error_type=type(error).__name__,error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
    return result


def execute(command,cwd,env,destination,timeout):
    started=utc();tick=time.monotonic();timed_out=False
    with (destination/'pytest.stdout.log').open('xb') as stdout,(destination/'pytest.stderr.log').open('xb') as stderr:
        child=subprocess.Popen(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
        try:code=child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True;os.killpg(child.pid,signal.SIGTERM)
            try:code=child.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);code=child.wait()
    return {'command':command,'cwd':str(cwd),'started_utc':started,'finished_utc':utc(),
        'wall_sec':time.monotonic()-tick,'exit_code':code,'timed_out':timed_out,'pid':child.pid,
        'stdout_sha256':digest(destination/'pytest.stdout.log'),'stderr_sha256':digest(destination/'pytest.stderr.log')}


def junit_summary(path):
    if not path.is_file():return {'status':'MISSING_JUNIT'}
    root=ET.parse(path).getroot();cases=list(root.iter('testcase'))
    return {'status':'PARSED','sha256':digest(path),'testcases':len(cases),
            'failures':sum(case.find('failure') is not None for case in cases),
            'errors':sum(case.find('error') is not None for case in cases),
            'skipped':sum(case.find('skipped') is not None for case in cases),
            'failure_identities':[{'classname':case.get('classname'),'name':case.get('name')} for case in cases
                                  if case.find('failure') is not None or case.find('error') is not None]}


def postcheck(destination,out=OUT,formal=FORMAL,legacy=LEGACY,python=PYTHON,timeout=3600):
    out=Path(out);formal=Path(formal);legacy=Path(legacy);python=Path(python)
    destination=validate_destination(Path(destination),out,formal,legacy)
    destination.mkdir(parents=True)
    report={'scope':'POSTHOC_SOURCE_SCOPE_VERIFICATION','status':'NOT_EXECUTED','started_utc':utc(),
        'postcheck_helper_version':POSTCHECK_HELPER_VERSION,
        'module_sha256':digest(__file__),'paid_provider_calls_requested':0,'preformal_gate_claim':False,
        'existing_preflight_changed':False,'automatic_rerun_or_failure_suppression':False,
        'known_legacy_path_ambiguity':'Old cwd=formal-root pytest ../tests resolves /opt/lingxi/tests symlink to legacy tests; editable installation alone does not prove imported business source.',
        'posthoc_helper_corrections':{
            'retained_previous_runs':['analysis/source_test_postcheck_v1','analysis/source_test_postcheck_v2','analysis/source_test_postcheck_v3'],
            'previous_helper_backup':'drivers/preparation_versions/formal_source_test_postcheck_v3_before_pipe_fd_fix.py',
            'previous_helper_sha256':'88ba1db8132f6814709347811513ef9cb77241d9aa79fa4884d3e5432ed68138',
            'v2_helper_backup':'drivers/preparation_versions/formal_source_test_postcheck_before_entry_guard_v3.py',
            'v2_helper_sha256':'a163df213e0fd8984af98afa267aeb184dec3d28d8bbcf22e3714e4c3610075e',
            'integer_fd_correction':'Restore original integer-FD skip policy; pipe/socket/anon_inode FD pseudo names are not filesystem paths. Path-based source protection remains; no new FD guard framework.',
            'entry_semantics':'symlink only mutates destination entry; rename/remove mutate entries via actual parent, not unchanged symlink targets; hardlink protected source and follow-target writes still blocked',
            'generated_artifact_exception':'exact formal/dashboard/index.html backed up in memory before pytest and restored in finally; no business source/test change or xfail/skip alteration',
            'historical_failures':'v1/v2 and original WSL/preflight results remain unchanged; this is explicitly a later posthoc run, never a replacement preformal gate'}}
    try:
        with ExitStack() as stack:
            for path in (out/'manifests/real_provider_execution.lock',out/'analysis/postrun_verification.lock'):
                lock=stack.enter_context(path.open('a+'))
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    report.update(status='GATE_LOCK_BUSY_NO_TESTS',busy_lock_path=str(path));return report
            report['locks']='both real-provider and postrun verification locks held exclusively'
            report['gate']=gate(out)
            if report['gate']['status']!='GATE_COMPLETE':report['status']='GATE_INCOMPLETE_NO_TESTS';return report
            algorithm=algorithm_ast(__file__)
            source_before={name:{'algorithm_sha256':_source_tree_sha256(root),'files':file_manifest(root),
                'algorithm_ast_sha256':algorithm_ast(root/'src/agent_runtime/evaluation/runner.py')}
                for name,root in (('formal',formal),('legacy',legacy))}
            if any(row['algorithm_ast_sha256']!=algorithm for row in source_before.values()):
                raise ValueError('same-sourceSHA algorithm not proven for both trees')
            freeze_path=out/'manifests/formal-freeze.json';freeze=json.loads(freeze_path.read_text())
            report['formal_freeze_source_match']=source_before['formal']['algorithm_sha256']==freeze['source_sha256']
            report['formal_freeze_sha256']=digest(freeze_path)
            report['source_trees_equal_before']=source_before['formal']['algorithm_sha256']==source_before['legacy']['algorithm_sha256']
            report['source_files_comparison']=compare(source_before['formal']['files'],source_before['legacy']['files'])
            tests_before={name:file_manifest(root,tests=True) for name,root in (('formal',formal),('legacy',legacy))}
            report['test_trees_comparison']=compare(tests_before['formal'],tests_before['legacy'])
            save(destination/'source_before.json',source_before);save(destination/'tests_before.json',tests_before)
            snapshot_upper=sum(row['size_bytes'] or 0 for tree in tests_before.values() for row in tree['files'])
            available=shutil.disk_usage(destination).free
            report['snapshot_capacity']={'upper_bound_test_bytes':snapshot_upper,'destination_available_bytes':available,'reserve_bytes':64*1024*1024}
            if snapshot_upper+64*1024*1024>available:raise ValueError('insufficient destination capacity for immutable test snapshots')
            report['test_snapshots']={name:snapshot_tests(root,tests_before[name],destination/'test_snapshots'/name)
                for name,root in (('formal',formal),('legacy',legacy))}
            dashboard,original_dashboard,dashboard_before=backup_dashboard(formal)
            try:
                env,report['execution_environment']=cleaned_environment(destination,formal,legacy,out,dashboard)
                command=[str(python),'-m','pytest','tests/unit','tests/integration','-q','--tb=short',
                         '--junitxml='+str(destination/'pytest.xml')]
                report['pytest']=execute(command,formal,env,destination,timeout)
            finally:
                report['generated_dashboard_restoration']=restore_dashboard(dashboard,original_dashboard,dashboard_before)
                generated_log=destination/'generated_dashboard_writes.jsonl'
                report['generated_dashboard_write_log_sha256']=digest(generated_log) if generated_log.is_file() else None
                source_after={name:{'algorithm_sha256':_source_tree_sha256(root),'files':file_manifest(root)}
                    for name,root in (('formal',formal),('legacy',legacy))}
                tests_after={name:file_manifest(root,tests=True) for name,root in (('formal',formal),('legacy',legacy))}
                save(destination/'source_after.json',source_after);save(destination/'tests_after.json',tests_after)
                report['source_stable']={name:source_before[name]['algorithm_sha256']==source_after[name]['algorithm_sha256'] for name in source_before}
                report['tests_stable']={name:tests_before[name]['byte_manifest_sha256']==tests_after[name]['byte_manifest_sha256'] for name in tests_before}
            report['junit']=junit_summary(destination/'pytest.xml')
            probes=[];probe_path=destination/'import_sources.jsonl'
            if probe_path.is_file():
                probes=[json.loads(line) for line in probe_path.read_text().splitlines() if line.strip()]
            all_probe_pids=sorted({row.get('pid') for row in probes})
            other_probe_rows=sum(row.get('pid')!=report['pytest']['pid'] for row in probes)
            probes=[row for row in probes if row.get('pid')==report['pytest']['pid'] and row.get('pytest_rootpath')==str(formal.resolve())]
            imports={path for row in probes for path in row.get('loaded_runtime_modules',{}).values()}
            wrong=sorted(path for path in imports if not within(Path(path).resolve(),(formal/'src').resolve()))
            conftests=sorted({path for row in probes for path in row.get('loaded_conftests',[])})
            wrong_conftests=sorted(path for path in conftests if not within(Path(path).resolve(),formal.resolve()))
            collected={item['path'] for row in probes for item in row.get('collection_items',[])}
            wrong_tests=sorted(path for path in collected if not within(Path(path).resolve(),(formal/'tests').resolve()))
            report['import_scope']={'observed_runtime_module_paths':len(imports),'outside_formal_src':wrong,
                'scope_pid':report['pytest']['pid'],'all_probe_pids':all_probe_pids,
                'nonprimary_probe_rows_retained_in_log':other_probe_rows,
                'loaded_conftests':[{'path':path,'sha256':digest(path)} for path in conftests],
                'outside_formal_conftests':wrong_conftests,'collected_test_paths':len(collected),'outside_formal_tests':wrong_tests,
                'probe_sha256':digest(probe_path) if probe_path.exists() else None,
                'status':'PROVEN_OBSERVED_MODULES_FORMAL' if imports and probes and collected and not wrong and not wrong_tests and not wrong_conftests else 'NOT_PROVEN_OR_SCOPE_MISMATCH'}
            report['blocked_network_events_sha256']=digest(destination/'blocked_network.jsonl') if (destination/'blocked_network.jsonl').is_file() else None
            passed=report['pytest']['exit_code']==0 and not report['pytest']['timed_out'] and report['formal_freeze_source_match'] and \
                report['import_scope']['status']=='PROVEN_OBSERVED_MODULES_FORMAL' and all(report['source_stable'].values()) and all(report['tests_stable'].values()) and report['generated_dashboard_restoration']['restored']
            report['status']='POSTHOC_TESTS_PASS_SOURCE_SCOPE_VERIFIED' if passed else 'POSTHOC_TEST_FAILURE_OR_SCOPE_ISSUE_RETAINED'
    except Exception as error:
        report.update(status='POSTHOC_EXECUTION_ERROR_RETAINED',error_type=type(error).__name__,
                      error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
    finally:
        report['finished_utc']=utc();save(destination/'source_test_postcheck.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--destination',required=True);parser.add_argument('--out',default=str(OUT))
    parser.add_argument('--formal-root',default=str(FORMAL));parser.add_argument('--legacy-root',default=str(LEGACY))
    parser.add_argument('--python',default=str(PYTHON));parser.add_argument('--timeout',type=int,default=3600)
    args=parser.parse_args();result=postcheck(args.destination,args.out,args.formal_root,args.legacy_root,args.python,args.timeout)
    print(json.dumps({key:result.get(key) for key in ('scope','status','paid_provider_calls_requested')}))
    raise SystemExit(0 if result['status']=='POSTHOC_TESTS_PASS_SOURCE_SCOPE_VERIFIED' else (result.get('pytest',{}).get('exit_code') or 2))
