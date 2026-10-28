"""OS-SOTA1: LLM call coalescing (vectored completion) — unit + end-to-end equivalence."""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_runtime.config import load_config
from agent_runtime.providers.mock import GatewayCalibratedLLM, MockLLM
from agent_runtime.runtime.llm_gateway import (
    COALESCE_SYSTEM,
    LLMGateway,
    build_coalesced_prompt,
    resolve_coalesce_mode,
    split_coalesced_answer,
)


class _Recorder:
    model = "rec"

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
        from agent_runtime.providers.base import LLMResponse

        self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
        text = self.reply(prompt) if self.reply else "x"
        return LLMResponse(text=text, prompt_tokens=10, completion_tokens=2, model="rec")


def test_resolve_mode():
    assert resolve_coalesce_mode("auto", "mock") == "off"
    assert resolve_coalesce_mode("auto", "mock-gateway") == "off"
    assert resolve_coalesce_mode("auto", "openai") == "task"
    assert resolve_coalesce_mode("0", "openai") == "off"
    assert resolve_coalesce_mode("task", "mock") == "task"
    with pytest.raises(ValueError):
        resolve_coalesce_mode("bogus", "mock")


def test_split_answers_handles_formats_and_continuations():
    text = "1: alpha\n2) beta\ncontinued\n[3]: gamma\n9: out of range"
    answers, misses = split_coalesced_answer(text, 4)
    assert answers == ["alpha", "beta continued", "gamma", ""]
    assert misses == 1


def test_singleton_is_sent_unchanged():
    rec = _Recorder()
    gw = LLMGateway(rec, mode="task")
    gw.submit("hello", system="sys", temperature=0.0, max_tokens=5)
    out = gw.flush()
    assert len(out) == 1 and rec.calls == [{"prompt": "hello", "system": "sys", "max_tokens": 5}]
    assert gw.stats.direct_singletons == 1 and gw.stats.batches == 0


def test_batch_is_one_request_with_summed_budget():
    rec = _Recorder(reply=lambda p: "1: a\n2: b\n3: c")
    gw = LLMGateway(rec, mode="task", contract_hint="short")
    tickets = [gw.submit(f"q{i}", system=f"role{i}", temperature=0.0, max_tokens=10).metadata["ticket"] for i in range(3)]
    responses = gw.flush()
    assert len(responses) == 1 and len(rec.calls) == 1
    call = rec.calls[0]
    assert call["system"] == COALESCE_SYSTEM
    assert call["max_tokens"] == 3 * 10 + 8 * 3
    assert "### 2 — role1" in call["prompt"] and "Per-item rule: short" in call["prompt"]
    assert [gw.results[t] for t in tickets] == ["a", "b", "c"]
    assert responses[0].metadata["coalesced_items"] == 3
    assert gw.summary()["provider_requests_saved"] == 2


def test_unparseable_batch_is_repaired_item_by_item():
    from agent_runtime.runtime.llm_gateway import reset_batch_windows

    reset_batch_windows()
    rec = _Recorder(reply=lambda p: "free-form text without numbering" if "### " in p else f"direct:{p}")
    gw = LLMGateway(rec, mode="task")
    for i in range(2):
        gw.submit(f"q{i}", system=None, temperature=None, max_tokens=None)
    responses = gw.flush()
    assert gw.stats.parse_misses == 2 and gw.stats.repaired == 2
    # batch of 2 -> window halves to 1 -> both items re-sent individually: nothing is lost
    assert [len(rec.calls), gw.results[1], gw.results[2]] == [3, "direct:q0", "direct:q1"]
    assert [r.metadata["repair_items"] for r in responses] == [0, 1, 1]
    assert sum(r.metadata["coalesced_items"] for r in responses) == 2  # logical requests counted once


