"""Reusable 200ms process-group monitor with stdout/stderr, exit and timeout records."""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import time
import psutil

OUT=Path('/evidence/formal_20261019')

def main():
    name=sys.argv[1];timeout=float(sys.argv[2]);command=sys.argv[3:]
    log=OUT/'logs/mechanisms'/name
    resource_path=OUT/'raw'/f'resources_{name}.jsonl'
    if resource_path.exists():raise RuntimeError('recording exists; choose explicit new version, never overwrite')
    started=time.perf_counter();timed_out=False
    with log.with_suffix('.stdout.log').open('x') as stdout,log.with_suffix('.stderr.log').open('x') as stderr,resource_path.open('x') as samples:
        process=subprocess.Popen(command,cwd='/opt/lingxi/formal-checkout',stdout=stdout,stderr=stderr,start_new_session=True)
        try:
            while process.poll() is None:
                rows=[]
                try:
                    root=psutil.Process(process.pid)
                    for p in [root]+root.children(recursive=True):
                        try:rows.append({'pid':p.pid,'rss_bytes':p.memory_info().rss,'cpu_sec':sum(p.cpu_times()[:2]),'fd_count':p.num_fds()})
                        except psutil.Error:pass
                except psutil.Error:pass
                samples.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'monotonic_ns':time.perf_counter_ns(),
                    'processes':rows,'loadavg':os.getloadavg(),'interval_sec':.2})+'\n');samples.flush()
                if time.perf_counter()-started>timeout:
                    import signal
                    os.killpg(process.pid,signal.SIGTERM);timed_out=True;break
                time.sleep(.2)
            code=process.wait(timeout=10)
        finally:
            if process.poll() is None:
                import signal
                os.killpg(process.pid,signal.SIGKILL);process.wait()
    result={'name':name,'command':command,'exit_code':code,'timed_out':timed_out,'wall_sec':time.perf_counter()-started,
        'sampling_interval_sec':.2,'finished_utc':datetime.now(timezone.utc).isoformat()}
    log.with_suffix('.result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
    raise SystemExit(code)

if __name__=='__main__':main()
