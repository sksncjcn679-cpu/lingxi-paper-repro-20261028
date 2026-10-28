"""Rebuild supplement reports and checksums offline. Never calls a model."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess

from analyze_pilot_supplement_v2 import analyze
from pilot_openeuler import sha
from pilot_supplement_v2 import verify_v1

ROOT = Path("/evidence/supplements")
NAMES = ["corpus_v2", "semantic_prompt_v3", "model2_reference_v3"]


def main():
    checksums_verified = verify_v1()
    summaries = {}
    for name in NAMES:
        root = ROOT / name
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            summaries[name] = analyze(root)
        (root / "logs/analysis.log").write_text(stream.getvalue())
        shutil.copy2(Path(__file__).with_name("analyze_pilot_supplement_v2.py"), root / "analysis/analyze_pilot_supplement_v2.py")
    test = subprocess.run(["/opt/lingxi/venv/bin/python", "/drivers/test_pilot_corpus_v2.py"], capture_output=True, text=True)
    (ROOT / "corpus_v2/logs/corpus_regression.log").write_text(test.stdout + test.stderr)
    if test.returncode:
        raise RuntimeError("corpus regression check failed")
    m1io = [json.loads(s) for s in (ROOT / "semantic_prompt_v3/raw/model_io.jsonl").read_text().splitlines()]
    m2io = [json.loads(s) for s in (ROOT / "model2_reference_v3/raw/model_io.jsonl").read_text().splitlines()]
    m1hashes = {r["messages_sha256"] for r in m1io}
    same_prompts = all(r["messages_sha256"] in m1hashes for r in m2io)
    if not same_prompts:
        raise RuntimeError("M2 reference did not use the same message payloads as M1")
    summary = {"scope": "supplemental pilot; not formal experiment", "original_checksums_verified": checksums_verified,
        "regression_test_exit_code": test.returncode, "M2_reference_prompts_match_M1": same_prompts,
        "batches": {name: {k: row[k] for k in ["planned_units", "terminal_units", "successes", "failures", "provider_attempts", "total_provider_tokens"]} for name, row in summaries.items()},
        "additional_provider_calls": sum(row["provider_attempts"] for row in summaries.values()),
        "additional_provider_tokens": sum(row["total_provider_tokens"] for row in summaries.values()),
        "currency_cost": "UNKNOWN", "human_review": "PENDING", "formal_experiment_ready": False,
        "disposition": "M2 is a provisional functional-check option; preserve M1 identity for protocol, no silent main-model switch"}
    (ROOT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    text = """## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run / descriptive audit
- Origin Date: 2026-10-19
- Verification Status: ANALYZED
- Version Label: pilot_supplements_v2_v3
- Scope: 公开合成试运行案例修正、提示词呈现诊断与第二模型参考检查；不是正式评测。

## 处理结果

原始失败保留。旧批次的 121 项校验和在补测前后均通过，原始数据、冻结驱动和原始报告未修改。补测在 openEuler 容器 `lingxi-openeuler-2403` 内完成，运行时源码 SHA 保持 `a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562`。没有重装系统、删除环境或修改项目密钥。

排查全部 24 个候选案例，明确找到 `pilot-migration-02/03/04/06` 四条矛盾：症状和特定检查证明需要重建，默认 `keep_binary` 回执却宣称所有 ABI/依赖正常。新生成器 `pilot_corpus_v2.py` 让迁移任务的两个备选动作读取同一份实际 ABI 证据，不改症状、标准诊断、动作候选集合和判分契约。迁移域 6 条案例的检查呈现调整，其余 18 条的业务观察保持原样。新增回归检查能检出旧版四处矛盾并验证修正版的契约和数据隔离。自动检查不替代人工语义审核。

## 事先固定的补测

| 批次 | 模型与范围 | 成功 / 计划 | 目的 |
|---|---|---|---|
| corpus_v2 | M1，6 案例 × 6 组 × 3 轮 | 82 / 108 | 修正矛盾后的迁移链补测 |
| semantic_prompt_v3 | M1，6 案例 × 6 组 × 1 轮 | 13 / 36 | 所有组采用相同业务提示词，去掉原始入站报文前缀 |
| model2_reference_v3 | M2，6 案例 × SS0 × 1 轮 | 6 / 6 | 同一业务提示词的第二模型参考检查 |

每批在调用前保存固定计划和源码、驱动、案例、协议哈希。三轮补测的记忆库按轮次、模型、组、任务链隔离；第一条任务的记忆为空。全部终态失败保留，不按成功情况更换答案或重复失败单元。两项后续诊断批次分别冻结，不能与第一批合并为单个预注册实验。

补充真实调用共 150 次，provider 返回 token 共 131,579；本次没有瞬态重试、接口错误、用量缺失或未闭合调用。金额未知，不作费用估算。三批各自的 usage、完整提示词、原始回答、执行结果、通信、状态、记忆和资源记录位于各自 `raw/`。

