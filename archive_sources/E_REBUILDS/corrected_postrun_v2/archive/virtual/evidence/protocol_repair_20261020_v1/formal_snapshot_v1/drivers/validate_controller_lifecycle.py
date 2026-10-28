"""Actual OS subprocess lifecycle check using isolated dummy children, no model."""
from pathlib import Path
import json,sys,subprocess,time,os,signal,hashlib
import psutil
HERE=Path(__file__).resolve().parent
DEST=Path('/evidence/protocol_repair_20261020_v1/calibration/controller_lifecycle_v1')
DEST.mkdir(exist_ok=False)
results=[]
for scenario in ['controller_interrupted','child_nonzero']:
    base=DEST/scenario;base.mkdir();(base/'manifests').mkdir();snap=base/'snapshot';snap.mkdir()
    (base/'manifests/formal-freeze.json').write_text('{}')
    dummy=snap/'dummy.py'
    dummy.write_text('import time; time.sleep(20)\n' if scenario=='controller_interrupted' else 'raise SystemExit(7)\n')
    (snap/'execution_plan.json').write_text(json.dumps({'stages':[{'name':'dummy','script':'dummy.py','args':[]}]}))
    code=("import importlib.util,pathlib; "
          f"s=importlib.util.spec_from_file_location('lifecycle_controller',{str(HERE/'formal_sequence.py')!r}); "
          "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
          f"m.OUT=pathlib.Path({str(base)!r}); m.SNAP=pathlib.Path({str(snap)!r}); "
          f"m.validate=lambda:{{'driver_snapshot':{str(snap)!r}}}; m.run()")
    with (base/'parent.stdout.log').open('x') as out,(base/'parent.stderr.log').open('x') as err:
        parent=subprocess.Popen([sys.executable,'-B','-c',code],stdout=out,stderr=err)
        status=base/'formal_execution_v1/execution.json'
        if scenario=='controller_interrupted':
            deadline=time.monotonic()+5;child_pid=None
            while time.monotonic()<deadline:
                if status.exists():
                    try:
                        observed=json.loads(status.read_text())
                        if observed.get('stages') and observed['stages'][0].get('pid'):
                            child_pid=observed['stages'][0]['pid'];break
                    except ValueError:pass
                time.sleep(.02)
            if child_pid is None:
                parent.terminate();parent.wait(timeout=5);raise AssertionError('dummy child never observed')
            os.kill(parent.pid,signal.SIGTERM)
            parent_code=parent.wait(timeout=5)
            receipt=json.loads(status.read_text());child=psutil.Process(child_pid)
            assert parent_code!=0 and child.is_running() and child.status()!=psutil.STATUS_ZOMBIE
            assert receipt['status']=='CONTROLLER_ERROR_CHILD_STILL_RUNNING_DO_NOT_RELAUNCH'
            assert receipt['supervised_child_observed_running'] is True and 'finished_utc' not in receipt
            assert receipt['controller_finished_utc'] and receipt['stages'][0]['process_group_id']==child_pid
            # The only terminated group is verified to execute this exact dummy file.
            assert str(dummy) in child.cmdline() and os.getpgid(child_pid)==child_pid
            os.killpg(child_pid,signal.SIGTERM)
            gone,alive=psutil.wait_procs([child],timeout=5)
            assert not alive,'isolated dummy cleanup failed'
            results.append({'scenario':scenario,'status':'PASS','controller_exit':parent_code,
                            'child_running_after_controller_exit':True,'false_whole_finished_timestamp':False,
                            'dummy_child_group_cleanup_verified':True})
        else:
            parent_code=parent.wait(timeout=5);receipt=json.loads(status.read_text())
            assert parent_code!=0 and receipt['stages'][0]['exit_code']==7
            assert receipt['status']=='ERROR_RETAINED_NO_AUTOMATIC_REPLAY' and receipt['finished_utc']
            assert receipt['supervised_child_observed_running'] is False
            results.append({'scenario':scenario,'status':'PASS','controller_exit':parent_code,
                            'dummy_child_exit':7,'whole_controller_error_records_confirmed_child_terminal':True})
report={'status':'PASS_REAL_OS_SUBPROCESS_LIFECYCLE_WITH_DUMMIES','paid_api_calls':0,'model_clients_imported':False,
        'cases':results,'controller_sha256':hashlib.sha256((HERE/'formal_sequence.py').read_bytes()).hexdigest(),
        'validator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(DEST/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
