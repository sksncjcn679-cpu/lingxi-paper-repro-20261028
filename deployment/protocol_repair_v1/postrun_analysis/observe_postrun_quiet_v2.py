"""Observe the original container only after root sees session 82436 terminate.

This is an operational observer, not an analysis or scientific-method gate.
It never imports an analyzer, reads raw, signals a process, restarts, or cleans up.
Run directly with docker exec (without an in-container shell/launcher).

Root must first write a fresh terminal-tool witness inside STUDY/analysis with:
  study_id: protocol_repair_20261020_v1
  status: ACTUAL_RETRIEVAL_CONTROLLER_TOOL_TERMINAL_OBSERVED
  observer: root
  retrieval_controller_session_id: 82436
  retrieval_controller_exec_observed_terminal_exit_code: 0
  observed_utc: actual timezone-aware tool observation time
  tool_name: the actual terminal tool name
  terminal_tool_result: the actual nonempty terminal result, as text or object
The observer binds this root-authored witness; it cannot inspect the tool session.
Witness contents and raw process command lines are never copied to the quiet JSON
or printed. Only pid/ppid/name/script basenames describe enumerated processes.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys


STUDY = 'protocol_repair_20261020_v1'
VERIFICATION = 'actual complete psutil container process enumeration after terminal controller'
METADATA = (
    'analysis/operational_observations/host_power_events_v1.json',
    'analysis/operational_observations/host_power_events_v2.json',
    'analysis/operational_observations/host_power_events_v3.json',
    'analysis/operational_observations/urgent_report_publication_v1.json',
)
LOCKS = ('formal_sequence_execution.lock', 'real_provider_execution.lock')
MAX_JSON_BYTES = 2 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    require(isinstance(value, str), 'missing observation timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'observation timestamp must have a timezone')
    return result


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            result.update(block)
    return result.hexdigest()


def local_file(out, value, analysis_only=False):
    value = Path(value)
    candidate = value if value.is_absolute() else out / value
    require(not candidate.is_symlink(), 'indirect operational evidence is forbidden')
    path = candidate.resolve(strict=True)
    boundary = out / 'analysis' if analysis_only else out
    require(path.is_relative_to(boundary) and path.is_file(), 'operational evidence escaped its required study directory')
    require(not any(part.is_symlink() for part in (candidate, *candidate.parents) if part != out.parent),
            'symlinked operational evidence is forbidden')
    return path


class Evidence:
    def __init__(self, out):
        self.out = out
        self.records = {}

    def read(self, value, analysis_only=False):
        path = local_file(self.out, value, analysis_only)
        before = path.stat()
        require(before.st_size <= MAX_JSON_BYTES, 'operational JSON exceeds the small-metadata bound')
        body = path.read_bytes()
        after = path.stat()
        require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                'operational evidence changed while being read')
        def pairs(items):
            result = {}
            for key, value in items:
                require(key not in result, 'duplicate operational JSON key')
                result[key] = value
            return result
        content = json.loads(body.decode('utf-8'), object_pairs_hook=pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite operational JSON')))
        relative = path.relative_to(self.out).as_posix()
        record = {'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
        require(relative not in self.records or self.records[relative] == record,
                'operational evidence changed between reads')
        self.records[relative] = record
        return path, content

    def stable(self):
        for relative, record in self.records.items():
            path = local_file(self.out, relative)
            require(path.stat().st_size == record['bytes'] and sha(path) == record['sha256'],
                    'bound operational evidence changed before quiet observation completed')


def processes():
    import psutil
    own_pid = os.getpid()
    own_source = Path(__file__).resolve()
    before = set(psutil.pids())
    seen = set()
    safe = []
    excluded = []
    unexpected = []
    for process in psutil.process_iter():
        # NoSuchProcess, AccessDenied, ZombieProcess, and other read failures
        # deliberately propagate. An incomplete enumeration never signs quiet.
        with process.oneshot():
            pid = process.pid
            ppid = process.ppid()
            name = process.name()
            command = process.cmdline()
        require(pid not in seen and isinstance(name, str) and name and isinstance(command, list) and
                all(isinstance(part, str) for part in command), 'process enumeration was incomplete')
        seen.add(pid)
        scripts = sorted({Path(part).name for part in command if Path(part).suffix.lower() in ('.py', '.sh')})
        row = {'pid': pid, 'ppid': ppid, 'name': Path(name).name, 'script_basenames': scripts}
        safe.append(row)
        if pid == own_pid:
            require(any(Path(part).is_absolute() and Path(part).resolve() == own_source for part in command),
                    'current probe process identity could not be established')
            excluded.append({**row, 'exclusion': 'this exact observer process'})
        elif (pid == 1 and ppid == 0 and name == 'docker-init' and command and
              Path(command[0]).name == 'docker-init' and command[1:] == ['--', 'sleep', 'infinity']):
            excluded.append({**row, 'exclusion': 'existing PID1 docker-init for sleep infinity'})
        elif (pid == 7 and ppid == 1 and name == 'sleep' and len(command) == 2 and
              Path(command[0]).name == 'sleep' and command[1] == 'infinity'):
            excluded.append({**row, 'exclusion': 'existing PID7 parent1 sleep infinity'})
        else:
            unexpected.append(row)
    require(before == seen == set(psutil.pids()), 'process population changed during complete enumeration')
    require({own_pid, 1, 7} <= seen, 'expected original init/sleep/probe identities are absent')
    require(not unexpected, 'additional container processes prevent quiet certification')
    return sorted(safe, key=lambda row: row['pid']), sorted(excluded, key=lambda row: row['pid'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-out', type=Path, required=True)
    parser.add_argument('--terminal-tool-witness', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, default=Path('analysis/postrun_window_v2.json'))
    args = parser.parse_args()
    out = args.study_out.resolve(strict=True)
    require(out.is_dir() and out.name == STUDY, 'exact original study root required')
    target = args.receipt if args.receipt.is_absolute() else out / args.receipt
    require(target.parent.is_dir() and not target.is_symlink(), 'existing receipt directory required')
    target = target.parent.resolve(strict=True) / target.name
    require(target.is_relative_to(out / 'analysis') and not target.exists(), 'quiet receipt must be fresh inside study analysis')
    evidence = Evidence(out)
    witness_path, witness = evidence.read(args.terminal_tool_witness, analysis_only=True)
    require(isinstance(witness, dict) and witness.get('study_id') == STUDY and
            witness.get('status') == 'ACTUAL_RETRIEVAL_CONTROLLER_TOOL_TERMINAL_OBSERVED' and
            witness.get('observer') == 'root' and witness.get('retrieval_controller_session_id') == 82436 and
            type(witness.get('retrieval_controller_exec_observed_terminal_exit_code')) is int and
            witness['retrieval_controller_exec_observed_terminal_exit_code'] == 0 and
            isinstance(witness.get('tool_name'), str) and bool(witness['tool_name'].strip()) and
            isinstance(witness.get('terminal_tool_result'), (str, dict)) and bool(witness['terminal_tool_result']),
            'actual root terminal-tool witness is required; a planned or still-running session is insufficient')
    witnessed = timestamp(witness.get('observed_utc'))
    _, execution = evidence.read('formal_execution_v1/execution.json')
    stages = execution.get('stages') or []
    require(execution.get('status') == 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT' and
            execution.get('finished_utc') and execution.get('supervised_child_observed_running') is not True and
            [stage.get('name') for stage in stages] == ['main', 'native_remaining', 'external'] and
            all(stage.get('status') == 'TERMINAL' and stage.get('exit_code') == 0 and stage.get('finished_utc') for stage in stages),
            'original real controller is not fully terminal')
    require(witnessed >= timestamp(execution['finished_utc']), 'tool witness predates real completion')
    _, aggregate = evidence.read('analysis/retrieval_matrix_process_status.json')
    expected = {(size, build) for size in (100, 1000, 5000, 20000) for build in (1, 2, 3)}
    require(isinstance(aggregate, list) and len(aggregate) == 12 and all(isinstance(row, dict) for row in aggregate),
            'complete twelve-worker aggregate required')
    indexed = {(row.get('size'), row.get('build')): row for row in aggregate}
    require(len(indexed) == 12 and set(indexed) == expected, 'worker identity population differs')
    timeouts = []
    for key in sorted(indexed):
        row = indexed[key]
        relative = f'analysis/retrieval_n{key[0]}_build{key[1]}_process.json'
        _, individual = evidence.read(relative)
        require(individual == row, 'aggregate and individual process receipts differ')
        if row.get('timed_out') is True:
            wall = row.get('wall_sec')
            require(row.get('returncode') is None and type(wall) in (int, float) and math.isfinite(wall) and wall >= 1200,
                    'timeout lacks the actual original 1200-second evidence')
            timeouts.append({'size': key[0], 'build': key[1],
                             'process_receipt_sha256': evidence.records[relative]['sha256'],
                             'native_worker_processes_observed_running': [], 'verification': VERIFICATION})
        else:
            require(row.get('timed_out') is False and type(row.get('returncode')) is int and row['returncode'] == 0,
                    'non-timeout worker error prevents this quiet certificate')
    metadata = {}
    for relative in METADATA:
        _, content = evidence.read(relative, analysis_only=True)
        metadata[relative] = {**evidence.records[relative], 'content': content}
    source_path = Path(__file__).resolve(strict=True)
    source_sha = sha(source_path)
    import fcntl
    with ExitStack() as stack:
        locks = []
        for name in LOCKS:
            path = local_file(out, 'manifests/' + name)
            stream = stack.enter_context(path.open('r'))
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            stat = os.fstat(stream.fileno())
            require((path.stat().st_dev, path.stat().st_ino) == (stat.st_dev, stat.st_ino), 'original execution lock identity changed')
            locks.append({'file': 'manifests/' + name, 'opened_readonly': True,
                          'operation': 'LOCK_EX | LOCK_NB', 'acquired': True,
                          'device': stat.st_dev, 'inode': stat.st_ino})
        complete_processes, exclusions = processes()
        evidence.stable()
        require(sha(source_path) == source_sha, 'observer source changed during observation')
        verified = datetime.now(timezone.utc)
        require(verified >= witnessed, 'tool witness timestamp is later than actual probe completion')
        receipt = {
            'study_id': STUDY, 'status': 'ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING',
            'verified_utc': verified.isoformat(), 'active_real_timing_processes': [],
            'active_retrieval_timing_processes': [], 'all_container_processes_except_init_sleep_and_probe': [],
            'retrieval_controller_session_id': 82436,
            'retrieval_controller_exec_observed_terminal_exit_code': 0,
            'timeout_worker_termination_checks': timeouts,
            'complete_container_processes_safe': complete_processes, 'exact_process_exclusions': exclusions,
            'original_execution_locks': locks,
            'root_terminal_tool_witness': {'relative_path': witness_path.relative_to(out).as_posix(),
                **evidence.records[witness_path.relative_to(out).as_posix()], 'observed_utc': witness['observed_utc'], 'observer': 'root'},
            'formal_execution_sha256': evidence.records['formal_execution_v1/execution.json']['sha256'],
            'observer_source_sha256': source_sha, 'operational_metadata': metadata,
            'evidence': evidence.records,
            'scope': 'actual original-container operational quiet observation; no analysis, raw read, process signal or scientific-method change',
        }
        # Exclusive creation only, while both original readonly locks remain held.
        with target.open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    print(json.dumps({'status': receipt['status'], 'receipt': target.relative_to(out).as_posix(),
                      'timeout_workers_observed_terminated': len(timeouts), 'processes': complete_processes}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Exception strings/tracebacks could contain command arguments or witness
        # contents. Expose only a safe class name; never write a failed quiet.
        print(json.dumps({'status': 'NO_QUIET_RECEIPT_SIGNED', 'error_type': type(error).__name__}), file=sys.stderr)
        sys.exit(1)
