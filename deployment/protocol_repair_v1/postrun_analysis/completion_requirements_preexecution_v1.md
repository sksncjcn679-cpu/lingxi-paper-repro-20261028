# 原方案完成要求：新批证据绑定清单 v1（执行前，只读梳理）

日期：2026-10-20。状态：**PREEXECUTION / NOT A COMPLETION CERTIFICATE**。

唯一实验设计权威是工作区根目录《论文实验设计方案.md》（2026-10-19版，下文“原文”）。本清单只整理完成证据，不增加任务、实验条件、检验、阈值或新门禁，不修改冻结源码、计划、统计方法和排除规则。唯一获批协议例外是以自动验证替代人工审核/评分，并记录变更；其范围见下文。图表数量与样本数来自方案，不能称为《软件学报》强制规定（原文245行）。

本次只读取原方案、现有要求矩阵，以及 `deployment/protocol_repair_v1/postrun_analysis/` 的说明和源码；未运行 Docker、测试、import、helper、模型、网络、分析、绘图、压力或重建，未读取 raw/outcomes，未计算大文件哈希。本文不能独立证明任何新批采集、审计或分析已完成。协调者提供的进度是 main2160已终、cross720已跑完、multiprocess开始；该进度不替代本清单所列的最终实际证据。

## 既有清单与本批身份

保留既有清单名称、编号及历史状态，不复制或重编号：

- `paper_experiments_20261019_pilot/formal_20261019/analysis/protocol_requirement_evidence_matrix_precompletion.csv`：**155行**，列为 `id / protocol_section / requirement / status / authoritative_evidence / review_basis / remaining_gate_or_limit`。
- 同目录 `protocol_requirement_evidence_matrix_precompletion.md`：原批完成前说明。
- 155行是原方案要求索引；部分行是历史实现/审计补充，例如T011，不能因此升格为原方案新条件。另一个“115 semantic indicators”口径是指标审计声明，**不是**要求清单行数。本清单不签发“115指标全通过”。

旧矩阵的 `OUT/`、旧PASS/准备状态、旧预抽30声明、旧real/retrieval路径和旧统计v3身份均不自动迁移到新批。逐项完成时应在结项证据索引中沿用原编号，链接本批实际证据或明确标注独立历史离线批；无须新造一套155项审计器。

本批宿主根为 `paper_experiments_20261019_pilot/protocol_repair_20261020_v1`，容器规范根为 `/evidence/protocol_repair_20261020_v1`。下文 `NEW/` 指此根，`POST/` 指 `deployment/protocol_repair_v1/postrun_analysis/`。本次未在 `deployment/protocol_repair_v1/INTEGRATION.md` 定位到文件；使用实际存在的 `POST/INTEGRATION.md`，不是将不存在的路径当证据。

按 `POST/INTEGRATION.md:9–24`，声明的冻结身份是：

- `formal-freeze.json` SHA256：`2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e`。
- 唯一主统计入口为冻结 `statistical_gates.py formal --version formal_primary_v1`，源码SHA256：`d87bb161630560f4fe7c62e061a167e36ba5d6c93a9c7a5dbd2e4d12c5240251`（简称d87）；只消费其实际保存结果，不调用旧报告的 `statistics()`。
- 次级描述边界声明SHA256：`f1e190ab50be5cc1b616b692bebb3f3e5b9a43b851f5554aab158e0da15a5cd4`；冻结检索驱动声明SHA256：`ec5fef7c508232d7c34671b8150c6a829ca26713eb5e2df92969a4b658c14921`。

以上为已读说明中的身份锚，本次未对冻结物重算哈希。后续实际执行须绑定当时受审版本和输入/输出身份；不能把准备说明中的哈希文字当成现存字节已验证。

## 已设计的入口（全部是未来证据的生成路线）

