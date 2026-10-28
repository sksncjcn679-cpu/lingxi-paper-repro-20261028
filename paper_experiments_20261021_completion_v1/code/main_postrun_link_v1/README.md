# 主采集终态后的单次离线衔接

宿主进程最多等待4小时，每10秒只检查 `logs/main_v1/execution_receipt.json`。等待期间不触碰 raw，也不接管或停止主采集及其监视进程。

只有源收据明确为 `ACTUAL_PROCESS_TERMINAL` 且 `actual_exit_code` 为整型0，才只读 `main_batch/raw/task_runs.jsonl`，确认2160个 main 计划单元全部唯一终态，保存全新的 `main_collection_terminal.json`。随后仅调用一次已准备的离线统计入口；统计容器沿用固定80471 image、2 CPU/2 GiB、无网络和只读输入。

源收据异常、数量或身份不符、已存在 witness/analysis 输出、锁定源码变化、超时，均保留失败并停止。日志目录本身是永久的独占单次锁，不删除、不自动重试。终态见证仅证明采集结束和数量，不表示业务全部成功或统计结果验收完成。

`configuration.json` 固定路径和所有入口/方法/计划摘要。实际进程信息保存在 `logs/main_postrun_link_v1/{process,started}.json`，未来真实退出结果保存在同目录 `execution_receipt.json`。不新增模型、API、benchmark或第二次主实验。
