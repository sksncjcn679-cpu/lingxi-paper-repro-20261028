"""Bounded, append-only real-provider pilot. This is not the formal paper experiment."""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

import numpy as np
import psutil
import yaml
from agent_runtime.config import load_config
from agent_runtime.evaluation.runner import _source_tree_sha256
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from pilot_corpus import corpus, make_task

OUT = Path("/evidence")
ROOT = Path("/opt/lingxi/checkout")
RAW = OUT / "raw"
ARMS = {"T0": ("text", False, False), "S0": ("structured", False, False), "SS0": ("structured", True, False),
        "T1": ("text", False, True), "S1": ("structured", False, True), "SS1": ("structured", True, True)}
MODELS = {"M1": "doubao-seed-2-0-mini-260215", "M2": "qwen3.7-flash"}
ATTEMPT_LIMIT = 240
TOKEN_STOP = 500000
SECRET = ""


class BudgetExceeded(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(value).hexdigest()


def append(name, row):
    text = json.dumps(row, ensure_ascii=False, default=str)
    if SECRET:
        text = text.replace(SECRET, "[REDACTED]")
    with (RAW / name).open("a", encoding="utf-8") as f:
        f.write(text + "\n")
        f.flush()


def rows(name):
    p = RAW / name
    return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []


def configuration(model, processes=False):
    global SECRET
    for raw in Path("/run/secrets/model.env").read_text().splitlines():
        if raw.strip() and not raw.startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    base = load_config(load_env=False)
    SECRET = base.llm.api_key
    assert base.llm.provider == "openai" and SECRET
    body = {"thinking": {"type": "disabled"}} if model == "M1" else {"enable_thinking": False}
    return base.with_overrides(
        llm=replace(base.llm, model=MODELS[model], temperature=0, max_tokens=1500, timeout_sec=60,
                    max_retries=2, completion_governor=False, output_contract="none", extra_body=body),
        control_transport="uds", pipeline_topology="request_reply", state_shared_memory=True,
        agent_processes=processes, text_style="lean", tokenizer_backend="tiktoken",
        llm_coalesce="off", llm_advisory=False, llm_judge=False, llm_context_mode="inbound",
        task_retry_budget=0, event_journal_enabled=True,
        data_dir="/opt/lingxi/pilot-data")


def prepare():
    if (OUT / "protocol.yaml").exists():
        raise RuntimeError("pilot protocol already exists; use its frozen plan")
    cases = corpus()
    (OUT / "corpus/tasks.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in cases))
    plan = []
    for i in [0, 6, 12, 18, 4]:
        plan.append({"phase": "calibration", "model": "M1", "arm": "SS0", "task_id": cases[i]["task_id"], "processes": False})
    for phase, model, selected in [("main", "M1", cases), ("model2", "M2", [cases[i] for i in [0, 6, 12, 18]])]:
        for i, case in enumerate(selected):
            arms = list(ARMS)
            arms = arms[i % 6:] + arms[:i % 6]
            for arm in arms:
                plan.append({"phase": phase, "model": model, "arm": arm, "task_id": case["task_id"], "processes": False})
    for case in cases[:2]:
        for arm in ["S0", "SS0", "SS1"]:
            plan.append({"phase": "multiprocess", "model": "M1", "arm": arm, "task_id": case["task_id"], "processes": True})
    for unit in plan:
        unit["run_id"] = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(unit, sort_keys=True)).hex
    (OUT / "manifests/plan.json").write_text(json.dumps(plan, indent=2))
    protocol = {"purpose": "environment_preflight_and_small_real_model_pilot_only", "protocol_date": "2026-10-19",
                "formal_experiment_started": False, "pilot_cases": 24, "pilot_chains": 4, "human_review": "PENDING",
                "planned_task_units": dict(Counter(u["phase"] for u in plan)), "max_provider_attempts": ATTEMPT_LIMIT,
                "observed_token_stop_before_next_request": TOKEN_STOP, "currency_prices": "UNKNOWN; no currency cost claim",
                "groups": ARMS, "seed": "model seed not supplied or claimed; runtime random_seed=42",
                "model_parameters": {"temperature": 0, "max_tokens": 1500, "timeout_sec": 60, "max_attempts_per_task": 3},
                "memory_isolation": "separate persistent SQLite store per phase/model/arm/chain; only earlier tasks visible",
                "failures": "retain all planned units and failures; no failed task auto-rerun; API transient retries bounded",
                "checkpoint": "skip terminal task rows; unmatched API start blocks automatic resume to prevent duplicate billing",
                "exclusion_rules": "none; unsupported cells retained as UNSUPPORTED; formal data and old archives excluded",
                "ipc_boundary": "runtime Channel UDS payload+framing+ACK+negotiation; excludes AgentHost RPC and kernel bookkeeping",
                "state_bytes": "local materialization and logical bytes separate from observable Channel socket bytes",
                "timing": "container on WSL, host load not isolated, instrumentation not calibrated; descriptive pilot only"}
    (OUT / "protocol.yaml").write_text(yaml.safe_dump(protocol, allow_unicode=True, sort_keys=False))
    (OUT / "manifests/models.json").write_text(json.dumps({"endpoint": "https://www.dmxapi.cn/v1/chat/completions", "models": MODELS,
                "M1_extra_body": {"thinking": {"type": "disabled"}}, "M2_extra_body": {"enable_thinking": False},
                "prices": "UNKNOWN", "server_side_idempotency_guarantee": "UNKNOWN"}, indent=2))
    print(f"Prepared {len(plan)} task units, attempt ceiling {ATTEMPT_LIMIT}", flush=True)


class ResourceSampler:
    def __init__(self, run_id):
        self.run_id = run_id
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)
    def sample(self):
        while not self.stop.is_set():
            root = psutil.Process()
            samples = []
            for p in [root] + root.children(recursive=True):
                try:
                    samples.append({"pid": p.pid, "rss": p.memory_info().rss, "cpu_sec": sum(p.cpu_times()[:2]), "fds": p.num_fds()})
                except psutil.Error:
                    pass
            append("resource_samples.jsonl", {"run_id": self.run_id, "timestamp": now(), "interval_sec": .2,
                                              "processes": samples, "rss_semantics": "sum includes shared pages more than once"})
            self.stop.wait(.2)
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


