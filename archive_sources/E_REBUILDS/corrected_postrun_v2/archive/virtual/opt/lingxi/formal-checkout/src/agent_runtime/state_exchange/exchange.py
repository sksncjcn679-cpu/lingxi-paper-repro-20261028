"""非文本中间状态交换。

在 Agent 间直接交换非文本中间表示，减少「内部状态—文本—内部状态」反复编解码：

- **embedding / 语义向量**：用于语义检索与路由；
- **compact state vector**：对计划/进度的紧凑数值特征；
- **plan DAG**：结构化计划图。

本模块实现三项低开销机制：

1. **内容寻址去重**：同一 task 内相同状态只物化一次，后续仅复用 ``state://`` 引用；
2. **传输精度压缩**：默认 adaptive；优先尝试 int8 per-vector symmetric scalar
   quantization，并在余弦保真度低于阈值时自动回退 float16；也可固定 float32/float16/
   int8 做严格消融。量化 scale 作为真实 payload 一起物化；
3. **只读共享视图**：float32/float16 数组通过 ``SharedVectorBuffer.get_view`` 消费，
   共享内存后端不再复制 payload。int8 在计算前需要一次 float32 解码，因此不宣称
   解码端 zero-copy，只宣称传输/存储 payload 更小。

内容哈希始终基于逻辑 float32 embedding，因此切换 transport precision 不改变同一
语义状态的内容身份。
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Callable

import numpy as np

from agent_runtime.providers.base import EmbeddingProvider, validate_embedding_matrix
from agent_runtime.runtime.errors import ReferenceError_
from agent_runtime.state_exchange.delta import JsonValue, VersionedStateLedger, VersionedStateRef
from agent_runtime.state_exchange.shared_buffer import SharedArrayDescriptor, SharedVectorBuffer


@dataclass(frozen=True)
class StateRef:
    """非文本状态引用及其物化开销。"""

    uri: str
    kind: str  # "plan" | "embedding" | "vector"
    shape: tuple
    dtype: str  # 实际存储 dtype
    summary: str
    content_hash: str
    # 当前调用对应对象的物理大小（去重后的对象本体大小）。
    nbytes: int
    # 若按原始 float32 状态传输，本次逻辑 payload 大小。
    logical_nbytes: int
    # 本次 put 真正新增物化的字节数；命中内容寻址去重时为 0。
    materialized_bytes: int
    transport: str  # "shm" | "inproc"
    precision: str  # "float32" | "float16" | "int8"
    deduplicated: bool = False
    # int8 symmetric quantization 的正比例 scale；非 int8 为 None。scale 本身也实际
    # 以一个 float32 标量物化到 buffer，因此已计入 nbytes/materialized_bytes。
    quant_scale: float | None = None


class OperandRankingProbe:
    """Declarative, serialisable receiver-decision contract (fable10).

    Represents the Retriever's use of a plan embedding: rank operand vectors by similarity
    (stable sort).  Being plain data (the operand matrix) rather than a closure, it can cross a
    process boundary with the state request -- required once Agents run in their own processes.
    """

    __slots__ = ("vectors",)

    def __init__(self, vectors: np.ndarray) -> None:
        self.vectors = np.ascontiguousarray(vectors, dtype=np.float32)

    def __call__(self, vec: np.ndarray) -> tuple[int, ...]:
        sims = self.vectors @ np.asarray(vec, dtype=np.float32)
        return tuple(int(i) for i in np.argsort(-sims, kind="stable"))

    def __getstate__(self):
        return self.vectors

    def __setstate__(self, state):
        self.vectors = state


_INT4_COSINE_FLOOR = 0.90  # sanity floor; the decision certificate is the real criterion


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 1.0 if denom == 0.0 else float(np.dot(a, b) / denom)


def _int4_pack(x: np.ndarray) -> tuple[np.ndarray, float]:
    """Symmetric per-vector int4 (values -7..7), two nibbles per byte (offset-8 encoding)."""
    max_abs = float(np.max(np.abs(x))) if x.size else 0.0
    scale = max_abs / 7.0 if max_abs > 0.0 else 1.0
    q = (np.clip(np.rint(x / scale), -7, 7).astype(np.int16) + 8).astype(np.uint8)
    if q.size % 2:
        q = np.concatenate([q, np.array([8], dtype=np.uint8)])
    packed = (q[0::2] << 4) | q[1::2]
    return np.ascontiguousarray(packed.astype(np.uint8)), scale


def _int4_unpack(packed: np.ndarray, scale: float, dim: int) -> np.ndarray:
    p = np.asarray(packed, dtype=np.uint8)
    if dim < 0 or (dim + 1) // 2 != p.size:
        raise ReferenceError_("int4 payload size does not match its logical dimension")
    q = np.empty(p.size * 2, dtype=np.int16)
    q[0::2] = p >> 4
    q[1::2] = p & 0x0F
    return (q[:dim] - 8).astype(np.float32) * np.float32(scale)


def _q4_block_pack(x: np.ndarray, block: int) -> tuple[np.ndarray, np.ndarray]:
    n = x.shape[0]
    pad = (-n) % block
    xp = np.concatenate([x, np.zeros(pad, np.float32)]).reshape(-1, block)
    scales = (np.abs(xp).max(axis=1) / 7.0).astype(np.float16)
    safe = scales.astype(np.float32)
    safe[safe == 0] = 1.0
    q = (np.clip(np.rint(xp / safe[:, None]), -7, 7).astype(np.int16) + 8).astype(np.uint8).ravel()[:n]
    if q.size % 2:
        q = np.concatenate([q, np.array([8], dtype=np.uint8)])
    return np.ascontiguousarray(((q[0::2] << 4) | q[1::2]).astype(np.uint8)), np.ascontiguousarray(scales)


def _q4_block_unpack(packed: np.ndarray, scales: np.ndarray, dim: int, block: int) -> np.ndarray:
    p = np.asarray(packed, dtype=np.uint8)
    sc = np.asarray(scales, dtype=np.float16).astype(np.float32)
    if (dim + 1) // 2 != p.size or sc.size != (dim + block - 1) // block:
        raise ReferenceError_("block-int4 payload does not match its logical dimension")
    q = np.empty(p.size * 2, dtype=np.int16)
    q[0::2] = p >> 4
    q[1::2] = p & 0x0F
    vals = (q[:dim] - 8).astype(np.float32)
    sc[sc == 0] = 1.0
    return vals * np.repeat(sc, block)[:dim]


def _enc_int8(x):
    s = float(np.max(np.abs(x))) / 127.0 if x.size else 1.0
    s = s or 1.0
    return np.ascontiguousarray(np.clip(np.rint(x / s), -127, 127).astype(np.int8)), float(s)


_CANDIDATES = (
    ("int4", lambda x: _int4_pack(x)),
    ("q4b32", lambda x: _q4_block_pack(x, 32)),
    ("q4b16", lambda x: _q4_block_pack(x, 16)),
    ("q4b8", lambda x: _q4_block_pack(x, 8)),
    ("int8", _enc_int8),
    ("float16", lambda x: (np.ascontiguousarray(x, dtype=np.float16), None)),
)


def _decode_candidate(name: str, payload: np.ndarray, scale, dim: int) -> np.ndarray:
    if name == "int4":
        return _int4_unpack(payload, float(scale), dim)
    if name.startswith("q4b"):
        return _q4_block_unpack(payload, scale, dim, int(name[3:]))
    if name == "int8":
        return payload.astype(np.float32) * float(scale)
    return np.asarray(payload, dtype=np.float32)


class StateExchange:
    """非文本状态的生成、存储、引用复用与解释。"""

    _PRECISIONS = {"float32", "float16", "int8", "adaptive"}

    def __init__(
        self,
        embedding: EmbeddingProvider,
        task_id: str = "global",
        use_shared_memory: bool = False,
        precision: str = "adaptive",
        adaptive_cosine_threshold: float = 0.999,
        deduplicate: bool = True,
        on_large_state: Callable[[bytes], bool | tuple[bool, int]] | None = None,
        fd_handoff_min_bytes: int = 1024 * 1024,
    ) -> None:
        precision = precision.lower()
        if precision not in self._PRECISIONS:
            raise ValueError(f"unsupported state precision: {precision}")
        self.embedding = embedding
        self.task_id = task_id
        self.precision = precision
        threshold = float(adaptive_cosine_threshold)
        if not np.isfinite(threshold) or not 0.0 < threshold <= 1.0:
            raise ValueError("adaptive_cosine_threshold must be in (0, 1]")
        self.adaptive_cosine_threshold = threshold
        self.deduplicate = deduplicate
        if fd_handoff_min_bytes <= 0:
            raise ValueError("fd_handoff_min_bytes must be positive")
        self._on_large_state = on_large_state
        self._fd_handoff_min_bytes = int(fd_handoff_min_bytes)
        self._buffer = SharedVectorBuffer(use_shared_memory)
        self._dags: dict[str, dict[str, Any]] = {}
        self._meta: dict[str, StateRef] = {}
        # Immutable control-plane state history. Numeric tensors remain in SharedVectorBuffer;
        # evolving JSON-like DAG/metadata can advance through verified deltas.
        self._versioned = VersionedStateLedger(namespace=task_id)
        import os as _os

        # fable9: decision-certified precision ladder (AGENT_DECISION_CERTIFIED=0 disables).
        self.decision_certified = _os.environ.get("AGENT_DECISION_CERTIFIED", "1").strip().lower() not in {"0", "false", "no", "off"}
        self.certificates = 0
        self.certificate_rejections = 0

    @property
    def transport(self) -> str:
        return "shm" if (self._buffer.use_shared_memory and not self._buffer.degraded) else "inproc"

    def _encode_embedding(
        self, embedding: np.ndarray, decision_probe: Any = None
    ) -> tuple[np.ndarray, float | None, str]:
        """按传输精度编码 embedding，并返回 ``(payload, scale, actual_precision)``。

        ``int8`` 使用每向量对称标量量化：``q = round(x / scale)``，范围截断到
        ``[-127, 127]``，其中 ``scale = max(abs(x)) / 127``。它不依赖语料校准，适合
        瞬时 Runtime state；代价是消费时需要一次解码分配。因此这是一个 transport
        codec，而不是替代 Sentence-Transformers 校准型 int8 检索索引的实现。
        """
        if self.precision == "float16":
            return np.ascontiguousarray(embedding, dtype=np.float16), None, "float16"
        probe = decision_probe
        if self.precision == "adaptive" and probe is not None and self.decision_certified:
            # fable9/fable14 decision-certified codec search: candidate encodings are tried in
            # increasing physical size -- global int4, block-scaled int4 (32/16/8-dim blocks with
            # fp16 scales, the GGUF Q4 idea), int8, fp16, fp32 -- and the FIRST one under which
            # the receiver's declared decision equals its float32 decision is kept.  Exact, not
            # statistical: the receiver decodes exactly the certified bytes.
            source = np.ascontiguousarray(embedding, dtype=np.float32)
            reference = probe(source)
            for i, (name, encode) in enumerate(_CANDIDATES):
                payload, scale = encode(source)
                decoded = _decode_candidate(name, payload, scale, source.shape[0])
                floor = _INT4_COSINE_FLOOR if name.startswith(("int4", "q4b")) else self.adaptive_cosine_threshold
                if name == "float16":
                    floor = 0.0
                if probe(decoded) == reference and _cosine(source, decoded) >= floor:
                    self.certificates += 1
                    if i:
                        self.certificate_rejections += 1
                    return payload, scale, name
            self.certificate_rejections += 1
            return source, None, "float32"
        if self.precision in {"int8", "adaptive"}:
            source = np.ascontiguousarray(embedding, dtype=np.float32)
            max_abs = float(np.max(np.abs(source))) if source.size else 0.0
            scale = max_abs / 127.0 if max_abs > 0.0 else 1.0
            quantized = np.clip(np.rint(source / scale), -127, 127).astype(np.int8)
            quantized = np.ascontiguousarray(quantized)
            if self.precision == "adaptive":
                reconstructed = quantized.astype(np.float32) * float(scale)
                denom = float(np.linalg.norm(source) * np.linalg.norm(reconstructed))
                cosine = 1.0 if denom == 0.0 else float(np.dot(source, reconstructed) / denom)
                if cosine < self.adaptive_cosine_threshold:
                    return np.ascontiguousarray(source, dtype=np.float16), None, "float16"
            return quantized, float(scale), "int8"
        return np.ascontiguousarray(embedding, dtype=np.float32), None, "float32"

    def _delete_payload(self, uri: str, *, include_vector: bool) -> None:
        self._buffer.delete(f"{uri}#emb")
        self._buffer.delete(f"{uri}#scale")
        if include_vector:
            self._buffer.delete(f"{uri}#vec")

    def _store_embedding(
        self, uri: str, embedding_f32: np.ndarray, decision_probe: Any = None
    ) -> tuple[int, np.ndarray, float | None, str]:
        encoded, scale, actual_precision = self._encode_embedding(embedding_f32, decision_probe)
        physical_nbytes = self._buffer.put(f"{uri}#emb", encoded)
        if isinstance(scale, np.ndarray):
            # block formats: the per-block fp16 scales are real transport payload.
            physical_nbytes += self._buffer.put(f"{uri}#scale", scale)
            scale = None
        elif scale is not None:
            # Scale is part of the real transport payload, not hidden Python metadata.
            physical_nbytes += self._buffer.put(
                f"{uri}#scale", np.asarray([scale], dtype=np.float32)
            )
        if self._on_large_state is not None and encoded.nbytes >= self._fd_handoff_min_bytes:
            outcome = self._on_large_state(np.ascontiguousarray(encoded).tobytes())
            ok, _socket_bytes = outcome if isinstance(outcome, tuple) else (outcome, 0)
            if not ok:
                raise ReferenceError_("sealed state attachment rejected by router")
        return physical_nbytes, encoded, scale, actual_precision

    def _decoded_embedding(self, uri: str, ref: StateRef) -> np.ndarray:
        """Return computation-ready embedding.

        float32/float16 keep the low-copy read-only transport view. int8 must dequantize
        into a float32 array; the returned array is read-only to preserve immutable state
        semantics even though the decode itself allocates.
        """
        encoded = self._buffer.get_view(f"{uri}#emb")
        if ref.precision.startswith("q4b"):
            if not self._buffer.exists(f"{uri}#scale"):
                raise ReferenceError_(f"block-int4 scales missing: {uri}")
            decoded = _q4_block_unpack(encoded, self._buffer.get_view(f"{uri}#scale"), int(ref.shape[0]), int(ref.precision[3:]))
            decoded.setflags(write=False)
            return decoded
        if ref.precision == "int4":
            if not self._buffer.exists(f"{uri}#scale"):
                raise ReferenceError_(f"int4 quantization scale missing: {uri}")
            decoded = _int4_unpack(encoded, float(self._buffer.get_view(f"{uri}#scale")[0]), int(ref.shape[0]))
            decoded.setflags(write=False)
            return decoded
        if ref.precision != "int8":
            return encoded
        if not self._buffer.exists(f"{uri}#scale"):
            raise ReferenceError_(f"int8 quantization scale missing: {uri}")
        scale_view = self._buffer.get_view(f"{uri}#scale")
        scale = float(scale_view[0])
        decoded = encoded.astype(np.float32) * scale
        decoded.setflags(write=False)
        return decoded


    # ----- versioned delta state -----
    def put_versioned_state(self, value: JsonValue) -> VersionedStateRef:
        """Create an immutable full snapshot for evolving control-plane state."""
        return self._versioned.put_snapshot(value)

    def update_versioned_state(
        self,
        base_uri: str,
        value: JsonValue,
        *,
        require_current_head: bool = True,
    ) -> VersionedStateRef:
        """Advance state with a verified delta when economical, else a full snapshot.

        ``require_current_head`` implements optimistic concurrency control: a stale agent cannot
        silently overwrite a newer state revision.
        """
        return self._versioned.update(
            base_uri, value, require_current_head=require_current_head
        )

    def get_versioned_state(self, uri: str) -> JsonValue:
        """Resolve and integrity-check a versioned state URI."""
        return self._versioned.resolve(uri)

    def get_versioned_ref(self, uri: str) -> VersionedStateRef | None:
        return self._versioned.get_ref(uri)

    # ----- 生成与存储 -----
    def put_plan_state(self, plan: dict[str, Any], *, decision_probe: Any = None) -> StateRef:
        """把计划编码为 DAG + embedding + compact vector 的非文本状态。"""
        try:
            plan_text = json.dumps(
                plan,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("plan state must be canonical JSON data with finite numbers") from exc
        matrix = validate_embedding_matrix(
            self.embedding.embed([plan_text]),
            expected_rows=1,
            expected_dim=getattr(self.embedding, "dim", None),
            context="state embedding provider",
        )
        embedding_f32 = matrix[0]
        vector = self._compact_vector(plan)

        # 内容哈希基于逻辑 float32 表示，与 transport precision 无关。
        content_hash = hashlib.sha256(
            plan_text.encode("utf-8") + np.ascontiguousarray(embedding_f32).tobytes()
        ).hexdigest()
        sid = content_hash[:32]
        uri = f"state://{self.task_id}/{sid}"
        logical_nbytes = int(embedding_f32.nbytes + vector.nbytes)

        if self.deduplicate and uri in self._meta:
            existing = self._meta[uri]
            return replace(existing, materialized_bytes=0, deduplicated=True)
        if not self.deduplicate and uri in self._meta:
            # Legacy/no-dedup 对照仍允许相同逻辑 state 再次物化，但先安全释放旧对象。
            self._delete_payload(uri, include_vector=True)

        physical_nbytes, encoded, scale, actual_precision = self._store_embedding(uri, embedding_f32, decision_probe)
        physical_nbytes += self._buffer.put(f"{uri}#vec", vector)
        self._dags[uri] = deepcopy(plan)
        ref = StateRef(
            uri=uri,
            kind="plan",
            # int4 is nibble-packed; the ref keeps the LOGICAL shape needed to unpack it.
            shape=tuple(embedding_f32.shape) if actual_precision.startswith(("int4", "q4b")) else tuple(encoded.shape),
            dtype=str(encoded.dtype),
            summary=f"plan(op={plan.get('operation')},n={len(plan.get('operands', []))})",
            content_hash=content_hash,
            nbytes=physical_nbytes,
            logical_nbytes=logical_nbytes,
            materialized_bytes=physical_nbytes,
            transport=self.transport,
            precision=actual_precision,
            quant_scale=scale,
        )
        self._meta[uri] = ref
        return ref

    def embed_text(self, text: str, kind: str = "embedding") -> StateRef:
        """把文本编码为语义向量状态（vector:// 引用）。"""
        matrix = validate_embedding_matrix(
            self.embedding.embed([text]),
            expected_rows=1,
            expected_dim=getattr(self.embedding, "dim", None),
            context="state embedding provider",
        )
        vec_f32 = matrix[0]
        content_hash = hashlib.sha256(np.ascontiguousarray(vec_f32).tobytes()).hexdigest()
        sid = content_hash[:32]
        uri = f"vector://{self.task_id}/{sid}"
        logical_nbytes = int(vec_f32.nbytes)

        if self.deduplicate and uri in self._meta:
            existing = self._meta[uri]
            return replace(existing, materialized_bytes=0, deduplicated=True)
        if not self.deduplicate and uri in self._meta:
            self._delete_payload(uri, include_vector=False)

        physical_nbytes, encoded, scale, actual_precision = self._store_embedding(uri, vec_f32)
        ref = StateRef(
            uri=uri,
            kind=kind,
            shape=tuple(encoded.shape),
            dtype=str(encoded.dtype),
            summary=text[:32],
            content_hash=content_hash,
            nbytes=physical_nbytes,
            logical_nbytes=logical_nbytes,
            materialized_bytes=physical_nbytes,
            transport=self.transport,
            precision=actual_precision,
            quant_scale=scale,
        )
        self._meta[uri] = ref
        return ref

    # ----- 解释（接收方消费） -----
    def get_plan_state(self, uri: str) -> dict[str, Any]:
        """还原计划状态。

        float32/float16 embedding 与 compact vector 使用只读 view；int8 embedding 会先
        解码为只读 float32 数组，因为量化 scale 必须恢复到计算域。
        """
        ref = self._meta.get(uri)
        if not self._is_ref_readable(uri, ref):
            raise ReferenceError_(f"状态引用失效: {uri}")
        assert ref is not None
        try:
            return {
                "dag": deepcopy(self._dags[uri]),
                "embedding": self._decoded_embedding(uri, ref),
                "vector": self._buffer.get_view(f"{uri}#vec"),
            }
        except (KeyError, ValueError) as exc:
            raise ReferenceError_(f"状态引用失效: {uri}") from exc

    def get_vector(self, uri: str, *, copy: bool = False) -> np.ndarray:
        """读取计算域向量。

        float32/float16 默认返回 transport read-only view；int8 会返回解码后的 read-only
        float32 数组。``copy=True`` 总是返回独立可写数组。
        """
        ref = self._meta.get(uri)
        if ref is None or not self._buffer.exists(f"{uri}#emb"):
            raise ReferenceError_(f"向量引用失效: {uri}")
        decoded = self._decoded_embedding(uri, ref)
        if copy:
            return np.array(decoded, copy=True)
        return decoded

    def get_encoded_vector(self, uri: str) -> np.ndarray:
        """返回真正上线/共享的只读 transport 表示，用于诊断和专项实验。"""
        if uri not in self._meta or not self._buffer.exists(f"{uri}#emb"):
            raise ReferenceError_(f"向量引用失效: {uri}")
        return self._buffer.get_view(f"{uri}#emb")

    def get_shared_descriptors(
        self, uri: str
    ) -> dict[str, SharedArrayDescriptor] | None:
        """返回 plan/vector 状态的跨进程共享内存描述符。

        仅当 shared-memory 后端真实可用时返回；DAG 元数据仍由 runtime 控制面解析，
        描述符承载数值 payload。int8 模式会额外返回 ``scale`` descriptor，确保独立
        consumer 可以完整重建计算域向量。
        """
        if uri not in self._meta:
            return None
        emb = self._buffer.descriptor(f"{uri}#emb")
        if emb is None:
            return None
        ref = self._meta[uri]
        out = {"embedding": emb}
        vec = self._buffer.descriptor(f"{uri}#vec")
        if ref.kind == "plan":
            # A mixed shm/in-process fallback is not a complete cross-process plan
            # transport.  Fail closed instead of returning a descriptor set that a fresh
            # consumer cannot reconstruct end-to-end.
            if vec is None:
                return None
            out["vector"] = vec
        scale = self._buffer.descriptor(f"{uri}#scale")
        if ref.precision in ("int8", "int4") or ref.precision.startswith("q4b"):
            if scale is None:
                return None
            out["scale"] = scale
        elif scale is not None:
            out["scale"] = scale
        return out

    def get_ref(self, uri: str) -> StateRef | None:
        return self._meta.get(uri)

    def _is_ref_readable(self, uri: str, ref: StateRef | None) -> bool:
        if ref is None or not self._buffer.exists(f"{uri}#emb"):
            return False
        if ref.precision == "int8" and not self._buffer.exists(f"{uri}#scale"):
            return False
        if ref.kind == "plan":
            return uri in self._dags and self._buffer.exists(f"{uri}#vec")
        return True

    def exists(self, uri: str) -> bool:
        """Return True only when the reference is currently consumable end-to-end."""
        return self._is_ref_readable(uri, self._meta.get(uri))

    def invalidate(self, uri: str) -> None:
        """使状态引用失效（用于演示降级路径）。"""
        self._dags.pop(uri, None)

    def _compact_vector(self, plan: dict[str, Any]) -> np.ndarray:
        """计划的紧凑数值特征（compact state vector）。"""
        operands = plan.get("operands", [])
        op = str(plan.get("operation", ""))
        op_code = (int(hashlib.sha256(op.encode()).hexdigest(), 16) % 97) / 97.0
        return np.array(
            [float(len(operands)), op_code, 1.0 if operands else 0.0],
            dtype=np.float32,
        )

    def close(self) -> None:
        self._buffer.close()
        self._dags.clear()
        self._meta.clear()


__all__ = ["StateExchange", "StateRef"]
