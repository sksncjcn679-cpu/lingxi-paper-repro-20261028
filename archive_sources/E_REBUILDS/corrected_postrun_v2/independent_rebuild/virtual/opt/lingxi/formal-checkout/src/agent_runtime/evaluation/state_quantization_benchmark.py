"""Transport precision fidelity benchmark for runtime embeddings."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any

import numpy as np

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.state_exchange.exchange import StateExchange


def _corpus(n: int) -> list[str]:
    ops = ["sum", "product", "repair", "refactor", "retrieve", "review", "count"]
    defects = ["zero division", "resource leak", "timeout", "schema mismatch", "none"]
    return [
        f"operation {ops[i % len(ops)]} operands item-{i % 13} item-{(i * 7) % 17} "
        f"defect {defects[i % len(defects)]} iteration {i}"
        for i in range(n)
    ]


def run_state_quantization_benchmark(*, samples: int = 200, dim: int = 256) -> dict[str, Any]:
    if samples <= 0 or dim <= 0:
        raise ValueError("samples and dim must be positive")
    provider = MockEmbedding(dim=dim, seed=42)
    texts = _corpus(samples)
    originals = provider.embed(texts).astype(np.float32)
    results: dict[str, Any] = {}

    for precision in ("float32", "float16", "int8"):
        ex = StateExchange(provider, task_id=f"quant-{precision}", precision=precision, deduplicate=True)
        cosines: list[float] = []
        physical: list[int] = []
        logical: list[int] = []
        try:
            for text, original in zip(texts, originals):
                ref = ex.embed_text(text)
                decoded = ex.get_vector(ref.uri).astype(np.float32, copy=False)
                denom = float(np.linalg.norm(original) * np.linalg.norm(decoded))
                cosine = float(np.dot(original, decoded) / denom) if denom else 1.0
                cosines.append(cosine)
                physical.append(int(ref.nbytes))
                logical.append(int(ref.logical_nbytes))
        finally:
            ex.close()
        total_physical = sum(physical)
        total_logical = sum(logical)
        ordered = sorted(cosines)
        p05 = ordered[max(0, int(0.05 * (len(ordered) - 1)))]
        results[precision] = {
            "samples": samples,
            "total_logical_bytes": total_logical,
            "total_physical_bytes": total_physical,
            "materialization_saving": 1.0 - total_physical / total_logical,
            "cosine": {
                "min": min(cosines),
                "p05": p05,
                "median": float(statistics.median(cosines)),
                "mean": float(statistics.fmean(cosines)),
            },
        }

    return {
        "suite": "state_quantization",
        "env": _env_info(),
        "methodology": {
            "provider": f"MockEmbedding(dim={dim}, seed=42)",
            "samples": samples,
            "scope": "runtime transport fidelity; not a real-model retrieval-quality benchmark",
            "int8": "per-vector symmetric scalar quantization with an explicit float32 scale payload",
        },
        "results": results,
    }


def render_state_quantization_report(payload: dict[str, Any]) -> str:
    lines = [
        "# State Transport Quantization Fidelity",
        "",
        "> Deterministic mock-embedding transport fidelity; not a claim about real-model retrieval accuracy.",
        "",
        f"- Source SHA-256: {payload['env']['source_tree_sha256']}",
        f"- Samples: {payload['methodology']['samples']}",
        "",
        "| Precision | physical bytes | saving vs logical float32 | min cosine | median cosine |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for precision in ("float32", "float16", "int8"):
        r = payload["results"][precision]
        lines.append(
            f"| {precision} | {r['total_physical_bytes']} | {r['materialization_saving']:.1%} | "
            f"{r['cosine']['min']:.6f} | {r['cosine']['median']:.6f} |"
        )
    lines += [
        "",
        "int8 includes the 4-byte per-vector scale in physical bytes. Unlike float16, int8 "
        "requires a decode allocation before floating-point computation, so its claim is smaller "
        "transport/storage payload rather than zero-copy compute.",
        "",
    ]
    return "\n".join(lines)


def write_state_quantization_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "state_quantization")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_state_quantization_report(payload), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "suite": payload["suite"],
                "environment": payload["env"],
                "methodology": payload["methodology"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_checksum_manifest(out)
    return out


def run_state_quantization_benchmark_cli(args) -> int:
    payload = run_state_quantization_benchmark(samples=args.samples, dim=args.dim)
    out = write_state_quantization_results(payload)
    print(out)
    return 0


__all__ = [
    "run_state_quantization_benchmark",
    "write_state_quantization_results",
    "run_state_quantization_benchmark_cli",
]
