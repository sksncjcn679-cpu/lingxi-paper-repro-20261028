"""工作流 checkpoint / resume。

checkpoint 在**阶段边界**创建，记录恢复执行所需的最小状态：已完成阶段结果、共享
blackboard、消息历史、指标计数，以及任务/拓扑指纹。恢复时重新物化 ``state://`` 与
``artifact://`` 引用，而不是持久化已经失效的进程内句柄。

文件存储采用同目录临时文件 + ``fsync`` + ``os.replace`` + 目录 ``fsync`` 的 durable
commit，并用 SHA-256 envelope 检测静默截断/意外损坏。该摘要不是认证 MAC，不能抵御
能重写文件并重新计算摘要的攻击者。checkpoint 可能包含任务内容，部署时
应把目录视为敏感运行数据并限制权限。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
import time
import uuid
from typing import Any

from agent_runtime.runtime.task import Task

CHECKPOINT_VERSION = "1"
CHECKPOINT_STORAGE_VERSION = 1
_CHECKPOINT_ID_RE = re.compile(r"^ckpt_[0-9a-f]{16}$")


class CheckpointCorruptionError(ValueError):
    """持久化 checkpoint 无法通过格式或内容完整性校验。"""


def task_fingerprint(task: Task) -> str:
    """对可持久化任务定义计算稳定指纹；verifier 函数本身不参与序列化。"""
    payload = {
        "task_id": task.task_id,
        "topic": task.topic,
        "description": task.description,
        "payload": task.payload,
        "expected": task.expected,
        "parent_task_id": task.parent_task_id,
        "context_id": task.context_id,
        "deadline_at": task.deadline_at,
        "tags": task.tags,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class WorkflowCheckpoint:
    """一次阶段边界快照。"""

    checkpoint_id: str
    version: str
    task_id: str
    task_fingerprint: str
    trace_id: str
    next_stage_index: int
    pipeline_signature: dict[str, str]
    runtime_signature: dict[str, Any]
    stage_results: list[dict[str, Any]]
    blackboard: dict[str, Any]
    metrics_state: dict[str, Any]
    messages: list[dict[str, Any]]
    created_at: float

    @classmethod
    def create(
        cls,
        *,
        task: Task,
        trace_id: str,
        next_stage_index: int,
        pipeline_signature: dict[str, str],
        runtime_signature: dict[str, Any],
        stage_results: list[dict[str, Any]],
        blackboard: dict[str, Any],
        metrics_state: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> "WorkflowCheckpoint":
        return cls(
            checkpoint_id=f"ckpt_{uuid.uuid4().hex[:16]}",
            version=CHECKPOINT_VERSION,
            task_id=task.task_id,
            task_fingerprint=task_fingerprint(task),
            trace_id=trace_id,
            next_stage_index=next_stage_index,
            pipeline_signature=dict(pipeline_signature),
            runtime_signature=_json_safe(runtime_signature),
            stage_results=list(stage_results),
            blackboard=_json_safe(blackboard),
            metrics_state=_json_safe(metrics_state),
            messages=_json_safe(messages),
            created_at=time.time(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowCheckpoint":
        return cls(**data)


class FileCheckpointStore:
    """单机 durable 文件 checkpoint 存储。

    ``save`` 的成功返回意味着 checkpoint 内容已经 flush 到文件，并尽力将 rename 后的
    目录项同步到磁盘。``load`` 会验证 SHA-256 envelope；旧版直接写 checkpoint JSON 的
    文件仍可读取，便于版本升级。
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Checkpoints may contain task payloads, blackboard state and message history.
        # Restrict directory traversal/listing as well as individual checkpoint files.
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def _path_for(self, checkpoint_id: str) -> Path:
        if not _CHECKPOINT_ID_RE.fullmatch(checkpoint_id):
            raise ValueError("invalid checkpoint id")
        return self.root / f"{checkpoint_id}.json"

    @staticmethod
    def _canonical_payload(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def _wrap(cls, checkpoint: WorkflowCheckpoint) -> dict[str, Any]:
        payload = checkpoint.to_dict()
        digest = hashlib.sha256(cls._canonical_payload(payload)).hexdigest()
        return {
            "format": "agent-runtime-checkpoint",
            "storage_version": CHECKPOINT_STORAGE_VERSION,
            "payload_sha256": digest,
            "checkpoint": payload,
        }

    @classmethod
    def _unwrap(cls, document: Any) -> WorkflowCheckpoint:
        if not isinstance(document, dict):
            raise CheckpointCorruptionError("checkpoint document must be a JSON object")

        # v0.2.0 and older: bare WorkflowCheckpoint JSON without storage envelope.
        if "checkpoint" not in document:
            try:
                return WorkflowCheckpoint.from_dict(document)
            except (TypeError, KeyError) as exc:
                raise CheckpointCorruptionError("invalid legacy checkpoint payload") from exc

        if document.get("format") != "agent-runtime-checkpoint":
            raise CheckpointCorruptionError("unknown checkpoint storage format")
        if document.get("storage_version") != CHECKPOINT_STORAGE_VERSION:
            raise CheckpointCorruptionError(
                f"unsupported checkpoint storage version: {document.get('storage_version')}"
            )
        payload = document.get("checkpoint")
        if not isinstance(payload, dict):
            raise CheckpointCorruptionError("checkpoint envelope payload is not an object")
        declared = document.get("payload_sha256")
        actual = hashlib.sha256(cls._canonical_payload(payload)).hexdigest()
        if not isinstance(declared, str) or not hmac.compare_digest(declared, actual):
            raise CheckpointCorruptionError("checkpoint integrity check failed")
        try:
            return WorkflowCheckpoint.from_dict(payload)
        except (TypeError, KeyError) as exc:
            raise CheckpointCorruptionError("invalid checkpoint payload") from exc

    def save(self, checkpoint: WorkflowCheckpoint) -> Path:
        final = self._path_for(checkpoint.checkpoint_id)
        tmp = self.root / f".{checkpoint.checkpoint_id}.{uuid.uuid4().hex}.tmp"
        document = self._wrap(checkpoint)
        raw = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        try:
            # Exclusive create prevents accidental collision with a stale temp file.
            with tmp.open("xb") as handle:
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, final)
            self._fsync_root()
        finally:
            # os.replace removes tmp on success; clean up only if a failure happened earlier.
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        return final

    def _fsync_root(self) -> None:
        """尽力持久化 rename 后的目录项；不支持目录 fsync 的平台安全降级。"""
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            fd = os.open(self.root, flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _load_path(self, path: Path) -> WorkflowCheckpoint:
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(path, flags)
            try:
                mode = os.fstat(fd).st_mode
                if not stat.S_ISREG(mode):
                    raise CheckpointCorruptionError(
                        f"checkpoint path is not a regular file: {path.name}"
                    )
                with os.fdopen(fd, "rb", closefd=False) as handle:
                    raw = handle.read()
            finally:
                os.close(fd)
            document = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CheckpointCorruptionError(f"cannot decode checkpoint: {path.name}") from exc
        checkpoint = self._unwrap(document)
        if path.stem != checkpoint.checkpoint_id:
            raise CheckpointCorruptionError(
                "checkpoint filename/id mismatch: "
                f"{path.stem!r} != {checkpoint.checkpoint_id!r}"
            )
        return checkpoint

    def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        return self._load_path(self._path_for(checkpoint_id))

    def latest(self, task_id: str) -> WorkflowCheckpoint | None:
        candidates: list[WorkflowCheckpoint] = []
        for path in self.root.glob("ckpt_*.json"):
            try:
                checkpoint = self._load_path(path)
            except (OSError, ValueError, TypeError, CheckpointCorruptionError):
                continue
            if checkpoint.task_id == task_id:
                candidates.append(checkpoint)
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.created_at)


