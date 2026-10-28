# 新 main 批次完成后的离线入口（当前仅准备）

禁止在主实验运行中执行。根任务收到实际主进程终态后，保存含 `actual_terminal: true` 与实际整型退出码0的终态收据，再显式提供 `--confirmed-main-terminal`。未提供该确认参数，controller 在接触raw前即退出。空JSON或没有退出事实的收据不算终态证明。准备阶段仅做源码/计划/语料身份和语法检查，没有读取运行中raw或运行统计。

## 固定边界

- 只取新批次 `phase=main`，2160个计划及终态一对一，120任务/20链、六臂、三轮；public6和mock/fixture不进入科学统计。保留所有业务失败。
- 按原本地尝试ID精确连接attempt starts、calls、HTTP accounting和model_io request/response；检查实际HTTP/SDK/模型响应usage核心三字段均为非负int（不接受bool）且total=prompt+completion。响应正文不进入派生文件或输出。
- 已观测安全 `x-oneapi-request-id` 在整个新raw capture中去重，缺该可选header单列NOT_PRESENT，不能据此把完整usage改成未知。
- 每task成本仅由核对后的真实call/HTTP usage求和，与原task标量比较。task异常但实际调用用量完整时，可在独立normalized记录恢复聚合并逐字段列出来源；不改raw。只有完整join证明实际零dispatch时才可记0，其他未知保持null并在统计前停。
- 调用未修改的 `formal_statistics_protocol_aligned_v3.analyze(normalized, mainplan, applicable108, seed=20261020)`。原7e201…方法、43fffd…v3、120/108/120/120/120人群、唯一10检验Holm族、质量NI放弃全部保留。
- 身份绑定复用冻结d87 `statistical_gates.load_verified_v3` 的既有规则：在独立模块命名空间只赋值两个已事前注册的任务ID摘要，原7e201…/43fffd…文件与全部统计函数不变。`identity_binding.json` 记录新plan/corpus摘要、120/108集合摘要和原注册contract来源；不声称原43fffd硬编码的是新任务集合。入口在读取原始记录前校验该绑定。
- 派生归一记录只保留分析所需原生字段和安全元数据，不包含请求、回答、异常全文。原2160质量分母不由双方成功子集替代；原方法内置的both-success只作辅助。
- 六臂 `observed_by_arm.csv` 与兼容既有长表schema的 `figure_04.csv` 提供原生观测。图04的wait fraction是逐task wait/wall，再按全部360单元取均值/分位数；缺失或wall=0时保留null。原exclusive category label保持原字面值，category值为每计划单元分配的recorded mass；缺category仅表示完整账本未分配该类计时份额，不表示该操作真实零时延。没有额外bootstrap/CI或第二检验族。

## 未来执行命令

仅在根任务确认主实验真正结束后，将下面收据路径替换为实际终态收据。该命令尚未执行。

```powershell
& 'D:/miniconda/python.exe' -X utf8 -B 'F:/codex/多agent系统架构-软件学报/paper_experiments_20261021_completion_v1/code/main_postrun_v1/run_after_main_terminal_v1.py' --study 'F:/codex/多agent系统架构-软件学报/paper_experiments_20261021_completion_v1/main_batch' --native 'F:/codex/多agent系统架构-软件学报/paper_experiments_20261021_completion_v1/code/native' --completion-receipt 'ACTUAL_TERMINAL_RECEIPT.json' --out 'F:/codex/多agent系统架构-软件学报/paper_experiments_20261021_completion_v1/main_batch/analysis/main_postrun_v1' --confirmed-main-terminal
```

同80471固定image、2 CPU/2 GiB、network-none/read-only root/no secret mounts，7a82…guard。输入路径仅新batch的raw/manifests/corpus和已复制native分析源；输出为不存在的新目录。原始hash清单在运行后动态生成，不能在准备阶段伪造。固定30分钟宿主限界；超时仅停止自己创建的容器并保留失败，不自动重跑。

实际退出收据、guard、验收、唯一v3结果和一次边界检查必须真实存在后才算离线分析完成。2160终态不是全部成功；新批次结果也不能回填历史107缺失usage、修改原主统计或取代旧独立机制实验。
