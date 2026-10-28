"""fable9 suite: decision-certified precision ladder for non-text state.

For N plans (operation + 3..8 operands drawn from an OS-operations vocabulary) the Planner
state is transported under four policies and the Retriever's real use of the vector — operand
ranking by similarity — is recomputed on the RECEIVED bytes:

* ``float32``            : lossless baseline (reference decision);
* ``int8``               : fable5-fable8 default (cosine-gated adaptive int8);
* ``int4_naive``         : always int4, no certificate (shows why a certificate is needed);
* ``certified``          : fable9 ladder int4 -> int8 -> fp16 -> fp32, keep the cheapest
                           precision whose receiver decision equals float32.

Reported: bytes per embedding, receiver decision agreement with float32, precision mix.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.state_exchange.exchange import StateExchange, _int4_pack, _int4_unpack

VOCAB = [
    "service_status", "port_probe", "dependency_status", "permission_status", "journal_excerpt",
    "strategy", "target_arch", "python_abi", "native_dependency", "service_manager",
    "package_manifest", "kernel_param", "sysctl_value", "memory_pressure", "cpu_load", "disk_io",
    "network_latency", "selinux_mode", "firewall_rules", "cgroup_limits", "oom_events",
    "rpm_database", "glibc_version", "systemd_unit", "boot_log",
]
OPS = ["diagnose_service", "assess_migration", "tune_kernel_param", "audit_security", "plan_capacity"]


def _decision(emb: MockEmbedding, operands: list[str], vec: np.ndarray) -> tuple[int, ...]:
    vecs = np.asarray(emb.embed(operands), dtype=np.float32)
    return tuple(int(i) for i in np.argsort(-(vecs @ np.asarray(vec, dtype=np.float32)), kind="stable"))


def run_decision_certified_benchmark(n: int = 300, seed: int = 7, provider: str = "docsem") -> dict[str, Any]:
    rng = random.Random(seed)
    if provider == "docsem":
        from agent_runtime.providers.docsem_embedding import DocSemEmbedding

        emb = DocSemEmbedding(dim=256)
    else:
        emb = MockEmbedding(dim=256)
    stats = {k: {"bytes": 0, "agree": 0} for k in ("float32", "int8", "int4_naive", "certified")}
    mix: dict[str, int] = {}
    for i in range(n):
        plan = {"operation": rng.choice(OPS), "operands": rng.sample(VOCAB, rng.randint(3, 8)), "defect": f"d{i % 17}"}
        text = json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        full = np.asarray(emb.embed([text])[0], dtype=np.float32)
        ref_decision = _decision(emb, plan["operands"], full)
        # float32
        stats["float32"]["bytes"] += full.nbytes
        stats["float32"]["agree"] += 1
        # int8 (legacy)
        scale8 = float(np.max(np.abs(full))) / 127.0 or 1.0
        q8 = np.clip(np.rint(full / scale8), -127, 127).astype(np.int8)
        stats["int8"]["bytes"] += q8.nbytes + 4
        stats["int8"]["agree"] += int(_decision(emb, plan["operands"], q8.astype(np.float32) * scale8) == ref_decision)
        # naive int4
        packed, s4 = _int4_pack(full)
        stats["int4_naive"]["bytes"] += packed.nbytes + 4
        stats["int4_naive"]["agree"] += int(_decision(emb, plan["operands"], _int4_unpack(packed, s4, full.size)) == ref_decision)
        # certified ladder through the real StateExchange; the receiver reads the stored bytes
        ex = StateExchange(emb, task_id=f"b{i}", precision="adaptive")
        try:
            probe = lambda v, ops=plan["operands"]: _decision(emb, ops, v)  # noqa: E731
            ref = ex.put_plan_state(plan, decision_probe=probe)
            received = ex.get_plan_state(ref.uri)["embedding"]
            encoded = ex.get_encoded_vector(ref.uri)
            stats["certified"]["bytes"] += int(encoded.nbytes) + (4 if ref.precision in ("int8", "int4") else 0)
            stats["certified"]["agree"] += int(_decision(emb, plan["operands"], received) == ref_decision)
            mix[ref.precision] = mix.get(ref.precision, 0) + 1
        finally:
            ex.close()
    summary = {
        k: {"bytes_per_embedding": v["bytes"] / n, "decision_agreement": v["agree"] / n} for k, v in stats.items()
    }
    return {
        "suite": "decision_certified_state",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "plans": n,
        "seed": seed,
        "provider": provider,
        "embedding_dim": 256,
        "summary": summary,
        "certified_precision_mix": mix,
        "claim_boundary": "mock deterministic embedding; the certificate is exact for the declared receiver "
                          "decision (operand ranking), not for arbitrary downstream uses",
    }


def render_decision_certified(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    lines = [
        "# Decision-certified non-text state (receiver decision recomputed on received bytes)",
        "",
        f"> {payload['plans']} plans, embedding={payload.get('provider', 'mock')}, dim={payload['embedding_dim']}, seed={payload['seed']}; decision = Retriever operand ranking.",
        "",
        "| Policy | Bytes / embedding | vs float32 | Receiver decision == float32 |",
        "| --- | ---: | ---: | ---: |",
    ]
    base = s["float32"]["bytes_per_embedding"]
    for k in ("float32", "int8", "int4_naive", "certified"):
        v = s[k]
        lines.append(f"| {k} | {v['bytes_per_embedding']:.1f} | {v['bytes_per_embedding'] / base:.1%} | {v['decision_agreement']:.1%} |")
    lines += ["", f"- Certified precision mix: {payload['certified_precision_mix']}.",
              f"- Claim boundary: {payload['claim_boundary']}.", ""]
    return "\n".join(lines)


def run_decision_certified_cli(args) -> int:
    from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest

    payload = run_decision_certified_benchmark(n=getattr(args, "plans", 300), provider="docsem")
    payload["mock"] = run_decision_certified_benchmark(n=getattr(args, "plans", 300), provider="mock")
    payload["env"] = _env_info()
    out = _allocate_result_dir("results", "decision_certified_state")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    text = render_decision_certified(payload) + "\n## Same study with the mock embedding\n\n" + render_decision_certified(payload["mock"]) + f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    _write_checksum_manifest(out)
    print(text)
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = ["run_decision_certified_benchmark", "render_decision_certified"]
