from __future__ import annotations

from types import SimpleNamespace
import sys

import numpy as np
import pytest

from agent_runtime.config import EmbeddingConfig, LLMConfig, load_config


class _FakeChatCompletions:
    def __init__(self):
        self.calls = []
        self.with_usage = True

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3) if self.with_usage else None
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=usage,
        )


class _FakeEmbeddings:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        rows = []
        for i, _ in enumerate(kwargs["input"]):
            rows.append(SimpleNamespace(embedding=[1.0 + i, 2.0, 3.0]))
        return SimpleNamespace(data=rows)


class _FakeOpenAIClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=_FakeChatCompletions())
        self.embeddings = _FakeEmbeddings()


@pytest.fixture
def fake_openai(monkeypatch):
    created = []

    def factory(**kwargs):
        client = _FakeOpenAIClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=factory))
    return created


def test_openai_llm_mocked_usage_and_fallback_token_count(fake_openai):
    from agent_runtime.providers.openai_provider import OpenAILLM

    llm = OpenAILLM(
        model="fake-model",
        api_key="secret",
        base_url="https://example.invalid/v1",
        temperature=0.2,
        max_tokens=77,
    )
    assert fake_openai[0].kwargs["api_key"] == "secret"
    result = llm.complete("hello", system="sys")
    assert result.text == "ok"
    assert result.prompt_tokens == 7 and result.completion_tokens == 3
    call = fake_openai[0].chat.completions.calls[-1]
    assert call["temperature"] == 0.2 and call["max_tokens"] == 77
    assert call["messages"][0] == {"role": "system", "content": "sys"}

    fake_openai[0].chat.completions.with_usage = False
    result2 = llm.complete("hello again", temperature=0.5, max_tokens=9)
    assert result2.prompt_tokens > 0 and result2.completion_tokens > 0
    call2 = fake_openai[0].chat.completions.calls[-1]
    assert call2["temperature"] == 0.5 and call2["max_tokens"] == 9
    assert call2["messages"] == [{"role": "user", "content": "hello again"}]


def test_openai_llm_requires_key(fake_openai):
    from agent_runtime.providers.openai_provider import OpenAILLM

    with pytest.raises(ValueError, match="api_key"):
        OpenAILLM(api_key="")


@pytest.mark.parametrize("body", [{"thinking": {"type": "disabled"}}, {"enable_thinking": False}])
def test_provider_specific_parameters_reach_sdk(fake_openai, body):
    from agent_runtime.providers.factory import build_llm
    cfg = load_config(load_env=False, llm=LLMConfig(
        provider="openai", api_key="test-key", extra_body=body,
        max_tokens=1500, max_retries=2, completion_governor=False,
    ))
    llm = build_llm(cfg)
    llm.complete("test")
    call = fake_openai[0].chat.completions.calls[-1]
    assert call["extra_body"] == body
    assert call["max_tokens"] == 1500
    assert llm.max_retries == 2


def test_dmx_environment_config(monkeypatch):
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("VLM_API_KEY", "test-vlm-key")
    monkeypatch.setenv("AGENT_LLM_EXTRA_BODY", '{"enable_thinking":false}')
    cfg = load_config(load_env=False)
    assert cfg.llm.api_key == "test-vlm-key"
    assert cfg.llm.extra_body == {"enable_thinking": False}
    monkeypatch.setenv("AGENT_LLM_API_KEY", "explicit-key")
    assert load_config(load_env=False).llm.api_key == "explicit-key"


@pytest.mark.parametrize("body", [[], {"messages": []}, {"max_tokens": 9999}])
def test_extra_body_cannot_replace_controlled_fields(body):
    with pytest.raises(ValueError, match="extra_body"):
        LLMConfig(extra_body=body)


def test_openai_embedding_mocked_and_empty_input(fake_openai):
    from agent_runtime.providers.openai_provider import OpenAIEmbedding

    emb = OpenAIEmbedding(model="embed-fake", api_key="secret", dim=5)
    empty = emb.embed([])
    assert empty.shape == (0, 5) and empty.dtype == np.float32
    matrix = emb.embed(["a", "b"])
    assert matrix.shape == (2, 3)
    assert emb.dim == 3
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0)
    assert fake_openai[-1].embeddings.calls[-1] == {
        "model": "embed-fake",
        "input": ["a", "b"],
    }


def test_openai_embedding_batches_and_caches_duplicate_texts(fake_openai):
    from agent_runtime.providers.openai_provider import OpenAIEmbedding

    embedding = OpenAIEmbedding(model="embed-fake", api_key="secret", dim=3)
    texts = [f"text-{i}" for i in range(65)] + ["text-0"]
    matrix = embedding.embed(texts)
    assert matrix.shape == (66, 3)
    calls = fake_openai[-1].embeddings.calls
    assert [len(call["input"]) for call in calls] == [64, 1]
    embedding.embed(["text-0", "text-64"])
    assert len(fake_openai[-1].embeddings.calls) == 2


def test_openai_embedding_requires_key(fake_openai):
    from agent_runtime.providers.openai_provider import OpenAIEmbedding

    with pytest.raises(ValueError, match="api_key"):
        OpenAIEmbedding(api_key="")


def test_provider_factory_openai_paths_use_config(fake_openai):
    from agent_runtime.providers.factory import build_embedding, build_llm

    cfg = load_config(
        load_env=False,
        llm=LLMConfig(provider="openai", api_key="secret", model="chat-fake"),
        embedding=EmbeddingConfig(provider="openai", model="emb-fake", dim=11),
    )
    llm = build_llm(cfg)
    embedding = build_embedding(cfg)
    assert llm.model == "chat-fake"
    assert embedding.model == "emb-fake"
    assert embedding.dim == 11
