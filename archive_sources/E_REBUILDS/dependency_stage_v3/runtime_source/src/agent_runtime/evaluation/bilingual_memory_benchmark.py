"""fable7: bilingual (Chinese query -> English memory) shared-memory retrieval benchmark.

Why: the contest scenarios are operated by Chinese-speaking users while historical memories
(logs, runbooks, manifests) are largely English.  The fable5 lexical provider matched only
``[a-z0-9_]`` so every Chinese query collapsed to one constant vector.  This suite measures, on
the SAME memories and queries, three embedding providers used by the runtime's MemoryStore:

* ``mock``            — the default deterministic mock (whitespace tokens + char 3-grams);
* ``lexical_legacy``  — the fable5 lexical features (Latin-only), reproduced verbatim;
* ``lexical``         — fable7 lexical: + CJK unigram/bigram + bilingual concept normalisation.

Queries come in three families: Chinese paraphrases (zh), English paraphrases (en) and
code-mixed queries (mix).  Every memory has hard negatives from the same domain.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from agent_runtime.providers.base import l2_normalize
from agent_runtime.providers.lexical_embedding import LexicalEmbedding
from agent_runtime.providers.mock import MockEmbedding

# (memory text, [zh query, en paraphrase, mixed query])
CORPUS: list[tuple[str, list[str]]] = [
    ("api.service failed to start because port 8080 is already in use by legacyd",
     ["api 服务启动失败，8080 端口被其他进程占用", "api daemon cannot bind 8080, address in use", "api.service 启动报 bind 端口冲突"]),
    ("worker.service start failure: permission denied opening /var/lib/app/state",
     ["worker 服务启动时访问状态目录权限被拒绝", "worker unit crashes with access denied on state dir", "worker.service 权限 denied 无法启动"]),
    ("redis dependency inactive so the web service times out on startup",
     ["web 服务启动超时，原因是依赖的 redis 没有运行", "web unit startup hangs because redis requirement is down", "web 启动卡住 redis 依赖未激活"]),
    ("python app migration to aarch64 blocked by i686-only native extension, rebuild required",
     ["应用迁移到 aarch64 架构，原生扩展只支持 i686，需要重新编译", "moving the app to arm64 needs recompiling the i686 native module", "迁移 aarch64 native 扩展需要 rebuild"]),
    ("all python packages are pure py3 wheels, migration to riscv64 is compatible",
     ["迁移到 riscv64 时所有软件包都是纯 python，完全兼容", "riscv64 porting is fine since every wheel is pure python", "riscv 迁移 纯 py3 包 兼容"]),
    ("glibc version mismatch breaks the rpm package on openEuler 24.03",
     ["openEuler 24.03 上 rpm 软件包因 glibc 版本不匹配无法安装", "rpm install fails on openEuler because of glibc incompatibility", "openEuler rpm glibc 不兼容"]),
    ("tune vm.swappiness and dirty_ratio kernel sysctl for memory pressure",
     ["内存压力大时调整内核的 swappiness 和 dirty_ratio 参数", "adjust sysctl swap and dirty page settings under memory pressure", "内存压力 sysctl swappiness 调优"]),
    ("oom killer terminated the database because rss exceeded the cgroup memory limit",
     ["数据库因为内存超过 cgroup 限制被 OOM 杀掉", "database killed by out-of-memory when ram exceeded container limit", "数据库 OOM cgroup 内存 超限"]),
    ("switch the cpu scheduler policy to reduce tail latency of the network service",
     ["调整 CPU 调度策略以降低网络服务的尾延迟", "change scheduling policy to cut p99 latency of the net daemon", "sched 策略 降低 网络 尾延迟"]),
    ("disk io saturation on the ext4 filesystem slows journald log writes",
     ["磁盘 IO 饱和导致 journald 写日志变慢", "journal logging is slow because storage io is saturated", "ext4 磁盘 io 饱和 日志 变慢"]),
    ("nginx config syntax error prevents reload after certificate rotation",
     ["证书轮换后 nginx 配置语法错误，无法重新加载", "nginx reload fails due to a bad configuration after cert renewal", "nginx 配置 syntax 错误 reload 失败"]),
    ("sshd listen port changed to 2222 and firewall must allow it",
     ["sshd 监听端口改成 2222 后需要放通防火墙", "open the firewall for ssh on its new port 2222", "sshd 2222 端口 防火墙"]),
    ("kernel module fails to load after upgrade because of missing symbol",
     ["内核升级后模块加载失败，缺少符号", "after kernel update the module will not insert, unresolved symbol", "升级 内核 模块 missing symbol"]),
    ("java service heap too small causes frequent gc pauses and high cpu",
     ["java 服务堆内存太小导致频繁 GC 和 CPU 升高", "jvm heap undersized so garbage collection pauses spike cpu", "java heap 太小 gc cpu 高"]),
    ("systemd unit restart loop because ExecStart path does not exist",
     ["systemd 单元因为 ExecStart 路径不存在而反复重启", "service unit keeps restarting since its start binary path is missing", "systemd ExecStart 路径 不存在 重启循环"]),
    ("tcp connection resets under load caused by small somaxconn backlog",
     ["高负载下 TCP 连接被重置，原因是 somaxconn 队列太小", "under load tcp resets happen because the listen backlog is tiny", "somaxconn 太小 tcp reset 高负载"]),
]


class LegacyLexicalEmbedding(LexicalEmbedding):
    """fable5 lexical features reproduced verbatim (Latin-only) for a fair before/after."""

    name = "lexical-embedding-legacy"

    @staticmethod
    def _features(text: str) -> dict[str, float]:
        tokens = re.findall(r"[a-z0-9_]+", (text or "").lower())
        features: dict[str, float] = {}
        for token in tokens:
            features["w:" + token] = features.get("w:" + token, 0.0) + 1.0
        for left, right in zip(tokens, tokens[1:]):
            key = f"b:{left}_{right}"
            features[key] = features.get(key, 0.0) + 1.0
        for token in tokens:
            padded = f"#{token}#"
            for i in range(len(padded) - 2):
                key = "c:" + padded[i : i + 3]
                features[key] = features.get(key, 0.0) + 0.3
        return features or {"w:<empty>": 1.0}


def _metrics(sim: np.ndarray, gold: list[int]) -> dict[str, float]:
    p1 = r3 = mrr = 0.0
    for row, g in zip(sim, gold):
        order = np.argsort(-row, kind="stable")
        rank = int(np.where(order == g)[0][0]) + 1
        p1 += rank == 1
        r3 += rank <= 3
        mrr += 1.0 / rank
    n = max(1, len(gold))
    return {"precision_at_1": p1 / n, "recall_at_3": r3 / n, "mrr": mrr / n, "queries": len(gold)}


def run_bilingual_memory_benchmark() -> dict[str, Any]:
    docs = [d for d, _ in CORPUS]
    families = {"zh": 0, "en": 1, "mix": 2}
    providers = {
        "mock": MockEmbedding(dim=256),
        "lexical_legacy": LegacyLexicalEmbedding(dim=512),
        "lexical": LexicalEmbedding(dim=512),
        "docsem": __import__("agent_runtime.providers.docsem_embedding", fromlist=["x"]).DocSemEmbedding(),
    }
    results: dict[str, Any] = {}
    for name, provider in providers.items():
        dmat = l2_normalize(np.asarray(provider.embed(docs), dtype=np.float32))
        per: dict[str, Any] = {}
        for fam, idx in families.items():
            queries = [qs[idx] for _, qs in CORPUS]
            qmat = l2_normalize(np.asarray(provider.embed(queries), dtype=np.float32))
            per[fam] = _metrics(qmat @ dmat.T, list(range(len(docs))))
        allq = [q for _, qs in CORPUS for q in qs]
        gold = [i for i in range(len(docs)) for _ in range(3)]
        qmat = l2_normalize(np.asarray(provider.embed(allq), dtype=np.float32))
        per["all"] = _metrics(qmat @ dmat.T, gold)
        results[name] = per
    return {
        "suite": "bilingual_memory",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "memories": len(docs),
        "query_families": list(families),
        "results": results,
        "claim_boundary": "deterministic lexical + auditable glossary, not a neural embedding; "
                          "hand-written paraphrases, 16 memories x 3 query families",
    }


def render_bilingual_memory(payload: dict[str, Any]) -> str:
    lines = [
        "# Bilingual shared-memory retrieval (Chinese/English/mixed queries -> English memories)",
        "",
        f"> {payload['memories']} memories; every query family has one paraphrase per memory; "
        "all other memories act as same-domain hard negatives.",
        "",
        "| Provider | zh P@1 | zh MRR | en P@1 | en MRR | mix P@1 | mix MRR | all P@1 | all R@3 | all MRR |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, per in payload["results"].items():
        lines.append(
            f"| {name} | {per['zh']['precision_at_1']:.1%} | {per['zh']['mrr']:.3f} | "
            f"{per['en']['precision_at_1']:.1%} | {per['en']['mrr']:.3f} | "
            f"{per['mix']['precision_at_1']:.1%} | {per['mix']['mrr']:.3f} | "
            f"{per['all']['precision_at_1']:.1%} | {per['all']['recall_at_3']:.1%} | {per['all']['mrr']:.3f} |"
        )
    lines += ["", f"- Claim boundary: {payload['claim_boundary']}.", ""]
    return "\n".join(lines)


def write_bilingual_memory(payload: dict[str, Any], root: str = "results") -> Path:
    from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest

    payload = dict(payload)
    payload["env"] = _env_info()
    out = _allocate_result_dir(root, "bilingual_memory")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(
        render_bilingual_memory(payload) + f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`\n",
        encoding="utf-8",
    )
    _write_checksum_manifest(out)
    return out


def run_bilingual_memory_cli(_args=None) -> int:
    payload = run_bilingual_memory_benchmark()
    out = write_bilingual_memory(payload)
    print(render_bilingual_memory(payload))
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = ["run_bilingual_memory_benchmark", "render_bilingual_memory", "write_bilingual_memory"]
