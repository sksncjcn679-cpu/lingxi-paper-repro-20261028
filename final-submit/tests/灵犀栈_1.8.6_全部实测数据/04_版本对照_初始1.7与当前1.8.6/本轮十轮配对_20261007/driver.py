from pathlib import Path
import hashlib, json, os, platform, subprocess, sys, tarfile, time

OUT = Path('/home/xk/version_ab186_20261007')
BASE = Path('/home/xk/version_proof_1718b_20261005')
NEW = Path('/tmp/agent-runtime-OS-SOTA1.8.6-exp')
OUT.mkdir(exist_ok=True)
if '--launch' in sys.argv:
    log = (OUT / 'driver.log').open('w')
    p = subprocess.Popen([sys.executable, '-u', __file__], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    print(json.dumps({'pid': p.pid, 'out': str(OUT)}))
    raise SystemExit

status = {'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'status': 'running', 'scope': 'same-host frozen evaluator UDS release AB, concurrent workload environment'}
def save():
    (OUT / 'status.json').write_text(json.dumps(status, indent=2))
save()
env = dict(os.environ, AGENT_LLM_PROVIDER='mock', AGENT_EMBEDDING_PROVIDER='docsem', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
command = [sys.executable, str(OUT / 'reproduce_release_ab.py'), str(BASE), str(NEW), str(OUT / 'release-ab.json')]
method = {'command': command, 'rounds': 10, 'offline_inner_repeats': 3, 'ordering': 'rotate modes, alternate releases', 'task_evaluator': 'frozen 1.7', 'transport': 'uds', 'environment': platform.platform(), 'python': sys.version, 'caveat': 'Other suites and real API runs execute concurrently. Latency and its CI describe this concurrent run and are not isolated-host performance proof.', 'baseline_probe_sha256': hashlib.sha256((BASE / 'probe.py').read_bytes()).hexdigest(), 'env_override': {k: env[k] for k in ('AGENT_LLM_PROVIDER','AGENT_EMBEDDING_PROVIDER','OPENBLAS_NUM_THREADS','OMP_NUM_THREADS')}}
(OUT / 'methodology.json').write_text(json.dumps(method, indent=2))
try:
    with (OUT / 'run.log').open('w') as log:
        p = subprocess.run(command, cwd=OUT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    status.update(status='complete' if p.returncode == 0 else 'failed', returncode=p.returncode)
except Exception as exc:
    status.update(status='failed', error=str(exc))
status['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
save()
with tarfile.open('/tmp/version_ab186_20261007.tar.gz', 'w:gz') as tar:
    tar.add(OUT, arcname=OUT.name)
