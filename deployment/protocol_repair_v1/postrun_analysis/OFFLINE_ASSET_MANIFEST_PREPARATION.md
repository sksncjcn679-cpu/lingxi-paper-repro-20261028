# 历史离线机制白名单准备与未来验证

已新增 `prepare_offline_asset_manifest.py`，宿主 AST/compile 静态语法检查通过。已仅运行其 `prepare` 入口，读取小型旧 inventories、checksums、selection、QA/rebuild 元数据，生成 `offline_manifest_preparation_v1/prepared_manifest.json` 与 `preparation_receipt.json`。没有读巨大 raw/hash，没有跑 Docker、SDK、模型、分析、绘图、性能测试或新正式结果。

- helper SHA256：`23acc866bdf6bf26b121b9037ea0907d16e662653b716258dcb4c5226ad890f6`。
- prepared manifest SHA256：`a17192b7d5e0215bf8d45391f918474e79e764bc6c1ef2bf75d4d8ff4841e678`。
- 状态严格为 `PREPARED_NOT_REHASHED` / `NOT_EXECUTED`，不能供报告冒充 approved manifest。
- 共8个历史批、126资产、118 raw引用（117唯一文件）；含全部30个 stress `*_batches.jsonl`。raw正文读取数0。

清单完整填入来源、原字节 SHA、bytes、topic、role、figure/table 映射与限制，不含 placeholder。18个 single/double×PDF/SVG/PNG 图导出和3个图CSV、table04来自旧 report_v3 checksums；旧 source_package_v2 inventory 与 report availability 保存的哈希共同绑定 state3 raw、quantization 及其余既有离线来源。stress30个 batch streams 特别取 original_raw_inventory 存值，修正旧 stress audit.sources 的索引遗漏；warmup、主事件、资源流也保留 hash-only 引用。

两个未在既有 metadata/checksums 登记的专用小CSV，按 root 明确授权单次流式读690/17545 bytes 取得准备 SHA，不解析数值。其 `hash_authority` 明确为 `PREPARATION_SMALL_ARTIFACT_HASH`，不宣称旧原登记hash；它们仍须 quiet 后重新验证。其余大资产没有在准备时重新 hash。

## 固定旧批与旧证明

八批为 state（附原212-row table04，显式同时依赖 quant raw）、quantization、stress、fault、protocol_v3、state_reuse、continuous100、state_phase_v3。protocol/reuse/continuous/stage 是各自独立旧批 appendix，不替代旧主图数据。故障表只选 `tables/stress_fault_recovery.csv`，同时携带其 `stress_fault_semantic_audit.json` 与专用执行元数据；绝不复制混有旧 real external 的 table06。

所有批保持旧 a040源身份 `a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562`、report_v3/source_package_v2、旧 runtime/library 版本与原测量限制。原 failed protocol-v2/state-v2/QA-v2、stage-v3首次失败分析及 `final_render_selection_v3` 的 PREPARED 状态放在 `archive_only`，不批准为当前数据。源相同不代表在新24对契约/corpus环境中测过性能，也不能从旧 fixture 成功声称新模型稳健性或真实 provider usage。

`original_qa_state` 保留旧 QA_v4 的实际 `VISUAL_LAYOUT_PASS_AUTOMATED_LIMITATIONS_RETAINED` 与 automated/axis/usage 等边界；不把旧 PREPARED selection 或 QA_v2 FAIL 变绿。stage supplement 独立保留 registered/executed analyzer SHA 与 correction receipt 的原状态：实际 recomputed_v2 PASS 与原 correction PREPARED receipt 不混为一次执行。

`existing_independent_rebuild_proof` 是旧 rebuild_formal_20261019_v2 元数据的窄投影，仅含05/06/08/table04原/重建CSV哈希、6个实际像素比较、22个既有重建资产SHA、原6个PDF SHA、4个已保存离线分析step receipt。像素相同不声称PDF byte相同；旧 rebuild 总体状态和原始输入完整性状态照录，不声称本次准备已经重建。

每批 `independent_rebuild_evidence[]` 的固定接口为 `source_root`、`relative_path`、`path`、`sha256`、`bytes`、`scope`、`applicable_scope`、`interpretation`。它们只绑定既有小型 QA/rebuild proof metadata；原文件可能含混合历史范围，不能把其旧 real/retrieval 字段用作新指标。报告重建可携带精确元数据字节或显式安全投影；数值/像素解释优先使用上述窄投影。报告资产仍只由 dedicated filename/topic 白名单准入。

## 未来 root 关闭计时后的一次验证

`verify` 独立入口先检查新版正式执行三stage均terminal0、无live child，以及 root 实际观察、晚于结束时间的 quiescence receipt，才读取白名单指定的旧离线文件。它不观察活进程、不取得容器Linux锁，因此实际 quiet 观察必须由 root 完成。不要现在运行，也不自动重试失败。

验证逐文件以1MiB块流式SHA：原离线资产、原离线raw（只读不parse）、其既有 independent rebuild input_view副本、22个既有重建导出/CSV，以及绑定的source/QA/metadata。对同一路径去重；不读取其他旧real/retrieval raw。archive-only raw不重新认证。先后的quiet/prepared元数据不允许改变。

将尖括号 receipt 名替换为 root 实际取得的路径；prepared SHA已经完整填好：

```text
python -B deployment/protocol_repair_v1/postrun_analysis/prepare_offline_asset_manifest.py verify --prepared-manifest deployment/protocol_repair_v1/postrun_analysis/offline_manifest_preparation_v1/prepared_manifest.json --prepared-sha256 a17192b7d5e0215bf8d45391f918474e79e764bc6c1ef2bf75d4d8ff4841e678 --study-out paper_experiments_20261019_pilot/protocol_repair_20261020_v1 --quiescence-receipt paper_experiments_20261019_pilot/protocol_repair_20261020_v1/analysis/<ACTUAL_QUIESCENCE_RECEIPT>.json --destination deployment/protocol_repair_v1/postrun_analysis/offline_manifest_verification_v1
```

必须新目标；原件不覆盖。验证失败保存 `verification_receipt.json` 的 `FAIL_RETAINED`，不输出approved文件。全部实际SHA/bytes相符才产生新 `approved_offline_manifest.json`：顶层 `status=APPROVED_OFFLINE_BYTE_ASSET_MANIFEST`、`verification_status=VERIFIED`、明确 scope `HISTORICAL_OFFLINE_BYTE_ASSETS_AND_OPAQUE_HASH_LINEAGE_ONLY`、实际receipt SHA、原prepared SHA和验证时间。报告适配器读取这个实际新文件，不能读 preparation receipt 代替它。

该步骤只证明历史离线资产及其既有重建证据的现存字节身份；不是新24契约的性能实验、此次独立重建、数值重分析或新的视觉QA。实际批准复核/报告复制/新的报告重建仍是后续独立动作。当前未运行 verify，所有现有源和冻结文件未修改。
