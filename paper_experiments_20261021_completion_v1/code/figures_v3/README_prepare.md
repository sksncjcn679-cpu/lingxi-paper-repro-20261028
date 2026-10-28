# 补充批次中文图接入（尚未执行）

当前只准备代码。主采集结束后，先由独立统计入口生成新结果，再生成实际输入manifest；禁止用占位JSON提前作图。此目录不含模型/SDK/网络入口，不读取task_runs/model_io/prompt，也不执行估计、bootstrap或检验。

入口按顺序：

1. `prepare_figure_data.py --manifest ACTUAL.json --out FRESH_DATA_DIR`：读取保存结果并投影。兼容`formal_primary_v1.json`字段；旧/new为两个2160运行的独立批次，固定五配对120/108任务、20链、3次。新四类端点必须完整；旧五token的INCOMPLETE保留为空，不以新批次回填。
2. `render_completion_figures.py --data FRESH_DATA_DIR/figure_data.json --out FRESH_RENDER_DIR`：只画图10–13，PDF/SVG/600dpi PNG，单双栏；必须在主采集结束后，在原不可变openEuler镜像、现有离线guard、只读输入挂载下执行。原九图不重画、不改数据。生成回执状态明确为QA_PENDING。
3. 实际单双栏逐图QA后，`package_completion_figures.py --data ... --render-root ... --qa-single ... --qa-double ... --stage FRESH_STAGE --zip FRESH.zip`。保留原九图包的全部manifest列明成员，逐文件SHA验证，再加入新四图、CSV、来源及回执；ZIP解码逐成员核SHA/CRC，不发布外部。

## 实际输入manifest契约

顶层：`schema_version="completion-figure-inputs-v1"`、`status="ACTUAL_COMPLETED_INPUTS"`、不同的`old_batch_id/new_batch_id`、`old_delivery_root`以及`bindings`。

`bindings`的每项必须为真实`{"path":绝对路径,"sha256":64位SHA}`，不填示意值：

- `old_primary`：旧正式`analysis/stats/formal_primary_v1.json`（旧批次单列）。
- `new_primary`：新复现同结构JSON，planned/observed=2160、missing=0、collection_status=COMPLETE、10项单一族。新token/IPC/quality/wall必须为COMPLETE并已有保存CI。若最终新研究使用不同schema/人群，停止并显式适配，不改统计。
- `new_timing_csv`：沿用旧`data/figure_04.csv`列名；main/main/runtime_uds、overall/all_formal，6臂各360，wall_sec与provider_wait_fraction；另含`kind=recorded_exclusive_partition_mass`的已保存互斥分解。后者按旧格式可无phase/dimension，但每类别必须6臂完整。禁止伪造read/provider_queue，禁止将嵌套index再加到父span。
- `seed_measurements_jsonl`、`seed_result`：`E:/codex_rebuilds/completion_20261021_v1/seed_write/run_v3/results/measurements.jsonl`、`result.json`。仅实际720初始化写入，四条件各180，不是新720全Agent/模型运行。原始测量全点保留；箱线只作描述分布，不加CI/p。
- `stress_csv`、`stress_point_identity`、`stress_method`：已完成压力补分析`output/analysis/stress_cell_ci.csv`、`point_identity.json`、`method_used.json`。30格CI明确采后/pointwise，原点与原分母逐格精确一致；不用斜率CI，也不称独立OS进程。
- `old_delivery_manifest`：原中文图包`delivery/chinese_figures_v1/manifest.json`；旧图5/6机制数据、旧图9外部NON_COMPARABLE均完整保留。
- `font`：已确认静态Regular400中文字体，SHA`7076eda296dba38bdd60a14d7259b6ed1470dddf66d1e05a88134e65e4d073f3`。原font/OFL仍在原包。

QA小回执契约：`schema_version="completion-figure-visual-qa-v1"`、实际`status=PASS_WITH_LIMITATIONS`或`PASS_PRESENTATION_AND_SOURCE_BYTE_QA`、`width=single/double`、`actual_new_views=[10,11,12,13]`、实际`render_receipt_sha256`、`unresolved_P1_P2=[]`。必须另记实际font/可检索/裁切/图例/CI/null/灰阶检查及各文件SHA，不能将静态源码或生成成功当视觉PASS。

## 图号与范围

原图1–9逐字节沿用。新增图10为两独立批次的token/运行时IPC相对百分比、质量百分点、guest wall秒差；四面板并列，保留质量折损和未完成。图11仅新批次耗时/SDK占比/互斥分解，分位数不是CI。图12为独立初始化分布，父inclusive与子index不相加。图13为旧压力点的新采后逐格CI，不是曲线同步置信带。

主实验拓扑runtime_uds，不外推四独立Agent进程；自动fixture质量不等于通用Linux正确率；wall/SDK等待不等于纯服务推理或队列时延。绘图程序不生成“显著提速”“质量不变”“非劣”措辞，不池化批次、不建立新检验族。实际论文图号可由作者引用映射，原四图不限制新增图的来源记录。

`base_renderer_v8.py`精确保留已QA的旧renderer字节（SHA`6e87b343e50323ac86e36419418440ec2254c1e4cfc4a866ef221b4ca9f050df`），只复用字体、原点绘制及耗时版式。新图仍需实际渲染/QA；本准备本身不声称PASS。