def test_max_batch_chunks_and_discard():
    from agent_runtime.runtime.llm_gateway import reset_batch_windows

    reset_batch_windows()
    rec = _Recorder(reply=lambda p: "\n".join(f"{i}: z" for i in range(1, 5)))
    gw = LLMGateway(rec, mode="task", max_batch=2)
    for i in range(5):
        gw.submit(f"q{i}", system=None, temperature=None, max_tokens=None)
    assert len(gw.flush()) == 3
    gw.submit("late", system=None, temperature=None, max_tokens=None)
    assert gw.discard() == 1 and gw.pending == 0


def test_mock_llm_answers_vectored_request_line_per_item():
    llm = MockLLM()
    prompt = build_coalesced_prompt(
        [type("P", (), {"prompt": f"p{i}", "system": "s"})() for i in range(4)], ""
    )
    resp = llm.complete(prompt, system=COALESCE_SYSTEM, max_tokens=200)
    answers, misses = split_coalesced_answer(resp.text, 4)
    assert misses == 0 and all(answers)


def test_gateway_projection_adds_calibrated_overhead():
    inner = MockLLM()
    proj = GatewayCalibratedLLM(inner)
    r0 = inner.complete("abc def", system="s")
    r1 = proj.complete("abc def", system="s")
    assert r1.prompt_tokens == 4382 + round(1.311 * r0.prompt_tokens)
    assert r1.metadata["billing_model"] == "projection"
    assert r1.metadata["runtime_visible_prompt_tokens"] == r0.prompt_tokens


def _run(coalesce: str, mode: str = "structured", use_state: bool = True, provider: str = "mock"):
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.runtime.builder import build_context, build_default_agents
    from agent_runtime.runtime.orchestrator import Orchestrator
    from agent_runtime.protocol.routine import reset_routine_registry

    reset_routine_registry()  # identical cold start for both arms (routine upload cost)
    cfg = load_config(load_env=False).with_overrides(llm_coalesce=coalesce, llm_context_mode="inbound")
    if provider != "mock":
        from dataclasses import replace

        cfg = cfg.with_overrides(llm=replace(cfg.llm, provider=provider))
    out = []
    for task in _self_contained_tasks():
        ctx = build_context(cfg, mode, task_id=task.task_id, use_state_exchange=use_state, use_sandbox=True)
        res = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
        out.append((res, ctx))
    return out


@pytest.mark.parametrize("mode,use_state", [("text", False), ("structured", True)])
def test_coalescing_preserves_task_results_and_cuts_provider_requests(mode, use_state):
    off = _run("off", mode, use_state)
    on = _run("task", mode, use_state)
    for (r0, c0), (r1, c1) in zip(off, on):
        assert r0.success and r1.success
        assert c0.blackboard.get("answer") == c1.blackboard.get("answer")
        s0, s1 = r0.metrics, r1.metrics
        assert s0.llm_calls == s0.llm_logical_requests == 4
        assert s1.llm_calls == 1 and s1.llm_logical_requests == 4
        assert s1.llm_coalesced_batches == 1 and s1.llm_coalesce_parse_misses == 0
        # communication-plane accounting is untouched by LLM batching
        assert s0.message_count == s1.message_count
        assert abs(s0.text_tokens - s1.text_tokens) <= max(2, 0.01 * s0.text_tokens)  # clock-delta jitter


def test_projected_bill_drops_with_coalescing():
    off = _run("off", provider="mock-gateway")
    on = _run("task", provider="mock-gateway")
    p_off = sum(r.metrics.llm_prompt_tokens for r, _ in off)
    p_on = sum(r.metrics.llm_prompt_tokens for r, _ in on)
    assert p_on < 0.35 * p_off


def test_crash_path_discards_queue():
    from agent_runtime.runtime.llm_gateway import LLMGateway as GW

    gw = GW(MockLLM(), mode="task")
    gw.submit("x", system=None, temperature=None, max_tokens=None)
    assert gw.discard() == 1


