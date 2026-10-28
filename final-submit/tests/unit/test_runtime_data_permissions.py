from __future__ import annotations

import os
import stat

import pytest

from agent_runtime.memory.store import MemoryStore
from agent_runtime.protocol.idempotency import PersistentIdempotencyCache
from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.runtime.checkpoint import FileCheckpointStore
from agent_runtime.runtime.event_log import EventJournal


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_sensitive_runtime_data_directories_are_private(tmp_path):
    checkpoint_dir = tmp_path / "checkpoints"
    FileCheckpointStore(checkpoint_dir)

    event_dir = tmp_path / "events"
    EventJournal(event_dir / "events.jsonl", durable=False)

    idem_dir = tmp_path / "idempotency"
    cache = PersistentIdempotencyCache(idem_dir / "completed.sqlite3")

    memory_dir = tmp_path / "memory"
    memory = MemoryStore(MockEmbedding(dim=256), db_path=str(memory_dir / "memory.sqlite3"))
    try:
        assert _mode(checkpoint_dir) & 0o077 == 0
        assert _mode(event_dir) & 0o077 == 0
        assert _mode(idem_dir) & 0o077 == 0
        assert _mode(memory_dir) & 0o077 == 0
        assert _mode(idem_dir / "completed.sqlite3") & 0o077 == 0
        assert _mode(memory_dir / "memory.sqlite3") & 0o077 == 0
    finally:
        memory.close()
        cache.close()
