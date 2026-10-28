"""命令行入口。

子命令：
- `version`：打印版本与协议版本。
- `demo`：在 text / structured 两种模式下运行 Demo 任务并对比通信开销。
- `benchmark`：运行基准实验（P7 实现）。
- `report`：从结果目录生成报告（P7 实现）。
"""

from __future__ import annotations

import os

import argparse
import asyncio

from agent_runtime import PROTOCOL_VERSION, __version__
from agent_runtime.config import load_config
from agent_runtime.observability.logging import setup_logging


def _cmd_version(_: argparse.Namespace) -> int:
    print(f"agent-runtime {__version__} (protocol {PROTOCOL_VERSION})")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from agent_runtime.runtime.builder import run_task

    # 场景随 agent_runtime 安装，CLI 可从任意工作目录运行。
    from agent_runtime.scenarios.demo import make_demo_task

    config = load_config()
    # 三种配置：A 文本基线 / B 结构化协议 / C 结构化 + 非文本状态 + 引用。
    configs: list[tuple[str, str, bool, str | None]] = [
        ("A:lean-text", "text", False, "lean"),
        ("B:struct", "structured", False, None),
        ("C:struct+state", "structured", True, None),
    ]
    results = {}
    for label, mode, use_state, text_style in configs:
        run_config = config.with_overrides(text_style=text_style) if text_style else config
        result = asyncio.run(
            run_task(
                make_demo_task(),
                config=run_config,
                mode=mode,
                use_state_exchange=use_state,
            )
        )
        results[label] = result

    _print_comparison(results)
    return 0


def _print_comparison(results: dict) -> None:
    labels = list(results.keys())
    print("\n=== Demo: A 文本基线 / B 结构化 / C 结构化+非文本状态 ===")
    header = f"{'metric':<18}" + "".join(f"{lbl:>16}" for lbl in labels)
    print(header)

    def row(name: str, fn) -> None:
        print(f"{name:<18}" + "".join(f"{str(fn(results[lbl])):>16}" for lbl in labels))

    row("success", lambda r: r.success)
    row("quality", lambda r: r.metrics.task_quality)
    row("messages", lambda r: r.metrics.message_count)
    row("text_tokens", lambda r: r.metrics.text_tokens)
    row("text_chars", lambda r: r.metrics.text_chars)
    row("wire_bytes", lambda r: r.metrics.wire_bytes)
    row("state_transfers", lambda r: r.metrics.state_transfers)
    row("state_logical_B", lambda r: r.metrics.state_logical_bytes)
    row("state_bytes", lambda r: r.metrics.state_bytes)
    row("state_dedup_hits", lambda r: r.metrics.state_dedup_hits)
    row("artifact_refs", lambda r: r.metrics.artifact_refs)
    row("llm_calls", lambda r: r.metrics.llm_calls)

    base = results[labels[0]].metrics.text_tokens
    print("\nToken 节省率 (相对 A 文本基线):")
    for lbl in labels[1:]:
        toks = results[lbl].metrics.text_tokens
        saving = 1 - toks / base if base else 0.0
        ok = results[lbl].success
        print(f"  {lbl:<16}: {saving:6.1%}   (success={ok})")


def _cmd_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.runner import run_benchmark_cli

    return run_benchmark_cli(args)


def _cmd_codec_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.codec_benchmark import run_codec_benchmark_cli

    return run_codec_benchmark_cli(args)


def _cmd_mailbox_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.mailbox_benchmark import run_mailbox_benchmark_cli

    return run_mailbox_benchmark_cli(args)


def _cmd_reliability_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.reliability_benchmark import run_reliability_benchmark_cli

    return run_reliability_benchmark_cli(args)