def test_group_commit_one_request_per_session_and_same_answers():
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.observability.metrics import MetricsCollector
    from agent_runtime.protocol.routine import reset_routine_registry
    from agent_runtime.providers.factory import build_llm
    from agent_runtime.runtime.builder import build_context, build_default_agents
    from agent_runtime.runtime.llm_gateway import flush_session
    from agent_runtime.runtime.orchestrator import Orchestrator

    reset_routine_registry()
    cfg = load_config(load_env=False).with_overrides(llm_coalesce="session", llm_context_mode="inbound")
    gw = LLMGateway(build_llm(cfg), mode="session")
    answers, per_task_calls = [], 0
    for task in _self_contained_tasks():
        ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True, use_sandbox=True, llm_gateway=gw)
        res = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
        assert res.success
        per_task_calls += res.metrics.llm_calls
        answers.append(ctx.blackboard.get("answer"))
    assert per_task_calls == 0 and gw.pending == 16
    session = MetricsCollector()
    assert flush_session(gw, session) == 1
    assert session.snapshot.llm_calls == 1 and session.snapshot.llm_logical_requests == 16
    assert session.snapshot.llm_coalesce_parse_misses == 0
    baseline = [c.blackboard.get("answer") for _, c in _run("off")]
    assert answers == baseline


def test_session_mode_without_shared_gateway_falls_back_to_task():
    for res, _ctx in _run("session"):
        assert res.metrics.llm_calls == 1 and res.metrics.llm_logical_requests == 4


def test_coalesce_benchmark_smoke():
    from agent_runtime.evaluation.coalesce_benchmark import render_coalesce_benchmark, run_coalesce_benchmark

    p = run_coalesce_benchmark(repeat=1, rounds=2)
    c = p["checks"]
    assert c["answers_identical_T"] and c["answers_identical_SS"] and c["answers_identical_SSM"]
    assert c["answers_identical_group"] and c["logical_requests_preserved"] and c["parse_misses"] == 0
    s = p["summary"]
    assert s["SS+G"]["llm_calls_mean"] == 1 and s["SS"]["llm_calls_mean"] == 16
    assert p["derived"]["SS+G_vs_T_billed"] > 0.85
    assert "Group commit" in render_coalesce_benchmark(p)


def test_grade_text_label_rules():
    from agent_runtime.runtime.context import grade_text

    g = {"kind": "label", "expected": "port_conflict"}
    assert grade_text(g, "port_conflict") and grade_text(g, "**Port_Conflict**.") and not grade_text(g, "permission_denied")
    assert not grade_text(g, "")
    with pytest.raises(ValueError):
        grade_text({"kind": "?"}, "x")


def test_deferred_judge_is_graded_at_flush():
    from agent_runtime.observability.metrics import MetricsCollector

    rec = _Recorder(reply=lambda p: "1: something\n2: port_conflict")
    gw = LLMGateway(rec, mode="task")
    sink = MetricsCollector()
    gw.submit("advisory", system=None, temperature=None, max_tokens=None)
    gw.submit("judge", system=None, temperature=None, max_tokens=None,
              grade={"kind": "label", "expected": "port_conflict"}, sink=sink)
    assert sink.snapshot.llm_judgements == 0
    gw.flush()
    assert sink.snapshot.llm_judgements == 1 and sink.snapshot.llm_judgements_correct == 1
    assert gw.stats.graded == 1


class _VisibilityOracle:
    """Judge model that can only use what is *visible in the inbound wire*: it answers the one
    candidate label that literally appears before the task line (else UNSEEN/AMBIG).  If a
    protocol hid the decision behind a reference, the oracle would fail."""
    model = "oracle"

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
        from agent_runtime.providers.base import LLMResponse
        import re

        def ans(item):
            m = re.search(r"from: ([a-z_, ]+)\.", item)
            if not m:
                return "ok"
            labels = [x.strip() for x in m.group(1).split(",")]
            hits = [x for x in labels if x in item.split("Task:")[0]]
            return hits[0] if len(hits) == 1 else "UNSEEN"
        if system == COALESCE_SYSTEM:
            items = prompt.split("### ")[1:]
            text = "\n".join(f"{k}: {ans(it)}" for k, it in enumerate(items, 1))
        else:
            text = ans(prompt)
        return LLMResponse(text=text, prompt_tokens=1, completion_tokens=1, model="oracle")


