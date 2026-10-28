# Agent Runtime OS-SOTA 1.8.6

本目录提供 Python 多智能体协作运行时。包版本为 `1.8.6a1`，发布材料以 OS-SOTA 1.8.6 标示。对应源码 SHA-256：`5a30d07c58e46eb7ddbcf8d98ac3e378929318275edcbe3dd9f8f9ac3a3326cc`。

## 目标机验证

已在 openEuler 24.03-LTS-SP3 x86_64 使用原生 UDS 验收。冻结1.7/候选1.8.6同机10轮配对结果见 `tests/verification_logs/release-ab.json`。七组消息、token、wire和RSS均下降；耗时均值均下降，但 `offline.SS`、`memory.off.extended`、`memory.on.extended` 三组95% CI跨零，不能写成显著加速。真实模型与当日完整复测见 `tests/最新实测/`。

## 安装和命令

在项目根目录执行：

```bash
python -m pip install -e ".[dev,llm]"
python scripts/rt.py --help
```

常用评测子命令见帮助输出。真实 LLM 需要通过环境变量或本机配置提供兼容 provider 凭证；不要将凭证写入仓库或日志。

## 测试

```bash
python -m pytest tests/unit tests/integration -q
```

共享内存、memfd、namespace、seccomp 和 cgroup 能否使用由目标主机内核及权限决定。namespace isolation 是显式可选项。当前材料不声称目标机 cgroup v2 可用或沙箱可抵御所有恶意代码。

## 指标边界

communication token、provider usage、完整传输 bytes、状态逻辑大小、记忆检索命中与记忆采纳是不同口径。具体样本、测试组、置信区间和限制见 `../tests/数据说明.md`。历史目录数据不得当作当前版本结果。
