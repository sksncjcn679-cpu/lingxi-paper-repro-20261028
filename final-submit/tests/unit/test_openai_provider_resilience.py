"""fable6: bounded timeout + visible retry loop for the real-LLM provider (offline, faked SDK)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


class _Timeout(Exception):
    pass


_Timeout.__name__ = "APITimeoutError"


class _BadRequest(Exception):
    status_code = 400


class _Status(Exception):
    def __init__(self, status: int, msg: str = "", retry_after: str | None = None):
        super().__init__(msg or f"HTTP {status}")
        self.status_code = status
        self.response = SimpleNamespace(status_code=status, headers={"retry-after": retry_after} if retry_after else {})


def _ok():
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=2, prompt_tokens_details=SimpleNamespace(cached_tokens=3))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=usage)


def _install(monkeypatch, script):
    created = []

    class Completions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            step = script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

    def factory(**kwargs):
        client = SimpleNamespace(kwargs=kwargs, chat=SimpleNamespace(completions=Completions()))
        created.append(client)
        return client

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=factory))
    return created


def _llm(**kw):
    from agent_runtime.providers.openai_provider import OpenAILLM

    waits: list[float] = []
    llm = OpenAILLM(model="m", api_key="k", sleep=waits.append, **kw)
    return llm, waits


def test_sdk_timeout_is_bounded_and_sdk_retries_disabled(monkeypatch):
    created = _install(monkeypatch, [_ok()])
    llm, _ = _llm(timeout_sec=12.5, max_retries=2)
    assert created[0].kwargs["timeout"] == 12.5
    assert created[0].kwargs["max_retries"] == 0
    resp = llm.complete("hi")
    assert resp.metadata["attempts"] == 1
    assert resp.metadata["cached_prompt_tokens"] == 3
    assert "request_wall_sec" in resp.metadata


def test_transient_errors_are_retried_with_recorded_backoff(monkeypatch):
    created = _install(monkeypatch, [_Timeout("timed out"), _Status(503), _ok()])
    llm, waits = _llm(max_retries=3)
    resp = llm.complete("hi")
    assert resp.text == "ok"
    assert resp.metadata["attempts"] == 3
    assert [r["error"] for r in resp.metadata["retries"]] == ["APITimeoutError", "_Status"]
    assert len(waits) == 2 and all(w > 0 for w in waits)
    assert len(created[0].chat.completions.calls) == 3


def test_retry_after_header_is_respected(monkeypatch):
    _install(monkeypatch, [_Status(429, retry_after="7"), _ok()])
    llm, waits = _llm(max_retries=1)
    llm.complete("hi")
    assert waits[0] >= 7.0


def test_retry_budget_is_finite(monkeypatch):
    _install(monkeypatch, [_Status(500), _Status(500), _Status(500)])
    llm, waits = _llm(max_retries=2)
    with pytest.raises(_Status):
        llm.complete("hi")
    assert len(waits) == 2


def test_non_transient_errors_are_not_retried(monkeypatch):
    _install(monkeypatch, [_BadRequest("bad request: something")])
    llm, waits = _llm(max_retries=5)
    with pytest.raises(_BadRequest):
        llm.complete("hi")
    assert waits == []


def test_unsupported_temperature_falls_back_once_and_is_recorded(monkeypatch):
    created = _install(monkeypatch, [
        _BadRequest("Unsupported value: 'temperature' does not support 0.0 with this model. Only the default (1) value is supported."),
        _ok(), _ok(),
    ])
    llm, _ = _llm()
    resp = llm.complete("hi")
    calls = created[0].chat.completions.calls
    assert "temperature" in calls[0] and "temperature" not in calls[1]
    assert resp.metadata["temperature_unsupported"] is True
    llm.complete("again")
    assert "temperature" not in calls[2]


def test_config_rejects_bad_timeout():
    from agent_runtime.config import LLMConfig

    with pytest.raises(ValueError):
        LLMConfig(timeout_sec=0)
    with pytest.raises(ValueError):
        LLMConfig(max_retries=-1)
