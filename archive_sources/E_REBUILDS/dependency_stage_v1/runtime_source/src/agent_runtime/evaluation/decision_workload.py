"""Paired protocol x memory factorial experiment with synchronous, consumed model output."""
from __future__ import annotations
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import time
import numpy as np

from agent_runtime.agents.model_decision import LABEL_ACTIONS, PROCEDURE
from agent_runtime.config import Config, LLMConfig, load_config
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.evaluation.stats import paired_ci95
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.base import LLMResponse
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.runtime.task import Task, VerdictResult

ARMS = {'T': ('text', False, False), 'SS': ('structured', True, False),
        'E_text_memory': ('text', False, True), 'SS_memory': ('structured', True, True)}

def make_tasks():
    examples = [
        ('port_conflict', 'api refuses to bind 8080: EADDRINUSE; an unrelated disk warning occurred yesterday', 'ss: legacyd listening on :8080'),
        ('permission_denied', 'worker fails to open /var/lib/app/state: EACCES; listen port 8081 is free', 'stat: state directory owned root mode 0700; service uid app'),
        ('dns_failure', 'registry hostname lookup returns EAI_AGAIN; IP route and disk are healthy', 'resolver: SERVFAIL for registry.example; /etc/resolv.conf has nameserver'),
        ('disk_full', 'logging daemon append fails ENOSPC; network and credentials are healthy', 'df: /var 100% used, 0 available bytes'),
        ('oom_killed', 'kernel log: Out of memory: Killed process worker; root filesystem has free space', 'memory.events: oom_kill 3; memory.current near memory.max'),
        ('selinux_denied', 'audit AVC denied name_bind; Unix permissions and port availability are valid', 'audit: avc denied { name_bind } scontext=app_t tcontext=port_t'),
        ('compatible', 'target openEuler x86_64; wheel targets cp312 x86_64 and interpreter is CPython 3.12; all required libraries available', 'ABI check: architecture, CPython ABI and native libraries match'),
        ('rebuild_required', 'target CPython 3.12 x86_64; native module built cp310 i686, cannot import; disk and permissions normal', 'ABI check: cp310 != cp312 and i686 != x86_64'),
    ]
    tasks = []
    for index, (label, symptom, probe) in enumerate(examples):
        action = LABEL_ACTIONS[label]
        expected = {'diagnosis': label, 'action': action, 'probe': probe}
        def verify(outputs, expected=expected):
            actual = outputs.get('answer')
            return VerdictResult(success=actual == expected, quality=float(actual == expected), detail={'expected': expected, 'actual': actual})
        checks = {'inspect_listener': 'ss: configured service port is free',
                  'inspect_permissions': 'stat: service uid owns runtime directory, mode 0750',
                  'inspect_resolver': 'resolver: NOERROR, hostname has a reachable address',
                  'inspect_disk': 'df: /var 20% used, free bytes available',
                  'inspect_memory': 'memory.events: oom_kill 0, current usage below limit',
                  'inspect_audit': 'audit: no AVC denial for this service',
                  'keep_binary': 'ABI check: architecture and interpreter ABI match',
                  'rebuild_binary': 'module metadata: cp312 x86_64; interpreter CPython 3.12 x86_64'}
        checks[action] = probe
        observations = {'incident_id': f'case-{index}', 'symptom': symptom, 'checks': checks,
                        'journal_context': '\n'.join(f'worker health-check {k}: loaded configuration; waiting for request' for k in range(12))}
        tasks.append(Task(task_id=f'decision-{index}', topic='linux-incident-reasoning', description='Diagnose the incident and choose its next read-only evidence check.',
                          payload={'question': 'llm_diagnose', 'operands': ['observations', 'procedure'],
                                   'defect': 'incident', 'facts': {'observations': observations, 'procedure': PROCEDURE}},
                          expected={'answer': expected}, verifier=verify, tags=['linux', 'incident'],
                          parent_task_id=tasks[-1].task_id if tasks else None))
    assert len({json.dumps(t.payload, sort_keys=True) for t in tasks}) == len(tasks)
    return tasks

class FixtureDecisionProvider:
    """Test-only deterministic fixture. Its results are explicitly never real model evidence."""
    name = 'decision-fixture'
    model = 'offline-decision-fixture'
    def complete(self, prompt, **kwargs):
        from agent_runtime.providers.tokenizer import HeuristicTokenizer
        observations = json.loads(prompt.split('\nObservations: ', 1)[1].split('\n\n', 1)[0])
        symptom = observations['symptom']
        cues = [('EADDRINUSE', 'port_conflict'), ('EACCES', 'permission_denied'), ('EAI_AGAIN', 'dns_failure'),
                ('ENOSPC', 'disk_full'), ('Out of memory', 'oom_killed'), ('AVC', 'selinux_denied'),
                ('built cp310', 'rebuild_required')]
        label = next((label for cue, label in cues if cue in symptom), 'compatible')
        text = json.dumps({'diagnosis': label, 'action': LABEL_ACTIONS[label]})
        tok = HeuristicTokenizer()
        return LLMResponse(text=text, prompt_tokens=tok.count(prompt), completion_tokens=tok.count(text), model=self.model)

