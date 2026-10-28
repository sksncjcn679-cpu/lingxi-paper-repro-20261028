"""Stateful structured-session wire codec.

Protocol 1.3 adds a small connection/session context on top of the canonical compact
``AgentMessage`` representation.  Fields that are invariant for one task (protocol version,
trace/task/context identifiers and deadline) are sent once and omitted from later frames.
Timestamps use microsecond deltas and parent/causation identifiers can refer to the previous
message without retransmitting a full identifier.

The JSON session representation is reversible and is used for token/character accounting.  The
MessagePack representation carries the same frame but additionally uses integer field keys,
raw canonical-id bytes and MessagePack extension values for ``state://``/``vector://``/
``artifact://`` references.  This keeps the token metric interpretable while reporting the real
binary control-plane bytes separately.

A codec instance is connection scoped.  Encoder and decoder state are intentionally separate so
one object can be used in tests without accidental state coupling.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

import msgpack

from agent_runtime.protocol import fastpack as _fastpack

from agent_runtime.protocol.message import AgentMessage
from agent_runtime.protocol.routine import PredictorContext, RefTable, Routine, compress, expand
from agent_runtime.protocol.schema_atoms import decode_value, encode_value

_SCHEMA_FIELDS = frozenset({"ip", "rs", "e"})
from agent_runtime.protocol.serialization import (
    FIELD_TO_COMPACT,
    _message_from_compact,
    compact_payload,
)

_DELTA_TS = "dt"
_STATIC_FIELDS = ("v", "tr", "tk", "cx", "dl")
_ID_FIELDS = {"id", "tr", "tk", "cx", "pm", "ca"}

# Stable Protocol-1.3 binary field tags.  Do not derive these from dict ordering.
_KEY_ORDER = [
    "v", "id", "tr", "tk", "st", "cx", "pm", "ca", "sq", "dl", "s", "t", "ts",
    _DELTA_TS, "a", "c", "sta", "ar", "er", "sr", "dep", "cf", "ip", "rs", "e", "ik",
    # Protocol 1.4 (fable7): CPHC absent-predicted-field list.  Appended, never reordered.
    "nx",
    # fable15: CPHC same-as-previous payload back-reference list.
    "eq",
]
KEY_TO_TAG: dict[str, int] = {key: idx for idx, key in enumerate(_KEY_ORDER)}
TAG_TO_KEY: dict[int, str] = {value: key for key, value in KEY_TO_TAG.items()}

_REF_RE = re.compile(r"^~([sva])([A-Za-z0-9_-]{22})$")
_REF_CODE = {"s": 1, "v": 2, "a": 3}
_CODE_REF = {value: key for key, value in _REF_CODE.items()}
_DT_EXT_CODE = 10  # stable fixed-width signed timestamp-delta atom
_VALUE_DICT_EXT_CODE = 20


@dataclass(frozen=True)
class _ValueDictRef:
    index: int


@dataclass
class _SessionState:
    static: dict[str, Any] = field(default_factory=dict)
    last_timestamp: int | None = None
    last_message_id: str | None = None
    values: dict[str, int] = field(default_factory=dict)
    values_by_index: dict[int, str] = field(default_factory=dict)


def _decode_b64url(token: str) -> bytes:
    return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))


def _encode_b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _reference_to_ext(value: Any) -> Any:
    if isinstance(value, str):
        match = _REF_RE.fullmatch(value)
        if match:
            raw = _decode_b64url(match.group(2))
            if len(raw) == 16:
                return msgpack.ExtType(_REF_CODE[match.group(1)], raw)
        return value
    if isinstance(value, list):
        return [_reference_to_ext(v) for v in value]
    if isinstance(value, dict):
        return {k: _reference_to_ext(v) for k, v in value.items()}
    return value


def _ext_hook(code: int, data: bytes) -> Any:
    if code == _DT_EXT_CODE and len(data) in {4, 8}:
        return int.from_bytes(data, byteorder="big", signed=True)
    if code == _VALUE_DICT_EXT_CODE and len(data) == 4:
        return _ValueDictRef(int.from_bytes(data, byteorder="big", signed=False))
    prefix = _CODE_REF.get(code)
    if prefix is None or len(data) != 16:
        return msgpack.ExtType(code, data)
    return f"~{prefix}{_encode_b64url(data)}"


def _canonical_id_to_bytes(field: str, value: Any) -> Any:
    if field not in _ID_FIELDS or not isinstance(value, str):
        return value
    # ``~~`` is an escaped user identifier and must remain text.
    if not value.startswith("~") or value.startswith("~~"):
        return value
    token = value[1:]
    try:
        raw = _decode_b64url(token)
    except Exception:  # noqa: BLE001 - non-canonical custom string remains text
        return value
    expected = 5 if field in {"tk", "cx"} else 6
    return raw if len(raw) == expected else value


def _canonical_id_from_bytes(field: str, value: Any) -> Any:
    if field not in _ID_FIELDS or not isinstance(value, (bytes, bytearray)):
        return value
    expected = 5 if field in {"tk", "cx"} else 6
    raw = bytes(value)
    if len(raw) != expected:
        raise ValueError(f"invalid binary canonical id width for {field}: {len(raw)}")
    return "~" + _encode_b64url(raw)


# fable14: pure per-(field, value) conversions -- memoised (ids repeat on every frame).
import functools as _functools

_id_to_bytes_cached = _functools.lru_cache(maxsize=8192)(_canonical_id_to_bytes)
_id_from_bytes_cached = _functools.lru_cache(maxsize=8192)(_canonical_id_from_bytes)


def _canonical_id_to_bytes_fast(field: str, value: Any) -> Any:
    return _id_to_bytes_cached(field, value) if isinstance(value, str) else _canonical_id_to_bytes(field, value)


def _canonical_id_from_bytes_fast(field: str, value: Any) -> Any:
    if isinstance(value, bytearray):
        value = bytes(value)
    return _id_from_bytes_cached(field, value) if isinstance(value, bytes) else _canonical_id_from_bytes(field, value)


class SessionWireCodec:
    """Connection-scoped reversible Protocol-1.3 session codec."""

    def __init__(
        self,
        *,
        value_dictionary: bool = False,
        predictive: bool = False,
        routine: Routine | None = None,
        schema_atoms: bool = False,
    ) -> None:
        self._enc = _SessionState()
        self._dec = _SessionState()
        self.value_dictionary = bool(value_dictionary)
        # Protocol 1.4 context-predictive header compression (see protocol/routine.py).
        self.predictive = bool(predictive)
        self.routine = routine
        # fable8: context-free schema key atoms + columnar record lists (protocol/schema_atoms.py).
        self.schema_atoms = bool(schema_atoms)
        self._enc_pred = PredictorContext(routine=routine)
        self._dec_pred = PredictorContext(routine=routine)
        self._enc_refs = RefTable()
        self._dec_refs = RefTable()

    def reset(self) -> None:
        self._enc = _SessionState()
        self._dec = _SessionState()
        self._enc_pred = PredictorContext(routine=self.routine)
        self._dec_pred = PredictorContext(routine=self.routine)
        self._enc_refs = RefTable()
        self._dec_refs = RefTable()

    @property
    def encoder_observed(self) -> list[dict[str, Any]]:
        """Uncompressed routing headers sent on this connection (for routine learning)."""
        return list(self._enc_pred.observed)

    @property
    def prediction_stats(self) -> dict[str, int]:
        return {
            "predicted_fields": self._enc_pred.predicted_fields,
            "explicit_fields": self._enc_pred.explicit_fields,
            "reference_table_hits": self._enc_refs.hits,
        }

    # ------------------------------------------------------------------ encode
    def _frame(self, message: AgentMessage) -> dict[str, Any]:
        raw = compact_payload(message)
        frame = dict(raw)

        # Static task/session fields are transmitted only when first observed or changed.
        for key in _STATIC_FIELDS:
            if key not in frame:
                continue
            value = frame[key]
            if key in self._enc.static and self._enc.static[key] == value:
                frame.pop(key)
            else:
                self._enc.static[key] = value

        ts = frame.pop(FIELD_TO_COMPACT["timestamp"])
        if self._enc.last_timestamp is None:
            frame[FIELD_TO_COMPACT["timestamp"]] = ts
        else:
            frame[_DELTA_TS] = int(ts) - int(self._enc.last_timestamp)
        self._enc.last_timestamp = int(ts)

        previous = self._enc.last_message_id
        parent_key = FIELD_TO_COMPACT["parent_message_id"]
        cause_key = FIELD_TO_COMPACT["causation_id"]
        parent = frame.get(parent_key)
        cause = frame.get(cause_key)
        if previous is not None and parent == previous:
            frame[parent_key] = -1  # previous message id
        if cause is not None:
            if cause == parent:
                frame[cause_key] = -2  # same as decoded parent
            elif previous is not None and cause == previous:
                frame[cause_key] = -1

        current_id = raw.get(FIELD_TO_COMPACT["message_id"])
        if isinstance(current_id, str):
            self._enc.last_message_id = current_id
        if self.schema_atoms:
            frame = {k: (encode_value(v) if k in _SCHEMA_FIELDS else v) for k, v in frame.items()}
        if self.predictive:
            frame = compress(self._enc_refs.encode_frame(frame), self._enc_pred)
        return frame

    def encode_json(self, message: AgentMessage) -> str:
        frame = self._frame(message)
        return json.dumps(frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    def encode_msgpack(self, message: AgentMessage) -> bytes:
        frame = self._frame(message)
        return self._pack_frame(frame)

    def encode_dual(self, message: AgentMessage) -> tuple[str, bytes]:
        """Return reversible session-JSON for token accounting and binary bytes for transport."""
        frame = self._frame(message)
        text = json.dumps(frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return text, self._pack_frame(frame)

    def encode_msgpack(self, message: AgentMessage) -> bytes:
        """Binary wire only (fable9): identical bytes to ``encode_dual(m)[1]`` without building the
        JSON token view -- the router re-encodes for verification and never needs the text."""
        return self._pack_frame(self._frame(message))

    def _encode_dictionary_values(self, value: Any) -> Any:
        if not self.value_dictionary:
            return value
        if isinstance(value, str) and len(value) >= 64:
            existing = self._enc.values.get(value)
            if existing is not None:
                return msgpack.ExtType(_VALUE_DICT_EXT_CODE, existing.to_bytes(4, "big"))
            index = len(self._enc.values)
            self._enc.values[value] = index
            self._enc.values_by_index[index] = value
            return value
        if isinstance(value, list):
            return [self._encode_dictionary_values(item) for item in value]
        if isinstance(value, dict):
            return {key: self._encode_dictionary_values(item) for key, item in value.items()}
        return value

    def _decode_dictionary_values(self, value: Any) -> Any:
        if isinstance(value, _ValueDictRef):
            try:
                return self._dec.values_by_index[value.index]
            except KeyError as exc:
                raise ValueError(f"unknown session value dictionary index: {value.index}") from exc
        if isinstance(value, str) and self.value_dictionary and len(value) >= 64:
            index = len(self._dec.values)
            self._dec.values[value] = index
            self._dec.values_by_index[index] = value
            return value
        if isinstance(value, list):
            return [self._decode_dictionary_values(item) for item in value]
        if isinstance(value, dict):
            return {key: self._decode_dictionary_values(item) for key, item in value.items()}
        return value

    def _pack_frame(self, frame: dict[str, Any]) -> bytes:
        packed: dict[int, Any] = {}
        for key, value in frame.items():
            try:
                tag = KEY_TO_TAG[key]
            except KeyError as exc:
                raise ValueError(f"unknown session field key: {key}") from exc
            value = self._encode_dictionary_values(value)
            value = _canonical_id_to_bytes_fast(key, value)
            if key == _DELTA_TS and type(value) is int:
                # MessagePack's native integer encoding changes width around 127/255/65535,
                # making identical runs differ by a byte solely because scheduler timing moved
                # a timestamp delta across a boundary. Protocol 1.3 reserves an ExtType for a
                # fixed-width signed delta so physical-wire benchmarks are byte-deterministic.
                width = 4 if -(2**31) <= value < 2**31 else 8
                value = msgpack.ExtType(
                    _DT_EXT_CODE, int(value).to_bytes(width, byteorder="big", signed=True)
                )
            value = _reference_to_ext(value)
            packed[tag] = value
        return _fastpack.packb(packed)

    # ------------------------------------------------------------------ decode
    def _restore(self, frame: dict[str, Any]) -> AgentMessage:
        raw = self._dec_refs.decode_frame(expand(frame, self._dec_pred)) if self.predictive else dict(frame)
        if self.schema_atoms:
            raw = {k: (decode_value(v) if k in _SCHEMA_FIELDS else v) for k, v in raw.items()}
        if not self.predictive and "nx" in raw:
            raise ValueError("CPHC frame received on a non-predictive session")

        # Rehydrate connection-static fields before interpreting task-scoped reference atoms.
        for key in _STATIC_FIELDS:
            if key in raw:
                self._dec.static[key] = raw[key]
            elif key in self._dec.static:
                raw[key] = self._dec.static[key]

        if _DELTA_TS in raw:
            delta = raw.pop(_DELTA_TS)
            if self._dec.last_timestamp is None or type(delta) is not int:
                raise ValueError("timestamp delta received before an absolute session timestamp")
            timestamp = self._dec.last_timestamp + delta
            raw[FIELD_TO_COMPACT["timestamp"]] = timestamp
        else:
            timestamp = raw.get(FIELD_TO_COMPACT["timestamp"])
            if type(timestamp) is not int:
                raise ValueError("session frame requires absolute integer timestamp on first frame")
        self._dec.last_timestamp = int(timestamp)

        previous = self._dec.last_message_id
        parent_key = FIELD_TO_COMPACT["parent_message_id"]
        cause_key = FIELD_TO_COMPACT["causation_id"]
        if raw.get(parent_key) == -1:
            if previous is None:
                raise ValueError("relative parent id received before any message")
            raw[parent_key] = previous
        if raw.get(cause_key) == -2:
            if parent_key not in raw or not isinstance(raw[parent_key], str):
                raise ValueError("causation=-2 requires a decoded parent id")
            raw[cause_key] = raw[parent_key]
        elif raw.get(cause_key) == -1:
            if previous is None:
                raise ValueError("relative causation id received before any message")
            raw[cause_key] = previous

        current_id = raw.get(FIELD_TO_COMPACT["message_id"])
        if isinstance(current_id, str):
            self._dec.last_message_id = current_id
        message = _message_from_compact(raw)
        return message

    def decode_json(self, wire: str) -> AgentMessage:
        frame = json.loads(wire)
        if not isinstance(frame, dict):
            raise ValueError("session JSON frame must decode to an object")
        return self._restore(frame)

    def decode_msgpack(self, wire: bytes) -> AgentMessage:
        packed = _fastpack.unpackb(wire, ext_hook=_ext_hook)
        if not isinstance(packed, dict):
            raise ValueError("session MessagePack frame must decode to a map")
        frame: dict[str, Any] = {}
        for tag, value in packed.items():
            if type(tag) is not int or tag not in TAG_TO_KEY:
                raise ValueError(f"unknown session field tag: {tag!r}")
            key = TAG_TO_KEY[tag]
            value = _canonical_id_from_bytes_fast(key, value)
            frame[key] = value
        if self.value_dictionary:
            frame = self._decode_dictionary_values(frame)
        return self._restore(frame)


__all__ = ["SessionWireCodec", "KEY_TO_TAG", "TAG_TO_KEY"]
