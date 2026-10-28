# 新批外部系统参考可比性审计：仅准备，尚未运行

本稿只增加 `postrun_analysis/external_reference_equivalence.py` 及本说明。宿主机已进行 AST/compile 静态语法检查；未导入运行 helper，未运行 Docker、框架、SDK、模型、fixture、统计或审计，未读取新 formal raw/outcome，也未读取任何密钥。不能据本稿声明实际 PASS。

helper SHA256：`78977bf9f560c63ececa63a5f6cec4a9ee58beff6132d395a825f188dfc3a1b5`。

准备时确认原件未改：full_metric_audit.py 为 `c6a09166d2eede459b907681a02950c3ff8cc683635554b257336b130ef18158`；external_runner.py 为 `45da2d4ae69ea5a9b4e66c37b677d21e2723cc83fa4b7c82b5da4b2140fd9c60`。本 helper 不在冻结根目录的 `*.py` copy glob 中，需由 root 后续独立绑定实际运行版本。

## 固定范围与证据边界

仅检查冻结计划原有的两外部框架各 60 task × repeat 0/1，共 240 external→native-main-T0 配对；native 唯一 run ID 为 120 个，在两框架中共用。60 task 沿用冻结计划的正式链 checkpoint 1/3/5 预选规则，不重新抽样。公共 144 或其他正式 arm 不进入比较。三个原 endpoint 为 `quality_success`、`provider_input_tokens`、`wall_sec`；不计算 mean、CI、p、Holm，不新增效果量、质量阈值或研究验收条件，不读取旧 real/retrieval 结果。

旧 `deployment/formal_external_audit.py` 的方法参考为事实、固定选择集、四角色、真实框架调度、只读 check 与错误保留；旧 `formal_external_measurement_audit.py` 的方法参考为实际 SDK 请求/usage、版本、同 task/repeat 与计量边界。旧脚本依赖旧 corpus/八对契约，不能对本批直接运行。本 helper 不调用旧脚本，不重新执行确定性框架，也不把旧批结论当作新批证据。

固定锚是 `formal-freeze.json` SHA256 `2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e`。先验证生命周期三 stage terminal 0、无 live child、正式结束之后的实际 quiescence receipt 和现有两执行锁，再读冻结 plan/source 及完成的 fullaudit。输入 fullaudit receipt SHA 必须由 root 提供，默认分析器源码锚为 c6；变更锚只能在 root 审查实际变更后明确指定。所有 fullaudit evidence 原始 bytes/hash 以流式 SHA 校验，raw 不解析；两个 6504 行投影逐行覆盖验证，仅保留 360 个相关 unit 的小投影，不全量载入大型 raw。

事实、24 对契约、main 模型、只读检查、原生 T0 无 memory/state 条件、实际外部版本/四角色/调度/工具和请求 body/hash 语义，依赖已绑定 c6 fullaudit 的已保存谓词结果与固定源码。这里不将委托验证说成新测量，不从缺失字段推导工具次数为零。选中 action 后必须有已记录的只读 check；保留失败且未到工具阶段的单位会明确记为未到达，不视为零工具成本。无 provider attempt 的实际模型事实交接不可验证，不能认证语义或 provider usage。

外部固定 JSON handoff、AutoGen broadcast、LangGraph typed state、原生 lean wire/UDS、各自失败路由、初始化/清理/实际重试均作为整体系统差异披露。只能使用 `SYSTEM_LEVEL_REFERENCE`；不宣称相同 prompt serialization、单一机制因果效果、相同 scheduler overhead 或质量非劣性。

## 门禁与 UNKNOWN 权限

全局 fullaudit `FAIL_RETAINED`、实际 receipt/source/hash/identity 无法绑定或任意 FAIL finding，均在选择投影之前阻断，输出 `NON_COMPARABLE`。不能以局部零 FAIL 绕过全局失败。

fullaudit `INCONCLUSIVE_RETAINED` 原样保留。每一条 UNKNOWN 均写入 `unknown_dispositions`，含原 code、run ID、scope、涉及的三个 endpoint 与原 claim limit：