def _cmd_memory_governance(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.memory_governance import run_memory_governance_cli

    return run_memory_governance_cli(args)


def _cmd_state_delta(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.state_delta_benchmark import run_state_delta_benchmark_cli

    return run_state_delta_benchmark_cli(args)


def _cmd_memory_reliability(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.memory_reliability_benchmark import (
        run_memory_reliability_benchmark_cli,
    )

    return run_memory_reliability_benchmark_cli(args)


def _cmd_state_quantization_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.state_quantization_benchmark import run_state_quantization_benchmark_cli

    return run_state_quantization_benchmark_cli(args)


def _cmd_resilience_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.resilience_benchmark import run_resilience_benchmark_cli

    return run_resilience_benchmark_cli(args)


def _cmd_idempotency_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.idempotency_benchmark import run_idempotency_benchmark_cli

    return run_idempotency_benchmark_cli(args)



def _cmd_memory_efficiency(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.memory_efficiency import run_memory_efficiency_cli

    return run_memory_efficiency_cli(args)


def _cmd_memory_accuracy(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.memory_accuracy import run_memory_accuracy_cli

    return run_memory_accuracy_cli(args)


def _cmd_stability_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.stability_benchmark import run_stability_cli

    return run_stability_cli(args)


def _cmd_contest_workload(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.contest_workload import run_contest_workload_cli

    return run_contest_workload_cli(args)


def _cmd_memory_scale(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.memory_scale_benchmark import run_memory_scale_cli

    return run_memory_scale_cli(args)


def _cmd_memory_consolidation(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.memory_consolidation import run_memory_consolidation_cli

    return run_memory_consolidation_cli(args)


def _cmd_memory_hard_set(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.memory_hard_set import run_memory_hard_set_cli

    return run_memory_hard_set_cli(args)


def _cmd_concurrency(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.concurrency_benchmark import run_concurrency_cli

    return run_concurrency_cli(args)


def _cmd_chaos(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.chaos_benchmark import run_chaos_cli

    return run_chaos_cli(args)


def _cmd_trace_export(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.trace_export import run_trace_export_cli

    return run_trace_export_cli(args)


def _cmd_session_dictionary(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.session_dictionary_benchmark import run_session_dictionary_cli

    return run_session_dictionary_cli(args)


def _cmd_latency_profile(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.latency_profile import run_latency_profile_cli

    return run_latency_profile_cli(args)


def _cmd_router(args: argparse.Namespace) -> int:
    from agent_runtime.scheduler.control_transport import _router_service_main

    return int(_router_service_main(args.socket))


def _cmd_cgroup(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.cgroup_benchmark import run_cgroup_cli

    return run_cgroup_cli(args)


def _cmd_fd_passing(args: argparse.Namespace) -> int:
    from agent_runtime.state_exchange.fd_passing import run_fd_passing_cli

    return run_fd_passing_cli(args)


def _cmd_large_state(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.state_fd_handoff_benchmark import run_state_fd_handoff_cli

    return run_state_fd_handoff_cli(args)


def _cmd_os_probe(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.os_probe import run_os_probe_cli

    return run_os_probe_cli(args)


def _cmd_competition_check(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.competition_scorecard import run_competition_scorecard_cli

    return run_competition_scorecard_cli(args)


def _cmd_competition_stress(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.competition_stress import run_competition_stress_cli

    return run_competition_stress_cli(args)


def _cmd_external_llm(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.external_llm_benchmark import run_external_llm_cli

    return run_external_llm_cli(args)


def _cmd_scale_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.scale_benchmark import run_scale_benchmark_cli

    return run_scale_benchmark_cli(args)

def _cmd_report(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.report import generate_report_cli

    return generate_report_cli(args)


def _cmd_ipc_benchmark(args: argparse.Namespace) -> int:
    from agent_runtime.evaluation.ipc_benchmark import run_ipc_benchmark_cli

    return run_ipc_benchmark_cli(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-runtime", description="多智能体协作运行时")
    parser.add_argument("--log-level", default="WARNING")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version").set_defaults(func=_cmd_version)

    p_demo = sub.add_parser("demo", help="运行 Demo 任务并对比双模式")
    p_demo.set_defaults(func=_cmd_demo)

    p_bench = sub.add_parser("benchmark", help="运行基准实验")
    p_bench.add_argument("--suite", default="main", choices=["main", "ablation", "memory", "memory_guard", "state", "reference_first", "text_fairness", "versioned_pipeline"])
    p_bench.add_argument("--repeat", type=int, default=5)
    p_bench.add_argument("--seed", type=int, default=42)
    p_bench.add_argument("--rounds", type=int, default=10)
    p_bench.add_argument(
        "--tokenizer",
        default="heuristic",
        choices=["heuristic", "auto", "tiktoken"],
        help="通信 token 计量后端；wire_bytes 始终同时记录",
    )
    p_bench.set_defaults(func=_cmd_benchmark)

    p_ipc = sub.add_parser("ipc-benchmark", help="跨进程 copy vs shared-memory 状态传递专项实验")
    p_ipc.add_argument("--sizes", default="1024,65536,1048576", help="逗号分隔的 payload 字节数")
    p_ipc.add_argument("--repeat", type=int, default=30)
    p_ipc.add_argument("--warmup", type=int, default=5)
    p_ipc.set_defaults(func=_cmd_ipc_benchmark)

    p_codec = sub.add_parser("codec-benchmark", help="compact JSON vs MessagePack 编解码专项实验")
    p_codec.add_argument("--repeat", type=int, default=2000)
    p_codec.set_defaults(func=_cmd_codec_benchmark)

    p_memgov = sub.add_parser("memory-governance", help="记忆生命周期/治理受控专项验证")
    p_memgov.set_defaults(func=_cmd_memory_governance)

    p_delta = sub.add_parser("state-delta", help="版本化增量状态 snapshot/delta 专项实验")
    p_delta.add_argument("--repeat", type=int, default=20)
    p_delta.set_defaults(func=_cmd_state_delta)

    p_memrel = sub.add_parser("memory-reliability", help="反馈校准Memory可靠度/隔离专项实验")
    p_memrel.add_argument("--rounds", type=int, default=6)
    p_memrel.set_defaults(func=_cmd_memory_reliability)


    p_rel = sub.add_parser("reliability-benchmark", help="Event Journal durability 开销专项实验")
    p_rel.add_argument("--repeat", type=int, default=20)
    p_rel.add_argument("--warmup", type=int, default=3)
    p_rel.set_defaults(func=_cmd_reliability_benchmark)

    p_mail = sub.add_parser("mailbox-benchmark", help="有界 mailbox 调度/背压专项实验")
    p_mail.add_argument("--echo-repeat", type=int, default=1000)
    p_mail.add_argument("--parallel-requests", type=int, default=200)
    p_mail.add_argument("--service-ms", type=float, default=2.0)
    p_mail.set_defaults(func=_cmd_mailbox_benchmark)

    p_idem = sub.add_parser("idempotency-benchmark", help="持久幂等完成结果回放专项实验")
    p_idem.add_argument("--repeat", type=int, default=20)
    p_idem.set_defaults(func=_cmd_idempotency_benchmark)

    p_res = sub.add_parser("resilience-benchmark", help="Retry Budget / Circuit Breaker 受控专项实验")
    p_res.set_defaults(func=_cmd_resilience_benchmark)

    p_quant = sub.add_parser("state-quantization", help="float32/float16/int8 状态传输精度保真专项")
    p_quant.add_argument("--samples", type=int, default=200)
    p_quant.add_argument("--dim", type=int, default=256)
    p_quant.set_defaults(func=_cmd_state_quantization_benchmark)

    p_memeff = sub.add_parser("memory-efficiency", help="同成功率、仅切Memory的重复计算隔离实验")
    p_memeff.add_argument("--repeat", type=int, default=10)
    p_memeff.set_defaults(func=_cmd_memory_efficiency)

    p_memacc = sub.add_parser("memory-accuracy", help="赛题向Memory关键词/标签/语义检索准确率专项")
    p_memacc.set_defaults(func=_cmd_memory_accuracy)

    p_stable = sub.add_parser("stability-benchmark", help="正式生成>=10轮连续任务稳定性证据")
    p_stable.add_argument("--rounds", type=int, default=10)
    p_stable.set_defaults(func=_cmd_stability_benchmark)

    p_contest = sub.add_parser("contest-workload", help="运行Linux/openEuler两组连续任务的赛题主证据")
    p_contest.set_defaults(func=_cmd_contest_workload)
    p_fd = sub.add_parser("fd-passing-benchmark", help="memfd 密封 + SCM_RIGHTS 零拷贝状态交接 vs socket 拷贝")
    p_fd.add_argument("--repeat", type=int, default=30)
    p_fd.set_defaults(func=_cmd_fd_passing)
    p_large = sub.add_parser("large-state-workload", help="运行时主链路大状态 copy/fd/inproc 对照")
    p_large.add_argument("--repeat", type=int, default=5)
    p_large.add_argument("--dim", type=int, default=300_000)
    p_large.set_defaults(func=_cmd_large_state)
    sub.add_parser("os-probe", help="实测内核能力（memfd 密封映射模式/SCM_RIGHTS/pidfd/cgroup）并记录后端选择").set_defaults(func=_cmd_os_probe)
    sub.add_parser("memory-scale", help="共享记忆 100/1k/5k 检索准确率与时延（排序等价校验）").set_defaults(func=_cmd_memory_scale)
    sub.add_parser("memory-consolidation", help="分层记忆巩固与冲突消解专项实验").set_defaults(func=_cmd_memory_consolidation)
    sub.add_parser("memory-hard-set", help="同义改写记忆检索困难集").set_defaults(func=_cmd_memory_hard_set)
    p_conc = sub.add_parser("concurrency-benchmark", help="并发多任务吞吐与尾延迟"); p_conc.add_argument("--repeat", type=int, default=1); p_conc.set_defaults(func=_cmd_concurrency)
    sub.add_parser("chaos-benchmark", help="router/状态/沙箱/工件故障注入与显式恢复结果").set_defaults(func=_cmd_chaos)
    p_trace = sub.add_parser("trace-export", help="把事件日志导出为 Chrome Trace JSON")
    p_trace.add_argument("--task", required=True)
    p_trace.add_argument("--journal-root", default=".agent_data/events")
    p_trace.add_argument("--output-root", default="results")
    p_trace.set_defaults(func=_cmd_trace_export)
    p_dict = sub.add_parser("session-dictionary", help="接收方作用域会话值字典专项消融")
    p_dict.add_argument("--repeat", type=int, default=3)
    p_dict.set_defaults(func=_cmd_session_dictionary)
    p_lat = sub.add_parser("latency-profile", help="Structured+State CPU/端到端时延剖析")
    p_lat.add_argument("--repeat", type=int, default=10)
    p_lat.set_defaults(func=_cmd_latency_profile)
    p_router = sub.add_parser("router", help="监听一个独立的 AF_UNIX 控制面 socket")
    p_router.add_argument("--socket", required=True)
    p_router.set_defaults(func=_cmd_router)
    sub.add_parser("cgroup-benchmark", help="cgroup v2 控制器真实可用性与权限验证").set_defaults(func=_cmd_cgroup)

    p_comp = sub.add_parser("competition-check", help="按赛题原文逐条生成可机验收评分卡")
    p_comp.add_argument("--rounds", type=int, default=10)
    p_comp.add_argument("--require-openeuler", action="store_true", help="非目标openEuler主机时返回非零")
    p_comp.set_defaults(func=_cmd_competition_check)

    p_stress = sub.add_parser("competition-stress", help="运行>=10轮OS任务×三通信模式分布式证据")
    p_stress.add_argument("--rounds", type=int, default=10)
    p_stress.add_argument("--seed", type=int, default=7000)
    p_stress.set_defaults(func=_cmd_competition_stress)

    p_scale = sub.add_parser("scale-benchmark", help="Small/Medium/Large × repeated runs + 95%% CI")
    p_scale.add_argument("--repeat", type=int, default=10)
    p_scale.set_defaults(func=_cmd_scale_benchmark)

    p_abl = sub.add_parser("fable7-ablation", help="fable7 各新机制逐一关闭的同机交错配对消融")
    p_abl.add_argument("--repeat", type=int, default=8)
    p_abl.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.fable7_ablation", fromlist=["x"]).run_fable7_ablation_cli(a))
    p_abl8 = sub.add_parser("mechanism-ablation", help="fable7+fable8 全部新机制逐一关闭的同机交错配对消融（同 fable7-ablation）")
    p_abl8.add_argument("--repeat", type=int, default=8)
    p_abl8.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.fable7_ablation", fromlist=["x"]).run_fable7_ablation_cli(a))

    p_bi = sub.add_parser("bilingual-memory", help="中文/英文/混合查询检索英文共享记忆（CJK 词法嵌入前后对照）")
    p_bi.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.bilingual_memory_benchmark", fromlist=["x"]).run_bilingual_memory_cli(a))

    p_dc = sub.add_parser("decision-certified-state", help="fable9：接收方决策认证的非文本状态精度阶梯（int4→int8→fp16→fp32）")
    p_dc.add_argument("--plans", type=int, default=300)
    p_dc.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.decision_certified_benchmark", fromlist=["x"]).run_decision_certified_cli(a))

    p_cr = sub.add_parser("criteria-report", help="按赛题五项评分细则汇总当前源码结果（含合规性说明）")
    p_cr.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.criteria_report", fromlist=["x"]).run_criteria_report_cli(a))

    p_iso = sub.add_parser("agent-isolation", help="fable10：每个 Agent 独立 OS 进程的等价性、故障隔离与开销")
    p_iso.add_argument("--repeat", type=int, default=3)
    p_iso.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.agent_isolation_benchmark", fromlist=["x"]).run_agent_isolation_cli(a))

    p_lh = sub.add_parser("long-horizon", help="fable11：多轮长程协作（累积上下文）通信与物理传输随轮数的增长")
    p_lh.add_argument("--rounds", default="1,2,4,8,16,32")
    p_lh.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.long_horizon_benchmark", fromlist=["x"]).run_long_horizon_cli(a))

    p_bm = sub.add_parser("billing-model", help="sota1：真实LLM账单分解（每请求固定开销 vs 运行时可控token）")
    p_bm.add_argument("--archive", default=None, help="cells.csv（默认 evidence/real_llm_rerun_20260924/cells.csv）")
    p_bm.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.billing_model", fromlist=["x"]).run_billing_model_cli(a))
    p_ls = sub.add_parser("long-session", help="OS-SOTA1.1：长协作会话（多轮×4任务）真实账单 + 大批次组提交（漏项修复/AIMD）")
    p_ls.add_argument("--rounds", type=int, default=3)
    p_ls.add_argument("--repeat", type=int, default=1)
    p_ls.add_argument("--arms", default="", help="子集，例如 T,SS+G")
    p_ls.add_argument("--allow-mock", action="store_true")
    p_ls.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.long_session_benchmark", fromlist=["x"]).run_long_session_cli(a))
    p_gate = sub.add_parser("sota2-gate", help="OS-SOTA1.5：按结果目录自动判定 OS-SOTA2 发布门槛（不运行实验）")
    p_gate.add_argument("--results", default="results")
    p_gate.add_argument("--windows-log", default=None, help="Windows 上 run_all_suites.sh 的日志")
    p_gate.add_argument("--strict", action="store_true", help="任一门槛未 PASS 时返回 1")
    p_gate.add_argument("--expect-sha", default=None, help="证据必须来自的源码 SHA（默认：当前源码树）")
    p_gate.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.sota2_gate", fromlist=["x"]).run_sota2_gate_cli(a))
    p_dw = sub.add_parser("decision-workload", help="Consumed synchronous model decisions: protocol x same-memory factorial")
    p_dw.add_argument("--repeat", type=int, default=5)
    p_dw.add_argument("--real", action="store_true")
    p_dw.add_argument("--agent-processes", action="store_true")
    p_dw.add_argument("--checkpoint")
    p_dw.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.decision_workload", fromlist=["x"]).run_cli(a))
    p_re = sub.add_parser("retrieval-expanded", help="Frozen 30-query corpus; docsem and BM25 reranking")
    p_re.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.retrieval_expanded", fromlist=["x"]).run_cli(a))
    p_cw = sub.add_parser("candidate-workflow", help="Planner -> Retriever cross-process matrix consumption")
    p_cw.add_argument("--repeat", type=int, default=5)
    p_cw.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.candidate_workload", fromlist=["x"]).run_cli(a))
    p_co = sub.add_parser("coalesce-benchmark", help="sota1：LLM调用合并（向量化补全）受控实验（账单投影+等价性）")
    p_co.add_argument("--repeat", type=int, default=3)
    p_co.add_argument("--rounds", type=int, default=10)
    p_co.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.coalesce_benchmark", fromlist=["x"]).run_coalesce_benchmark_cli(a))

    p_sec = sub.add_parser("sandbox-security", help="fable12：内核级 CodeAct 沙箱（seccomp-BPF + Landlock）攻击遏制、功能与开销")
    p_sec.add_argument("--repeat", type=int, default=40)
    p_sec.set_defaults(func=lambda a: __import__("agent_runtime.evaluation.sandbox_security_benchmark", fromlist=["x"]).run_sandbox_security_cli(a))

    p_ext = sub.add_parser("external-llm", help="使用真实OpenAI兼容LLM运行补充外部有效性实验")
    p_ext.add_argument("--repeat", type=int, default=1)
    p_ext.add_argument("--judge", action="store_true", help="OS-SOTA1.1：每个任务加一次语义裁判（仅读入站报文），报告各变体的判断准确率")
    p_ext.add_argument("--allow-mock", action="store_true", help="仅用于验证实验脚手架；产物不得标为真实LLM证据")
    p_ext.add_argument("--variants", default="", help="逗号分隔的变体子集，例如 T,SS,FULL（默认全部 8 个）")
    p_ext.add_argument("--checkpoint", default=None, help="JSONL 断点文件：每完成一格立即落盘，中断后用同一路径续跑")
    p_ext.add_argument("--budget-min", type=float, default=None, help="总墙钟预算（分钟）；到时输出标注 INCOMPLETE 的部分报告")
    p_ext.add_argument("--order", choices=["balanced", "fixed"], default="balanced", help="balanced=每轮轮转变体顺序以抵消提供方漂移")
    p_ext.set_defaults(func=_cmd_external_llm)

    p_report = sub.add_parser("report", help="从结果目录生成报告")
    p_report.add_argument("--results", required=True)
    p_report.set_defaults(func=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(level=getattr(args, "log_level", "WARNING"), fmt="text")
    return _run_guarded(args)


def _run_guarded(args) -> int:
    """OS-SOTA1: on non-Linux hosts, a suite that needs Linux kernel features (AF_UNIX,
    memfd, seccomp/Landlock, fork, cgroup v2) reports UNSUPPORTED instead of crashing.

    On Linux every exception still propagates unchanged: a crash there is a real bug.
    """
    import sys as _sys

    # AGENT_PLATFORM_OVERRIDE lets CI rehearse the non-Linux path on a Linux box.
    platform_name = os.environ.get("AGENT_PLATFORM_OVERRIDE", _sys.platform)
    if platform_name.startswith("linux") or os.environ.get("AGENT_STRICT_PLATFORM") == "1":
        return args.func(args)
    try:
        return args.func(args)
    except (RuntimeError, AttributeError, OSError, NotImplementedError, ImportError) as exc:
        return _report_unsupported(args, exc)


def _report_unsupported(args, exc: BaseException) -> int:
    import json as _json
    import platform as _platform
    from datetime import datetime, timezone

    from agent_runtime.evaluation.runner import _allocate_result_dir

    suite = str(getattr(args, "command", None) or "unknown").replace("-", "_")
    out = _allocate_result_dir("results", f"{suite}_unsupported")
    payload = {
        "suite": suite,
        "status": "unsupported_platform",
        "platform": _platform.platform(),
        "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "requires Linux/openEuler kernel features; not evidence, not a failure of the mechanism",
    }
    (out / "summary.json").write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"UNSUPPORTED on {payload['platform']}: {payload['reason']}\n结果已保存到: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
