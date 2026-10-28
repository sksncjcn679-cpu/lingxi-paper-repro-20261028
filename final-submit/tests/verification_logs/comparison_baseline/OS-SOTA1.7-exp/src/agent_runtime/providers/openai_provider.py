"""OpenAI 兼容 Provider（可选）。

仅在配置 provider=openai 且安装了 openai 包时使用。延迟导入以避免
默认 mock-first 路径产生额外依赖。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from agent_runtime.providers.base import LLMResponse, l2_normalize
from agent_runtime.providers.tokenizer import get_tokenizer


class OpenAILLM:
    """通过 OpenAI 兼容接口访问真实 LLM。"""

    name = "openai-llm"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_sec: float = 60.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] | None = None,
        reasoning_effort: str | None = None,
        completion_governor: bool = True,
    ) -> None:
        from openai import OpenAI  # 延迟导入

        if not api_key:
            raise ValueError("OpenAI provider 需要 api_key（见 .env AGENT_LLM_API_KEY）")
        self.model = model
        self._default_temperature = temperature
        self._default_max_tokens = max_tokens
        self.timeout_sec = float(timeout_sec)
        self.max_retries = int(max_retries)
        self._sleep = sleep or time.sleep
        # The SDK's own retry loop is disabled (max_retries=0) so every attempt, its wait and
        # its outcome are visible to the runtime and recorded in response metadata.
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=self.timeout_sec, max_retries=0)
        self._tok = get_tokenizer(prefer_tiktoken=True, model=model)
        self._temperature_supported = True
        # sota1 completion-budget governor (sticky, per provider instance):
        # * the negotiated cap parameter is remembered, so a gateway that rejects
        #   ``max_tokens`` costs one failed request per session instead of one per call;
        # * a measured cap violation (completion > requested cap) escalates once to
        #   ``max_completion_tokens`` and then to ``reasoning_effort=low``;
        # * unsupported ``reasoning_effort`` is dropped and remembered.
        self._cap_param: str | None = "max_tokens"
        self._reasoning_effort = reasoning_effort
        self._reasoning_supported = True
        self._governor = bool(completion_governor)
        self.governor_log: list[dict[str, Any]] = []
        # Safety valve: on reasoning models ``max_completion_tokens`` also bounds hidden
        # reasoning, so a tight cap can end with finish_reason=length and *empty* visible text.
        # When that is observed the cap sent to the provider is relaxed (x2, at most x4).
        self._cap_scale = 1.0

    def _create(self, kwargs: dict[str, Any], metadata: dict[str, Any]) -> Any:
        """One logical request with bounded, jittered exponential-backoff retries.

        Only transient failures (timeouts, connection resets, HTTP 408/409/429/5xx) are retried;
        parameter errors propagate immediately so the caller's compatibility fallbacks run.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._client.chat.completions.create(**kwargs)
                metadata["attempts"] = metadata.get("attempts", 0) + attempt
                return resp
            except Exception as exc:  # noqa: BLE001 - provider-specific exception classes vary
                if attempt > self.max_retries or not _is_transient_error(exc):
                    metadata["attempts"] = metadata.get("attempts", 0) + attempt
                    raise
                wait = min(20.0, 0.5 * (2 ** (attempt - 1))) * (0.5 + random.random())
                retry_after = _retry_after_seconds(exc)
                if retry_after is not None:
                    wait = max(wait, min(retry_after, 30.0))
                metadata.setdefault("retries", []).append(
                    {"attempt": attempt, "error": type(exc).__name__, "wait_sec": round(wait, 3)}
                )
                self._sleep(wait)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        requested_max_tokens = self._default_max_tokens if max_tokens is None else max_tokens
        metadata: dict[str, Any] = {}
        started = time.perf_counter()
        resp = self._create_negotiated(messages, temperature, requested_max_tokens, metadata)
        metadata["request_wall_sec"] = round(time.perf_counter() - started, 4)
        if not self._temperature_supported:
            metadata["temperature_unsupported"] = True
        text = resp.choices[0].message.content or ""
        finish_reason = getattr(resp.choices[0], "finish_reason", None)
        if requested_max_tokens is not None and not text.strip() and finish_reason == "length":
            metadata["truncated_empty"] = True
            if self._governor and self._cap_scale < 4.0:
                self._cap_scale = min(4.0, self._cap_scale * 2.0)
                metadata["governor_action"] = f"cap_scale={self._cap_scale:g}"
                self.governor_log.append({"requested_cap": requested_max_tokens, "action": metadata["governor_action"]})
        usage = resp.usage
        metadata["usage_source"] = "provider_reported" if usage is not None else "local_estimate"
        reasoning_tokens = 0
        if usage is not None:
            prompt_tokens = _usage_get(usage, "prompt_tokens")
            completion_tokens = _usage_get(usage, "completion_tokens")
            cached_prompt_tokens = _usage_get(_usage_get(usage, "prompt_tokens_details", None), "cached_tokens")
            reasoning_tokens = _usage_get(_usage_get(usage, "completion_tokens_details", None), "reasoning_tokens")
        else:
            prompt_tokens = self._tok.count(f"{system or ''}\n{prompt}")
            completion_tokens = self._tok.count(text)
            cached_prompt_tokens = 0
        metadata["cached_prompt_tokens"] = cached_prompt_tokens
        metadata["reasoning_tokens"] = reasoning_tokens
        # Runtime-visible prompt size (local tokenizer) for billing decomposition.
        metadata["visible_prompt_tokens"] = self._tok.count(f"{system or ''}\n{prompt}")
        if (
            requested_max_tokens is not None
            and not metadata.get("truncated_empty")
            and completion_tokens > requested_max_tokens * self._cap_scale + max(8, requested_max_tokens // 10)
        ):
            metadata["cap_violation"] = True
            action = self._escalate_after_violation(reasoning_tokens) if self._governor else None
            if action:
                metadata["governor_action"] = action
            self.governor_log.append({
                "requested_cap": requested_max_tokens,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
                "action": action,
            })
        return LLMResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self.model,
            metadata=metadata,
        )

    def _escalate_after_violation(self, reasoning_tokens: int = 0) -> str | None:
        """One escalation step per observed violation (sticky for the session).

        If the provider reports hidden reasoning tokens, the overrun is reasoning, so lowering
        ``reasoning_effort`` is tried first (a tighter cap would only truncate the visible
        answer).  Otherwise the cap parameter itself was ignored, so ``max_completion_tokens``
        is tried first.
        """
        can_reason = self._reasoning_supported and self._reasoning_effort not in ("low", "minimal")
        steps = []
        if reasoning_tokens > 0 and can_reason:
            steps.append("reasoning")
        if self._cap_param == "max_tokens":
            steps.append("cap")
        if can_reason and "reasoning" not in steps:
            steps.append("reasoning")
        for step in steps:
            if step == "reasoning":
                self._reasoning_effort = "low"
                return "reasoning_effort=low"
            if step == "cap":
                self._cap_param = "max_completion_tokens"
                return "cap_param=max_completion_tokens"
        return None

    def _build_kwargs(self, messages, temperature, cap) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if self._temperature_supported:
            kwargs["temperature"] = self._default_temperature if temperature is None else temperature
        if cap is not None and self._cap_param is not None:
            kwargs[self._cap_param] = int(round(cap * self._cap_scale))
        if self._reasoning_effort and self._reasoning_supported:
            kwargs["reasoning_effort"] = self._reasoning_effort
        return kwargs

    def _create_negotiated(self, messages, temperature, cap, metadata: dict[str, Any]) -> Any:
        """Create a completion, negotiating unsupported parameters once and remembering them."""
        for _ in range(6):  # each branch permanently removes one incompatibility
            kwargs = self._build_kwargs(messages, temperature, cap)
            try:
                resp = self._create(kwargs, metadata)
                metadata["max_tokens_parameter"] = self._cap_param if cap is not None else None
                if self._reasoning_effort and self._reasoning_supported:
                    metadata["reasoning_effort"] = self._reasoning_effort
                return resp
            except Exception as exc:  # noqa: BLE001 - provider-specific exception classes vary
                if "temperature" in kwargs and _is_unsupported_temperature_error(exc):
                    # Reasoning-class models only accept their default sampling temperature.
                    self._temperature_supported = False
                    metadata["temperature_unsupported"] = True
                    continue
                if "reasoning_effort" in kwargs and _is_unsupported_param_error(exc, "reasoning_effort"):
                    self._reasoning_supported = False
                    metadata["reasoning_effort_unsupported"] = True
                    continue
                if cap is not None and self._cap_param is not None and _is_unsupported_max_tokens_error(exc):
                    if self._cap_param == "max_tokens":
                        self._cap_param = "max_completion_tokens"
                        metadata["max_tokens_fallback"] = True
                    else:
                        self._cap_param = None
                        metadata["max_tokens_unsupported"] = True
                    continue
                raise
        raise RuntimeError("provider parameter negotiation did not converge")


