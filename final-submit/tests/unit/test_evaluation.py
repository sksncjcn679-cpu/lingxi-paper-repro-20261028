"""评测框架单元测试（benchmark smoke + 可追溯产物）。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from agent_runtime.evaluation.runner import run_suite, write_results
from agent_runtime.evaluation.runner import _platform_class
from agent_runtime.evaluation.stats import holm_bonferroni, paired_analysis, summarize


def test_summarize_basic():
    s = summarize([1.0, 2.0, 3.0, 4.0])
    assert s["mean"] == 2.5
    assert s["n"] == 4
    assert s["p50"] == 2.5


def test_paired_statistics_and_holm_correction():
    analysis = paired_analysis([10.0, 20.0, 30.0], [8.0, 18.0, 27.0])
    assert analysis["n"] == 3
    assert analysis["mean_difference"] == -7 / 3
    assert analysis["cohens_dz"] < 0
    assert analysis["ci95"]["low"] < analysis["mean_difference"] < analysis["ci95"]["high"]
    assert 0.0 <= analysis["p_value_normal_approx"] <= 1.0
    assert 0.0 <= analysis["power_normal_approx"] <= 1.0

    adjusted = holm_bonferroni({"small": 0.01, "medium": 0.04, "large": 0.2})
    assert adjusted == {"small": 0.03, "medium": 0.08, "large": 0.2}


def test_platform_class_distinguishes_windows_wsl_and_linux(monkeypatch):
    import platform
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    assert _platform_class() == "windows"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "release", lambda: "6.18.33.2-microsoft-standard-WSL2")
    assert _platform_class() == "wsl"
    monkeypatch.setattr(platform, "release", lambda: "6.6.0-openEuler")
    assert _platform_class() == "linux"


def test_semantic_judge_records_correct_and_incorrect_labels(monkeypatch):
    from agent_runtime.agents.reviewer import ReviewerSummarizerAgent
    from agent_runtime.observability.metrics import MetricsCollector
    from agent_runtime.providers.base import LLMResponse
    from agent_runtime.scenarios.linux_service import make_l1

    from agent_runtime.runtime.context import apply_grade

    task = make_l1()
    metrics = MetricsCollector()

    def _fake(answer):
        # OS-SOTA1.1 contract: the judge passes a declarative ``grade``; the runtime grades
        # the text when it arrives (directly here, or at the coalescing flush).
        def call_llm(prompt, system=None, deferrable=False, grade=None):
            resp = LLMResponse(answer, 3, 1, "mock")
            if grade:
                apply_grade(grade, resp.text, metrics)
            return resp
        return call_llm

    ctx = SimpleNamespace(metrics=metrics, call_llm=_fake("port_conflict"))
    monkeypatch.setenv("AGENT_LLM_JUDGE", "1")
    ReviewerSummarizerAgent._judge_inbound_message(task, ctx)
    assert metrics.snapshot.llm_judgements == 1
    assert metrics.snapshot.llm_judgements_correct == 1

    ctx.call_llm = _fake("permission_denied")
    ReviewerSummarizerAgent._judge_inbound_message(task, ctx)
    assert metrics.snapshot.llm_judgements == 2
    assert metrics.snapshot.llm_judgements_correct == 1


def test_main_suite_smoke():
    payload = run_suite(suite="main", repeat=2, seed=1)
    results = payload["results"]
    assert payload["baseline_id"] == "A"
    assert payload["env"]["communication_tokenizer"] == "heuristic"
    assert payload["env"]["tokenizer_backend_requested"] == "heuristic"
    assert set(results.keys()) == {"A", "B", "C", "D"}
    # 全部配置成功率 100%
    for r in results.values():
        assert r["derived"]["success_rate"] == 1.0
    # 结构化 + 状态相对文本基线显著节省 token
    assert results["C"]["derived"]["token_saving_vs_baseline"] > 0.3
    assert results["D"]["derived"]["token_saving_vs_baseline"] > 0.3
    # 文本基线节省为 0（基准自身）
    assert results["A"]["derived"]["token_saving_vs_baseline"] == 0.0
    # 保留旧字段兼容已有结果消费代码
    assert results["C"]["derived"]["token_saving_vs_A"] == results["C"]["derived"]["token_saving_vs_baseline"]


def test_full_system_memory_reuse():
    payload = run_suite(suite="main", repeat=2, seed=7)
    d = payload["results"]["D"]["summary"]
    # 全系统在两次相同任务序列中产生记忆复用
    assert d["memory_used"]["mean"] > 0
    assert d["memory_effective"]["mean"] > 0


def test_memory_transfer_suite_is_causal():
    payload = run_suite(suite="memory", repeat=1, seed=11)
    results = payload["results"]
    assert payload["baseline_id"] == "M0"
    # A2/B2 都故意不提供策略：无共享记忆不能完成完整序列。
    assert results["M0"]["derived"]["success_rate"] == 0.0
    # 同一任务序列仅打开共享记忆后完整成功，并产生 2 次有效迁移。
    assert results["M1"]["derived"]["success_rate"] == 1.0
    assert results["M1"]["summary"]["memory_used"]["mean"] >= 2
    assert results["M1"]["summary"]["memory_effective"]["mean"] >= 2
    assert results["M1"]["summary"]["memory_harmful"]["mean"] == 0
    # M0 会早停、M1 会完成；两者的时延/token/工具量不能直接当效率对比。
    assert results["M1"]["derived"]["success_delta_vs_baseline"] == 1.0
    assert results["M1"]["derived"]["efficiency_comparable"] is False
    assert results["M1"]["derived"]["latency_improvement_vs_baseline"] is None
    assert results["M1"]["derived"]["tool_call_reduction_vs_baseline"] is None


def test_written_results_have_provenance_and_valid_manifest(tmp_path: Path):
    payload = run_suite(suite="main", repeat=1, seed=3)
    out = write_results(payload, root=str(tmp_path))
    assert (out / "metadata.json").is_file()
    assert (out / "MANIFEST.sha256").is_file()
    assert payload["env"]["source_tree_sha256"]
    assert payload["env"]["packages"]["numpy"] != "not-installed"

    for line in (out / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        actual = hashlib.sha256((out / rel).read_bytes()).hexdigest()
        assert actual == digest


def test_write_results_never_reuses_directory(tmp_path: Path):
    payload = run_suite(suite="main", repeat=1, seed=9)
    first = write_results(payload, root=str(tmp_path))
    second = write_results(payload, root=str(tmp_path))
    assert first != second
    assert first.is_dir() and second.is_dir()
    assert (first / "MANIFEST.sha256").is_file()
    assert (second / "MANIFEST.sha256").is_file()


def test_state_transport_suite_separates_dedup_and_precision_gains():
    payload = run_suite(suite="state", repeat=1, seed=13)
    results = payload["results"]
    assert payload["baseline_id"] == "S0"
    assert set(results) == {"S0", "S1", "S2", "S3"}
    assert all(r["derived"]["success_rate"] == 1.0 for r in results.values())

    s0 = results["S0"]["summary"]
    s1 = results["S1"]["summary"]
    s2 = results["S2"]["summary"]
    s3 = results["S3"]["summary"]

    # S1 only enables content-addressed dedup, so the repeated state materialization halves.
    assert s1["state_bytes"]["mean"] < s0["state_bytes"]["mean"]
    assert s1["state_dedup_hits"]["mean"] > 0
    # S2 additionally stores embedding payload as float16, further reducing bytes.
    assert s2["state_bytes"]["mean"] < s1["state_bytes"]["mean"]
    # S3 uses int8 plus an actually materialized float32 scale; it must beat float16 bytes
    # without changing task success. Fidelity is independently guarded in test_state_exchange.
    assert s3["state_bytes"]["mean"] < s2["state_bytes"]["mean"]
    # Logical payload is kept fixed across the ablation: only transport/materialization changes.
    assert s0["state_logical_bytes"]["mean"] == s1["state_logical_bytes"]["mean"]
    assert s1["state_logical_bytes"]["mean"] == s2["state_logical_bytes"]["mean"]
    assert s2["state_logical_bytes"]["mean"] == s3["state_logical_bytes"]["mean"]
    # v0.6.1 also routes versioned collaboration state through the real pipeline.  Those
    # control-state bytes are independent of embedding precision, so total-state savings are
    # deliberately more conservative than the old plan-only figure.
    assert results["S2"]["derived"]["state_bytes_reduction_vs_baseline"] > 0.50
    # Isolating the numerical plan-state payload still shows the expected >70% gain.
    plan_s0 = s0["state_bytes"]["mean"] - s0["versioned_state_bytes"]["mean"]
    plan_s2 = s2["state_bytes"]["mean"] - s2["versioned_state_bytes"]["mean"]
    assert 1 - plan_s2 / plan_s0 > 0.70
    assert results["S3"]["derived"]["state_bytes_reduction_vs_baseline"] > 0.70
    plan_s3 = s3["state_bytes"]["mean"] - s3["versioned_state_bytes"]["mean"]
    assert 1 - plan_s3 / plan_s0 > 0.85


def test_memory_guard_suite_quantifies_negative_transfer_prevention():
    payload = run_suite(suite="memory_guard", repeat=1, seed=23)
    results = payload["results"]
    assert set(results) == {"G0", "G1"}

    g0 = results["G0"]["summary"]
    g1 = results["G1"]["summary"]
    # 同一不兼容候选在无门控时被误用并造成 harmful hit。
    assert g0["memory_incompatible"]["mean"] >= 1
    assert g0["memory_rejected"]["mean"] == 0
    assert g0["memory_used"]["mean"] >= 1
    assert g0["memory_harmful"]["mean"] >= 1
    # 门控启用后同一候选被拒绝，不再造成负迁移。
    assert g1["memory_incompatible"]["mean"] >= 1
    assert g1["memory_rejected"]["mean"] >= 1
    assert g1["memory_used"]["mean"] == 0
    assert g1["memory_harmful"]["mean"] == 0
    # 两者执行路径不同：门控后提前拒绝，不能把少跑一次错误 sandbox 当效率提升。
    assert results["G1"]["derived"]["efficiency_comparable"] is False
    assert results["G1"]["derived"]["latency_improvement_vs_baseline"] is None
