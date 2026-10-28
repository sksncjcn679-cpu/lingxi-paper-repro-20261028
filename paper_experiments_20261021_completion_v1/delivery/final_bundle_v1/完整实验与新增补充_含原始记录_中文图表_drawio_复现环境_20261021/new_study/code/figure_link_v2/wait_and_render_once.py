"""Bounded host bridge: wait for actual postrun completion, render once, retain failures."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback

IMAGE = 'sha256:80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58'
GUARD_SHA = '7a82d8b4263b127b938958bc6bca74d8db50f0517e2654e94547bd4c67f77e2d'
NAME = 'lingxi-completion-figures-20261021-v2'
LIMIT_SECONDS = 14400
POLL_SECONDS = 10


def now(): return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def need(ok, code):
    if not ok: raise RuntimeError(code)


def main():
    link = Path(__file__).resolve().parent
    new = link.parent.parent
    old = new.parent/'paper_experiments_20261019_pilot/protocol_repair_20261020_v1'
    figure_code = new/'code/figures_v2'
    stat_control = new/'code/main_postrun_v1'
    stat_run = new/'main_batch/analysis/main_postrun_v1'
    analysis = stat_run/'outputs/analysis'
    log = new/'logs/figure_link_v2'
    out = new/'figures_v2'
    need(not log.exists() and not out.exists(), 'FRESH_OUTPUTS_REQUIRED_NO_AUTOMATIC_REPLAY')
    log.mkdir()
    control = log/'run_control'; control.mkdir()
    source_paths = [p for p in figure_code.iterdir() if p.is_file()]
    source_paths += [p for p in link.iterdir() if p.is_file()]
    source_paths += [stat_control/'offline_analysis_runner.py']
    source_snapshot = {str(p): {'sha256': sha(p), 'bytes': p.stat().st_size} for p in source_paths}
    need(sha(stat_control/'offline_analysis_runner.py') == GUARD_SHA, 'ORIGINAL_OFFLINE_GUARD_CHANGED')
    for path in source_paths:
        if path.suffix == '.py': ast.parse(path.read_text(encoding='utf-8-sig'))
    started = now()
    save(log/'started.json', {'status': 'ACTUAL_BOUNDED_HOST_BRIDGE_STARTED', 'host_pid': os.getpid(),
        'started_utc': started, 'wait_limit_seconds': LIMIT_SECONDS, 'poll_seconds': POLL_SECONDS,
        'wait_for': str(stat_run/'execution_receipt.json'), 'source_snapshot': source_snapshot,
        'new_output': str(out), 'model_calls': 0, 'raw_model_records_read_or_hashed': False,
        'image': IMAGE, 'automatic_replays': 0, 'visual_qa': 'NOT_YET_PERFORMED'})
    print(json.dumps({'status': 'ACTUAL_BOUNDED_WAIT_STARTED', 'host_pid': os.getpid(),
                      'wait_for': str(stat_run/'execution_receipt.json')}), flush=True)
    deadline = time.monotonic()+LIMIT_SECONDS
    waiting_for_verify_since = None
    code = 1; command = None; rendered = False; reason = None
    try:
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError('BOUNDED_WAIT_EXPIRED_NO_RENDER_ATTEMPT')
            if (stat_run/'execution_receipt.json').is_file():
                execution = load(stat_run/'execution_receipt.json')
                need(execution.get('actual_terminal') is True, 'POSTRUN_TERMINAL_NOT_PROVED')
                need(type(execution.get('actual_exit_code')) is int and execution['actual_exit_code'] == 0,
                     'POSTRUN_ACTUAL_EXIT_NONZERO_NO_RENDER')
                if (stat_run/'verification_receipt.json').is_file(): break
                if waiting_for_verify_since is None: waiting_for_verify_since = time.monotonic()
                need(time.monotonic()-waiting_for_verify_since < 60, 'POSTRUN_VERIFICATION_NOT_SAVED')
            time.sleep(POLL_SECONDS)
        verify = load(stat_run/'verification_receipt.json')
        need(verify.get('status') == 'ACTUAL_NEW_MAIN_POSTRUN_VERIFIED', 'POSTRUN_NOT_VERIFIED')
        need(verify['execution_receipt_sha256'] == sha(stat_run/'execution_receipt.json'), 'POSTRUN_EXIT_HASH_DIFFERS')
        guard = load(stat_run/'outputs/guard_result.json')
        need(guard.get('exit_code') == 0 and guard.get('denied_count') == 0, 'POSTRUN_GUARD_NOT_CLEAN')
        receipt = load(analysis/'analysis_receipt.json')
        need(receipt.get('status') == 'ACTUAL_NEW_MAIN_V3_ANALYSIS_COMPLETE' and
             receipt.get('one_postrun_boundary_check_passed') is True, 'POSTRUN_ANALYSIS_NOT_COMPLETE')
        need(verify['analysis_receipt_sha256'] == sha(analysis/'analysis_receipt.json') and
             verify['guard_result_sha256'] == sha(stat_run/'outputs/guard_result.json'), 'POSTRUN_VERIFICATION_HASH_DIFFERS')
        acceptance = load(analysis/'acceptance.json')
        need(acceptance.get('status') == 'MAIN2160_EXACT_JOIN_USAGE_COMPLETE' and
             acceptance.get('planned_main_units') == acceptance.get('terminal_main_units') == 2160 and
             acceptance.get('unknown_task_usage') == 0, 'MAIN_COLLECTION_OR_USAGE_INCOMPLETE')
        for name in ('formal_primary_v3.json', 'figure_04.csv', 'acceptance.json'):
            need(receipt['output_sha256'][name] == sha(analysis/name), 'ANALYSIS_OUTPUT_HASH_DIFFERS_'+name)
        main = load(new/'main_collection_terminal.json')
        need(main.get('actual_terminal') is True and type(main.get('actual_exit_code')) is int and
             main['actual_exit_code'] == 0, 'MAIN_ACTUAL_EXIT_NOT_PROVED')
        need(all(sha(Path(p)) == record['sha256'] for p, record in source_snapshot.items()), 'PREPARED_SOURCE_CHANGED')
        seed = Path('E:/codex_rebuilds/completion_20261021_v1/seed_write/run_v3/results')
        stress = Path('E:/codex_rebuilds/completion_20261021_v1/stress_ci/output/analysis')
        old_delivery = old/'delivery/chinese_figures_v1'
        bound = {
            'old_primary': (old/'analysis/stats/formal_primary_v1.json', '/inputs/old_stats/formal_primary_v1.json'),
            'new_primary': (analysis/'formal_primary_v3.json', '/inputs/new_analysis/formal_primary_v3.json'),
            'new_acceptance': (analysis/'acceptance.json', '/inputs/new_analysis/acceptance.json'),
            'new_timing_csv': (analysis/'figure_04.csv', '/inputs/new_analysis/figure_04.csv'),
            'seed_measurements_jsonl': (seed/'measurements.jsonl', '/inputs/seed/measurements.jsonl'),
            'seed_result': (seed/'result.json', '/inputs/seed/result.json'),
            'stress_csv': (stress/'stress_cell_ci.csv', '/inputs/stress/stress_cell_ci.csv'),
            'stress_point_identity': (stress/'point_identity.json', '/inputs/stress/point_identity.json'),
            'stress_method': (stress/'method_used.json', '/inputs/stress/method_used.json'),
            'old_delivery_manifest': (old_delivery/'manifest.json', '/inputs/old_delivery/manifest.json'),
            'font': (old_delivery/'fonts/NotoSansSC-Regular.ttf', '/inputs/old_delivery/fonts/NotoSansSC-Regular.ttf'),
        }
        bindings = {name: {'path': guest, 'sha256': sha(host)} for name, (host, guest) in bound.items()}
        manifest = {'schema_version': 'completion-figure-inputs-v1', 'status': 'ACTUAL_COMPLETED_INPUTS',
            'created_utc': now(), 'old_batch_id': 'protocol_repair_20261020_v1',
            'new_batch_id': 'completion_main_20261021_v1', 'old_delivery_root': '/inputs/old_delivery',
            'bindings': bindings, 'bound_actual_analysis_receipt_sha256': sha(analysis/'analysis_receipt.json'),
            'scope': 'Saved results only; independent batches not pooled; no estimates recalculated.'}
        save(control/'actual_input_manifest.json', manifest)
        save(control/'actual_host_binding_map.json', {name: {'host_path': str(host), 'container_path': guest,
             'sha256': bindings[name]['sha256']} for name, (host, guest) in bound.items()})
        config = {'script': '/link/figure_pipeline.py', 'script_sha256': sha(link/'figure_pipeline.py'),
            'arguments': ['--manifest', '/run-control/actual_input_manifest.json', '--out', '/output/results'],
            'approved_offline_sources': {'/link/figure_pipeline.py': sha(link/'figure_pipeline.py')},
            'guard_result': '/output/guard_result.json', 'guard_events': '/output/guard_events.jsonl',
            'protected_inputs': [v['path'] for v in bindings.values()]+['/run-control/actual_input_manifest.json',
                '/run-control/guard_configuration.json', '/link/figure_pipeline.py', '/guard/offline_analysis_runner.py']+
                ['/figure-code/'+p.name for p in figure_code.iterdir() if p.is_file()],
            'writable_roots': ['/output', '/tmp']}
        save(control/'guard_configuration.json', config)
        out.mkdir()
        mounts = [(link, '/link'), (figure_code, '/figure-code'), (stat_control, '/guard'),
            (control, '/run-control'), (old/'analysis/stats', '/inputs/old_stats'),
            (analysis, '/inputs/new_analysis'), (seed, '/inputs/seed'), (stress, '/inputs/stress'),
            (old_delivery, '/inputs/old_delivery')]
        command = ['docker', 'run', '--pull', 'never', '--name', NAME, '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--cpus', '2', '--memory', '2g',
            '--pids-limit', '512', '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m',
            '--env', 'PYTHONDONTWRITEBYTECODE=1']
        for host, guest in mounts:
            command += ['--mount', f'type=bind,source={host.as_posix()},target={guest},readonly']
        command += ['--mount', f'type=bind,source={out.as_posix()},target=/output',
            '--entrypoint', '/opt/lingxi/formal-venv/bin/python', IMAGE, '-X', 'utf8', '-B',
            '/guard/offline_analysis_runner.py', '/run-control/guard_configuration.json']
        save(log/'render_started.json', {'started_utc': now(), 'command': command,
            'actual_completed_statistical_receipt_sha256': sha(stat_run/'execution_receipt.json'),
            'actual_analysis_verification_receipt_sha256': sha(stat_run/'verification_receipt.json'),
            'actual_input_manifest_sha256': sha(control/'actual_input_manifest.json'),
            'source_snapshot': source_snapshot, 'model_calls': 0, 'statistical_runs': 0})
        with (log/'stdout.log').open('wb') as stdout, (log/'stderr.log').open('wb') as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            save(log/'process.json', {'host_docker_pid': process.pid, 'host_bridge_pid': os.getpid(), 'started_utc': now()})
            try: code = process.wait(timeout=1200)
            except subprocess.TimeoutExpired:
                subprocess.run(['docker', 'stop', '--time', '5', NAME], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=20)
                try: process.wait(timeout=20)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
                code = 124
        need(code == 0, 'ACTUAL_RENDER_EXIT_NONZERO_RETAINED')
        result_guard = load(out/'guard_result.json')
        need(result_guard.get('exit_code') == result_guard.get('denied_count') == 0, 'RENDER_GUARD_NOT_CLEAN')
        pipeline = load(out/'results/pipeline_receipt.json')
        need(pipeline.get('status') == 'ACTUAL_PROJECTION_AND_RENDER_COMPLETED_VISUAL_QA_PENDING' and
             pipeline.get('rendered_file_count') == 24, 'RENDER_RECEIPT_INCOMPLETE')
        need(all(sha(host) == bindings[name]['sha256'] for name, (host, _) in bound.items()), 'BOUND_PRESENTATION_INPUT_CHANGED')
        need(all(sha(Path(p)) == record['sha256'] for p, record in source_snapshot.items()), 'PRESENTATION_SOURCE_CHANGED')
        rendered = True
    except BaseException as error:
        reason = {'type': type(error).__name__, 'reason_code': str(error),
            'frames': [{'file': f.filename, 'line': f.lineno, 'function': f.name}
                for f in traceback.extract_tb(error.__traceback__)]}
        if code == 0: code = 1
    save(log/'execution_receipt.json', {'status': 'ACTUAL_RENDER_COMPLETED_VISUAL_QA_PENDING' if rendered else 'STOPPED_RETAINED_NO_REPLAY',
        'actual_terminal': True, 'actual_exit_code': 0 if rendered else code,
        'started_utc': started, 'finished_utc': now(), 'host_pid': os.getpid(), 'image': IMAGE,
        'command': command, 'exception_metadata': reason, 'source_snapshot': source_snapshot,
        'rendered': rendered, 'visual_qa': 'NOT_YET_PERFORMED', 'package_runs': 0,
        'model_calls': 0, 'statistical_runs': 0, 'raw_model_records_read_or_hashed': False,
        'original_results_or_figure_assets_modified': False,
        'stdout_sha256': sha(log/'stdout.log') if (log/'stdout.log').is_file() else None,
        'stderr_sha256': sha(log/'stderr.log') if (log/'stderr.log').is_file() else None})
    print(json.dumps({'status': 'ACTUAL_RENDER_COMPLETED_VISUAL_QA_PENDING' if rendered else 'STOPPED_RETAINED_NO_REPLAY',
        'actual_exit_code': 0 if rendered else code, 'receipt': str(log/'execution_receipt.json')}), flush=True)
    return 0 if rendered else code


if __name__ == '__main__':
    raise SystemExit(main())
