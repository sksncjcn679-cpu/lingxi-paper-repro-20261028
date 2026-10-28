# 项目部署检查与实验方案对照

日期：2026-10-19。

## 检查范围

已建立完整源码清单，覆盖源码工程 172 个文件、适配前约 30,270 行 Python，并对全部 Python 文件做语法解析；重点核对配置、模型适配、四阶段执行路径、通信与状态引用、记忆存储和评测入口。已阅读工程与演示 README、四份项目 PDF、《论文实验设计方案》以及数据说明；历史实验作为历史证据保留。此检查不等同于逐行安全审计。

提交包的工程位于 `final-submit/src`，Python 包位于 `final-submit/src/src/agent_runtime`。根目录 Git 原有跟踪路径与当前展开的 `final-submit` 目录不同；本次没有恢复旧路径、重置工作区或提交更改。

## 实际架构

| 模块 | 主要职责与已核对内容 |
|---|---|
| `runtime` | 默认 Planner → Retriever → Executor → Reviewer；阶段 checkpoint、结果验证、资源清理；独立进程是可选配置 |
| `protocol`、`registry` | Pydantic 消息契约、能力注册、文本与结构化表示、会话编码、幂等键 |
| `scheduler` | 进程内控制面及 UDS；具体后端依赖平台能力 |
| `state_exchange` | 数组表示、量化、去重、增量状态、共享缓冲区、Linux 文件描述符交接 |
| `memory` | SQLite 持久记录、NumPy 向量检索、关键词/标签、BM25、来源链和采用反馈 |
| `sandbox` | 子进程执行、超时和输出限制；Linux 上按能力启用其他约束 |
| `providers` | 显式 mock 或 OpenAI 兼容真实服务；默认 docsem 本地向量模型 |
| `evaluation` | 49 个评测模块；原有 decision-workload 为四组八案例，不能直接充当新方案六组正式矩阵 |

真实模型决策路径中，模型输出 `diagnosis` 和 `action`；执行器根据动作选择案例证据，验证器检查最终答案。本次真实调用验证了这一消费路径。

## 本次修改

1. `config.py`：增加 `VLM_API_KEY` 兼容入口、JSON 格式的模型附加参数配置；禁止附加参数覆盖 model、messages、token 上限等标准请求字段。
2. `providers/factory.py`、`openai_provider.py`：把模型附加参数经 SDK 的 `extra_body` 传到 DMXAPI；保留 SDK 重试关闭及运行时显式重试机制。本地配置设置两次重试，关闭自动扩大输出预算的 governor。
3. `sandbox/executor.py`：Windows 子进程环境白名单保留 `SystemRoot`、`WINDIR`、`TEMP`、`TMP`。实测原实现会在 Python 初始化随机数时退出，修复后基础执行、超时等回归通过。密钥不进入子进程白名单。
4. 添加对应适配回归测试、仅本机控制台、启动/停止脚本、依赖清单和独立验证副本准备器。

原始源码 SHA 已按项目自身算法核对为：

`5a30d07c58e46eb7ddbcf8d98ac3e378929318275edcbe3dd9f8f9ac3a3326cc`

部署版本源码 SHA：

`a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562`

本次适配后的结果必须使用新 SHA。模型初始四次连通验证发生在 Windows 执行器修复前，对应 SHA 另存于 `.local/smoke-summary.json`；最后一次真实验证直接记录部署版本 SHA。两者都只作为部署验证，不合并生成性能结论。

## 与论文方案的关系

| 方案要求 | 当前状态 |
|---|---|
| 环境、依赖、源码身份和能力预检 | Windows 及 openEuler 容器记录均已保存；原生 openEuler 内核验收仍待执行 |
| 主模型及第二模型可访问 | M1、M2 都取得真实响应和 provider usage |
| 新建 144 案例 / 240 检索 query、人工复核、冻结 | 正式集未实施；新增 24 个试运行候选案例，人工复核待完成 |
| 六组同任务、同角色图的正式实验 | 正式矩阵未实施；已完成容器内主模型 24 案例×六组的一轮试运行 |
| 逐 API 尝试原始 usage、消息/状态/记忆事件与互斥时间分解 | 试运行新增逐尝试原始 usage 与事件；AgentHost RPC 完整字节、互斥时延和插桩开销校准仍有缺口 |
| 跨进程消费、Linux IPC 与状态后端矩阵 | 已完成容器内 IPC 小型检查及六个跨进程真实任务；原生目标与正式规模矩阵仍待执行 |
| AutoGen / LangGraph 等价适配 | 尚未实施 |
| 正式统计、图表、盲评和结论索引 | 尚未实施，不使用旧竞赛数据代填 |

方案中的 `所有文件/src` 对应本目录实际的 `final-submit/src`。本次未运行上千任务的正式付费矩阵，没有改写协议、旧实验原始记录或历史看板。

## 验证记录

本机部署和验证的具体命令见 `LOCAL_DEPLOYMENT.md`。最终同源码验证副本共 653 项：566 项通过、87 项因系统能力或权限不可用跳过、0 项失败、0 项错误，耗时约 85 秒。跳过原因包括 fork、AF_UNIX、memfd、seccomp/Landlock、namespace 和 Windows 符号链接权限；跳过不等于这些能力已通过验证。

