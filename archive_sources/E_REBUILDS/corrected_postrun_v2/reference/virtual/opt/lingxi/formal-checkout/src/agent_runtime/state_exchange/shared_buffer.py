"""向量传输缓冲区。

支持通过 :mod:`multiprocessing.shared_memory` 进行大向量共享；共享对象按不可变数据
处理，接收方可通过 :meth:`get_view` 获得**只读零拷贝视图**。此外可导出一个只含
共享内存名称 / shape / dtype 的小型描述符，让另一个 Python 进程 attach 同一 payload，
避免通过进程间管道再次 pickle/复制整个 NumPy 数组。

不支持共享内存时自动降级为进程内数组。务必调用 :meth:`close` 释放共享内存句柄。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import sys
from typing import Iterator

import threading

import numpy as np


@dataclass(frozen=True)
class SharedArrayDescriptor:
    """可序列化的共享数组控制面描述符（不包含数组 payload）。"""

    name: str
    shape: tuple[int, ...]
    dtype: str
    nbytes: int
    # fable9: byte offset inside the named segment (non-zero for arena-allocated objects).
    offset: int = 0


_ATTACH_LOCK = threading.Lock()


class SharedVectorBuffer:
    """向量缓冲区，可选共享内存后端。

    ``get`` 保留旧接口（返回拷贝）；``get_view`` 用于运行时的低开销消费，返回只读
    NumPy 视图。调用方不得在 buffer ``close`` 后继续持有该视图。
    """

    # fable9 Plasma-style arena: small immutable objects are bump-allocated inside one shared
    # segment instead of one POSIX segment each (shm_open+ftruncate+mmap and later
    # munmap+unlink per object, ~50 us measured).  Objects larger than a quarter chunk keep a
    # dedicated segment.  A chunk is released when its last object is deleted.
    ARENA_CHUNK = 256 * 1024
    ARENA_ALIGN = 64

    def __init__(self, use_shared_memory: bool = False, *, arena: bool | None = None) -> None:
        import os as _os

        self.use_shared_memory = use_shared_memory
        self._inproc: dict[str, np.ndarray] = {}
        self._shm: dict[str, tuple] = {}  # key -> (SharedMemory, shape, dtype)
        self._degraded = False
        if arena is None:
            arena = _os.environ.get("AGENT_SHM_ARENA", "1").strip().lower() not in {"0", "false", "no", "off"}
        self.arena = bool(arena)
        self._chunks: list[list] = []  # [SharedMemory, used_bytes, live_objects]
        self._arena_objs: dict[str, tuple[int, int, tuple, np.dtype]] = {}
        self.segments_created = 0

    def _arena_alloc(self, nbytes: int) -> tuple[int, int]:
        from multiprocessing import shared_memory

        need = max(nbytes, 1)
        if self._chunks:
            chunk = self._chunks[-1]
            off = (chunk[1] + self.ARENA_ALIGN - 1) // self.ARENA_ALIGN * self.ARENA_ALIGN
            if chunk[0] is not None and off + need <= chunk[0].size:
                chunk[1] = off + need
                chunk[2] += 1
                return len(self._chunks) - 1, off
        shm = shared_memory.SharedMemory(create=True, size=self.ARENA_CHUNK)
        self.segments_created += 1
        self._chunks.append([shm, need, 1])
        return len(self._chunks) - 1, 0

    @property
    def degraded(self) -> bool:
        """是否发生过从共享内存到进程内的降级。"""
        return self._degraded

    def put(self, key: str, array: np.ndarray) -> int:
        """存入向量，返回实际物化字节数。

        上层 ``StateExchange`` 负责内容寻址去重；这里若 key 已存在则显式拒绝覆盖，
        避免共享内存句柄被覆盖后泄漏。
        """
        if self.exists(key):
            raise KeyError(f"buffer key already exists: {key}")
        array = np.ascontiguousarray(array)
        if self.use_shared_memory and self.arena and array.nbytes <= self.ARENA_CHUNK // 4 and not array.dtype.hasobject:
            try:
                idx, off = self._arena_alloc(array.nbytes)
                buf = np.ndarray(array.shape, dtype=array.dtype, buffer=self._chunks[idx][0].buf, offset=off)
                buf[...] = array[...]
                self._arena_objs[key] = (idx, off, array.shape, array.dtype)
                return int(array.nbytes)
            except Exception:  # noqa: BLE001 - fall back to a dedicated segment / in-process
                pass
        if self.use_shared_memory:
            shm = None
            try:
                from multiprocessing import shared_memory

                shm = shared_memory.SharedMemory(create=True, size=max(array.nbytes, 1))
                self.segments_created += 1
                buf = np.ndarray(array.shape, dtype=array.dtype, buffer=shm.buf)
                buf[...] = array[...]
                self._shm[key] = (shm, array.shape, array.dtype)
                return int(array.nbytes)
            except Exception:  # noqa: BLE001 - 共享内存不可用则降级
                # Allocation may succeed before a later buffer/copy step fails.  Release
                # such partially-created segments before falling back to in-process data.
                if shm is not None:
                    self._release_shared_memory(shm)
                self._degraded = True
        self._inproc[key] = array.copy()
        return int(array.nbytes)

    def get_view(self, key: str) -> np.ndarray:
        """返回只读、零额外数据拷贝的数组视图。

        - shared-memory 后端：view 直接指向共享内存；
        - in-process 后端：view 指向内部 NumPy 数组；
        - 两种情况下均设为 read-only，保持内容寻址对象不可变语义。

        ``writeable=False`` 是运行时 API 级保护，不等同于操作系统级访问控制。
        """
        if key in self._arena_objs:
            idx, off, shape, dtype = self._arena_objs[key]
            view = np.ndarray(shape, dtype=dtype, buffer=self._chunks[idx][0].buf, offset=off)
        elif key in self._shm:
            shm, shape, dtype = self._shm[key]
            view = np.ndarray(shape, dtype=dtype, buffer=shm.buf)
        elif key in self._inproc:
            view = self._inproc[key].view()
        else:
            raise KeyError(key)
        view.setflags(write=False)
        return view

    def get(self, key: str) -> np.ndarray:
        """兼容接口：取出独立拷贝。需要低开销只读消费时使用 ``get_view``。"""
        return np.array(self.get_view(key), copy=True)

    def descriptor(self, key: str) -> SharedArrayDescriptor | None:
        """导出跨进程 attach 所需的最小描述符；inproc 后端返回 ``None``。"""
        if key in self._arena_objs:
            idx, off, shape, dtype = self._arena_objs[key]
            return SharedArrayDescriptor(
                name=self._chunks[idx][0].name,
                shape=tuple(int(x) for x in shape),
                dtype=str(np.dtype(dtype)),
                nbytes=int(np.prod(shape, dtype=np.int64) * np.dtype(dtype).itemsize),
                offset=int(off),
            )
        item = self._shm.get(key)
        if item is None:
            return None
        shm, shape, dtype = item
        return SharedArrayDescriptor(
            name=shm.name,
            shape=tuple(int(x) for x in shape),
            dtype=str(np.dtype(dtype)),
            nbytes=int(np.prod(shape, dtype=np.int64) * np.dtype(dtype).itemsize),
        )

    @staticmethod
    @contextmanager
    def attach_readonly(descriptor: SharedArrayDescriptor) -> Iterator[np.ndarray]:
        """在独立进程/对象中 attach 已存在共享数组，并暴露只读 NumPy view。

        生命周期由 owner 管理：consumer 只 ``close`` 本地句柄，绝不 ``unlink``。
        Python 3.13+ 对独立 ``subprocess`` attach 必须 ``track=False``，否则 consumer
        自己的 resource tracker 会在其退出时提前删除 owner 仍在使用的共享段。旧版
        Python 没有公开 ``track`` 参数，因此仅在该兼容分支取消 consumer 注册。
        """
        from multiprocessing import shared_memory

        if any(int(dim) < 0 for dim in descriptor.shape) or int(descriptor.nbytes) < 0:
            raise ValueError("shared descriptor shape/nbytes must be non-negative")
        dtype = np.dtype(descriptor.dtype)
        if dtype.hasobject:
            raise ValueError("shared descriptor object dtype is not supported")
        elements = 1
        for dim in descriptor.shape:
            elements *= int(dim)
            if elements > sys.maxsize:
                raise ValueError("shared descriptor size overflow")
        expected_nbytes = elements * int(dtype.itemsize)
        if expected_nbytes > sys.maxsize:
            raise ValueError("shared descriptor size overflow")
        if expected_nbytes != int(descriptor.nbytes):
            raise ValueError(
                f"shared descriptor size mismatch: expected={expected_nbytes}, "
                f"declared={descriptor.nbytes}"
            )

        try:
            # Added in Python 3.13.  The owner process remains the sole lifecycle tracker.
            shm = shared_memory.SharedMemory(
                name=descriptor.name,
                create=False,
                track=False,
            )
        except TypeError:  # pragma: no cover - exercised on Python 3.10-3.12
            # CPython <=3.12 registers every attach with the resource tracker; a consumer that
            # later unregisters also removes the owner's registration, producing
            # ``KeyError: '/psm_*'`` noise and risking premature unlink.  Attach without
            # registering at all: the owner remains the sole lifecycle tracker.
            from multiprocessing import resource_tracker

            with _ATTACH_LOCK:
                original = resource_tracker.register

                def _skip_shm(name, rtype):  # noqa: ANN001
                    if rtype != "shared_memory":
                        original(name, rtype)

                resource_tracker.register = _skip_shm
                try:
                    shm = shared_memory.SharedMemory(name=descriptor.name, create=False)
                finally:
                    resource_tracker.register = original
        try:
            offset = int(getattr(descriptor, "offset", 0))
            if offset < 0 or offset % max(1, dtype.alignment) != 0:
                raise ValueError("shared descriptor offset invalid")
            if shm.size < offset + max(expected_nbytes, 1):
                raise ValueError(
                    f"shared segment too small: actual={shm.size}, expected={offset + max(expected_nbytes, 1)}"
                )
            view = np.ndarray(
                descriptor.shape,
                dtype=dtype,
                buffer=shm.buf,
                offset=offset,
            )
            view.setflags(write=False)
            yield view
        finally:
            shm.close()

    @staticmethod
    def _release_shared_memory(shm) -> None:
        """Best-effort close + unlink without letting one failure skip the other."""
        try:
            shm.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            shm.unlink()
        except Exception:  # noqa: BLE001
            pass

    def delete(self, key: str) -> None:
        """删除单个对象并释放其共享内存；不存在时静默返回。"""
        if key in self._arena_objs:
            idx, _off, _shape, _dtype = self._arena_objs.pop(key)
            chunk = self._chunks[idx]
            chunk[2] -= 1
            if chunk[2] <= 0 and idx != len(self._chunks) - 1 and chunk[0] is not None:
                self._release_shared_memory(chunk[0])
                chunk[0] = None
            return
        if key in self._shm:
            shm, _, _ = self._shm.pop(key)
            self._release_shared_memory(shm)
        self._inproc.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self._arena_objs or key in self._shm or key in self._inproc

    def close(self) -> None:
        """释放所有共享内存句柄。"""
        for shm, _, _ in self._shm.values():
            self._release_shared_memory(shm)
        self._shm.clear()
        self._inproc.clear()
        for chunk in self._chunks:
            if chunk[0] is not None:
                self._release_shared_memory(chunk[0])
                chunk[0] = None
        self._chunks.clear()
        self._arena_objs.clear()


__all__ = ["SharedVectorBuffer", "SharedArrayDescriptor"]
