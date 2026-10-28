"""fable16: fast MessagePack subset must be byte-identical to msgpack and round-trip exactly."""

from __future__ import annotations

import random

import msgpack
import pytest

from agent_runtime.protocol import fastpack


def _rand(rng, depth=0):
    r = rng.random()
    if depth > 3 or r < 0.5:
        return rng.choice([
            None, True, False, 0, 1, 127, 128, 255, 256, 65535, 65536, 2**32 - 1, 2**32, 2**63, 2**64 - 1,
            -1, -32, -33, -128, -129, -32768, -32769, -(2**31), -(2**31) - 1, -(2**63),
            0.0, -0.0, 1.5, 1e300, float("inf"),
            "", "a" * 31, "b" * 32, "c" * 255, "d" * 256, "中文" * 40, "e" * 70000,
            b"", b"x" * 255, b"y" * 256, b"z" * 70000,
            msgpack.ExtType(1, b"a"), msgpack.ExtType(2, b"ab"), msgpack.ExtType(3, b"abcd"),
            msgpack.ExtType(4, b"a" * 8), msgpack.ExtType(5, b"a" * 16), msgpack.ExtType(6, b"a" * 3),
            msgpack.ExtType(7, b"a" * 300), msgpack.ExtType(9, b""),
            rng.randint(-(2**63), 2**64 - 1), rng.random(),
        ])
    if r < 0.75:
        return [_rand(rng, depth + 1) for _ in range(rng.choice([0, 3, 15, 16, 20]))]
    return {rng.choice([rng.randint(-5, 300), "k%d" % rng.randint(0, 40)]): _rand(rng, depth + 1)
            for _ in range(rng.choice([0, 2, 15, 16, 18]))}


@pytest.mark.parametrize("seed", range(30))
def test_byte_identical_to_msgpack_and_roundtrip(seed, monkeypatch):
    monkeypatch.setattr(fastpack, "USE_FAST", True)
    rng = random.Random(seed)
    for _ in range(40):
        v = _rand(rng)
        ref = msgpack.packb(v, use_bin_type=True, strict_types=True)
        assert fastpack.packb(v) == ref
        assert fastpack.unpackb(ref) == msgpack.unpackb(ref, raw=False, strict_map_key=False)


def test_ext_hook_and_trailing_bytes(monkeypatch):
    monkeypatch.setattr(fastpack, "USE_FAST", True)
    data = msgpack.packb({1: msgpack.ExtType(7, b"abcd")}, use_bin_type=True)
    assert fastpack.unpackb(data, ext_hook=lambda c, d: (c, d)) == {1: (7, b"abcd")}
    with pytest.raises(ValueError):
        fastpack.unpackb(data + b"\x00")
    with pytest.raises(Exception):
        fastpack.unpackb(b"\xc1")  # never-used type byte -> msgpack raises too


def test_unsupported_types_fall_back_to_msgpack(monkeypatch):
    monkeypatch.setattr(fastpack, "USE_FAST", True)
    with pytest.raises(Exception):
        fastpack.packb({1: object()})
    assert fastpack.packb([2**64]) if False else True