| 简称 | 已设计入口与预期保存物 | 当前证据地位 |
|---|---|---|
| A | `POST/full_metric_audit.py` → `NEW/analysis/full_metric_audit_vN/{metric_audit.json,findings.json,unit_checks.jsonl,audited_observations.jsonl}` | 静态准备；新批完整实际审计及字段兼容性尚未由本次执行证明。详见 `FULL_METRIC_AUDIT_PREPARATION.md:72–145`。 |
| P | 冻结d87 `formal` → `NEW/analysis/stats/formal_primary_v1.json` | 唯一主确认性权威；实际结果尚待全real闭合后生成。算法与种子不在本次复审范围。 |
| H | `POST/secondary_heterogeneity.py` → `NEW/analysis/postrun/secondary_heterogeneity_vN/`，含计划单位、状态/索引事件、边际及配对描述、coverage/receipt | 静态准备；按既有冻结边界，不新增p值/检验族。见 `README.md:7–19`。 |
| R | 冻结检索驱动另开安静计时窗口；`POST/retrieval_postrun_analysis_v1.py` 原先绑定的 `bind/analyze` → `NEW/analysis/retrieval_postrun_analysis_v1/` | 本清单未启动检索或分析；已绑定版本不可重新绑定。见 `retrieval_postrun_analysis_v1_notes.txt:9–47`。 |
| D | `POST/original_reference_descriptives.py` → `NEW/analysis/postrun/original_reference_descriptives_vN/`，30绝对质量行、9同60任务/两轮系统参考行、receipt | 仅准备；不是新的主确认族，也不自行证明外部可比性。见 `ORIGINAL_REFERENCE_DESCRIPTIVES_INTERFACE.md:86–174`。 |
| E | `POST/external_reference_equivalence.py` → `NEW/analysis/external_reference_equivalence_vN/{audit.json,matched_units.jsonl,findings.json}` | 仅准备；须实际审计240 external→main T0精确配对。`NON_COMPARABLE`是可保留的实际结果，不可强行变PASS。 |
| O | `POST/prepare_offline_asset_manifest.py verify`，实际成功才生成 `approved_offline_manifest.json` | 只允许独立历史离线白名单；v2准备清单身份见 `POSTRUN_REBUILD_USAGE.txt:23–34`。准备清单不等于批准，旧real/retrieval不在复用范围。 |
| G | `POST/saved_report_adapter.py`（先数值包，再新版本 `--render`）与 `pure_report_plots.py` | 准备；消费A/P/H/R/D/E实际保存权威和O实际批准清单。须实际数值核对、逐图视觉验收。 |
| B | `POST/postrun_rebuild.py run/rebuild` 与 `offline_analysis_runner.py` | 准备；归档及独立无provider重建、比较、视觉QA尚待实际执行。见 `POSTRUN_REBUILD_USAGE.txt:1–110`。 |

执行安全顺序按 `POST/INTEGRATION.md:26–57`：先由协调者实际观察controller及children都终止、全部三stage退出码、全部6504身份终态与无未闭合starts；不得以controller失败推断子进程结束。真实计时与检索计时均实际停止后才开始对应重分析/归档工作。实际quiescence记录是测量分时与后处理的证据，不是要求新增一次实验。

## 按原文逐节绑定完成证据

本表每行沿用既有编号集合。**所有行当前统一为“本次未执行、尚不能证明本批完成”**；表中入口存在仅表示路线已设计。“没有定位入口”只描述本次允许读取的postrun范围，不断言其他目录不存在工具。

