"""Import-only, fail-closed caller audit for the external calibration process.

No task units, framework runs, SDK requests, or performance work are dispatched.
INET creation, DNS resolution, connect, and credential reads remain prohibited.
Stacks retain module/function/line identity without source lines or local values.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import socket
import sys
import traceback

HERE = Path(__file__).resolve().parent
OUT = Path("/evidence/protocol_repair_20261020_v1")
DEFAULT_DESTINATION = OUT / "calibration/external_access_diagnostic_v1"
V1_HELPER_SHA256 = "ac9b39dcd4cdbadbf4c314bb59c066675277fe2cbd47cbdeb3fec4ef9ade8b11"
RUNNER_SHA256 = "45da2d4ae69ea5a9b4e66c37b677d21e2723cc83fa4b7c82b5da4b2140fd9c60"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_file(path):
    if path.startswith("<"):
        return path
    return "/".join(Path(path).parts[-4:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.parent != (OUT / "calibration").resolve():
        raise ValueError("diagnostic destination must be a direct repair calibration child")
    if destination.exists():
        raise RuntimeError("diagnostic destination exists; choose a new version")
    destination.mkdir(parents=True)
    events = []
    counts = Counter()
    phases = []
    phase = "before_imports"
    report = {"status": "DIAGNOSTIC_NOT_CALIBRATION", "task_units_executed": 0,
              "sdk_requests_dispatched": 0, "paid_api_calls": 0,
              "inet_and_credential_denial_active": True,
              "stack_redaction": "file suffix, function and line only; no source lines, arguments, locals or environment values"}
    failure = None

    def audit(event, arguments):
        kind = None
        extra = {}
        if event == "socket.__new__" and arguments[1] in (socket.AF_INET, socket.AF_INET6):
            kind = "internet_socket_attempts"
            extra = {"family": int(arguments[1]), "family_name": socket.AddressFamily(arguments[1]).name,
                     "socket_type": int(arguments[2]), "protocol": int(arguments[3])}
        elif event in ("socket.getaddrinfo", "socket.connect"):
            kind = "dns_attempts" if event == "socket.getaddrinfo" else "connect_attempts"
        elif event == "open" and isinstance(arguments[0], (str, bytes)):
            name = arguments[0].decode() if isinstance(arguments[0], bytes) else arguments[0]
            path = Path(name)
            if path.name in (".env", "model.env") or "/run/secrets/" in path.as_posix():
                kind = "secret_file_reads"
        if kind is not None:
            counts[kind] += 1
            frames = [{"file": safe_file(frame.filename), "function": frame.name, "line": frame.lineno}
                      for frame in traceback.extract_stack(limit=48)[:-1]]
            # Store no event arguments for DNS/connect/open: they could include
            # private endpoints or names. Socket constructor numeric values only.
            events.append({"index": len(events) + 1, "phase": phase, "event": event,
                           "classification": kind, "decision": "BLOCKED_BEFORE_OPERATION",
                           "frames": frames, **extra})
            raise RuntimeError("import-only diagnostic blocked " + kind)

    sys.addaudithook(audit)
    sys.path.insert(0, str(HERE))
    try:
        assert digest(HERE / "calibrate_external.py") == V1_HELPER_SHA256, "preserve the executed v1 helper source"
        assert digest(HERE / "external_runner.py") == RUNNER_SHA256, "runner source differs from v1"
        prior = OUT / "calibration/external_sdk_v1/external_sdk_audit.json"
        if prior.exists():
            saved = json.loads(prior.read_text())
            report.update(prior_report_sha256=digest(prior), prior_status=saved.get("status"),
                          prior_blocked_access_attempts=saved.get("blocked_access_attempts"))
        for name in ("external_runner", "httpx", "openai"):
            phase = "import:" + name
            module = importlib.import_module(name)
            phases.append({"phase": phase, "status": "IMPORTED", "module_file": safe_file(module.__file__),
                           "module_sha256": digest(module.__file__), "audit_events_so_far": len(events)})
        runner = sys.modules["external_runner"]
        phase = "offline_native_configuration"
        cfg = runner.native.configuration(offline=True)
        assert cfg.llm.provider == "mock" and not cfg.llm.api_key and not runner.native.SECRET
        phases.append({"phase": phase, "status": "OFFLINE_NO_KEY", "audit_events_so_far": len(events)})
        phase = "sdk_mock_client_construct_and_close_no_request"
        httpx = sys.modules["httpx"]
        OpenAI = sys.modules["openai"].OpenAI

        def no_dispatch(request):
            raise AssertionError("import-only diagnostic must never dispatch even a mock HTTP request")

        client = OpenAI(api_key="offline-import-diagnostic", base_url="https://offline-import-fixture.invalid/v1",
                        max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(no_dispatch), trust_env=False))
        client.close()
        assert client.is_closed()
        phases.append({"phase": phase, "status": "CLOSED_WITHOUT_REQUEST", "audit_events_so_far": len(events)})
        for name in ("autogen_agentchat.agents", "autogen_agentchat.base", "autogen_agentchat.messages",
                     "autogen_agentchat.teams", "langgraph.graph"):
            phase = "import:" + name
            module = importlib.import_module(name)
            phases.append({"phase": phase, "status": "IMPORTED", "module_file": safe_file(module.__file__),
                           "module_sha256": digest(module.__file__), "audit_events_so_far": len(events)})
        phase = "record_actual_dependency_sources"
        report["actual_framework_versions"] = runner.adapter.versions()
        probe = sys.modules.get("urllib3.util.connection")
        if probe is not None:
            function = probe._has_ipv6
            source, first_line = inspect.getsourcelines(function)
            report["urllib3_probe_source"] = {"module_file": safe_file(probe.__file__),
                "module_sha256": digest(probe.__file__), "function": function.__qualname__,
                "first_line": first_line, "source": "".join(source),
                "function_source_sha256": hashlib.sha256("".join(source).encode()).hexdigest()}
        assert digest(HERE / "calibrate_external.py") == V1_HELPER_SHA256
        assert digest(HERE / "external_runner.py") == RUNNER_SHA256
        report.update(status="IMPORT_DIAGNOSTIC_COMPLETED_ALL_ACCESS_ATTEMPTS_RETAINED",
                      preserved_v1_helper_sha256=V1_HELPER_SHA256, preserved_runner_sha256=RUNNER_SHA256)
    except BaseException as exc:
        failure = exc
        report.update(status="IMPORT_DIAGNOSTIC_FAILED_RETAINED", error_type=type(exc).__name__,
                      error_phase=phase, error="exception text omitted; audit stack and type retained")
    finally:
        report.update(audit_counts=dict(counts), audit_events=events, import_phases=phases,
                      diagnostic_source_sha256=digest(Path(__file__)), destination=str(destination))
        with (destination / "access_diagnostic.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({key: value for key, value in report.items() if key not in
                          ("audit_events", "import_phases", "urllib3_probe_source")}, ensure_ascii=False), flush=True)
    if failure is not None:
        raise failure


if __name__ == "__main__":
    main()
