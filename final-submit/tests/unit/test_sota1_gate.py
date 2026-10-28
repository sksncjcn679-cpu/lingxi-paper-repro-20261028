"""OS-SOTA1.5: automatic OS-SOTA2 gate evaluation on synthetic result trees."""
from __future__ import annotations

import json

from agent_runtime.evaluation.sota2_gate import evaluate, render


def _write(root, name, data):
    d = root / name
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps(data))


def _cell(prompt, completion, judged=0, correct=0):
    return {"llm_prompt_tokens": prompt, "llm_completion_tokens": completion,
            "llm_judgements": judged, "llm_judgements_correct": correct}


def test_all_gates_pass_on_complete_evidence(tmp_path):
    r = tmp_path / "results"
    _write(r, "20260101-000000-1-competition_scorecard", {"summary": {
        "all_non_environment_mandatory_passed": True, "openeuler_target_verified": True,
        "mandatory_non_environment_passed": 20, "mandatory_non_environment_total": 20}})
    raw = {"T_memory_pair": [_cell(40000, 1600, 2, 2)] * 3,
           "FULL_coalesce": [_cell(5000, 200, 2, 2), _cell(4990, 180, 2, 2), _cell(5010, 210, 2, 2)],
           "T_group": [_cell(9500, 300, 2, 2)] * 3}
    # judge-on run decides G3, a separate judge-off run decides G2 (instrument separation)
    _write(r, "20260101-000001-1-external_llm", {"provider": {"llm_provider": "openai"}, "raw": raw,
                                                "summary": {"T": {"judge_evaluated": True}}, "env": {"source_tree_sha256": "a" * 64}})
    _write(r, "20260101-000003-1-external_llm", {"provider": {"llm_provider": "openai"}, "raw": raw,
                                                "summary": {"T": {"judge_evaluated": False}}, "env": {"source_tree_sha256": "a" * 64}})
    _write(r, "20260101-000002-1-long_session", {"provider": {"llm_provider": "openai"},
        "checks": {"no_unanswered_logical_requests": True},
        "summary": {"T+G": {"success_rate": 1.0, "billed_total_tokens_mean": 30000},
                    "SS+G": {"success_rate": 1.0, "billed_total_tokens_mean": 14000, "llm_coalesce_repairs_mean": 0}}})
    log = tmp_path / "win.log"
    log.write_text("===== a =====\n===== EXIT:0 =====\n===== python3 -m agent_runtime.cli competition-check =====\n"
                   "===== EXIT:1 =====\n===== python3 scripts/verify_results.py --require-linux =====\n===== EXIT:1 =====\n")
    p = evaluate(r, log, expect_sha="a" * 64)
    assert {k: g["status"] for k, g in p["gates"].items()} == {
        "G1_openeuler": "PASS", "G2_real_bill": "PASS", "G3_semantic_validity": "PASS",
        "G4_long_session": "PASS", "G5_windows": "PASS"}
    assert p["release_ready"] and "Release ready: **True**" in render(p)


def test_gates_pending_or_fail_on_weak_evidence(tmp_path):
    r = tmp_path / "results"
    raw = {"T_memory_pair": [_cell(40000, 1600)], "FULL_coalesce": [_cell(5000, 200)]}
    _write(r, "20260101-000001-1-external_llm", {"provider": {"llm_provider": "openai"}, "raw": raw, "summary": {}})
    _write(r, "20260101-000002-1-external_llm", {"provider": {"llm_provider": "mock"}, "raw": {}, "summary": {}})
    log = tmp_path / "win.log"
    log.write_text("===== python3 -m agent_runtime.cli sandbox-security =====\n===== EXIT:9 =====\n")
    p = evaluate(r, log, expect_sha="a" * 64)
    g = p["gates"]
    assert g["G2_real_bill"]["status"] == "PENDING"  # only n=1 (mock run ignored)
    assert g["G1_openeuler"]["status"] == "PENDING" and g["G3_semantic_validity"]["status"] == "PENDING"
    assert g["G5_windows"]["status"] == "FAIL" and not p["release_ready"]


def test_evidence_from_other_source_tree_is_invalid(tmp_path):
    r = tmp_path / "results"
    _write(r, "20260101-000000-1-competition_scorecard", {"env": {"source_tree_sha256": "b" * 64}, "summary": {
        "all_non_environment_mandatory_passed": True, "openeuler_target_verified": True}})
    log = tmp_path / "win.log"
    log.write_text("===== x =====\nSource SHA-256: `" + "c" * 64 + "`\n===== EXIT:0 =====\n")
    p = evaluate(r, log, expect_sha="a" * 64)
    assert p["gates"]["G1_openeuler"]["status"] == "INVALID"
    assert p["gates"]["G5_windows"]["status"] == "INVALID" and not p["release_ready"]


def test_judge_on_run_never_decides_bill_gate(tmp_path):
    r = tmp_path / "results"
    raw = {"T_memory_pair": [_cell(50000, 1600)] * 3, "FULL_coalesce": [_cell(9700, 190)] * 3}
    _write(r, "20260101-000001-1-external_llm", {"provider": {"llm_provider": "openai"}, "raw": raw,
                                                "summary": {"T": {"judge_evaluated": True}}})
    g = evaluate(r, None, expect_sha="a" * 64)["gates"]["G2_real_bill"]
    assert g["status"] == "PENDING" and "instrument-perturbed" in g["detail"]


def test_default_expected_sha_is_current_tree(tmp_path):
    from agent_runtime.evaluation.runner import _env_info

    p = evaluate(tmp_path / "results", None)
    assert p["expected_source_sha"] == _env_info()["source_tree_sha256"] and len(p["expected_source_sha"]) == 64
