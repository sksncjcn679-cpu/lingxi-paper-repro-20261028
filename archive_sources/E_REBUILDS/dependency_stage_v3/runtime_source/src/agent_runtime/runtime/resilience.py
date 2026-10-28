"""Small, explicit runtime resilience primitives.

These primitives intentionally stay local and deterministic. They provide two controls
that are useful in an agent runtime without pretending to be a distributed consensus
layer:

- ``RetryBudget`` caps retries across *all* agents in one task so nested/serial retry
  policies cannot amplify a provider outage into a retry storm.
- ``CircuitBreaker`` stops calling a repeatedly failing agent until a cooldown expires,
  then permits one half-open probe.

Both are opt-in in ``Config`` to preserve historical benchmark comparability.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import Callable


@dataclass
class RetryBudget:
    """Task-scoped retry token bucket with a fixed total allowance."""

    max_retries: int
    used: int = 0
    denied: int = 0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")

    @property
    def remaining(self) -> int:
        return max(0, self.max_retries - self.used)

    def consume(self) -> bool:
        if self.used >= self.max_retries:
            self.denied += 1
            return False
        self.used += 1
        return True


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-agent failure circuit with a single half-open probe.

    This is a process-local protection mechanism. It does not synchronize breaker state
    between distributed workers.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        reset_timeout_sec: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if not math.isfinite(reset_timeout_sec) or reset_timeout_sec < 0:
            raise ValueError("reset_timeout_sec must be a finite non-negative number")
        self.failure_threshold = int(failure_threshold)
        self.reset_timeout_sec = float(reset_timeout_sec)
        self._clock = clock or time.monotonic
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at: float | None = None
        self._probe_in_flight = False

    def allow_request(self) -> bool:
        if self.state is CircuitState.CLOSED:
            return True
        if self.state is CircuitState.OPEN:
            assert self.opened_at is not None
            if self._clock() - self.opened_at < self.reset_timeout_sec:
                return False
            self.state = CircuitState.HALF_OPEN
            self._probe_in_flight = False
        if self.state is CircuitState.HALF_OPEN:
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> CircuitState:
        self._probe_in_flight = False
        if self.state is CircuitState.HALF_OPEN:
            self._open()
            return self.state
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self._open()
        return self.state

    def _open(self) -> None:
        self.state = CircuitState.OPEN
        self.opened_at = self._clock()


__all__ = ["RetryBudget", "CircuitBreaker", "CircuitState"]