| 原文精确位置；既有编号 | 原方案必须满足的内容 | 足以支持完成的未来证据；对应入口 |
|---|---|---|
| 0节9–11行、1节15–23行；G001–G004 | 独立新结果目录；不改写旧归档；质量/token/模型时延来自真实provider与逐次usage；失败按 `UNSUPPORTED/INCOMPLETE/NON_COMPARABLE`保留；RQ1–5按各自对照解释 | 新批原始账、冻结身份、逐计划ID终态/失败及未知记录、结论→图表→原始记录索引。A核账，P/H/R/D/E/G提供分范围结果；目录或总manifest不能单独证明完成。 |
| 2.1节31–35行；E001–E010 | 自己的干净源码副本/整树SHA、依赖/内核/发行版/Python/VM/CPU；业务变更留差异并重新冻结重跑受影响单元；正式前冻结任务/排除规则；相同输入契约/角色/工具/参数/模型；单独性能窗口；CPU绑定/频率、宿主可见性、虚拟化、网络、同步/抢占实录 | 本批实际源清单、业务差异与受影响单元版本映射、依赖锁、环境与模型参数快照、请求参数实录、时间窗口及活动记录。A验证实际绑定，G表1展示；本次未读本批环境/变更记录，不能沿用旧清单的环境PASS。无法观察项目明确UNKNOWN，不能追认冻结前已控制。 |
| 2.2节39–52行、8节194行；P001–P011 | 8条预检命令完整stdout/stderr/退出码；AF_UNIX、SHM、memfd/SCM_RIGHTS、namespace/cgroup/Landlock实际能力；可探测不等于可强制；核心若在Windows/macOS执行，另做目标Linux IPC且耗时不合并 | 本批或明确复用身份的预检日志及 `preflight_report.md`，各能力实际验证或UNSUPPORTED。命令为 `pip install -e '.[dev,llm]'`、source identity、rt version、rt os-probe、原定unit/integration pytest、uname、os-release、Python version，逐项对应原文42–49行。本次不补跑。目标是openEuler用户态/Docker/WSL2的真实条件，不能称原生openEuler内核VM（`INTEGRATION.md:68–70`）。 |
| 3.1节58–64行；C001–C012 | 4域×6链×6事件=144唯一任务；公开24、正式120/20链；每域三难度各≥10；链前因后果且后续答案不同；≥90状态任务且小中大均衡，保留无状态；案例全字段、来源/改写/许可、独立确定性判定；防答案泄漏、prompt哈希/事实覆盖、各组隔离/空库/仅更早记忆 | 冻结corpus/checksums、链依赖/任务唯一性与分层审计、逐任务字段及来源许可、实际请求事实/标签泄漏检查、独立判定与状态消费证据。A覆盖本批144案例及6504单位事实/身份核验；自动验证例外另见下表。程序fixture成功只证明冻结canonical诊断/动作契约，不扩为无限制Linux诊断。 |
| 3.2节66–68行；C013–C017 | ≥240不同query，每域60，≥60无答案；词面/同义/混合语言/过期冲突恶意覆盖；100/1000/5000/20000实际池；相关集合/可采用范围；有答案与无答案分母分开，同任务派生query不作独立样本 | 本批冻结query/gold/池身份与自动标注依据、12池/三次建库实录、全部计划检索终态及错误/容量边界；R独立重建gold及14,400观察/60cell/20pooled cell。自动gold不能称人工标注，未测20k不得由5k外推。 |
| 4.1节74–85行；R001–R006、R013、C006 | 六组T0/S0/SS0/T1/S1/SS1；lean完整事实、同任务/角色/评测器；真实状态生产→交接→接收/解码/消费并影响计算；不适用标N/A；主120×6×3=2160；平衡轮换、链因果、每组每轮独立空库；质量n=120/20链 | 本批冻结计划、各组语义事实等价实录、2160精确ID终态、顺序/空库/更早记忆审计、逐URI消费和进程/释放连接。A+P+H；S0↔SS0使用冻结108适用任务，其他四pair120，适用子集与全体描述分开，不能只凭SS开关或引用生成计收益。 |
| 4.1节87–93行；R007–R012 | 第二家族/提供方60分层任务×6×2=720；同60任务多进程S0/SS0/SS1×2=360；AutoGen/LangGraph各60×2=120、四角色/只读工具/同任务契约/固定版本；原最低3480，若前瞻扩样才5640 | 精确预选任务/模型身份、720/360/各120终态；独立PID真实接收/消费/清理；实际框架调度和三endpoint计量/语义等价审计。A/H/D/E/G；外部差异仅系统参考，无法验证为NON_COMPARABLE单列。缺第二模型可按原文留空并缩小泛化主张，不能mock代填。当前冻结120任务且已放弃不劣措辞，不在本次新增扩样。 |
| 4.1节93–95行；R013–R015 | 每单元明确ID、计划分母；试运行请求数估费用/时间；价格快照/预算上限/重试余量；幂等/断点不重计；预算缩水须正式前决定且重算功效 | 冻结预算/价格来源和未知项、估算公式参数、真实尝试/重试/断点终态去重。A/P核身份与观察；`INTEGRATION.md:69–70`已指出币种价格/上限未知，不能从token推账单或宣称预算价格完整。后处理不能补造正式前预算。 |
| 4.2节101行；O001–O002 | lean text/JSON/MessagePack/session codec×0.5/2/8/32/128KiB；每格1000、预热100、3进程批；编码/解码/控制帧/CPU/socket实传字节/错误率 | 保留全部格/预热/批次/PID与原始记录、批次/源/配置SHA、审计及结果附录。O只认证独立历史protocol_v3字节与既有证据，G分批引用；一次旧CLI或复用源SHA不能代替矩阵。 |
| 4.2节102行、4.3节117行；O003–O004、A013 | socket copy/SHM/memfd-SCM_RIGHTS×1/4/16/64/256KiB/1/4MiB；每格200、预热20、3批；首次物化/复用/传递/映射/释放、p50/p95/p99、RSS/FD；小载荷反例 | 原始冷/热、后端、真实进程和状态全程事件，分项成本及边界；O+G图5/表4/附录。不能将已有arena复用写0映射耗时，也不能把融合read/计算拆成假读耗时；新状态stage补充与旧主图独立注明。 |
| 4.2节103行；O005–O006 | float32/float16/int8/adaptive×128/256/768/1536维×四分布；每格≥200唯一状态；编码后字节、余弦、接收方实际决策一致、回退、耗时 | 每格唯一状态计数、编码实测和consumer真实决策连接、失败/回退原始记录。O+G图6/表4；余弦或模拟成功不能代替实际决策保持。 |
| 4.2节104行；O007 | 重复率0/25/50/75/100%×修改1/10/50%；每格≥100状态序列×3批；去重/增量、物化字节、开销/失效 | 独立历史reuse批原始序列/批次/失效核验、测量边界和结果；O+G附录。3循环批不扩写为3独立进程批；不以旧任务fixture证明新契约性能等价。 |
| 4.2节105行；O008–O009 | 4池×语义/关键词/标签/混合/混合+反馈×240query×3独立建库=14400；P@1/Recall@3/10/MRR/nDCG@10、无答案误检、p50/p95、RSS | R本批独立安静窗口采集终态与实际分析，失败/质量/耗时null留计划分母，MRR@10截断如实披露；检索构造反馈不是真实provider采用。旧检索不能复用为本批结果。 |
| 4.2节106–111行；O010–O012、O020 | 链长1/2/4/8/16/32×并发1/2/4/8/16；每格≥30链/批，4角色；吞吐/尾时延/token/字节斜率/资源/失败；至少100连续轮；3/6/8 Agent扩展明确非当前主门禁 | 全30格批次、原始散点/CI/资源/失败及100轮时序和清理记录；O+G图8及附录。压力只以实际范围解释；100轮不推无界稳定。本次不要求新增Agent数实验。 |
| 4.2节107/109行；O013–O019 | router崩溃、丢ACK、坏状态、超时、重复消息、错误记忆六类各≥30实际注入；恢复率、重复副作用、错误传播、泄漏、降级 | 每类实际注入/恢复/资源/任务质量原始记录、专用故障语义审计和表。O+G表6/附录。fixture最终正确、下一会话恢复与坏状态/错误记忆曾被消费分别呈现，不能合成“普遍鲁棒”。容量停止按预注册规则保留UNSUPPORTED/超时。 |
| 4.3节115–117行；A001–A013 | ≥60任务且每域≥15，每对照≥3轮；逐项关引用优先、会话字典、去重、量化、增量、BM25、反馈/隔离、采用门控；同质量JSON/MessagePack；关联/无关/过时/错误冲突四条件；异工作量标WORK_NOT_EQUIVALENT | 精确冻结子集/各phase180终态、同角色/工具次数/步骤/质量与唯一变化因素核验；P保存既有匹配效应，A/H/G保留全部消融/条件/失败。冻结6360中额外匹配基线/五个memory phase是现有计划账结构，不在本清单另造第5种原文必需条件。 |
| 5.1节123–129行；M001–M015 | 主质量独立验证器成功率，所有planned包括崩溃/超时；动作/完整性/无效JSON/错误类型；provider逐次输入/输出/缓存/total/来源/调用重试；通信token另账并注明tokenizer；IPC/本地物化/逻辑三账；互斥wall分解、重叠另列、尾时延/均值/CI/吞吐/CPU；记忆候选/采用/采用后成功及配对因果/负迁移分开；RSS/FD/SHM/可靠性 | A逐次/逐消息/逐URI/索引/资源连接；P/H/D/R/G按各自分母输出；显式cache0与未报告null分开，未观测provider queue/网络不假拆，no-attempt不能认证provider usage；索引seed独立时间无逻辑write span保持NOT_OBSERVED。自动验证不产生人工盲评分。 |
| 5.2节135–143行；S001–S009 | `task_runs.parquet`或`.csv`、`provider_calls.jsonl`、`messages.jsonl`、`states.jsonl`、`memory_events.jsonl`、`resource_samples.parquet`、`manifest.json`；逐表原文全部必需字段/完整schema；资源100–500ms采样与开销；只追加/只读归档 | 最终实际文件、完整字段/类型/单位/nullable与关系schema、行数/ID/来源/未知/单位核验及原始不可变证明。A验证观察关系，但不是结构化表导出证书；本次所读POST源没有定位到task_runs CSV/Parquet及resource_samples Parquet/schema导出阶段（见缺口1）。 |
| 6节147–153行；T001–T011 | 同task/model/repeat配对；域内整链10000 bootstrap；原五pair×输入token/同拓扑IPC共10 Holm；链符号翻转精确或≥100000；−5pp风险与保守单侧95%界；只公开pilot前瞻功效，扩样或放弃不劣；时延/失败/全planned与both-success辅助差异/异质性/原始分布；旧数据不拼池 | 只用P实际保存的冻结结果、方法/身份/任务链数/分母及保留失败/缺失；H/D/R为原分工描述，不能再调用旧statistics或新增确认族。冻结108状态适用/其余120人群、5质量界与14跨phase匹配效应沿用现有设计。前瞻放弃不劣措辞持续有效。本次只核证据入口，不复审或更换统计算法。 |
| 7.1节161–170行；F001–F010 | 图1方法数据流；图2六组provider token/IPC配对点CI＋独立物化；图3分域质量CI/错误；图4互斥耗时/尾时延；图5状态冷/热路径；图6精度/字节/实际决策Pareto；图7检索质量/无答案误检/p95/负迁移；图8链长/并发吞吐/p95/通信增长；图9仅外部可比时增设；禁误导图型 | G实际图1–8；5/6/8仅O批准的专用旧离线字节并写批/源/边界，图7只用新检索；D+E实际可比后图9，否则完整描述/不可比原因入附录。逐图每数据点→已保存权威→原始记录可追踪。8张是方案建议交付清单，不以脚本存在宣称已成图。 |
| 7.2节176–183行；F011–F018 | 表1环境/版本/模型/VM/任务/n/配置；表2六组/唯一变化/同工作量；表3成功/usage/IPC/物化/调用/时延CI；表4状态路径精度成本/决策/资源；表5候选/采用/采用后成功/因果负迁移/额外开销；表6外部或故障/不可比/不支持；域难度、全消融、每档规模、模型域、失败超时、版本哈希统计附录 | G实际table_01–06.csv与所有附录；不得复制含旧real external的旧table06。每图`figure_XX.csv`及可复现源、caption n/重复单位/CI/边界/mock或真实身份。表4旧offline与新记录trace分节，不合并成同次测量。 |
| 7.3节187–190行；F019–F021 | 矢量PDF/SVG/600dpi PNG、单/双栏；字体嵌入/路径、黑白可辨；模式色固定#183153/#087F8C/#6356A5，记忆线型/填充、风险#D96C3F、中性灰/白；单位/CI/坐标一致；核值/缺失非0/字体/图例/PDF可检索 | G实际导出后数值一致性、颜色语义和版面/文字检查的实录，逐PDF/PNG实际视觉验收。B数值/PNG比较不是PDF/SVG版面或可检索文字证明；所有文件及正确导出参数存在也不能代替实际QA。 |
| 8节194–200行；D001–D004 | 预检→任务/公开24修脚手架→5–10任务usage/字节守恒/重复计量/状态消费者/记忆与开销校准→冻结→六组/跨模型外部→分时离线→从只读归档一键全部表图及≥30跨域原始单元抽查 | 实际冻结前校准/metric_audit及时间顺序证据，与终态后A完整自动验证、P/H/R/D/E/G/B实际receipts分开。旧版预抽30不能迁移或追认；获批自动替代在本批以全6504事实/账连接实际结果陈述，不发明“预注册30抽样”。 |
| 5.2节143行、8节202–221行；D005–D007 | protocol/预检/corpus/manifests/raw/logs/analysis/figures/tables/checksums/README/claims_to_evidence/limitations；正式与检索冻结、六组完整、usage可逐call核对、状态/记忆闭环、图表只读重建、外部与跨模型缺口明确、全部主措辞有原始路径 | B实际新归档/独立重建与身份/数值比较，G实际图表QA；最终README命令、checksums、原始只读索引、结论索引与有效性威胁逐条关闭。本次所读POST源未定位这四个最终命名文档/清单的生成或最终验收入口（见缺口2）；不能以report_availability或重建receipt代替。 |
| 9节227–240行、10节245–246行；D008 | 方案自查的机制区分/样本/真实与mock/状态消费/记忆因果/三类字节/质量边界/外部限制持续有效；写作前独立相关工作与实质差异梳理；按系统集成或机制创新实情定位 | claims_to_evidence/limitations与论文措辞实际核对；相关工作文献及贡献对照属写作交付，本次未读/未执行，POST无对应研究入口。不得由实验数量推出创新，也不新增实验。 |
| 文末摘要256行、1节25行；D009 | 正式n、质量变化及CI、输入token变化、可观测IPC变化、状态/记忆增量、载荷/任务适用边界；旧2026-10-07先导独立陈述 | G实际`analysis/abstract_metrics.json`只装载审计后的已保存值，继而核定论文句子/样本链数/质量契约/未知/负结果；不能选择更有利旧估计、把主n写为重复执行数或捏造上游未保存的T0→SS1效应。不劣措辞继续保留弃用。 |

