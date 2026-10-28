"""Frozen external framework units with the same private configuration and ledgers.

No native prompt adapter is applied. AutoGen/LangGraph retain the documented fixed
external template and actual framework scheduling. Use `dry` for unpaid checks.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, "/opt/lingxi/formal-checkout/src")
import psutil
from openai import OpenAI
from agent_runtime.agents.model_decision import LABEL_ACTIONS
from agent_runtime.providers.openai_provider import _is_transient_error, _retry_after_seconds
from agent_runtime.evaluation.runner import _source_tree_sha256
import formal_real_driver as native
from formal_external_adapters import external_messages, run_external
from formal_fixture_checks import automatic_label
from formal_recording import Recorder, ResourceSampler, now, sha

OUT = native.OUT
DRY_OUT = OUT / "calibration/external_recorder_dry_v1"
EXTERNAL_PHASES = ("external_autogen", "external_langgraph")


@contextmanager
def execution_lock():
    """Shared lock name reserved for both native and external real runners."""
    path = OUT / "manifests/real_provider_execution.lock"
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another real-provider runner holds the shared execution lock") from exc
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps({"pid": os.getpid(), "runner": "formal_external_runner", "started_utc": now()}))
        stream.flush()
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def guard_resume():
    terminal = {r["run_id"] for r in native.read_rows("task_runs.jsonl")}
    starts = native.read_rows("provider_attempt_starts.jsonl")
    ends = {r["request_id"] for r in native.read_rows("provider_calls.jsonl")}
    if {r["request_id"] for r in starts} - ends:
        raise RuntimeError("unresolved provider attempt; no automatic replay")
    if {r["run_id"] for r in starts} - terminal:
        raise RuntimeError("provider attempt has no terminal task; explicit recovery required")
    if {r["run_id"] for r in native.read_rows("task_unit_starts.jsonl")} - terminal:
        raise RuntimeError("started task has no terminal record; explicit recovery required")
    return terminal


def guard_budget(budget, *, reserve_attempts=1):
    starts = native.read_rows("provider_attempt_starts.jsonl")
    total = sum((r.get("usage_raw") or {}).get("total_tokens", 0)
                for r in native.read_rows("provider_calls.jsonl"))
    if len(starts) + reserve_attempts > budget["max_provider_attempts"]:
        raise native.BudgetStop("frozen attempt ceiling: stop before entering external unit/request")
    if total >= budget["observed_reported_total_tokens_stop"]:
        raise native.BudgetStop("frozen reported-token ceiling: stop before external unit/request")


def _fd_map():
    output = {}
    for path in Path("/proc/self/fd").iterdir():
        try:
            output[path.name] = os.readlink(path)
        except OSError:
            pass
    return output


def execute(unit, row, *, dry=False, _fixture_evidence=False):
    cfg = native.configuration(unit["model"], False, None, row["state_spec"]["size_class"])
    out = DRY_OUT if dry else OUT
    (out/"raw").mkdir(parents=True, exist_ok=True)
    common = {**unit, "model_id": cfg.llm.model, "source_sha256": _source_tree_sha256(native.ROOT),
              "task_sha256": row["content_sha256"], "seed": cfg.random_seed,
              "framework": unit["phase"].removeprefix("external_"),
              "actual_topology": "framework_inprocess",
              "model_evidence": "offline_sdk_transport_fixture_not_provider" if _fixture_evidence else "deterministic_fixture" if dry else "real_provider"}
    recorder = Recorder(out, common, native.SECRET)
    process = psutil.Process()
    cpu_before = sum(process.cpu_times()[:2])
    fd_before = process.num_fds()
    fd_targets_before = _fd_map()
    shm_before = {p.name for p in Path("/dev/shm").iterdir()}
    started_ns = time.perf_counter_ns()
    sampler = ResourceSampler(recorder)
    calls = []
    client = None
    active_role_spans = {}
    result = None
    fatal_budget_error = None
    row_result = {**common, "start_utc": now(), "status": "FAILED", "success": False, "quality": 0,
                  "model_evidence": "offline_sdk_transport_fixture_not_provider" if _fixture_evidence else "deterministic_fixture" if dry else "real_provider",
                  "state_applicable": row["state_spec"]["applicable"],
                  "state_size_class": row["state_spec"]["size_class"], "state_dimension": None,
                  "state_consumed": False, "memory_count_before": 0, "memory_count_after": 0,
                  "consumer_path": "four_logical_roles_in_one_process_no_state_exchange",
                  "observable_ipc_bytes": 0,
                  "ipc_accounting": {"observed_socket_bytes": 0, "by_boundary": {},
                      "successful_syscalls": 0, "framing_consumption_conserved": True,
                      "scope": "external_framework_in_process_role_handoffs_no_AF_UNIX_control_or_AgentHost_RPC",
                      "excluded": ["HTTP/provider network", "local framework materialization",
                                   "logical payload", "kernel bookkeeping"]},
                  "local_state_materialized_bytes": 0, "logical_state_bytes": 0,
                  "communication_tokens_proxy": None, "comparison_scope": "SYSTEM_LEVEL_REFERENCE"}
    budget = json.loads((OUT/"manifests/budget_limits.json").read_text())

    def event_sink(event):
        kind, role = event["kind"], event.get("role")
        if kind == "role.start":
            category = {"planner": "planning", "retriever": "retrieval", "executor": "execution", "reviewer": "verify"}[role]
            span = recorder.span(category, f"{common['framework']}.{role}")
            span.__enter__()
            active_role_spans[role] = span
        recorder.add("external_execution_events.jsonl", event)
        if kind in {"role.end", "role.error"}:
            span = active_role_spans.pop(role, None)
            if span:
                span.__exit__(None, None, None)

    def actual_http_request(request):
        nonlocal fatal_budget_error
        # Inspect the SDK's encoded HTTP body immediately before its transport.
        # Authorization and all other headers are deliberately never retained.
        call = calls[-1]
        content = request.content
        call.update(actual_request_utf8_bytes=len(content), actual_http_body_sha256=sha(content),
                    request_encoding="actual_SDK_HTTP_request_content",
                    actual_http_dispatches=call.get("actual_http_dispatches", 0)+1)
        if len(content) > budget["max_actual_utf8_request_bytes"]:
            fatal_budget_error = native.BudgetStop("actual SDK HTTP request exceeds frozen byte ceiling before transport")
            raise fatal_budget_error
        if call["actual_http_dispatches"] != 1:
            raise RuntimeError("unexpected extra HTTP dispatch within one no-retry SDK attempt")
        body = json.loads(content)
        if body.get("messages") != call["_actual_messages"]:
            raise RuntimeError("SDK HTTP messages differ from the fact-audited external template")
        recorder.add("model_io.jsonl", {"request_id": call["request_id"], "direction": "http_request_body",
                                      "request": body, "actual_utf8_bytes": len(content),
                                      "headers": "NOT_RETAINED"}, durable=True)
        call["http_transport_eligible"] = True

    def complete(messages, metadata):
        nonlocal fatal_budget_error
        if dry:
            recorder.add("dry_prompts.jsonl", {"messages": messages, "fact_audit": metadata["fact_audit"]})
            label = automatic_label(row)
            return json.dumps({"diagnosis": label, "action": LABEL_ACTIONS[label]})
        expected_messages, fact_audit = external_messages(row,
            {"operation": "llm_diagnose", "operands": ["observations", "procedure"], "defect": row["domain"]},
            {"observations": row["observations"], "procedure": row["procedure"]})
        if messages != expected_messages or not metadata["fact_audit"]["source_facts_equal"]:
            raise RuntimeError("external actual messages differ from fixed fact-audited template")
        for attempt in range(1, 4):
            try:
                guard_budget(budget)
            except native.BudgetStop as exc:
                fatal_budget_error = exc
                raise
            request_id = unit["run_id"]+f"-{attempt}"
            kwargs = {"model": cfg.llm.model, "messages": messages, "temperature": cfg.llm.temperature,
                      "max_tokens": cfg.llm.max_tokens, "extra_body": cfg.llm.extra_body,
                      "extra_headers": {"X-Client-Request-Id": request_id, "Idempotency-Key": unit["run_id"]}}
            if cfg.llm.reasoning_effort:
                kwargs["reasoning_effort"] = cfg.llm.reasoning_effort
            call = {"request_id": request_id, "attempt": attempt, "start_utc": now(),
                    "requested_model": cfg.llm.model, "endpoint": "https://www.dmxapi.cn/v1/chat/completions",
                    "actual_prompt_sha256": sha(messages), "prompt_sha256": sha(messages),
                    "actual_request_utf8_bytes": None, "actual_http_dispatches": 0,
                    "sdk_kwargs_utf8_bytes_proxy": len(json.dumps({k:v for k,v in kwargs.items() if k!="extra_headers"}, ensure_ascii=False).encode()),
                    "prompt_chars": sum(len(m["content"]) for m in messages),
                    "fact_audit": [fact_audit], "business_facts_equal": True,
                    "temperature": cfg.llm.temperature, "max_tokens": cfg.llm.max_tokens,
                    "extra_body": cfg.llm.extra_body, "timeout_sec": 60,
                    "reasoning_effort": cfg.llm.reasoning_effort,
                    "sdk_max_retries": 0, "usage_source": "none", "usage_raw": None,
                    "idempotency": "client_headers_not_verified_server_deduplication"}
            calls.append(call)
            recorder.add("provider_attempt_starts.jsonl", call, durable=True)
            recorder.add("model_io.jsonl", {"request_id": request_id, "direction": "request",
                                          "messages": messages}, durable=True)
            call["_actual_messages"] = messages
            tick = time.perf_counter()
            caught = None
            response = None
            try:
                with recorder.span("provider_wait", "chat_completions_create", request_id=request_id):
                    response = client.chat.completions.create(**kwargs)
                usage = response.usage
                call.update(status="OK", returned_model=response.model,
                            usage_raw=usage.model_dump() if usage else None,
                            usage_source="offline_sdk_transport_fixture" if _fixture_evidence else "provider_reported" if usage else "missing",
                            finish_reason=response.choices[0].finish_reason if response.choices else None,
                            provider_request_id=getattr(response, "_request_id", None),
                            response_sha256=sha(response.model_dump()))
                recorder.add("model_io.jsonl", {"request_id": request_id, "direction": "response",
                                              "response": response.model_dump()}, durable=True)
            except Exception as exc:
                caught = fatal_budget_error or exc
                call.update(status="ERROR", error_type=type(caught).__name__, error=str(caught)[:800])
            finally:
                call.pop("_actual_messages", None)
                call.update(end_utc=now(), wall_sec=time.perf_counter()-tick)
                recorder.add("provider_calls.jsonl", call, durable=True)
            if caught is None:
                return response.choices[0].message.content or "" if response.choices else ""
            if fatal_budget_error or attempt == 3 or not _is_transient_error(caught):
                raise caught
            wait = min(20., .5*(2**(attempt-1))) * (.5+random.random())
            retry_after = _retry_after_seconds(caught)
            if retry_after is not None:
                wait = max(wait, min(retry_after, 30.))
            recorder.add("provider_retry_waits.jsonl", {"request_id": request_id, "wait_sec": wait,
                                                       "error_type": type(caught).__name__})
            with recorder.span("retry_backoff", "bounded_transient_retry_wait"):
                time.sleep(wait)

    try:
        with sampler:
            if not dry:
                with recorder.span("setup", "external_provider_client"):
                    client = OpenAI(api_key=cfg.llm.api_key, base_url=cfg.llm.base_url,
                                    timeout=60, max_retries=0)
                    client._client.event_hooks["request"].append(actual_http_request)
            result = asyncio.run(run_external(common["framework"], row, complete,
                                               unit_id=unit["run_id"], event_sink=event_sink))
            recorder.add("external_framework_runs.jsonl", result)
            usage = [c["usage_raw"] for c in calls if c.get("usage_raw") is not None]
            complete_usage = bool(calls) and len(usage) == len(calls) and all(c.get("status") == "OK" for c in calls) and not _fixture_evidence
            row_result.update(success=result["success"], quality=result["quality"],
                              status="COMPLETED" if result["success"] else "FAILED",
                              outputs={"answer": result["answer"]}, error=result["errors"] or None,
                              observed_fixture_check=result["fixture_check"], fact_audit=result["fact_audit"],
                              external_adapter_version=result["adapter_version"],
                              external_framework_versions=result["framework_versions"], role_order=result["role_order"],
                              provider_usage_complete=complete_usage and not dry,
                              provider_input_tokens=sum(u.get("prompt_tokens", 0) for u in usage) if complete_usage and not dry else None,
                              provider_output_tokens=sum(u.get("completion_tokens", 0) for u in usage) if complete_usage and not dry else None,
                              provider_total_tokens=sum(u.get("total_tokens", 0) for u in usage) if complete_usage and not dry else None,
                              provider_cached_input_tokens=sum((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) for u in usage) if complete_usage and not dry else None,
                              reported_token_lower_bound=sum(u.get("total_tokens", 0) for u in usage) if not _fixture_evidence else None)
    except Exception as exc:
        row_result.update(error={"type": type(exc).__name__, "message": str(exc)[:1000]},
                          provider_usage_complete=False, provider_input_tokens=None,
                          provider_output_tokens=None, provider_total_tokens=None)
    finally:
        for role, span in reversed(list(active_role_spans.items())):
            span.__exit__(None, None, None)
        if client is not None:
            with recorder.span("cleanup", "close_external_provider_client"):
                client.close()
        ended_ns = time.perf_counter_ns()
        partition = recorder.partition(started_ns, ended_ns)
        row_result.update(end_utc=now(), wall_sec=(ended_ns-started_ns)/1e9,
                          wall_start_monotonic_ns=started_ns, wall_end_monotonic_ns=ended_ns,
                          provider_attempts=len(calls),
                          provider_wait_sec=sum(s["inclusive_sec"] for s in recorder.spans if s["category"] == "provider_wait"),
                          exclusive_spans_sec=partition,
                          span_partition_error_sec=abs(sum(partition.values())-(ended_ns-started_ns)/1e9),
                          runtime_cpu_sec=sum(process.cpu_times()[:2])-cpu_before,
                          fd_count_before=fd_before, fd_count_after=process.num_fds(),
                          fd_targets_before=fd_targets_before, fd_targets_after=_fd_map(),
                          sampled_peak_process_group_rss=sampler.peak_rss,
                          sampled_peak_process_group_fds=sampler.peak_fds,
                          worker_pids={}, workers_remaining=[],
                          logical_role_pids={role: os.getpid() for role in ("planner", "retriever", "executor", "reviewer")},
                          shm_names_created_but_remaining=sorted({p.name for p in Path("/dev/shm").iterdir()}-shm_before))
        previous_flush = recorder.flush_sec
        recorder.flush()
        row_result["terminal_record_flush_sec_excluded_from_wall"] = recorder.flush_sec-previous_flush
        row_result["all_record_flush_sec_including_durable_in_wall"] = recorder.flush_sec
        recorder.write("dry_task_runs.jsonl" if dry else "task_runs.jsonl", [row_result], fsync=True)
    print(json.dumps({key: row_result.get(key) for key in
        ["phase", "task_id", "repeat", "success", "provider_attempts", "wall_sec", "error"]}, ensure_ascii=False), flush=True)
    if fatal_budget_error:
        raise fatal_budget_error
    return row_result


def run(phases, *, dry=False, limit=None):
    if not phases or not set(phases) <= set(EXTERNAL_PHASES):
        raise ValueError(f"external phases must be selected from {EXTERNAL_PHASES}")
    plan = json.loads((OUT/"manifests/formal_plan.json").read_text())
    by_id = {row["task_id"]: row for row in native.cases()}
    if dry:
        path = DRY_OUT/"raw/dry_task_runs.jsonl"
        done = {r["run_id"] for r in [json.loads(s) for s in path.read_text().splitlines()]} if path.exists() else set()
    else:
        native.validate_freeze()
        done = guard_resume()
    budget = json.loads((OUT/"manifests/budget_limits.json").read_text())
    count = 0
    for unit in plan:
        if unit["phase"] not in phases or unit["run_id"] in done:
            continue
        if not dry:
            guard_budget(budget, reserve_attempts=3)
            native.configuration(unit["model"])
            Recorder(OUT, unit, native.SECRET).add("task_unit_starts.jsonl", {"start_utc": now()}, durable=True)
        execute(unit, by_id[unit["task_id"]], dry=dry)
        count += 1
        if limit is not None and count >= limit:
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["dry", "run"])
    parser.add_argument("--phases", nargs="+", default=list(EXTERNAL_PHASES))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.command == "run":
        with execution_lock():
            run(args.phases, dry=False, limit=args.limit)
    else:
        run(args.phases, dry=True, limit=args.limit)
