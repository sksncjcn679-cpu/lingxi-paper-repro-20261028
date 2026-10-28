"""No-network SDK transport/ledger audit, isolated from real formal records."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, "/opt/lingxi/formal-checkout/src")
import httpx2
from openai import OpenAI
from agent_runtime.config import Config, LLMConfig
from agent_runtime.agents.model_decision import LABEL_ACTIONS
import formal_external_runner as runner
from formal_fixture_checks import automatic_label
from formal_recording import sha


def main():
    original_out = runner.OUT
    out = original_out/"calibration/external_sdk_transport_v1"
    if out.exists():
        raise RuntimeError("isolated SDK audit already exists; refusing overwrite")
    (out/"raw").mkdir(parents=True)
    (out/"manifests").mkdir()
    budget = json.loads((original_out/"manifests/budget_limits.json").read_text())
    (out/"manifests/budget_limits.json").write_text(json.dumps(budget))
    row = runner.native.cases()[0]
    label = automatic_label(row)
    secret = "OFFLINE_SDK_AUDIT_SECRET_NEVER_LOG"
    cfg = Config(llm=LLMConfig(provider="openai", api_key=secret, model="qwen3.7-plus",
                 base_url="https://offline-fixture.invalid/v1", temperature=0, max_tokens=1500,
                 timeout_sec=60, max_retries=2, extra_body={"enable_thinking": False}))
    runner.OUT = runner.native.OUT = out
    runner.native.configuration = lambda *a, **k: cfg
    runner.native.SECRET = secret
    runner.time.sleep = lambda seconds: None
    checks = []
    total_dispatches = 0
    for framework in ("autogen", "langgraph"):
        for mode in ("success", "two_503_then_success", "wrong_answer", "three_timeouts", "malformed_answer"):
            dispatches = []
            def transport(request):
                payload = json.loads(request.content)
                dispatches.append(payload)
                assert request.headers.get("authorization") == "Bearer "+secret
                assert payload["enable_thinking"] is False and "extra_body" not in payload
                assert payload["model"] == cfg.llm.model and payload["temperature"] == 0 and payload["max_tokens"] == 1500
                assert request.headers.get("x-stainless-retry-count") == "0"
                if mode == "three_timeouts":
                    raise httpx2.ReadTimeout("deterministic offline timeout", request=request)
                if mode == "two_503_then_success" and len(dispatches) < 3:
                    return httpx2.Response(503, json={"error": {"message": "offline transient fixture", "type": "server_error"}})
                chosen = next(key for key in LABEL_ACTIONS if key != label) if mode == "wrong_answer" else label
                content = "{bad-json" if mode == "malformed_answer" else json.dumps({"diagnosis": chosen, "action": LABEL_ACTIONS[chosen]})
                return httpx2.Response(200, json={"id": "offline-sdk-response", "object": "chat.completion",
                    "created": 1792368000, "model": "offline-sdk-fixture-not-real-provider",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}})
            def factory(**kwargs):
                return OpenAI(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))
            runner.OpenAI = factory
            unit = {"phase": "external_"+framework, "task_id": row["task_id"], "chain_id": row["chain_id"],
                    "domain": row["domain"], "difficulty": row["difficulty"], "arm": "T0", "repeat": 0,
                    "model": "main", "processes": False, "intervention": None,
                    "run_id": "offline-sdk-audit-"+framework+"-"+mode,
                    "unit_id": "offline-sdk-audit-"+framework+"-"+mode,
                    "topology": "framework_inprocess"}
            result = runner.execute(unit, row, _fixture_evidence=True)
            expected_attempts = 3 if mode in {"two_503_then_success", "three_timeouts"} else 1
            expected_success = mode in {"success", "two_503_then_success"}
            starts = [r for r in runner.native.read_rows("provider_attempt_starts.jsonl") if r["run_id"] == unit["run_id"]]
            calls = [r for r in runner.native.read_rows("provider_calls.jsonl") if r["run_id"] == unit["run_id"]]
            body_rows = [r for r in runner.native.read_rows("model_io.jsonl") if r["run_id"] == unit["run_id"] and r["direction"] == "http_request_body"]
            exact_bodies = all(c["actual_request_utf8_bytes"] == b["actual_utf8_bytes"] and
                               c["actual_http_body_sha256"] == sha(json.dumps(b["request"], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
                               for c, b in zip(calls, body_rows))
            good = (result["success"] == expected_success and len(dispatches) == expected_attempts and
                    len(starts) == len(calls) == len(body_rows) == expected_attempts and exact_bodies and
                    result["observable_ipc_bytes"] == 0 and result["provider_input_tokens"] is None and
                    not result["provider_usage_complete"] and result["span_partition_error_sec"] < 1e-8 and
                    result["model_evidence"] == "offline_sdk_transport_fixture_not_provider")
            checks.append({"framework": framework, "mode": mode, "pass": good,
                           "actual_sdk_http_dispatches": len(dispatches), "recorded_attempts": len(calls),
                           "expected_attempts": expected_attempts, "expected_success": expected_success,
                           "actual_success": result["success"], "actual_body_sha_and_bytes_conserved": exact_bodies})
            total_dispatches += len(dispatches)
    serialized = "\n".join(path.read_text() for path in (out/"raw").glob("*.jsonl"))
    secret_absent = secret not in serialized
    report = {"material_passport": {"artifact_type": "offline_sdk_transport_recorder_audit",
                                      "scope": "isolated MockTransport fixtures; no network or paid calls"},
              "status": "PASS" if all(c["pass"] for c in checks) and secret_absent else "FAILED",
              "real_provider_calls": 0, "mock_http_dispatches": total_dispatches,
              "secret_absent_from_all_saved_jsonl": secret_absent, "checks": checks,
              "ledgers_isolated_path": str(out),
              "validated": ["actual SDK encoded HTTP body hash/length", "extra_body flattened by SDK",
                  "SDK hidden retries disabled", "three attempts maximum", "only transient errors retried",
                  "full attempts/request/response ledger", "malformed and wrong answers retained",
                  "mutually exclusive span conservation", "no fabricated IPC or real usage",
                  "Authorization excluded from all saved records"]}
    (original_out/"analysis/external_sdk_recorder_audit_v1.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
