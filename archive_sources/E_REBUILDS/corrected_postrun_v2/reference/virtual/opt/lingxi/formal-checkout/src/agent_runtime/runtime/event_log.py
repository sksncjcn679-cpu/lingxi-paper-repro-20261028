"""Durable append-only runtime event journal with hash-chain integrity.

The journal is intentionally transport-agnostic: it records *facts* about a workflow
(task/stage/message/checkpoint events) as canonical JSON lines.  Every record includes
its predecessor hash, making interior deletion, reordering and record tampering detectable.
Without an external signed/anchored tail hash, deleting a coherent suffix cannot be
distinguished from a journal that legitimately ended earlier.

This is an audit/replay primitive rather than a security signature: an attacker who can
rewrite the entire file can recompute the chain.  Protect the directory with normal OS
permissions when events contain sensitive task data.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import time
import threading
import uuid
from typing import Any, Iterable


JOURNAL_VERSION = 1
GENESIS_HASH = "0" * 64
JOURNAL_DURABILITY_MODES = {"sync", "task", "buffered"}
_TASK_BOUNDARY_EVENTS = {"task.completed", "task.failed", "task.paused"}


_PROCESS_LOCK_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


def journal_path_for(root: str | os.PathLike[str], task_id: str) -> Path:
    """Return a traversal-safe deterministic pathname for one task journal.

    External task IDs are data, not path components.  Hashing also avoids platform file-name
    length limits while the original task ID remains present in every journal event.
    """
    identity = str(task_id or "global")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return Path(root) / f"task-{digest}.jsonl"


def _process_lock(path: Path) -> threading.RLock:
    """Return a process-local lock for one journal pathname.

    ``flock`` provides cross-process exclusion on the Linux/openEuler target.  The
    process lock also serializes independent ``EventJournal`` instances in the same
    interpreter and provides a safe fallback on platforms without ``fcntl``.
    """

    key = str(path.resolve())
    with _PROCESS_LOCK_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


@contextmanager
def _exclusive_file_lock(fd: int):
    """Best-effort cross-process exclusive lock for the journal file descriptor."""

    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX compatibility path
        yield
        return

    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


class EventJournalCorruptionError(ValueError):
    """Raised when an event journal cannot pass structural/hash-chain verification."""


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("event payload contains non-finite float")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


@dataclass(frozen=True)
class RuntimeEvent:
    """One immutable event in the workflow journal."""

    version: int
    sequence: int
    event_id: str
    timestamp: float
    event_type: str
    trace_id: str
    task_id: str
    stage: str | None
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplaySummary:
    """Deterministic summary reconstructed only from journal events."""

    task_id: str
    trace_id: str
    started: bool
    terminal_event: str | None
    stages_started: tuple[str, ...]
    stages_completed: tuple[str, ...]
    message_ids: tuple[str, ...]
    checkpoint_ids: tuple[str, ...]


def _canonical_event_payload(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash_event(data_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_event_payload(data_without_hash)).hexdigest()



def _secure_open(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open a journal path without following a symlink when the platform supports it."""
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags, mode)
    except OSError as exc:
        # ELOOP is the common O_NOFOLLOW result.  Use one stable public error so callers
        # do not need platform-specific errno handling.
        if path.is_symlink():
            raise ValueError(f"event journal path must not be a symlink: {path}") from exc
        raise


