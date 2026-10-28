from agent_runtime.evaluation.os_probe import run_os_probe


def test_os_probe_records_real_capabilities_and_selection():
    p = run_os_probe()
    assert {"rlimit", "cgroup_v2", "af_unix", "posix_shm", "fork_server"} <= set(p["checks"])
    assert p["selection"]["control_plane"]
    assert p["memfd"]["consumer_read_mode"] in {"mmap_shared_ro", "mmap_private_ro", "pread", None}


def test_openeuler_gate_requires_signed_prechecks(monkeypatch, tmp_path):
    import agent_runtime.evaluation.competition_scorecard as sc

    rel = tmp_path / "os-release"
    rel.write_text('NAME="openEuler"\nVERSION="24.03 (LTS-SP3)"\nVERSION_ID="24.03"\n')
    real_path = sc.Path
    monkeypatch.setattr(sc, "Path", lambda p, *a: real_path(rel) if str(p) == "/etc/os-release" else real_path(p, *a))
    monkeypatch.delenv("AGENT_OPENEULER_PRECHECKS", raising=False)
    ok, detail = sc._is_target_openeuler()
    assert ok is False and "not signed" in detail
    from agent_runtime.evaluation.runner import _env_info

    monkeypatch.setenv("AGENT_OPENEULER_PRECHECKS", _env_info()["source_tree_sha256"])
    assert sc._is_target_openeuler()[0] is True
    monkeypatch.setenv("AGENT_OPENEULER_PRECHECKS", "0" * 64)
    assert sc._is_target_openeuler()[0] is False
