from __future__ import annotations

import math

import pytest

from agent_runtime.config import Config, EmbeddingConfig, LLMConfig, SandboxConfig, load_config
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.protocol.idempotency import PersistentIdempotencyCache


def test_nested_config_rejects_invalid_numeric_and_backend_values(tmp_path):
    with pytest.raises(ValueError, match="temperature"):
        LLMConfig(temperature=math.nan)
    with pytest.raises(ValueError, match="max_tokens"):
        LLMConfig(max_tokens=0)
    with pytest.raises(ValueError, match="embedding dim"):
        EmbeddingConfig(dim=0)
    with pytest.raises(ValueError, match="sandbox backend"):
        SandboxConfig(backend="typo")
    with pytest.raises(ValueError, match="must be positive"):
        SandboxConfig(timeout_sec=0)


def test_runtime_config_rejects_invalid_enumerations_and_limits():
    with pytest.raises(ValueError, match="state_precision"):
        Config(state_precision="float12")
    with pytest.raises(ValueError, match="event_journal_durability"):
        Config(event_journal_durability="sometimes")
    with pytest.raises(ValueError, match="idempotency_ttl_sec"):
        Config(idempotency_ttl_sec=math.inf)
    with pytest.raises(ValueError, match="memory_recency_half_life_sec"):
        Config(memory_recency_half_life_sec=math.nan)


def test_forward_topology_is_default_and_request_reply_remains_selectable(monkeypatch):
    monkeypatch.delenv("AGENT_PIPELINE_TOPOLOGY", raising=False)
    monkeypatch.delenv("AGENT_CONTROL_TRANSPORT", raising=False)
    assert Config().pipeline_topology == "request_reply"
    assert load_config(load_env=False).pipeline_topology == "request_reply"
    uds_topology = "forward" if hasattr(__import__("socket"), "AF_UNIX") else "request_reply"
    assert load_config(load_env=False, control_transport="uds").pipeline_topology == uds_topology
    assert load_config(load_env=False, pipeline_topology="request_reply").pipeline_topology == "request_reply"


def test_persistent_stores_reject_non_finite_policy_values(tmp_path):
    with pytest.raises(ValueError, match="finite positive"):
        PersistentIdempotencyCache(tmp_path / "idem.sqlite3", ttl_sec=math.nan)
    with pytest.raises(ValueError, match="recency_half_life"):
        MemoryStore(MockEmbedding(dim=8), recency_half_life_sec=math.inf)
    with pytest.raises(ValueError, match="dedup_threshold"):
        MemoryStore(MockEmbedding(dim=8), dedup_threshold=math.nan)
    with pytest.raises(ValueError, match="quality_floor"):
        MemoryStore(MockEmbedding(dim=8), quality_floor=1.1)