def _ensure_regular_journal_path(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"event journal path must not be a symlink: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"event journal path must be a regular file: {path}")


class EventJournal:
    """Append-only JSONL journal.

    ``append`` uses one ``O_APPEND`` write per event.  With ``durable=True`` it also
    calls ``fsync`` before returning.  Existing journals are fully verified at open so
    sequence/hash state never continues from a corrupt tail.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        durable: bool | None = None,
        durability: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _ensure_regular_journal_path(self.path)
        # Event payloads can contain task/message metadata.  File mode 0600 alone is
        # insufficient if sibling SQLite/WAL or future sidecar files are added, so keep
        # the containing runtime-data directory private as well.
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        if durability is None:
            # Backward compatibility: durable=False meant buffered/no-fsync.
            durability = "buffered" if durable is False else "sync"
        durability = str(durability).lower()
        if durability not in JOURNAL_DURABILITY_MODES:
            raise ValueError(
                "journal durability must be one of: " + ", ".join(sorted(JOURNAL_DURABILITY_MODES))
            )
        self.durability = durability
        # Compatibility property for older callers. True means per-event synchronous fsync.
        self.durable = durability == "sync"
        self._sequence = 0
        self._previous_hash = GENESIS_HASH
        self._known_size = 0
        self._append_lock = _process_lock(self.path)
        if self.path.exists() and self.path.stat().st_size:
            events = self.verify()
            if events:
                self._sequence = events[-1].sequence
                self._previous_hash = events[-1].event_hash
            self._known_size = self.path.stat().st_size
        else:
            # Best-effort permissions before first append.
            try:
                self.path.touch(mode=0o600, exist_ok=True)
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            try:
                self._known_size = self.path.stat().st_size
            except OSError:
                self._known_size = 0
        # fable10 fix (TOCTOU): the sequence above and the size were read at different moments
        # without the journal lock, so another process could append in between and make a stale
        # sequence look current (observed as a duplicate sequence ~1/30 runs of the multi-writer
        # test).  Mark the view unknown: the first append re-reads the chain under flock.
        self._known_size = -1

    def append(
        self,
        event_type: str,
        *,
        trace_id: str,
        task_id: str,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> RuntimeEvent:
        if not event_type or not isinstance(event_type, str):
            raise ValueError("event_type must be a non-empty string")
        if not trace_id or not task_id:
            raise ValueError("trace_id and task_id must be non-empty")
        event_timestamp = float(time.time() if timestamp is None else timestamp)
        if not math.isfinite(event_timestamp):
            raise ValueError("event timestamp must be finite")

        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
        with self._append_lock:
            fd = _secure_open(self.path, flags, 0o600)
            try:
                with _exclusive_file_lock(fd):
                    current_size = os.fstat(fd).st_size
                    # Another EventJournal instance/process may have appended since this
                    # object last wrote. Refresh from the verified chain before allocating
                    # the next sequence/hash; otherwise two stale writers can both emit
                    # sequence N+1 and permanently corrupt the journal.
                    if current_size != self._known_size:
                        events = self._read_and_verify(verify_chain=True, acquire_lock=False, fd=fd)
                        if events:
                            self._sequence = events[-1].sequence
                            self._previous_hash = events[-1].event_hash
                        else:
                            self._sequence = 0
                            self._previous_hash = GENESIS_HASH
                        self._known_size = current_size

                    sequence = self._sequence + 1
                    data = {
                        "version": JOURNAL_VERSION,
                        "sequence": sequence,
                        "event_id": f"evt_{uuid.uuid4().hex[:16]}",
                        "timestamp": event_timestamp,
                        "event_type": event_type,
                        "trace_id": str(trace_id),
                        "task_id": str(task_id),
                        "stage": stage,
                        "payload": _json_safe(payload or {}),
                        "previous_hash": self._previous_hash,
                    }
                    event_hash = _hash_event(data)
                    document = dict(data)
                    document["event_hash"] = event_hash
                    raw = _canonical_event_payload(document) + b"\n"

                    written = 0
                    while written < len(raw):
                        n = os.write(fd, raw[written:])
                        if n <= 0:
                            raise OSError("short write while appending event journal")
                        written += n
                    if self.durability == "sync" or (
                        self.durability == "task" and event_type in _TASK_BOUNDARY_EVENTS
                    ):
                        os.fsync(fd)

                    event = RuntimeEvent(event_hash=event_hash, **data)
                    self._sequence = sequence
                    self._previous_hash = event_hash
                    self._known_size = os.fstat(fd).st_size
                    return event
            finally:
                os.close(fd)

    def flush(self) -> None:
        """Force all previously written journal bytes to stable storage best-effort.

        ``task`` mode calls the equivalent fsync on terminal task-boundary events;
        ``buffered`` callers may invoke this explicitly when they choose a durability
        boundary.
        """
        _ensure_regular_journal_path(self.path)
        if not self.path.exists():
            return
        # Windows rejects fsync on a read-only descriptor.  The journal is owned by
        # this process and append-created, so use a writable descriptor for the
        # explicit durability boundary while keeping the read path read-only.
        fd = _secure_open(self.path, os.O_RDWR)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def read(self) -> list[RuntimeEvent]:
        return self._read_and_verify(verify_chain=False)

    def verify(self) -> list[RuntimeEvent]:
        """Return all events if the full sequence/hash chain is valid."""
        return self._read_and_verify(verify_chain=True)

    def _read_and_verify(
        self, *, verify_chain: bool, acquire_lock: bool = True, fd: int | None = None
    ) -> list[RuntimeEvent]:
        _ensure_regular_journal_path(self.path)
        if not self.path.exists():
            return []
        if acquire_lock:
            # Readers participate in the same lock protocol as appenders so opening a
            # journal while another process is between partial ``os.write`` calls cannot
            # misclassify an in-flight record as permanent corruption.
            with self._append_lock:
                fd = _secure_open(self.path, os.O_RDONLY)
                try:
                    with _exclusive_file_lock(fd):
                        return self._read_and_verify(
                            verify_chain=verify_chain, acquire_lock=False, fd=fd
                        )
                finally:
                    os.close(fd)

        events: list[RuntimeEvent] = []
        previous = GENESIS_HASH
        expected_sequence = 1
        owned_fd = False
        if fd is None:
            fd = _secure_open(self.path, os.O_RDONLY)
            owned_fd = True
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(fd), "r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        document = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise EventJournalCorruptionError(
                            f"invalid JSON at journal line {line_no}"
                        ) from exc
                    try:
                        event = RuntimeEvent(**document)
                    except TypeError as exc:
                        raise EventJournalCorruptionError(
                            f"invalid event schema at journal line {line_no}"
                        ) from exc
                    if verify_chain:
                        if event.version != JOURNAL_VERSION:
                            raise EventJournalCorruptionError(
                                f"unsupported journal version at line {line_no}: {event.version}"
                            )
                        if event.sequence != expected_sequence:
                            raise EventJournalCorruptionError(
                                f"non-contiguous sequence at line {line_no}: {event.sequence}"
                            )
                        if not hmac.compare_digest(event.previous_hash, previous):
                            raise EventJournalCorruptionError(
                                f"previous hash mismatch at line {line_no}"
                            )
                        raw = event.to_dict()
                        declared = raw.pop("event_hash")
                        actual = _hash_event(raw)
                        if not hmac.compare_digest(declared, actual):
                            raise EventJournalCorruptionError(
                                f"event hash mismatch at line {line_no}"
                            )
                    events.append(event)
                    previous = event.event_hash
                    expected_sequence += 1
        except UnicodeError as exc:
            raise EventJournalCorruptionError("journal is not valid UTF-8") from exc
        finally:
            if owned_fd and fd is not None:
                os.close(fd)
        return events

    def events_for_task(self, task_id: str) -> list[RuntimeEvent]:
        return [event for event in self.verify() if event.task_id == task_id]

    def replay(self, task_id: str, *, trace_id: str | None = None) -> ReplaySummary:
        events = self.events_for_task(task_id)
        if not events:
            raise KeyError(f"no events found for task: {task_id}")
        if trace_id is None:
            # Same deterministic task may be executed repeatedly in experiments.  Replay
            # defaults to the most recent trace instead of conflating independent runs.
            trace_id = events[-1].trace_id
        events = [event for event in events if event.trace_id == trace_id]
        if not events:
            raise KeyError(f"trace {trace_id!r} not found for task {task_id!r}")
        stages_started: list[str] = []
        stages_completed: list[str] = []
        message_ids: list[str] = []
        checkpoint_ids: list[str] = []
        terminal: str | None = None
        replay_trace_id = events[0].trace_id
        started = False
        for event in events:
            if event.trace_id != replay_trace_id:
                raise EventJournalCorruptionError(
                    f"task {task_id} spans multiple trace ids in one replay"
                )
            if event.event_type == "task.started":
                started = True
            elif event.event_type == "stage.started" and event.stage:
                stages_started.append(event.stage)
            elif event.event_type == "stage.completed" and event.stage:
                stages_completed.append(event.stage)
            elif event.event_type == "message.sent":
                message_id = event.payload.get("message_id")
                if isinstance(message_id, str):
                    message_ids.append(message_id)
            elif event.event_type == "checkpoint.saved":
                checkpoint_id = event.payload.get("checkpoint_id")
                if isinstance(checkpoint_id, str):
                    checkpoint_ids.append(checkpoint_id)
            elif event.event_type in {
                "task.completed",
                "task.failed",
                "task.paused",
            }:
                terminal = event.event_type
        return ReplaySummary(
            task_id=task_id,
            trace_id=replay_trace_id,
            started=started,
            terminal_event=terminal,
            stages_started=tuple(stages_started),
            stages_completed=tuple(stages_completed),
            message_ids=tuple(message_ids),
            checkpoint_ids=tuple(checkpoint_ids),
        )


def replay_events(
    events: Iterable[RuntimeEvent],
    task_id: str,
    *,
    trace_id: str | None = None,
) -> ReplaySummary:
    """Replay an in-memory event sequence using ``EventJournal.replay`` semantics.

    Repeated executions may legitimately share one deterministic ``task_id``.  Like the
    file-backed API, the in-memory helper therefore defaults to the most recent trace
    instead of treating multiple traces as corruption.
    """
    selected = [event for event in events if event.task_id == task_id]
    if not selected:
        raise KeyError(task_id)
    if trace_id is None:
        trace_id = selected[-1].trace_id
    selected = [event for event in selected if event.trace_id == trace_id]
    if not selected:
        raise KeyError(f"trace {trace_id!r} not found for task {task_id!r}")

    stages_started = tuple(
        e.stage for e in selected if e.event_type == "stage.started" and e.stage is not None
    )
    stages_completed = tuple(
        e.stage for e in selected if e.event_type == "stage.completed" and e.stage is not None
    )
    message_ids = tuple(
        str(e.payload["message_id"])
        for e in selected
        if e.event_type == "message.sent" and "message_id" in e.payload
    )
    checkpoint_ids = tuple(
        str(e.payload["checkpoint_id"])
        for e in selected
        if e.event_type == "checkpoint.saved" and "checkpoint_id" in e.payload
    )
    terminal = next(
        (
            e.event_type
            for e in reversed(selected)
            if e.event_type in {"task.completed", "task.failed", "task.paused"}
        ),
        None,
    )
    trace_ids = {e.trace_id for e in selected}
    if len(trace_ids) != 1:
        raise EventJournalCorruptionError("replay trace contains multiple trace ids")
    return ReplaySummary(
        task_id=task_id,
        trace_id=next(iter(trace_ids)),
        started=any(e.event_type == "task.started" for e in selected),
        terminal_event=terminal,
        stages_started=stages_started,
        stages_completed=stages_completed,
        message_ids=message_ids,
        checkpoint_ids=checkpoint_ids,
    )


__all__ = [
    "EventJournal",
    "EventJournalCorruptionError",
    "RuntimeEvent",
    "ReplaySummary",
    "replay_events",
    "JOURNAL_VERSION",
    "JOURNAL_DURABILITY_MODES",
    "journal_path_for",
]
