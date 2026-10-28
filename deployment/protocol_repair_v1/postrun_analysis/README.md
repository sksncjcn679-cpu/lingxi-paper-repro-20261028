正式采集结束、provider/性能计时均停止后执行：

```text
python /drivers/protocol_repair_v1/postrun_analysis/secondary_heterogeneity.py --version secondary_heterogeneity_v1
```

输出新目录 `/evidence/protocol_repair_20261020_v1/analysis/postrun/secondary_heterogeneity_v1/`：

- `secondary_units.jsonl`：全部6360计划单元；含失败/缺失和每项测量的适用性。
- `measurement_events.jsonl`：按URI保留的实际状态事件和原生索引事件；不读模型消息。
- `heterogeneity.jsonl`：全部冻结维度的边际分布、任务均值和域内整链bootstrap区间。
- `paired_heterogeneity.jsonl`：原五对的分层探索性差值及质量保守界；不输出p值、不做Holm。
- `coverage.json`、`receipt.json`：空/稀疏层、计划分母、输入身份和测量边界。

不同phase/model/topology分开；S0→SS0仅在108适用面计算。输入长度直接使用冻结分箱。14项跨阶段匹配效应仍由原统计入口负责。

未观测成本为null，实际记录的失败成本保留。无新映射不补0ns；融合读/算不拆分；索引不加到已含索引的外层时段；共享释放按组去重。无p值或额外确认性结论。仅在采集已停止但保留不完整矩阵报告时使用 `--allow-incomplete`；未闭合starts仍拒绝分析。

准备阶段只完成静态检查。正式结束后仍需实际验证：新raw字段兼容性；一个域仅一链时不出CI；未知usage不补零；共享release重复URI只计一次；恒定质量使用非退化保守界；所有输出不含p_raw/p_holm/新增检验族。复用既有统计不变量，勿重跑旧脚本的历史OUT写入入口。