def execute(unit, case, memory):
    cfg = configuration(unit["model"], unit["processes"])
    mode, state_on, memory_on = ARMS[unit["arm"]]
    common = {k: unit[k] for k in ["run_id", "phase", "model", "arm", "task_id", "processes"]}
    common.update(chain_id=case["chain_id"], domain=case["domain"], difficulty=case["difficulty"], repeat=0)
    started = time.perf_counter()
    result_row = {**common, "source_sha256": _source_tree_sha256(ROOT), "task_sha256": case["content_sha256"],
                  "start_utc": now(), "status": "FAILED", "success": False, "quality": 0,
                  "memory_count_before": memory.count() if memory else 0}
    ctx = None
    originals = {}
    call_count = 0
    shm_before = {p.name for p in Path("/dev/shm").iterdir()}
    try:
        ctx = build_context(cfg, mode, task_id=case["task_id"], use_memory=memory_on, use_state_exchange=state_on,
                            use_sandbox=False, memory=memory)
        create = ctx.llm._client.chat.completions.create

        def audited_create(**kwargs):
            nonlocal call_count
            attempts = rows("provider_attempt_starts.jsonl")
            billed = sum((r.get("usage_raw") or {}).get("total_tokens", 0) for r in rows("provider_calls.jsonl"))
            if len(attempts) >= ATTEMPT_LIMIT or billed >= TOKEN_STOP or call_count >= 3:
                raise BudgetExceeded("pilot provider budget exhausted")
            call_count += 1
            request_id = unit["run_id"] + f"-{call_count}"
            kwargs["extra_headers"] = {**kwargs.get("extra_headers", {}), "X-Client-Request-Id": request_id, "Idempotency-Key": unit["run_id"]}
            messages = kwargs["messages"]
            prompt = "\n".join(m.get("content", "") for m in messages)
            facts_ok = False
            try:
                observations = json.JSONDecoder().raw_decode(prompt.rsplit("\nObservations: ", 1)[1])[0]
                facts_ok = observations == case["observations"]
            except (ValueError, IndexError):
                pass
            record = {**common, "request_id": request_id, "attempt": call_count, "start_utc": now(),
                      "endpoint": "https://www.dmxapi.cn/v1/chat/completions", "requested_model": kwargs["model"],
                      "prompt_sha256": sha(messages), "prompt_chars": len(prompt), "business_facts_equal": facts_ok,
                      "temperature": kwargs.get("temperature"), "max_tokens": kwargs.get("max_tokens"),
                      "extra_body": kwargs.get("extra_body"), "timeout_sec": cfg.llm.timeout_sec,
                      "usage_source": "none", "usage_raw": None}
            append("provider_attempt_starts.jsonl", {**common, "request_id": request_id, "start_utc": record["start_utc"]})
            tick = time.perf_counter()
            try:
                if not facts_ok:
                    raise ValueError("business facts coverage check failed before provider call")
                response = create(**kwargs)
                usage = response.usage
                record.update(usage_raw=usage.model_dump() if usage else None,
                              usage_source="provider_reported" if usage else "missing", returned_model=response.model,
                              finish_reason=response.choices[0].finish_reason, response_sha256=sha(response.model_dump()),
                              provider_request_id=getattr(response, "_request_id", None), status="OK")
                return response
            except Exception as exc:
                record.update(status="ERROR", error_type=type(exc).__name__, error=str(exc)[:500])
                raise
            finally:
                record.update(end_utc=now(), wall_sec=time.perf_counter()-tick)
                append("provider_calls.jsonl", record)
        ctx.llm._client.chat.completions.create = audited_create

        send = ctx.channel.send
        def audited_send(message):
            before = asdict(ctx.metrics.snapshot)
            tick = time.perf_counter()
            output = send(message)
            after = asdict(ctx.metrics.snapshot)
            append("messages.jsonl", {**common, "timestamp": now(), "message_id": message.message_id,
                "source": message.source_agent, "target": message.target_agent, "action": message.action_type.value,
                "protocol_version": message.protocol_version, "sequence_no": message.sequence_no,
                "codec": "lean_text" if mode == "text" else cfg.structured_wire_codec,
                "physical_payload_bytes": after["wire_bytes"]-before["wire_bytes"],
                "communication_tokens": after["text_tokens"]-before["text_tokens"], "tokenizer": ctx.channel.tokenizer.name,
                "uds_framed_bytes": after["ipc_framed_bytes"]-before["ipc_framed_bytes"],
                "ack_bytes": after["ipc_ack_bytes"]-before["ipc_ack_bytes"],
                "verified": after["ipc_verified"]-before["ipc_verified"], "span_sec": time.perf_counter()-tick,
                "state_ref": message.state_reference, "artifact_refs": message.artifact_references,
                "logical_message_sha256": sha(message.model_dump(mode="json"))})
            return output
        ctx.channel.send = audited_send

        for name in ["put_plan_state", "get_plan_state", "get_versioned_state", "get_vector"]:
            original = getattr(ctx.state_exchange, name)
            def state_call(*args, _name=name, _fn=original, **kwargs):
                tick = time.perf_counter()
                output = _fn(*args, **kwargs)
                record = {**common, "event": _name, "timestamp": now(), "pid": os.getpid(),
                          "span_sec": time.perf_counter()-tick, "transport": ctx.state_exchange.transport,
                          "consumer_access": "runtime service read via AgentHost RPC" if unit["processes"] else "agent in runtime process"}
                if _name == "put_plan_state":
                    record.update(reference=asdict(output))
                else:
                    record.update(uri=args[0] if args else None)
                    if isinstance(output, dict) and "embedding" in output:
                        record.update(embedding_shape=list(output["embedding"].shape), embedding_dtype=str(output["embedding"].dtype),
                                      decoded_embedding_sha256=sha(output["embedding"].tobytes()))
                append("states.jsonl", record)
                return output
            setattr(ctx.state_exchange, name, state_call)

        if memory:
            for name in ["write", "retrieve", "mark_used", "record_feedback"]:
                original = getattr(memory, name)
                originals[name] = original
                def memory_call(*args, _name=name, _fn=original, **kwargs):
                    tick = time.perf_counter()
                    output = _fn(*args, **kwargs)
                    event = {**common, "event": _name, "timestamp": now(), "span_sec": time.perf_counter()-tick}
                    if _name == "retrieve":
                        event.update(query_sha256=sha([args, kwargs]), candidates=[{"memory_id": h.record.memory_id, "score": h.score,
                            "source_task_id": h.record.payload.get("task_id"), "summary_sha256": sha(h.record.summary)} for h in output],
                            relevance_labels="NOT_ANNOTATED; not retrieval accuracy evidence")
                    elif _name == "write":
                        event.update(memory_id=output[0].memory_id, created=output[1], record_sha256=sha(output[0].model_dump(mode="json")))
                    else:
                        event.update(memory_id=args[0] if args else kwargs.get("memory_id"), feedback=args[1:] if len(args)>1 else kwargs)
                    append("memory_events.jsonl", event)
                    return output
                setattr(memory, name, memory_call)

        with ResourceSampler(unit["run_id"]):
            result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(make_task(case), ctx))
        metrics = asdict(result.metrics)
        result_row.update(success=result.success, quality=result.quality, outputs=result.outputs, error=result.error,
                          status="COMPLETED" if result.success else "FAILED", metrics=metrics,
                          state_consumed=bool(ctx.blackboard.get("state_consumed")),
                          memory_lineage=ctx.blackboard.get("memory_lineage"),
                          execution_provenance=ctx.blackboard.get("execution_provenance"),
                          observable_channel_socket_bytes=metrics["ipc_framed_bytes"]+metrics["ipc_ack_bytes"],
                          local_state_materialization_bytes=metrics["state_bytes"], logical_state_bytes=metrics["state_logical_bytes"],
                          state_fd_socket_bytes=metrics["state_fd_socket_bytes"],
                          agent_host_rpc_bytes="NOT_INSTRUMENTED" if unit["processes"] else "NOT_APPLICABLE")
    except Exception as exc:
        result_row.update(error={"type": type(exc).__name__, "message": str(exc)[:500]})
    finally:
        for name, original in originals.items():
            setattr(memory, name, original)
        if ctx:
            ctx.llm._client.close()
        result_row.update(end_utc=now(), wall_sec=time.perf_counter()-started, provider_attempts=call_count,
                          shm_names_created_but_remaining=sorted({p.name for p in Path("/dev/shm").iterdir()}-shm_before))
        append("task_runs.jsonl", result_row)
    print(json.dumps({k: result_row.get(k) for k in ["phase", "model", "arm", "task_id", "success", "provider_attempts", "wall_sec", "error"]}), flush=True)
    return result_row


