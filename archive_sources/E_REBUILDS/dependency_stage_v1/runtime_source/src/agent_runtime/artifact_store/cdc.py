"""fable11: content-defined chunking (CDC) with a context-scoped chunk pool.

Multi-round collaboration re-sends the *same* growing context (incident logs, transcripts,
manifests) round after round.  Whole-blob content addressing cannot deduplicate "previous
context + a few new lines", so the physical artifact plane grows O(K^2) over K rounds.

Here a blob is split at *content-defined* boundaries -- a Gear rolling hash (the FastCDC family,
Xia et al., USENIX ATC'16) evaluated over the bytes, with min/avg/max chunk sizes -- so an append
only changes the tail chunk(s): every earlier chunk keeps its SHA-256 and is found in the
context's chunk pool.  A blob then materialises as a small manifest (ordered chunk hashes) plus
only the chunks the pool has never seen.  Chunks are verified by hash when reassembled.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict

import numpy as np

MIN_CHUNK = 512
AVG_MASK = (1 << 10) - 1  # ~1 KiB average boundary spacing after MIN_CHUNK
MAX_CHUNK = 8192
_GEAR = np.random.default_rng(0x6A09E667).integers(0, 2**32, size=256, dtype=np.uint64).astype(np.uint32)


def chunk_boundaries(data: bytes) -> list[int]:
    """End offsets of content-defined chunks (Gear hash, 32-bit, FastCDC-style cut test)."""
    n = len(data)
    if n <= MIN_CHUNK:
        return [n] if n else []
    arr = np.frombuffer(data, dtype=np.uint8)
    gear = _GEAR[arr]
    cuts: list[int] = []
    start = 0
    h = 0
    i = start + MIN_CHUNK
    # Scalar rolling loop over the region after MIN_CHUNK (bounded by MAX_CHUNK per chunk).
    g = gear.tolist()
    while start < n:
        end_limit = min(n, start + MAX_CHUNK)
        i = start + MIN_CHUNK
        h = 0
        cut = end_limit
        while i < end_limit:
            h = ((h << 1) + g[i]) & 0xFFFFFFFF
            if (h & AVG_MASK) == 0:
                cut = i + 1
                break
            i += 1
        cuts.append(cut)
        start = cut
    return cuts


def split(data: bytes) -> list[bytes]:
    out, prev = [], 0
    for cut in chunk_boundaries(data):
        out.append(data[prev:cut])
        prev = cut
    return out


class ChunkPool:
    """Chunks shared by all tasks of one collaboration context (bounded by bytes, LRU)."""

    def __init__(self, capacity_bytes: int = 64 * 1024 * 1024) -> None:
        self._chunks: OrderedDict[bytes, bytes] = OrderedDict()
        self._bytes = 0
        self.capacity_bytes = int(capacity_bytes)
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def has(self, digest: bytes) -> bool:
        return digest in self._chunks

    def put(self, digest: bytes, physical: bytes) -> None:
        with self.lock:
            if digest in self._chunks:
                self._chunks.move_to_end(digest)
                return
            self._chunks[digest] = physical
            self._bytes += len(physical)
            while self._bytes > self.capacity_bytes and len(self._chunks) > 1:
                _, old = self._chunks.popitem(last=False)
                self._bytes -= len(old)

    def get(self, digest: bytes) -> bytes | None:
        with self.lock:
            return self._chunks.get(digest)


_POOLS: OrderedDict[str, ChunkPool] = OrderedDict()
_POOLS_LOCK = threading.Lock()


def context_pool(context_id: str | None, max_contexts: int = 64) -> ChunkPool | None:
    if not context_id:
        return None
    with _POOLS_LOCK:
        pool = _POOLS.get(context_id)
        if pool is None:
            pool = ChunkPool()
            _POOLS[context_id] = pool
            while len(_POOLS) > max_contexts:
                _POOLS.popitem(last=False)
        else:
            _POOLS.move_to_end(context_id)
        return pool


def reset_context_pools() -> None:
    with _POOLS_LOCK:
        _POOLS.clear()


def chunk_digest(chunk: bytes) -> bytes:
    return hashlib.sha256(chunk).digest()[:16]


__all__ = ["ChunkPool", "chunk_boundaries", "chunk_digest", "context_pool", "reset_context_pools", "split"]
