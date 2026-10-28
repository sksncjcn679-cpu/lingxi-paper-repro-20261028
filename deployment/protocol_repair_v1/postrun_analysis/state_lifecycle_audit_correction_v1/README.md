这是分析器纠错提案，尚未应用主审核器或下游绑定。

`full_metric_audit.py.preimage` 保留原 `0d9a5645…` 文件的精确字节。当前主文件及 `PURE_REUSED` 中的九个定义（包括 legacy `audit_state`）均未改变。新函数名为 `audit_state_lifecycles`；它是新的代次联接实现，不能称作原样复用旧 `audit_state`。

新入口以单个 unit 的内存记录为输入，无原始文件、模型、runtime 或全量审核入口。规则如下：

- 成功的真实非去重 producer 必须有本次实际物化的计划组件；完整 producer 结束后才可消费。
- 组件按 `buffer_key` 独立跟踪实际物化、释放和再次物化。消费期间任何必需组件被释放或替换均失败。不存在的可选 `#scale` 删除不能关闭仍存活的 `#emb/#vec`。
- `deduplicated=true/materialized_bytes=0` 只是既有代次的别名，必须回连调用前已完成、同 URI／完整 hash、整调用期间存活的真实生成。首次缺失、错误 hash、释放后去重均失败。
- 同 URI 再物化会开新代次，每个物理组件仍须正确释放；新代次不会替旧代次掩盖早消费或释放后消费。
- 消费携带 hash 或存在先前成功 `state_resolve.reference` 时，核对该完整 hash。冻结的 compute 记录本身只有 URI：无 resolve 时只能利用当时唯一的已观察 producer hash，不能宣称重新计算了消费者 payload hash；同 URI 已出现多个 hash 时不得盲猜。
- 成功／失败单位的“计划是否被消费”仍按原 URI 级 FAIL／UNKNOWN 口径；不把已释放的未使用物理中间代次另造为任务质量失败。

原边界检查、producer错误排除、URI/hash格式、mapping数量／状态、物化组件清理、共享释放不按URI分摊时长、fused标志及三段时长守恒、RPC帧字节／worker PID／总IPC约束和原四项计数语义继续保留。新增的是时间及物理代次联接，无任何 phase 豁免。

已实际执行的仅是纯数据验证：

```text
python -B validate_fixtures.py --version fixture_validation_v1
```

结果在 `fixture_validation_v1/`：9个方法、23个内存案例 PASS；包括合法后续dedup、首次前消费、释放后消费、孤立dedup、错误hash、合法重物化及空档、未释放覆盖、多组件释放、可选scale no-op、共享组、普通路径，以及原mapping／fused／RPC失败规则。两个正例同时证明旧全对全联接会误报 `CONSUME_PRECEDES_PRODUCE`。

`integration_proposal.diff` 只是供root评审的显式import＋调用点变更，未应用。后续若批准，须新增版本回执，明确 active state entry 已变，并保留旧 v2 FAIL；该fixture PASS不表示全量审核已通过。本提案没有扫描任何真实raw、重新解释实验成功数、改统计方法或运行实验。