def run_decision_workload(*, repeat=5, real=False, agent_processes=False, checkpoint=None, progress=None):
    if repeat < 1:
        raise ValueError('repeat must be positive')
    base = load_config(load_env=real) if real else Config(llm=LLMConfig(provider='mock'))
    if real and (base.llm.provider in {'mock', 'mock-gateway'} or not base.llm.api_key):
        raise ValueError('real decision workload requires an explicitly configured real provider')
    cfg = base.with_overrides(llm_coalesce='off', llm_advisory=False, llm_judge=False,
            llm_context_mode='inbound', text_style='lean', pipeline_topology='request_reply',
            llm=replace(base.llm, max_tokens=512, output_contract='none', temperature=0.0, max_retries=1, timeout_sec=60),
            control_transport='uds' if hasattr(socket, 'AF_UNIX') else 'inproc',
            state_shared_memory=hasattr(socket, 'AF_UNIX'), agent_processes=agent_processes)
    if real:
        cfg = cfg.with_overrides(tokenizer_backend='tiktoken')
    tasks = make_tasks()
    raw = []
    cp = Path(checkpoint) if checkpoint else None
    # Resume only a matching source, provider and workload protocol. Secret values are never saved.
    source = _env_info()['source_tree_sha256']
    identity = {'source_sha': source, 'model': cfg.llm.model if real else 'offline-decision-fixture',
                'base_url': cfg.llm.base_url if real else None, 'agent_processes': agent_processes}
    done_repeats = set()
    if cp and cp.exists():
        for line in cp.read_text(encoding='utf-8').splitlines():
            cell = json.loads(line)
            if cell.get('identity') != identity:
                raise ValueError('checkpoint identity differs from frozen source/provider')
            if len(cell.get('rows', [])) == len(tasks) * len(ARMS):
                raw.extend(cell['rows']); done_repeats.add(cell['repeat'])
    for rep in range(repeat):
        if rep in done_repeats:
            continue
        memories = {arm: MemoryStore(build_embedding(cfg)) for arm, (_, _, mem) in ARMS.items() if mem}
        block = []
        try:
            # Interleave arms for EACH task. Each arm owns a separate, initially empty memory.
            # Task order remains causal for related-task memory. Rotating arm order limits drift.
            for ti, task in enumerate(tasks):
                order = list(ARMS)
                shift = (rep + ti) % len(order)
                order = order[shift:] + order[:shift]
                for arm in order:
                    mode, state, memory_on = ARMS[arm]
                    ctx = build_context(cfg, mode, task_id=task.task_id, use_memory=memory_on,
                                        use_state_exchange=state, use_sandbox=False, memory=memories.get(arm))
                    if not real:
                        ctx.llm = FixtureDecisionProvider()
                    start = time.perf_counter()
                    result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
                    wall = time.perf_counter() - start
                    m = result.metrics
                    row = {'repeat': rep, 'task_index': ti, 'arm': arm, 'success': result.success,
                           'answer': result.outputs.get('answer'), 'wall_sec': wall, 'messages': m.message_count,
                           'communication_tokens': m.text_tokens, 'message_chars': m.text_chars,
                           'wire_bytes': m.wire_bytes, 'ipc_frames': m.ipc_frames, 'ipc_verified': m.ipc_verified,
                           'control_bytes': m.ipc_framed_bytes + m.ipc_ack_bytes if m.ipc_frames else m.wire_bytes,
                           'state_bytes': m.state_bytes, 'state_shm_transfers': m.state_shm_transfers,
                           'artifact_bytes': m.artifact_bytes, 'provider_calls': m.llm_calls,
                           'prompt_tokens': m.llm_prompt_tokens, 'completion_tokens': m.llm_completion_tokens,
                           'cached_prompt_tokens': m.llm_cached_prompt_tokens, 'memory_used': m.memory_used,
                           'memory_effective': m.memory_effective, 'memory_fast_path_hits': m.memory_fast_path_hits,
                           'execution_provenance': ctx.blackboard.get('execution_provenance'),
                           'usage_source': ctx.blackboard.get('decision_usage_source'),
                           'memory_lineage': ctx.blackboard.get('memory_lineage'), 'error': result.error}
                    row['total_model_tokens'] = row['prompt_tokens'] + row['completion_tokens']
                    row['strict_transport_bytes'] = row['control_bytes'] + row['state_bytes'] + row['artifact_bytes']
                    block.append(row)
                    if progress:
                        progress({'repeat': rep + 1, 'task': ti + 1, 'arm': arm, 'success': result.success, 'wall_sec': round(wall, 3)})
        finally:
            for memory in memories.values():
                memory.close()
        raw.extend(block)
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            with cp.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'identity': identity, 'repeat': rep, 'rows': block}, ensure_ascii=False) + '\n')
    summary = {}
    for arm in ARMS:
        rows = [r for r in raw if r['arm'] == arm]
        latency = [r['wall_sec'] for r in rows]
        summary[arm] = {'tasks': len(rows), 'successes': sum(r['success'] for r in rows),
                        'accuracy': sum(r['success'] for r in rows)/max(1, len(rows)),
                        'wall_p50_sec': float(np.percentile(latency, 50)), 'wall_p95_sec': float(np.percentile(latency, 95)),
                        **{key: sum(r[key] for r in rows) for key in ('messages','communication_tokens','message_chars','control_bytes',
                           'state_bytes','artifact_bytes','strict_transport_bytes','prompt_tokens','completion_tokens','cached_prompt_tokens',
                           'total_model_tokens','provider_calls','memory_used','memory_effective','memory_fast_path_hits')}}
    comparisons = {}
    for control, treatment in [('T','SS'), ('T','E_text_memory'), ('SS','SS_memory'), ('E_text_memory','SS_memory')]:
        paired = {}
        for metric in ('total_model_tokens', 'strict_transport_bytes', 'wall_sec'):
            a = [sum(r[metric] for r in raw if r['arm']==control and r['repeat']==rep) for rep in range(repeat)]
            b = [sum(r[metric] for r in raw if r['arm']==treatment and r['repeat']==rep) for rep in range(repeat)]
            paired[metric] = {'control_total': sum(a), 'treatment_total': sum(b),
                              'saving': 1-sum(b)/sum(a) if sum(a) else None,
                              'paired_ci95_difference': paired_ci95(a,b) if repeat >= 2 else None}
        comparisons[f'{treatment}_vs_{control}'] = paired
    return {'suite': 'decision_workload', 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'env': _env_info(tokenizer_backend=cfg.tokenizer_backend), 'provider': identity, 'real_provider': real,
            'methodology': {'repeat': repeat, 'tasks_per_arm': len(tasks), 'arms': ARMS,
                'output_contract': 'same two-key JSON; max_tokens=512; synchronous decisions; advisory off',
                'input': 'same observations and same read-only action options; no expected answer in model prompt',
                'memory_tasks': '8 distinct incidents; no exact-repeat cache',
                'order': 'task-level interleaving with rotating arm order',
                'claim_boundary': 'fixture is test-only; real success requires model decision consumed by executor and joint verifier',
                'currency_cost': 'not calculated; provider usage tokens and cached input reported separately'},
            'summary': summary, 'comparisons': comparisons, 'raw': raw}

