"""Content-addressed Artifact store with optional durable persistence.

Long payloads are stored once and agent messages carry only ``artifact://`` references.
The default backend remains in-memory for benchmark determinism.  When ``root`` is
provided, blobs are written atomically by SHA-256 and metadata/refcounts are kept in
SQLite so a fresh process can reopen the same store.

The integrity hash detects accidental corruption; it is not an authentication MAC.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import zlib
from dataclasses import dataclass
from typing import Any

from agent_runtime.runtime.errors import ReferenceError_

# fable7 adaptive physical encoding of the artifact data plane.  Content addressing, URIs and
# SHA-256 verification stay defined over the *logical* bytes; only the materialized
# representation changes, and only when it is at least 10% smaller.  Legacy raw blobs remain
# readable; raw payloads that happen to start with a magic are escaped.
_MAGIC_Z = b"ARZ1"
_MAGIC_RAW = b"ARR1"
_MIN_COMPRESS = 256


_MAGIC_CDC = b"ARC1"
CDC_MIN_BLOB = 16 * 1024  # below this, whole-blob zlib is as good and has no manifest overhead


def _chunked_physical(data: bytes, pool: Any, *, compress: bool) -> tuple[bytes, int]:
    """fable11: manifest of content-defined chunks; only chunks new to the context pool are
    materialised (and charged).  Returns (manifest, charged_bytes)."""
    from agent_runtime.artifact_store.cdc import chunk_digest, split

    digests = []
    charged = 0
    for chunk in split(data):
        d = chunk_digest(chunk)
        digests.append(d)
        if not pool.has(d):
            phys = encode_physical(chunk, enabled=compress)
            pool.put(d, phys)
            charged += len(phys)
            pool.misses += 1
        else:
            pool.hits += 1
    manifest = _MAGIC_CDC + b"".join(digests)
    return manifest, charged + len(manifest)


def _reassemble(manifest: bytes, pools: dict, ref: Any) -> bytes | None:
    from agent_runtime.artifact_store.cdc import chunk_digest

    pool = pools.get(ref.content_hash)
    body = manifest[4:]
    if pool is None or len(body) % 16:
        return None
    parts = []
    for i in range(0, len(body), 16):
        d = body[i : i + 16]
        phys = pool.get(d)
        if phys is None:
            return None
        chunk = decode_physical(phys)
        if chunk_digest(chunk) != d:
            return None
        parts.append(chunk)
    data = b"".join(parts)
    if len(data) != ref.size or hashlib.sha256(data).hexdigest() != ref.content_hash:
        return None
    return data


_MAGIC_ZD = b"ARD1"  # fable16: zlib with the shipped domain preset dictionary
_ZDICT: bytes | None = None


def _domain_zdict() -> bytes:
    """Preset DEFLATE dictionary derived deterministically from the shipped docsem model: the
    most frequent words of the host-documentation corpus, filling zlib's 32 KiB window (most
    frequent last, where back-references are cheapest).  Built from generic OS documentation,
    never from task data -- the brotli/zstd trained-dictionary idea."""
    global _ZDICT
    if _ZDICT is None:
        try:
            from agent_runtime.providers.docsem_embedding import compression_dictionary_bytes

            _ZDICT = compression_dictionary_bytes()
        except Exception:  # noqa: BLE001 - dictionary unavailable -> plain zlib only
            _ZDICT = b""
    return _ZDICT


def encode_physical(data: bytes, *, enabled: bool = True) -> bytes:
    if enabled and len(data) >= _MIN_COMPRESS:
        best = _MAGIC_Z + zlib.compress(data, 6)
        zd = _domain_zdict()
        if zd:
            c = zlib.compressobj(6, zlib.DEFLATED, 15, 9, zlib.Z_DEFAULT_STRATEGY, zd)
            cand = _MAGIC_ZD + c.compress(data) + c.flush()
            if len(cand) < len(best):
                best = cand
        if len(best) <= 0.9 * len(data):
            return best
    if data[:4] in (_MAGIC_Z, _MAGIC_RAW, _MAGIC_CDC, _MAGIC_ZD):
        return _MAGIC_RAW + data
    return data


def decode_physical(raw: bytes, expected_size: int | None = None) -> bytes:
    head = raw[:4]
    if head == _MAGIC_Z:
        limit = (expected_size + 1) if expected_size is not None else 0
        d = zlib.decompressobj()
        out = d.decompress(raw[4:], limit) if limit else d.decompress(raw[4:])
        if limit and (d.unconsumed_tail or len(out) > expected_size):
            raise ValueError("compressed artifact exceeds its declared size")
        return out
    if head == _MAGIC_ZD:
        zd = _domain_zdict()
        if not zd:
            raise ValueError("artifact needs the domain dictionary, which is unavailable")
        limit = (expected_size + 1) if expected_size is not None else 0
        d = zlib.decompressobj(15, zdict=zd)
        out = d.decompress(raw[4:], limit) if limit else d.decompress(raw[4:])
        if limit and (d.unconsumed_tail or len(out) > expected_size):
            raise ValueError("compressed artifact exceeds its declared size")
        return out
    if head == _MAGIC_RAW:
        return raw[4:]
    return raw


@dataclass(frozen=True)
class ArtifactRef:
    """Artifact reference transmitted in messages instead of the full payload."""

    uri: str
    content_hash: str
    summary: str
    size: int
    media_type: str
    scope: str


def _to_bytes(content: Any, media_type: str) -> tuple[bytes, str]:
    if isinstance(content, bytes):
        return content, media_type
    if isinstance(content, str):
        return content.encode("utf-8"), media_type
    try:
        payload = json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "JSON artifacts must contain only standard JSON values with finite numbers"
        ) from exc
    return payload, "application/json"


def _summarize(content: Any) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, bytes):
        text = f"<{len(content)} bytes>"
    else:
        text = json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    return text[:48] + "…" if len(text) > 48 else text


_PERSISTENT_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS refs (
    uri TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    summary TEXT NOT NULL,
    size INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    refcount INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS blobs (
    content_hash TEXT PRIMARY KEY,
    size INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refs_hash ON refs(content_hash);
"""


