from __future__ import annotations

import asyncio
import math
import time

import pytest

from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.scheduler.mailbox import (
    AsyncMailboxRouter,
    MailboxDeadlineExceeded,
    MailboxFullError,
)


def _msg(target: str, step: str) -> AgentMessage:
    return AgentMessage(
        trace_id="tr-mailbox",
        task_id="task-mailbox",
        step_id=step,
        source_agent="orchestrator",
        target_agent=target,
        action_type=ActionType.EXECUTE,
    )


def _reply(message: AgentMessage) -> AgentMessage:
    return AgentMessage(
        trace_id=message.trace_id,
        task_id=message.task_id,
        step_id=message.step_id,
        context_id=message.context_id,
        parent_message_id=message.message_id,
        causation_id=message.message_id,
        source_agent=message.target_agent,
        target_agent=message.source_agent,
        action_type=ActionType.RESULT,
        status=MessageStatus.OK,
    )


def test_mailbox_preserves_order_per_single_worker():
    async def scenario():
        seen: list[str] = []

        async def handler(message):
            seen.append(message.step_id)
            await asyncio.sleep(0)
            return _reply(message)

        router = AsyncMailboxRouter(queue_size=4)
        router.register("worker", handler)
        await router.start()
        try:
            replies = await asyncio.gather(
                router.request(_msg("worker", "s1")),
                router.request(_msg("worker", "s2")),
                router.request(_msg("worker", "s3")),
            )
            assert [r.step_id for r in replies] == ["s1", "s2", "s3"]
            assert seen == ["s1", "s2", "s3"]
            stats = router.stats("worker")
            assert stats.enqueued == 3
            assert stats.processed == 3
        finally:
            await router.close()

    asyncio.run(scenario())


def test_mailbox_backpressure_rejects_when_target_queue_is_full():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(message):
            entered.set()
            await release.wait()
            return _reply(message)

        router = AsyncMailboxRouter(queue_size=1, overflow="reject")
        router.register("worker", handler)
        await router.start()
        first = asyncio.create_task(router.request(_msg("worker", "s1")))
        await entered.wait()  # first is being processed, queue has one free slot
        second = asyncio.create_task(router.request(_msg("worker", "s2")))
        await asyncio.sleep(0)  # second occupies the only queued slot
        with pytest.raises(MailboxFullError):
            await router.request(_msg("worker", "s3"))
        release.set()
        await first
        await second
        assert router.stats("worker").rejected == 1
        await router.close()

    asyncio.run(scenario())


def test_handler_failure_isolated_and_next_request_still_runs():
    async def scenario():
        calls = 0

        async def handler(message):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            return _reply(message)

        router = AsyncMailboxRouter(queue_size=2)
        router.register("worker", handler)
        await router.start()
        try:
            with pytest.raises(RuntimeError, match="boom"):
                await router.request(_msg("worker", "s1"))
            reply = await router.request(_msg("worker", "s2"))
            assert reply.status == MessageStatus.OK
            stats = router.stats("worker")
            assert stats.failures == 1
            assert stats.processed == 1
        finally:
            await router.close()

    asyncio.run(scenario())


def test_router_enforces_message_deadline_before_enqueue():
    async def scenario():
        async def handler(message):
            return _reply(message)

        router = AsyncMailboxRouter()
        router.register("worker", handler)
        await router.start()
        try:
            message = _msg("worker", "expired").model_copy(
                update={"deadline_at": time.time() - 1}
            )
            with pytest.raises(MailboxDeadlineExceeded):
                await router.request(message)
            assert router.stats("worker").deadline_exceeded == 1
        finally:
            await router.close()

    asyncio.run(scenario())


def test_handler_is_cancelled_when_request_budget_expires_and_worker_recovers():
    async def scenario():
        cancelled = asyncio.Event()

        async def handler(message):
            if message.step_id == "slow":
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return _reply(message)

        router = AsyncMailboxRouter(queue_size=2)
        router.register("worker", handler)
        await router.start()
        try:
            with pytest.raises(MailboxDeadlineExceeded):
                await router.request(_msg("worker", "slow"), timeout=0.03)
            await asyncio.wait_for(cancelled.wait(), timeout=0.5)
            reply = await asyncio.wait_for(
                router.request(_msg("worker", "next"), timeout=0.5), timeout=1.0
            )
            assert reply.step_id == "next"
            assert reply.status == MessageStatus.OK
            stats = router.stats("worker")
            assert stats.deadline_exceeded >= 1
            assert stats.processed == 1
        finally:
            await router.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [math.nan, math.inf, -math.inf])
def test_mailbox_rejects_non_finite_timeout(timeout: float):
    async def scenario():
        async def handler(message):
            return _reply(message)

        router = AsyncMailboxRouter()
        router.register("worker", handler)
        await router.start()
        try:
            with pytest.raises(ValueError, match="finite"):
                await router.request(_msg("worker", "bad-timeout"), timeout=timeout)
        finally:
            await router.close()

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_router_close_cancels_inflight_request_instead_of_hanging():
    started = asyncio.Event()

    async def handler(message):
        started.set()
        await asyncio.sleep(60)
        return message

    router = AsyncMailboxRouter(queue_size=2)
    router.register("worker", handler)
    await router.start()
    request = asyncio.create_task(router.request(_msg("worker", "close")))
    await started.wait()
    await router.close()

    assert request.done()
    with pytest.raises(asyncio.CancelledError):
        await request
