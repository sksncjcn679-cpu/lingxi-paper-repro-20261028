"""Same-host release comparison using the frozen 1.7 task factory and evaluator."""
from pathlib import Path
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys

BASE = (Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/home/xk/version_proof_1718b_20261005')).resolve()
NEW = (Path(sys.argv[2]) if len(sys.argv) > 2 else Path('/tmp/agent-runtime-OS-SOTA1.8.6-exp')).resolve()
OUT = (Path(sys.argv[3]) if len(sys.argv) > 3 else NEW / 'release-ab.json').resolve()
OUT.parent.mkdir(parents=True, exist_ok=True)
PROGRESS = OUT.with_name('release-ab-progress.json')
SOURCES = {'1.7': BASE / 'OS-SOTA1.7-exp', '1.8.6': NEW}


def source_hash(root):
    paths = [root / 'pyproject.toml']
    for folder in ('src', 'scenarios', 'scripts'):
        paths.extend(p for p in (root / folder).rglob('*')
                     if p.is_file() and p.suffix in {'.py', '.sh', '.json', '.npz'})
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        h.update(p.relative_to(root).as_posix().encode() + b'\0' + p.read_bytes() + b'\0')
    return h.hexdigest()


def run_cell(version, kind, arm, rep):
    root = SOURCES[version]
    env = dict(os.environ)
    for key in ('AGENT_PIPELINE_TOPOLOGY', 'AGENT_CONTROL_TRANSPORT', 'AGENT_ROUTER_SOCKET'):
        env.pop(key, None)
    env.update(PYTHONPATH=str(root / 'src'), AGENT_PROJECT_ROOT=str(root),
               OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    p = subprocess.run([sys.executable, str(BASE / 'probe.py'), kind, arm, str(rep)],
                       cwd=root, env=env, capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(f'{version}/{kind}/{arm}: {p.stderr[-5000:]}')
    return json.loads(p.stdout.strip().splitlines()[-1])


def mean_rows(rows):
    return {k: statistics.mean(float(r[k]) for r in rows)
            for k in rows[0] if isinstance(rows[0][k], (int, float))}


def main():
    raw = []
    groups = {}
    cells = [('offline', 'T'), ('offline', 'S'), ('offline', 'SS'), ('memory', 'off'), ('memory', 'on')]
    for rep in range(10):
        for kind, arm in cells[rep % 5:] + cells[:rep % 5]:
            order = ['1.7', '1.8.6'] if rep % 2 == 0 else ['1.8.6', '1.7']
            PROGRESS.write_text(json.dumps({'round': rep, 'cell': [kind, arm], 'completed': len(raw)}))
            pair = {v: run_cell(v, kind, arm, rep) for v in order}
            if pair['1.7']['task_sha'] != pair['1.8.6']['task_sha']:
                raise RuntimeError('task fingerprint mismatch')
            raw.append({'repeat': rep, 'kind': kind, 'arm': arm, 'versions': pair})
            if kind == 'offline':
                groups.setdefault(f'{kind}.{arm}', []).append({v: mean_rows(p['rows']) for v, p in pair.items()})
            else:
                for phase in ('normal', 'extended'):
                    a, b = pair['1.7'][phase], pair['1.8.6'][phase]
                    if [t['answer'] for t in a['tasks']] != [t['answer'] for t in b['tasks']]:
                        raise RuntimeError('memory task answers changed')
                    groups.setdefault(f'{kind}.{arm}.{phase}', []).append({
                        v: {**p[phase]['aggregate'], 'success_count': p[phase]['success_count'],
                            'task_count': p[phase]['task_count']}
                        for v, p in pair.items()})
    summary = {}
    for name, pairs in groups.items():
        summary[name] = {}
        for key, value in pairs[0]['1.7'].items():
            if not isinstance(value, (int, float)):
                continue
            a = [float(p['1.7'][key]) for p in pairs]
            b = [float(p['1.8.6'][key]) for p in pairs]
            d = [y-x for x, y in zip(a, b)]
            delta = statistics.mean(d)
            half = 2.262 * statistics.stdev(d) / len(d)**0.5
            summary[name][key] = {'old_mean': statistics.mean(a), 'new_mean': statistics.mean(b),
                                  'new_minus_old': delta, 'ci95': [delta-half, delta+half],
                                  'change_percent': 100*delta/statistics.mean(a) if statistics.mean(a) else None}
    OUT.write_text(json.dumps({'environment': {'platform': platform.platform(), 'python': sys.version,
                                              'os_release': Path('/etc/os-release').read_text()},
                               'sources': {v: source_hash(p) for v, p in SOURCES.items()},
                               'methodology': {'rounds': 10, 'offline_inner_repeats': 3,
                                               'task_evaluator': 'frozen 1.7', 'transport': 'uds',
                                               'ordering': 'rotate modes, alternate releases'},
                               'summary': summary, 'raw': raw}, indent=2))
    PROGRESS.write_text(json.dumps({'status': 'complete', 'completed': len(raw)}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        PROGRESS.write_text(json.dumps({'status': 'failed', 'error': str(exc)}))
        raise
