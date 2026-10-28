"""Rebuild descriptive pilot tables and audits from saved raw records; no paper inference."""
from pathlib import Path
from collections import Counter, defaultdict
import argparse
import csv
import hashlib
import io
import json
import xml.etree.ElementTree as ET


def load(root, name):
    p = root / "raw" / name
    return [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def write_csv(path, data):
    if not data:
        return
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, list(data[0]))
    writer.writeheader()
    writer.writerows(data)
    content = buffer.getvalue().encode("utf-8-sig")
    if "raw" in path.parts and path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f"refusing to overwrite an inconsistent raw CSV export: {path}")
        return
    path.write_bytes(content)


def analyze(root):
    plan = json.loads((root / "manifests/plan.json").read_text())
    tasks = load(root, "task_runs.jsonl")
    calls = load(root, "provider_calls.jsonl")
    messages = load(root, "messages.jsonl")
    states = load(root, "states.jsonl")
    memory = load(root, "memory_events.jsonl")
    starts = load(root, "provider_attempt_starts.jsonl")
    planned = Counter(r["phase"] for r in plan)
    completed = Counter(r["phase"] for r in tasks)
    terminal_ids = [r["run_id"] for r in tasks]
    duplicate = len(terminal_ids) != len(set(terminal_ids))
    missing = sorted({r["run_id"] for r in plan} - set(terminal_ids))
    calls_by_task, messages_by_task, states_by_task = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in calls:
        calls_by_task[r["run_id"]].append(r)
    for r in messages:
        messages_by_task[r["run_id"]].append(r)
    for r in states:
        states_by_task[r["run_id"]].append(r)
    table = []
    for phase, model, arm in sorted({(r["phase"], r["model"], r["arm"]) for r in tasks}):
        group = [r for r in tasks if (r["phase"], r["model"], r["arm"]) == (phase, model, arm)]
        ids = {r["run_id"] for r in group}
        api = [r for r in calls if r["run_id"] in ids]
        usage = [r["usage_raw"] for r in api if r.get("usage_raw")]
        table.append({"phase": phase, "model": model, "arm": arm, "tasks": len(group),
                      "successes": sum(r["success"] for r in group), "failures": sum(not r["success"] for r in group),
                      "provider_attempts": len(api), "prompt_tokens": sum(u.get("prompt_tokens", 0) for u in usage),
                      "completion_tokens": sum(u.get("completion_tokens", 0) for u in usage),
                      "cached_input_tokens": sum((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) for u in usage),
                      "observable_channel_socket_bytes": sum(r.get("observable_channel_socket_bytes", 0) for r in group),
                      "local_state_materialization_bytes": sum(r.get("local_state_materialization_bytes", 0) for r in group),
                      "logical_state_bytes": sum(r.get("logical_state_bytes", 0) for r in group),
                      "memory_adoptions": sum(r.get("metrics", {}).get("memory_used", 0) for r in group),
                      "state_consumed_tasks": sum(r.get("state_consumed", False) for r in group),
                      "wall_sum_sec_diagnostic_only": sum(r["wall_sec"] for r in group)})
    write_csv(root / "tables/pilot_by_arm.csv", table)
    task_table, byte_audit, token_mismatches = [], [], []
    for r in tasks:
        metric = r.get("metrics", {})
        usage = [c["usage_raw"] for c in calls_by_task[r["run_id"]] if c.get("usage_raw")]
        raw_input = sum(u.get("prompt_tokens", 0) for u in usage)
        raw_output = sum(u.get("completion_tokens", 0) for u in usage)
        if raw_input != metric.get("llm_prompt_tokens", 0) or raw_output != metric.get("llm_completion_tokens", 0):
            token_mismatches.append(r["run_id"])
        msg = messages_by_task[r["run_id"]]
        observed_message_bytes = sum(m["uds_framed_bytes"] + m["ack_bytes"] for m in msg)
        final_bytes = r.get("observable_channel_socket_bytes", 0)
        residual = final_bytes - observed_message_bytes
        byte_audit.append({"run_id": r["run_id"], "phase": r["phase"], "message_control_bytes": observed_message_bytes,
                           "final_metric_control_bytes": final_bytes, "residual_setup_barrier_close_bytes": residual,
                           "negative_residual": residual < 0, "whole_system_physical_bytes_certified": False})
        task_table.append({**{k: r[k] for k in ["run_id", "phase", "task_id", "chain_id", "domain", "difficulty", "arm", "model", "repeat", "success", "status", "start_utc", "end_utc", "wall_sec", "source_sha256", "task_sha256", "provider_attempts"]},
                           "prompt_tokens": raw_input, "completion_tokens": raw_output,
                           "message_count": metric.get("message_count"), "tool_calls": metric.get("tool_calls"),
                           "observable_channel_socket_bytes": final_bytes,
                           "local_state_materialization_bytes": r.get("local_state_materialization_bytes"),
                           "logical_state_bytes": r.get("logical_state_bytes"), "state_consumed": r.get("state_consumed"),
                           "memory_queries": metric.get("memory_queries"), "memory_used": metric.get("memory_used"),
                           "memory_effective": metric.get("memory_effective"), "processes": r["processes"],
                           "error": json.dumps(r.get("error"), ensure_ascii=False)})
    write_csv(root / "raw/task_runs.csv", task_table)
    write_csv(root / "tables/channel_byte_audit.csv", byte_audit)
    failures = [{"run_id": r["run_id"], "phase": r["phase"], "task_id": r["task_id"], "arm": r["arm"],
                 "model": r["model"], "outputs": r.get("outputs"), "error": r.get("error")} for r in tasks if not r["success"]]
    (root / "analysis/task_failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    test = ET.parse(root / "logs/preflight/pytest.xml").getroot().find("testsuite")
    test_summary = {k: int(test.attrib[k]) for k in ["tests", "failures", "errors", "skipped"]}
    test_summary["passed"] = test_summary["tests"] - test_summary["failures"] - test_summary["errors"] - test_summary["skipped"]
    audit = {"planned_units": dict(planned), "terminal_units": dict(completed), "missing_run_ids": missing,
             "duplicate_terminal_rows": duplicate, "provider_attempts": len(calls), "unmatched_attempt_starts": sorted({c["request_id"] for c in starts}-{c["request_id"] for c in calls}),
             "usage_missing": [c["request_id"] for c in calls if c["usage_source"] != "provider_reported"],
             "business_fact_coverage_failures": [c["request_id"] for c in calls if not c["business_facts_equal"]],
             "raw_usage_vs_runtime_token_mismatches": token_mismatches,
             "negative_channel_byte_residuals": [r["run_id"] for r in byte_audit if r["negative_residual"]],
             "state_expected_but_not_consumed": [r["run_id"] for r in tasks if r["arm"].startswith("SS") and not r.get("state_consumed")],
             "shm_residue": [r["run_id"] for r in tasks if r["shm_names_created_but_remaining"]],
             "memory_off_adoptions": [r["run_id"] for r in tasks if r["arm"].endswith("0") and r.get("metrics", {}).get("memory_used")],
             "first_task_memory_not_empty": [r["run_id"] for r in tasks if r["task_id"].endswith("-01") and r["memory_count_before"]],
             "tests": test_summary, "task_successes": sum(r["success"] for r in tasks), "task_failures": len(failures),
             "raw_counts": {"messages": len(messages), "state_events": len(states), "memory_events": len(memory), "resource_samples": len(load(root, "resource_samples.jsonl"))},
             "total_provider_tokens": sum((c.get("usage_raw") or {}).get("total_tokens", 0) for c in calls),
             "scope": "pilot diagnostics; no significance, generalization, or speedup claim"}
    (root / "analysis/data_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "tables/pilot_by_arm.json").write_text(json.dumps(table, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="/evidence")
    args = parser.parse_args()
    analyze(Path(args.root))
