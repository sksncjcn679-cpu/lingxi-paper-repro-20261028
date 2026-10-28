"""Protocol 1.4 — Context-Predictive Header Compression (CPHC) with negotiated *routines*.

Motivation
----------
After Protocol 1.3 removed static task fields, relative ids and absolute timestamps, the
remaining per-frame cost of a structured Agent message is dominated by its *routing header*:
``st`` (step), ``s``/``t`` (source/target agent atoms), ``a`` (action), ``c`` (capability),
``sta`` (status), ``sq`` (sequence) and ``pm``/``ca`` (parent/causation links).  In a
multi-Agent pipeline these fields are almost entirely *predictable*: a reply mirrors its request,
and the request that follows a reply is fixed by the collaboration routine
(Planner → Retriever → Executor → Reviewer).

Two ideas from the literature are combined here:

* **ROHC** (RFC 3095 / RFC 5795, Robust Header Compression): compressor and decompressor keep a
  shared *context* and transmit only the header fields that differ from what the context
  predicts.  Mis-predictions are always correct, merely more expensive.
* **Agora** (Marro et al., 2024, "A Scalable Communication Protocol for Networks of LLMs"):
  frequently repeated interaction patterns are captured once as a *protocol document* identified
  by its hash; later sessions refer to the hash instead of re-describing the pattern.

A :class:`Routine` is exactly such a document: the observed transition table
``previous header → next header``.  It is learned from completed sessions, identified by a
SHA-256 digest, uploaded to the router once (bytes and tokens are charged to the session that
uploads it) and afterwards referenced by digest in the connection hello.

Correctness contract
--------------------
Compression is *lossless by construction*: the encoder emits every header field whose actual
value differs from the prediction, plus an ``nx`` list naming predicted fields that are absent
in the real message.  Encoder and decoder update their contexts from the *uncompressed* frame, so
a wrong prediction never desynchronises the peers.  The router still decodes, re-encodes and
returns a SHA-256 digest for every frame, which the client verifies.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

# Header fields subject to routine prediction (Protocol-1.3 compact keys).
ROUTINE_KEYS: tuple[str, ...] = ("st", "s", "t", "a", "c", "sta", "cf")  # fable15: + confidence
# Fields predicted arithmetically from the previous frame.
LINK_KEYS: tuple[str, ...] = ("sq", "pm", "ca")
from agent_runtime import PROTOCOL_VERSION as _PROTOCOL_VERSION  # noqa: E402
ABSENT_KEY = "nx"
# fable15: payload fields identical to the previous frame's are sent as {"eq": [field, ...]}.
SAME_KEY = "eq"
PAYLOAD_KEYS: tuple[str, ...] = ("rs", "ip", "ar", "er", "sr", "e", "dep")
_MISSING = object()


def _header_key(frame: dict[str, Any] | None) -> str:
    """Canonical transition key of a (1.3-level) frame; ``"^"`` denotes session start."""
    if frame is None:
        return "^"
    return json.dumps([frame.get(k) for k in ROUTINE_KEYS], separators=(",", ":"))


@dataclass
class Routine:
    """Immutable-by-convention transition table ``prev-header-key -> next header``."""

    transitions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Hot-path lookup keyed by header tuples (avoids a json.dumps per predicted frame).
        self._fast: dict[Any, dict[str, Any]] = {}
        for key, header in self.transitions.items():
            self._fast["^" if key == "^" else tuple(json.loads(key))] = header

    def lookup(self, prev: dict[str, Any] | None) -> dict[str, Any] | None:
        if prev is None:
            return self._fast.get("^")
        return self._fast.get(tuple(prev.get(k) for k in ROUTINE_KEYS))

    def to_json(self) -> str:
        # Routines are immutable by convention; the canonical document is computed once
        # (fable8: it was re-serialised ~20x per session for digest/upload/accounting).
        doc = self.__dict__.get("_doc")
        if doc is None:
            doc = json.dumps(
                {"cphc": 1, "tr": self.transitions}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            self.__dict__["_doc"] = doc
        return doc

    @property
    def digest(self) -> str:
        d = self.__dict__.get("_digest")
        if d is None:
            d = hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()[:24]
            self.__dict__["_digest"] = d
        return d

    @classmethod
    def from_json(cls, text: str) -> "Routine":
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("cphc") != 1 or not isinstance(data.get("tr"), dict):
            raise ValueError("invalid routine document")
        transitions: dict[str, dict[str, Any]] = {}
        for key, header in data["tr"].items():
            if not isinstance(key, str) or not isinstance(header, dict):
                raise ValueError("invalid routine transition")
            unknown = set(header) - set(ROUTINE_KEYS)
            if unknown:
                raise ValueError(f"routine header has unknown keys: {sorted(unknown)}")
            transitions[key] = dict(header)
        return cls(transitions)

    @classmethod
    def learn(cls, frames: list[dict[str, Any]], base: "Routine | None" = None) -> "Routine":
        """Merge the transitions observed in one session into ``base`` (latest wins)."""
        transitions = dict(base.transitions) if base is not None else {}
        prev: dict[str, Any] | None = None
        for frame in frames:
            transitions[_header_key(prev)] = {k: frame[k] for k in ROUTINE_KEYS if k in frame}
            prev = frame
        return cls(transitions)


@dataclass
class PredictorContext:
    """One side (encoder or decoder) of the shared CPHC context."""

    routine: Routine | None = None
    prev: dict[str, Any] | None = None
    last_sq: int | None = None
    observed: list[dict[str, Any]] = field(default_factory=list)
    predicted_fields: int = 0
    explicit_fields: int = 0
    prev_payload: dict[str, Any] = field(default_factory=dict)
    copied_fields: int = 0

    def predict(self) -> dict[str, Any]:
        """Predicted 1.3-level header for the next frame."""
        guess: dict[str, Any] = {}
        prev = self.prev
        learned = self.routine.lookup(prev) if self.routine is not None else None
        if learned is not None:
            guess.update(learned)
        elif prev is not None and "sta" not in prev:
            # Generic request/reply rule (needs no routine): a reply mirrors its request.
            for key in ("st", "a", "c"):
                if key in prev:
                    guess[key] = prev[key]
            if "t" in prev:
                guess["s"] = prev["t"]
            if "s" in prev:
                guess["t"] = prev["s"]
            guess["sta"] = 1
        if prev is not None:
            if isinstance(self.last_sq, int):
                guess["sq"] = self.last_sq + 1
            guess["pm"] = -1
            guess["ca"] = -2
        else:
            # fable16: a session's first frame carries the negotiated protocol version and
            # starts the sequence at 1 -- both predictable from the handshake.
            guess["v"] = _PROTOCOL_VERSION
            guess["sq"] = 1
        return guess

    def observe(self, frame: dict[str, Any]) -> None:
        header = {k: frame[k] for k in ROUTINE_KEYS + LINK_KEYS if k in frame}
        self.prev = header
        sq = frame.get("sq")
        self.last_sq = sq if isinstance(sq, int) and not isinstance(sq, bool) else None
        self.observed.append(header)


def _json_len(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def compress(frame: dict[str, Any], ctx: PredictorContext) -> dict[str, Any]:
    """Drop header fields equal to the prediction; list absent-but-predicted ones in ``nx``;
    replace payload fields identical to the previous frame's by an ``eq`` back-reference."""
    if ABSENT_KEY in frame or SAME_KEY in frame:
        raise ValueError(f"reserved session key {ABSENT_KEY!r}/{SAME_KEY!r} in frame")
    same = [k for k in PAYLOAD_KEYS if k in frame and k in ctx.prev_payload
            and ctx.prev_payload[k] == frame[k] and _json_len(frame[k]) > 12]
    payload_now = {k: frame[k] for k in PAYLOAD_KEYS if k in frame}
    if same:
        frame = {k: v for k, v in frame.items() if k not in same}
        ctx.copied_fields += len(same)
    guess = ctx.predict()
    out: dict[str, Any] = {}
    for key, value in frame.items():
        if key in guess and guess[key] == value and type(guess[key]) is type(value):
            ctx.predicted_fields += 1
            continue
        if key in ROUTINE_KEYS or key in LINK_KEYS:
            ctx.explicit_fields += 1
        out[key] = value
    # Negotiated JSON sorts routine header keys; the local learned routine retains
    # observation order. Canonicalize the list so both peers re-encode identical bytes.
    absent = sorted(key for key in guess if key not in frame)
    if absent:
        out[ABSENT_KEY] = absent
    if same:
        out[SAME_KEY] = same
        frame = {**frame, **{k: payload_now[k] for k in same}}
    ctx.observe(frame)
    ctx.prev_payload = payload_now
    return out


