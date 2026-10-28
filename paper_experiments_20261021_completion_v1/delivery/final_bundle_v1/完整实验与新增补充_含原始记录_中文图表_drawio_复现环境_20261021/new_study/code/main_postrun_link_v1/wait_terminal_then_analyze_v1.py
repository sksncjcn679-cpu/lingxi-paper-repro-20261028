"""One-shot host handoff: never read raw before the real main exits successfully.

This process only polls one small launcher receipt while collection is active.
It never launches a collection/model process and never signals the main or its
monitor. The sole child is the already-prepared bounded offline controller.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time


class StopRetained(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def need(value, code):
    if not value:
        raise StopRetained(code)


def verify_pins(configuration):
    for item in configuration['pinned_files']:
        path = Path(item['path'])
        need(path.is_file() and path.stat().st_size == item['bytes']
             and sha(path) == item['sha256'], 'LOCKED_PREPARED_SOURCE_OR_PLAN_CHANGED')


def terminal_main_count(configuration):
    """Invoked only after actual launcher terminal+strict int0 has been read."""
    plan_path = Path(configuration['formal_plan'])
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    planned = {row['run_id']: row for row in plan if row['phase'] == 'main'}
    need(len(planned) == 2160, 'LOCKED_MAIN_PLAN_NOT_2160')
    raw = Path(configuration['study']) / 'raw' / 'task_runs.jsonl'
    need(raw.is_file(), 'POST_TERMINAL_TASK_RUNS_MISSING')
    before = raw.stat()
    seen = set()
    h = hashlib.sha256()
    line_count = 0
    with raw.open('rb') as stream:
        for line in stream:
            line_count += 1
            h.update(line)
            row = json.loads(line)
            need(isinstance(row, dict), 'POST_TERMINAL_TASK_RUN_ROW_NOT_OBJECT')
            if row.get('phase') != 'main':
                continue
            run_id = row.get('run_id')
            need(isinstance(run_id, str) and run_id in planned and run_id not in seen,
                 'POST_TERMINAL_MAIN_DUPLICATE_OR_UNPLANNED')
            expected = planned[run_id]
            need(all(row.get(key) == expected[key] for key in
                     ('study_id', 'run_id', 'task_id', 'arm', 'repeat', 'model', 'topology', 'phase')),
                 'POST_TERMINAL_MAIN_PLAN_IDENTITY_MISMATCH')
            need(type(row.get('success')) is bool
                 and isinstance(row.get('status'), str) and bool(row['status'])
                 and row.get('model_evidence') == 'real_provider',
                 'POST_TERMINAL_MAIN_RECORD_NOT_COMPLETE_REAL_TERMINAL')
            seen.add(run_id)
    after = raw.stat()
    need((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
         'POST_TERMINAL_TASK_RUNS_CHANGED_DURING_COUNT')
    need(len(seen) == 2160 and seen == set(planned), 'POST_TERMINAL_MAIN_COUNT_NOT_EXACT_2160')
    return {'path': str(raw), 'bytes': after.st_size, 'sha256': h.hexdigest(),
            'total_lines_including_excluded_public': line_count, 'actual_complete_main_terminals': len(seen),
            'exact_registered_main_run_ids': True, 'all_business_failures_retained': True,
            'raw_read_only_after_actual_successful_launcher_exit': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configuration', required=True, type=Path)
    parser.add_argument('--configuration-sha256', required=True)
    args = parser.parse_args()
    need(sha(args.configuration) == args.configuration_sha256, 'HANDOFF_CONFIGURATION_SHA_CHANGED')
    configuration = json.loads(args.configuration.read_text(encoding='utf-8'))
    logs = Path(configuration['logs'])
    # This exclusive directory is the persistent single-invocation lock. It is
    # never removed or reused after failure, timeout, or successful handoff.
    if logs.exists():
        print(json.dumps({'status': 'EXISTING_HANDOFF_RETAINED_NO_SECOND_LAUNCH', 'logs': str(logs)}), flush=True)
        return 17
    logs.mkdir()
    started = now()
    started_mono = time.monotonic()
    polls = 0
    raw_started = False
    handoff_started = False
    main_receipt_sha = None
    observed_main_status = None
    observed_main_exit_code = None
    main_summary = None
    code = 1
    reason = 'UNINITIALIZED'
    child_pid = None
    witness = Path(configuration['terminal_witness'])
    analysis_out = Path(configuration['analysis_out'])
    launcher_receipt = Path(configuration['launcher_receipt'])
    save(logs / 'process.json', {'pid': os.getpid(), 'started_utc': started,
                               'configuration_sha256': args.configuration_sha256})
    save(logs / 'started.json', {'status': 'ACTUAL_HOST_HANDOFF_STARTED_WAITING_ONLY',
         'pid': os.getpid(), 'started_utc': started, 'configuration': str(args.configuration),
         'configuration_sha256': args.configuration_sha256, 'source_sha256': sha(Path(__file__)),
         'wait_limit_seconds': configuration['wait_limit_seconds'], 'poll_interval_seconds': 10,
         'watched_small_receipt': str(launcher_receipt), 'running_raw_read_or_hashed': False,
         'new_model_API_benchmark_or_second_main_launches': 0})
    print(json.dumps({'status': 'ACTUAL_HOST_HANDOFF_STARTED_WAITING_ONLY', 'pid': os.getpid(),
                      'logs': str(logs), 'wait_limit_seconds': configuration['wait_limit_seconds']}), flush=True)
    try:
        verify_pins(configuration)
        need(not witness.exists() and not analysis_out.exists(), 'EXISTING_WITNESS_OR_ANALYSIS_RETAINED_NO_RERUN')
        need(configuration['wait_limit_seconds'] == 14400, 'WAIT_LIMIT_NOT_LOCKED_FOUR_HOURS')
        # No raw existence/stat/read/hash operation occurs in this waiting loop.
        parse_failures = 0
        while True:
            if time.monotonic() - started_mono >= configuration['wait_limit_seconds']:
                raise StopRetained('MAIN_RECEIPT_FOUR_HOUR_WAIT_TIMEOUT')
            polls += 1
            if launcher_receipt.is_file():
                body = launcher_receipt.read_bytes()
                try:
                    terminal = json.loads(body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # A small receipt may be observed between open and close.
                    parse_failures += 1
                    need(parse_failures <= 3, 'LAUNCHER_RECEIPT_INVALID_JSON_RETAINED')
                else:
                    main_receipt_sha = hashlib.sha256(body).hexdigest()
                    if isinstance(terminal, dict):
                        observed_main_status = terminal.get('status')
                        value = terminal.get('actual_exit_code')
                        observed_main_exit_code = value if type(value) is int else None
                    need(isinstance(terminal, dict) and terminal.get('status') == 'ACTUAL_PROCESS_TERMINAL',
                         'LAUNCHER_RECEIPT_NOT_ACTUAL_PROCESS_TERMINAL')
                    need(type(terminal.get('actual_exit_code')) is int and terminal['actual_exit_code'] == 0,
                         'MAIN_ACTUAL_EXIT_NOT_INT_ZERO_RETAINED_NO_ANALYSIS')
                    break
            time.sleep(min(10, max(0.01, configuration['wait_limit_seconds'] - (time.monotonic() - started_mono))))
        verify_pins(configuration)
        need(not witness.exists() and not analysis_out.exists(), 'EXISTING_WITNESS_OR_ANALYSIS_RETAINED_NO_RERUN')
        raw_started = True
        main_summary = terminal_main_count(configuration)
        need(sha(launcher_receipt) == main_receipt_sha, 'LAUNCHER_RECEIPT_CHANGED_DURING_TERMINAL_CHECK')
        save(witness, {'schema_version': 'completion-main-collection-terminal-v1',
             'status': 'ACTUAL_MAIN2160_COLLECTION_TERMINAL_CONFIRMED', 'actual_terminal': True,
             'actual_exit_code': 0, 'confirmed_utc': now(), 'source_execution_receipt': str(launcher_receipt),
             'source_execution_receipt_sha256': main_receipt_sha, 'source_execution_status': 'ACTUAL_PROCESS_TERMINAL',
             'actual_complete_main_terminals': 2160, 'main_count_evidence': main_summary,
             'formal_plan': configuration['formal_plan'], 'formal_plan_sha256': sha(Path(configuration['formal_plan'])),
             'handoff_configuration_sha256': args.configuration_sha256,
             'scope': 'Collection terminal/count witness only; not all business success or completed offline analysis.'})
        command = [configuration['python'], '-X', 'utf8', '-B', configuration['postrun_controller'],
                   '--study', configuration['study'], '--native', configuration['native'],
                   '--completion-receipt', str(witness), '--out', str(analysis_out), '--confirmed-main-terminal']
        save(logs / 'single_analysis_invocation.json', {'status': 'ONE_SHOT_OFFLINE_CONTROLLER_INVOCATION',
             'started_utc': now(), 'command': command, 'terminal_witness_sha256': sha(witness),
             'no_automatic_retry': True, 'model_API_or_second_main_launches': 0})
        handoff_started = True
        with (logs / 'analysis_stdout.log').open('xb') as stdout, (logs / 'analysis_stderr.log').open('xb') as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            child_pid = process.pid
            save(logs / 'analysis_process.json', {'pid': child_pid, 'started_utc': now(),
                 'scope': 'Only this offline child; original main/monitor are never signalled.'})
            # The locked existing controller owns its 1800-second timeout and
            # may stop only its newly created offline Docker container.
            code = process.wait()
        reason = 'OFFLINE_CONTROLLER_ACTUAL_EXIT_ZERO' if code == 0 else 'OFFLINE_CONTROLLER_ACTUAL_FAILURE_RETAINED_NO_RETRY'
    except StopRetained as error:
        reason = str(error)
        code = 124 if reason == 'MAIN_RECEIPT_FOUR_HOUR_WAIT_TIMEOUT' else 2
    except Exception as error:
        # Never serialize raw rows, receipt commands, exception text or bodies.
        reason = 'HANDOFF_EXCEPTION_TYPE_' + type(error).__name__
        code = 1
    receipt = {'schema_version': 'completion-main-one-shot-handoff-execution-v1',
         'status': 'ACTUAL_HANDOFF_TERMINAL', 'actual_terminal': True, 'actual_exit_code': code,
         'reason_code': reason, 'started_utc': started, 'finished_utc': now(), 'pid': os.getpid(),
         'wait_poll_count': polls, 'elapsed_seconds': time.monotonic() - started_mono,
         'configuration_sha256': args.configuration_sha256, 'source_sha256': sha(Path(__file__)),
         'source_execution_receipt_sha256': main_receipt_sha,
         'observed_main_status': observed_main_status,
         'observed_main_actual_exit_code': observed_main_exit_code,
         'raw_terminal_count_started_after_launcher_success': raw_started,
         'main_count_evidence': main_summary, 'single_offline_analysis_started': handoff_started,
         'offline_child_pid': child_pid, 'automatic_retries': 0,
         'original_main_or_monitor_signalled_or_restarted': False,
         'new_model_API_benchmark_or_second_main_launches': 0,
         'scope': 'One-shot handoff execution; actual analysis outputs and guard still require result review.'}
    if witness.is_file():
        receipt['terminal_witness_sha256'] = sha(witness)
    save(logs / 'execution_receipt.json', receipt)
    print(json.dumps({'status': receipt['status'], 'actual_exit_code': code, 'reason_code': reason,
                      'logs': str(logs)}), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
