import pytest

from agent_runtime.evaluation.stability_benchmark import run_stability_benchmark


def test_stability_rejects_less_than_competition_minimum():
    with pytest.raises(ValueError):
        run_stability_benchmark(9)


def test_stability_generates_ten_round_evidence():
    payload = run_stability_benchmark(10)
    assert payload["rounds"] == 10
    assert payload["summary"]["success_count"] == 10
    assert payload["summary"]["error_count"] == 0
    assert payload["summary"]["total_state_transfers"] > 0


def test_stability_artifacts_have_manifest(tmp_path):
    from agent_runtime.evaluation.stability_benchmark import write_stability_results
    p = run_stability_benchmark(10)
    out = write_stability_results(p, root=str(tmp_path))
    assert (out / "summary.json").exists()
    assert "metadata.json" in (out / "MANIFEST.sha256").read_text()