## 新批单位账与唯一协议例外

原文85–93行的原始最低真实主/复核/跨进程/外部合计为3480；原文115–117行另要求消融/传统序列化和四类记忆条件。现有新批账（`FULL_METRIC_AUDIT_PREPARATION.md:16–22`）为：

| 新批冻结账范围 | 计划数 | 未来实际证明方式 |
|---|---:|---|
| main | 2160 | A逐ID终态与P原人群结果；本次未读取终态。 |
| cross-model | 720 | A/H按独立模型保留，不与main拼池。 |
| multiprocess | 360 | A实际PID/消费/释放，不用UDS帧冒充独立Agent进程。 |
| AutoGen、LangGraph | 各120，合240 | A+D+E；失败与不可比保留。 |
| ablation baseline、8关闭项 | 180＋8×180=1620 | A/P/H/G原子干预与匹配基线；不按成功删分母。 |
| 2个传统序列化条件 | 2×180=360 | 同质量/同工作量检查，差异明确。 |
| 5个冻结memory phase | 5×180=900 | 保留冻结phase身份，四类原文任务条件及其既有匹配参考不改定义。 |
| formal合计 | **6360** | 全冻结formal ID终态；不把主3480误当包含全部消融后的最终账。 |
| public | **144** | 独立公开校准前缀不变；不进入formal效应人群。 |
| public＋formal总账 | **6504** | A全量账/事实验证；总数只是闭合记账，不能池化性能估计。 |
| 检索 | **14400** | 独立窗口、4池×3建库×5模式×240；R全部计划观察，不能作14400独立任务。 |