@pytest.mark.parametrize("mode,use_state,coalesce,view", [
    ("text", False, "off", "wire"), ("structured", False, "off", "wire"), ("structured", True, "off", "wire"),
    ("text", False, "task", "wire"), ("structured", True, "task", "wire"),
    ("text", False, "task", "semantic"), ("structured", True, "task", "semantic"), ("structured", True, "off", "semantic"),
])
def test_decision_label_visible_in_inbound_wire_all_modes(mode, use_state, coalesce, view, monkeypatch):
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.runtime import builder as b
    from agent_runtime.runtime.builder import build_context, build_default_agents
    from agent_runtime.runtime.orchestrator import Orchestrator

    monkeypatch.setenv("AGENT_LLM_JUDGE", "1")
    monkeypatch.setattr(b, "build_llm", lambda cfg: _VisibilityOracle())
    cfg = load_config(load_env=False).with_overrides(llm_coalesce=coalesce, llm_context_mode="inbound", text_style="lean",
                                                     llm_judge=True, llm_view=view)
    judged = correct = 0
    for task in _self_contained_tasks():
        ctx = build_context(cfg, mode, task_id=task.task_id, use_state_exchange=use_state, use_sandbox=True)
        res = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
        assert res.success
        judged += res.metrics.llm_judgements
        correct += res.metrics.llm_judgements_correct
        assert res.metrics.llm_calls == (1 if coalesce == "task" else 5)
    assert (judged, correct) == (4, 4)


class _CapacityLLM:
    """A model that only answers the first ``cap`` items of any batch (drops the rest)."""
    model = "capacity-model"

    def __init__(self, cap):
        self.cap, self.batch_sizes = cap, []

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
        from agent_runtime.providers.base import LLMResponse

        if system == COALESCE_SYSTEM:
            n = len(prompt.split("### ")) - 1
            self.batch_sizes.append(n)
            text = "\n".join(f"{k}: ans{k}" for k in range(1, min(n, self.cap) + 1))
        else:
            self.batch_sizes.append(1)
            text = "single"
        return LLMResponse(text=text, prompt_tokens=1, completion_tokens=1, model=self.model)


def test_aimd_window_converges_below_model_capacity_and_loses_nothing():
    from agent_runtime.runtime.llm_gateway import _window_get, reset_batch_windows

    reset_batch_windows()
    llm = _CapacityLLM(cap=5)
    total_items = 0
    for _round in range(6):
        gw = LLMGateway(llm, mode="session", max_batch=16)
        for i in range(16):
            gw.submit(f"q{i}", system=None, temperature=None, max_tokens=None)
        gw.flush()
        total_items += 16
        assert all(gw.results[t] for t in range(1, 17))  # every logical request answered
    assert 4 <= _window_get("capacity-model", 16) <= 6
    # after learning, the window tracks the model's real capacity (5); probing is damped
    assert max(llm.batch_sizes[-8:]) <= 6


def test_non_adaptive_gateway_keeps_fixed_window():
    from agent_runtime.runtime.llm_gateway import reset_batch_windows

    reset_batch_windows()
    llm = _CapacityLLM(cap=100)
    gw = LLMGateway(llm, mode="task", max_batch=4, adaptive=False)
    for i in range(8):
        gw.submit(f"q{i}", system=None, temperature=None, max_tokens=None)
    gw.flush()
    assert llm.batch_sizes == [4, 4]


def _ctx_with_gateway(llm, piggyback=True):
    from agent_runtime.runtime.builder import build_context
    from agent_runtime.runtime import builder as b

    cfg = load_config(load_env=False).with_overrides(llm_coalesce="task", llm_piggyback=piggyback)
    orig = b.build_llm
    b.build_llm = lambda c: llm
    try:
        return build_context(cfg, "structured", task_id="pb", use_state_exchange=False, use_sandbox=False)
    finally:
        b.build_llm = orig


