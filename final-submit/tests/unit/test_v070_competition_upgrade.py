from __future__ import annotations

import pytest

from agent_runtime.evaluation.external_llm_benchmark import run_external_llm_benchmark
from agent_runtime.evaluation.competition_stress import run_competition_stress, render_competition_stress
from agent_runtime.observability.metrics import MetricsCollector


def test_external_llm_benchmark_refuses_mock_by_default(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "mock")
    with pytest.raises(RuntimeError, match="requires AGENT_LLM_PROVIDER"):
        run_external_llm_benchmark(repeat=1)


def test_metrics_record_real_state_transport() -> None:
    metrics = MetricsCollector()
    metrics.record_state_transfer(
        100,
        logical_bytes=400,
        precision="int8",
        transport="shm",
    )
    snap = metrics.snapshot
    assert snap.state_int8_transfers == 1
    assert snap.state_shm_transfers == 1
    assert snap.state_inproc_transfers == 0
    assert snap.state_bytes == 100
    assert snap.state_logical_bytes == 400


def test_external_llm_harness_smoke_with_explicit_mock_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "mock")
    payload = run_external_llm_benchmark(repeat=1, allow_mock=True)
    assert payload["provider"]["llm_provider"] == "mock"
    assert payload["summary"]["T"]["success_rate"] == 1.0
    assert payload["summary"]["SS"]["success_rate"] == 1.0
    assert payload["summary"]["SS"]["strict_transport_saving_vs_text"] > 0.5


def test_competition_stress_aggregates_distributions_without_live_runtime(monkeypatch) -> None:
    # Unit-test distribution/accounting logic without adding 120 live task runs to every pytest.
    import agent_runtime.evaluation.competition_stress as mod

    def fake_run_once(cfg, _factory, seed):
        base = {
            "message_count": 36.0,
            "text_tokens": 1000.0,
            "wire_bytes": 4000.0,
            "ipc_frames": 36.0,
            "ipc_framed_bytes": 4144.0,
            "ipc_ack_bytes": 180.0,
            "state_bytes": 0.0,
            "state_logical_bytes": 0.0,
            "state_shm_transfers": 0.0,
            "state_int8_transfers": 0.0,
            "latency_sec": 0.1,
            "ipc_roundtrip_sec": 0.01,
            "task_success": 1.0,
        }
        if cfg.id == "S":
            base.update(text_tokens=800.0, wire_bytes=2800.0, ipc_framed_bytes=2944.0)
        elif cfg.id == "SS":
            base.update(
                text_tokens=200.0,
                wire_bytes=500.0,
                ipc_framed_bytes=644.0,
                state_bytes=800.0,
                state_logical_bytes=3200.0,
                state_shm_transfers=8.0,
                state_int8_transfers=8.0,
            )
        return base

    monkeypatch.setattr(mod, "run_once", fake_run_once)
    payload = run_competition_stress(rounds=10, seed=1)
    assert payload["summary"]["T"]["success_rounds"] == 10
    assert payload["summary"]["SS"]["saving_vs_text"]["strict_transport_bytes"]["mean"] > 0.6
    assert payload["summary"]["SS"]["real_ipc_coverage"]["mean"] == 1.0
    report = render_competition_stress(payload)
    assert "10 rounds" in report
    assert "State / OS path" in report


def test_competition_stress_rejects_less_than_ten_rounds() -> None:
    with pytest.raises(ValueError, match="rounds >= 10"):
        run_competition_stress(rounds=9)


