"""任务签名：用于判定记忆复用是「同一子问题」还是「相似子问题」。

- 签名一致 → 同一子问题 → 可直接复用结论（answer）；
- 操作相同但签名不同 → 相似子问题 → 复用策略/模板（solution）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def task_signature(operation: str, evidence: dict[str, Any]) -> str:
    """基于操作 + 已解析证据值生成稳定签名。"""
    items = sorted((k, str(v)) for k, v in evidence.items() if v is not None)
    raw = f"{operation}|" + json.dumps(items, ensure_ascii=False)
    # 128-bit prefix is compact enough for metadata while avoiding the weak 64-bit
    # collision margin of the historical signature.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def request_fingerprint(topic: str, payload: dict[str, Any]) -> str:
    """Stable exact-input fingerprint for the Memory fast path.

    Unlike semantic retrieval, this key is intentionally strict.  It covers the operation,
    operand order, intent/defect class and the current values of all referenced facts.  A prior
    answer is eligible for direct reuse only when this fingerprint matches exactly; related but
    non-identical tasks continue through normal procedural-memory retrieval.
    """
    operands = [str(x) for x in payload.get("operands", [])]
    facts = payload.get("facts")
    facts = facts if isinstance(facts, dict) else {}
    selected_facts = {key: facts.get(key) for key in operands if key in facts}
    canonical = {
        "topic": str(topic),
        "operation": payload.get("question"),
        "operands": operands,
        "defect": payload.get("defect", ""),
        "facts": selected_facts,
    }
    raw = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["task_signature", "request_fingerprint"]
