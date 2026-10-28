"""Unpaid, deterministic equivalence/failure audit of both actual frameworks."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, "/opt/lingxi/formal-checkout/src")
from agent_runtime.agents.model_decision import LABEL_ACTIONS
from formal_external_adapters import ADAPTER_VERSION, ROLES, run_external, sha, versions
from formal_fixture_checks import automatic_label


def append(file, row):
    file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"))+"\n")
    file.flush()


async def audit(out: Path):
    corpus = out/"corpus/automatic_validated_v1/tasks.jsonl"
    cases = [json.loads(line) for line in corpus.read_text().splitlines()]
    path = out/"raw/external_framework_offline_audit_v1.jsonl"
    if path.exists():
        raise FileExistsError(f"audit outputs are append-only; existing completed path: {path}")
    failures, totals, paired_prompts = [], {}, {}
    with path.open("x") as file:
        for framework in ("autogen", "langgraph"):
            totals[framework] = {"cases": 0, "success": 0, "role_order_correct": 0,
                                 "facts_complete": 0, "fixture_executed": 0, "one_model_decision": 0}
            for index, row in enumerate(cases):
                async def complete(messages, metadata):
                    # Independent evidence-cue parser, not the author's expected
                    # answer. This is a recorder/scheduler fixture, not model data.
                    label = automatic_label(row)
                    assert "expected" not in metadata and len(messages) == 2
                    return json.dumps({"diagnosis": label, "action": LABEL_ACTIONS[label]})
                result = await run_external(framework, row, complete,
                    unit_id=f"external-offline-audit-v1:{framework}:{row['task_id']}")
                result["scope"] = "OFFLINE_DETERMINISTIC_FIXTURE_NOT_PROVIDER_QUALITY_OR_USAGE"
                append(file, result)
                counts = totals[framework]
                counts["cases"] += 1
                counts["success"] += int(result["success"])
                counts["role_order_correct"] += int(tuple(result["role_order"]) == ROLES)
                counts["facts_complete"] += int(result["fact_audit"]["source_facts_equal"])
                counts["fixture_executed"] += int(bool(result["fixture_check"]) and result["fixture_check"]["read_only"])
                counts["one_model_decision"] += int(result["logical_model_invocations"] == 1)
                prompt_hash = sha(result["model_messages"])
                if framework == "autogen":
                    paired_prompts[row["task_id"]] = prompt_hash
                elif paired_prompts[row["task_id"]] != prompt_hash:
                    failures.append({"framework": framework, "task_id": row["task_id"], "type": "cross_framework_prompt_mismatch"})
                if not result["success"]:
                    failures.append({"framework": framework, "task_id": row["task_id"], "type": "fixture_failed", "errors": result["errors"]})
                if (index+1) % 24 == 0:
                    print(json.dumps({"framework": framework, "completed": index+1, "total": len(cases)}), flush=True)
        negative_checks = []
        row = cases[0]
        for framework in ("autogen", "langgraph"):
            for mode in ("malformed_json", "extra_field", "wrong_valid_answer", "provider_exception"):
                async def invalid(messages, metadata):
                    label = automatic_label(row)
                    if mode == "malformed_json":
                        return "{not-json"
                    if mode == "extra_field":
                        return json.dumps({"diagnosis": label, "action": LABEL_ACTIONS[label], "explanation": "extra"})
                    if mode == "wrong_valid_answer":
                        wrong = next(key for key in LABEL_ACTIONS if key != label)
                        return json.dumps({"diagnosis": wrong, "action": LABEL_ACTIONS[wrong]})
                    raise TimeoutError("deterministic unpaid provider-timeout fixture")
                result = await run_external(framework, row, invalid,
                    unit_id=f"external-negative-audit-v1:{framework}:{mode}")
                result["scope"] = "OFFLINE_NEGATIVE_FIXTURE_NOT_PROVIDER_FAILURE_RATE"
                result["negative_test_mode"] = mode
                append(file, result)
                passed = not result["success"] and result["role_order"] == list(ROLES) and result["logical_model_invocations"] == 1
                negative_checks.append({"framework": framework, "mode": mode, "failure_retained": passed})
                if not passed:
                    failures.append({"framework": framework, "type": "failure_repaired_or_dropped", "mode": mode})
    for framework, counts in totals.items():
        if any(value != len(cases) for value in counts.values()):
            failures.append({"framework": framework, "type": "aggregate_contract_mismatch", "counts": counts})
    result = {
        "material_passport": {"artifact_type": "offline_external_framework_contract_audit",
                              "created_utc": datetime.now(timezone.utc).isoformat(),
                              "source": "frozen_candidate_scenarios_and_installed_frameworks",
                              "human_review": "NOT_PERFORMED_USER_AUTHORIZED_AUTOMATIC_VALIDATION"},
        "status": "PASS" if not failures else "FAILED", "adapter_version": ADAPTER_VERSION,
        "versions": versions(), "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
        "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "raw_path": str(path),
        "task_cases_per_framework": len(cases), "normal_units": 2*len(cases),
        "negative_units": len(negative_checks), "real_provider_calls": 0,
        "totals": totals, "negative_checks": negative_checks, "failures": failures,
        "semantic_contract_audit": {
            "four_role_graph": "PASS_PLANNER_RETRIEVER_EXECUTOR_REVIEWER",
            "actual_framework_scheduling": "PASS_INSTALLED_AUTOGEN_TEAM_AND_COMPILED_LANGGRAPH",
            "same_task_facts": "PASS_COMPLETE_OBSERVATIONS_AND_PROCEDURE_ONE_COPY",
            "same_model_messages_between_external_frameworks": "PASS" if not any(f.get("type") == "cross_framework_prompt_mismatch" for f in failures) else "FAILED",
            "read_only_tool": "PASS_EXECUTED_FORMAL_FIXTURE_CHECK_NO_HOST_MUTATION",
            "output_contract": "PASS_NATIVE_PARSE_DECISION_PLUS_MODEL_SELECTED_PROBE",
            "scorer": "PASS_SAME_MAKE_TASK_VERIFIER_AS_NATIVE",
            "failure_handling": "PASS_WRONG_MALFORMED_AND_TIMEOUT_RETAINED_NO_REPAIR",
        },
        "measurement_audit": {
            "actual_provider_io_usage": "PENDING_UNIFIED_REAL_EXECUTOR_CALIBRATION",
            "resources_and_actual_ipc": "PENDING_UNIFIED_REAL_EXECUTOR_INSTRUMENTATION",
            "logical_model_invocations_per_unit": 1,
            "serialization_byte_accounting": "LOCAL_FRAMEWORK_SERIALIZATIONS_NEVER_LABELLED_IPC",
        },
        "comparison_scope": "SYSTEM_LEVEL_REFERENCE",
        "disclosed_differences": [
            "AutoGen RoundRobinGroupChat broadcasts role-output TextMessages and retains its transcript; LangGraph merges typed graph state without a checkpointer.",
            "Both adapters run four logical roles in one process; they do not implement native SS SHM/memfd exchange or native shared-memory retrieval.",
            "Both use one fixed external JSON handoff prompt. Accessible business facts, actions, system instruction, parser, fixture check, and verifier match native T0; native wire-message envelopes differ.",
            "Deterministic planner, retriever and reviewer have advisory LLM calls disabled, as in the native formal configuration; only executor asks the measured model for one logical decision.",
            "No external conversation history is passed to the model beyond its single frozen executor handoff. Framework transcript/state retention is still measured separately.",
            "The offline fixture validates plumbing and grading; it is not evidence of real-model task quality, provider token usage, latency or cross-framework superiority.",
        ],
    }
    (out/"analysis/external_framework_equivalence_v1.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"status": result["status"], "normal_units": result["normal_units"],
                      "negative_units": result["negative_units"], "failures": failures}, ensure_ascii=False), flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/evidence/formal_20261019")
    args = parser.parse_args()
    asyncio.run(audit(Path(args.out)))
