"""根据配置构造 LLM / Embedding Provider。"""

from __future__ import annotations

from agent_runtime.config import Config
from agent_runtime.providers.base import EmbeddingProvider, LLMProvider
from agent_runtime.providers.mock import MockEmbedding, MockLLM
from agent_runtime.providers.lexical_embedding import LexicalEmbedding


def build_llm(config: Config) -> LLMProvider:
    """按配置返回 LLM provider；只有显式 ``mock`` 才允许使用 mock。"""
    provider = config.llm.provider.lower()
    if provider == "mock":
        return MockLLM(model=config.llm.model or "mock-llm", seed=config.random_seed)
    if provider == "mock-gateway":
        # sota1: deterministic projection of a real gateway bill (calibrated fixed per-request
        # overhead); tagged as a projection, never as provider-billed evidence.
        from agent_runtime.providers.mock import GatewayCalibratedLLM

        return GatewayCalibratedLLM(MockLLM(model="mock-llm", seed=config.random_seed))
    if provider == "openai":
        from agent_runtime.providers.openai_provider import OpenAILLM

        return OpenAILLM(
            model=config.llm.model,
            api_key=config.llm.api_key,
            base_url=config.llm.base_url,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            timeout_sec=config.llm.timeout_sec,
            max_retries=config.llm.max_retries,
            reasoning_effort=config.llm.reasoning_effort,
            completion_governor=config.llm.completion_governor,
        )
    raise ValueError(
        f"unsupported LLM provider: {config.llm.provider!r}; expected 'mock' or 'openai'"
    )


def build_embedding(config: Config) -> EmbeddingProvider:
    """按配置返回 embedding provider；显式真实 provider 不静默降级为 mock。"""
    provider = config.embedding.provider.lower()
    if provider == "mock":
        return MockEmbedding(dim=config.embedding.dim, seed=config.random_seed)
    if provider == "docsem":
        from agent_runtime.providers.docsem_embedding import DocSemEmbedding

        return DocSemEmbedding(dim=max(192, int(config.embedding.dim)))
    if provider == "lexical":
        return LexicalEmbedding(dim=config.embedding.dim)
    if provider == "openai":
        from agent_runtime.providers.openai_provider import OpenAIEmbedding

        return OpenAIEmbedding(
            model=config.embedding.model,
            api_key=config.llm.api_key,
            base_url=config.llm.base_url,
            dim=config.embedding.dim,
        )
    if provider in ("sentence-transformers", "st"):
        try:
            from agent_runtime.providers.st_provider import SentenceTransformerEmbedding

            return SentenceTransformerEmbedding(model=config.embedding.model)
        except Exception as exc:  # noqa: BLE001 - convert optional dependency failure
            raise RuntimeError(
                "sentence-transformers embedding provider requested but unavailable; "
                "install the optional embedding dependencies"
            ) from exc
    raise ValueError(
        "unsupported embedding provider: "
        f"{config.embedding.provider!r}; expected mock, lexical, docsem, openai, or sentence-transformers"
    )


__all__ = ["build_llm", "build_embedding"]
