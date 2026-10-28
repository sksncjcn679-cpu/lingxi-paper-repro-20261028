"""OS-SOTA1: completion-budget governor, sticky parameter negotiation, billing decomposition."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


class _Completions:
    def __init__(self, completion=3, reasoning=0, reject=()):
        self.calls, self.completion, self.reasoning, self.reject = [], completion, reasoning, set(reject)

    def create(self, **kw):
        self.calls.append(kw)
        for param in self.reject:
            if param in kw:
                raise ValueError(f"Unsupported parameter: '{param}' is not supported with this model")
        usage = SimpleNamespace(
            prompt_tokens=4400, completion_tokens=self.completion,
            prompt_tokens_details=SimpleNamespace(cached_tokens=3840),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=self.reasoning),
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=usage)


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def factory(**kw):
        client = SimpleNamespace(chat=SimpleNamespace(completions=holder["c"]), embeddings=None)
        return client

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=factory))
    return holder


def _llm(**kw):
    from agent_runtime.providers.openai_provider import OpenAILLM

    return OpenAILLM(model="m", api_key="k", **kw)


def test_reasoning_overrun_escalates_reasoning_effort_first(fake):
    fake["c"] = _Completions(completion=300, reasoning=250)
    llm = _llm()
    r = llm.complete("hi", max_tokens=96)
    assert r.metadata["cap_violation"] and r.metadata["governor_action"] == "reasoning_effort=low"
    assert r.metadata["reasoning_tokens"] == 250 and r.metadata["cached_prompt_tokens"] == 3840
    r2 = llm.complete("hi", max_tokens=96)
    assert fake["c"].calls[-1]["reasoning_effort"] == "low"
    assert r2.metadata["governor_action"] == "cap_param=max_completion_tokens"
    llm.complete("hi", max_tokens=96)
    assert "max_completion_tokens" in fake["c"].calls[-1] and "max_tokens" not in fake["c"].calls[-1]


def test_ignored_cap_without_reasoning_switches_cap_param_first(fake):
    fake["c"] = _Completions(completion=300, reasoning=0)
    llm = _llm()
    assert llm.complete("hi", max_tokens=96).metadata["governor_action"] == "cap_param=max_completion_tokens"


def test_governor_can_be_disabled(fake):
    fake["c"] = _Completions(completion=300)
    llm = _llm(completion_governor=False)
    r = llm.complete("hi", max_tokens=96)
    assert r.metadata["cap_violation"] and "governor_action" not in r.metadata
    llm.complete("hi", max_tokens=96)
    assert "max_tokens" in fake["c"].calls[-1]


def test_rejected_max_tokens_costs_one_failed_request_per_session(fake):
    fake["c"] = _Completions(reject={"max_tokens"})
    llm = _llm()
    for _ in range(3):
        assert llm.complete("hi", max_tokens=10).text == "ok"
    # 1 rejected + 3 successful; previously every call paid a rejected attempt (6 total)
    assert len(fake["c"].calls) == 4


def test_unsupported_reasoning_effort_is_dropped_and_remembered(fake):
    fake["c"] = _Completions(reject={"reasoning_effort"})
    llm = _llm(reasoning_effort="low")
    llm.complete("a", max_tokens=10)
    llm.complete("b", max_tokens=10)
    assert len(fake["c"].calls) == 3 and "reasoning_effort" not in fake["c"].calls[-1]


def test_visible_prompt_tokens_reported(fake):
    fake["c"] = _Completions()
    r = _llm().complete("hello world", system="sys")
    assert r.metadata["visible_prompt_tokens"] > 0


def test_fit_recovers_synthetic_overhead():
    from agent_runtime.evaluation.billing_model import fit_billing

    rows = [{"requests": n, "visible": v, "billed": 4000 * n + 1.25 * v} for n, v in [(8, 4000), (8, 700), (4, 400), (1, 300), (2, 900)]]
    f = fit_billing(rows)
    assert abs(f["overhead_per_request"] - 4000) < 1e-6 and abs(f["visible_scale"] - 1.25) < 1e-9
    assert fit_billing(rows[:1]) is None


def test_mock_run_has_no_hidden_overhead():
    from agent_runtime.evaluation.billing_model import fit_billing

    rows = [{"requests": n, "visible": v, "billed": v} for n, v in [(8, 4000), (8, 700), (4, 400)]]
    f = fit_billing(rows)
    assert f["overhead_per_request"] == 0.0 and abs(f["visible_scale"] - 1.0) < 1e-9


def test_archive_fit_and_holdout():
    from agent_runtime.evaluation.billing_model import archive_report

    p = archive_report()
    assert p["fit"]["cells"] == 72
    assert 4300 < p["fit"]["overhead_per_request"] < 4450 and p["fit"]["r2"] > 0.999
    assert p["holdout_request_count"]["max_rel_err"] < 0.01
    assert p["variants"]["SS"]["fixed_share_mean"] > 0.95
    assert p["cap96_cells_with_mean_over_cap"] >= 1


def test_external_variant_table_keeps_baselines_uncoalesced():
    from agent_runtime.config import load_config
    from agent_runtime.evaluation.external_llm_benchmark import _variant_table

    variants, _ = _variant_table(load_config(load_env=False))
    for cid in ("T", "T_memory_pair", "S", "SS", "SS_memory_off", "SS_memory_on", "SS_concise", "FULL"):
        assert variants[cid]["coalesce"] == "off"
    for cid in ("T_coalesce", "T_memory_pair_coalesce", "SS_coalesce", "FULL_coalesce"):
        assert variants[cid]["coalesce"] == "task"
    assert variants["FULL_coalesce"]["max_tokens"] == variants["FULL"]["max_tokens"]


def test_empty_truncation_relaxes_cap(fake, monkeypatch):
    class _Empty(_Completions):
        def create(self, **kw):
            r = super().create(**kw)
            r.choices[0].message.content = ""
            r.choices[0].finish_reason = "length"
            return r

    fake["c"] = _Empty(completion=96, reasoning=96)
    llm = _llm()
    r = llm.complete("hi", max_tokens=96)
    assert r.metadata["truncated_empty"] and r.metadata["governor_action"] == "cap_scale=2"
    llm.complete("hi", max_tokens=96)
    assert fake["c"].calls[-1]["max_tokens"] == 192
    for _ in range(3):
        llm.complete("hi", max_tokens=96)
    assert fake["c"].calls[-1]["max_tokens"] == 384  # bounded at x4
