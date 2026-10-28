"""Reviewer / Summarizer Agent：审查结果、判定成败、沉淀共享记忆。"""

from __future__ import annotations

import os
from typing import Any

from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.registry.capability import Capability
from agent_runtime.runtime.agent import BaseAgent
from agent_runtime.runtime.context import RunContext


JUDGE_GLOBAL_LABELS = (
    "port_conflict", "permission_denied", "compatible", "rebuild_required",
    "dns_failure", "disk_full", "oom_killed", "selinux_denied",
)


class ReviewerSummarizerAgent(BaseAgent):
    """对执行结果进行审查与总结，并将经验沉淀为共享记忆。

    任务的自动验收（task.verify）由运行上下文持有的任务对象提供，不通过 Agent 间
    消息传输（它属于评测/真值，而非协作内容）。记忆沉淀在 P4 接入。
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(
            name="reviewer",
            capabilities=["review", "summarize", "memorize"],
            capability_specs=[
                Capability(name="review", description="Verify execution output against task acceptance criteria.", input_schema={"value":"json","task":"context"}, output_schema={"success":"boolean","quality":"number"}),
                Capability(name="summarize", description="Create a concise reusable summary of the task outcome.", input_schema={"outcome":"object"}, output_schema={"summary":"string"}),
                Capability(name="memorize", description="Persist reusable evidence/strategy metadata into shared memory.", input_schema={"task":"object","verdict":"object"}, output_schema={"memory_id":"string"}),
            ],
            actions=[ActionType.REVIEW, ActionType.SUMMARIZE, ActionType.MEMORIZE],
            **kwargs,
        )

    async def handle(self, message: AgentMessage, ctx: RunContext) -> AgentMessage:
        self.consume_workflow_state(message, ctx, expected_stage="executor")
        value = message.input_parameters.get("value")
        task = ctx.blackboard.get("task")

        outputs: dict[str, Any] = {"answer": value}
        if task is not None:
            verdict = task.verify(outputs)
        else:  # 无任务对象时的保守判定
            from agent_runtime.runtime.task import VerdictResult

            verdict = VerdictResult(success=value is not None, quality=1.0 if value else 0.0)

        ctx.metrics.set_result(verdict.success, verdict.quality)
        ctx.blackboard["answer"] = value
        ctx.blackboard["verdict"] = verdict

        # This is an explicit semantic-visibility experiment. The prompt contains the
        # exact inbound wire message through RunContext.call_llm; it does not inject the
        # task's original facts, so reference-first modes are tested honestly.
        judge_on = bool(getattr(ctx.config, "llm_judge", False)) or os.environ.get("AGENT_LLM_JUDGE", "").strip() == "1"
        if judge_on and task is not None:
            self._judge_inbound_message(task, ctx)

        # A verified memory reuse already carries a durable summary/procedure.  Re-running an
        # LLM solely to paraphrase the same successful result defeats the purpose of cross-task
        # reuse, so the fast path performs deterministic verification + structured feedback only.
        if not (
            ctx.config.memory_fast_path
            and verdict.success
            and (
                ctx.blackboard.get("memory_fast_path")
                or ctx.blackboard.get("memory_adopted_id") is not None
            )
        ):
            if getattr(ctx.config, "llm_advisory", True):
                ctx.call_llm(
                    f"Summarize the task outcome (success={verdict.success}).",
                    system="You are a reviewer that writes concise, reusable summaries.",
                    deferrable=True,
                )

        # 记忆沉淀接入点（P4 实现）。
        if ctx.use_memory and ctx.memory is not None:
            self._memorize(task, value, verdict, ctx)

        return self.reply(
            message,
            ctx,
            action_type=ActionType.REVIEW,
            capability="review",
            result={"answer": value, "success": verdict.success, "quality": verdict.quality},
            status=MessageStatus.OK if verdict.success else MessageStatus.ERROR,
            confidence=0.9 if verdict.success else 0.3,
        )

    @staticmethod
    def _judge_inbound_message(task, ctx: RunContext) -> None:
        # Duck-typed on purpose: in agent-process mode these are RPC proxies of dicts, and the
        # old ``isinstance(..., dict)`` check silently disabled the judge there.
        try:
            expected = task.expected.get("answer")
        except AttributeError:
            expected = None
        try:
            labels = list(task.payload.get("diagnosis_labels", []) or [])
        except AttributeError:
            labels = []
        labels = [str(label).strip().lower() for label in labels if str(label).strip()]
        if getattr(getattr(ctx, "config", None), "llm_judge_labels", "task") == "global" and labels:
            # OS-SOTA1.1: 8-way choice (chance 12.5%) instead of the task's 2 labels (chance
            # 50%): every scenario label plus plausible OS-fault distractors, fixed order.
            labels = list(JUDGE_GLOBAL_LABELS)
        if expected is None or not labels:
            return
        prompt = (
            "Given the inbound message, choose the single most likely diagnosis label from: "
            + ", ".join(labels)
            + ". Answer with the label only."
        )
        # OS-SOTA1.1: the verdict only feeds a metric, never the task outcome, so the judge is
        # deferrable and can travel in the coalesced request.  Grading is declarative (pure
        # data, so it also crosses the agent-process RPC boundary) and is applied by the
        # runtime when the text arrives -- immediately for direct calls, at flush otherwise.
        ctx.call_llm(
            prompt,
            system="You are a strict diagnostic judge.",
            deferrable=True,
            grade={"kind": "label", "expected": str(expected).strip().lower(), "labels": labels},
        )

    def _memorize(self, task, value, verdict, ctx: RunContext) -> None:
        """将结论沉淀为共享记忆，并对复用过的记忆写回效果反馈。"""
        from agent_runtime.memory.models import MemoryRecord, MemoryType

        if ctx.memory is None:
            return

        # 复用效果反馈：只有“真正采用”且可以归因的结果才更新可靠度后验。
        # Task 失败本身不等于 Memory 有害；例如外部 verifier、sandbox timeout、
        # provider failure 都不能被粗暴归罪给一条被采用的经验。
        attribution = self._attribute_memory_outcome(task, value, verdict, ctx)
        ctx.blackboard["memory_feedback_attribution"] = attribution
        for mid in ctx.blackboard.get("reused_memory_ids", []):
            if attribution == "effective":
                ctx.memory.record_feedback(mid, effective=True)
                ctx.metrics.record_memory(effective=1)
            elif attribution == "harmful":
                ctx.memory.record_feedback(mid, effective=False)
                ctx.metrics.record_memory(harmful=1)
            else:
                ctx.metrics.record_memory(unattributed=1)
            ctx.record_event(
                "memory.feedback",
                stage=self.name,
                payload={"memory_id": mid, "attribution": attribution},
            )

        plan = ctx.blackboard.get("plan", {})
        operands = plan.get("operands", [])
        defect = plan.get("defect") or ""
        record = MemoryRecord(
            source_agent=self.name,
            task_topic=task.topic if task else "unknown",
            summary=f"operation {plan.get('operation')} over {operands} -> {value}",
            memory_type=MemoryType.PROCEDURAL,
            tags=list(task.tags) if task else [],
            keywords=[
                str(plan.get("operation")),
                *([str(defect)] if defect else []),
                *[str(o) for o in operands],
            ],
            confidence=0.9 if verdict.success else 0.3,
            quality_score=0.9 if verdict.success else 0.2,
            importance_score=0.85 if verdict.success else 0.25,
            artifact_refs=(
                [str(ctx.blackboard["evidence_artifact_ref"])]
                if ctx.blackboard.get("evidence_artifact_ref") else []
            ),
            evidence_refs=list(ctx.blackboard.get("retrieved_evidence_refs", [])),
            derived_from=([str(ctx.blackboard["memory_adopted_id"])] if ctx.blackboard.get("memory_adopted_id") else []),
            provenance={
                "trace_id": ctx.trace_id,
                "agents": ["planner", "retriever", "executor", "reviewer"],
            },
            task_id=ctx.task_id,
            success_feedback=verdict.success,
            payload={
                "operation": plan.get("operation"),
                "operands": operands,
                "answer": value,
                "solution": ctx.blackboard.get("solution_used"),
                "signature": ctx.blackboard.get("signature"),
                "request_fingerprint": ctx.blackboard.get("request_fingerprint"),
                "defect": defect,
                "evidence_keys": sorted(str(k) for k in ctx.blackboard.get("evidence", {})),
            },
        )
        ctx.memory.write(record)

    @staticmethod
    def _attribute_memory_outcome(task, value, verdict, ctx: RunContext) -> str:
        """Return ``effective`` / ``harmful`` / ``unattributed`` / ``none``.

        The goal is deliberately conservative causal attribution, not a generic blame model.
        - a successful task after an actually adopted memory is positive evidence;
        - a reused *answer* is harmful only when it directly conflicts with an explicit expected
          answer (or equivalent verifier detail);
        - a reused *solution* is harmful only when execution shows the strategy itself was
          inapplicable or its generated program failed functional verification;
        - infrastructure failures or unrelated verifier failures stay unattributed and therefore
          do not change the memory reliability posterior.
        """

        contribution = ctx.blackboard.get("memory_reuse_contribution")
        if not isinstance(contribution, dict):
            return "none"
        if verdict.success:
            return "effective"

        mode = contribution.get("mode")
        provenance = ctx.blackboard.get("execution_provenance")
        provenance = provenance if isinstance(provenance, dict) else {}

        if mode == "answer" and provenance.get("source") == "memory_answer":
            expected_known = False
            expected = None
            if task is not None and isinstance(getattr(task, "expected", None), dict):
                if "answer" in task.expected:
                    expected_known = True
                    expected = task.expected.get("answer")
            if not expected_known and isinstance(getattr(verdict, "detail", None), dict):
                if "expected" in verdict.detail:
                    expected_known = True
                    expected = verdict.detail.get("expected")
            if expected_known and value != expected:
                return "harmful"
            return "unattributed"

        if mode == "solution" and provenance.get("source") == "memory_solution":
            if provenance.get("failure_class") in {"strategy_inapplicable", "verification_failed"}:
                return "harmful"
            return "unattributed"

        return "unattributed"


__all__ = ["ReviewerSummarizerAgent"]