def validate_checkpoint(
    checkpoint: WorkflowCheckpoint,
    *,
    task: Task,
    pipeline_signature: dict[str, str],
    runtime_signature: dict[str, Any],
) -> None:
    """拒绝任务或 Agent 拓扑不一致的恢复请求。"""
    if checkpoint.version != CHECKPOINT_VERSION:
        raise ValueError(f"unsupported checkpoint version: {checkpoint.version}")
    if checkpoint.task_id != task.task_id or checkpoint.task_fingerprint != task_fingerprint(task):
        raise ValueError("checkpoint task does not match current task")
    if checkpoint.pipeline_signature != pipeline_signature:
        raise ValueError("checkpoint pipeline/agent identities do not match current runtime")
    if checkpoint.runtime_signature != _json_safe(runtime_signature):
        raise ValueError("checkpoint runtime configuration does not match current runtime")


def _json_safe(value: Any) -> Any:
    """保留 JSON 原生结构；未知对象降为字符串，避免 checkpoint 写入失败。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


__all__ = [
    "WorkflowCheckpoint",
    "FileCheckpointStore",
    "CheckpointCorruptionError",
    "task_fingerprint",
    "validate_checkpoint",
    "CHECKPOINT_VERSION",
    "CHECKPOINT_STORAGE_VERSION",
]
