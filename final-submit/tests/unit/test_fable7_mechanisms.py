"""fable7 reliability tests: sandbox precompile, calibration guard, CJK lexical, LLM checkpointing."""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------- sandbox precompile
@pytest.mark.parametrize("precompile", ["1", "0"])
def test_precompiled_sandbox_is_behaviourally_identical(monkeypatch, precompile):
    from agent_runtime.config import SandboxConfig
    from agent_runtime.sandbox.executor import CodeActExecutor

    monkeypatch.setenv("AGENT_SANDBOX_PRECOMPILE", precompile)
    ex = CodeActExecutor(SandboxConfig(warm_start=True, timeout_sec=5))
    ok = ex.run("import sys\nprint('hi', __name__, __file__)\nprint(sys.argv)\n")
    assert ok.exit_code == 0 and ok.stdout.strip().splitlines() == ["hi __main__ main.py", "['main.py']"]
    syntax = ex.run("def broken(:\n    pass\n")
    assert syntax.exit_code == 1 and "SyntaxError" in syntax.stderr
    boom = ex.run("raise ValueError('x')\n")
    assert boom.exit_code == 1 and "ValueError" in boom.stderr
    code = ex.run("raise SystemExit(3)\n")
    assert code.exit_code == 3
    # The compiled-code cache must never return stale code for different source.
    a = ex.run("print(1)\n")
    b = ex.run("print(2)\n")
    assert (a.stdout.strip(), b.stdout.strip()) == ("1", "2")


def test_precompile_does_not_leak_between_executions():
    from agent_runtime.config import SandboxConfig
    from agent_runtime.sandbox.executor import CodeActExecutor

    ex = CodeActExecutor(SandboxConfig(warm_start=True, timeout_sec=5))
    ex.run("import builtins\nbuiltins.LEAK = 1\nx = 5\n")
    out = ex.run("import builtins\nprint(hasattr(builtins, 'LEAK'), 'x' in globals())\n")
    assert out.stdout.strip() == "False False"


# ---------------------------------------------------------------- calibration guard
def _write_cal(path, *, stable=True, consistent=True, k=1):
    import platform

    from agent_runtime.state_exchange.fd_passing import CALIBRATION_VERSION

    bucket = {"size": 4 << 20, "copy_ms": 97.0, "fd_handoff_ms": 0.6, "materialise_copy_in_ms": 4.0,
              "materialise_in_place_ms": 4.0, "k_copy_in": k, "k_in_place": k, "stable": stable}
    path.write_text(json.dumps({
        "supported": True, "version": CALIBRATION_VERSION, "kernel": platform.uname().release,
        "machine": platform.uname().machine, "buckets": {str(4 << 20): bucket},
        "consistency": {"consistent": consistent}, **{kk: bucket[kk] for kk in ("copy_ms", "fd_handoff_ms",
        "materialise_copy_in_ms", "materialise_in_place_ms", "k_copy_in", "k_in_place")}, "size": 4 << 20,
    }))


@pytest.mark.parametrize("stable,consistent,expect_cal", [(True, True, True), (False, True, False), (True, False, False)])
def test_unstable_or_inconsistent_calibration_is_rejected(monkeypatch, tmp_path, stable, consistent, expect_cal):
    from agent_runtime.state_exchange import fd_passing as fp

    path = tmp_path / "fd_policy.json"
    _write_cal(path, stable=stable, consistent=consistent, k=1)
    monkeypatch.setattr(fp, "calibration_path", lambda: path)
    monkeypatch.setattr(fp, "_CALIBRATION", None)
    monkeypatch.delenv("AGENT_FD_MIN_FANOUT_COPYIN", raising=False)
    monkeypatch.delenv("AGENT_FD_MIN_FANOUT_INPLACE", raising=False)
    pol = fp.policy_thresholds(4 << 20)
    if expect_cal:
        assert pol["source"] == "calibration" and pol["k_copy_in"] == 1
    else:
        assert pol["source"].startswith("default") and pol["k_copy_in"] == fp.DEFAULT_MIN_FANOUT_COPY_IN


def test_calibration_consistency_detects_per_byte_contradiction():
    from agent_runtime.state_exchange.fd_passing import calibration_consistency

    # openEuler VM evidence: 4 MiB copy 97.3 ms vs 16 MiB copy 17.3 ms  (~22x per-MiB spread)
    bad = {str(4 << 20): {"copy_ms": 97.3}, str(16 << 20): {"copy_ms": 17.3}}
    good = {str(4 << 20): {"copy_ms": 2.0}, str(16 << 20): {"copy_ms": 6.0}}
    assert calibration_consistency(bad)["consistent"] is False
    assert calibration_consistency(good)["consistent"] is True


