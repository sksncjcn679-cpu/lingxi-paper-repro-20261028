"""Predeclared qwen3.7-plus comparison using the frozen v3 semantic task inputs."""
import argparse
from collections import Counter
import contextlib
import io
import json
from pathlib import Path
import shutil
import uuid

import pilot_supplement_v2 as supplement
from pilot_corpus_v2 import VERSION, corpus, audit
from analyze_pilot_supplement_v2 import analyze

driver = supplement.driver
MODEL_KEY = "M3"
MODEL = "qwen3.7-plus"
OUT = Path("/evidence/model_checks/qwen37_plus_20261019")
FILES = supplement.FILES + ["pilot_qwen37_plus.py", "analyze_pilot_supplement_v2.py"]


def verify_history():
    verified = {"original": supplement.verify_v1()}
    root = Path("/evidence/supplements")
    count = 0
    for line in (root / "checksums.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert driver.sha((root / name).read_bytes()) == digest, name
        count += 1
    verified["previous_supplement"] = count
    return verified


def prepare():
    verified = verify_history()
    OUT.mkdir(parents=True, exist_ok=False)
    for folder in ["raw", "corpus", "manifests", "analysis", "logs", "drivers"]:
        (OUT / folder).mkdir()
    cases = corpus()
    assert not audit(cases)
    (OUT / "corpus/tasks.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in cases))
    plan = []
    for index, case in enumerate(r for r in cases if r["domain"] == "migration"):
        arms = list(driver.ARMS)
        offset = index % len(arms)
        for arm in arms[offset:] + arms[:offset]:
            unit = {"phase": "qwen37_plus_v3_repeat_01", "repeat": 1, "model": MODEL_KEY, "arm": arm,
                    "task_id": case["task_id"], "processes": False, "corpus_version": VERSION,
                    "prompt_version": "semantic_task_only_v3"}
            unit["run_id"] = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(unit, sort_keys=True)).hex
            plan.append(unit)
    assert len(plan) == 36
    supplement.dump(OUT / "manifests/plan.json", plan)
    supplement.dump(OUT / "manifests/history_verification.json", verified)
    supplement.dump(OUT / "protocol.json", {
        "purpose": "user-requested qwen3.7-plus pilot comparison; not a formal experiment",
        "planned_units": 36, "unique_cases": 6, "rounds": 1, "groups": driver.ARMS,
        "corpus_version": VERSION, "llm_context_mode": "none", "model_key": MODEL_KEY, "model": MODEL,
        "endpoint": "https://www.dmxapi.cn/v1/chat/completions", "temperature": 0, "max_tokens": 1500,
        "extra_body": {"enable_thinking": False}, "timeout_sec": 60, "max_attempts_per_unit": 3,
        "attempt_ceiling": 120, "observed_token_stop_before_next_request": 200000,
        "memory": "new isolated store per phase/model/arm/chain; checkpoint 1 starts empty",
        "failure_policy": "retain every result; skip terminal units; never replace wrong model answers",
        "selection": "all six corrected migration cases and all six groups, fixed before calls",
        "comparison": "same messages, order, validator and runtime as M1 semantic_prompt_v3; compare hashes per case and arm",
        "interpretation": "single synthetic chain; no population accuracy, provider internals or general capability claim",
        "formal_experiment": False, "human_review": "PENDING", "currency_cost": "UNKNOWN"})
    for name in FILES:
        shutil.copy2(Path(__file__).with_name(name), OUT / "drivers" / name)
    paths = ["corpus/tasks.jsonl", "manifests/plan.json", "protocol.json"]
    supplement.dump(OUT / "manifests/freeze.json", {
        "source_sha256": driver._source_tree_sha256(driver.ROOT),
        "files": {p: driver.sha((OUT / p).read_bytes()) for p in paths},
        "drivers": {name: driver.sha(Path(__file__).with_name(name).read_bytes()) for name in FILES},
        "frozen_at_utc": driver.now()})
    print(json.dumps({"prepared_units": len(plan), "model": MODEL, "history_verified": verified}), flush=True)


def run():
    verify_history()
    supplement.OUT = OUT
    driver.MODELS[MODEL_KEY] = MODEL
    original_configuration = driver.configuration
    def configuration(model, processes=False):
        return original_configuration(model, processes).with_overrides(llm_context_mode="none")
    driver.configuration = configuration
    original_execute = driver.execute
    def execute(unit, case, memory):
        result = original_execute(unit, case, memory)
        calls = driver.rows("provider_calls.jsonl")
        # A permanent endpoint/configuration error is not a model-quality trial.
        if calls and calls[-1].get("status") == "ERROR" and calls[-1].get("error_type") in {
            "AuthenticationError", "PermissionDeniedError", "NotFoundError", "BadRequestError"}:
            raise RuntimeError("permanent provider error retained; remaining units INCOMPLETE, no fallback model")
        return result
    driver.execute = execute
    supplement.run()
    print(json.dumps({"history_verified_after_run": verify_history()}), flush=True)


def finish():
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        result = analyze(OUT)
    (OUT / "logs/analysis.log").write_text(stream.getvalue())
    load = lambda p: [json.loads(s) for s in p.read_text().splitlines()]
    own_io = load(OUT / "raw/model_io.jsonl")
    own_plan = json.loads((OUT / "manifests/plan.json").read_text())
    own_keys = {u["run_id"]: (u["task_id"], u["arm"]) for u in own_plan}
    old_root = Path("/evidence/supplements/semantic_prompt_v3")
    old_plan = json.loads((old_root / "manifests/plan.json").read_text())
    old_keys = {u["run_id"]: (u["task_id"], u["arm"]) for u in old_plan}
    old_prompts = {old_keys[r["run_id"]]: r["messages_sha256"] for r in load(old_root / "raw/model_io.jsonl")}
    mismatches = [r["run_id"] for r in own_io if r["messages_sha256"] != old_prompts[own_keys[r["run_id"]]]]
    calls = load(OUT / "raw/provider_calls.jsonl")
    comparison = {"model": MODEL, "successes": result["successes"], "planned_units": 36,
        "terminal_units": result["terminal_units"], "unique_cases": 6,
        "same_prompt_pairs_verified": len(own_io)-len(mismatches), "prompt_pair_mismatches": mismatches,
        "provider_reported_model_ids": dict(Counter(r.get("returned_model") for r in calls)),
        "finish_reasons": dict(Counter(r.get("finish_reason") for r in calls)),
        "provider_attempts": result["provider_attempts"], "provider_tokens": result["total_provider_tokens"],
        "prior_M1_successes": 13, "prior_M1_units": 36, "prior_M2_reference_successes": 6, "prior_M2_reference_units": 6,
        "history_verified": verify_history(), "currency_cost": "UNKNOWN", "formal_experiment": False}
    supplement.dump(OUT / "analysis/model_comparison.json", comparison)
    table = "\n".join(f"| {r['arm']} | {r['successes']} / {r['tasks']} |" for r in result["by_arm"])
    report = f"""## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run / descriptive comparison
- Origin Date: 2026-10-19
- Verification Status: ANALYZED
- Version Label: qwen37_plus_semantic_v3
- Scope: 用户指定模型的迁移案例试运行；非正式论文评测。

## 结果

`{MODEL}` 完成 {result['terminal_units']}/36 个预定单元，成功 {result['successes']}，失败 {result['failures']}。实际请求 {result['provider_attempts']} 次，provider 返回 token {result['total_provider_tokens']}；金额未知。模型返回标识为 `{dict(Counter(r.get('returned_model') for r in calls))}`，仅为接口返回信息，未验证服务端实际部署身份。

| 组别 | 成功 / 完成 |
|---|---|
{table}

## 比较条件

沿用修正版 v2 的 6 个迁移案例、6 个组别、各一轮，运行于同一 openEuler 容器。与此前 M1 的 36 个语义提示词单元逐案例、逐组核对：{len(own_io)-len(mismatches)} 对消息哈希相同，{len(mismatches)} 对不同。`temperature=0`、`max_tokens=1500`、超时 60 秒、最多三次尝试；Qwen 关闭 thinking。语义观察、判分器和部署源码保持不变。没有把标准答案写入提示词，没有用规则回填错误答案，没有重跑终态失败。

此前同设置 M1 为 13/36，Qwen Flash 参考为 6/6（SS0 单组），样本量不同，不直接比较总体比例。所有数字均来自公开合成迁移任务，36 次执行仅涉及 6 个独立案例、1 条任务链；不能视作 36 个独立故障样本。

## 如何理解模型能力

在相同业务提示词的本组样例上可以比较实际输出表现。若 Plus 优于豆包，支持“豆包在该模型配置、提示词与案例组合下诊断表现较弱”，但不能证明模型固有能力不足是此前失败的唯一原因。旧版案例曾有矛盾，入站报文的呈现方式也有差异；修正版仍待人工审核。接口响应正常与诊断正确是两个独立检查项。不能推断为通用准确率，也不能把单轮差异当作正式统计结论。

## 证据与复现

逐次请求配置和 usage：`raw/provider_calls.jsonl`；完整提示词与回答：`raw/model_io.jsonl`；执行与判分：`raw/task_runs.jsonl`。原始失败见 `analysis/task_failures.json`，逐项核对见 `analysis/data_audit.json`，模型和提示词对照见 `analysis/model_comparison.json`。`manifests/freeze.json` 和 `drivers/` 保存调用前冻结的输入与脚本。旧批次 121 项、此前补测 78 项校验和再次通过，历史数据未改写。

入口 `deployment/pilot_qwen37_plus.py` 的 `run` 跳过已终态单元；`finish` 只重建本批分析和校验和。模型在本批中单独标记 M3，未覆盖旧 M1/M2 的名称或原始记录，未改变正式实验主模型设置。
"""
    (OUT / "模型试运行报告.md").write_text(report, encoding="utf-8")
    content = "".join(f"{driver.sha(p.read_bytes())}  {p.relative_to(OUT).as_posix()}\n" for p in sorted(OUT.rglob("*")) if p.is_file() and p.name != "checksums.sha256")
    (OUT / "checksums.sha256").write_text(content)
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "run", "finish"])
    args = parser.parse_args()
    {"prepare": prepare, "run": run, "finish": finish}[args.action]()
