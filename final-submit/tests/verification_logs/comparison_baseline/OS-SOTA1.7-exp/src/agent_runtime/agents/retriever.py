"""Retriever Agent：信息检索（事实 / 共享记忆 / 非文本状态消费）。"""

from __future__ import annotations

from typing import Any

import numpy as np

from agent_runtime.protocol.message import ActionType, AgentMessage
from agent_runtime.registry.capability import Capability
from agent_runtime.runtime.agent import BaseAgent
from agent_runtime.runtime.context import RunContext
from agent_runtime.runtime.errors import ReferenceError_
from agent_runtime.providers.base import validate_embedding_matrix


class RetrieverAgent(BaseAgent):
    """根据计划检索 operands 的取值。

    支持两种输入承载：
    - 内联：直接从 input_parameters 读取 plan / facts（基线）；
    - 引用：从 `state://`（plan 非文本状态）与 `artifact://`（facts）解析，
      并**真实消费**计划 embedding 对检索目标做语义排序（状态参与路由/决策）。
    引用失效时自动降级。记忆复用在 P4 接入。
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(
            name="retriever",
            capabilities=["retrieval", "memory_lookup", "semantic_search"],
            capability_specs=[
                Capability(name="retrieval", description="Resolve task operands from inline facts or referenced artifacts/state.", input_schema={"plan":"object|state_ref","facts":"object|artifact_ref"}, output_schema={"evidence":"object"}),
                Capability(name="memory_lookup", description="Retrieve reusable cross-task memory by semantic/keyword/tag signals.", input_schema={"query":"string","keywords":"array","tags":"array"}, output_schema={"memory_refs":"array"}),
                Capability(name="semantic_search", description="Use non-text embeddings to rank retrieval targets.", input_schema={"embedding":"vector","candidates":"array"}, output_schema={"ranked_candidates":"array"}),
            ],
            actions=[ActionType.RETRIEVE],
            **kwargs,
        )

    async def handle(self, message: AgentMessage, ctx: RunContext) -> AgentMessage:
        self.consume_workflow_state(message, ctx, expected_stage="planner")
        plan = message.input_parameters.get("plan")
        if plan is None:
            plan = self._resolve_plan(message, ctx)
        facts = message.input_parameters.get("facts")
        if facts is None:
            facts = self._resolve_facts(message, ctx)
        plan = plan or {}
        facts = facts or {}
        operands = plan.get("operands", [])

        # Build the deterministic evidence view first, then let Memory decide whether some
        # logical lookups are redundant.  Accessing the already-resolved artifact map is not
        # itself counted as an external tool call; the per-operand counters below represent the
        # work a normal Retriever would perform.
        evidence: dict[str, Any] = {op: facts.get(op) for op in operands}
        evidence_refs: list[str] = []
        if ctx.use_memory and ctx.memory is not None:
            evidence_refs = self._augment_from_memory(plan, evidence, ctx)

        exact_answer = ctx.blackboard.get("reused_answer") is not None
        reused_solution = ctx.blackboard.get("reused_solution")
        for op in operands:
            if ctx.config.memory_fast_path and exact_answer:
                continue
            if (
                ctx.config.memory_fast_path
                and op == "strategy"
                and evidence.get(op) is None
                and reused_solution is not None
            ):
                # The missing procedure is supplied by a compatible procedural memory.
                continue
            ctx.record_tool(f"lookup:{op}")
        ctx.blackboard["retrieved_evidence_refs"] = list(evidence_refs)

        ctx.blackboard["evidence"] = evidence
        if not (
            ctx.config.memory_fast_path
            and ctx.blackboard.get("memory_candidate_id") is not None
        ):
            if getattr(ctx.config, "llm_advisory", True):
                ctx.call_llm(
                    f"Summarize retrieved evidence keys: {list(evidence.keys())}",
                    system="You are an information retriever.",
                    deferrable=True,
                )

        # Producer-side out-of-band materialization: evidence is an Artifact before the
        # reply crosses the control plane.  Keep the full value on the local message so
        # checkpoint/idempotency replay can rematerialize a stale reference in a new process.
        artifact_references: list[str] = []
        if (
            ctx.config.reference_first_data_plane
            and ctx.use_state_exchange
            and ctx.artifact_store is not None
        ):
            ev_ref = ctx.artifact_store.put(evidence, summary=f"evidence(n={len(evidence)})")
            ctx.blackboard["evidence_artifact_ref"] = ev_ref.uri
            if ctx.config.reference_first_data_plane:
                from agent_runtime.protocol.placement import choose_reference_placement

                decision = choose_reference_placement(
                    action_type=ActionType.RETRIEVE,
                    result={"evidence": evidence},
                    artifact_reference=ev_ref.uri,
                )
                ctx.blackboard["evidence_reply_placement"] = {
                    "reference": decision.use_reference,
                    "inline_bytes": decision.inline_bytes,
                    "reference_bytes": decision.reference_bytes,
                }
                ctx.metrics.record_placement(
                    use_reference=decision.use_reference,
                    saving_bytes=decision.saving_bytes,
                )
                if decision.use_reference:
                    artifact_references.append(ev_ref.uri)

        return self.reply(
            message,
            ctx,
            action_type=ActionType.RETRIEVE,
            capability="retrieval",
            result={"evidence": evidence},
            artifact_references=artifact_references,
            evidence_references=evidence_refs,
            # The plan reference is an input, not Retriever output; do not echo it on the reply.
            state_reference=None,
            confidence=0.9,
        )

    def _resolve_plan(self, message: AgentMessage, ctx: RunContext) -> dict[str, Any]:
        """从非文本状态解析计划，并消费 embedding 进行语义排序。"""
        ref = message.input_parameters.get("plan_ref") or message.state_reference
        if not ref or ctx.state_exchange is None:
            return {}
        try:
            state = ctx.state_exchange.get_plan_state(ref)
        except ReferenceError_:
            ctx.metrics.record_degradation()
            return {}
        dag = dict(state["dag"])
        emb = state["embedding"]
        operands = dag.get("operands", [])
        if operands:
            # 真实消费：用 plan embedding 对检索目标做语义相似度排序。
            op_vecs = validate_embedding_matrix(
                ctx.embedding.embed([str(o) for o in operands]),
                expected_rows=len(operands),
                expected_dim=int(np.asarray(emb).shape[0]),
                context="retriever operand embedding",
            )
            sims = op_vecs @ np.asarray(emb, dtype=np.float32)
            # Same deterministic decision function the Planner certified (stable sort).
            order = list(np.argsort(-sims, kind="stable"))
            dag["operands"] = [operands[i] for i in order]
        ctx.blackboard["state_consumed"] = True
        return dag

    def _resolve_facts(self, message: AgentMessage, ctx: RunContext) -> dict[str, Any]:
        ref = message.input_parameters.get("facts_ref")
        if not ref or ctx.artifact_store is None:
            return {}
        try:
            return ctx.artifact_store.get_json(ref)
        except ReferenceError_:
            ctx.metrics.record_degradation()
            return {}

    def _augment_from_memory(
        self, plan: dict[str, Any], evidence: dict[str, Any], ctx: RunContext
    ) -> list[str]:
        """从共享记忆检索可复用经验。

        - 命中「同一子问题」（签名一致）→ 复用结论（answer），下游跳过重复计算；
        - 命中「相似子问题」（操作相同、签名不同）→ 复用策略/模板（solution），
          如 A1 的修复策略用于 A2、B1 的改造规范用于 B2。
        记录 Retrieved / Used 命中；效果反馈（Effective/Harmful）由 Reviewer 验收后写回。
        """
        if ctx.memory is None:
            return []
        from agent_runtime.memory.signature import task_signature

        operation = plan.get("operation") or "noop"
        operands = plan.get("operands", [])
        defect = plan.get("defect") or ""
        signature = task_signature(operation, evidence)
        ctx.blackboard["signature"] = signature

        query = f"operation {operation} defect {defect} operands {operands}"
        keywords = [str(operation), *([str(defect)] if defect else []), *[str(o) for o in operands]]
        hits = ctx.memory.retrieve(
            query, keywords=keywords, top_k=3, current_task_id=ctx.task_id
        )
        compatibility = [
            self._memory_is_compatible(h.record, operation, defect, signature) for h in hits
        ]
        incompatible = sum(1 for ok in compatibility if not ok)
        gate_enabled = bool(ctx.config.memory_compatibility_gate)
        ctx.metrics.record_memory(
            queries=1,
            query_hits=1 if hits else 0,
            retrieved=len(hits),
            incompatible=incompatible,
            rejected=incompatible if gate_enabled else 0,
        )

        refs = [f"memory://{h.record.memory_id}" for h in hits]
        # Selection is not adoption.  ``memory_used`` and ``reuse_count`` are updated only
        # by the Executor when a retrieved answer/strategy actually affects execution.
        ctx.blackboard["reused_memory_ids"] = []
        ctx.blackboard.pop("memory_candidate_id", None)
        ctx.blackboard.pop("memory_candidate_mode", None)
        if gate_enabled:
            best = next((h for h, ok in zip(hits, compatibility) if ok), None)
        else:
            # 仅用于受控消融：模拟“命中即用”的朴素复用策略。
            best = hits[0] if hits else None
        if best is not None:
            payload = best.record.payload
            if hasattr(ctx.memory, "get_lineage"):
                ctx.blackboard["memory_lineage"] = ctx.memory.get_lineage(best.record.memory_id)
            if payload.get("signature") == signature:
                ctx.blackboard["reused_answer"] = payload.get("answer")
                ctx.blackboard["memory_candidate_id"] = best.record.memory_id
                ctx.blackboard["memory_candidate_mode"] = "answer"
            elif payload.get("solution") is not None:
                ctx.blackboard["reused_solution"] = payload.get("solution")
                ctx.blackboard["memory_candidate_id"] = best.record.memory_id
                ctx.blackboard["memory_candidate_mode"] = "solution"
        return refs

    @staticmethod
    def _memory_is_compatible(record, operation: str, defect: str, signature: str) -> bool:
        """Gate memory reuse to reduce obvious negative transfer.

        Exact sub-problem matches may reuse an answer.  Cross-task procedural reuse
        additionally requires the same operation and, when the planner provides a
        defect/intent class, the same class.  This keeps retrieval broad for recall
        while making *use* conservative.
        """
        if record.success_feedback is False:
            return False
        payload = record.payload
        if payload.get("operation") != operation:
            return False
        if payload.get("signature") == signature:
            return True
        if payload.get("solution") is None:
            return False
        if defect:
            return str(payload.get("defect") or "") == str(defect)
        return True


__all__ = ["RetrieverAgent"]
