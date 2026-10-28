"""Benchmark versioned full-snapshot vs delta state propagation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
from typing import Any

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.state_exchange.delta import VersionedStateLedger, canonical_json_bytes


def _dag(nodes: int) -> dict[str, Any]:
    return {
        "operation": "workflow",
        "nodes": [
            {
                "id": i,
                "kind": "agent" if i % 3 else "tool",
                "status": "pending",
                "priority": i % 5,
                "payload": f"node-{i}-" + ("x" * 48),
            }
            for i in range(nodes)
        ],
        "edges": [[i, i + 1] for i in range(nodes - 1)],
        "meta": {"revision": 1, "owner": "planner", "policy": "deterministic"},
    }


def _edited(base: dict[str, Any], changed_nodes: int, revision: int = 2) -> dict[str, Any]:
    out = json.loads(json.dumps(base))
    for idx in range(min(changed_nodes, len(out["nodes"]))):
        out["nodes"][idx]["status"] = "done"
        out["nodes"][idx]["priority"] = (out["nodes"][idx]["priority"] + 1) % 5
    out["meta"]["revision"] = revision
    return out


def _latency_case(nodes: int, changed_nodes: int, repeat: int) -> dict[str, float]:
    update_ms: list[float] = []
    resolve_ms: list[float] = []
    for _ in range(repeat):
        base = _dag(nodes)
        target = _edited(base, changed_nodes)
        ledger = VersionedStateLedger(namespace="bench")
        first = ledger.put_snapshot(base)
        start = time.perf_counter_ns()
        ref = ledger.update(first.uri, target)
        update_ms.append((time.perf_counter_ns() - start) / 1_000_000)
        start = time.perf_counter_ns()
        assert ledger.resolve(ref.uri) == target
        resolve_ms.append((time.perf_counter_ns() - start) / 1_000_000)
    return {
        "update_median_ms": float(statistics.median(update_ms)),
        "resolve_median_ms": float(statistics.median(resolve_ms)),
        "update_p95_ms": float(sorted(update_ms)[max(0, int(0.95 * (len(update_ms) - 1)))]),
        "resolve_p95_ms": float(sorted(resolve_ms)[max(0, int(0.95 * (len(resolve_ms) - 1)))]),
    }


def run_state_delta_benchmark(*, repeat: int = 30) -> dict[str, Any]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    cases: list[dict[str, Any]] = []
    for nodes in (32, 128, 512):
        for ratio in (0.01, 0.05, 0.20, 0.60):
            changed = max(1, int(nodes * ratio))
            base = _dag(nodes)
            target = _edited(base, changed)
            full_bytes = len(canonical_json_bytes(target))
            ledger = VersionedStateLedger(namespace="bench")
            first = ledger.put_snapshot(base)
            ref = ledger.update(first.uri, target)
            assert ledger.resolve(ref.uri) == target
            cases.append(
                {
                    "nodes": nodes,
                    "changed_nodes": changed,
                    "change_ratio": changed / nodes,
                    "mutation": "sparse_fields",
                    "full_snapshot_bytes": full_bytes,
                    "materialized_bytes": ref.materialized_bytes,
                    "kind": ref.kind,
                    "delta_ops": ref.delta_ops,
                    "saving": 1.0 - ref.materialized_bytes / full_bytes,
                    "latency": _latency_case(nodes, changed, repeat),
                }
            )

    # Explicit high-churn control: a delta larger than the compact replacement snapshot must
    # fall back instead of paying patch overhead.
    base = _dag(128)
    replacement_state = {
        "operation": "replacement",
        "nodes": [{"id": i, "value": i * 3} for i in range(8)],
        "meta": {"revision": 99},
    }
    ledger = VersionedStateLedger(namespace="fallback")
    first = ledger.put_snapshot(base)
    fallback_ref = ledger.update(first.uri, replacement_state)
    fallback_full = len(canonical_json_bytes(replacement_state))
    assert fallback_ref.kind == "snapshot"
    cases.append(
        {
            "nodes": 128,
            "changed_nodes": 128,
            "change_ratio": 1.0,
            "mutation": "replace_all",
            "full_snapshot_bytes": fallback_full,
            "materialized_bytes": fallback_ref.materialized_bytes,
            "kind": fallback_ref.kind,
            "delta_ops": fallback_ref.delta_ops,
            "saving": 1.0 - fallback_ref.materialized_bytes / fallback_full,
            "latency": {
                "update_median_ms": 0.0,
                "resolve_median_ms": 0.0,
                "update_p95_ms": 0.0,
                "resolve_p95_ms": 0.0,
            },
        }
    )

    # Multi-step workflow: repeated small revisions with periodic snapshots to cap replay depth.
    ledger = VersionedStateLedger(namespace="chain", max_delta_depth=8)
    state = _dag(256)
    ref = ledger.put_snapshot(state)
    full_total = ref.logical_bytes
    materialized_total = ref.materialized_bytes
    deltas = 0
    snapshots = 1
    for revision in range(2, 22):
        state = _edited(state, changed_nodes=2, revision=revision)
        # Move the changed positions through the graph rather than rewriting only the first two.
        i = (revision * 7) % 256
        j = (revision * 13) % 256
        state["nodes"][i]["status"] = "running" if revision % 2 else "done"
        state["nodes"][j]["priority"] = revision % 5
        ref = ledger.compare_and_update(ref.uri, state)
        full_total += ref.logical_bytes
        materialized_total += ref.materialized_bytes
        if ref.kind == "delta":
            deltas += 1
        else:
            snapshots += 1
    assert ledger.resolve(ref.uri) == state

    chain = {
        "revisions": 21,
        "deltas": deltas,
        "snapshots": snapshots,
        "full_snapshot_bytes_if_always_sent": full_total,
        "versioned_materialized_bytes": materialized_total,
        "saving": 1.0 - materialized_total / full_total,
        "final_chain_depth": ref.depth,
    }
    return {
        "suite": "state_delta",
        "env": _env_info(),
        "methodology": {
            "state": "JSON-like plan DAG/control-plane state",
            "patch": "deterministic add/remove/replace delta with base/target SHA-256 verification",
            "fallback": "full snapshot when delta >= 90% of target snapshot or chain depth > 8",
            "scope": "controlled state-transport benchmark; not a CRDT or network-distributed consistency claim",
            "repeat": repeat,
        },
        "cases": cases,
        "chain": chain,
    }


def render_state_delta_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Versioned Delta State Benchmark",
        "",
        "> Immutable snapshot/delta transport benchmark. Concurrent stale writes are detected, not merged; this is not a CRDT claim.",
        "",
        f"- Source SHA-256: {payload['env']['source_tree_sha256']}",
        f"- Repeat: {payload['methodology']['repeat']}",
        "",
        "| Nodes | Changed | Full B | Sent B | Mode | Saving | Update median ms | Resolve median ms |",
        "| ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    for case in payload["cases"]:
        lines.append(
            f"| {case['nodes']} | {case['changed_nodes']} ({case['change_ratio']:.1%}) | "
            f"{case['full_snapshot_bytes']} | {case['materialized_bytes']} | {case['kind']} | "
            f"{case['saving']:.1%} | {case['latency']['update_median_ms']:.4f} | "
            f"{case['latency']['resolve_median_ms']:.4f} |"
        )
    chain = payload["chain"]
    lines += [
        "",
        "## 21-revision chain",
        "",
        f"- Delta revisions: {chain['deltas']}; full snapshots: {chain['snapshots']}",
        f"- Always-full bytes: {chain['full_snapshot_bytes_if_always_sent']}",
        f"- Versioned materialized bytes: {chain['versioned_materialized_bytes']}",
        f"- Saving: {chain['saving']:.1%}",
        f"- Final chain depth: {chain['final_chain_depth']}",
        "",
    ]
    return "\n".join(lines)


def write_state_delta_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "state_delta")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_state_delta_report(payload), encoding="utf-8")
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


def run_state_delta_benchmark_cli(args: Any) -> int:
    payload = run_state_delta_benchmark(repeat=args.repeat)
    out = write_state_delta_results(payload)
    print(render_state_delta_report(payload))
    print(f"\nResults saved to: {out}")
    return 0


__all__ = [
    "run_state_delta_benchmark",
    "render_state_delta_report",
    "write_state_delta_results",
    "run_state_delta_benchmark_cli",
]
