"""Idempotency controls for duplicate message delivery.

The in-memory cache is the zero-I/O default. ``PersistentIdempotencyCache`` stores
*completed* request results in SQLite so a fresh process can replay a previously
completed idempotent request. It is not a distributed transaction/lease system and does
not claim exactly-once execution for two concurrent first deliveries.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Protocol

from agent_runtime.protocol.message import AgentMessage


def fingerprint(message: AgentMessage) -> str:
    """Return a stable fingerprint for one *logical* idempotent request.

    Transport-only identity (message/trace IDs, timestamp, deadline) is deliberately
    excluded so a redelivery maps to the same completed result.  Business-affecting
    fields are always included, even when callers supply an ``idempotency_key``.
    Reusing the same key with a different payload/reference/target therefore cannot
    silently replay a stale result.
    """

    logical = {
        "protocol_version": message.protocol_version,
        "task_id": message.task_id,
        "step_id": message.step_id,
        "source_agent": message.source_agent,
        "target_agent": message.target_agent,
        "action_type": message.action_type.value,
        "capability": message.capability,
        "input_parameters": message.input_parameters,
        "dependencies": message.dependencies,
        "artifact_references": message.artifact_references,
        "evidence_references": message.evidence_references,
        "state_reference": message.state_reference,
        "idempotency_key": message.idempotency_key,
    }
    payload = json.dumps(
        logical, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    key = message.idempotency_key or "auto"
    return f"{message.task_id}|{message.step_id}|{message.action_type.value}|{key}|{digest[:24]}"


class IdempotencyStore(Protocol):
    def seen(self, message: AgentMessage) -> bool: ...
    def get(self, message: AgentMessage) -> AgentMessage | None: ...
    def remember(self, request: AgentMessage, result: AgentMessage) -> None: ...
    def clear(self) -> None: ...


class IdempotencyCache:
    """Process-local completed-result cache."""

    persistent = False

    def __init__(self) -> None:
        self._store: dict[str, AgentMessage] = {}

    def seen(self, message: AgentMessage) -> bool:
        return fingerprint(message) in self._store

    def get(self, message: AgentMessage) -> AgentMessage | None:
        return self._store.get(fingerprint(message))

    def remember(self, request: AgentMessage, result: AgentMessage) -> None:
        self._store[fingerprint(request)] = result

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


class PersistentIdempotencyCache:
    """SQLite cache for completed idempotent requests across process restarts.

    ``ttl_sec`` bounds how long completed results remain eligible for replay. The DB is
    WAL-backed and safe for multiple readers/writers, but two processes that both observe
    a *missing* key may still execute the side effect concurrently before either stores a
    result. Use an external transactional system when first-delivery exactly-once is
    required.
    """

    persistent = True

    def __init__(self, path: str | os.PathLike[str], *, ttl_sec: float = 24 * 3600) -> None:
        if not math.isfinite(ttl_sec) or ttl_sec <= 0:
            raise ValueError("idempotency ttl_sec must be a finite positive number")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # SQLite WAL/SHM sidecars commonly inherit 0644 from the process umask.
        # A private directory is therefore the reliable boundary for cached task/results.
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self.ttl_sec = float(ttl_sec)
        self.conn = sqlite3.connect(self.path, timeout=5.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS completed_requests (
                fingerprint TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                result_json TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_completed_requests_expiry ON completed_requests(expires_at)"
        )
        self.conn.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _row(self, message: AgentMessage):
        key = fingerprint(message)
        row = self.conn.execute(
            "SELECT result_json,expires_at FROM completed_requests WHERE fingerprint=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        if float(row[1]) <= time.time():
            with self.conn:
                self.conn.execute("DELETE FROM completed_requests WHERE fingerprint=?", (key,))
            return None
        return row

    def seen(self, message: AgentMessage) -> bool:
        # ``seen`` means a replayable completed result exists, not merely that a SQLite
        # row is present.  Corrupt rows are removed by ``get`` and must not be reported
        # as valid idempotency hits.
        return self.get(message) is not None

    def get(self, message: AgentMessage) -> AgentMessage | None:
        row = self._row(message)
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
            return AgentMessage.model_validate(payload)
        except (json.JSONDecodeError, ValueError, TypeError):
            # Corrupt cache entries are safer to ignore than to replay.
            with self.conn:
                self.conn.execute(
                    "DELETE FROM completed_requests WHERE fingerprint=?", (fingerprint(message),)
                )
            return None

    def remember(self, request: AgentMessage, result: AgentMessage) -> None:
        now = time.time()
        raw = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        with self.conn:
            self.conn.execute(
                """INSERT INTO completed_requests(fingerprint,task_id,created_at,expires_at,result_json)
                VALUES(?,?,?,?,?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    task_id=excluded.task_id,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at,
                    result_json=excluded.result_json""",
                (fingerprint(request), request.task_id, now, now + self.ttl_sec, raw),
            )

    def prune(self, *, now: float | None = None) -> int:
        cutoff = time.time() if now is None else float(now)
        with self.conn:
            cur = self.conn.execute("DELETE FROM completed_requests WHERE expires_at<=?", (cutoff,))
        return int(cur.rowcount)

    def clear(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM completed_requests")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PersistentIdempotencyCache":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __len__(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM completed_requests").fetchone()[0])


def rebind_cached_result(
    cached: AgentMessage,
    request: AgentMessage,
    *,
    trace_id: str,
) -> AgentMessage:
    """Rebind a cached logical result to the current delivery lineage.

    The response message ID is intentionally stable (same logical idempotent result), while
    trace/context/parent/deadline are refreshed for observability of this delivery.
    """
    return cached.model_copy(
        update={
            "trace_id": trace_id,
            "task_id": request.task_id,
            "step_id": request.step_id,
            "context_id": request.context_id,
            "parent_message_id": request.message_id,
            "causation_id": request.message_id,
            "sequence_no": (request.sequence_no + 1) if request.sequence_no is not None else None,
            "deadline_at": request.deadline_at,
            "target_agent": request.source_agent or "orchestrator",
        }
    )


__all__ = [
    "IdempotencyStore",
    "IdempotencyCache",
    "PersistentIdempotencyCache",
    "fingerprint",
    "rebind_cached_result",
]