def run(phases):
    plan = json.loads((OUT / "manifests/plan.json").read_text())
    cases = {r["task_id"]: r for r in corpus()}
    done = {r["run_id"] for r in rows("task_runs.jsonl")}
    starts = {r["request_id"] for r in rows("provider_attempt_starts.jsonl")}
    ends = {r["request_id"] for r in rows("provider_calls.jsonl")}
    if starts-ends:
        raise RuntimeError("unresolved provider attempts exist; no automatic replay permitted")
    stores = {}
    try:
        for unit in plan:
            if unit["phase"] not in phases or unit["run_id"] in done:
                continue
            case = cases[unit["task_id"]]
            key = (unit["phase"], unit["model"], unit["arm"], case["chain_id"])
            memory = None
            if ARMS[unit["arm"]][2]:
                if key not in stores:
                    path = Path("/opt/lingxi/pilot-memory") / ("__".join(key)+".sqlite")
                    stores[key] = MemoryStore(build_embedding(configuration(unit["model"])), db_path=str(path))
                memory = stores[key]
            result = execute(unit, case, memory)
            if (result.get("error") or {}).get("type") == "BudgetExceeded":
                raise BudgetExceeded("pilot budget stop; remaining planned units are incomplete")
    finally:
        for store in stores.values():
            store.close()


