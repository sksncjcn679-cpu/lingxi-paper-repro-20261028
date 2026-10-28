"""External SDK calibration v2; all responses and reported usage are fixtures.

Four public domains x two real framework schedulers, six boundary scenarios, and
two direct dry units. This script never loads credentials or permits Internet
sockets. Retain and classify the precisely diagnosed, still-blocked local IPv6
probe; every other network/credential attempt must be absent. The v1 helper and
its failed evidence remain unchanged. This is not a performance experiment.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import socket
import sys
import time
import traceback
from types import SimpleNamespace
import uuid

HERE = Path(__file__).resolve().parent
STUDY_OUT = Path("/evidence/protocol_repair_20261020_v1")
DEFAULT_DESTINATION = STUDY_OUT / "calibration/external_sdk_v2"
DIAGNOSTIC = STUDY_OUT / "calibration/external_access_diagnostic_v1/access_diagnostic.json"
DIAGNOSTIC_SHA256 = "5d400ac296d61ca5096adec1c68742b592ef5d19637903134fabcfb0264f340d"
V1_HELPER_SHA256 = "ac9b39dcd4cdbadbf4c314bb59c066675277fe2cbd47cbdeb3fec4ef9ade8b11"
RUNNER_SHA256 = "45da2d4ae69ea5a9b4e66c37b677d21e2723cc83fa4b7c82b5da4b2140fd9c60"
V1_FAIL_SHA256 = "5cf1e7a2bf27692515213703f6d234395e8d016f688a820b9028dc316bc1eb5f"
PROBE_MODULE_SHA256 = "2633bbdb69731e5ccb5cf4e4afd65605d86c7979cc5633126f50c92d5ad74a74"
PROBE_FUNCTION_SHA256 = "d8ab130d2b780c9623eebffb295089e34150fa55636d9398c1b0f9340a2b09cf"
DOMAINS = ("service", "migration", "resources", "network_permissions")
FRAMEWORKS = ("autogen", "langgraph")
SDK_SCENARIOS = (("autogen", "malformed_json"), ("langgraph", "wrong_action"),
                 ("autogen", "missing_usage"), ("langgraph", "missing_cached"),
                 ("autogen", "two_503_then_success"), ("langgraph", "three_503_exhausted"))
OFFLINE_KEY = "OFFLINE_EXTERNAL_SDK_CALIBRATION_NOT_A_PRIVATE_KEY"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def file_hashes(folder):
    return {str(path): digest(path) for path in sorted(folder.glob("*")) if path.is_file()}


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.parent != (STUDY_OUT / "calibration").resolve():
        raise ValueError("calibration output must be a new direct child of the repair calibration directory")
    if destination.exists():
        raise RuntimeError("calibration destination exists; choose a new version, never overwrite")
    destination.mkdir(parents=True)
    (destination / "manifests").mkdir()
    blocked = Counter()
    classified = Counter()
    unexpected = Counter()
    access_events = []
    reference_event = {}
    phase = "initial_validation"

    def deny_internet_and_secret_reads(event, arguments):
        kind = None
        details = {}
        if event == "socket.__new__" and arguments[1] in (socket.AF_INET, socket.AF_INET6):
            kind = "internet_socket_attempts"
            details.update(family=int(arguments[1]), family_name=socket.AddressFamily(arguments[1]).name,
                           socket_type=int(arguments[2]), protocol=int(arguments[3]))
        elif event in ("socket.getaddrinfo", "socket.connect"):
            kind = "dns_attempts" if event == "socket.getaddrinfo" else "connect_attempts"
        elif event == "open" and isinstance(arguments[0], (str, bytes)):
            name = arguments[0].decode() if isinstance(arguments[0], bytes) else arguments[0]
            path = Path(name)
            if path.name in (".env", "model.env") or "/run/secrets/" in path.as_posix():
                kind = "secret_file_reads"
        if kind is None:
            return
        blocked[kind] += 1
        frames = [{"file": frame.filename if frame.filename.startswith("<") else
                   "/".join(Path(frame.filename).parts[-4:]),
                   "function": frame.name, "line": frame.lineno}
                  for frame in traceback.extract_stack(limit=48)[:-1]]
        expected_probe = False
        if (kind == "internet_socket_attempts" and phase == "import:autogen_agentchat.agents"
                and details == {key: reference_event.get(key) for key in
                                ("family", "family_name", "socket_type", "protocol")}):
            # Compare the complete observed non-importlib caller chain, exact
            # line numbers, file identity, function source and module bytes.
            # All sockets remain blocked even when this attribution succeeds.
            actual_chain = [frame for frame in frames if not frame["file"].startswith("<frozen")]
            reference_chain = [frame for frame in reference_event.get("frames", [])
                               if not frame["file"].startswith("<frozen")]
            frame = inspect.currentframe().f_back
            while frame is not None:
                if (frame.f_code.co_name == "_has_ipv6"
                        and frame.f_globals.get("__name__") == "urllib3.util.connection"):
                    try:
                        function = frame.f_globals.get("_has_ipv6")
                        source = inspect.getsource(function)
                        module_sha = digest(frame.f_code.co_filename)
                        function_sha = hashlib.sha256(source.encode()).hexdigest()
                        details.update(module_sha256=module_sha, function_source_sha256=function_sha,
                                       function="urllib3.util.connection._has_ipv6")
                        expected_probe = (function.__code__ is frame.f_code and actual_chain == reference_chain
                                          and module_sha == PROBE_MODULE_SHA256
                                          and function_sha == PROBE_FUNCTION_SHA256)
                    except Exception as exc:
                        details["attribution_error_type"] = type(exc).__name__
                    break
                frame = frame.f_back
            del frame
        classification = "expected_blocked_local_ipv6_probe" if expected_probe else "unexpected_" + kind
        classified[classification] += 1
        if not expected_probe:
            unexpected[kind] += 1
        access_events.append({"index": len(access_events) + 1, "phase": phase, "event": event,
                              "classification": classification, "raw_denial_category": kind,
                              "decision": "BLOCKED_BEFORE_OPERATION", "frames": frames, **details})
        raise RuntimeError("offline external calibration blocked " + classification)

    # Install the denial before importing either driver or third-party runtime.
    sys.addaudithook(deny_internet_and_secret_reads)
    sys.path.insert(0, str(HERE))
    started = time.perf_counter()
    report = {"status": "FAIL_RETAINED", "model_evidence": "offline_sdk_transport_fixture_not_provider",
              "paid_api_calls": 0, "real_provider_usage_calibrated": False,
              "model_quality_measured": False, "performance_or_overhead_measured": False}
    failure = None
    runner = None
    clients = []
    results = []
    checks = []
    transports = []
    config_calls = []
    skipped_waits = []
    original_globals = None
    try:
        assert digest(DIAGNOSTIC) == DIAGNOSTIC_SHA256
        diagnostic = json.loads(DIAGNOSTIC.read_text())
        assert diagnostic["status"] == "IMPORT_DIAGNOSTIC_COMPLETED_ALL_ACCESS_ATTEMPTS_RETAINED"
        assert diagnostic["audit_counts"] == {"internet_socket_attempts": 1}
        assert len(diagnostic["audit_events"]) == 1
        reference_event.update(diagnostic["audit_events"][0])
        assert reference_event["phase"] == "import:autogen_agentchat.agents"
        assert reference_event["event"] == "socket.__new__" and reference_event["family"] == int(socket.AF_INET6)
        assert diagnostic["urllib3_probe_source"]["module_sha256"] == PROBE_MODULE_SHA256
        assert diagnostic["urllib3_probe_source"]["function_source_sha256"] == PROBE_FUNCTION_SHA256
        assert digest(HERE / "calibrate_external.py") == V1_HELPER_SHA256
        assert digest(HERE / "external_runner.py") == RUNNER_SHA256
        v1_fail = STUDY_OUT / "calibration/external_sdk_v1/external_sdk_audit.json"
        assert digest(v1_fail) == V1_FAIL_SHA256
        report.update(v1_helper_sha256=V1_HELPER_SHA256, v1_fail_sha256=V1_FAIL_SHA256,
                      diagnostic_sha256=DIAGNOSTIC_SHA256,
                      expected_probe_module_sha256=PROBE_MODULE_SHA256,
                      expected_probe_function_source_sha256=PROBE_FUNCTION_SHA256,
                      denial_policy="all INET/DNS/connect/credential attempts remain blocked; only attribution differs")
        phase = "import:external_runner"
        import external_runner as runner
        phase = "import:httpx"
        import httpx
        phase = "import:openai"
        from openai import OpenAI
        # Reproduce the diagnostic import phase before task-unit timing. This
        # executes the same import-only dependency probe under the same denial.
        phase = "import:autogen_agentchat.agents"
        importlib.import_module("autogen_agentchat.agents")
        phase = "calibration_setup"

        original_globals = (runner.OUT, runner.native.OUT, runner.DRY_OUT,
                            runner.native.configuration, runner.time)
        assert runner.OUT == runner.native.OUT == STUDY_OUT
        source_before = runner._source_tree_sha256(runner.native.ROOT)
        driver_hashes = {str(path): digest(path) for path in sorted(HERE.glob("*.py"))}
        dependency_hashes = {str(Path(module.__file__)): digest(module.__file__) for module in
                             (runner.adapter, runner.oracle, sys.modules["bootstrap"],
                              sys.modules["contract"], sys.modules["formal_recording"],
                              sys.modules["formal_fact_calibration"], sys.modules["formal_fixture_checks"])}
        corpus_hash = digest(runner.native.CORPUS)
        study_raw_before = file_hashes(STUDY_OUT / "raw")
        versions = {name: importlib.metadata.version(name) for name in
                    ("openai", "httpx", "psutil", "autogen-agentchat", "langgraph")}
        assert runner.adapter.versions() == runner.adapter.PINNED_VERSIONS
        assert runner.adapter.check is runner.oracle.check
        assert len(runner.LABEL_ACTIONS) == 24
        captured = json.loads(runner.adapter.DECISION_INSTRUCTION.split("Allowed pairs: ", 1)[1])
        assert captured == runner.LABEL_ACTIONS == runner.EFFECTIVE_CONTRACT["installed_label_actions"]
        report.update(helper_sha256=driver_hashes, reused_dependency_sha256=dependency_hashes,
                      business_source_before_sha256=source_before, corpus_sha256=corpus_hash,
                      package_versions=versions, python_version=sys.version,
                      effective_contract_sha256=runner.EFFECTIVE_CONTRACT["effective_contract_sha256"],
                      planned_sdk_fixture_units=14, planned_direct_dry_units=2,
                      planned_mock_http_dispatches=18)

        public = [row for row in runner.native.cases() if row["split"] == "pilot"]
        selected = {domain: sorted((row for row in public if row["domain"] == domain),
                                   key=lambda row: row["task_id"])[0] for domain in DOMAINS}
        assert len(public) == 24 and len(selected) == 4
        specifications = [(framework, "success", selected[domain], False)
                          for domain in DOMAINS for framework in FRAMEWORKS]
        specifications += [(framework, mode, selected[DOMAINS[index % 4]], False)
                           for index, (framework, mode) in enumerate(SDK_SCENARIOS)]
        specifications += [(framework, "direct_dry", selected[DOMAINS[index]], True)
                           for index, framework in enumerate(FRAMEWORKS)]
        units = []
        for framework, mode, row, dry in specifications:
            unit = {"study_id": "protocol_repair_20261020_v1", "phase": "external_" + framework,
                    "task_id": row["task_id"], "chain_id": row["chain_id"], "domain": row["domain"],
                    "difficulty": row["difficulty"], "task_sha256": row["content_sha256"],
                    "arm": "T0", "repeat": 0, "model": "main", "processes": False,
                    "intervention": None, "topology": "framework_inprocess", "calibration_only": True,
                    "calibration_scenario": mode, "calibration_destination": str(destination),
                    "effective_contract_sha256": runner.EFFECTIVE_CONTRACT["effective_contract_sha256"]}
            unit["run_id"] = uuid.uuid5(uuid.NAMESPACE_URL, "external-sdk-calibration/" + canonical(unit)).hex
            unit["unit_id"] = unit["run_id"]
            units.append(unit)
        assert len(units) == len({unit["run_id"] for unit in units}) == 16
        write_new(destination / "manifests/calibration_units.json", units)
        budget = json.loads((STUDY_OUT / "manifests/budget_limits.json").read_text())
        budget.update(max_provider_attempts=18, observed_reported_total_tokens_stop=200000,
                      scope="offline external SDK fixtures only; zero real provider calls")
        write_new(destination / "manifests/budget_limits.json", budget)

        original_configuration = runner.native.configuration

        def only_offline_configuration(*arguments, **keywords):
            assert keywords.get("offline") is True, "calibration tried to request private configuration"
            config_calls.append({"offline": True})
            cfg = original_configuration(*arguments, **keywords)
            assert cfg.llm.provider == "mock" and not cfg.llm.api_key and not runner.native.SECRET
            return cfg

        runner.OUT = runner.native.OUT = destination
        runner.DRY_OUT = destination / "dry"
        runner.native.configuration = only_offline_configuration
        # Keep requested backoff values in the raw ledger without making this an
        # unnecessary wall-time exercise. This process-local seam does not alter time globally.
        runner.time = SimpleNamespace(perf_counter=time.perf_counter, perf_counter_ns=time.perf_counter_ns,
                                      sleep=lambda seconds: skipped_waits.append(seconds))

        for unit, (framework, mode, row, dry) in zip(units, specifications):
            phase = "unit:" + unit["run_id"]
            decision = runner.oracle.classify_facts(row["domain"], row["observations"])
            selected_response = dict(decision)
            if mode == "wrong_action":
                selected_response["action"] = next(action for action in runner.LABEL_ACTIONS.values()
                                                   if action != decision["action"])
            response_text = "{not-json" if mode == "malformed_json" else canonical(selected_response)
            dispatches = []

            def factory(cfg):
                def handler(request):
                    payload = json.loads(request.content)
                    request_id = request.headers["X-Client-Request-Id"]
                    assert request.headers["authorization"] == "Bearer " + OFFLINE_KEY
                    assert request.headers["Idempotency-Key"] == unit["run_id"]
                    assert request.headers.get("x-stainless-retry-count") == "0"
                    assert payload["model"] == cfg.llm.model and payload["temperature"] == 0
                    assert payload["max_tokens"] == 1500 and payload["enable_thinking"] is False
                    assert "extra_body" not in payload
                    expected_messages, _ = runner.adapter.external_messages(row,
                        {"operation": "llm_diagnose", "operands": ["observations", "procedure"], "defect": row["domain"]},
                        {"observations": row["observations"], "procedure": row["procedure"]})
                    assert payload["messages"] == expected_messages
                    entry = {"run_id": unit["run_id"], "request_id": request_id,
                             "bytes": len(request.content), "sha256": hashlib.sha256(request.content).hexdigest(),
                             "payload": payload, "model_evidence": "offline_sdk_transport_fixture_not_provider"}
                    dispatches.append(entry)
                    transports.append(entry)
                    if mode == "three_503_exhausted" or (mode == "two_503_then_success" and len(dispatches) < 3):
                        return httpx.Response(503, json={"error": {"type": "server_error",
                                                                  "message": "offline transient fixture"}})
                    response = {"id": "offline-" + request_id, "object": "chat.completion", "created": 1792458000,
                                "model": "offline-external-sdk-fixture-not-provider",
                                "choices": [{"index": 0, "message": {"role": "assistant", "content": response_text},
                                             "finish_reason": "stop"}]}
                    if mode != "missing_usage":
                        response["usage"] = {"prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60}
                        if mode != "missing_cached":
                            response["usage"]["prompt_tokens_details"] = {"cached_tokens": 0}
                    return httpx.Response(200, json=response)

                client = OpenAI(api_key=OFFLINE_KEY, base_url="https://offline-external-fixture.invalid/v1",
                                timeout=60, max_retries=0,
                                http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False))
                clients.append(client)
                return client

            result = runner.execute(unit, row, dry=dry, _sdk_fixture_factory=None if dry else factory)
            results.append(result)
            expected_attempts = 0 if dry else 3 if mode in ("two_503_then_success", "three_503_exhausted") else 1
            expected_success = mode not in ("malformed_json", "wrong_action", "three_503_exhausted")
            assert result["success"] is expected_success and result["quality"] == float(expected_success)
            assert result["provider_attempts"] == len(dispatches) == expected_attempts
            assert result["model_evidence"] == ("deterministic_fixture" if dry else
                                                 "offline_sdk_transport_fixture_not_provider")
            assert result["effective_contract_sha256"] == runner.EFFECTIVE_CONTRACT["effective_contract_sha256"]
            assert not result["provider_usage_complete"] and not result["provider_cached_input_tokens_complete"]
            assert all(result[name] is None for name in ("provider_input_tokens", "provider_output_tokens",
                       "provider_total_tokens", "provider_cached_input_tokens", "reported_token_lower_bound"))
            assert not result["workers_remaining"] and not result["shm_names_created_but_remaining"]
            assert not result.get("cleanup_error") and result["observable_ipc_bytes"] == 0
            assert result["span_partition_error_sec"] < 1e-8
            assert result["role_order"] == list(runner.adapter.ROLES)
            assert result["external_framework_versions"] == runner.adapter.PINNED_VERSIONS
            if mode == "wrong_action":
                answer = result["outputs"]["answer"]
                assert {key: answer[key] for key in decision} == selected_response
                assert result["observed_fixture_check"]["current_record_matches_check"] is False
            if mode in ("malformed_json", "three_503_exhausted"):
                assert result["outputs"]["answer"] is None and result["error"]
            if mode == "missing_usage":
                assert result["fixture_usage_for_calibration"] == []
            if mode == "missing_cached":
                assert len(result["fixture_usage_for_calibration"]) == 1
                assert (result["fixture_usage_for_calibration"][0].get("prompt_tokens_details") or {}).get("cached_tokens") is None
            checks.append({"run_id": unit["run_id"], "framework": framework, "domain": row["domain"],
                           "scenario": mode, "passed": True, "success_expected": expected_success,
                           "attempts_expected": expected_attempts, "actual_dispatches": len(dispatches),
                           "model_evidence": result["model_evidence"]})

        raw = destination / "raw"
        starts = rows(raw / "provider_attempt_starts.jsonl")
        calls = rows(raw / "provider_calls.jsonl")
        io = rows(raw / "model_io.jsonl")
        bodies = [item for item in io if item["direction"] == "http_request_body"]
        requests = [item for item in io if item["direction"] == "request"]
        terminals = rows(raw / "task_runs.jsonl")
        dry_terminals = rows(runner.DRY_OUT / "raw/dry_task_runs.jsonl")
        framework_runs = rows(raw / "external_framework_runs.jsonl")
        framework_runs += rows(runner.DRY_OUT / "raw/external_framework_runs.jsonl")
        assert len(starts) == len(calls) == len(bodies) == len(requests) == len(transports) == 18
        assert len(terminals) == 14 and len(dry_terminals) == 2 and len(framework_runs) == 16
        joined = {item["request_id"]: item for item in transports}
        by_body = {item["request_id"]: item for item in bodies}
        assert len(joined) == len(by_body) == 18
        assert set(joined) == {item["request_id"] for item in starts} == {item["request_id"] for item in calls}
        for call in calls:
            transport, body = joined[call["request_id"]], by_body[call["request_id"]]
            assert call["run_id"] == transport["run_id"] == body["run_id"]
            assert call["actual_http_dispatches"] == 1 and call["sdk_max_retries"] == 0
            assert call["actual_http_body_sha256"] == transport["sha256"]
            assert call["actual_request_utf8_bytes"] == body["actual_utf8_bytes"] == transport["bytes"]
            assert body["request"] == transport["payload"] and body["headers"] == "NOT_RETAINED"
            assert call["model_evidence"] == "offline_sdk_transport_fixture_not_provider"
            assert call["business_facts_equal"] is True
            if call["status"] == "OK":
                assert call["usage_source"] == ("missing" if call["usage_raw"] is None else "offline_sdk_transport_fixture")
        for work in framework_runs:
            counts = Counter(event["kind"] for event in work["events"])
            assert work["logical_model_invocations"] == 1 and work["role_order"] == list(runner.adapter.ROLES)
            assert counts["role.start"] == counts["role.end"] == counts["framework.agent_input"] == 4
            assert counts["model.invoke"] == 1 and counts["tool.lookup"] == 2
        assert len(skipped_waits) == len(rows(raw / "provider_retry_waits.jsonl")) == 4
        assert len(config_calls) == 16 and not runner.native.SECRET
        assert all(client.is_closed() for client in clients)
        assert blocked == Counter({"internet_socket_attempts": 1})
        assert classified == Counter({"expected_blocked_local_ipv6_probe": 1}) and not unexpected
        assert len(results) == len(checks) == 16
        assert runner._source_tree_sha256(runner.native.ROOT) == source_before
        assert all(digest(path) == value for path, value in {**driver_hashes, **dependency_hashes}.items())
        assert file_hashes(STUDY_OUT / "raw") == study_raw_before
        assert digest(HERE / "calibrate_external.py") == V1_HELPER_SHA256
        assert digest(HERE / "external_runner.py") == RUNNER_SHA256
        assert digest(v1_fail) == V1_FAIL_SHA256
        saved_text = "\n".join(path.read_text() for path in destination.rglob("*.jsonl"))
        assert OFFLINE_KEY not in saved_text
        # Exercise aggregation independently of fixture tagging: unknown cached
        # usage stays unknown, while a reported zero remains an observed zero.
        known = {"status": "OK", "usage_raw": {"prompt_tokens": 42, "completion_tokens": 18,
                  "total_tokens": 60, "prompt_tokens_details": {"cached_tokens": 0}}}
        assert runner._usage_fields([known], dry=False, fixture=False)["provider_cached_input_tokens"] == 0
        unknown_cache = {"status": "OK", "usage_raw": {"prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60}}
        assert runner._usage_fields([unknown_cache], dry=False, fixture=False)["provider_cached_input_tokens"] is None
        assert not runner._usage_fields([{"status": "OK", "usage_raw": None}], dry=False,
                                        fixture=False)["provider_usage_complete"]
        report.update(status="PASS_OFFLINE_EXTERNAL_RUNNER_SDK_CALIBRATION_V2", task_units=16,
                      sdk_fixture_units=14, direct_dry_units=2, public_domains=4, public_tasks=len(selected),
                      actual_mock_http_dispatches=18, actual_framework_versions=runner.adapter.versions(),
                      package_versions=versions, python_version=sys.version,
                      effective_contract_sha256=runner.EFFECTIVE_CONTRACT["effective_contract_sha256"],
                      business_source_before_after_sha256=source_before, helper_sha256=driver_hashes,
                      reused_dependency_sha256=dependency_hashes, corpus_sha256=corpus_hash,
                      raw_sha256=file_hashes(raw), dry_raw_sha256=file_hashes(runner.DRY_OUT / "raw"),
                      complete_attempt_and_actual_body_sha_join="PASS", work_counter_contract="PASS",
                      actual_sdk_clients_closed=len(clients), study_raw_unchanged=True,
                      retry_waits_skipped_for_local_fixture_only=len(skipped_waits),
                      missing_usage_and_cached_usage_never_filled=True,
                      errors_retained_without_answer_repair=True, checks=checks)
    except BaseException as exc:
        failure = exc
        report.update(error_type=type(exc).__name__, error=str(exc)[:2000],
                      finished_units=len(results), finished_checks=checks)
    finally:
        for client in clients:
            if not client.is_closed():
                try:
                    client.close()
                except Exception as exc:
                    report.setdefault("cleanup_errors", []).append({"type": type(exc).__name__, "message": str(exc)[:500]})
        if original_globals is not None:
            runner.OUT, runner.native.OUT, runner.DRY_OUT, runner.native.configuration, runner.time = original_globals
        report.update(blocked_access_attempts=dict(blocked), elapsed_local_fixture_sec=time.perf_counter() - started,
                      access_classification_counts=dict(classified),
                      unexpected_access_attempts={name: unexpected[name] for name in
                          ("internet_socket_attempts", "dns_attempts", "connect_attempts", "secret_file_reads")},
                      expected_blocked_local_ipv6_probe=classified["expected_blocked_local_ipv6_probe"],
                      blocked_access_events=access_events, destination=str(destination), results_are_fixtures_only=True)
        write_new(destination / "external_sdk_audit.json", report)
        print(json.dumps({key: value for key, value in report.items() if key not in
                          ("helper_sha256", "reused_dependency_sha256", "raw_sha256", "dry_raw_sha256",
                           "checks", "finished_checks", "blocked_access_events")}, ensure_ascii=False), flush=True)
    if failure is not None:
        raise failure


if __name__ == "__main__":
    main()
