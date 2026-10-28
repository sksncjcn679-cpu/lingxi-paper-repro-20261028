from __future__ import annotations

import numpy as np

from agent_runtime.config import Config, EmbeddingConfig
from agent_runtime.providers.factory import build_embedding
from agent_runtime.providers.lexical_embedding import LexicalEmbedding


def test_lexical_embedding_is_deterministic_and_normalized():
    provider = LexicalEmbedding(dim=64)
    first = provider.embed(["systemd service port conflict", ""])
    second = provider.embed(["systemd service port conflict", ""])
    assert first.shape == (2, 64)
    assert first.dtype == np.float32
    assert np.array_equal(first, second)
    assert np.allclose(np.linalg.norm(first, axis=1), 1.0)


def test_factory_builds_lexical_provider():
    cfg = Config(embedding=EmbeddingConfig(provider="lexical", dim=32))
    provider = build_embedding(cfg)
    assert provider.name == "lexical-embedding"
    assert provider.dim == 32