- 匹配 unit 的 usage incomplete/no-attempt 限制 token endpoint；no-attempt 同时因实际事实交接未观测而使该配对语义不可认证。
- 匹配 unit 的 wall boundary/thread ambiguity 限制 wall endpoint。
- 匹配 unit 的 resource samples、physical IPC、state/plan consumption、memory index、seed logical-write 外层 span 未观测，不是这三个 endpoint 的认证对象，仍保留其原声明限制。seed 处于整体 wall 内并不表示逻辑 seed span 已测量。
- 匹配 unit 的 source、identity、cleanup、observer、请求 body 未观测及所有未识别 UNKNOWN，保守阻断三个 endpoint。
- 没有 run ID 的全局/未定作用域 UNKNOWN，保守阻断三个 endpoint。
- 确定不在 360 个精确匹配 ID 内的单位级 UNKNOWN 不影响这三个配对 endpoint；它原先限制的其他声明不因此获准。

质量失败不要求修复或成功，不删除、不补跑；缺失 usage、耗时、工具计量保持缺失。token endpoint 需要保存的真实 provider input 与完整 core usage 谓词；wall endpoint 需要已观测 wall/provider wait/exclusive partition。缺失不补 0。可比性只说明对应保存值的受限比较权限，不代表任务成功、额外指标完备或研究总体通过。

## 下游接口

输出新目录 `analysis/external_reference_equivalence_vN/`，存在则拒绝覆盖；`audit.json` 最后写入。全局 `status` 只有 `COMPARABLE_SYSTEM_LEVEL_REFERENCE` / `NON_COMPARABLE`。前者要求完整 240 exact pairs、全部三 endpoint 可比、无阻断 finding。图 9 是否绘制由报告适配器依此决定；本 helper 不绘图。

- `matched_units.jsonl`：精确 external/native T0 run IDs、task/repeat/framework、phase/arm/model、`comparison_scope`、整体/语义/计量 status、三个 `endpoint_comparability[endpoint]={status,reason_codes}`、原始 check 是否观测/未到达失败等审计投影。不会包含模型文本、prompt、Authorization 或原始请求。
- `audit.json.endpoint_summary[framework][endpoint]`：status、planned 120、observed、non_comparable_pairs、reason_codes。共两框架×三 endpoint，全部都是记录计数，不是性能统计。
- `findings.json`：阻断原因。UNKNOWN 限制另在 `unknown_dispositions` 和对应配对 endpoint reason codes 中完整保存。
- `audit.json.bindings`：formal_freeze_sha256、实际 full_metric_audit_sha256、full_metric_analyzer_sha256、formal_plan_sha256、corpus_sha256、source/effective_contract hashes、`full_metric_output_sha256`（原 fullaudit.output_sha256 整 dict）及 `full_metric_evidence_manifest_sha256`。失败时也尽可能保留已读取 receipt 的绑定，未读取的字段不假造。
- evidence manifest 的 canonical bytes 精确为 `json.dumps(fullaudit['evidence'], sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')`，再作 SHA256。`audit.json.output_sha256` 只有 `matched_units.jsonl` 和 `findings.json` 两键。

已与 pilot_gate_review 对齐：描述统计另从独立 original-method helper 的已保存 `matched_external_summary.json` 消费。此审计不绑定未来 summary，避免互相依赖；NON_COMPARABLE 时保留实际 audit/endpoint 状态和原描述附录，不强制变绿。

## 待 root 实际关闭、完整审计完成后的命令

下面两个尖括号值必须替换为实际完成 fullaudit receipt 的 SHA256 和实际 quiescence receipt 文件名；不是允许现在执行，也不是填入预设结果。默认只运行一次，失败留原件，再经审查决定是否新建版本，不自动重跑。

```text
/opt/lingxi/formal-venv/bin/python -B /drivers/protocol_repair_v1/postrun_analysis/external_reference_equivalence.py --study-out /evidence/protocol_repair_20261020_v1 --full-audit-dir /evidence/protocol_repair_20261020_v1/analysis/full_metric_audit_v1 --full-audit-sha256 <ACTUAL_METRIC_AUDIT_JSON_SHA256> --quiescence-receipt /evidence/protocol_repair_20261020_v1/analysis/<ACTUAL_POSTRUN_QUIESCENCE_RECEIPT>.json --version external_reference_equivalence_v1
```

退出码 0 仅对应实际可比；2 对应已保留 NON_COMPARABLE。生命周期/锁前置门禁拒绝时不进入审计。source 的静态检查不验证实际 schema 或实际 endpoint 可比性，需独立审查和终止后的一次实际运行才能得知。
