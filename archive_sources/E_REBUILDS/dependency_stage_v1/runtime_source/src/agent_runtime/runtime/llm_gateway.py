"""sota1: runtime LLM gateway with *call coalescing* (vectored completion).

Why this exists (measured, not assumed)
---------------------------------------
The 2026-09-24 real-model rerun of fable7…fable16 (gpt-6-luna, 8 variants × 9 versions) shows
that the provider bills a fixed ~4.4K prompt tokens on **every** request, independent of what
the runtime sends (least-squares fit over all 72 cells: ``P = 4382·requests + 1.31·V``, see
``evaluation/billing_model.py``).  For Structured+State this fixed part is ~97% of the bill, so
nine versions of communication work (5,599 → 407 communication tokens) moved the FULL
total-token saving only from 56.2% to 55.0%.  By Amdahl's law the only remaining large lever is
the number of provider requests.

In this runtime most agent LLM calls are *not on the critical path*: planner decomposition,
retrieval summary, execution narration and outcome summary are advisory text whose content no
downstream stage reads (the deterministic pipeline decides; the judge call is the exception and
stays synchronous).  Such calls are declared ``deferrable`` and queued here.  At the task
commit barrier the gateway issues **one** vectored request that carries every queued item
(numbered, each with its own role instruction and inbound context), then splits the answer
back per item — the same idea as ``writev``/``io_uring`` batching a syscall's fixed cost.

Guarantees
----------
* Opt-in per run (``AGENT_LLM_COALESCE=task``; ``auto`` enables it only for real providers).
* A single queued item is sent unchanged (no framing overhead, identical to the direct path).
* Non-deferrable calls bypass the queue and are answered synchronously.
* Every logical request is still counted (``llm_logical_requests``); provider requests are
  counted in ``llm_calls`` as before, so reports can show both.
* Parse failures never fail a task: missing items are recorded as ``llm_coalesce_parse_misses``.

``session`` mode (group commit)
-------------------------------
Like a file system's or database's *group commit*, one gateway can be shared by the tasks of a
collaboration session (e.g. one round of continuous tasks).  Per-task barriers then do not
flush; the session owner calls :func:`flush_session` once, and every advisory request of the
round travels in one provider request (bounded by ``max_batch`` items per request).  Task
answers never depend on these texts, so correctness is unchanged; the provider tokens are
recorded into the session's metrics collector.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.providers.base import LLMProvider, LLMResponse

COALESCE_SYSTEM = (
    "You serve several agents of one multi-agent runtime in a single request. "
    "Answer every numbered item independently. Output exactly one line per item, "
    "formatted as '<number>: <answer>'. No other text."
)

_ITEM_RE = re.compile(r"^\s*\[?(\d{1,3})\]?\s*[:.)\-]\s*(.*)$")


@dataclass
class PendingLLMCall:
    ticket: int
    prompt: str
    system: str | None
    max_tokens: int | None
    temperature: float | None
    submitted_at: float = field(default_factory=time.perf_counter)
    # Declarative grading applied when the answer arrives (e.g. the semantic judge); the sink
    # is the submitting task's MetricsCollector (runtime-side object, never pickled).
    grade: dict[str, Any] | None = None
    sink: Any = None
    attempts: int = 0


@dataclass
class CoalesceStats:
    batches: int = 0
    items: int = 0
    direct_singletons: int = 0
    parse_misses: int = 0
    provider_requests_saved: int = 0
    graded: int = 0
    repaired: int = 0
    window_decreases: int = 0
    waves: int = 0
    wall_sec: float = 0.0
    projected_serial_sec: float = 0.0
    projected_wall_sec: float = 0.0


_WINDOWS: dict[str, int] = {}
_CLEAN: dict[str, int] = {}
_SLOW_START: dict[str, bool] = {}
_STORE_LOADED = False


def _store_path():
    import os as _os

    p = _os.environ.get("AGENT_LLM_WINDOW_STORE", "").strip()
    return p or None


def _load_window_store() -> None:
    """Opt-in persistence of learned per-model windows (like TCP's per-destination metrics
    cache): ``AGENT_LLM_WINDOW_STORE=/path/windows.json``."""
    global _STORE_LOADED
    if _STORE_LOADED:
        return
    _STORE_LOADED = True
    path = _store_path()
    if not path:
        return
    import json as _json

    try:
        data = _json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        return
    for key, entry in (data or {}).items():
        if isinstance(entry, dict) and isinstance(entry.get("window"), int):
            _WINDOWS.setdefault(key, max(1, entry["window"]))
            _SLOW_START.setdefault(key, bool(entry.get("slow_start", True)))


def _save_window_store() -> None:
    path = _store_path()
    if not path:
        return
    import json as _json
    import os as _os

    data = {k: {"window": v, "slow_start": _SLOW_START.get(k, True)} for k, v in _WINDOWS.items()}
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump(data, fh, indent=1, sort_keys=True)
        _os.replace(tmp, path)
    except OSError:
        pass


def _window_get(key: str, default: int) -> int:
    return _WINDOWS.get(key, default)


def _window_set(key: str, value: int) -> None:
    _WINDOWS[key] = int(value)
    _save_window_store()


def reset_batch_windows() -> None:
    """Forget learned per-model batch windows (tests / new provider sessions)."""
    global _STORE_LOADED
    _WINDOWS.clear()
    _CLEAN.clear()
    _SLOW_START.clear()
    _STORE_LOADED = False


def resolve_coalesce_mode(value: str | None, provider: str) -> str:
    """Map the user setting to ``off`` or ``task`` (``auto`` = on for real providers only)."""
    v = (value or "auto").strip().lower()
    if v in {"0", "off", "false", "no", "none"}:
        return "off"
    if v in {"1", "on", "true", "yes", "task"}:
        return "task"
    if v in {"session", "group"}:
        return "session"
    if v == "auto":
        return "off" if (provider or "mock").strip().lower().startswith("mock") else "task"
    raise ValueError(f"unknown AGENT_LLM_COALESCE value: {value!r}")


def split_coalesced_answer(text: str, n: int) -> tuple[list[str], int]:
    """Split ``'<k>: answer'`` lines into ``n`` answers; return (answers, misses).

    Continuation lines are appended to the preceding item so multi-line answers survive.
    """
    answers: list[list[str]] = [[] for _ in range(n)]
    current: int | None = None
    for line in (text or "").splitlines():
        m = _ITEM_RE.match(line)
        if m and 1 <= int(m.group(1)) <= n:
            current = int(m.group(1)) - 1
            answers[current].append(m.group(2).strip())
        elif m:
            current = None  # numbered line outside the batch: never glue it onto an item
        elif current is not None and line.strip():
            answers[current].append(line.strip())
    joined = [" ".join(a).strip() for a in answers]
    misses = sum(1 for a in joined if not a)
    return joined, misses


def build_coalesced_prompt(items: list[PendingLLMCall], contract_hint: str) -> str:
    parts = []
    for i, it in enumerate(items, 1):
        role = (it.system or "").strip()
        head = f"### {i}" + (f" — {role}" if role else "")
        parts.append(f"{head}\n{it.prompt.strip()}")
    hint = f"\n\nPer-item rule: {contract_hint}" if contract_hint else ""
    return "\n\n".join(parts) + hint


class LLMGateway:
    """Per-task LLM submission queue.  Not thread-safe by design (one per RunContext)."""

    def __init__(
        self,
        llm: LLMProvider,
        *,
        mode: str = "off",
        contract_hint: str = "",
        max_batch: int | None = None,
        batch_max_tokens_cap: int = 4096,
        adaptive: bool = True,
        max_repairs: int = 5,
        probe_every: int = 4,
        parallelism: int | None = None,
        init_window: int | None = None,
    ) -> None:
        if mode not in {"off", "task", "session"}:
            raise ValueError(f"unknown coalesce mode: {mode}")
        self.llm = llm
        self.mode = mode
        self.contract_hint = contract_hint
        import os as _os

        # OS-SOTA1.6: the window starts at ``init_window`` (16 = the size the real model has
        # answered without a single miss, OS-SOTA1.5 long session) and may grow by TCP-style
        # slow start (x2 per clean full batch) up to ``max_batch`` (64) until the first miss.
        if max_batch is None:
            max_batch = int(_os.environ.get("AGENT_LLM_MAX_BATCH", "64") or 64)
        self.max_batch = max(1, int(max_batch))
        if init_window is None:
            init_window = int(_os.environ.get("AGENT_LLM_INIT_WINDOW", "16") or 16)
        self.init_window = max(1, min(self.max_batch, int(init_window)))
        self.batch_max_tokens_cap = int(batch_max_tokens_cap)
        self._queue: list[PendingLLMCall] = []
        self._next_ticket = 1
        self.results: dict[int, str] = {}
        self.stats = CoalesceStats()
        self.adaptive = bool(adaptive)
        # 5 attempts >= log2(16)+1: with a shrinking window every item ends answered.
        self.max_repairs = max(0, int(max_repairs))
        self.probe_every = max(1, int(probe_every))
        if parallelism is None:
            import os as _os

            parallelism = int(_os.environ.get("AGENT_LLM_PARALLEL", "4") or 4)
        self.parallelism = max(1, int(parallelism))
        self._window_key = str(getattr(llm, "model", "") or type(llm).__name__)
        _load_window_store()

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def pending(self) -> int:
        return len(self._queue)

    def submit(
        self,
        prompt: str,
        *,
        system: str | None,
        temperature: float | None,
        max_tokens: int | None,
        grade: dict[str, Any] | None = None,
        sink: Any = None,
    ) -> LLMResponse:
        """Queue a deferrable call and return a zero-cost placeholder response."""
        ticket = self._next_ticket
        self._next_ticket += 1
        self._queue.append(PendingLLMCall(ticket, prompt, system, max_tokens, temperature, grade=grade, sink=sink))
        return LLMResponse(
            text="",
            prompt_tokens=0,
            completion_tokens=0,
            model=getattr(self.llm, "model", ""),
            metadata={"deferred": True, "ticket": ticket},
        )

    def discard(self) -> int:
        """Drop queued calls (task crashed before the barrier); returns how many were dropped."""
        n = len(self._queue)
        self._queue.clear()
        return n

    def flush(self) -> list[LLMResponse]:
        """Issue the queued calls; returns the provider responses actually made.

        * OS-SOTA1.1 *miss repair + AIMD batch window*: items a response did not answer are
          re-queued (never silently lost); the per-model window is set to the measured number
          of answered items on a miss and probed upward after clean full batches.
        * OS-SOTA1.5 *parallel vectored submission*: when more than one window of items is
          queued (long sessions), up to ``parallelism`` batches are in flight at once -- like
          several SQEs submitted to io_uring -- so wall time grows with the number of *waves*,
          not requests.  Results are applied in submission order, so answers, grading and
          accounting are identical to the serial path.
        """
        responses: list[LLMResponse] = []
        while self._queue:
            w = (min(self.max_batch, _window_get(self._window_key, self.init_window)) if self.adaptive
                 else self.init_window)
            wave: list[list[PendingLLMCall]] = []
            while self._queue and len(wave) < self.parallelism:
                chunk, self._queue = self._queue[:w], self._queue[w:]
                wave.append(chunk)
            t0 = time.perf_counter()
            if len(wave) == 1:
                calls = [self._call(wave[0])]
            else:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=len(wave), thread_name_prefix="llm-gw") as pool:
                    calls = list(pool.map(self._call, wave))
            self.stats.waves += 1
            self.stats.wall_sec += time.perf_counter() - t0
            proj = [float((resp.metadata or {}).get("projected_latency_sec", 0.0) or 0.0) for resp, _ in calls]
            self.stats.projected_serial_sec += sum(proj)
            self.stats.projected_wall_sec += max(proj) if proj else 0.0
            requeue: list[PendingLLMCall] = []
            for chunk, (resp, answers) in zip(wave, calls):
                missed = self._apply(chunk, resp, answers)
                responses.append(resp)
                if self.adaptive:
                    self._adapt(chunk, missed, w)
                retry = [it for it in missed if it.attempts < self.max_repairs]
                for it in missed:
                    if it not in retry:
                        self._deliver(it, "")
                for it in retry:
                    it.attempts += 1
                self.stats.repaired += len(retry)
                requeue.extend(retry)
            self._queue = requeue + self._queue
        return responses

    def _adapt(self, chunk: list[PendingLLMCall], missed: list[PendingLLMCall], w: int) -> None:
        if missed and len(chunk) > 1:
            # The answered count is a direct capacity measurement; fall back to halving when
            # nothing was answered (multiplicative decrease).
            answered = len(chunk) - len(missed)
            _window_set(self._window_key, max(1, answered if answered else len(chunk) // 2))
            _CLEAN[self._window_key] = 0
            _SLOW_START[self._window_key] = False  # first loss ends slow start (TCP ssthresh)
            self.stats.window_decreases += 1
        elif not missed and len(chunk) >= w:
            if _SLOW_START.get(self._window_key, True):
                _window_set(self._window_key, min(self.max_batch, w * 2))
                return
            # Congestion avoidance: additive increase after several clean full batches.
            _CLEAN[self._window_key] = _CLEAN.get(self._window_key, 0) + 1
            if _CLEAN[self._window_key] >= self.probe_every:
                _window_set(self._window_key, min(self.max_batch, w + 1))
                _CLEAN[self._window_key] = 0

    def _deliver(self, it: PendingLLMCall, text: str) -> None:
        self.results[it.ticket] = text
        if it.grade and it.sink is not None:
            from agent_runtime.runtime.context import apply_grade

            apply_grade(it.grade, text, it.sink)
            self.stats.graded += 1

    def _call(self, chunk: list[PendingLLMCall]) -> tuple[LLMResponse, list[str]]:
        """Provider request only (thread-safe: no gateway state is touched here)."""
        if len(chunk) == 1:
            it = chunk[0]
            resp = self.llm.complete(it.prompt, system=it.system, temperature=it.temperature, max_tokens=it.max_tokens)
            return resp, [resp.text]
        caps = [it.max_tokens for it in chunk]
        if all(c is not None for c in caps):
            # Same total completion budget as the separate calls would have had.
            max_tokens: int | None = min(self.batch_max_tokens_cap, int(sum(caps)) + 8 * len(chunk))
        else:
            max_tokens = None
        temps = {it.temperature for it in chunk}
        temperature = temps.pop() if len(temps) == 1 else None
        prompt = build_coalesced_prompt(chunk, self.contract_hint)
        resp = self.llm.complete(prompt, system=COALESCE_SYSTEM, temperature=temperature, max_tokens=max_tokens)
        answers, _ = split_coalesced_answer(resp.text, len(chunk))
        return resp, answers

    def _apply(self, chunk: list[PendingLLMCall], resp: LLMResponse, answers: list[str]) -> list[PendingLLMCall]:
        new_items = sum(1 for it in chunk if it.attempts == 0)
        if len(chunk) == 1:
            self._deliver(chunk[0], answers[0])
            self.stats.direct_singletons += 1
            resp.metadata.update({"coalesced_items": new_items, "repair_items": 1 - new_items})
            return []
        missed = []
        for it, ans in zip(chunk, answers):
            if ans:
                self._deliver(it, ans)
            else:
                missed.append(it)
        self.stats.batches += 1
        self.stats.items += new_items
        self.stats.parse_misses += len(missed)
        self.stats.provider_requests_saved += max(0, new_items - 1)
        resp.metadata.update({"coalesced_items": new_items, "repair_items": len(chunk) - new_items,
                              "coalesce_parse_misses": len(missed)})
        return missed

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "batches": self.stats.batches,
            "items": self.stats.items,
            "direct_singletons": self.stats.direct_singletons,
            "parse_misses": self.stats.parse_misses,
            "provider_requests_saved": self.stats.provider_requests_saved,
            "repaired": self.stats.repaired,
            "window_decreases": self.stats.window_decreases,
            "window": _window_get(self._window_key, self.init_window),
            "init_window": self.init_window,
            "max_batch": self.max_batch,
            "slow_start": _SLOW_START.get(self._window_key, True),
            "parallelism": self.parallelism,
            "waves": self.stats.waves,
            "wall_sec": round(self.stats.wall_sec, 4),
            "projected_serial_sec": round(self.stats.projected_serial_sec, 3),
            "projected_wall_sec": round(self.stats.projected_wall_sec, 3),
        }


__all__ = [
    "COALESCE_SYSTEM",
    "LLMGateway",
    "PendingLLMCall",
    "build_coalesced_prompt",
    "resolve_coalesce_mode",
    "split_coalesced_answer",
    "flush_session",
    "reset_batch_windows",
]


def flush_session(gateway: "LLMGateway", metrics: Any) -> int:
    """Group-commit flush for a shared ``session`` gateway; records usage into ``metrics``."""
    if gateway is None or not gateway.pending:
        return 0
    responses = gateway.flush()
    for resp in responses:
        md = resp.metadata or {}
        metrics.record_llm(
            resp.prompt_tokens,
            resp.completion_tokens,
            int(md.get("cached_prompt_tokens", 0) or 0),
            logical_requests=int(md.get("coalesced_items", 1)),
            reasoning_tokens=int(md.get("reasoning_tokens", 0) or 0),
            cap_violation=bool(md.get("cap_violation", False)),
            visible_prompt_tokens=md.get("visible_prompt_tokens", md.get("runtime_visible_prompt_tokens")),
            projected_latency_sec=float(md.get("projected_latency_sec", 0.0) or 0.0),
            repair_items=int(md.get("repair_items", 0) or 0) if "repair_items" in md else 0,
        )
        if md.get("coalesce_parse_misses"):
            metrics.record_llm_parse_misses(int(md["coalesce_parse_misses"]))
    return len(responses)
