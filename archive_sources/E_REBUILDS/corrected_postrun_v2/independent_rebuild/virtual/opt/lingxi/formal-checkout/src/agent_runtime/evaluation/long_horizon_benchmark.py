"""fable11 suite: long-horizon, multi-round collaboration (the contest's "多轮、多 Agent、重复上下文多").

An on-call incident is worked in K consecutive rounds by the same four Agents.  Every round is a
real service-diagnosis task (different fault each round, verified answer), and -- as in real
incident handling -- the evidence carries the *accumulated* incident journal: all lines of the
previous rounds plus this round's new lines.  The diagnosis program receives that journal as an
operand, so the context is genuinely transported and consumed.

Arms (same tasks, same message graph, same verifiers):

* ``T``          Lean Text: every round re-sends the accumulated journal inline.
* ``T_delta``    Lower bound for ANY text protocol: each round sends only its new lines
                 (assumes perfectly stateful receivers; not a runnable baseline of this repo's
                 agents, but the same tasks with the journal truncated to the new lines).
* ``S``          Structured protocol only (context inline, like text).
* ``SS``         Structured + state/artifact planes, whole-blob content addressing.
* ``SS_cdc``     SS + fable11 content-defined chunk dedup across the rounds of one context.

Reported per K: communication tokens, strict full-plane physical bytes, success.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime.runtime.task import Task, VerdictResult

FAULTS = ("port_conflict", "permission_denied", "dependency_missing")


def _verifier(expected: str):
    def verify(outputs: dict) -> VerdictResult:
        ok = outputs.get("answer") == expected
        return VerdictResult(success=ok, quality=1.0 if ok else 0.0, detail={"expected": expected})

    return verify


def _round_lines(r: int, fault: str) -> list[str]:
    ts = f"Sep 21 {9 + r // 60:02d}:{r % 60:02d}"
    detail = {
        "port_conflict": "OSError: [Errno 98] Address already in use on 0.0.0.0:8080",
        "permission_denied": "PermissionError: [Errno 13] Permission denied: '/var/lib/api/state'",
        "dependency_missing": "ConnectionRefusedError: redis://127.0.0.1:6379 unavailable (redis.service inactive)",
    }[fault]
    import hashlib as _h

    # Access-log lines with per-request trace ids: realistic, high-entropy journal content
    # (templated lines alone would let zlib hide most of the growth inside one blob).
    access = [
        f"{ts}:{10 + j:02d} api api[{3000 + r}]: GET /v1/orders/{_h.sha256(f'{r}-{j}'.encode()).hexdigest()[:12]} "
        f"trace={_h.sha256(f't{r}-{j}'.encode()).hexdigest()[:32]} status=503 dur_ms={(r * 37 + j * 11) % 900}"
        for j in range(12)
    ]
    return access + [
        f"{ts}:01 api systemd[1]: round {r}: Starting api.service",
        f"{ts}:01 api api[{3000 + r}]: loading /etc/api/config.yaml (revision {r})",
        f"{ts}:02 api api[{3000 + r}]: {detail}",
        f"{ts}:02 api systemd[1]: api.service: Main process exited, status=1/FAILURE",
        f"{ts}:03 oncall[{r}]: triage note r{r}: fault candidate recorded, escalation level {1 + r % 3}",
    ]


def make_incident(k: int, *, delta_only: bool = False, incident: str = "inc-0001") -> list[Task]:
    tasks: list[Task] = []
    journal: list[str] = []
    for r in range(k):
        fault = FAULTS[r % len(FAULTS)]
        new = _round_lines(r, fault)
        journal += new
        facts = {
            "service_status": "Active: failed (Result: exit-code)"
            + ("; bind: Address already in use" if fault == "port_conflict" else "")
            + ("; Permission denied" if fault == "permission_denied" else ""),
            "port_probe": "LISTEN 0 128 0.0.0.0:8080 users:((legacyd,pid=941,fd=7))" if fault == "port_conflict" else "port 8080 free (no socket bound)",
            "dependency_status": "redis.service inactive (dead)" if fault == "dependency_missing" else "redis.service active",
            "permission_status": "denied: /var/lib/api/state owned by root:root 0700" if fault == "permission_denied" else "ok",
            "journal_excerpt": "\n".join(new if delta_only else journal),
            "strategy": "diagnose_service_v1",
        }
        tasks.append(Task(
            topic="linux-service-diagnosis",
            description=f"Incident {incident} round {r + 1}/{k}: diagnose api.service using the incident journal.",
            payload={
                "question": "diagnose_service",
                "operands": ["service_status", "port_probe", "dependency_status", "permission_status", "journal_excerpt", "strategy"],
                "defect": "service_start_failure",
                "diagnosis_labels": list(FAULTS),
                "facts": facts,
            },
            expected={"answer": fault},
            verifier=_verifier(fault),
            context_id=incident,
            tags=["incident", "multi-round"],
        ))
    return tasks


def _arm(label: str, k: int) -> dict[str, Any]:
    from agent_runtime.artifact_store.cdc import reset_context_pools
    from agent_runtime.evaluation.runner import ExperimentConfig, run_once

    uds = "uds"
    cfgs = {
        "T": ExperimentConfig("T", "x", "text", False, False, True, text_style="lean", control_transport=uds),
        "T_delta": ExperimentConfig("T_delta", "x", "text", False, False, True, text_style="lean", control_transport=uds),
        "S": ExperimentConfig("S", "x", "structured", False, False, True, structured_wire_codec="session_msgpack", control_transport=uds),
        "SS": ExperimentConfig("SS", "x", "structured", True, False, True, structured_wire_codec="session_msgpack",
                               control_transport=uds, state_shared_memory=True, state_precision="adaptive"),
        "SS_cdc": ExperimentConfig("SS_cdc", "x", "structured", True, False, True, structured_wire_codec="session_msgpack",
                                   control_transport=uds, state_shared_memory=True, state_precision="adaptive"),
    }
    prev = os.environ.get("AGENT_CONTEXT_DEDUP")
    os.environ["AGENT_CONTEXT_DEDUP"] = "1" if label == "SS_cdc" else "0"
    try:
        reset_context_pools()
        row = run_once(cfgs[label], lambda: make_incident(k, delta_only=(label == "T_delta")), 42)
    finally:
        if prev is None:
            os.environ.pop("AGENT_CONTEXT_DEDUP", None)
        else:
            os.environ["AGENT_CONTEXT_DEDUP"] = prev
    control = (row["ipc_framed_bytes"] + row["ipc_ack_bytes"]) if row["ipc_frames"] else row["wire_bytes"]
    return {
        "success": row["task_success"],
        "tokens": row["text_tokens"],
        "full_bytes": control + row["state_bytes"] + row["artifact_bytes"],
        "artifact_bytes": row["artifact_bytes"],
        "messages": row["message_count"],
        "latency_ms_per_round": row["latency_sec"] / k * 1000,
    }


def run_long_horizon_benchmark(ks: tuple[int, ...] = (1, 2, 4, 8, 16, 32)) -> dict[str, Any]:
    arms = ("T", "T_delta", "S", "SS", "SS_cdc")
    table: dict[str, dict[str, Any]] = {}
    for k in ks:
        table[str(k)] = {a: _arm(a, k) for a in arms}
    return {"suite": "long_horizon", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "rounds": list(ks), "arms": list(arms), "results": table}


def render_long_horizon(p: dict[str, Any]) -> str:
    L = ["# Long-horizon multi-round collaboration (accumulated incident journal)", "",
         "> Same K diagnosis rounds per arm; every arm must answer every round correctly. Savings are vs Lean Text (T).", "",
         "| K | T tokens | T_delta tokens (text lower bound) | S tokens | SS tokens | SS saving vs T | SS saving vs T_delta | T full B | SS full B | SS_cdc full B | SS_cdc byte saving vs T | success T/SS/SS_cdc |",
         "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for k, r in p["results"].items():
        t, td, s, ss, sc = r["T"], r["T_delta"], r["S"], r["SS"], r["SS_cdc"]
        L.append(
            f"| {k} | {t['tokens']:.0f} | {td['tokens']:.0f} | {s['tokens']:.0f} | {ss['tokens']:.0f} | "
            f"{1 - ss['tokens'] / t['tokens']:.1%} | {1 - ss['tokens'] / td['tokens']:.1%} | {t['full_bytes']:.0f} | "
            f"{ss['full_bytes']:.0f} | {sc['full_bytes']:.0f} | {1 - sc['full_bytes'] / t['full_bytes']:.1%} | "
            f"{t['success']:.0%}/{ss['success']:.0%}/{sc['success']:.0%} |"
        )
    L += ["", "- T_delta is an optimistic lower bound for text protocols (receivers assumed to remember all earlier rounds).",
          "- SS tokens stay flat per round because the journal travels on the artifact plane by reference; "
          "SS_cdc additionally keeps the *physical* artifact plane linear in K via content-defined chunk dedup.", ""]
    return "\n".join(L)


def run_long_horizon_cli(args) -> int:
    from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest

    ks = tuple(int(x) for x in str(getattr(args, "rounds", "1,2,4,8,16,32")).split(","))
    payload = run_long_horizon_benchmark(ks)
    payload["env"] = _env_info()
    out = _allocate_result_dir("results", "long_horizon")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    text = render_long_horizon(payload) + f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    _write_checksum_manifest(out)
    print(text)
    ok = all(v[a]["success"] == 1.0 for v in payload["results"].values() for a in ("T", "S", "SS", "SS_cdc"))
    return 0 if ok else 1
