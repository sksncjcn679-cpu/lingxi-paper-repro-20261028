"""Protocol 1.4 extension (fable8): schema-declared key atoms + columnar record lists.

Two lossless, *context-free* transformations applied to the payload fields (``ip``/``rs``/...)
of session frames.  Unlike the per-session CPHC state, both are fully determined by a static,
published table, so any reader holding the protocol version can decode a frame in isolation.

1. **Key atoms** (the protobuf idea: field *numbers* on the wire, field *names* in the schema).
   Dict keys that are declared in the built-in capability schemas, or are runtime task-format /
   reference fields, are written as ``"$<n>"`` where ``n`` is their index in :data:`KEY_ATOMS`.
   The table is append-only; indices never change.  User keys that begin with ``$`` are escaped
   as ``$$...``.  Keys outside the table are sent verbatim — nothing user-specific is interned.

2. **Columnar record lists** (CSV header / Arrow-style).  A list of >= 3 dicts that share the
   exact same key sequence is written once as ``{"$t": [keys...], "$r": [[values...], ...]}``.
   All keys stay visible (once), so the frame remains self-describing and LLM-readable; only
   the repetition of the same keys on every row is removed.

Decoding rejects unknown atoms and malformed tables.  Round-trip exactness is property-tested.
"""

from __future__ import annotations

import re
from typing import Any

# Append-only.  Sources: keys of every built-in capability input/output schema (handshake
# contracts) + runtime task-format / reference fields the orchestrator itself generates.
KEY_ATOMS: tuple[str, ...] = (
    # capability-schema keys
    "candidates", "code", "description", "embedding", "evidence", "exit_code", "facts",
    "keywords", "memory_id", "memory_refs", "operands", "operation", "outcome", "payload",
    "plan", "policy", "quality", "query", "ranked_candidates", "stdout", "success", "summary",
    "tags", "task", "value", "values", "verdict",
    # runtime task-format and reference fields
    "workflow_state_ref", "plan_ref", "facts_ref", "evidence_ref", "items", "answer", "topic",
    "question", "defect", "memory_fast_path",
    # fable15: versioned collaboration-state vocabulary (runtime-generated coordination fields)
    "stage", "execution", "has_value", "value_type", "review", "count", "keys",
)
_ATOM_OF = {k: i for i, k in enumerate(KEY_ATOMS)}
_ATOM_RE = re.compile(r"^\$(0|[1-9][0-9]{0,3})$")
COLUMN_KEY, ROWS_KEY = "$t", "$r"
MIN_COLUMNAR_ROWS = 3
SCHEMA_ATOM_VERSION = len(KEY_ATOMS)  # bumps whenever the table grows


def _enc_key(key: str) -> str:
    idx = _ATOM_OF.get(key)
    if idx is not None:
        return f"${idx}"
    if key.startswith("$"):
        return "$" + key
    return key


def _dec_key(key: str) -> str:
    if key.startswith("$$"):
        return key[1:]
    match = _ATOM_RE.match(key)
    if match:
        idx = int(match.group(1))
        if idx >= len(KEY_ATOMS):
            raise ValueError(f"unknown schema key atom {key!r}")
        return KEY_ATOMS[idx]
    if key.startswith("$"):
        raise ValueError(f"malformed schema key {key!r}")
    return key


MASK_KEY = "$m"


def _union_order(rows: list[dict]) -> list[str] | None:
    """Key union that preserves every row's own key order, or None if no such order exists."""
    order: list[str] = []
    for row in rows:
        prev = None
        for key in row:
            if not isinstance(key, str):
                return None
            if key not in order:
                order.insert(order.index(prev) + 1 if prev is not None else 0, key)
            prev = key
    pos = {k: i for i, k in enumerate(order)}
    for row in rows:
        idx = [pos[k] for k in row]
        if idx != sorted(idx):
            return None
    return order


def encode_value(value: Any, atoms: bool = True) -> Any:
    """``atoms=False`` applies only the columnar layout (used for the text-fairness baseline)."""
    ek = _enc_key if atoms else (lambda k: k)
    if isinstance(value, dict):
        return {ek(k) if isinstance(k, str) else k: encode_value(v, atoms) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) >= MIN_COLUMNAR_ROWS and all(isinstance(x, dict) and x for x in value):
            cols = _union_order(value)
            if cols is not None and len(cols) >= 2 and len(cols) <= 60:
                full = all(len(x) == len(cols) for x in value)
                table: dict[str, Any] = {
                    COLUMN_KEY: [ek(k) for k in cols],
                    ROWS_KEY: [[encode_value(x[k], atoms) for k in cols if k in x] for x in value],
                }
                if not full:
                    # Presence bitmask per row (bit i = column i present); key order within a
                    # row is recovered from the union order, which respects every row's order.
                    table[MASK_KEY] = [sum(1 << i for i, k in enumerate(cols) if k in x) for x in value]
                return table
        return [encode_value(x, atoms) for x in value]
    return value


def decode_value(value: Any) -> Any:
    if isinstance(value, dict):
        if COLUMN_KEY in value or ROWS_KEY in value or MASK_KEY in value:
            keys = set(value)
            if not ({COLUMN_KEY, ROWS_KEY} <= keys <= {COLUMN_KEY, ROWS_KEY, MASK_KEY}):
                raise ValueError("malformed columnar table")
            cols, rows = value[COLUMN_KEY], value[ROWS_KEY]
            if not isinstance(cols, list) or not isinstance(rows, list) or len(rows) < MIN_COLUMNAR_ROWS:
                raise ValueError("malformed columnar table")
            names = [_dec_key(c) for c in cols]
            if len(set(names)) != len(names) or len(names) < 2:
                raise ValueError("duplicate or too few columnar keys")
            masks = value.get(MASK_KEY)
            full = (1 << len(names)) - 1
            if masks is None:
                masks = [full] * len(rows)
            if not isinstance(masks, list) or len(masks) != len(rows):
                raise ValueError("columnar mask length mismatch")
            out = []
            for row, mask in zip(rows, masks):
                if not isinstance(mask, int) or isinstance(mask, bool) or mask <= 0 or mask > full:
                    raise ValueError("invalid columnar mask")
                present = [n for i, n in enumerate(names) if mask >> i & 1]
                if not isinstance(row, list) or len(row) != len(present):
                    raise ValueError("columnar row width mismatch")
                out.append({n: decode_value(v) for n, v in zip(present, row)})
            return out
        return {_dec_key(k) if isinstance(k, str) else k: decode_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_value(x) for x in value]
    return value


def readable_keys(value: Any) -> Any:
    """LLM-facing view: expand key atoms to their names but keep the compact columnar layout
    (tables stay readable).  Used only for prompts; the protocol wire is unchanged."""
    if isinstance(value, dict):
        if COLUMN_KEY in value and isinstance(value.get(COLUMN_KEY), list):
            out = dict(value)
            out[COLUMN_KEY] = [_dec_key(c) if isinstance(c, str) else c for c in value[COLUMN_KEY]]
            out[ROWS_KEY] = readable_keys(value.get(ROWS_KEY))
            return out
        return {(_dec_key(k) if isinstance(k, str) else k): readable_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [readable_keys(x) for x in value]
    return value


__all__ = ["KEY_ATOMS", "readable_keys", "SCHEMA_ATOM_VERSION", "decode_value", "encode_value"]
