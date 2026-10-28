from __future__ import annotations

from agent_runtime.config import Config, LLMConfig, load_config
from agent_runtime.observability.metrics import MetricsCollector
from agent_runtime.providers.base import LLMResponse
from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.runtime.context import RunContext


class _RecordingLLM:
    model = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return LLMResponse("ok", 1, 1, self.model)


def _context(contract: str) -> tuple[RunContext, _RecordingLLM]:
    llm = _RecordingLLM()
    cfg = Config(llm=LLMConfig(output_contract=contract, max_tokens=None))
    ctx = RunContext(
        config=cfg,
        llm=llm,
        embedding=MockEmbedding(dim=8),
        metrics=MetricsCollector(),
        channel=None,
        mode="text",
        task_id="contract-test",
    )
    return ctx, llm


def test_load_config_reads_output_contract_and_optional_max_tokens(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_OUTPUT_CONTRACT", "json")
    monkeypatch.setenv("AGENT_LLM_MAX_TOKENS", "")
    cfg = load_config(load_env=False)
    assert cfg.llm.output_contract == "json"
    assert cfg.llm.max_tokens is None


def test_invalid_output_contract_is_rejected():
    try:
        LLMConfig(output_contract="xml")
    except ValueError as exc:
        assert "output_contract" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("invalid output contract was accepted")


def test_output_contract_is_appended_only_when_requested():
    none_ctx, none_llm = _context("none")
    none_ctx.call_llm("task prompt")
    assert none_llm.calls[-1]["prompt"] == "task prompt"

    concise_ctx, concise_llm = _context("concise")
    concise_ctx.call_llm("task prompt")
    assert str(concise_llm.calls[-1]["prompt"]).endswith(
        "Answer in at most 2 short sentences."
    )


def test_external_mock_benchmark_exposes_concise_variant():
    from agent_runtime.evaluation.external_llm_benchmark import run_external_llm_benchmark

    payload = run_external_llm_benchmark(repeat=1, allow_mock=True)
    assert "SS_concise" in payload["summary"]
    assert payload["summary"]["SS_concise"]["success_rate"] == 1.0
