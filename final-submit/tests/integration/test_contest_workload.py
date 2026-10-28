from agent_runtime.evaluation.contest_workload import run_contest_workload


def test_contest_workload_closes_three_official_mechanisms():
    p = run_contest_workload()
    comm = p["communication"]
    assert all(comm[k]["task_success"] == 1.0 for k in ("T", "S", "SS"))
    assert comm["T"]["message_count"] == comm["S"]["message_count"] == comm["SS"]["message_count"]
    assert comm["S"]["wire_bytes"] < comm["T"]["wire_bytes"]
    assert comm["SS"]["state_transfers"] > 0
    assert comm["SS"]["wire_bytes"] < comm["S"]["wire_bytes"]
    assert comm["SS"]["transport_bytes"] < comm["T"]["transport_bytes"]
    assert comm["SS"]["state_physical_saving_vs_logical"] > 0
    assert comm["SS"]["versioned_delta_ratio"] > 0

    m0, m1 = p["memory"]["M0"], p["memory"]["M1"]
    assert m0["success_count"] == 2
    assert m1["success_count"] == 4
    assert m1["aggregate"]["memory_used"] >= 2
    assert m1["aggregate"]["memory_effective"] >= 2
    assert m1["aggregate"]["memory_harmful"] == 0
    assert m1["memory_summary"]["query_hit_rate"] > 0
    assert m1["memory_summary"]["followup_effective_reuse_rate"] == 1.0
    assert m1["memory_summary"]["adoption_precision"] == 1.0
    assert m1["memory_summary"]["negative_transfer_rate"] == 0.0
