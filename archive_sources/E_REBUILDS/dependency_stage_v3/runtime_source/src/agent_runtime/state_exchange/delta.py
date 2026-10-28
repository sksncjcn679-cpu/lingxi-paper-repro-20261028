"""Versioned delta state for low-overhead multi-agent state propagation.

This module complements ``StateExchange`` rather than replacing it.  Numerical payloads still use
``state://`` / shared-memory references; JSON-like control-plane state (plan DAGs, routing metadata,
review annotations) can be stored as immutable snapshots and advanced with compact deltas.

Design properties:
- content-addressed immutable versions;
- compact add/remove/replace patches;
- base-hash + target-hash verification;
- automatic full-snapshot fallback when a patch is not economical;
- bounded delta-chain depth;
- optimistic compare-and-update conflict detection for concurrent writers.

The implementation intentionally uses a small deterministic patch format instead of claiming CRDT
semantics.  Conflicting concurrent writes are detected, not silently merged.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
PathPart: TypeAlias = str | int


class StateConflictError(RuntimeError):
    """Raised when an optimistic update is based on a stale head."""


class StateIntegrityError(RuntimeError):
    """Raised when a snapshot/delta chain fails hash verification."""


@dataclass(frozen=True)
class PatchOp:
    op: Literal["add", "remove", "replace"]
    path: tuple[PathPart, ...]
    value: JsonValue | None = None

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"o": self.op, "p": list(self.path)}
        if self.op != "remove":
            payload["v"] = self.value
        return payload


@dataclass(frozen=True)
class VersionedStateRef:
    uri: str
    kind: Literal["snapshot", "delta"]
    content_hash: str
    base_uri: str | None
    base_hash: str | None
    logical_bytes: int
    materialized_bytes: int
    delta_ops: int
    depth: int
    deduplicated: bool = False

    @property
    def saving_ratio(self) -> float:
        if self.logical_bytes <= 0:
            return 0.0
        return 1.0 - (self.materialized_bytes / self.logical_bytes)


@dataclass
class _Entry:
    ref: VersionedStateRef
    snapshot: JsonValue | None = None
    ops: tuple[PatchOp, ...] = ()
    # fable15: compact physical encoding (the stored representation); decoded on resolve.
    blob: bytes | None = None


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Stable UTF-8 JSON representation used for hashing and byte accounting."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_hash(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _patch_bytes(ops: tuple[PatchOp, ...]) -> bytes:
    return json.dumps(
        [op.to_wire() for op in ops],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def diff_state(old: JsonValue, new: JsonValue) -> tuple[PatchOp, ...]:
    """Compute a deterministic structural patch.

    Lists are diffed element-wise when possible; removals happen from the tail toward the front so
    indices stay valid during replay.  The format is intentionally small and sufficient for the
    plan/state structures used by the runtime.
    """

    ops: list[PatchOp] = []

    def walk(a: JsonValue, b: JsonValue, path: tuple[PathPart, ...]) -> None:
        if type(a) is not type(b):
            ops.append(PatchOp("replace", path, deepcopy(b)))
            return

        if isinstance(a, dict) and isinstance(b, dict):
            a_keys = set(a)
            b_keys = set(b)
            for key in sorted(a_keys - b_keys):
                ops.append(PatchOp("remove", path + (key,)))
            for key in sorted(b_keys - a_keys):
                ops.append(PatchOp("add", path + (key,), deepcopy(b[key])))
            for key in sorted(a_keys & b_keys):
                walk(a[key], b[key], path + (key,))
            return

        if isinstance(a, list) and isinstance(b, list):
            shared = min(len(a), len(b))
            for idx in range(shared):
                walk(a[idx], b[idx], path + (idx,))
            for idx in range(len(a) - 1, len(b) - 1, -1):
                ops.append(PatchOp("remove", path + (idx,)))
            for idx in range(shared, len(b)):
                ops.append(PatchOp("add", path + (idx,), deepcopy(b[idx])))
            return

        if a != b:
            ops.append(PatchOp("replace", path, deepcopy(b)))

    walk(old, new, ())
    return tuple(ops)


def apply_patch(base: JsonValue, ops: tuple[PatchOp, ...]) -> JsonValue:
    """Apply a patch to a deep copy of ``base`` and return the new immutable logical value."""

    root: JsonValue = deepcopy(base)

    def parent_for(path: tuple[PathPart, ...]) -> tuple[Any, PathPart]:
        if not path:
            raise StateIntegrityError("root operation has no parent")
        node: Any = root
        for part in path[:-1]:
            if isinstance(part, int):
                if not isinstance(node, list) or part < 0 or part >= len(node):
                    raise StateIntegrityError(f"invalid list path: {path}")
                node = node[part]
            else:
                if not isinstance(node, dict) or part not in node:
                    raise StateIntegrityError(f"invalid object path: {path}")
                node = node[part]
        return node, path[-1]

    for op in ops:
        if not op.path:
            if op.op == "remove":
                raise StateIntegrityError("cannot remove root state")
            root = deepcopy(op.value)
            continue

        parent, key = parent_for(op.path)
        if isinstance(key, int):
            if not isinstance(parent, list):
                raise StateIntegrityError(f"list operation on non-list: {op.path}")
            if op.op == "add":
                if key < 0 or key > len(parent):
                    raise StateIntegrityError(f"invalid list add index: {op.path}")
                parent.insert(key, deepcopy(op.value))
            elif op.op == "remove":
                if key < 0 or key >= len(parent):
                    raise StateIntegrityError(f"invalid list remove index: {op.path}")
                parent.pop(key)
            else:
                if key < 0 or key >= len(parent):
                    raise StateIntegrityError(f"invalid list replace index: {op.path}")
                parent[key] = deepcopy(op.value)
        else:
            if not isinstance(parent, dict):
                raise StateIntegrityError(f"object operation on non-object: {op.path}")
            if op.op == "remove":
                if key not in parent:
                    raise StateIntegrityError(f"missing object key: {op.path}")
                del parent[key]
            else:
                parent[key] = deepcopy(op.value)

    return root


class VersionedStateLedger:
    """Immutable snapshot + delta ledger with optimistic concurrency control."""

    def __init__(
        self,
        *,
        namespace: str = "global",
        delta_ratio_threshold: float = 0.90,
        max_delta_depth: int = 8,
    ) -> None:
        if not 0.0 < delta_ratio_threshold <= 1.0:
            raise ValueError("delta_ratio_threshold must be in (0, 1]")
        if max_delta_depth < 1:
            raise ValueError("max_delta_depth must be >= 1")
        self.namespace = namespace
        self.delta_ratio_threshold = float(delta_ratio_threshold)
        self.max_delta_depth = int(max_delta_depth)
        self._entries: dict[str, _Entry] = {}
        self._head_uri: str | None = None
        # fable8: verified-resolution cache.  Versions are content-addressed and immutable, so a
        # value whose SHA-256 was verified once can be served again (as a private deep copy)
        # without replaying the delta chain and re-hashing every level (previously O(depth^2)
        # per read).  Keyed by the entry object identity as well, so replacing an entry
        # invalidates it; returned values are always copies, so callers cannot poison it.
        self._verified: dict[str, tuple[Any, JsonValue]] = {}
        self.resolve_cache_hits = 0
        import os as _os

        self.compact = _os.environ.get("AGENT_STATE_COMPACT", "1").strip().lower() not in {"0", "false", "no", "off"}
        self._cache_enabled = _os.environ.get("AGENT_STATE_RESOLVE_CACHE", "1").strip().lower() not in {"0", "false", "no", "off"}

    @property
    def head_uri(self) -> str | None:
        return self._head_uri

    def put_snapshot(self, value: JsonValue) -> VersionedStateRef:
        payload = canonical_json_bytes(value)
        digest = hashlib.sha256(payload).hexdigest()
        uri = self._uri(digest)
        existing = self._entries.get(uri)
        if existing is not None:
            # ``put_snapshot`` is also an explicit head-selection operation.  A dedup hit
            # must therefore move the optimistic-concurrency head just like a newly
            # materialized snapshot; otherwise compare_and_update() sees a stale head.
            self._head_uri = uri
            return VersionedStateRef(
                **{**existing.ref.__dict__, "materialized_bytes": 0, "deduplicated": True}
            )

        ref = VersionedStateRef(
            uri=uri,
            kind="snapshot",
            content_hash=digest,
            base_uri=None,
            base_hash=None,
            logical_bytes=len(payload),
            materialized_bytes=len(payload),
            delta_ops=0,
            depth=0,
        )
        if self.compact:
            from agent_runtime.state_exchange.compact import encode_snapshot

            blob = encode_snapshot(value, self.namespace)
            ref = VersionedStateRef(**{**ref.__dict__, "materialized_bytes": len(blob)})
            self._entries[uri] = _Entry(ref=ref, blob=blob)
        else:
            self._entries[uri] = _Entry(ref=ref, snapshot=deepcopy(value))
        self._head_uri = uri
        return ref

    def update(
        self,
        base_uri: str,
        new_value: JsonValue,
        *,
        require_current_head: bool = False,
    ) -> VersionedStateRef:
        if require_current_head and self._head_uri != base_uri:
            raise StateConflictError(
                f"stale state base: expected head={self._head_uri}, got={base_uri}"
            )
        base_entry = self._entries.get(base_uri)
        if base_entry is None:
            raise KeyError(f"unknown base state: {base_uri}")
        base = self.resolve(base_uri)
        target_payload = canonical_json_bytes(new_value)
        target_hash = hashlib.sha256(target_payload).hexdigest()
        target_uri = self._uri(target_hash)
        if target_uri == base_uri:
            return VersionedStateRef(
                **{**base_entry.ref.__dict__, "materialized_bytes": 0, "deduplicated": True}
            )
        existing = self._entries.get(target_uri)
        if existing is not None:
            self._head_uri = target_uri
            return VersionedStateRef(
                **{**existing.ref.__dict__, "materialized_bytes": 0, "deduplicated": True}
            )

        ops = diff_state(base, new_value)
        # The delta-vs-snapshot decision uses the canonical JSON sizes (cheap, C-accelerated);
        # only the chosen representation is then encoded in compact form.
        json_delta = _patch_bytes(ops)
        next_depth = base_entry.ref.depth + 1
        use_delta = (
            bool(ops)
            and next_depth <= self.max_delta_depth
            and len(json_delta) < len(target_payload) * self.delta_ratio_threshold
        )
        if self.compact:
            from agent_runtime.state_exchange.compact import encode_ops, encode_snapshot

            delta_payload = encode_ops(ops, self.namespace) if use_delta else b""
            snapshot_blob = b"" if use_delta else encode_snapshot(new_value, self.namespace)
        else:
            delta_payload = json_delta
            snapshot_blob = target_payload

        if use_delta:
            ref = VersionedStateRef(
                uri=target_uri,
                kind="delta",
                content_hash=target_hash,
                base_uri=base_uri,
                base_hash=base_entry.ref.content_hash,
                logical_bytes=len(target_payload),
                materialized_bytes=len(delta_payload),
                delta_ops=len(ops),
                depth=next_depth,
            )
            self._entries[target_uri] = _Entry(ref=ref, blob=delta_payload) if self.compact else _Entry(ref=ref, ops=ops)
        else:
            ref = VersionedStateRef(
                uri=target_uri,
                kind="snapshot",
                content_hash=target_hash,
                base_uri=None,
                base_hash=None,
                logical_bytes=len(target_payload),
                materialized_bytes=len(snapshot_blob),
                delta_ops=0,
                depth=0,
            )
            self._entries[target_uri] = _Entry(ref=ref, blob=snapshot_blob) if self.compact else _Entry(ref=ref, snapshot=deepcopy(new_value))

        self._head_uri = target_uri
        return ref

    def _verify_on_write(self, uri: str, value: JsonValue, payload: bytes, base: JsonValue | None = None) -> None:
        """fable15: decode the just-stored compact bytes once and check they reproduce the exact
        canonical content; then serve reads from the verified cache (same guarantee as verify-on-
        first-read, without re-decoding and re-applying the delta chain later)."""
        entry = self._entries[uri]
        if entry.blob is None or not self._cache_enabled:
            return
        from agent_runtime.state_exchange.compact import decode_ops, decode_snapshot

        if entry.ref.kind == "snapshot":
            got = decode_snapshot(entry.blob, self.namespace)
        else:
            got = apply_patch(base, decode_ops(entry.blob, self.namespace))
        if canonical_json_bytes(got) != payload:
            raise StateIntegrityError(f"compact encoding does not reproduce the state: {uri}")
        if len(self._verified) >= 256:
            self._verified.pop(next(iter(self._verified)))
        self._verified[uri] = ((id(entry), id(entry.blob)), got)

    def compare_and_update(self, expected_uri: str, new_value: JsonValue) -> VersionedStateRef:
        return self.update(expected_uri, new_value, require_current_head=True)

    def resolve(self, uri: str) -> JsonValue:
        entry = self._entries.get(uri)
        if entry is None:
            raise KeyError(f"unknown state: {uri}")
        cached = self._verified.get(uri) if self._cache_enabled else None
        if cached is not None and cached[0] == (id(entry), id(entry.blob)):
            self.resolve_cache_hits += 1
            return deepcopy(cached[1])
        ref = entry.ref
        if ref.kind == "snapshot":
            if entry.blob is not None:
                from agent_runtime.state_exchange.compact import decode_snapshot

                try:
                    value = decode_snapshot(entry.blob, self.namespace)
                except Exception as exc:  # noqa: BLE001
                    raise StateIntegrityError(f"snapshot payload undecodable: {uri}") from exc
            elif entry.snapshot is None:
                raise StateIntegrityError(f"snapshot payload missing: {uri}")
            else:
                value = deepcopy(entry.snapshot)
        else:
            if ref.base_uri is None or ref.base_hash is None:
                raise StateIntegrityError(f"delta base metadata missing: {uri}")
            base_entry = self._entries.get(ref.base_uri)
            if base_entry is None or base_entry.ref.content_hash != ref.base_hash:
                raise StateIntegrityError(f"delta base hash mismatch: {uri}")
            ops = entry.ops
            if entry.blob is not None:
                from agent_runtime.state_exchange.compact import decode_ops

                try:
                    ops = decode_ops(entry.blob, self.namespace)
                except Exception as exc:  # noqa: BLE001
                    raise StateIntegrityError(f"delta payload undecodable: {uri}") from exc
            value = apply_patch(self.resolve(ref.base_uri), ops)

        if content_hash(value) != ref.content_hash:
            raise StateIntegrityError(f"state content hash mismatch: {uri}")
        if self._cache_enabled:
            if len(self._verified) >= 256:
                self._verified.pop(next(iter(self._verified)))
            self._verified[uri] = ((id(entry), id(entry.blob)), deepcopy(value))
        return value

    def get_ref(self, uri: str) -> VersionedStateRef | None:
        entry = self._entries.get(uri)
        return entry.ref if entry is not None else None

    def chain(self, uri: str) -> list[VersionedStateRef]:
        refs: list[VersionedStateRef] = []
        seen: set[str] = set()
        current = uri
        while True:
            if current in seen:
                raise StateIntegrityError("cycle in state delta chain")
            seen.add(current)
            entry = self._entries.get(current)
            if entry is None:
                raise KeyError(f"unknown state: {current}")
            refs.append(entry.ref)
            if entry.ref.kind == "snapshot":
                break
            if entry.ref.base_uri is None:
                raise StateIntegrityError("delta missing base uri")
            current = entry.ref.base_uri
        refs.reverse()
        return refs

    def _uri(self, digest: str) -> str:
        return f"vstate://{self.namespace}/{digest[:32]}"


__all__ = [
    "JsonValue",
    "PatchOp",
    "StateConflictError",
    "StateIntegrityError",
    "VersionedStateLedger",
    "VersionedStateRef",
    "apply_patch",
    "canonical_json_bytes",
    "content_hash",
    "diff_state",
]