def audit():
    tasks = [r for r in rows("task_runs.jsonl") if r["phase"] == "calibration"]
    calls = [r for r in rows("provider_calls.jsonl") if r["phase"] == "calibration"]
    states = [r for r in rows("states.jsonl") if r["phase"] == "calibration"]
    checks = {"five_calibration_units": len(tasks) == 5, "calibration_tasks_succeeded": all(r["success"] for r in tasks),
              "provider_usage_returned": len(calls) >= 5 and all(r["usage_source"] == "provider_reported" for r in calls),
              "business_facts_preserved": all(r["business_facts_equal"] for r in calls),
              "state_consumed": all(r.get("state_consumed") for r in tasks),
              "state_reads_logged": any(r["event"] == "get_plan_state" for r in states),
              "no_shm_residue": all(not r["shm_names_created_but_remaining"] for r in tasks)}
    report = {"status": "PILOT_READY_WITH_LIMITATIONS" if all(checks.values()) else "INCOMPLETE", "checks": checks,
              "not_certified": ["full byte conservation across all transports", "mutually exclusive latency spans", "human-reviewed corpus",
                                "instrumentation overhead neutrality", "independent child-process SHM mapping", "retrieval accuracy labels"]}
    (OUT / "analysis/metric_audit.json").write_text(json.dumps(report, indent=2))
    if all(checks.values()):
        files = [Path(__file__), Path(__file__).with_name("pilot_corpus.py")]
        freeze = {"source_sha256": _source_tree_sha256(ROOT), "corpus_sha256": sha((OUT / "corpus/tasks.jsonl").read_bytes()),
                  "plan_sha256": sha((OUT / "manifests/plan.json").read_bytes()), "protocol_sha256": sha((OUT / "protocol.yaml").read_bytes()),
                  "drivers": {p.name: sha(p.read_bytes()) for p in files}, "frozen_at_utc": now(), "scope": "pilot only"}
        (OUT / "manifests/pilot-freeze.json").write_text(json.dumps(freeze, indent=2))
    print(json.dumps(report), flush=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "calibrate", "audit", "run"])
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare()
    elif args.phase == "calibrate":
        run(["calibration"])
    elif args.phase == "audit":
        raise SystemExit(audit())
    else:
        if not (OUT / "manifests/pilot-freeze.json").exists():
            raise SystemExit("calibration audit and freeze required")
        frozen = json.loads((OUT / "manifests/pilot-freeze.json").read_text())
        assert frozen["source_sha256"] == _source_tree_sha256(ROOT), "runtime source changed after freeze"
        assert frozen["corpus_sha256"] == sha((OUT / "corpus/tasks.jsonl").read_bytes()), "corpus changed after freeze"
        assert frozen["plan_sha256"] == sha((OUT / "manifests/plan.json").read_bytes()), "plan changed after freeze"
        assert frozen["protocol_sha256"] == sha((OUT / "protocol.yaml").read_bytes()), "protocol changed after freeze"
        for name, digest in frozen["drivers"].items():
            assert digest == sha(Path(__file__).with_name(name).read_bytes()), "driver changed after freeze"
        run(["main", "model2", "multiprocess"])
