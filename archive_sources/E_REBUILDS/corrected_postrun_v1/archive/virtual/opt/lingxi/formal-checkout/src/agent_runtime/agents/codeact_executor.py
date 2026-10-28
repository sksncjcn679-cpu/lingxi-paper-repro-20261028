"""CodeAct Executor Agent：执行计划得到结果。

P1 直接进行确定性计算；P5 接入沙箱（LLM 生成 Python 代码并在受限环境执行）。
"""

from __future__ import annotations

from typing import Any

from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.registry.capability import Capability
from agent_runtime.runtime.agent import BaseAgent
from agent_runtime.runtime.context import RunContext
from agent_runtime.runtime.errors import ReferenceError_

_OPS = {
    "sum": lambda xs: sum(xs),
    "product": lambda xs: _product(xs),
    "max": lambda xs: max(xs),
    "min": lambda xs: min(xs),
    "mean": lambda xs: sum(xs) / len(xs) if xs else 0,
    "count": lambda xs: len(xs),
    "concat": lambda xs: "".join(str(x) for x in xs),
}


def _product(xs: list[Any]) -> Any:
    out: Any = 1
    for x in xs:
        out *= x
    return out


class CodeActExecutorAgent(BaseAgent):
    """根据 operation + evidence 计算结果。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            name="executor",
            capabilities=["execute", "codeact", "compute"],
            capability_specs=[
                Capability(name="execute", description="Execute a compact plan using retrieved evidence and return a typed result.", input_schema={"plan":"object","evidence":"object"}, output_schema={"value":"json"}),
                Capability(name="codeact", description="Run generated/deterministic Python code in the configured lightweight sandbox.", input_schema={"code":"string","policy":"sandbox"}, output_schema={"stdout":"string","exit_code":"integer"}),
                Capability(name="compute", description="Perform deterministic computation over retrieved operands.", input_schema={"operation":"string","values":"array"}, output_schema={"value":"json"}),
            ],
            actions=[ActionType.EXECUTE],
            **kwargs,
        )

    async def handle(self, message: AgentMessage, ctx: RunContext) -> AgentMessage:
        self.consume_workflow_state(message, ctx, expected_stage="retriever")
        plan = message.input_parameters.get("plan")
        if plan is None:
            plan = self._resolve_plan(message, ctx)
        evidence = message.input_parameters.get("evidence")
        if evidence is None:
            evidence = self._resolve_evidence(message, ctx)
        plan = plan or {}
        evidence = evidence or {}
        operation = plan.get("operation", "noop")
        operands = plan.get("operands", [])

        if operation == "llm_diagnose":
            from agent_runtime.agents.model_decision import diagnose
            value = diagnose(evidence, ctx)
        elif operation == "rank_candidates":
            from agent_runtime.agents.candidate_workflow import rank_candidates
            value = rank_candidates(evidence, ctx)
        elif operation == "codefix":
            value: Any = await self._handle_codefix(evidence, ctx)
        elif operation in {"diagnose_service", "assess_migration", "tune_kernel_param"}:
            value = await self._handle_system_operation(operation, evidence, ctx)
        else:
            value = await self._handle_arithmetic(operation, operands, evidence, ctx)

        ctx.blackboard["execution"] = value
        if not (
            ctx.config.memory_fast_path
            and ctx.blackboard.get("memory_adopted_id") is not None
        ):
            if getattr(ctx.config, "llm_advisory", True):
                ctx.call_llm(
                    f"Execute operation {operation} over {len(operands)} operands.",
                    system="You are a careful code executor.",
                    deferrable=True,
                )

        status = MessageStatus.OK if value is not None else MessageStatus.ERROR
        return self.reply(
            message,
            ctx,
            action_type=ActionType.EXECUTE,
            capability="execute",
            result={"value": value},
            status=status,
            confidence=0.92,
        )

    async def _handle_arithmetic(
        self, operation: str, operands: list[Any], evidence: dict[str, Any], ctx: RunContext
    ) -> Any:
        values = [evidence.get(k) for k in operands]
        values = [v for v in values if v is not None]
        reused = ctx.blackboard.get("reused_answer")
        if reused is not None:
            self._mark_memory_adopted(ctx, mode="answer", value=reused)
            ctx.blackboard["execution_provenance"] = {
                "source": "memory_answer",
                "memory_id": ctx.blackboard.get("memory_candidate_id"),
                "status": "completed",
            }
            value = reused  # 同一子问题：复用结论，跳过重复计算。
        elif ctx.sandbox is not None:
            value = await self._execute_in_sandbox(operation, values, ctx)
            ctx.blackboard["execution_provenance"] = {
                "source": "sandbox",
                "status": "completed" if value is not None else "failed",
            }
        else:
            ctx.record_tool(f"compute:{operation}:{operands}")
            value = self._compute(operation, values)
            ctx.blackboard["execution_provenance"] = {
                "source": "local_compute",
                "status": "completed" if value is not None else "failed",
            }
        ctx.blackboard["solution_used"] = value
        return value


    async def _handle_system_operation(
        self, operation: str, evidence: dict[str, Any], ctx: RunContext
    ) -> str | None:
        """Execute deterministic Linux/openEuler diagnostic procedures in the sandbox.

        The first task in each continuous group provides the named procedure; the second
        intentionally omits it so the same procedure must be adopted from shared memory.
        This isolates Runtime reuse rather than relying on host-specific services.
        """
        import json

        evidence_strategy = evidence.get("strategy")
        reused_solution = ctx.blackboard.get("reused_solution")
        strategy = evidence_strategy or reused_solution
        expected = {
            "diagnose_service": "diagnose_service_v1",
            "assess_migration": "assess_openeuler_migration_v1",
            "tune_kernel_param": "tune_kernel_param_v1",
        }.get(operation)
        # The adversarial ABI pair deliberately uses separate procedures under the same
        # operation name.  The retriever's defect gate keeps these procedures distinct.
        if operation == "assess_migration" and strategy in {"assess_abi_v1", "assess_glibc_symbol_v1"}:
            expected = strategy
        if strategy != expected:
            ctx.blackboard["execution_provenance"] = {
                "source": "memory_solution" if evidence_strategy is None and reused_solution else "task_evidence",
                "status": "failed",
                "failure_class": "strategy_inapplicable",
            }
            return None
        from_memory = evidence_strategy is None and reused_solution is not None
        if from_memory:
            self._mark_memory_adopted(ctx, mode="solution", value=strategy)

        payload = {k: v for k, v in evidence.items() if k != "strategy"}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if operation == "diagnose_service":
            logic = (
                "status = str(data.get('service_status','')).lower()\n"
                "port = str(data.get('port_probe','')).lower()\n"
                "dep = str(data.get('dependency_status','')).lower()\n"
                "perm = str(data.get('permission_status','')).lower()\n"
                "if 'denied' in perm or 'permission denied' in status: answer='permission_denied'\n"
                "elif 'listen' in port or 'address already in use' in status or 'occupied' in port: answer='port_conflict'\n"
                "elif 'missing' in dep or 'inactive' in dep or 'failed' in dep: answer='dependency_missing'\n"
                "elif 'failed' in status: answer='service_failure_unknown'\n"
                "else: answer='healthy'\n"
            )
        elif operation == "tune_kernel_param":
            logic = (
                "sysctl = data.get('sysctl_snapshot', {})\n"
                "if 'vm.swappiness' in sysctl and 'net.core.somaxconn' in sysctl: answer='kernel_profile_tuned'\n"
                "elif 'vm.swappiness' in sysctl: answer='swappiness_tuned'\n"
                "elif 'net.core.somaxconn' in sysctl: answer='somaxconn_tuned'\n"
                "else: answer='kernel_profile_unknown'\n"
            )
        elif operation == "assess_migration" and strategy in {"assess_abi_v1", "assess_glibc_symbol_v1"}:
            answer_literal = "symbol_review" if strategy == "assess_glibc_symbol_v1" else "abi_compatible"
            logic = (
                "native = str(data.get('native_dependency', '')).lower()\n"
                f"answer = {answer_literal!r}\n"
                "if 'glibc symbol' in native: answer='symbol_review'\n"
            )
        else:
            logic = (
                "arch = str(data.get('target_arch','')).lower()\n"
                "abi = str(data.get('python_abi','')).lower()\n"
                "native = str(data.get('native_dependency','')).lower()\n"
                "manager = str(data.get('service_manager','')).lower()\n"
                "if 'i686' in native or arch not in {'x86_64','aarch64'}: answer='rebuild_required'\n"
                "elif abi and abi not in {'cp310','cp311','cp312','py3'}: answer='python_abi_review'\n"
                "elif manager not in {'systemd','none'}: answer='service_unit_conversion'\n"
                "else: answer='compatible'\n"
            )
        program = (
            "import json\n"
            f"data = json.loads({encoded!r})\n"
            + logic
            + "print(json.dumps({'value': answer}))\n"
        )
        if ctx.sandbox is None:
            ctx.metrics.record_degradation()
            ctx.blackboard["execution_provenance"] = {
                "source": "memory_solution" if from_memory else "task_evidence",
                "status": "failed",
                "failure_class": "sandbox_required",
            }
            return None
        ctx.record_tool(f"sandbox:{operation}")
        result = ctx.sandbox.run(program)
        if ctx.artifact_store is not None and result.stdout:
            ctx.artifact_store.put(result.stdout, summary=f"{operation} stdout")
        if not result.ok:
            ctx.blackboard["execution_provenance"] = {
                "source": "memory_solution" if from_memory else "task_evidence",
                "memory_id": ctx.blackboard.get("memory_candidate_id") if from_memory else None,
                "status": "failed",
                "failure_class": "sandbox_infrastructure" if (result.timed_out or result.error) else "verification_failed",
            }
            return None
        try:
            value = json.loads(result.stdout.strip()).get("value")
        except (ValueError, AttributeError):
            value = None
        ctx.blackboard["solution_used"] = strategy
        ctx.blackboard["execution_provenance"] = {
            "source": "memory_solution" if from_memory else "task_evidence",
            "memory_id": ctx.blackboard.get("memory_candidate_id") if from_memory else None,
            "status": "completed" if value is not None else "failed",
            "failure_class": None if value is not None else "verification_failed",
        }
        return value

    async def _handle_codefix(self, evidence: dict[str, Any], ctx: RunContext) -> bool:
        """应用修复/改造策略并在沙箱中验证（CodeAct）。

        策略来自证据，或复用共享记忆中的相似经验（reused_solution）。
        """
        from agent_runtime.agents.code_strategies import apply_strategy

        module = evidence.get("module")
        test = evidence.get("test")
        evidence_strategy = evidence.get("strategy")
        reused_solution = ctx.blackboard.get("reused_solution")
        strategy = evidence_strategy or reused_solution
        if not module or not test or not strategy:
            ctx.blackboard["execution_provenance"] = {
                "source": "memory_solution" if evidence_strategy is None and reused_solution else "task_evidence",
                "status": "failed",
                "failure_class": "missing_input",
            }
            return False
        from_memory = evidence_strategy is None and reused_solution is not None
        if from_memory:
            self._mark_memory_adopted(ctx, mode="solution", value=strategy)
        fixed = apply_strategy(strategy, module)
        if fixed is None:
            ctx.blackboard["execution_provenance"] = {
                "source": "memory_solution" if from_memory else "task_evidence",
                "memory_id": ctx.blackboard.get("memory_candidate_id") if from_memory else None,
                "status": "failed",
                "failure_class": "strategy_inapplicable",
            }
            return False
        ctx.blackboard["solution_used"] = strategy
        program = fixed + "\n\n" + test
        if ctx.artifact_store is not None:
            ctx.artifact_store.put(fixed, summary="fixed module")
        if ctx.sandbox is not None:
            ctx.record_tool("sandbox:codefix")
            result = ctx.sandbox.run(program)
            if result.ok:
                failure_class = None
            elif result.timed_out or result.error is not None:
                failure_class = "sandbox_infrastructure"
            else:
                failure_class = "verification_failed"
            ctx.blackboard["execution_provenance"] = {
                "source": "memory_solution" if from_memory else "task_evidence",
                "memory_id": ctx.blackboard.get("memory_candidate_id") if from_memory else None,
                "status": "completed" if result.ok else "failed",
                "failure_class": failure_class,
                "sandbox_timed_out": result.timed_out,
                "sandbox_error": result.error,
                "sandbox_exit_code": result.exit_code,
            }
            return result.ok
        # Never execute task-provided/generated code inside the Runtime process.  Earlier
        # versions used a local ``exec`` fallback when no sandbox service was configured;
        # that turned ``use_sandbox=False`` into arbitrary host-code execution.  Code-fix
        # tasks now require an explicit sandbox backend and fail closed otherwise.
        ctx.metrics.record_degradation()
        ctx.blackboard["execution_provenance"] = {
            "source": "memory_solution" if from_memory else "task_evidence",
            "memory_id": ctx.blackboard.get("memory_candidate_id") if from_memory else None,
            "status": "failed",
            "failure_class": "sandbox_required",
        }
        return False

    @staticmethod
    def _mark_memory_adopted(
        ctx: RunContext,
        *,
        mode: str,
        value: Any,
    ) -> None:
        """Mark a retrieved memory as *used* only when it changes execution.

        Retriever selection alone is not a reuse event.  This fixes the previous inflation where
        a candidate could increment reuse_count even when task-provided evidence won precedence.
        """

        memory_id = ctx.blackboard.get("memory_candidate_id")
        if not memory_id or ctx.memory is None:
            return
        if ctx.blackboard.get("memory_adopted_id") == memory_id:
            return
        ctx.memory.mark_used(str(memory_id))
        ctx.metrics.record_memory(used=1)
        ctx.blackboard["memory_adopted_id"] = str(memory_id)
        ctx.blackboard["reused_memory_ids"] = [str(memory_id)]
        ctx.blackboard["memory_reuse_contribution"] = {
            "memory_id": str(memory_id),
            "mode": mode,
            "value": value,
        }

    def _compute(self, operation: str, values: list[Any]) -> Any:
        fn = _OPS.get(operation)
        if fn is None:
            return None
        try:
            return fn(values)
        except Exception:  # noqa: BLE001
            return None

    async def _execute_in_sandbox(self, operation: str, values: list[Any], ctx: RunContext) -> Any:
        """生成 Python 代码并在沙箱中执行，解析结果；失败则降级本地计算。"""
        import json

        assert ctx.sandbox is not None
        code = self._codegen(operation, values)
        result = ctx.sandbox.run(code)
        ctx.record_tool(f"sandbox:{operation}")
        if ctx.artifact_store is not None and result.stdout:
            ctx.artifact_store.put(result.stdout, summary="codeact stdout")
        if not result.ok:
            ctx.metrics.record_degradation()
            return self._compute(operation, values)
        try:
            return json.loads(result.stdout.strip()).get("value")
        except (ValueError, AttributeError):
            ctx.metrics.record_degradation()
            return self._compute(operation, values)

    @staticmethod
    def _codegen(operation: str, values: list[Any]) -> str:
        """确定性地生成执行代码（mock 模式下代表 LLM 生成的 CodeAct 代码）。"""
        return (
            "import json\n"
            f"values = {values!r}\n"
            f"op = {operation!r}\n"
            "def product(xs):\n"
            "    r = 1\n"
            "    for x in xs:\n"
            "        r *= x\n"
            "    return r\n"
            "ops = {\n"
            "    'sum': sum, 'product': product, 'max': max, 'min': min,\n"
            "    'mean': lambda xs: sum(xs) / len(xs) if xs else 0,\n"
            "    'count': len, 'concat': lambda xs: ''.join(str(x) for x in xs),\n"
            "}\n"
            "fn = ops.get(op)\n"
            "print(json.dumps({'value': fn(values) if fn else None}))\n"
        )

    def _resolve_plan(self, message: AgentMessage, ctx: RunContext) -> dict[str, Any]:
        ref = message.input_parameters.get("plan_ref") or message.state_reference
        if not ref or ctx.state_exchange is None:
            return {}
        try:
            return dict(ctx.state_exchange.get_plan_state(ref)["dag"])
        except ReferenceError_:
            ctx.metrics.record_degradation()
            return {}

    def _resolve_evidence(self, message: AgentMessage, ctx: RunContext) -> dict[str, Any]:
        ref = message.input_parameters.get("evidence_ref")
        if not ref or ctx.artifact_store is None:
            return {}
        try:
            return ctx.artifact_store.get_json(ref)
        except ReferenceError_:
            ctx.metrics.record_degradation()
            return {}


__all__ = ["CodeActExecutorAgent"]