def test_piggyback_critical_call_carries_queued_items():
    rec = _Recorder(reply=lambda p: "1: adv-a\n2: adv-b\n3: critical-answer")
    ctx = _ctx_with_gateway(rec)
    ctx.call_llm("advisory a", deferrable=True)
    ctx.call_llm("advisory b", deferrable=True)
    r = ctx.call_llm("need this now")
    assert r.text == "critical-answer" and r.metadata["piggybacked"]
    assert len(rec.calls) == 1 and ctx.llm_gateway.pending == 0
    s = ctx.metrics.snapshot
    assert (s.llm_calls, s.llm_logical_requests, s.llm_piggybacked_calls) == (1, 3, 1)
    assert ctx.flush_llm() == 0  # nothing left for the barrier


def test_piggyback_off_or_empty_queue_uses_direct_call():
    rec = _Recorder(reply=lambda p: "direct")
    ctx = _ctx_with_gateway(rec)
    assert ctx.call_llm("now").text == "direct" and ctx.metrics.snapshot.llm_piggybacked_calls == 0
    ctx2 = _ctx_with_gateway(_Recorder(reply=lambda p: "direct"), piggyback=False)
    ctx2.call_llm("advisory", deferrable=True)
    assert ctx2.call_llm("now").text == "direct" and ctx2.llm_gateway.pending == 1


def test_long_session_smoke_projection(monkeypatch):
    from agent_runtime.evaluation.long_session_benchmark import render_long_session, run_long_session

    monkeypatch.setenv("AGENT_LLM_PROVIDER", "mock-gateway")
    p = run_long_session(rounds=2, repeat=1, allow_mock=True, arms=["T", "SS+G", "FULL+G"])
    s = p["summary"]
    assert all(x["success_rate"] == 1.0 for x in s.values())
    assert p["checks"]["answers_identical_SS+G"] and p["checks"]["no_unanswered_logical_requests"]
    assert s["T"]["llm_calls_mean"] == 32 and s["SS+G"]["llm_calls_mean"] == 2  # 32 items, init window 16
    assert s["FULL+G"]["llm_calls_mean"] == 1 and s["FULL+G"]["billed_saving_vs_T"] > 0.9
    assert "PROJECTION" in render_long_session(p)


def test_long_session_refuses_mock_without_flag(monkeypatch):
    from agent_runtime.evaluation.long_session_benchmark import run_long_session

    monkeypatch.setenv("AGENT_LLM_PROVIDER", "mock")
    with pytest.raises(SystemExit):
        run_long_session(rounds=1, allow_mock=False)


def test_related_variants_use_memory_on_non_identical_follow_up():
    from agent_runtime.evaluation.external_llm_benchmark import _related_tasks, _variant_table

    text_tasks, linked = _related_tasks(False), _related_tasks(True)
    assert text_tasks[1].task_id != text_tasks[0].task_id and text_tasks[1].description != text_tasks[0].description
    assert len(str(linked[1].payload)) < len(str(text_tasks[1].payload))  # procedure omitted -> needs memory
    v, _ = _variant_table(load_config(load_env=False))
    assert v["FULL_related"]["use_memory"] and v["FULL_related_group"]["coalesce"] == "session"
    assert v["T_related"]["coalesce"] == "off"


def test_judge_uses_eight_way_label_space_by_default():
    from agent_runtime.agents.reviewer import JUDGE_GLOBAL_LABELS

    assert load_config(load_env=False).llm_judge_labels == "global" and len(JUDGE_GLOBAL_LABELS) == 8


class _SlowLLM:
    model = "slow-model"

    def __init__(self, delay=0.15):
        self.delay = delay

    def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
        import time as _t

        from agent_runtime.providers.base import LLMResponse

        _t.sleep(self.delay)
        n = len(prompt.split("### ")) - 1 if system == COALESCE_SYSTEM else 1
        text = "\n".join(f"{k}: a{k}-{abs(hash(prompt)) % 97}" for k in range(1, n + 1)) if n > 1 else "solo"
        return LLMResponse(text=text, prompt_tokens=1, completion_tokens=1, model=self.model,
                           metadata={"projected_latency_sec": 7.0})


