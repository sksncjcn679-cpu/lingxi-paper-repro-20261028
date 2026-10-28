"""fable16: fast pure-Python MessagePack for the runtime's wire subset.

``msgpack``'s own pure-Python fallback is a general streaming implementation with heavy
per-object dispatch.  When the C extension is not available (minimal images, some distro
packages), every control frame, router re-encode and state-ledger entry pays for it.  This
module implements exactly the subset the runtime emits -- nil/bool/int/float64/str/bin/array/
map/ext -- producing **byte-identical** output to ``msgpack.packb(obj, use_bin_type=True,
strict_types=True)`` and accepting everything that encoder produces.  Anything outside the
subset raises ``_Unsupported`` and callers fall back to ``msgpack`` itself.

``USE_FAST`` is True only when ``msgpack`` is running its Python fallback; with the C extension
the runtime keeps using C.  Equivalence is property-tested against ``msgpack``.
"""

from __future__ import annotations

import struct
from typing import Any, Callable

import msgpack

USE_FAST = "fallback" in getattr(msgpack.Packer, "__module__", "")

_ExtType = msgpack.ExtType
_pk_d = struct.Struct(">d").pack
_pk_B = struct.Struct(">B").pack
_pk_H = struct.Struct(">H").pack
_pk_I = struct.Struct(">I").pack
_pk_Q = struct.Struct(">Q").pack
_pk_b = struct.Struct(">b").pack
_pk_h = struct.Struct(">h").pack
_pk_i = struct.Struct(">i").pack
_pk_q = struct.Struct(">q").pack


class _Unsupported(Exception):
    pass


def _pack(o: Any, out: list) -> None:
    t = type(o)
    if t is str:
        b = o.encode("utf-8")
        n = len(b)
        if n < 32:
            out.append(bytes((0xA0 | n,)))
        elif n < 256:
            out.append(b"\xd9" + _pk_B(n))
        elif n < 65536:
            out.append(b"\xda" + _pk_H(n))
        elif n < 2**32:
            out.append(b"\xdb" + _pk_I(n))
        else:
            raise _Unsupported
        out.append(b)
    elif t is int:
        if 0 <= o < 128:
            out.append(bytes((o,)))
        elif -32 <= o < 0:
            out.append(bytes((o & 0xFF,)))
        elif o >= 0:
            if o < 256:
                out.append(b"\xcc" + _pk_B(o))
            elif o < 65536:
                out.append(b"\xcd" + _pk_H(o))
            elif o < 2**32:
                out.append(b"\xce" + _pk_I(o))
            elif o < 2**64:
                out.append(b"\xcf" + _pk_Q(o))
            else:
                raise _Unsupported
        else:
            if o >= -128:
                out.append(b"\xd0" + _pk_b(o))
            elif o >= -32768:
                out.append(b"\xd1" + _pk_h(o))
            elif o >= -(2**31):
                out.append(b"\xd2" + _pk_i(o))
            elif o >= -(2**63):
                out.append(b"\xd3" + _pk_q(o))
            else:
                raise _Unsupported
    elif t is dict:
        n = len(o)
        if n < 16:
            out.append(bytes((0x80 | n,)))
        elif n < 65536:
            out.append(b"\xde" + _pk_H(n))
        else:
            out.append(b"\xdf" + _pk_I(n))
        for k, v in o.items():
            _pack(k, out)
            _pack(v, out)
    elif t is list:
        n = len(o)
        if n < 16:
            out.append(bytes((0x90 | n,)))
        elif n < 65536:
            out.append(b"\xdc" + _pk_H(n))
        else:
            out.append(b"\xdd" + _pk_I(n))
        for v in o:
            _pack(v, out)
    elif o is None:
        out.append(b"\xc0")
    elif o is True:
        out.append(b"\xc3")
    elif o is False:
        out.append(b"\xc2")
    elif t is float:
        out.append(b"\xcb" + _pk_d(o))
    elif t is bytes or t is bytearray:
        n = len(o)
        if n < 256:
            out.append(b"\xc4" + _pk_B(n))
        elif n < 65536:
            out.append(b"\xc5" + _pk_H(n))
        elif n < 2**32:
            out.append(b"\xc6" + _pk_I(n))
        else:
            raise _Unsupported
        out.append(bytes(o))
    elif t is _ExtType:
        code, data = o.code, o.data
        n = len(data)
        head = {1: b"\xd4", 2: b"\xd5", 4: b"\xd6", 8: b"\xd7", 16: b"\xd8"}.get(n)
        if head is not None:
            out.append(head + _pk_b(code))
        elif n < 256:
            out.append(b"\xc7" + _pk_B(n) + _pk_b(code))
        elif n < 65536:
            out.append(b"\xc8" + _pk_H(n) + _pk_b(code))
        else:
            out.append(b"\xc9" + _pk_I(n) + _pk_b(code))
        out.append(data)
    else:
        raise _Unsupported