# ---------------------------------------------------------------- CJK lexical embedding
def test_lexical_embedding_no_longer_collapses_chinese():
    import numpy as np

    from agent_runtime.providers.lexical_embedding import LexicalEmbedding

    e = LexicalEmbedding()
    a, b, c = e.embed(["服务启动失败端口被占用", "磁盘写入日志变慢", "服务启动失败端口冲突"])
    assert not np.allclose(a, b)
    assert float(a @ c) > float(a @ b)


def test_bilingual_benchmark_improves_over_legacy_lexical():
    from agent_runtime.evaluation.bilingual_memory_benchmark import run_bilingual_memory_benchmark

    r = run_bilingual_memory_benchmark()["results"]
    assert r["lexical"]["all"]["mrr"] > r["lexical_legacy"]["all"]["mrr"]
    assert r["lexical"]["zh"]["precision_at_1"] >= r["lexical_legacy"]["zh"]["precision_at_1"]


# ---------------------------------------------------------------- external LLM harness
def test_run_plan_balanced_rotation_covers_every_position():
    from agent_runtime.evaluation.external_llm_benchmark import run_plan

    plan = run_plan(["A", "B", "C"], 3, "balanced")
    firsts = [plan[i * 3][1] for i in range(3)]
    assert sorted(firsts) == ["A", "B", "C"]
    assert [c for _, c in run_plan(["A", "B"], 2, "fixed")] == ["A", "B", "A", "B"]


def test_external_llm_checkpoint_resume_and_budget(monkeypatch, tmp_path):
    from agent_runtime.evaluation import external_llm_benchmark as ext

    calls = []

    def fake_once(base_cfg, variant, i, kernel):
        calls.append(i)
        return {"success": True, "total_llm_tokens": 10, "latency_sec": 1.0, "errors": []}

    monkeypatch.setattr(ext, "_run_variant_once", fake_once)
    ckpt = tmp_path / "llm.jsonl"
    first = ext.run_external_llm_benchmark(repeat=2, allow_mock=True, variants=["T", "SS"], checkpoint=ckpt)
    assert first["completeness"]["complete"] and len(calls) == 4
    lines = ckpt.read_text().splitlines()
    assert len(lines) == 5  # header + 4 cells
    again = ext.run_external_llm_benchmark(repeat=2, allow_mock=True, variants=["T", "SS"], checkpoint=ckpt)
    assert len(calls) == 4 and again["completeness"]["resumed_cells"] == 4
    # torn trailing line after a crash is ignored, that cell reruns
    ckpt.write_text("\n".join(lines[:-1]) + '\n{"repeat": 1, "vari')
    ext.run_external_llm_benchmark(repeat=2, allow_mock=True, variants=["T", "SS"], checkpoint=ckpt)
    assert len(calls) == 5
    text = ext.render_external_llm(again)
    assert "Lean Text" in text and "not run" in text  # arms outside the subset render as not run


def test_external_llm_budget_stops_with_partial_report(monkeypatch):
    import time

    from agent_runtime.evaluation import external_llm_benchmark as ext

    def slow(base_cfg, variant, i, kernel):
        time.sleep(0.05)
        return {"success": True, "total_llm_tokens": 1, "latency_sec": 0.05, "errors": []}

    monkeypatch.setattr(ext, "_run_variant_once", slow)
    out = ext.run_external_llm_benchmark(repeat=50, allow_mock=True, variants=["T"], budget_sec=0.2)
    assert out["completeness"]["complete"] is False
    assert 1 <= out["completeness"]["collected_cells"] < 50
    assert "INCOMPLETE" in ext.render_external_llm(out)


def test_external_llm_crashed_cell_is_recorded_not_fatal(monkeypatch):
    from agent_runtime.evaluation import external_llm_benchmark as ext

    def crash(*_a):
        raise TimeoutError("provider stalled")

    monkeypatch.setattr(ext, "_run_variant_once", crash)
    out = ext.run_external_llm_benchmark(repeat=1, allow_mock=True, variants=["T"])
    row = out["raw"]["T"][0]
    assert row["crashed"] and "TimeoutError" in row["errors"][0]["error"]
    assert out["summary"]["T"]["success_rate"] == 0.0


def test_checkpoint_from_other_model_is_refused(monkeypatch, tmp_path):
    from agent_runtime.evaluation import external_llm_benchmark as ext

    ckpt = tmp_path / "llm.jsonl"
    ckpt.write_text(json.dumps({"header": {"llm_model": "someone-else"}}) + "\n")
    with pytest.raises(RuntimeError, match="different provider"):
        ext.run_external_llm_benchmark(repeat=1, allow_mock=True, variants=["T"], checkpoint=ckpt)