def write_results(payload, root='results'):
    out = _allocate_result_dir(str(root), 'decision_workload')
    (out/'summary.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Model decisions consumed by the task workflow', '',
             'Real provider usage' if payload['real_provider'] else 'OFFLINE FIXTURE — not real LLM quality or billing evidence', '',
             '| Arm | Accuracy | Calls | Prompt/completion | Cached input | Communication tokens | Strict transport B | Wall p50/p95 s |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for arm, s in payload['summary'].items():
        lines.append(f"| {arm} | {s['successes']}/{s['tasks']} | {s['provider_calls']} | {s['prompt_tokens']}/{s['completion_tokens']} | {s['cached_prompt_tokens']} | {s['communication_tokens']} | {s['strict_transport_bytes']} | {s['wall_p50_sec']:.3f}/{s['wall_p95_sec']:.3f} |")
    lines += ['', 'Text+Memory uses the same store and task sequence as Structured+State+Memory. No headline assumes an improvement in quality or latency.',
              'Communication token counting and provider usage are separate; bytes on unsupported IPC hosts are serialization/materialization measurements.',
              f"Source SHA-256: `{payload['env']['source_tree_sha256']}`", '']
    (out/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    _write_checksum_manifest(out)
    return out

def run_cli(args):
    p = run_decision_workload(repeat=args.repeat, real=args.real, agent_processes=args.agent_processes,
                             checkpoint=args.checkpoint, progress=lambda v: print(json.dumps(v), flush=True))
    out = write_results(p)
    print(f'Results: {out}', flush=True)
    return 0 if all(s['accuracy']==1 for s in p['summary'].values()) else 1
