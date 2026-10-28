"""指标采集。

集中采集赛题要求的 16 项指标及派生指标。一个 `MetricsCollector` 对应一次
任务运行（或一次实验重复），可序列化为 dict 落盘。
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

try:  # psutil 为核心依赖，但保留降级以增强健壮性
    import psutil

    _PROC = psutil.Process()
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore[assignment]
    _PROC = None


@dataclass
class MetricsSnapshot:
    """单次运行的指标快照。"""

    # 1. Agent 消息次数
    message_count: int = 0
    # 2. 文本 token / 字符
    text_tokens: int = 0
    text_chars: int = 0
    # 实际上线文本的 UTF-8 序列化字节数，与 tokenizer 无关。
    wire_bytes: int = 0
    # Structured Protocol 1.3 wire details.  Tokens/chars are counted on the reversible JSON
    # semantic frame; binary_frames indicates how many messages physically used MessagePack.
    binary_frames: int = 0
    session_frames: int = 0
    # Protocol 1.4 (fable7) CPHC: routine negotiation cost is charged in full (tokens are also
    # added to text_tokens, bytes to wire/ipc bytes); prediction/ref-table hits are diagnostic.
    routine_uploads: int = 0
    routine_upload_tokens: int = 0
    routine_upload_bytes: int = 0
    routine_references: int = 0
    cphc_predicted_fields: int = 0
    cphc_explicit_fields: int = 0
    cphc_reference_hits: int = 0
    # fable7 pipelined verification (ACK window + commit barrier).
    ipc_pipelined_frames: int = 0
    ipc_barrier_wait_sec: float = 0.0
    # fable8 cumulative-ACK barrier requests (4-byte empty frames, charged to ipc_framed_bytes).
    ipc_barrier_requests: int = 0
    # fable8 agentic plan caching (planner LLM decomposition skipped on verified signature hit).
    plan_cache_hits: int = 0
    # fable10 process-per-agent runtime.
    agent_process_crashes: int = 0
    plan_cache_misses: int = 0
    # Real kernel control-plane IPC (when Channel uses the optional UDS router process).
    ipc_frames: int = 0
    ipc_payload_bytes: int = 0
    ipc_framed_bytes: int = 0
    ipc_ack_bytes: int = 0
    ipc_roundtrip_sec: float = 0.0
    ipc_socket_calls: int = 0
    ipc_verified: int = 0
    # 3. 非文本状态次数与字节
    state_transfers: int = 0
    # 实际物化/搬运的非文本状态字节（应用精度压缩与内容寻址去重后）。
    state_bytes: int = 0
    # 若每次传递都按原始 float32 payload 物化时的逻辑字节，用作低开销基线。
    state_logical_bytes: int = 0
    # 命中内容寻址去重、只复用 state:// 引用的次数。
    state_dedup_hits: int = 0
    state_float32_transfers: int = 0
    state_float16_transfers: int = 0
    state_int8_transfers: int = 0
    # fable9 decision-certified nibble quantization.
    state_int4_transfers: int = 0
    state_q4block_transfers: int = 0
    state_decision_certificates: int = 0
    # Numeric state data-plane transport actually used for each logical handoff.
    state_shm_transfers: int = 0
    state_inproc_transfers: int = 0
    # Main-pipeline sealed memfd + SCM_RIGHTS attachments. State bytes remain counted
    # in state_bytes; socket bytes only account for the attachment header/ack.
    state_fd_handoffs: int = 0
    state_fd_bytes: int = 0
    state_fd_socket_bytes: int = 0
    state_fd_failures: int = 0
    # Versioned control-state is part of the real main pipeline in v0.6.1. Keep a
    # dedicated breakdown while also including these bytes in the generic state totals.
    versioned_state_transfers: int = 0
    versioned_state_bytes: int = 0
    versioned_state_logical_bytes: int = 0
    versioned_state_delta_updates: int = 0
    versioned_state_snapshots: int = 0
    # 4. Artifact 引用次数
    artifact_refs: int = 0
    # Physical artifact-plane bytes, counted once for newly materialized content-addressed objects.
    artifact_bytes: int = 0
    # 自适应 control/data-plane placement 可观测指标。
    placement_decisions: int = 0
    placement_reference: int = 0
    placement_inline: int = 0
    placement_estimated_savings_bytes: int = 0
    # 5. 端到端耗时（秒）
    latency_sec: float = 0.0
    # 7. LLM 调用次数 / token
    llm_calls: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_cached_prompt_tokens: int = 0
    # sota1: logical agent LLM requests vs provider requests (coalescing), reasoning tokens
    # reported inside completion tokens, and completion-cap violations seen by the governor.
    llm_logical_requests: int = 0
    # Prompt tokens the runtime itself sent (local tokenizer), as opposed to provider-billed.
    llm_visible_prompt_tokens: int = 0
    llm_coalesced_batches: int = 0
    llm_coalesced_items: int = 0
    llm_coalesce_parse_misses: int = 0
    llm_coalesce_repairs: int = 0
    llm_piggybacked_calls: int = 0
    llm_reasoning_tokens: int = 0
    llm_cap_violations: int = 0
    # Only set by the mock-gateway projection provider (calibrated latency law); 0 otherwise.
    llm_projected_latency_sec: float = 0.0
    # Optional semantic judge: deliberately disabled unless AGENT_LLM_JUDGE=1.
    llm_judgements: int = 0
    llm_judgements_correct: int = 0
    # 8. 工具调用与重复调用
    tool_calls: int = 0
    repeated_tool_calls: int = 0
    # 9/10. 记忆命中：query-level 与 candidate/use-level 分开计量。
    memory_queries: int = 0
    memory_query_hits: int = 0
    memory_retrieved: int = 0
    # 检索到但不满足 operation/defect/signature 兼容条件的候选。
    memory_incompatible: int = 0
    # 其中因兼容门控而真正阻止使用的候选。
    memory_rejected: int = 0
    memory_used: int = 0
    memory_effective: int = 0
    memory_harmful: int = 0
    memory_conflicts: int = 0
    # Adopted memories whose downstream task failed for a reason that cannot be
    # causally attributed to the memory. These do not update the reliability posterior.
    memory_unattributed: int = 0
    # Exact-input cache layer, deliberately separated from semantic retrieval hit-rate metrics.
    memory_fast_path_probes: int = 0
    memory_fast_path_hits: int = 0
    memory_fast_path_tasks: int = 0
    memory_fast_path_skipped_stages: int = 0
    # 11/12. 成功率 / 质量
    task_success: bool = False
    task_quality: float = 0.0
    # 13/14. 资源
    cpu_percent: float = 0.0
    rss_peak_mb: float = 0.0
    # 15+. 已完成请求的幂等回放次数（避免重复 Agent/工具副作用）。
    idempotency_replays: int = 0
    # 可靠性附加指标：真正发生的 retry / 因共享预算拒绝 / circuit 拒绝。
    retry_attempts: int = 0
    retry_budget_exhausted: int = 0
    circuit_rejections: int = 0
    # 16. 错误 / 降级
    error_count: int = 0
    degradation_count: int = 0

    # 附加：每步延迟样本（用于 P50/P95）
    step_latencies: list[float] = field(default_factory=list)
    # 工具调用签名计数（用于识别重复计算）
    _tool_signatures: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_tool_signatures", None)
        d["derived"] = {
            "memory_hit_rate": self._safe_div(self.memory_query_hits, self.memory_queries),
            "memory_fast_path_hit_rate": self._safe_div(
                self.memory_fast_path_hits, self.memory_fast_path_probes
            ),
            "effective_hit_rate": self._safe_div(self.memory_effective, self.memory_used),
            "total_llm_tokens": self.llm_prompt_tokens + self.llm_completion_tokens,
            "state_materialization_saving": (
                1.0 - self._safe_div(self.state_bytes, self.state_logical_bytes)
                if self.state_logical_bytes
                else 0.0
            ),
        }
        return d

    @staticmethod
    def _safe_div(a: float, b: float) -> float:
        return float(a) / float(b) if b else 0.0


class MetricsCollector:
    """指标采集器：贯穿一次任务运行，由各模块写入。"""

    def __init__(self) -> None:
        self.snapshot = MetricsSnapshot()
        self._start: float | None = None
        # checkpoint/resume 时累积之前已执行阶段的端到端耗时。
        self._elapsed_before_start: float = 0.0

    # ----- 计时 -----
    def start(self) -> None:
        self._start = time.perf_counter()
        self._sample_resources(force=True)

    def stop(self) -> None:
        if self._start is not None:
            self.snapshot.latency_sec = (
                self._elapsed_before_start + time.perf_counter() - self._start
            )
            self._start = None
        self._sample_resources(force=True)

    def export_state(self) -> dict[str, Any]:
        """导出可 JSON 序列化的指标状态，用于 workflow checkpoint。"""
        state = asdict(self.snapshot)
        elapsed = float(self.snapshot.latency_sec)
        if self._start is not None:
            elapsed = self._elapsed_before_start + time.perf_counter() - self._start
        state["latency_sec"] = elapsed
        return state

    def restore_state(self, state: dict[str, Any]) -> None:
        """从 checkpoint 恢复计数，并让后续 ``stop`` 继续累计时延。"""
        fields = MetricsSnapshot.__dataclass_fields__
        payload = {name: state[name] for name in fields if name in state}
        self.snapshot = MetricsSnapshot(**payload)
        self._elapsed_before_start = float(self.snapshot.latency_sec)
        self._start = None

    def record_step_latency(self, seconds: float) -> None:
        self.snapshot.step_latencies.append(seconds)

    # ----- 通信 -----
    def record_message(self, *, tokens: int = 0, chars: int = 0, wire_bytes: int = 0) -> None:
        self.snapshot.message_count += 1
        self.snapshot.text_tokens += tokens
        self.snapshot.text_chars += chars
        self.snapshot.wire_bytes += wire_bytes

    def record_routine_negotiation(self, *, uploaded: bool, tokens: int, wire_bytes: int, framed_bytes: int = 0) -> None:
        """Charge a CPHC routine upload to the communication totals (never to message_count)."""
        if uploaded:
            self.snapshot.routine_uploads += 1
            self.snapshot.routine_upload_tokens += int(tokens)
            self.snapshot.routine_upload_bytes += int(wire_bytes)
            self.snapshot.text_tokens += int(tokens)
            self.snapshot.text_chars += int(wire_bytes)
            self.snapshot.wire_bytes += int(wire_bytes)
            self.snapshot.ipc_framed_bytes += int(framed_bytes)
        else:
            self.snapshot.routine_references += 1

    def record_ack_batch(self, *, verified: int, ack_bytes: int, wait_sec: float, socket_calls: int) -> None:
        """Pipelined verification: ACKs collected at the commit barrier."""
        self.snapshot.ipc_verified += int(verified)
        self.snapshot.ipc_ack_bytes += int(ack_bytes)
        self.snapshot.ipc_roundtrip_sec += float(wait_sec)
        self.snapshot.ipc_socket_calls += int(socket_calls)
        self.snapshot.ipc_barrier_wait_sec += float(wait_sec)
        self.snapshot.ipc_pipelined_frames += int(verified)

    def record_cphc(self, *, predicted: int, explicit: int, reference_hits: int) -> None:
        self.snapshot.cphc_predicted_fields += int(predicted)
        self.snapshot.cphc_explicit_fields += int(explicit)
        self.snapshot.cphc_reference_hits += int(reference_hits)

    def record_wire_codec(self, *, binary: bool, session: bool) -> None:
        if binary:
            self.snapshot.binary_frames += 1
        if session:
            self.snapshot.session_frames += 1

    def record_ipc(
        self,
        *,
        payload_bytes: int,
        framed_bytes: int,
        ack_bytes: int,
        roundtrip_sec: float,
        socket_calls: int = 0,
        verified: int = 0,
    ) -> None:
        self.snapshot.ipc_frames += 1
        self.snapshot.ipc_payload_bytes += int(payload_bytes)
        self.snapshot.ipc_framed_bytes += int(framed_bytes)
        self.snapshot.ipc_ack_bytes += int(ack_bytes)
        self.snapshot.ipc_roundtrip_sec += float(roundtrip_sec)
        self.snapshot.ipc_socket_calls += int(socket_calls)
        self.snapshot.ipc_verified += int(verified)

    def record_state_transfer(
        self,
        n_bytes: int,
        *,
        logical_bytes: int | None = None,
        deduplicated: bool = False,
        precision: str | None = None,
        transport: str | None = None,
    ) -> None:
        """记录一次状态引用传递及实际物化成本。

        ``n_bytes`` 是本次真正新增的状态 payload；内容寻址复用时可为 0。
        ``logical_bytes`` 是不采用压缩/去重时对应的 float32 payload 大小。
        """
        self.snapshot.state_transfers += 1
        self.snapshot.state_bytes += int(n_bytes)
        self.snapshot.state_logical_bytes += int(
            n_bytes if logical_bytes is None else logical_bytes
        )
        if deduplicated:
            self.snapshot.state_dedup_hits += 1
        if precision == "float32":
            self.snapshot.state_float32_transfers += 1
        elif precision == "float16":
            self.snapshot.state_float16_transfers += 1
        elif precision == "int8":
            self.snapshot.state_int8_transfers += 1
        elif precision == "int4":
            self.snapshot.state_int4_transfers += 1
        elif precision and precision.startswith("q4b"):
            # block-scaled 4-bit (fable14) counts in the 4-bit family and separately.
            self.snapshot.state_int4_transfers += 1
            self.snapshot.state_q4block_transfers += 1
        if transport == "shm":
            self.snapshot.state_shm_transfers += 1
        elif transport == "inproc":
            self.snapshot.state_inproc_transfers += 1

    def record_state_fd_handoff(self, *, state_bytes: int, socket_bytes: int, ok: bool) -> None:
        self.snapshot.state_fd_handoffs += 1
        self.snapshot.state_fd_bytes += int(state_bytes)
        self.snapshot.state_fd_socket_bytes += int(socket_bytes)
        if not ok:
            self.snapshot.state_fd_failures += 1

    def record_versioned_state_transfer(
        self,
        n_bytes: int,
        *,
        logical_bytes: int,
        kind: str,
        deduplicated: bool = False,
    ) -> None:
        """Record one versioned control-state handoff/materialization.

        Versioned state is not a side benchmark anymore: it participates in the normal
        Orchestrator pipeline. Therefore its payload contributes to the generic state totals,
        while the dedicated counters keep the contribution auditable.
        """

        self.record_state_transfer(
            n_bytes,
            logical_bytes=logical_bytes,
            deduplicated=deduplicated,
        )
        self.snapshot.versioned_state_transfers += 1
        self.snapshot.versioned_state_bytes += int(n_bytes)
        self.snapshot.versioned_state_logical_bytes += int(logical_bytes)
        if kind == "delta":
            self.snapshot.versioned_state_delta_updates += 1
        elif kind == "snapshot":
            self.snapshot.versioned_state_snapshots += 1

    def record_artifact_ref(self, n: int = 1) -> None:
        self.snapshot.artifact_refs += n

    def record_placement(self, *, use_reference: bool, saving_bytes: int = 0) -> None:
        self.snapshot.placement_decisions += 1
        if use_reference:
            self.snapshot.placement_reference += 1
            self.snapshot.placement_estimated_savings_bytes += max(0, int(saving_bytes))
        else:
            self.snapshot.placement_inline += 1

    # ----- 模型 / 工具 -----
    def record_llm(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int = 0,
        *,
        logical_requests: int = 1,
        reasoning_tokens: int = 0,
        cap_violation: bool = False,
        visible_prompt_tokens: int | None = None,
        projected_latency_sec: float = 0.0,
        repair_items: int = 0,
    ) -> None:
        """Record one *provider* request that served ``logical_requests`` agent requests."""
        self.snapshot.llm_calls += 1
        self.snapshot.llm_prompt_tokens += prompt_tokens
        self.snapshot.llm_completion_tokens += completion_tokens
        self.snapshot.llm_cached_prompt_tokens += int(cached_prompt_tokens)
        self.snapshot.llm_logical_requests += int(logical_requests)
        self.snapshot.llm_visible_prompt_tokens += int(prompt_tokens if visible_prompt_tokens is None else visible_prompt_tokens)
        self.snapshot.llm_reasoning_tokens += int(reasoning_tokens)
        self.snapshot.llm_cap_violations += int(bool(cap_violation))
        self.snapshot.llm_projected_latency_sec += float(projected_latency_sec or 0.0)
        self.snapshot.llm_coalesce_repairs += int(repair_items)
        if logical_requests + repair_items > 1:
            self.snapshot.llm_coalesced_batches += 1
            self.snapshot.llm_coalesced_items += int(logical_requests)

    def record_llm_parse_misses(self, n: int) -> None:
        self.snapshot.llm_coalesce_parse_misses += int(n)

    def record_llm_judgement(self, *, correct: bool) -> None:
        self.snapshot.llm_judgements += 1
        self.snapshot.llm_judgements_correct += int(bool(correct))

    def record_tool_call(self, signature: str) -> None:
        self.snapshot.tool_calls += 1
        prev = self.snapshot._tool_signatures.get(signature, 0)
        if prev > 0:
            self.snapshot.repeated_tool_calls += 1
        self.snapshot._tool_signatures[signature] = prev + 1

    # ----- 记忆 -----
    def record_memory(
        self,
        *,
        queries: int = 0,
        query_hits: int = 0,
        retrieved: int = 0,
        incompatible: int = 0,
        rejected: int = 0,
        used: int = 0,
        effective: int = 0,
        harmful: int = 0,
        unattributed: int = 0,
    ) -> None:
        self.snapshot.memory_queries += queries
        self.snapshot.memory_query_hits += query_hits
        self.snapshot.memory_retrieved += retrieved
        self.snapshot.memory_incompatible += incompatible
        self.snapshot.memory_rejected += rejected
        self.snapshot.memory_used += used
        self.snapshot.memory_effective += effective
        self.snapshot.memory_harmful += harmful
        self.snapshot.memory_unattributed += unattributed

    def record_memory_conflict(self, n: int = 1) -> None:
        self.snapshot.memory_conflicts += int(n)

    def record_memory_fast_path(
        self,
        *,
        probes: int = 0,
        hits: int = 0,
        tasks: int = 0,
        skipped_stages: int = 0,
    ) -> None:
        self.snapshot.memory_fast_path_probes += int(probes)
        self.snapshot.memory_fast_path_hits += int(hits)
        self.snapshot.memory_fast_path_tasks += int(tasks)
        self.snapshot.memory_fast_path_skipped_stages += int(skipped_stages)

    # ----- 结果 -----
    def set_result(self, success: bool, quality: float) -> None:
        self.snapshot.task_success = success
        self.snapshot.task_quality = quality

    def record_error(self) -> None:
        self.snapshot.error_count += 1

    def record_idempotency_replay(self, n: int = 1) -> None:
        self.snapshot.idempotency_replays += int(n)

    def record_retry(self, n: int = 1) -> None:
        self.snapshot.retry_attempts += int(n)

    def record_retry_budget_exhausted(self, n: int = 1) -> None:
        self.snapshot.retry_budget_exhausted += int(n)

    def record_circuit_rejection(self, n: int = 1) -> None:
        self.snapshot.circuit_rejections += int(n)

    def record_degradation(self) -> None:
        self.snapshot.degradation_count += 1

    # ----- 资源 -----
    def _sample_resources(self, force: bool = False) -> None:
        if _PROC is None:
            return
        # fable7: each sample reads /proc twice (~0.1-0.15 ms).  Intermediate per-stage samples
        # were ~10 per task, i.e. >1 ms of pure observability overhead on a ~12 ms task.  Start
        # and stop are always sampled; intermediate samples are rate-limited (default 10 ms).
        now = time.perf_counter()
        if not force and now - getattr(self, "_last_sample", -1e9) < _SAMPLE_INTERVAL_SEC:
            return
        self._last_sample = now
        fast = _fast_sample()
        if fast is not None:
            rss_bytes, cpu_percent = fast
            self.snapshot.rss_peak_mb = max(self.snapshot.rss_peak_mb, rss_bytes / (1024 * 1024))
            self.snapshot.cpu_percent = cpu_percent
            return
        try:
            rss_mb = _PROC.memory_info().rss / (1024 * 1024)
            self.snapshot.rss_peak_mb = max(self.snapshot.rss_peak_mb, rss_mb)
            self.snapshot.cpu_percent = _PROC.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001
            pass

    def sample_resources(self) -> None:
        """对外暴露的资源采样（可在长任务中周期调用）。"""
        self._sample_resources()


# fable8: psutil opens/parses/closes /proc files on every call (~25 us for rss + cpu%).  The
# same numbers come from one pread() on a persistent /proc/self/statm descriptor (identical
# resident-set value: field 2 x page size) and os.times() deltas (process user+system CPU time
# over wall time, the definition psutil.Process.cpu_percent(interval=None) uses): ~1.5 us.
# Falls back to psutil when /proc is unavailable or after fork (pid change).
_STATM: dict[str, Any] = {"fd": None, "pid": None, "page": 0, "last": None}


def _fast_sample() -> tuple[int, float] | None:
    try:
        pid = os.getpid()
        if _STATM["pid"] != pid:
            if _STATM["fd"] is not None:
                try:
                    os.close(_STATM["fd"])
                except OSError:
                    pass
            _STATM.update(fd=os.open("/proc/self/statm", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)),
                          pid=pid, page=os.sysconf("SC_PAGE_SIZE"), last=None)
        rss = int(os.pread(_STATM["fd"], 128, 0).split()[1]) * int(_STATM["page"])
        t = os.times()
        cpu, wall = t.user + t.system, time.monotonic()
        last = _STATM["last"]
        _STATM["last"] = (cpu, wall)
        pct = 0.0 if last is None or wall <= last[1] else max(0.0, (cpu - last[0]) / (wall - last[1]) * 100.0)
        return rss, pct
    except (OSError, ValueError, IndexError):
        return None


_SAMPLE_INTERVAL_SEC = max(0.0, float(os.environ.get("AGENT_RESOURCE_SAMPLE_MS", "10") or 10) / 1000.0)

__all__ = ["MetricsCollector", "MetricsSnapshot"]