def _usage_get(obj: Any, name: str, default: Any = 0) -> Any:
    if obj is None:
        return default
    value = obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
    if default == 0:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
    return value


def _is_unsupported_param_error(exc: Exception, param: str) -> bool:
    text = str(exc).lower()
    return param in text and any(
        m in text for m in ("unsupported", "not support", "unknown parameter", "unrecognized", "unexpected keyword", "invalid parameter", "not allowed")
    )


def _is_unsupported_max_tokens_error(exc: Exception) -> bool:
    """Recognize the provider's varying error text without hiding real failures."""
    text = str(exc).lower()
    mentions_parameter = "max_tokens" in text or "max completion tokens" in text
    unsupported = any(
        marker in text
        for marker in (
            "unsupported",
            "not support",
            "unknown parameter",
            "unrecognized",
            "unexpected keyword",
            "invalid parameter",
        )
    )
    return mentions_parameter and unsupported


_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
_TRANSIENT_NAMES = ("timeout", "connection", "ratelimit", "internalserver", "serviceunavailable", "apierror")


def _is_transient_error(exc: Exception) -> bool:
    """Classify retryable provider failures without importing SDK-specific classes."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in _TRANSIENT_STATUS
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__.lower()
    if any(marker in name for marker in _TRANSIENT_NAMES):
        return "badrequest" not in name and "authentication" not in name
    text = str(exc).lower()
    return any(m in text for m in ("timed out", "timeout", "connection reset", "temporarily unavailable", "rate limit"))


def _retry_after_seconds(exc: Exception) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
        return float(value) if value is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _is_unsupported_temperature_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "temperature" in text and any(
        m in text for m in ("unsupported", "not support", "only the default", "does not support", "invalid")
    )


class OpenAIEmbedding:
    """通过 OpenAI 兼容接口访问真实 embedding。"""

    name = "openai-embedding"

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        dim: int = 1536,
    ) -> None:
        from openai import OpenAI  # 延迟导入

        if not api_key:
            raise ValueError("OpenAI embedding 需要 api_key")
        self.model = model
        self.dim = dim
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._cache: dict[str, np.ndarray] = {}

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        values = list(texts)
        output: list[np.ndarray | None] = [None] * len(values)
        missing: dict[str, list[int]] = {}
        for index, text in enumerate(values):
            cached = self._cache.get(text)
            if cached is None:
                missing.setdefault(text, []).append(index)
            else:
                output[index] = cached.copy()
        missing_texts = list(missing)
        for start in range(0, len(missing_texts), 64):
            batch_texts = missing_texts[start : start + 64]
            resp = self._client.embeddings.create(
                model=self.model,
                input=batch_texts,
            )
            rows = np.asarray([d.embedding for d in resp.data], dtype=np.float32)
            if rows.ndim != 2 or rows.shape[0] != len(batch_texts):
                raise ValueError("OpenAI embedding provider returned an invalid batch shape")
            rows = l2_normalize(rows)
            self.dim = rows.shape[1]
            for text, row in zip(batch_texts, rows, strict=True):
                saved = np.asarray(row, dtype=np.float32)
                self._cache[text] = saved.copy()
                for index in missing[text]:
                    output[index] = saved.copy()
        return np.vstack([row for row in output if row is not None]).astype(np.float32)


__all__ = ["OpenAILLM", "OpenAIEmbedding"]