全量测试日志、JUnit 结果、包依赖检查、模型真实调用记录及来源哈希存放于 `.local`。原始布局测试的失败日志也保留，以记录 Windows 执行器缺陷和归档布局不一致的修复过程。

DMXAPI 参数核对依据：[豆包思考开关](https://doc.dmxapi.cn/thinking-doubao.html)、[Qwen 思考开关](https://doc.dmxapi.cn/thinking-qwen.html)、[Chat Completions](https://doc.dmxapi.cn/openai-chat.html)。实际可用性以本次真实请求及供应商用量为依据。

## Docker / WSL 故障排查补记

2026-10-19 后续排障确认：报错发生在 Hyper-V 创建虚拟机隔离分区阶段。管理员读取的 `Microsoft-Windows-Hyper-V-Worker-Admin` 日志中，事件 3110 为“无法创建分区: 系统资源不足，无法完成请求的服务。 (0x800705AA)”。这里的分区不是磁盘分区。原始 Docker 注册报错时间为北京时间 20:05:36；同类事件在后续启动对照中复现。

磁盘检查时 C 盘约剩 8.9 GiB、Docker 数据所在 F 盘约剩 61.6 GiB。Windows 页文件在 F 盘，已分配 20 GiB，当时使用约 2.7 GiB；宿主机可用物理内存约 1.9 GiB，后续对照约 2–3.3 GiB。没有磁盘满、全局提交额度耗尽或 VHD 损坏的证据。

排查进行了单项和组合启动对照，逐次记录配置、退出码与内核可见的实际 CPU/内存。最初默认配置失败；仅限制内存为 3 GB 也曾失败；仅限制为 2/4 个处理器成功。进一步测试中 16 CPU 配 1/2/3 GB 内存均启动成功；16 CPU 配 8 GB 的显式配置失败。默认配置在较晚的一次重试又成功，因此不能声称存在固定的“最多 7 个 CPU”限制，也不能把低物理内存或 CPU 数量单独确定为唯一根因。

已确认的故障层次是：**WSL 经 HCS 请求创建 Hyper-V 分区时，宿主资源分配失败；较大的 CPU/内存资源组合与当时系统状态共同影响可启动性。** 详细事件未标明具体失败的内核资源池，不能进一步断言是某个 Windows 缺陷、硬件损坏或单一内存上限。Hyper-V 启动项为 Auto，日志和成功启动均证明硬件虚拟化可用；未改动 BIOS、Windows 启动项或页文件。

同时发现 Docker Desktop 卡在旧错误界面，正常退出超时。确认没有运行中的 WSL 工作负载后，结束卡住的 Docker 进程并重新启动。没有删除镜像、容器、发行版或虚拟磁盘；`docker-desktop` 使用原来的 `F:\dockerdata\main\ext4.vhdx` 重新注册成功。

最终保存的用户级配置位于 `%USERPROFILE%\.wslconfig`：4 个处理器、3 GB 内存上限、2 GB 交换文件位于 `F:\dockerdata\wsl-resources\swap.vhdx`，图形支持恢复默认。原来不存在 `.wslconfig`，新增情况及中间配置均有记录。该配置影响本用户的所有 WSL2 发行版。

原始证据保存在 `.local/wsl-repair/`，包括 `startup-comparison.jsonl`、Docker 启停日志、管理员导出的 Hyper-V 事件及只读启动项快照。`deployment/collect_wsl_diagnostics.ps1` 是本次使用的只读日志导出脚本。

最终验收：共保存 29 次 WSL 启动对照；Docker Engine 28.5.1 返回 Linux、4 CPU、约 2.84 GiB 可见内存；`docker run --rm hello-world` 成功拉取镜像并运行，退出码 0。原有 Windows 项目服务仍返回健康状态。本次验证证明 Docker 已恢复运行，不代表项目的 Linux/openEuler 专项测试已经通过。

## 后续 openEuler 容器验收与真实试运行

随后已在官方 openEuler 24.03 LTS SP3 镜像中安装 Python 3.11.6 和项目依赖，工程部署在 Linux 持久卷。全量测试 653 项中 651 通过、1 因 namespace 权限限制跳过、1 个原生 Linux 平台门禁失败；WSL 分类未被伪装。UDS、SHM、memfd/SCM_RIGHTS、独立 Agent 进程及 seccomp/Landlock 的有限实际样例验证通过；应用 cgroup 写入因容器内只读而不可用。

真实试运行按预先保存的 179 单元计划完成：五个校准任务、主模型 24 个新候选案例×六组、第二模型四案例×六组、独立 Agent 进程六任务。共 178 成功、1 个 S1 迁移诊断错误；179 次 API 响应均返回供应商 usage，合计 162,018 tokens，无额外重试。唯一错误与所有失败日志保留，未重跑替换。

完整证据、候选语料、驱动冻结、原始记录、用量/状态/记忆检查、复现说明和限制见 `paper_experiments_20261019_pilot/`。这是容器部署与试运行证据，仍不构成原生 openEuler 或论文正式矩阵验收。启动和使用入口为 `openeuler.ps1`；网页 8765 仍属于先前 Windows 部署。