def expand(frame: dict[str, Any], ctx: PredictorContext) -> dict[str, Any]:
    """Inverse of :func:`compress` using the decoder-side context."""
    guess = ctx.predict()
    absent = frame.get(ABSENT_KEY, [])
    if not isinstance(absent, list) or any(k not in guess for k in absent):
        raise ValueError("invalid CPHC absent-field list")
    same = frame.get(SAME_KEY, [])
    if not isinstance(same, list) or any(k not in ctx.prev_payload for k in same):
        raise ValueError("invalid CPHC same-as-previous list")
    full = {k: v for k, v in guess.items() if k not in absent}
    full.update({k: v for k, v in frame.items() if k not in (ABSENT_KEY, SAME_KEY)})
    for k in same:
        full[k] = ctx.prev_payload[k]
    ctx.observe(full)
    ctx.prev_payload = {k: full[k] for k in PAYLOAD_KEYS if k in full}
    return full


# ---------------------------------------------------------------------------------------------
# HPACK-style dynamic reference table (RFC 7541 idea applied to data-plane references).
#
# ``state://``/``vector://``/``artifact://`` references are 128-bit content addresses rendered
# as 24-character tokens (``~s`` + 22 base64url chars).  Within one collaboration session the
# same few references recur constantly (plan state in the reply, the forward request, the
# reviewer request; facts artifact as ``ar`` *and* as ``facts_ref``/``evidence_ref``).  Both
# peers append every first-seen reference to a per-connection table in deterministic traversal
# order; later occurrences are sent as ``^<index>``.  Strings that genuinely start with ``^`` are
# escaped as ``^^...``.
# ---------------------------------------------------------------------------------------------
import re as _re

