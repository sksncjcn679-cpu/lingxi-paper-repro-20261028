"""Deterministic lexical embedding for offline, non-mock retrieval experiments."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

import numpy as np

from agent_runtime.providers.base import l2_normalize


_LATIN_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

# Small, auditable bilingual glossary of OS/operations concepts (not a learned model).
_CONCEPTS: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    name: (frozenset(en.split()), tuple(zh.split()))
    for name, en, zh in (
        ("port", "port ports listen bind socket", "端口 监听 绑定"),
        ("permission", "permission permissions denied chmod chown access", "权限 拒绝访问 无权"),
        ("service", "service services systemd daemon unit", "服务 守护进程 单元"),
        ("dependency", "dependency dependencies requires redis", "依赖"),
        ("start_failure", "start startup failed failure crash", "启动 失败 崩溃"),
        ("migration", "migration migrate porting port_to", "迁移 移植"),
        ("architecture", "arch architecture x86_64 aarch64 riscv i686 abi", "架构 指令集"),
        ("package", "package packages rpm pip wheel manifest", "软件包 依赖包 包"),
        ("rebuild", "rebuild recompile build compile", "重新编译 重建 编译 构建"),
        ("compatible", "compatible compatibility portable", "兼容"),
        ("kernel", "kernel sysctl module", "内核"),
        ("memory", "memory ram oom swap rss", "内存 交换"),
        ("cpu", "cpu scheduler scheduling sched", "处理器 调度"),
        ("network", "network net tcp udp latency", "网络 延迟"),
        ("disk", "disk io storage filesystem", "磁盘 存储 文件系统"),
        ("config", "config configuration setting settings", "配置 设置"),
        ("log", "log logs journal journald", "日志"),
        ("diagnose", "diagnose diagnosis troubleshoot root cause", "诊断 排查 根因"),
    )
}


_EN_TO_CONCEPTS: dict[str, tuple[str, ...]] = {}
for _c, (_en, _zh) in _CONCEPTS.items():
    for _t in _en:
        _EN_TO_CONCEPTS[_t] = _EN_TO_CONCEPTS.get(_t, ()) + (_c,)


class LexicalEmbedding:
    """Hashed word/character features with deterministic signed buckets."""

    name = "lexical-embedding"

    def __init__(self, dim: int = 512, idf: dict[str, float] | None = None) -> None:
        if dim <= 0:
            raise ValueError("lexical embedding dim must be positive")
        self.dim = int(dim)
        self._idf = dict(idf or {})

    @staticmethod
    def _features(text: str) -> dict[str, float]:
        lowered = (text or "").lower()
        tokens = _LATIN_RE.findall(lowered)
        features: dict[str, float] = {}

        def add(key: str, weight: float) -> None:
            features[key] = features.get(key, 0.0) + weight

        for token in tokens:
            add("w:" + token, 1.0)
        for left, right in zip(tokens, tokens[1:]):
            add(f"b:{left}_{right}", 1.0)
        for token in tokens:
            padded = f"#{token}#"
            for i in range(len(padded) - 2):
                add("c:" + padded[i : i + 3], 0.3)
        # fable7: CJK support.  The historical provider matched only [a-z0-9_] and therefore
        # mapped every Chinese sentence to the same "<empty>" vector.  Chinese has no word
        # delimiters, so character unigrams + bigrams are the standard segmentation-free
        # features (as in character n-gram IR baselines for Chinese).
        for run in _CJK_RE.findall(lowered):
            for ch in run:
                add("h:" + ch, 0.5)
            for i in range(len(run) - 1):
                add("hb:" + run[i : i + 2], 1.0)
        # Bilingual OS-domain concept normalisation: a Chinese term and its English counterpart
        # emit the same concept feature, so a Chinese query can retrieve an English memory.
        # concept hits via a reverse index (same counts, same concept order as before)
        en_hits: dict[str, int] = {}
        for t in tokens:
            for concept in _EN_TO_CONCEPTS.get(t, ()):
                en_hits[concept] = en_hits.get(concept, 0) + 1
        has_cjk = bool(_CJK_RE.search(lowered))
        for concept, (_en_terms, zh_terms) in _CONCEPTS.items():
            hits = en_hits.get(concept, 0)
            if has_cjk:
                hits += sum(lowered.count(z) for z in zh_terms)
            if hits:
                add("k:" + concept, 1.5 * hits)
        return features or {"w:<empty>": 1.0}

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature, tf in self._features(text).items():
                # fable14: memoise the (deterministic) feature hash -- n-grams repeat heavily
                # across texts; identical output, ~3x fewer blake2b calls in steady state.
                value = _HASH_CACHE.get(feature)
                if value is None:
                    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                    value = int.from_bytes(digest, "little", signed=False)
                    if len(_HASH_CACHE) < 262144:
                        _HASH_CACHE[feature] = value
                index = value % self.dim
                sign = 1.0 if (value >> 63) & 1 else -1.0
                out[row, index] += sign * (1.0 + math.log(tf)) * self._idf.get(feature, 1.0)
        return l2_normalize(out)


_HASH_CACHE: dict[str, int] = {}

__all__ = ["LexicalEmbedding"]