## 剩余失败与解释边界

第一批 26 个失败中，24 个是有解析结果但诊断/动作不符合案例契约，2 个未形成有效答案，分别涉及格式错误和不受支持的选择；详见 `corpus_v2/analysis/task_failures.json`。第二批的 23 个失败均是解析成功后的错误判断。保存的 provider 回答与运行时使用的诊断/动作一致，已核对提示词哈希、业务事实覆盖、token 对账、状态消费和 SHM 清理，没有发现链路把正确回答改成错误回答的证据。

还发现旧提示词存在呈现差异：文本组的原始入站报文包含完整业务事实，后面的任务提示又呈现一次；结构化组前缀使用压缩字段/引用，部分组没有同样的事实重复。业务事实覆盖相同并不意味着模型看到的呈现相同。第一批文本组 T0/T1 都为 18/18，但不能据此将其他组的失败直接归因为结构化传输本身，也不能把较少 token 直接称为同质量效率收益。

第二批统一采用 `llm_context_mode=none`，六个案例分别在各组中使用完全相同的消息内容（逐案例消息哈希仅 1 种），所有 IPC、状态和记忆路径仍执行。结果仍有错误，且两个相同消息哈希出现不同回答。只能确认在请求 `temperature=0` 的该接口/模型组合中观察到了回答差异，不能确认服务端实际采样设置或唯一成因。语义层把同一份 ABI 事实置于两个动作键下，也可能造成理解负担，仍需人工审阅事实与候选动作的分离方式。

M2 六次检查使用的消息哈希全部能在第二批 M1 请求中找到，没有加入标准答案或模型专用提示词。6/6 仅说明这一小组案例本次通过，不证明通用准确率，也不能凭不同样本数的总体比例比较模型。

## 当前可用范围与下一步

openEuler 环境可以继续做功能验证；可选择已有的 M2 `qwen3.7-flash`（`enable_thinking=false`）。M1 的模型身份和旧数据保留，未悄悄更换正式实验主模型。当前不能宣称案例矛盾解释了全部失败，也不能宣称诊断可靠性已修复。

正式实验尚不具备启动条件：需人工审核候选任务、明确区分事实与候选动作，并预先确定统一的模型输入呈现方式；随后重新冻结协议和预算。若采用统一业务提示词，不能再声称该设置降低了模型输入 token，IPC 字节需独立报告。若研究压缩报文本身对模型的影响，应将其单独作为呈现因素并报告质量风险。以上批次均为公开试运行，未读取正式集效果进行调优。

环境既有限制也仍存在：openEuler 用户空间运行于 WSL2 内核；原生 Linux 平台门禁失败、namespace 限制和容器内 cgroup 只读不能因模型检查成功而视为通过。

## 复现与核验

各子目录 `protocol.json`、`manifests/plan.json`、`manifests/freeze.json` 和 `drivers/` 保存冻结输入及执行驱动。`analysis/data_audit.json`、`analysis/task_failures.json` 可由 `deployment/analyze_pilot_supplement_v2.py` 从原始记录离线重建。`deployment/finalize_pilot_supplements.py` 只重新生成审计、报告和校验和，不调用模型。使用原容器的只读 `/drivers` 和 `/evidence` 挂载运行；断点恢复只跳过终态单元，不覆盖失败。

新增驱动分别为 `pilot_supplement_v2.py`、`pilot_semantic_prompt_v3.py`、`pilot_model2_reference_v3.py`，运行入口均是 `run`；所有预定单元现已终态，再执行将跳过，不会重跑这些调用。不要删除任务记录或复用旧记忆库制造“全新”试验。
"""
    # Fail closed if descriptive counts change: update the report deliberately.
    assert [(summaries[n]["successes"], summaries[n]["terminal_units"]) for n in NAMES] == [(82,108),(13,36),(6,6)]
    assert (summaries["corpus_v2"]["wrong_parsed_answers"], summaries["corpus_v2"]["failures_without_parsed_answer"]) == (24, 2)
    assert summary["additional_provider_calls"] == 150 and summary["additional_provider_tokens"] == 131579
    (ROOT / "修正与补测报告.md").write_text(text, encoding="utf-8")
    shutil.copy2(Path(__file__), ROOT / "finalize_pilot_supplements.py")
    for name in NAMES:
        root = ROOT / name
        content = "".join(f"{sha(p.read_bytes())}  {p.relative_to(root).as_posix()}\n" for p in sorted(root.rglob("*")) if p.is_file() and p.name != "checksums.sha256")
        (root / "checksums.sha256").write_text(content)
    content = "".join(f"{sha(p.read_bytes())}  {p.relative_to(ROOT).as_posix()}\n" for p in sorted(ROOT.rglob("*")) if p.is_file() and p != ROOT / "checksums.sha256")
    (ROOT / "checksums.sha256").write_text(content)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