| 受例外影响的原文位置/旧编号 | 获批变更后的未来证据要求 | 禁止扩大例外的范围 |
|---|---|---|
| 3.1节62行，3.2节68行，5.1节123行，8节195/200行；C009/C010/C015/M003/D003的人工审核、双人盲评/评分、标注/抽查表述 | 本批实际协议变更记录引用用户授权、原条款、自动验证版本/身份、实际覆盖范围和保留限制；A实际完整案例/6504单位事实与账核验，R实际独立自动gold核验。`INTEGRATION.md:65–67`已经声明取消人工评分/审核；它不是实际审计receipt。本次允许范围内未定位本批独立变更记录的具体保存路径，结项索引须补定位。 | 不造两位评审、人工分数、agreement/分歧裁决，不追认“正式前预抽30”；不把自动fixture判定说成开放解释人工质量或生产Linux诊断准确率；不取消任何其他任务数、记录、统计、真实模型、图表和复现要求。 |

## 少量真实交付缺口与收尾边界

1. **规范表/完整schema的导出路线尚未定位。** 原文135–141行要求task_runs CSV或Parquet以及resource_samples Parquet和完整schema。已读 `postrun_rebuild.py:324–348` 的7阶段没有导出阶段，`collect_inputs:183–186`只归档JSONL raw；A的连接验证与G的table_03汇总均不等于逐task规范表。本项应由后续受版本约束的导出/现有适配路线实际生成并核对，不能修改原始账或把JSONL改名交差。本清单不编写或运行该工具。
2. **最终复现说明与结论/限制/校验文件尚未绑定到生成和验收入口。** 原文143、215–221行的`README_reproduce.md`、`claims_to_evidence.md`、`limitations.md`、`checksums.sha256`在已读POST源码中未见生成/最终验收路线。G会输出abstract facts/availability及输出hash字典（`saved_report_adapter.py:865–889`），B会归档/比较，但这些不等于上述命名交付和逐结论原始路径闭环。结项仍须实际交付和核验；本项不增设实验。
3. **实际运行证据仍是最大剩余工作，无法靠准备文件关闭。** 完整6504终态与真正安静窗口、A/P/H实际结果、新14400检索及R、D/E、O实际字节批准、G数值/全部图表视觉QA、B新归档/独立重建都须实际执行并留下输出与失败。明确UNKNOWN/UNSUPPORTED/NON_COMPARABLE的终态可支持受限报告；它们不能被声明为对应指标已完整测得。任何后处理来源身份说明冲突须用实际受审版本绑定，不从说明文字推PASS。
4. **既有观察限制不会在后处理自动消失。** openEuler用户态/Docker/WSL2拓扑、canonical诊断/只读fixture质量范围、未知价格/预算和provider queue/网络拆分、no-attempt/失败usage、seed logical-write span、融合读算/复用映射、独立历史offline与新契约的差异均须随图表和主张保留。若本批环境或协议例外已经另有合法证据，本清单只要求结项索引给出原路径/身份；本次未读它们，不宣称缺失事实。

完成判定仍使用原文221行：原始证据、各自分母、状态与记忆闭环、可重建图表、限制和结论索引真正闭合才可作相应完整声明。本文件自身、155行历史清单、任一模板/脚本或静态准备receipt均不证明实验完成。本文仅新增此文件；不对正在运行的批次采取任何动作。
