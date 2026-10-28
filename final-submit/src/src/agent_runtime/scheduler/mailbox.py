"""Bounded async mailbox router for local multi-agent runtimes.

The default competition Orchestrator remains deterministic and sequential.  This module
provides an actor-style transport primitive for concurrent/local-distributed deployments:
per-target bounded queues, explicit backpressure, request deadlines, and handler-failure
isolation.  Handlers are ordinary async callables, so they can wrap ``BaseAgent.run`` or
an IPC client without coupling the router to a specific agent implementation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import math
import time
from typing import Literal

from agent_runtime.protocol.message import AgentMessage


class MailboxError(RuntimeError):
    """Base mailbox/router error."""


class RouteNotFoundError(MailboxError):
    """No handler has been registered for the target agent."""


class MailboxFullError(MailboxError):
    """A bounded target mailbox rejected a request because it was full."""


class MailboxDeadlineExceeded(MailboxError):
    """Request deadline expired before a response could be delivered."""


@dataclass
class MailboxStats:
    enqueued: int = 0
    processed: int = 0
    rejected: int = 0
    failures: int = 0
    deadline_exceeded: int = 0
    max_depth: int = 0


@dataclass
class _Request:
    message: AgentMessage
    future: asyncio.Future[AgentMessage]
    # Effective absolute deadline combining message.deadline_at and request(timeout).
    deadline_at: float | None = None


Handler = Callable[[AgentMessage], Awaitable[AgentMessage]]
OverflowPolicy = Literal["reject", "block"]


class AsyncMailboxRouter:
    """Actor-style request router with one bounded mailbox per registered target."""

    def __init__(
        self,
        *,
        queue_size: int = 64,
        overflow: OverflowPolicy = "reject",
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        if overflow not in {"reject", "block"}:
            raise ValueError("overflow must be 'reject' or 'block'")
        self.queue_size = int(queue_size)
        self.overflow = overflow
        self._handlers: dict[str, Handler] = {}
        self._worker_counts: dict[str, int] = {}
        self._queues: dict[str, asyncio.Queue[_Request]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._stats: dict[str, MailboxStats] = {}
        self._started = False
        self._closed = False

    def register(self, target: str, handler: Handler, *, workers: int = 1) -> None:
        if self._started:
            raise RuntimeError("register handlers before starting the router")
        if not target:
            raise ValueError("target must be non-empty")
        if workers <= 0:
            raise ValueError("workers must be positive")
        if target in self._handlers:
            raise ValueError(f"target already registered: {target}")
        self._handlers[target] = handler
        self._worker_counts[target] = int(workers)
        self._queues[target] = asyncio.Queue(maxsize=self.queue_size)
        self._stats[target] = MailboxStats()

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("router is closed")
        if self._started:
            return
        self._started = True
        for target in self._handlers:
            for worker_id in range(self._worker_counts[target]):
                self._tasks.append(
                    asyncio.create_task(
                        self._worker(target),
                        name=f"agent-mailbox:{target}:{worker_id}",
                    )
                )

    async def request(
        self,
        message: AgentMessage,
        *,
        timeout: float | None = None,
    ) -> AgentMessage:
        if not self._started or self._closed:
            raise RuntimeError("router is not running")
        target = message.target_agent
        queue = self._queues.get(target)
        if queue is None:
            raise RouteNotFoundError(target)
        stats = self._stats[target]

        if timeout is not None:
            if not math.isfinite(timeout):
                raise ValueError("timeout must be finite when provided")
            if timeout < 0:
                stats.deadline_exceeded += 1
                raise MailboxDeadlineExceeded(f"deadline exceeded before enqueue: {target}")
        now = time.time()
        absolute_deadlines: list[float] = []
        if timeout is not None:
            absolute_deadlines.append(now + float(timeout))
        if message.deadline_at is not None:
            absolute_deadlines.append(float(message.deadline_at))
        effective_deadline = min(absolute_deadlines) if absolute_deadlines else None
        budget = self._budget_until(effective_deadline)
        if budget is not None and budget <= 0:
            stats.deadline_exceeded += 1
            raise MailboxDeadlineExceeded(f"deadline exceeded before enqueue: {target}")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentMessage] = loop.create_future()
        item = _Request(message=message, future=future, deadline_at=effective_deadline)
        if self.overflow == "reject":
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull as exc:
                stats.rejected += 1
                raise MailboxFullError(f"mailbox full: {target}") from exc
        else:
            try:
                if budget is None:
                    await queue.put(item)
                else:
                    await asyncio.wait_for(queue.put(item), timeout=budget)
            except asyncio.TimeoutError as exc:
                stats.deadline_exceeded += 1
                raise MailboxDeadlineExceeded(f"deadline exceeded while enqueueing: {target}") from exc

        stats.enqueued += 1
        stats.max_depth = max(stats.max_depth, queue.qsize())
        budget = self._budget_until(effective_deadline)
        try:
            if budget is None:
                return await future
            if budget <= 0:
                raise asyncio.TimeoutError
            # shield prevents wait_for from cancelling the shared Future; the worker owns
            # completion/cancellation and can distinguish caller timeout from handler failure.
            return await asyncio.wait_for(asyncio.shield(future), timeout=budget)
        except asyncio.TimeoutError as exc:
            stats.deadline_exceeded += 1
            if not future.done():
                future.cancel()
            raise MailboxDeadlineExceeded(f"deadline exceeded waiting for response: {target}") from exc

    @staticmethod
    def _budget_until(deadline_at: float | None) -> float | None:
        return None if deadline_at is None else float(deadline_at) - time.time()

    @staticmethod
    def _remaining_budget(message: AgentMessage, timeout: float | None) -> float | None:
        """Backward-compatible helper used by callers/tests outside the router."""
        budgets: list[float] = []
        if timeout is not None:
            if not math.isfinite(timeout):
                raise ValueError("timeout must be finite when provided")
            if timeout < 0:
                return 0.0
            budgets.append(float(timeout))
        if message.deadline_at is not None:
            budgets.append(float(message.deadline_at) - time.time())
        return min(budgets) if budgets else None

    async def _worker(self, target: str) -> None:
        queue = self._queues[target]
        handler = self._handlers[target]
        stats = self._stats[target]
        while True:
            item = await queue.get()
            try:
                if item.future.cancelled():
                    continue
                try:
                    budget = self._budget_until(item.deadline_at)
                    if budget is not None and budget <= 0:
                        raise MailboxDeadlineExceeded(
                            f"deadline exceeded before dispatch: {target}"
                        )
                    if budget is None:
                        response = await handler(item.message)
                    else:
                        try:
                            response = await asyncio.wait_for(handler(item.message), timeout=budget)
                        except asyncio.TimeoutError as exc:
                            raise MailboxDeadlineExceeded(
                                f"deadline exceeded during handler execution: {target}"
                            ) from exc
                    if not isinstance(response, AgentMessage):
                        raise TypeError("mailbox handler must return AgentMessage")
                except asyncio.CancelledError:
                    # Router shutdown may cancel a worker while it owns an in-flight
                    # request.  Propagate that cancellation to the caller Future; otherwise
                    # request() can wait forever because the item is no longer queued and
                    # no worker remains to complete it.
                    if not item.future.done():
                        item.future.cancel()
                    raise
                except MailboxDeadlineExceeded as exc:
                    # If the caller already timed out it cancelled the Future and counted the
                    # timeout. Otherwise the worker is the first observer and owns the metric.
                    if not item.future.done():
                        stats.deadline_exceeded += 1
                        item.future.set_exception(exc)
                except Exception as exc:  # noqa: BLE001 - isolate one request from worker lifecycle
                    stats.failures += 1
                    if not item.future.done():
                        item.future.set_exception(exc)
                else:
                    stats.processed += 1
                    if not item.future.done():
                        item.future.set_result(response)
            finally:
                queue.task_done()

    def stats(self, target: str | None = None) -> MailboxStats | dict[str, MailboxStats]:
        if target is not None:
            try:
                value = self._stats[target]
            except KeyError as exc:
                raise RouteNotFoundError(target) from exc
            return MailboxStats(**vars(value))
        return {name: MailboxStats(**vars(value)) for name, value in self._stats.items()}

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for queue in self._queues.values():
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not item.future.done():
                    item.future.cancel()
                queue.task_done()

    async def __aenter__(self) -> "AsyncMailboxRouter":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


__all__ = [
    "AsyncMailboxRouter",
    "MailboxStats",
    "MailboxError",
    "MailboxFullError",
    "MailboxDeadlineExceeded",
    "RouteNotFoundError",
]
