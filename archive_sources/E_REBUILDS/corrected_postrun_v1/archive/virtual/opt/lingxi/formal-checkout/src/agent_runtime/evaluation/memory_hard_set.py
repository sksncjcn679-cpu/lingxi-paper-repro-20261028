"""Hard retrieval set for mock, lexical and optional OpenAI embeddings."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import numpy as np

from agent_runtime.config import load_config
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.providers.factory import build_embedding
from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.providers.lexical_embedding import LexicalEmbedding


_CASES = [
    ("m1", "service startup failed because port 8080 is occupied", "daemon cannot bind its listening address: address already in use"),
    ("m2", "python extension cannot load because the CPython ABI is incompatible", "native module targets a different interpreter binary interface"),
    ("m3", "systemd unit lacks permission to read its state directory", "service account is denied access to the runtime data path"),
    ("m4", "openEuler package needs an i686 native library rebuilt", "32-bit dependency is unavailable on the x86_64 target"),
    ("m5", "network listener backlog is too small for burst traffic", "connection queue overflows under concurrent arrivals"),
]


def _score(provider, query: str, memories: list[tuple[str, str]], correct: str) -> dict[str, Any]:
    values = [text for _, text in memories]
    matrix = provider.embed(values + [query])
    sims = matrix[:-1] @ matrix[-1]
    order = sorted(range(len(values)), key=lambda i: float(sims[i]), reverse=True)
    ranked = [memories[index][0] for index in order]
    rank = ranked.index(correct) + 1 if correct in ranked else len(ranked) + 1
    return {
        "p_at_1": float(ranked[0] == correct),
        "recall_at_3": float(correct in ranked[:3]),
        "mrr": 1.0 / rank,
        "ranked": ranked,
    }


def run_memory_hard_set() -> dict[str, Any]:
    memories = [(mid, text) for mid, text, _ in _CASES]
    queries = [(mid, query) for mid, _, query in _CASES]
    providers: dict[str, Any] = {
        "mock": MockEmbedding(dim=256, seed=42),
        "lexical": LexicalEmbedding(dim=512),
        "docsem": __import__("agent_runtime.providers.docsem_embedding", fromlist=["x"]).DocSemEmbedding(),
    }
    openai_error = None
    cfg = load_config(load_env=True)
    if cfg.embedding.provider == "openai" or (cfg.llm.provider == "openai" and cfg.llm.api_key):
        try:
            providers["openai"] = build_embedding(cfg.with_overrides(embedding=cfg.embedding.__class__(
                provider="openai", model=cfg.embedding.model, dim=cfg.embedding.dim
            )))
        except Exception as exc:  # noqa: BLE001 - preserve the real provider failure
            openai_error = f"{type(exc).__name__}: {exc}"
    else:
        openai_error = "not requested: no explicit OpenAI embedding configuration"

    summary: dict[str, Any] = {}
    for name, provider in providers.items():
        rows = [_score(provider, query, memories, correct) for correct, query in queries]
        summary[name] = {
            "provider": getattr(provider, "name", name),
            "p_at_1": float(np.mean([row["p_at_1"] for row in rows])),
            "recall_at_3": float(np.mean([row["recall_at_3"] for row in rows])),
            "mrr": float(np.mean([row["mrr"] for row in rows])),
            "rows": rows,
        }
    summary["openai"] = summary.get("openai", {"status": "not_run", "error": openai_error})
    return {
        "suite": "memory_hard_set",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "env": _env_info(),
        "methodology": {"memory_count": len(memories), "query_count": len(queries), "queries_are_lexically_rewritten": True},
        "summary": summary,
    }


def render_memory_hard_set(payload: dict[str, Any]) -> str:
    lines = ["# Memory Hard-Set Benchmark", "", "| Provider | P@1 | Recall@3 | MRR | Status |", "| --- | ---: | ---: | ---: | --- |"]
    for name, row in payload["summary"].items():
        if "mrr" not in row:
            lines.append(f"| {name} | - | - | - | {row.get('status')}: {row.get('error')} |")
        else:
            lines.append(f"| {name} | {row['p_at_1']:.1%} | {row['recall_at_3']:.1%} | {row['mrr']:.3f} | measured |")
    lines.extend(["", f"Source SHA-256: `{payload['env']['source_tree_sha256']}`", ""])
    return "\n".join(lines)


def write_memory_hard_set_results(payload: dict[str, Any], root: str | Path = "results") -> Path:
    out = _allocate_result_dir(str(root), "memory_hard_set")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_memory_hard_set(payload), encoding="utf-8")
    _write_checksum_manifest(out)
    return out


def run_memory_hard_set_cli(_args) -> int:
    payload = run_memory_hard_set()
    out = write_memory_hard_set_results(payload)
    print(render_memory_hard_set(payload))
    print(f"\nResults: {out}")
    return 0


__all__ = ["run_memory_hard_set", "write_memory_hard_set_results"]
