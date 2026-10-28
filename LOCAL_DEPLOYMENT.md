# 灵犀栈本地部署

部署日期：2026-10-19。运行环境：Windows 10、Python 3.10.8，项目独立虚拟环境 `.venv`。

## 使用入口

打开 <http://127.0.0.1:8765/>。本地控制台已连接真实运行时，可以选择内置案例、模型和通信模式。模型选择结果由 Executor 消费，再交给确定性验证器检查。每次执行会调用付费模型接口。

在项目根目录的 PowerShell 中：

```powershell
./local.ps1 start       # 后台启动；重复执行不会再开一个服务
./local.ps1 status      # 查看服务和公开配置
./local.ps1 stop        # 停止本项目服务
./local.ps1 cli version
./local.ps1 cli --help
./local.ps1 smoke       # 一次真实豆包任务；会调用 API
./local.ps1 test        # 完整离线单元与集成测试
```

接口说明：<http://127.0.0.1:8765/docs>。旧实验看板：<http://127.0.0.1:8765/archive/>。旧看板展示 2026-10-07 归档结果，不会随本地测试更新。原有两个网页交互原型和 `lingxi_code.py` 仍属于模拟演示。

## 模型配置

根目录 `.env` 保存私有配置，密钥从用户指定的已有本地凭证文件读取，变量名 `VLM_API_KEY`；也兼容优先级更高的 `AGENT_LLM_API_KEY`。`.env`、虚拟环境、任务日志和验证副本均已加入忽略规则。不要将私有文件复制进论文复现包。

| 配置 | 当前值 |
|---|---|
| 接口 | `https://www.dmxapi.cn/v1/chat/completions` |
| 默认模型 M1 | `doubao-seed-2-0-mini-260215` |
| 第二模型 M2 | `qwen3.7-flash` |
| temperature / max_tokens | `0` / `1500` |
| 请求超时 | 60 秒 |
| 瞬时故障重试 | 首次加两次重试，最多三次尝试 |
| M1 请求附加字段 | `thinking: {type: disabled}` |
| M2 请求附加字段 | `enable_thinking: false` |
| Embedding | 项目自带 `docsem`，本地计算 |
| Windows 通信 | `inproc`，不启用独立 Agent 进程 |

控制台会按所选模型自动切换关闭思考的参数。CLI 使用 `.env` 的默认模型；改用 M2 时，同时修改 `AGENT_LLM_MODEL` 和 `AGENT_LLM_EXTRA_BODY`，参考 `.env.example`。修改配置后重启服务。历史材料的 `gpt-6-luna` 是旧实验身份，不是本次部署指定的必需模型。

现有运行时的任务接口为文本决策；本次验证没有识图任务。没有把其他项目的 JPEG 输入逻辑移入本项目。

## 安装与复现

