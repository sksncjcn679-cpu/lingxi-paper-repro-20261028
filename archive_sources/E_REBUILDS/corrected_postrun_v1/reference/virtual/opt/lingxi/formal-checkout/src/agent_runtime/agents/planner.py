"""Planner Agent：任务规划 / 分解。"""

from __future__ import annotations

from agent_runtime.protocol.message import ActionType, AgentMessage
from agent_runtime.registry.capability import Capability
from agent_runtime.runtime.agent import BaseAgent, RetryPolicy
from agent_runtime.runtime.context import RunContext


class PlannerAgent(BaseAgent):
    """将任务分解为可执行的计划（operation + operands）。

    规划逻辑是确定性的（由任务输入决定），同时调用一次 LLM 以产出规划理由，
    使 token 统计反映真实的规划开销。
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(
            name="planner",
            capabilities=["task_planning", "decompose"],
            capability_specs=[
                Capability(name="task_planning", description="Decompose a task into an operation, operands and intent/defect class.", input_schema={"description":"string","payload":"object"}, output_schema={"plan":"object"}),
                Capability(name="decompose", description="Produce a compact executable plan for downstream agents.", input_schema={"task":"object"}, output_schema={"operation":"string","operands":"array"}),
            ],
            actions=[ActionType.PLAN],
            retry=kwargs.pop("retry", RetryPolicy(max_retries=2)),
            **kwargs,
        )

    async def handle(self, message: AgentMessage, ctx: RunContext) -> AgentMessage:
        params = message.input_parameters
        description = params.get("description", "")
        payload = params.get("payload", {})

        # 支持两种入站形态：内联完整 payload（基线）或扁平的小字段（引用模式）。
        src = payload if payload else params
        operation = src.get("question")
        operands = src.get("operands")
        defect = src.get("defect")

        # fable8 agentic plan caching: skip the LLM decomposition only when shared memory holds
        # a Reviewer-verified plan with exactly the same signature (operation + operand schema +
        # topic).  The plan itself is still derived from THIS task's payload below, so a cache
        # hit can change cost, never the plan; the Reviewer still verifies the final answer.
        template = None
        topic = ctx.blackboard.get("task_topic")
        if (
            topic
            and getattr(ctx.config, "plan_cache", False)
            and ctx.use_memory
            and ctx.memory is not None
            and operation
            and hasattr(ctx.memory, "find_plan_template")
        ):
            template = ctx.memory.find_plan_template(
                operation=str(operation),
                operands=list(operands or []),
                topic=str(topic),
                current_task_id=ctx.task_id,
            )
        if template is not None:
            ctx.metrics.snapshot.plan_cache_hits += 1
            ctx.blackboard["plan_cache_memory_id"] = template.memory_id
        else:
            if getattr(ctx.config, "plan_cache", False) and ctx.use_memory:
                ctx.metrics.snapshot.plan_cache_misses += 1
            if getattr(ctx.config, "llm_advisory", True):
                ctx.call_llm(
                    f"Decompose the following task into operation and operands: {description}",
                    system="You are a meticulous task planner.",
                    deferrable=True,
                )
        plan = {
            "operation": operation or "noop",
            "operands": list(operands or []),
            "defect": defect or "",
        }
        ctx.blackboard["plan"] = plan

        # Reference-first data plane: when state exchange is enabled, materialize the
        # authoritative plan before constructing the control-plane reply.  The reply object
        # deliberately keeps the full plan in-memory for checkpoint/idempotency recovery,
        # while structured wire projection can carry only ``state_reference`` + summary.
        state_reference = None
        if (
            ctx.config.reference_first_data_plane
            and ctx.use_state_exchange
            and ctx.state_exchange is not None
        ):
            ref = ctx.state_exchange.put_plan_state(plan, decision_probe=_operand_ranking_probe(plan, ctx))
            ctx.metrics.snapshot.state_decision_certificates = int(getattr(ctx.state_exchange, "certificates", 0))
            ctx.blackboard["plan_state_ref"] = ref.uri
            if ctx.config.reference_first_data_plane:
                from agent_runtime.protocol.placement import choose_reference_placement

                decision = choose_reference_placement(
                    action_type=ActionType.PLAN,
                    result={"plan": plan},
                    state_reference=ref.uri,
                )
                ctx.blackboard["plan_reply_placement"] = {
                    "reference": decision.use_reference,
                    "inline_bytes": decision.inline_bytes,
                    "reference_bytes": decision.reference_bytes,
                }
                ctx.metrics.record_placement(
                    use_reference=decision.use_reference,
                    saving_bytes=decision.saving_bytes,
                )
                if decision.use_reference:
                    state_reference = ref.uri

        return self.reply(
            message,
            ctx,
            action_type=ActionType.PLAN,
            capability="task_planning",
            result={"plan": plan},
            state_reference=state_reference,
            confidence=0.95,
        )


def _operand_ranking_probe(plan: dict, ctx: RunContext):
    """The Retriever's declared use of the plan embedding (operand ranking, see
    ``RetrieverAgent._resolve_plan``) as a deterministic decision function.

    The Planner can evaluate it because the operand texts and the embedding provider are
    shared; the state exchange then keeps the cheapest precision under which the decision is
    bit-for-bit identical to float32.
    """
    operands = [str(o) for o in plan.get("operands") or []]
    if len(operands) < 2 or ctx.embedding is None:
        return None
    import numpy as _np

    from agent_runtime.state_exchange.exchange import OperandRankingProbe

    return OperandRankingProbe(_np.asarray(ctx.embedding.embed(operands), dtype=_np.float32))


__all__ = ["PlannerAgent"]