def test_new_benchmark_writers_and_contest_renderer(tmp_path, monkeypatch) -> None:
    import agent_runtime.evaluation.competition_stress as stress_mod
    import agent_runtime.evaluation.external_llm_benchmark as ext_mod
    from agent_runtime.evaluation.contest_workload import render_contest_workload, write_contest_workload

    def fake_run_once(cfg, _factory, seed):
        row = {
            "message_count": 36.0,
            "text_tokens": 1000.0,
            "wire_bytes": 4000.0,
            "ipc_frames": 36.0,
            "ipc_framed_bytes": 4144.0,
            "ipc_ack_bytes": 180.0,
            "state_bytes": 0.0,
            "state_logical_bytes": 0.0,
            "state_shm_transfers": 0.0,
            "state_int8_transfers": 0.0,
            "latency_sec": 0.1,
            "ipc_roundtrip_sec": 0.01,
            "task_success": 1.0,
        }
        if cfg.id == "S":
            row.update(text_tokens=800.0, wire_bytes=2800.0, ipc_framed_bytes=2944.0)
        elif cfg.id == "SS":
            row.update(
                text_tokens=200.0,
                wire_bytes=500.0,
                ipc_framed_bytes=644.0,
                state_bytes=800.0,
                state_logical_bytes=3200.0,
                state_shm_transfers=8.0,
                state_int8_transfers=8.0,
            )
        return row

    monkeypatch.setattr(stress_mod, "run_once", fake_run_once)
    stress_payload = stress_mod.run_competition_stress(rounds=10, seed=2)
    stress_dir = stress_mod.write_competition_stress(stress_payload, tmp_path / "stress")
    assert (stress_dir / "summary.json").exists()
    assert (stress_dir / "MANIFEST.sha256").exists()

    monkeypatch.setenv("AGENT_LLM_PROVIDER", "mock")
    ext_payload = ext_mod.run_external_llm_benchmark(repeat=1, allow_mock=True)
    ext_dir = ext_mod.write_external_llm_results(ext_payload, tmp_path / "external")
    assert (ext_dir / "report.md").exists()

    # Exercise the rich contest renderer/writer without another expensive live workload.
    comm_base = {
        "task_success": 1.0,
        "message_count": 36.0,
        "text_tokens": 1000.0,
        "wire_bytes": 4000.0,
        "control_physical_bytes": 4200.0,
        "state_bytes": 0.0,
        "transport_bytes": 4200.0,
        "token_delta_vs_text": 0.0,
        "control_physical_delta_vs_text": 0.0,
        "transport_delta_vs_text": 0.0,
    }
    comm_s = dict(comm_base, text_tokens=800.0, wire_bytes=2800.0, control_physical_bytes=3000.0, transport_bytes=3000.0, token_delta_vs_text=0.2, control_physical_delta_vs_text=0.286, transport_delta_vs_text=0.286)
    comm_ss = dict(
        comm_base,
        text_tokens=200.0,
        wire_bytes=500.0,
        control_physical_bytes=700.0,
        state_bytes=800.0,
        state_logical_bytes=3200.0,
        transport_bytes=1500.0,
        token_delta_vs_text=0.8,
        control_physical_delta_vs_text=0.833,
        transport_delta_vs_text=0.643,
        state_physical_saving_vs_logical=0.75,
        state_int8_transfers=8.0,
        state_float16_transfers=0.0,
        state_float32_transfers=0.0,
        state_shm_transfers=8.0,
        ipc_frames=36.0,
        ipc_framed_bytes=520.0,
        ipc_ack_bytes=180.0,
        versioned_state_snapshots=4.0,
        versioned_state_delta_updates=12.0,
        versioned_delta_ratio=0.75,
        placement_reference=8.0,
        placement_decisions=8.0,
        placement_estimated_savings_bytes=10000.0,
    )
    mem_agg0 = {"memory_queries": 0.0, "memory_query_hits": 0.0, "memory_used": 0.0, "memory_effective": 0.0, "memory_harmful": 0.0}
    mem_agg1 = {"memory_queries": 4.0, "memory_query_hits": 3.0, "memory_used": 2.0, "memory_effective": 2.0, "memory_harmful": 0.0}
    contest_payload = {
        "suite": "contest_workload",
        "generated_at_utc": "2026-09-21T00:00:00+00:00",
        "env": {"source_tree_sha256": "abc"},
        "methodology": {"protocol_version": "1.3", "state_precision": "adaptive"},
        "communication": {"T": comm_base, "S": comm_s, "SS": comm_ss},
        "memory": {
            "M0": {"success_count": 2, "task_count": 4, "aggregate": mem_agg0},
            "M1": {"success_count": 4, "task_count": 4, "aggregate": mem_agg1, "memory_summary": {"query_hit_rate": 0.75, "fast_path_hit_rate": 0.0, "followup_effective_reuse_rate": 1.0, "adoption_precision": 1.0, "negative_transfer_rate": 0.0}},
        },
    }
    text = render_contest_workload(contest_payload)
    assert "Strict transport" in text
    contest_dir = write_contest_workload(contest_payload, tmp_path / "contest")
    assert (contest_dir / "summary.json").exists()
