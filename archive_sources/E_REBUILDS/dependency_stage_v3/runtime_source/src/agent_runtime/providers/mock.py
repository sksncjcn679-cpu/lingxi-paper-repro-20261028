"""确定性 mock Provider。

设计目标：
- 无需任何 API Key 即可运行全部测试与实验；
- 输出完全由输入 + 随机种子决定，保证实验可复现；
- mock LLM 支持「指令式」生成：当提示中包含 JSON 结构化指令时，返回稳定的
  结构化文本，使上层 Agent 逻辑可被确定性驱动。

注意：mock 代表「推理能力」的占位。任务的语义正确性由场景 verifier 与确定性
变换保证；对通信开销的对比只取决于消息内容与计量口径，与 mock 创造力无关。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import numpy as np

from agent_runtime.providers.base import LLMResponse, l2_normalize
from agent_runtime.providers.tokenizer import HeuristicTokenizer, Tokenizer

_WORDS = [
    "plan",
    "retrieve",
    "execute",
    "review",
    "analyze",
    "summarize",
    "fix",
    "patch",
    "test",
    "log",
    "exception",
    "resource",
    "async",
    "await",
    "close",
    "handle",
    "verify",
    "evidence",
    "strategy",
]


def _stable_hash(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


class MockLLM:
    """确定性 mock LLM。

    生成策略：基于提示哈希 + 种子产出确定性的伪文本。文本长度受 max_tokens 约束，
    便于在 token 统计上呈现合理规模。
    """

    name = "mock-llm"

    def __init__(
        self,
        model: str = "mock-llm",
        seed: int = 42,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        self.model = model
        self.seed = seed
        self._tok = tokenizer or HeuristicTokenizer()

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        full_prompt = f"{system or ''}\n{prompt}"
        from agent_runtime.runtime.llm_gateway import COALESCE_SYSTEM

        if system == COALESCE_SYSTEM:
            # sota1: behave like an instruction-following model on a vectored request — one
            # deterministic '<k>: answer' line per numbered item, each generated exactly as the
            # item would have been generated on its own (same length law, per-item budget).
            items = [chunk for chunk in prompt.split("### ")[1:]]
            per_cap = max(1, int((max_tokens or 64 * max(1, len(items))) / max(1, len(items))))
            lines = []
            for k, item in enumerate(items, 1):
                lines.append(f"{k}: {self._words(f'{self.seed}:{item}', per_cap)}")
            text = "\n".join(lines)
        else:
            text = self._words(f"{self.seed}:{full_prompt}", max_tokens or 64)
        prompt_tokens = self._tok.count(full_prompt)
        completion_tokens = self._tok.count(text)
        return LLMResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self.model,
        )


    @staticmethod
    def _words(key: str, cap: int) -> str:
        h = _stable_hash(key)
        rng = np.random.default_rng(h % (2**32))
        n_words = int(min(cap, 8 + (h % 40)))
        return " ".join(rng.choice(_WORDS, size=n_words).tolist())


class GatewayCalibratedLLM:
    """sota1: deterministic *projection* of a real provider's bill (never real evidence).

    Wraps :class:`MockLLM` and adds the provider-side accounting measured on the 2026-09-24
    gpt-6-luna rerun (72 cells, fable7…fable16): every request is billed a fixed hidden prompt
    ``overhead_tokens`` plus ``prompt_scale`` × the runtime-visible prompt (heuristic count).
    Fit over 72 cells: overhead=4382, scale=1.311, R²=0.9998, max relative error 0.67% (``evaluation/billing_model.py``).
    Responses are tagged ``metadata['billing_model']='projection'`` so they cannot be mistaken
    for provider-billed numbers.
    """

    name = "gateway-calibrated-mock"

    def __init__(self, inner: "MockLLM", *, overhead_tokens: int = 4382, prompt_scale: float = 1.311,
                 cached_fraction: float = 0.85, latency_per_request: float = 7.03,
                 latency_per_completion_token: float = 0.0433) -> None:
        self.inner = inner
        self.model = f"{inner.model}+gateway-projection"
        self.overhead_tokens = int(overhead_tokens)
        self.prompt_scale = float(prompt_scale)
        self.cached_fraction = float(cached_fraction)
        # Coarse latency law from the same archive (R^2=0.62, noisy): reported, never slept.
        self.latency_per_request = float(latency_per_request)
        self.latency_per_completion_token = float(latency_per_completion_token)

    def complete(self, prompt: str, *, system: str | None = None, temperature: float | None = None,
                 max_tokens: int | None = None) -> LLMResponse:
        r = self.inner.complete(prompt, system=system, temperature=temperature, max_tokens=max_tokens)
        billed = self.overhead_tokens + int(round(self.prompt_scale * r.prompt_tokens))
        return LLMResponse(
            text=r.text,
            prompt_tokens=billed,
            completion_tokens=r.completion_tokens,
            model=self.model,
            metadata={
                "billing_model": "projection",
                "runtime_visible_prompt_tokens": r.prompt_tokens,
                "cached_prompt_tokens": int(self.overhead_tokens * self.cached_fraction),
                "projected_latency_sec": self.latency_per_request + self.latency_per_completion_token * r.completion_tokens,
            },
        )


class MockEmbedding:
    """确定性 mock embedding。

    基于字符 n-gram 的哈希特征 + 归一化，产出稳定向量。相似文本（共享子串）
    会得到较高余弦相似度，使语义检索在 mock 下也具备可解释的区分度。
    """

    name = "mock-embedding"

    def __init__(self, dim: int = 256, seed: int = 42) -> None:
        self.dim = dim
        self.seed = seed

    def _embed_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        text = (text or "").lower()
        # 字符级 3-gram 哈希到固定维度，形成稳定的词袋式表示。
        tokens = text.split()
        grams = list(tokens)
        for tok in tokens:
            padded = f"#{tok}#"
            grams.extend(padded[i : i + 3] for i in range(len(padded) - 2))
        if not grams:
            grams = ["#empty#"]
        for g in grams:
            idx = _stable_hash(f"{self.seed}:{g}") % self.dim
            vec[idx] += 1.0
        return vec

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        matrix = np.vstack([self._embed_one(t) for t in texts])
        return l2_normalize(matrix)


__all__ = ["MockLLM", "MockEmbedding"]