class ArtifactStore:
    """Content-addressed Artifact store.

    Parameters
    ----------
    task_id:
        Namespace used for task-scoped artifact URIs.
    root:
        Optional persistent directory.  ``None`` keeps the original in-memory backend.
    verify_reads:
        Recompute SHA-256 when reading a persistent blob.  Enabled by default because
        artifacts are normally much larger than metadata but still small enough for the
        competition workloads; deployments may disable it when a lower layer already
        provides integrity guarantees.
    """

    def __init__(
        self,
        task_id: str = "global",
        *,
        root: str | os.PathLike[str] | None = None,
        verify_reads: bool = True,
        compress: bool | None = None,
    ) -> None:
        self.task_id = task_id
        if compress is None:
            compress = os.environ.get("AGENT_ARTIFACT_COMPRESSION", "1").strip().lower() not in {"0", "false", "no", "off"}
        self.compress = bool(compress)
        # Logical bytes of newly materialized objects (before physical encoding), for disclosure.
        self.logical_materialized_bytes = 0
        self.cdc_blobs = 0
        self.verify_reads = bool(verify_reads)
        self.root = Path(root) if root is not None else None
        self._blobs: dict[str, bytes] = {}
        self._meta: dict[str, ArtifactRef] = {}
        # Dedup-aware physical data-plane bytes materialized by this store instance (v0.6.6):
        # counted once per new content hash, so it belongs in the strict transport total.
        self.materialized_bytes = 0
        self._refcount: dict[str, int] = {}
        self._conn: sqlite3.Connection | None = None
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            self._blob_root = self.root / "blobs"
            self._blob_root.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.root / "artifacts.sqlite3", timeout=5.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_PERSISTENT_SCHEMA)
            self._conn.commit()
            try:
                os.chmod(self.root / "artifacts.sqlite3", 0o600)
            except OSError:
                pass
            for path in (self.root, self._blob_root):
                try:
                    os.chmod(path, 0o700)
                except OSError:
                    pass

    @property
    def persistent(self) -> bool:
        return self._conn is not None

    def put(
        self,
        content: Any,
        *,
        media_type: str = "application/json",
        scope: str = "task",
        summary: str | None = None,
        chunk_pool: Any = None,
    ) -> ArtifactRef:
        if scope not in {"task", "global"}:
            raise ValueError("scope must be 'task' or 'global'")
        if chunk_pool is None:
            chunk_pool = getattr(self, "default_chunk_pool", None)
        data, media_type = _to_bytes(content, media_type)
        content_hash = hashlib.sha256(data).hexdigest()
        # Keep 128 bits of the SHA-256 in externally visible URIs.  The full digest is
        # still verified on reads; the longer prefix materially lowers accidental or
        # adversarial namespace-collision risk compared with the historical 64-bit ID.
        artifact_id = content_hash[:32]
        prefix = f"task/{self.task_id}" if scope == "task" else "global"
        uri = f"artifact://{prefix}/{artifact_id}"
        ref = ArtifactRef(
            uri=uri,
            content_hash=content_hash,
            summary=summary or _summarize(content),
            size=len(data),
            media_type=media_type,
            scope=scope,
        )

        if self._conn is None:
            if content_hash not in self._blobs:
                if chunk_pool is not None and len(data) >= CDC_MIN_BLOB:
                    physical, charged = _chunked_physical(data, chunk_pool, compress=self.compress)
                    self.__dict__.setdefault("_chunk_pools", {})[content_hash] = chunk_pool
                    self.cdc_blobs += 1
                else:
                    physical = encode_physical(data, enabled=self.compress)
                    charged = len(physical)
                self.materialized_bytes += charged
                self.logical_materialized_bytes += len(data)
                self._blobs[content_hash] = physical
            self._refcount[content_hash] = self._refcount.get(content_hash, 0) + 1
            self._meta[uri] = ref
            return ref

        physical = encode_physical(data, enabled=self.compress)
        if uri not in self._meta:
            self.materialized_bytes += len(physical)
            self.logical_materialized_bytes += len(data)
        self._persist_blob(content_hash, physical)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO blobs(content_hash,size) VALUES(?,?)",
                (content_hash, len(data)),
            )
            # One atomic UPSERT avoids the SELECT-then-INSERT race when independent
            # processes publish the same content-addressed URI concurrently.
            self._conn.execute(
                """INSERT INTO refs(uri,content_hash,summary,size,media_type,scope,refcount)
                VALUES(?,?,?,?,?,?,1)
                ON CONFLICT(uri) DO UPDATE SET
                    refcount=refs.refcount+1,
                    summary=excluded.summary""",
                (
                    uri,
                    content_hash,
                    ref.summary,
                    ref.size,
                    ref.media_type,
                    ref.scope,
                ),
            )
        return ref

    def _blob_path(self, content_hash: str) -> Path:
        assert self.root is not None
        # The digest may come back from persistent SQLite metadata, not only from our
        # own put() path.  Revalidate it before using it as a pathname so a corrupt or
        # tampered DB cannot turn GC/read operations into path traversal.
        if not isinstance(content_hash, str) or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise ValueError("invalid artifact content hash in persistent metadata")
        shard = self._blob_root / content_hash[:2]
        shard.mkdir(parents=True, exist_ok=True)
        return shard / content_hash

    def _persist_blob(self, content_hash: str, data: bytes) -> None:
        """Persist the *physical* encoding ``data`` of the blob named by ``content_hash``."""
        path = self._blob_path(content_hash)
        if self._blob_is_valid(path, content_hash):
            # A valid content-addressed blob is already durable.  If the pathname exists
            # but bytes are corrupted, do *not* bless it and do not make recovery
            # impossible: write the known-good payload to a temp file and atomically
            # replace the damaged blob below.
            return

        fd, tmp_name = tempfile.mkstemp(prefix=f".{content_hash}.", suffix=".tmp", dir=path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            try:
                os.replace(tmp, path)
            except PermissionError:
                # Windows can reject the replace when another writer has just created
                # the same content-addressed target.  A verified target means that
                # writer completed the operation for us; do not turn that benign race
                # into a failed publish.  Corrupt or missing targets still surface the
                # original error.
                if not self._blob_is_valid(path, content_hash):
                    raise
            self._fsync_dir(path.parent)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _blob_is_valid(path: Path, content_hash: str) -> bool:
        if not path.exists():
            return False
        try:
            return hashlib.sha256(decode_physical(path.read_bytes())).hexdigest() == content_hash
        except (OSError, ValueError, zlib.error):
            return False

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def get_bytes(self, uri: str) -> bytes:
        ref = self.get_ref(uri)
        if ref is None:
            raise ReferenceError_(f"Artifact 引用失效: {uri}")
        if self._conn is None:
            raw = self._blobs.get(ref.content_hash)
            if raw is None:
                raise ReferenceError_(f"Artifact 引用失效: {uri}")
            if raw[:4] == _MAGIC_CDC:
                data = _reassemble(raw, self.__dict__.get("_chunk_pools", {}), ref)
                if data is None:
                    raise ReferenceError_(f"Artifact 分块缺失或损坏: {uri}")
                return data
            if raw[:4] not in (_MAGIC_Z, _MAGIC_ZD):
                return decode_physical(raw, ref.size)
            # fable8: immutable content-addressed blob -> decode each compressed blob once.
            cache = self.__dict__.setdefault("_decoded", {})
            hit = cache.get(ref.content_hash)
            if hit is not None and hit[0] is raw:
                return hit[1]
            data = decode_physical(raw, ref.size)
            if len(data) != ref.size:
                raise ReferenceError_(f"Artifact 大小校验失败: {uri}")
            if len(cache) >= 64:
                cache.pop(next(iter(cache)))
            cache[ref.content_hash] = (raw, data)
            return data

        try:
            path = self._blob_path(ref.content_hash)
        except ValueError as exc:
            raise ReferenceError_(f"Artifact 元数据损坏: {uri}") from exc
        try:
            data = decode_physical(path.read_bytes(), ref.size)
        except OSError as exc:
            raise ReferenceError_(f"Artifact 引用失效: {uri}") from exc
        except (ValueError, zlib.error) as exc:
            raise ReferenceError_(f"Artifact 内容损坏: {uri}") from exc
        if len(data) != ref.size:
            raise ReferenceError_(f"Artifact 大小校验失败: {uri}")
        if self.verify_reads:
            actual = hashlib.sha256(data).hexdigest()
            if actual != ref.content_hash:
                raise ReferenceError_(f"Artifact 内容哈希校验失败: {uri}")
        return data

    PROJECTION_MEDIA_TYPE = "application/vnd.agent-runtime.projection+json"

    def put_projection(self, base_uri: str, keys: list[str], *, summary: str | None = None) -> ArtifactRef:
        """Store a *view* over an existing JSON-object artifact instead of a second copy (v0.6.6).

        Evidence is usually a key subset of the task facts that are already on the data plane.
        The projection object holds only the base URI and key list; reads resolve it back to the
        exact dict ``{k: base[k] for k in keys}``, so consumers are unaffected.
        """
        base = self.get_json(base_uri)
        if not isinstance(base, dict):
            raise ValueError("projection base must be a JSON object artifact")
        return self.put(
            {"__projection__": {"base": base_uri, "keys": [str(k) for k in keys]}},
            media_type=self.PROJECTION_MEDIA_TYPE,
            summary=summary or f"projection(n={len(keys)})",
        )

    def get_json(self, uri: str) -> Any:
        value = json.loads(self.get_bytes(uri).decode("utf-8"))
        if isinstance(value, dict) and set(value) == {"__projection__"}:
            spec = value["__projection__"]
            base = self.get_json(str(spec["base"]))
            if not isinstance(base, dict):
                raise ReferenceError_(f"projection base is not an object: {spec['base']}")
            return {k: base.get(k) for k in spec["keys"]}
        return value

    def get_ref(self, uri: str) -> ArtifactRef | None:
        if self._conn is None:
            return self._meta.get(uri)
        row = self._conn.execute(
            "SELECT uri,content_hash,summary,size,media_type,scope FROM refs WHERE uri=?",
            (uri,),
        ).fetchone()
        if row is None:
            return None
        scope = row["scope"]
        if scope == "task" and not uri.startswith(f"artifact://task/{self.task_id}/"):
            # Persistent roots are shared across tasks for physical deduplication, but a
            # task-scoped reference is still an access boundary. Knowing another task's
            # URI must not be enough to read its payload through this store instance.
            return None
        return ArtifactRef(
            uri=row["uri"],
            content_hash=row["content_hash"],
            summary=row["summary"],
            size=int(row["size"]),
            media_type=row["media_type"],
            scope=scope,
        )

    def exists(self, uri: str) -> bool:
        ref = self.get_ref(uri)
        if ref is None:
            return False
        if self._conn is None:
            return ref.content_hash in self._blobs
        try:
            path = self._blob_path(ref.content_hash)
        except ValueError:
            return False
        if not path.is_file():
            return False
        if not self.verify_reads:
            return True
        try:
            data = decode_physical(path.read_bytes(), ref.size)
            if len(data) != ref.size:
                return False
            return hashlib.sha256(data).hexdigest() == ref.content_hash
        except (OSError, ValueError, zlib.error):
            return False

    def incref(self, uri: str) -> None:
        ref = self.get_ref(uri)
        if ref is None:
            return
        if self._conn is None:
            self._refcount[ref.content_hash] = self._refcount.get(ref.content_hash, 0) + 1
            return
        with self._conn:
            self._conn.execute("UPDATE refs SET refcount=refcount+1 WHERE uri=?", (uri,))

    def decref(self, uri: str) -> None:
        ref = self.get_ref(uri)
        if ref is None:
            return
        if self._conn is None:
            self._refcount[ref.content_hash] = max(0, self._refcount.get(ref.content_hash, 0) - 1)
            return
        with self._conn:
            self._conn.execute(
                "UPDATE refs SET refcount=CASE WHEN refcount>0 THEN refcount-1 ELSE 0 END WHERE uri=?",
                (uri,),
            )

    def collect(self) -> int:
        """Collect unreferenced blobs; return the number of physical blobs removed."""
        if self._conn is None:
            removed = 0
            for content_hash, count in list(self._refcount.items()):
                if count <= 0:
                    self._blobs.pop(content_hash, None)
                    self._refcount.pop(content_hash, None)
                    removed += 1
            for uri, ref in list(self._meta.items()):
                if ref.content_hash not in self._blobs:
                    self._meta.pop(uri, None)
            return removed

        assert self._conn is not None
        removed = 0
        with self._conn:
            dead_refs = self._conn.execute(
                "SELECT uri,content_hash FROM refs WHERE refcount<=0"
            ).fetchall()
            for row in dead_refs:
                self._conn.execute("DELETE FROM refs WHERE uri=?", (row["uri"],))
            candidate_hashes = {row["content_hash"] for row in dead_refs}
            for content_hash in candidate_hashes:
                remaining = self._conn.execute(
                    "SELECT COUNT(*) FROM refs WHERE content_hash=? AND refcount>0",
                    (content_hash,),
                ).fetchone()[0]
                if remaining:
                    continue
                try:
                    path = self._blob_path(content_hash)
                except ValueError:
                    # Corrupt metadata is removed from the catalog but never interpreted
                    # as a filesystem path.
                    self._conn.execute("DELETE FROM blobs WHERE content_hash=?", (content_hash,))
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                self._conn.execute("DELETE FROM blobs WHERE content_hash=?", (content_hash,))
                removed += 1
        return removed

    def invalidate(self, uri: str) -> None:
        """Invalidate the physical blob while keeping metadata for dangling-ref tests."""
        ref = self.get_ref(uri)
        if ref is None:
            return
        if self._conn is None:
            self._blobs.pop(ref.content_hash, None)
            return
        try:
            path = self._blob_path(ref.content_hash)
        except ValueError:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "ArtifactStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __len__(self) -> int:
        if self._conn is None:
            return len(self._blobs)
        return int(self._conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0])


__all__ = ["ArtifactStore", "ArtifactRef"]
