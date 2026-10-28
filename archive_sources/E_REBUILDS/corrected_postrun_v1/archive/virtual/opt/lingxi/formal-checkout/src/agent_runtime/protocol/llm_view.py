"""OS-SOTA1.5: *semantic LLM view* of an inbound message.

The wire is optimised for machines: message/trace/task ids, sequence numbers, clock deltas,
opaque session-dictionary tokens (``~xxxx``) and reference-table indices (``^k``) are needed by
the router and the codec but carry no meaning for a language model -- yet every one of those
tokens is billed 1:1 (real fit: billed = 4386·requests + 1.000·visible).  ``semantic_view``
projects the wire onto the fields a model can use.  The projection is applied identically to
text and structured wires (fairness), only to what the LLM sees; the transport, accounting of
communication tokens and the router's lossless verification are untouched.
"""

from __future__ import annotations

import json
import re

# Structured (session-msgpack JSON view) plumbing keys.
_STRUCT_DROP = {"id", "tr", "tk", "cx", "ts", "dt", "st", "s", "t", "a", "c", "nx", "pm", "cz", "sq", "v"}
# Lean-text plumbing fields.
_TEXT_DROP = {"trace", "timestamp", "step", "seq", "context", "parent", "cause", "task", "msg", "id"}
_OPAQUE = re.compile(r"^[~^][A-Za-z0-9_\-]*$")


def _strip_opaque(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v2 = _strip_opaque(v)
            if v2 is None or v2 == [] or v2 == {}:
                continue
            out[k] = v2
        return out
    if isinstance(value, list):
        items = [x for x in (_strip_opaque(v) for v in value) if x is not None]
        return items
    if isinstance(value, str) and _OPAQUE.match(value):
        return None
    return value


def semantic_view(wire: str) -> str:
    """Return the model-facing projection of ``wire`` (never raises; unknown shapes pass through)."""
    if not wire:
        return wire
    text = wire.strip()
    if text.startswith("{"):
        try:
            frame = json.loads(text)
        except ValueError:
            return wire
        if not isinstance(frame, dict):
            return wire
        kept = {k: v for k, v in frame.items() if k not in _STRUCT_DROP}
        kept = _strip_opaque(kept)
        return json.dumps(kept, ensure_ascii=False, separators=(",", ":")) if kept else wire
    if " | " in text and "=" in text.split(" | ", 1)[0]:
        parts = []
        for part in text.split(" | "):
            key = part.split("=", 1)[0].strip()
            if key in _TEXT_DROP:
                continue
            parts.append(part)
        return " | ".join(parts) if parts else wire
    return wire


__all__ = ["semantic_view"]
