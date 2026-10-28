from __future__ import annotations

import numpy as np
import pytest

from agent_runtime.memory.models import MemoryRecord, MemoryType
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.base import validate_embedding_matrix


class BadEmbedding:
    name = "bad"
    dim = 4

    def __init__(self, matrix):
        self.matrix = np.asarray(matrix)

    def embed(self, texts):
        return self.matrix


def test_validate_embedding_matrix_rejects_object_nonfinite_and_shape():
    with pytest.raises(ValueError, match="non-numeric"):
        validate_embedding_matrix(np.array([[object()]], dtype=object), expected_rows=1)
    with pytest.raises(ValueError, match="non-finite"):
        validate_embedding_matrix(np.array([[1.0, np.inf]]), expected_rows=1)
    with pytest.raises(ValueError, match="2-D"):
        validate_embedding_matrix(np.array([1.0, 2.0]), expected_rows=1)
    with pytest.raises(ValueError, match="rows"):
        validate_embedding_matrix(np.ones((2, 4)), expected_rows=1, expected_dim=4)


def _record() -> MemoryRecord:
    return MemoryRecord(
        source_agent="reviewer",
        task_topic="embedding validation",
        summary="valid memory payload",
        memory_type=MemoryType.PROCEDURAL,
    )


def test_memory_store_rejects_nonfinite_embedding_on_write():
    store = MemoryStore(BadEmbedding([[0.0, 1.0, np.nan, 2.0]]))
    try:
        with pytest.raises(ValueError, match="non-finite"):
            store.write(_record())
    finally:
        store.close()


def test_memory_store_rejects_bad_query_embedding_before_search():
    # Seed with one valid record using a mutable provider, then make the query malformed.
    provider = BadEmbedding([[1.0, 0.0, 0.0, 0.0]])
    store = MemoryStore(provider)
    try:
        store.write(_record())
        provider.matrix = np.array([[1.0, 2.0, 3.0]])
        with pytest.raises(ValueError, match="returned dim 3, expected 4"):
            store.retrieve("query")
    finally:
        store.close()


def test_retriever_rejects_malformed_operand_embedding_provider() -> None:
    import asyncio
    import numpy as np
    import pytest

    from agent_runtime.agents.retriever import RetrieverAgent
    from agent_runtime.artifact_store.store import ArtifactStore
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.observability.metrics import MetricsCollector
    from agent_runtime.protocol.message import ActionType, AgentMessage
    from agent_runtime.config import Config
    from agent_runtime.runtime.context import RunContext
    from agent_runtime.state_exchange.exchange import StateExchange

    class BadEmbedding:
        name = "bad"
        dim = 4
        def embed(self, texts):
            return np.array([[float("nan")] * 4 for _ in texts], dtype=np.float32)

    class DummyLLM:
        name = "dummy"
        model = "dummy"
        def complete(self, prompt, *, system=None, temperature=None, max_tokens=None):
            from agent_runtime.providers.base import LLMResponse
            return LLMResponse("ok", 0, 0, "dummy")

    cfg = Config()
    # Build the persisted plan with a valid provider, then consume it with a malformed one.
    from agent_runtime.providers.mock import MockEmbedding
    good_state = StateExchange(MockEmbedding(dim=4), precision="float32")
    ref = good_state.put_plan_state({"operation": "sum", "operands": ["a", "b"]})

    ctx = RunContext(
        task_id="t",
        config=cfg,
        llm=DummyLLM(),
        embedding=BadEmbedding(),
        metrics=MetricsCollector(),
        channel=__import__("agent_runtime.runtime.channel", fromlist=["Channel"]).Channel(
            "structured", MetricsCollector(), __import__("agent_runtime.providers.tokenizer", fromlist=["HeuristicTokenizer"]).HeuristicTokenizer()
        ),
        mode="structured",
        state_exchange=good_state,
        artifact_store=ArtifactStore(),
        memory=None,
    )
    msg = AgentMessage(
        trace_id="trace-test",
        task_id="t",
        source_agent="planner",
        target_agent="retriever",
        action_type=ActionType.RETRIEVE,
        capability="retrieval",
        input_parameters={"plan_ref": ref.uri},
    )
    with pytest.raises(ValueError, match="non-finite"):
        RetrieverAgent()._resolve_plan(msg, ctx)