def packb(obj: Any) -> bytes:
    """== msgpack.packb(obj, use_bin_type=True, strict_types=True) for the runtime subset."""
    if not USE_FAST:
        return msgpack.packb(obj, use_bin_type=True, strict_types=True)
    out: list = []
    try:
        _pack(obj, out)
    except (_Unsupported, OverflowError):
        return msgpack.packb(obj, use_bin_type=True, strict_types=True)
    return b"".join(out)


_up = struct.unpack_from


def _unpack(b: bytes, i: int, ext_hook) -> tuple[Any, int]:
    c = b[i]
    i += 1
    if c < 0x80:
        return c, i
    if c >= 0xE0:
        return c - 256, i
    if 0xA0 <= c <= 0xBF:
        n = c & 0x1F
        return b[i:i + n].decode("utf-8"), i + n
    if 0x80 <= c <= 0x8F:
        n = c & 0x0F
        d = {}
        for _ in range(n):
            k, i = _unpack(b, i, ext_hook)
            v, i = _unpack(b, i, ext_hook)
            d[k] = v
        return d, i
    if 0x90 <= c <= 0x9F:
        n = c & 0x0F
        arr = []
        for _ in range(n):
            v, i = _unpack(b, i, ext_hook)
            arr.append(v)
        return arr, i
    if c == 0xC0:
        return None, i
    if c == 0xC2:
        return False, i
    if c == 0xC3:
        return True, i
    if c == 0xCB:
        return _up(">d", b, i)[0], i + 8
    if c == 0xCA:
        return _up(">f", b, i)[0], i + 4
    if c == 0xCC:
        return b[i], i + 1
    if c == 0xCD:
        return _up(">H", b, i)[0], i + 2
    if c == 0xCE:
        return _up(">I", b, i)[0], i + 4
    if c == 0xCF:
        return _up(">Q", b, i)[0], i + 8
    if c == 0xD0:
        return _up(">b", b, i)[0], i + 1
    if c == 0xD1:
        return _up(">h", b, i)[0], i + 2
    if c == 0xD2:
        return _up(">i", b, i)[0], i + 4
    if c == 0xD3:
        return _up(">q", b, i)[0], i + 8
    if c in (0xD9, 0xDA, 0xDB):
        w = {0xD9: 1, 0xDA: 2, 0xDB: 4}[c]
        n = int.from_bytes(b[i:i + w], "big")
        i += w
        return b[i:i + n].decode("utf-8"), i + n
    if c in (0xC4, 0xC5, 0xC6):
        w = {0xC4: 1, 0xC5: 2, 0xC6: 4}[c]
        n = int.from_bytes(b[i:i + w], "big")
        i += w
        return bytes(b[i:i + n]), i + n
    if c in (0xDC, 0xDD, 0xDE, 0xDF):
        w = 2 if c in (0xDC, 0xDE) else 4
        n = int.from_bytes(b[i:i + w], "big")
        i += w
        if c in (0xDC, 0xDD):
            arr = []
            for _ in range(n):
                v, i = _unpack(b, i, ext_hook)
                arr.append(v)
            return arr, i
        d = {}
        for _ in range(n):
            k, i = _unpack(b, i, ext_hook)
            v, i = _unpack(b, i, ext_hook)
            d[k] = v
        return d, i
    if 0xD4 <= c <= 0xD8 or c in (0xC7, 0xC8, 0xC9):
        if c >= 0xD4:
            n = {0xD4: 1, 0xD5: 2, 0xD6: 4, 0xD7: 8, 0xD8: 16}[c]
        else:
            w = {0xC7: 1, 0xC8: 2, 0xC9: 4}[c]
            n = int.from_bytes(b[i:i + w], "big")
            i += w
        code = _up(">b", b, i)[0]
        i += 1
        data = bytes(b[i:i + n])
        i += n
        return (ext_hook(code, data) if ext_hook else _ExtType(code, data)), i
    raise _Unsupported


def unpackb(data: bytes, ext_hook: Callable | None = None) -> Any:
    """== msgpack.unpackb(data, raw=False, strict_map_key=False, ext_hook=...) for the subset."""
    if not USE_FAST:
        kw = {"ext_hook": ext_hook} if ext_hook else {}
        return msgpack.unpackb(data, raw=False, strict_map_key=False, **kw)
    try:
        obj, end = _unpack(bytes(data), 0, ext_hook)
    except (_Unsupported, IndexError, struct.error, UnicodeDecodeError):
        kw = {"ext_hook": ext_hook} if ext_hook else {}
        return msgpack.unpackb(data, raw=False, strict_map_key=False, **kw)
    if end != len(data):
        raise ValueError("trailing bytes after MessagePack object")
    return obj


__all__ = ["USE_FAST", "packb", "unpackb"]