_TABLE_REF_RE = _re.compile(r"^~[sva][A-Za-z0-9_-]{22}$")
# Only these Protocol-1.3 fields can carry data-plane references or free-form payload strings;
# routing/header fields are ints and canonical ids, so they are never traversed.
REF_BEARING_KEYS = frozenset({"sr", "ar", "er", "dep", "ip", "rs", "e", "ik"})
_INDEX_RE = _re.compile(r"^\^(0|[1-9][0-9]{0,5})$")
MAX_REF_TABLE = 4096


@dataclass
class RefTable:
    index: dict[str, int] = field(default_factory=dict)
    values: list[str] = field(default_factory=list)
    hits: int = 0

    def _add(self, value: str) -> None:
        if len(self.values) < MAX_REF_TABLE:
            self.index[value] = len(self.values)
            self.values.append(value)

    def encode_frame(self, frame: dict[str, Any]) -> dict[str, Any]:
        return {k: (self.encode(v) if k in REF_BEARING_KEYS else v) for k, v in frame.items()}

    def decode_frame(self, frame: dict[str, Any]) -> dict[str, Any]:
        return {k: (self.decode(v) if k in REF_BEARING_KEYS else v) for k, v in frame.items()}

    def encode(self, value: Any) -> Any:
        if isinstance(value, str):
            if _TABLE_REF_RE.match(value):
                idx = self.index.get(value)
                if idx is not None:
                    self.hits += 1
                    return f"^{idx}"
                self._add(value)
                return value
            if value.startswith("^"):
                return "^" + value
            return value
        if isinstance(value, list):
            return [self.encode(v) for v in value]
        if isinstance(value, dict):
            return {k: self.encode(v) for k, v in value.items()}
        return value

    def decode(self, value: Any) -> Any:
        if isinstance(value, str):
            if value.startswith("^^"):
                return value[1:]
            match = _INDEX_RE.match(value)
            if match:
                idx = int(match.group(1))
                if idx >= len(self.values):
                    raise ValueError(f"unknown CPHC reference index {idx}")
                return self.values[idx]
            if value.startswith("^"):
                raise ValueError("malformed CPHC reference token")
            if _TABLE_REF_RE.match(value) and value not in self.index:
                self._add(value)
            return value
        if isinstance(value, list):
            return [self.decode(v) for v in value]
        if isinstance(value, dict):
            return {k: self.decode(v) for k, v in value.items()}
        return value


class RoutineRegistry:
    """Process-wide store of learned routines and of which digests a router already holds."""

    def __init__(self, capacity: int = 64) -> None:
        self._lock = threading.Lock()
        self._by_digest: OrderedDict[str, Routine] = OrderedDict()
        self._latest: dict[str, str] = {}
        self._uploaded: set[tuple[Any, str]] = set()
        self.capacity = int(capacity)

    def reset(self) -> None:
        with self._lock:
            self._by_digest.clear()
            self._latest.clear()
            self._uploaded.clear()

    def put(self, routine: Routine) -> str:
        digest = routine.digest
        with self._lock:
            self._by_digest[digest] = routine
            self._by_digest.move_to_end(digest)
            while len(self._by_digest) > self.capacity:
                self._by_digest.popitem(last=False)
        return digest

    def get(self, digest: str) -> Routine | None:
        with self._lock:
            return self._by_digest.get(digest)

    def latest(self, scope: str) -> Routine | None:
        with self._lock:
            digest = self._latest.get(scope)
            return self._by_digest.get(digest) if digest else None

    def learn(self, scope: str, frames: list[dict[str, Any]]) -> Routine | None:
        if not frames:
            return None
        routine = Routine.learn(frames, self.latest(scope))
        digest = self.put(routine)
        with self._lock:
            self._latest[scope] = digest
        return routine

    def needs_upload(self, router_id: Any, digest: str) -> bool:
        with self._lock:
            return (router_id, digest) not in self._uploaded

    def mark_uploaded(self, router_id: Any, digest: str) -> None:
        with self._lock:
            self._uploaded.add((router_id, digest))

    def forget_router(self, router_id: Any) -> None:
        with self._lock:
            self._uploaded = {item for item in self._uploaded if item[0] != router_id}


ROUTINES = RoutineRegistry()


def reset_routine_registry() -> None:
    """Cold-start the client-side registry (benchmarks measure learning + upload cost)."""
    ROUTINES.reset()


__all__ = [
    "ABSENT_KEY",
    "PredictorContext",
    "ROUTINES",
    "ROUTINE_KEYS",
    "Routine",
    "RefTable",
    "RoutineRegistry",
    "compress",
    "expand",
    "reset_routine_registry",
]
