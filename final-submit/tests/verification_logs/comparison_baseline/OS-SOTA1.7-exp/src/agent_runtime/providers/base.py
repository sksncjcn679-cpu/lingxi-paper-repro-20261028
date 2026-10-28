"""Provider 抽象：LLM 与 Embedding 的统一接口与数据类型。

所有 Agent 通过该抽象访问模型能力，便于在 mock 与真实 provider 间切换，
保证实验可复现与无 API Key 可运行。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class LLMResponse:
    """LLM 补全结果，含 token 计数（用于通信/计算开销统计）。"""

    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    cached: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@runtime_checkable
class LLMProvider(Protocol):
    """LLM Provider 接口。"""

    name: str
    model: str

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """根据提示生成补全。"""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding Provider 接口。"""

    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """返回形状为 (len(texts), dim) 的 float32 向量矩阵（已 L2 归一化）。"""
        ...



def validate_embedding_matrix(
    matrix: np.ndarray,
    *,
    expected_rows: int,
    expected_dim: int | None = None,
    context: str = "embedding provider",
) -> np.ndarray:
    """Validate provider output before it enters state/memory stores.

    Real providers are an external trust boundary: malformed shapes, object dtypes or
    NaN/Infinity must fail fast instead of being hashed, quantized or persisted.  The
    returned matrix is a contiguous float32 view/copy with deterministic numeric semantics.
    """
    arr = np.asarray(matrix)
    if arr.dtype.kind not in {"f", "i", "u"}:
        raise ValueError(f"{context} returned non-numeric dtype: {arr.dtype}")
    if arr.ndim != 2:
        raise ValueError(f"{context} must return a 2-D matrix, got shape {arr.shape}")
    if arr.shape[0] != int(expected_rows):
        raise ValueError(
            f"{context} returned {arr.shape[0]} rows for {expected_rows} input texts"
        )
    if expected_dim is not None and arr.shape[1] != int(expected_dim):
        raise ValueError(
            f"{context} returned dim {arr.shape[1]}, expected {int(expected_dim)}"
        )
    arr32 = np.ascontiguousarray(arr, dtype=np.float32)
    if not np.all(np.isfinite(arr32)):
        raise ValueError(f"{context} returned non-finite embedding values")
    return arr32

def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """对每一行做 L2 归一化，便于用内积近似余弦相似度。"""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


__all__ = [
    "LLMResponse",
    "LLMProvider",
    "EmbeddingProvider",
    "validate_embedding_matrix",
    "l2_normalize",
]
