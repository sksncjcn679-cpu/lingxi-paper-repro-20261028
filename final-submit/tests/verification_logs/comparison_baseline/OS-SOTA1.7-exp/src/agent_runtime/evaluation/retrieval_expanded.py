"""Frozen 30-query retrieval set, with provenance and multiple relevant records allowed."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import time
from pathlib import Path
import numpy as np
from agent_runtime.evaluation.runner import _env_info, _allocate_result_dir, _write_checksum_manifest
from agent_runtime.memory.bm25 import scores, fused_order
from agent_runtime.providers.docsem_embedding import DocSemEmbedding
from agent_runtime.providers.lexical_embedding import LexicalEmbedding

# Frozen before the new benchmark is run. No parameter tuning or 70% acceptance threshold.
CASES = [
 ('bind','service startup failed because port 8080 is occupied','daemon cannot bind its listening address: address already in use'),
 ('abi','python extension cannot load because the CPython ABI is incompatible','native module targets a different interpreter binary interface'),
 ('access','systemd unit lacks permission to read its state directory','service account is denied access to the runtime data path'),
 ('arch','openEuler package needs an i686 native library rebuilt','32-bit dependency is unavailable on the x86_64 target'),
 ('backlog','network listener backlog is too small for burst traffic','connection queue overflows under concurrent arrivals'),
 ('dns','DNS resolver cannot resolve the upstream service hostname','registry name lookup returns SERVFAIL while its numeric address is reachable'),
 ('disk','filesystem has no free disk space so writes fail','log appends fail ENOSPC with df reporting zero available capacity'),
 ('oom','kernel OOM killer terminated a process at the memory limit','worker disappeared after cgroup memory.events increased oom_kill'),
 ('selinux','SELinux policy denies a service bind operation','audit AVC reports name_bind despite normal Unix file permissions'),
 ('fdlimit','process reached the open file descriptor limit','accept fails EMFILE after too many socket handles remain live'),
 ('inode','filesystem exhausted inodes despite spare byte capacity','cannot create another small file although df shows unused gigabytes'),
 ('readonly','mount is read-only and refuses updates','configuration save returns EROFS on the mounted volume'),
 ('dependency','systemd start failed because its required dependency was stopped','unit waits on Requires service that is inactive'),
 ('execformat','binary executable format does not match the machine','program launch returns Exec format error on a different CPU architecture'),
 ('interpreter','script shebang names a nonexistent interpreter','executable script exists but its first-line runtime path is missing'),
 ('symlink','symbolic links form a circular chain','path lookup hits ELOOP from mutually referring links'),
 ('zombie','parent did not reap exited child processes','defunct workers accumulate because waitpid is never called'),
 ('deadlock','two threads wait for locks in opposite order','request hangs when mutex A and mutex B are acquired inconsistently'),
 ('cpuquota','container CPU quota throttles an otherwise idle host','cgroup cpu.stat nr_throttled rises and request latency spikes'),
 ('tcpreset','peer forcibly closed an established TCP connection','client sees ECONNRESET during an existing session'),
 ('tls','TLS certificate trust verification failed','HTTPS client rejects the issuer chain although TCP connect works'),
 ('routing','network destination is unreachable because no route exists','connect returns ENETUNREACH with an empty routing table'),
 ('sharedlib','dynamic loader cannot locate the required shared object','startup reports libexample.so not found in the search paths'),
 ('locale','UTF-8 data is decoded under an ASCII locale','Chinese log text raises UnicodeDecodeError when LANG is C'),
 ('clock','clock skew makes signed credentials appear expired','authentication rejects a valid ticket after local wall time drifts'),
 ('bind','EADDRINUSE: Address already in use','daemon bind collides with another live listener'),
 ('access','EACCES: Permission denied','service identity is forbidden from opening its state file'),
 ('disk','ENOSPC: No space left on device','write request fails because storage has zero remaining capacity'),
 ('alloc','ENOMEM: Out of memory','allocation of additional pages exhausts available RAM'),
 ('missing','ENOENT: No such file or directory','configuration pathname resolves to a missing target'),
]
SOURCE = 'https://docs.python.org/3/library/errno.html'

def ranking_metrics(rank, relevant):
    first = next((k for k, i in enumerate(rank, 1) if i in relevant), None)
    retrieved = relevant & set(rank[:3])
    return {'p_at_1': float(bool(rank) and rank[0] in relevant),
            'recall_at_3': len(retrieved)/len(relevant) if relevant else 0.0,
            'hit_at_3': float(bool(retrieved)), 'mrr': 1/first if first is not None else 0.0,
            'ranked': rank, 'relevant_indices': sorted(relevant)}

def run():
    texts = [x[1] for x in CASES]
    queries = [x[2] for x in CASES]
    summaries = {}
    for name, provider in [('lexical', LexicalEmbedding(dim=256)), ('docsem', DocSemEmbedding(dim=256))]:
        t0 = time.perf_counter()
        vectors = provider.embed(texts + queries)
        rows = []
        for index, (concept, _, query) in enumerate(CASES):
            semantic = (vectors[:len(texts)] @ vectors[len(texts)+index]).tolist()
            order = sorted(range(len(texts)), key=lambda i: (-semantic[i], i))
            lexical = scores(query, texts)
            # Vector top-20 then BM25 fusion, exactly the documented candidate limit.
            pool = order[:20]
            fused = fused_order([semantic[i] for i in pool], [lexical[i] for i in pool])
            reordered = [pool[i] for i in fused]
            relevant = {i for i, x in enumerate(CASES) if x[0] == concept}
            rows.append({'query': query, 'concept': concept, 'source_kind': 'official_documentation_anchored' if index >= 25 else 'team_authored',
                         'semantic': ranking_metrics(order,relevant), 'bm25_rerank': ranking_metrics(reordered,relevant)})
        for strategy in ('semantic', 'bm25_rerank'):
            selected = [r[strategy] for r in rows]
            summaries[f'{name}_{strategy}'] = {k: float(np.mean([r[k] for r in selected])) for k in ('p_at_1','recall_at_3','hit_at_3','mrr')}
            summaries[f'{name}_{strategy}']['rows'] = rows
            summaries[f'{name}_{strategy}']['total_sec'] = time.perf_counter()-t0
    return {'suite':'retrieval_expanded','generated_at_utc':datetime.now(timezone.utc).isoformat(),'env':_env_info(),
            'methodology':{'query_count':len(CASES),'memory_count':len(CASES),'semantic_candidate_count':20,
                 'bm25':{'k1':1.2,'b':0.75,'rrf_constant':60},
                 'dataset_sha256':hashlib.sha256(json.dumps(CASES,ensure_ascii=False).encode()).hexdigest(),
                 'provenance':'25 team-authored pairs; 5 anchored to verified Python errno documentation, queries still team-authored',
                 'sources':[SOURCE], 'external_blind_test':False,
                 'limits':'small diagnostic corpus, not neural embedding generalization; reranker improvement not assumed'},'summary':summaries}

def run_cli(args):
    p=run(); out=_allocate_result_dir('results','retrieval_expanded')
    (out/'summary.json').write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Expanded retrieval benchmark — frozen N=30','','| Provider/strategy | P@1 | Recall@3 | MRR |','|---|---:|---:|---:|']
    for name,r in p['summary'].items():
        lines.append(f"| {name} | {r['p_at_1']:.1%} | {r['recall_at_3']:.1%} | {r['mrr']:.3f} |")
    lines += ['',p['methodology']['provenance'],p['methodology']['limits'],f"Source SHA-256: `{p['env']['source_tree_sha256']}`",'']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8'); _write_checksum_manifest(out)
    print('\n'.join(lines)); print(f'Results: {out}'); return 0