def test_parallel_waves_cut_wall_time_and_keep_results_identical():
    import time as _t

    from agent_runtime.runtime.llm_gateway import reset_batch_windows

    outs = {}
    for par in (1, 4):
        reset_batch_windows()
        gw = LLMGateway(_SlowLLM(), mode="session", max_batch=4, parallelism=par)
        for i in range(16):
            gw.submit(f"q{i}", system=None, temperature=None, max_tokens=None)
        t0 = _t.perf_counter()
        responses = gw.flush()
        outs[par] = (_t.perf_counter() - t0, dict(gw.results), len(responses), gw.summary())
    assert outs[1][1] == outs[4][1] and outs[1][2] == outs[4][2] == 4
    assert outs[4][0] < 0.6 * outs[1][0]
    assert outs[1][3]["waves"] == 4 and outs[4][3]["waves"] == 1
    assert outs[1][3]["projected_wall_sec"] == 28.0 and outs[4][3]["projected_wall_sec"] == 7.0


def test_semantic_view_strips_plumbing_only():
    from agent_runtime.protocol.llm_view import semantic_view

    s = '{"id":"~r7Oj","tr":"~ZV","st":3,"a":6,"ip":{"value":"port_conflict","workflow_state_ref":"~vkU","plan_ref":"^0"},"dt":348}'
    assert semantic_view(s) == '{"ip":{"value":"port_conflict"}}'
    t = "from=planner | to=retriever | action=retrieve | trace=trace_1 | timestamp=1.5 | seq=3 | input={\"a\":1}"
    assert semantic_view(t) == 'from=planner | to=retriever | action=retrieve | input={"a":1}'
    assert semantic_view("free text") == "free text" and semantic_view("") == ""
    assert semantic_view('{"id":"~x"}') == '{"id":"~x"}'  # never produce an empty view


def test_semantic_view_variants_have_text_twin():
    from agent_runtime.evaluation.external_llm_benchmark import _variant_table

    v, _ = _variant_table(load_config(load_env=False))
    assert v["T_group_sv"]["llm_view"] == v["SS_group_sv"]["llm_view"] == "semantic"
    assert "llm_view" not in v["SS_group"]


def test_slow_start_doubles_until_first_miss_then_damped(monkeypatch):
    from agent_runtime.runtime.llm_gateway import _SLOW_START, _window_get, reset_batch_windows

    reset_batch_windows()
    llm = _CapacityLLM(cap=40)
    sizes = []
    for _ in range(4):
        gw = LLMGateway(llm, mode="session", max_batch=64, init_window=16, parallelism=1)
        for i in range(64):
            gw.submit(f"q{i}", system=None, temperature=None, max_tokens=None)
        n0 = len(llm.batch_sizes)
        gw.flush()
        sizes.append(llm.batch_sizes[n0:])
        assert all(gw.results[t] for t in range(1, 65))
    # 16 -> 32 -> 64 (miss: 40 answered) -> window 40, slow start off
    assert sizes[0][:3] == [16, 32, 16] and 64 in sizes[1]
    assert _window_get("capacity-model", 16) in (40, 41) and _SLOW_START["capacity-model"] is False


def test_window_store_persists_learned_capacity(tmp_path, monkeypatch):
    from agent_runtime.runtime import llm_gateway as g

    store = tmp_path / "w.json"
    monkeypatch.setenv("AGENT_LLM_WINDOW_STORE", str(store))
    g.reset_batch_windows()
    gw = LLMGateway(_CapacityLLM(cap=5), mode="session", max_batch=64, init_window=16)
    for i in range(16):
        gw.submit(f"q{i}", system=None, temperature=None, max_tokens=None)
    gw.flush()
    assert json.loads(store.read_text())["capacity-model"]["window"] == 5
    g.reset_batch_windows()  # new process
    gw2 = LLMGateway(_CapacityLLM(cap=5), mode="session", max_batch=64, init_window=16)
    assert gw2.summary()["window"] == 5
