"""Audit the predeclared supplement without changing or replaying its raw data."""
from collections import Counter, defaultdict
import argparse
import csv
import io
import json
from pathlib import Path

from pilot_openeuler import sha
from pilot_corpus_v2 import audit


def analyze(root):
    def rows(name):
        path = root / "raw" / name
        return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []
    tasks, calls, starts, model_io = (rows(n) for n in ["task_runs.jsonl", "provider_calls.jsonl", "provider_attempt_starts.jsonl", "model_io.jsonl"])
    plan = json.loads((root / "manifests/plan.json").read_text())
    cases = {r["task_id"]: r for r in map(json.loads, (root / "corpus/tasks.jsonl").read_text().splitlines())}
    ids = [r["run_id"] for r in tasks]
    planned_ids = {u["run_id"] for u in plan}
    calls_by_id = {r["run_id"]: r for r in calls}
    io_by_id = {r["run_id"]: r for r in model_io}
    failures, violations, malformed_outputs = [], [], []
    for task in tasks:
        rid = task["run_id"]
        call, output = calls_by_id.get(rid), io_by_id.get(rid)
        expected = cases[task["task_id"]]["expected"]
        answer = task.get("outputs", {}).get("answer")
        if task["success"] != (answer == expected):
            violations.append({"run_id": rid, "issue": "recorded verdict differs from exact contract"})
        if call and output:
            if output["messages_sha256"] != sha(output["messages"]) or output["messages_sha256"] != call["prompt_sha256"]:
                violations.append({"run_id": rid, "issue": "saved prompt hash mismatch"})
            try:
                parsed = json.loads(output["response_text"].strip().removeprefix("```json").removesuffix("```").strip())
                if not isinstance(parsed, dict):
                    raise ValueError("response is not a JSON object")
            except (ValueError, TypeError) as exc:
                malformed_outputs.append({"run_id": rid, "issue": str(exc)})
                parsed = {}
            if answer and any(parsed.get(k) != answer.get(k) for k in ["diagnosis", "action"]):
                violations.append({"run_id": rid, "issue": "runtime answer differs from provider decision"})
            usage = call.get("usage_raw") or {}
            metrics = task.get("metrics") or {}
            if usage.get("prompt_tokens") != metrics.get("llm_prompt_tokens") or usage.get("completion_tokens") != metrics.get("llm_completion_tokens"):
                violations.append({"run_id": rid, "issue": "provider/runtime token mismatch"})
        if not task["success"]:
            failures.append({"run_id": rid, "task_id": task["task_id"], "arm": task["arm"], "repeat": task["repeat"],
                             "expected": expected, "actual": answer, "error": task.get("error"),
                             "provider_status": call.get("status") if call else None,
                             "provider_response": output.get("response_text") if output else None})
    by_arm = []
    for arm in ["T0", "S0", "SS0", "T1", "S1", "SS1"]:
        group = [r for r in tasks if r["arm"] == arm]
        by_arm.append({"arm": arm, "tasks": len(group), "successes": sum(r["success"] for r in group),
                       "failures": sum(not r["success"] for r in group)})
    prompts_by_case = defaultdict(set)
    outputs_by_prompt = defaultdict(set)
    for output in model_io:
        unit = next(u for u in plan if u["run_id"] == output["run_id"])
        prompts_by_case[(unit["repeat"], unit["task_id"])].add(output["messages_sha256"])
        outputs_by_prompt[output["messages_sha256"]].add(output["response_text"])
    result = {"planned_units": len(plan), "terminal_units": len(tasks), "successes": sum(r["success"] for r in tasks),
              "failures": len(failures), "provider_attempts": len(calls),
              "total_provider_tokens": sum((r.get("usage_raw") or {}).get("total_tokens", 0) for r in calls),
              "missing_units": sorted(planned_ids - set(ids)), "unexpected_units": sorted(set(ids)-planned_ids),
              "duplicate_units": [k for k, v in Counter(ids).items() if v != 1],
              "unmatched_attempts": sorted({r["request_id"] for r in starts}-{r["request_id"] for r in calls}),
              "missing_model_io_units": sorted(set(ids)-set(io_by_id)), "record_violations": violations,
              "malformed_provider_outputs": malformed_outputs,
              "provider_errors": [r["request_id"] for r in calls if r["status"] != "OK"],
              "missing_usage": [r["request_id"] for r in calls if r["usage_source"] != "provider_reported"],
              "fact_coverage_errors": [r["request_id"] for r in calls if not r["business_facts_equal"]],
              "first_checkpoint_nonempty_memory": [r["run_id"] for r in tasks if r["task_id"].endswith("-01") and r["memory_count_before"]],
              "state_expected_not_consumed": [r["run_id"] for r in tasks if r["arm"].startswith("SS") and not r.get("state_consumed")],
              "memory_off_adoptions": [r["run_id"] for r in tasks if r["arm"].endswith("0") and r.get("metrics", {}).get("memory_used")],
              "shm_residue": [r["run_id"] for r in tasks if r.get("shm_names_created_but_remaining")],
              "corrected_corpus_conflicts": audit(list(cases.values())), "by_arm": by_arm,
              "wrong_parsed_answers": sum(f["actual"] is not None for f in failures),
              "failures_without_parsed_answer": sum(f["actual"] is None for f in failures),
              "model_prompt_variants_per_case": [{"repeat": k[0], "task_id": k[1], "variants": len(v)} for k, v in sorted(prompts_by_case.items())],
              "same_prompt_different_responses": [{"prompt_sha256": k, "response_variants": len(v)} for k, v in outputs_by_prompt.items() if len(v) > 1],
              "previous_failed_case": [{"arm": r["arm"], "repeat": r["repeat"], "success": r["success"]} for r in tasks if r["task_id"] == "pilot-migration-04"],
              "by_repeat": [{"repeat": i, "tasks": sum(r["repeat"] == i for r in tasks),
                             "failures": sum(r["repeat"] == i and not r["success"] for r in tasks)} for i in (1, 2, 3)],
              "formal_experiment": False, "human_review": "PENDING", "currency_cost": "UNKNOWN"}
    for name, value in [("data_audit.json", result), ("task_failures.json", failures)]:
        (root / "analysis" / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, list(by_arm[0]))
    writer.writeheader()
    writer.writerows(by_arm)
    (root / "analysis/by_arm.csv").write_text(buffer.getvalue(), encoding="utf-8-sig")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="/evidence/supplements/corpus_v2")
    args = parser.parse_args()
    analyze(Path(args.root))
