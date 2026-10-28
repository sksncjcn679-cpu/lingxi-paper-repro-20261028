"""fable15: compact physical encoding of versioned collaboration-state entries.

The ledger's *logical* identity is unchanged: content hashes are still SHA-256 over canonical
JSON and every resolve re-verifies them.  Only the stored representation changes, from canonical
JSON to MessagePack with (a) declared keys as small integers from the static, append-only
``KEY_ATOMS`` table, (b) same-namespace ``state://`` / ``vstate://`` / ``artifact://``
references as 16-byte binary digests, and (c) JSON-Patch ops as ``[opcode, path, value]``
(path keys that are atoms are written as negative integers, which can never collide with list
indices).  Anything MessagePack cannot represent exactly falls back to canonical JSON.
"""

from __future__ import annotations

import json
import re

import msgpack

from agent_runtime.protocol import fastpack as _fp

from agent_runtime.protocol.schema_atoms import KEY_ATOMS

_ATOM = {k: i for i, k in enumerate(KEY_ATOMS)}
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_OPS = {"add": 0, "replace": 1, "remove": 2}
_OPS_INV = {v: k for k, v in _OPS.items()}
_SNAPSHOT, _PATCH, _JSON = b"\x01", b"\x02", b"J"
_JSNAP, _JPATCH = b"\x03", b"\x04"  # compact-JSON forms (C-accelerated json, same atoms/refs)
_REF_CODE = {1: "s", 2: "v", 3: "a"}
_REF_CODE_INV = {v: k for k, v in _REF_CODE.items()}
import base64 as _b64

_B64REF = re.compile(r"^~([sva])([A-Za-z0-9_-]{22})$")


def _jenc(v, ns):
    if isinstance(v, str):
        for code, pre in _prefixes(ns):
            if v.startswith(pre) and _HEX32.match(v[len(pre):]):
                return "~" + _REF_CODE[code] + _b64.urlsafe_b64encode(bytes.fromhex(v[len(pre):])).decode().rstrip("=")
        return "~" + v if v.startswith("~") else v
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            idx = _ATOM.get(k)
            out[f"${idx}" if idx is not None else ("$" + k if k.startswith("$") else k)] = _jenc(x, ns)
        return out
    if isinstance(v, list):
        return [_jenc(x, ns) for x in v]
    return v


def _jdec(v, ns):
    if isinstance(v, str):
        if v.startswith("~~"):
            return v[1:]
        m = _B64REF.match(v)
        if m:
            pre = dict(_prefixes(ns))[_REF_CODE_INV[m.group(1)]]
            return pre + _b64.urlsafe_b64decode(m.group(2) + "==").hex()
        if v.startswith("~"):
            raise ValueError("malformed compact state string")
        return v
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k.startswith("$$"):
                k = k[1:]
            elif k.startswith("$"):
                i = int(k[1:])
                if not 0 <= i < len(KEY_ATOMS):
                    raise ValueError("unknown compact state key atom")
                k = KEY_ATOMS[i]
            out[k] = _jdec(x, ns)
        return out
    if isinstance(v, list):
        return [_jdec(x, ns) for x in v]
    return v


def _jdump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _prefixes(ns: str) -> tuple[tuple[int, str], ...]:
    return ((1, f"state://{ns}/"), (2, f"vstate://{ns}/"), (3, f"artifact://task/{ns}/"))


def _enc(v, ns):
    if isinstance(v, str):
        for code, pre in _prefixes(ns):
            if v.startswith(pre) and _HEX32.match(v[len(pre):]):
                return msgpack.ExtType(code, bytes.fromhex(v[len(pre):]))
        return v
    if isinstance(v, dict):
        return {_ATOM.get(k, k) if isinstance(k, str) else k: _enc(x, ns) for k, x in v.items()}
    if isinstance(v, list):
        return [_enc(x, ns) for x in v]
    return v


def _dec(v, ns):
    if isinstance(v, msgpack.ExtType):
        pre = dict(_prefixes(ns)).get(v.code)
        if pre is None or len(v.data) != 16:
            raise ValueError("invalid compact state reference")
        return pre + v.data.hex()
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if isinstance(k, int):
                if not 0 <= k < len(KEY_ATOMS):
                    raise ValueError("unknown compact state key atom")
                k = KEY_ATOMS[k]
            out[k] = _dec(x, ns)
        return out
    if isinstance(v, list):
        return [_dec(x, ns) for x in v]
    return v


def _canon(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def encode_snapshot(value, ns: str) -> bytes:
    # No encode-time self-check: every resolve re-verifies the SHA-256 of the canonical JSON of
    # the decoded value, so a codec defect can never go unnoticed.
    try:
        return _SNAPSHOT + _fp.packb(_enc(value, ns))
    except Exception:  # noqa: BLE001 - values msgpack cannot represent exactly
        return _JSON + _canon(value)


def decode_snapshot(blob: bytes, ns: str):
    if blob[:1] == _JSON:
        return json.loads(blob[1:])
    if blob[:1] == _JSNAP:
        return _jdec(json.loads(blob[1:]), ns)
    if blob[:1] != _SNAPSHOT:
        raise ValueError("not a compact snapshot")
    return _dec(_fp.unpackb(blob[1:]), ns)


def encode_ops_json(ops, ns: str) -> bytes:
    try:
        jrows = []
        for op in ops:
            path = [(-(_ATOM[p] + 1) if isinstance(p, str) and p in _ATOM else p) for p in op.path]
            row = [_OPS[op.op], path]
            if op.op != "remove":
                row.append(_jenc(op.value, ns))
            jrows.append(row)
        return _JPATCH + _jdump(jrows)
    except Exception:  # noqa: BLE001
        return _JSON + json.dumps([o.to_wire() for o in ops], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def encode_ops(ops, ns: str) -> bytes:
    rows = []
    for op in ops:
        path = [(-(_ATOM[p] + 1) if isinstance(p, str) and p in _ATOM else p) for p in op.path]
        row = [_OPS[op.op], path]
        if op.op != "remove":
            row.append(_enc(op.value, ns))
        rows.append(row)
    try:
        return _PATCH + _fp.packb(rows)
    except Exception:  # noqa: BLE001
        pass
    return _JSON + json.dumps([o.to_wire() for o in ops], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def decode_ops(blob: bytes, ns: str):
    from agent_runtime.state_exchange.delta import PatchOp

    if blob[:1] == _JSON:
        return tuple(PatchOp(d["o"], tuple(d["p"]), d.get("v")) for d in json.loads(blob[1:]))
    if blob[:1] == _JPATCH:
        out = []
        for row in json.loads(blob[1:]):
            parts = tuple(KEY_ATOMS[-p - 1] if isinstance(p, int) and p < 0 else p for p in row[1])
            out.append(PatchOp(_OPS_INV[row[0]], parts, _jdec(row[2], ns) if len(row) > 2 else None))
        return tuple(out)
    if blob[:1] != _PATCH:
        raise ValueError("not a compact patch")
    out = []
    for row in _fp.unpackb(blob[1:]):
        code, path = row[0], row[1]
        parts = tuple(KEY_ATOMS[-p - 1] if isinstance(p, int) and p < 0 else p for p in path)
        out.append(PatchOp(_OPS_INV[code], parts, _dec(row[2], ns) if len(row) > 2 else None))
    return tuple(out)


__all__ = ["decode_ops", "decode_snapshot", "encode_ops", "encode_snapshot"]
