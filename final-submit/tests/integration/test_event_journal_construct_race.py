"""fable10: journal writers constructed concurrently with other writers' appends (TOCTOU)."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="fork unavailable")


def test_concurrent_construct_and_append_keeps_one_valid_chain(tmp_path):
    from agent_runtime.runtime.event_log import EventJournal

    path = tmp_path / "events.jsonl"
    EventJournal(path, durability="buffered").append("seed", trace_id="t", task_id="k")
    pids = []
    for w in range(8):
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child
            code = 0
            try:
                for i in range(15):
                    # a fresh instance per append maximises construct/append interleavings
                    EventJournal(path, durability="buffered").append("e", trace_id="t", task_id=f"w{w}", payload={"i": i})
            except Exception:
                code = 1
            os._exit(code)
        pids.append(pid)
    codes = [os.waitpid(p, 0)[1] for p in pids]
    assert all(c == 0 for c in codes)
    events = EventJournal(path).verify()
    assert [e.sequence for e in events] == list(range(1, 1 + 1 + 8 * 15))
