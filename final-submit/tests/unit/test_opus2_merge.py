"""opus2 merge: strict all-plane accounting, sublinear memory, LLM inbound context, scale sweep."""

from __future__ import annotations

from agent_runtime.config import load_config
from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.evaluation.runner import ExperimentConfig, run_once
from agent_runtime.providers.mock import MockEmbedding

SS = ExperimentConfig("SS", "ss", "structured", True, False, True)
T = ExperimentConfig("T", "t", "text", False, False, True, text_style="lean")


def test_strict_transport_counts_artifact_plane():
    row = run_once(SS, _self_contained_tasks, 42)
    assert row["artifact_bytes"] > 1000


def test_artifact_bytes_are_dedup_aware():
    from agent_runtime.artifact_store.store import ArtifactStore

    s = ArtifactStore(task_id="t", compress=False)
    a = s.put({"k": "x" * 300})
    s.put({"k": "x" * 300})
    assert s.materialized_bytes == a.size
    # fable7: with adaptive physical encoding the second put is still free (dedup), and the
    # first costs only its compressed size while the logical size is disclosed separately.
    z = ArtifactStore(task_id="t")
    b = z.put({"k": "x" * 300})
    z.put({"k": "x" * 300})
    assert z.logical_materialized_bytes == b.size
    assert 0 < z.materialized_bytes < b.size
    assert z.get_json(b.uri) == {"k": "x" * 300}


def test_sublinear_memory_retrieval_is_ranking_equivalent():
    from agent_runtime.evaluation.memory_scale_benchmark import run_memory_scale_benchmark

    r = run_memory_scale_benchmark(sizes=(60, 300), queries_per_target=2)
    for row in r["results"].values():
        assert row["ranking_mismatches"] == 0 and row["precision_at_1"] == 1.0


def test_memory_index_sees_other_connection_writes(tmp_path):
    from agent_runtime.memory.models import MemoryRecord
    from agent_runtime.memory.store import MemoryStore

    db = str(tmp_path / "m.sqlite3")
    a, b = MemoryStore(MockEmbedding(dim=64), db_path=db), MemoryStore(MockEmbedding(dim=64), db_path=db)
    try:
        assert b.retrieve(None, keywords=["zeta"], top_k=3) == []
        a.write(MemoryRecord(source_agent="x", task_topic="t", summary="zeta procedure", keywords=["zeta"], tags=["q"]))
        assert [h.record.summary for h in b.retrieve(None, keywords=["zeta"], top_k=3)] == ["zeta procedure"]
    finally:
        a.close()
        b.close()


def test_llm_inbound_context_makes_prompt_tokens_protocol_sensitive(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_CONTEXT", "inbound")
    t, ss = run_once(T, _self_contained_tasks, 42), run_once(SS, _self_contained_tasks, 42)
    assert ss["llm_prompt_tokens"] < t["llm_prompt_tokens"]
    monkeypatch.delenv("AGENT_LLM_CONTEXT")
    assert load_config(load_env=False).llm_context_mode == "none"