已安装依赖，不需要再次安装。重建环境时：

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r deployment/requirements-windows.lock
./.venv/Scripts/python.exe -m pip install --no-deps -e ./final-submit/src
./local.ps1 start
```

`local.ps1 test` 会把源码、测试和所需历史 CSV 复制到 `.local/validation`，按旧测试预期还原工程布局。验证副本中的运行时代码与部署源码 SHA 相同；看板测试只改写副本，历史看板不被覆盖。归档 CSV 仅用于原有账单拟合回归测试，不进入本次真实调用结果。

## 文件与记录

| 路径 | 内容 |
|---|---|
| `deployment/local_server.py` | 真实任务控制接口，仅监听本机 |
| `deployment/index.html` | 本地验证页面 |
| `.local/runs/*.json` | 每个真实验证任务的输出、指标、provider 用量来源、请求配置和哈希 |
| `.local/smoke-summary.json` | 本次六次真实调用的汇总 |
| `.local/preflight_report.md` | 最终预检结果：566 通过、87 跳过、0 失败；六次真实任务均成功 |
| `.local/source-before.json` | 适配前完整源码文件清单与逐文件 SHA |
| `.local/source-manifest.json` | 部署源码 SHA、文件清单、环境与依赖 |
| `.local/original-source/` | 四个修改源文件的原始内容 |
| `.local/deployment-source.patch` | 原始源码与部署源码的差异 |
| `.local/logs/` | 安装、测试、能力探测和服务日志 |
| `DEPLOYMENT_REVIEW.md` | 代码结构、适配及论文实验门禁说明 |

## 运行边界

本次完成的是 Windows 本地运行与真实模型冒烟验证。首次 Docker 启动遭遇 `HCS/ERROR_NO_SYSTEM_RESOURCES`，后续已完成排障并恢复 WSL/Docker；故障定位和证据见 `DEPLOYMENT_REVIEW.md`。目前仍未完成项目在 openEuler / Linux 的部署验收。UDS、memfd/SCM_RIGHTS、namespace、cgroup 等能力必须在目标 Linux 环境另做实际验证。

控制台六种模式每次使用新记忆库，仅用于路径验证，不能评价跨任务知识复用。当前任务来自原有八案例集合，本次六个任务执行不构成《论文实验设计方案》的正式任务集或实验矩阵，也不用于效率结论。正式实验还需独立任务与检索标注集、统一原始记录器、预算冻结、跨进程和外部框架适配。

## 后续 openEuler 部署与试运行

Docker 中已另行部署 openEuler 24.03 LTS SP3，容器名 `lingxi-openeuler-2403`。源码和 Python 环境保存在 Linux 持久卷内，不使用 Windows 虚拟环境：

```powershell
./openeuler.ps1 status
./openeuler.ps1 cli --help
./openeuler.ps1 shell
./openeuler.ps1 analyze   # 仅重算结果，不调用模型
./openeuler.ps1 pilot     # 恢复当前冻结计划，已终态单元不重复调用
./openeuler.ps1 test      # 新建日志目录复测；WSL 原生平台门禁仍会失败
```

结果目录为 `paper_experiments_20261019_pilot/`。全量测试为 651 通过、1 跳过、1 个原生 Linux 平台门禁失败；真实试运行 179 个单元全部完成，178 成功、1 个诊断错误，原始失败保留。详细记录见该目录的 `preflight_report.md` 与 `README_reproduce.md`。

此部署为 openEuler 用户空间 + WSL2 内核，不等于原生 openEuler VM 验收。网页 8765 继续使用原 Windows 后端，两个环境的结果分开保存。

## 迁移案例诊断失败的修正与补测

原 179 次试运行的结果保持原样；后续排查发现 4 条迁移案例包含互相矛盾的 ABI 检查结果，另有入站报文在文本组重复呈现业务事实的对照差异。已增加版本化案例修正、回归检查和三批独立补测，详见 `paper_experiments_20261019_pilot/supplements/修正与补测报告.md`。

修正案例后 M1 迁移链六组、三轮为 82/108；统一模型业务提示词后的 M1 单轮为 13/36；同一业务提示词的 M2 参考检查为 6/6。不能把原有失败全部归因于输入矛盾，也不能将不同批次的总体比例直接作为模型或机制效果结论。M2 可作为当前小范围功能验证选项；网页已有 M2 选择入口。原主模型配置和正式协议未悄悄更换，正式实验仍等待任务人工审核、提示词方案确定及重新冻结。M2 的六次检查不证明普遍可靠。

新增真实调用共 150 次、provider token 131,579，全部记录保留。原始批次的 121 项校验和前后均通过；补测各有独立计划、冻结哈希和原始记录。查看汇总用 `./openeuler.ps1 supplements`，仅离线重建补测报告用 `./openeuler.ps1 analyze-supplements`。

## qwen3.7-plus 指定模型试运行

按用户要求另行测试 `qwen3.7-plus`，使用相同修正版迁移案例、相同六组、相同语义提示词和判分器，共 36 个固定单元，结果 **36/36 成功**，实际调用 36 次、provider token 19,890，无接口错误或重试。与此前豆包语义提示词批次逐案例、逐组比对，36 对消息哈希全部一致；此前豆包为 13/36。结果支持该任务配置下模型选择对诊断表现有明显影响，但仅涉及 6 个合成案例、1 条任务链，不能当作通用能力评测。

新结果独立保存在 `paper_experiments_20261019_pilot/model_checks/qwen37_plus_20261019/`，详细报告为 `模型试运行报告.md`。请求使用 `.cn` 接口、`enable_thinking=false`、温度 0、最大输出 1500、超时 60 秒，接口返回模型标识全部为 `qwen3.7-plus`。本轮单独标记 M3，保留旧 M1/M2 和正式协议身份。运行入口 `deployment/pilot_qwen37_plus.py`，`run` 跳过已完成单元，`finish` 仅离线重建该批报告。旧批次 121 项和此前补测 78 项校验和均再次通过。
