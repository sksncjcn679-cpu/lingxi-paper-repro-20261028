"""fable13: documentation-trained semantic embedding (default provider)."""

from __future__ import annotations

import numpy as np


def test_model_ships_and_is_normalised():
    from agent_runtime.providers.docsem_embedding import DocSemEmbedding, model_info

    info = model_info()
    assert info["vocab"] > 10000 and info["dim"] == 128 and "Levy" in info["method"]
    e = DocSemEmbedding(dim=256)
    m = e.embed(["daemon cannot bind its listening address", "端口被占用", ""])
    assert m.shape == (3, 256)
    assert np.allclose(np.linalg.norm(m[:2], axis=1), 1.0, atol=1e-5)


def test_unused_provider_does_not_load_vectors(monkeypatch):
    from agent_runtime.providers import docsem_embedding as module

    monkeypatch.setattr(module, "_load", lambda: (_ for _ in ()).throw(
        AssertionError("unused vectors loaded")))
    provider = module.DocSemEmbedding(dim=256)
    assert provider.dim == 256
    assert provider.embed([]).shape == (0, 256)


def test_semantic_paraphrase_beats_lexical_on_hard_set():
    from agent_runtime.evaluation.memory_hard_set import run_memory_hard_set

    s = run_memory_hard_set()["summary"]
    assert s["docsem"]["mrr"] > s["lexical"]["mrr"] > s["mock"]["mrr"]


def test_default_provider_is_docsem_and_mock_still_available(monkeypatch):
    from agent_runtime.config import load_config
    from agent_runtime.providers.factory import build_embedding

    monkeypatch.delenv("AGENT_EMBEDDING_PROVIDER", raising=False)
    cfg = load_config(load_env=False)
    assert cfg.embedding.provider == "docsem"
    assert type(build_embedding(cfg)).__name__ == "DocSemEmbedding"
    monkeypatch.setenv("AGENT_EMBEDDING_PROVIDER", "mock")
    assert type(build_embedding(load_config(load_env=False))).__name__ == "MockEmbedding"


def test_training_corpus_excludes_the_project():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("t", Path(__file__).resolve().parents[2] / "scripts" / "train_os_word_vectors.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "agent_runtime" in mod.EXCLUDE and all("agent_runtime" not in s for s, _ in mod.SOURCES)


def test_compression_dictionary_reuses_the_loaded_model(monkeypatch):
    from agent_runtime.artifact_store import store
    from agent_runtime.providers import docsem_embedding

    index, _vecs, _sif, _pc, _meta, prob = docsem_embedding._load()
    vocab = [None] * len(index)
    for word, position in index.items():
        vocab[position] = word
    order = np.argsort(-prob, kind="stable")
    words, size = [], 0
    for i in order:
        word = vocab[int(i)]
        if size + len(word) + 1 > 32768:
            break
        words.append(word)
        size += len(word) + 1
    expected = " ".join(reversed(words)).encode("ascii", "ignore")

    monkeypatch.setattr(store, "_ZDICT", None)
    monkeypatch.setattr(
        np,
        "load",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model archive reloaded")),
    )
    assert docsem_embedding.compression_dictionary_bytes() == expected
    assert store._domain_zdict() == expected
