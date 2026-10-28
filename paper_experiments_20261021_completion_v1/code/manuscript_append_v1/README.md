# 文末追加工具：仅源码准备

遵循 `manifests/append_only_delivery_amendment_v1.json`。原稿文件不动；新DOCX保留原137个正文块及其顺序，在原参考文献之后、原末尾sectPr之前追加补充章节。首个新增标题强制另起一页。旧摘要、正文、图表编号、公式、参考文献均不替换、不移动。

当前仅保存源码并做AST静态检查。尚未执行作者操作、marker、DOCX生成或渲染，也没有虚构补充结果JSON。旧 `code/manuscript/` 替换方案保留作历史准备，不执行。

## 真实输入约定

由根代理审阅真实补充内容后提供JSON及其SHA256。顶层字段：`schema_version="manuscript-append-content-v1"`、`source_sha256`（原稿18ca54…完整SHA）、`actual_results_only=true`、`batches_not_pooled=true`、`evidence`、`blocks`。`evidence`每项为真实文件的`path/sha256/scope`；工具只核文件身份，不把存在性或哈希当科学结论通过。

`blocks`按最终追加顺序排列，第一项必须为标题段落；工具不生成结果数字或研究结论。

- `kind="paragraph"`：`text`及可选`style`。样式为`body/heading1/heading2/abstract_zh/abstract_en/caption`，均复用原稿样式，不修改styles。
- `kind="table"`：`number="S1"`、`caption="补充表S1…"`、`rows`（含表头；每格是已审阅字符串）和可选`template_unit`。表格按原模板列数接收输入；默认B0058为3列，其他原表可显式选择，必须为无合并/无复杂对象的相同列数模板。不把null隐式格式化成0。
- `kind="figure"`：`number="S1"`、`caption="补充图S1…"`、真实PNG的`path/sha256`、`alt_text`，可选正整数`width_emu`。按源图模板保持长宽比；只增加媒体及关系。补充表和图各自从S1连续编号，正文交叉引用由已审输入明确给定。

不要把原稿中的历史“尚待”改掉；补充正文明确其历史快照身份及现在的实际结果，提供原B位置对应表。三批身份、720初始化边界、采后压力CI及保留限制均写在补充内容里，不能把旧数值混成新结果。

## 根审阅后才执行

主采集结束、真实统计/图像完成且补充文本审阅后，先按documents技能执行一次真正作者操作marker，再使用官方bundled Python：

```powershell
& 'C:/Users/暴瘦/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -X utf8 -B 'F:/codex/多agent系统架构-软件学报/paper_experiments_20261021_completion_v1/code/manuscript_append_v1/append_only_manuscript_v1.py' --source $OriginalDocx --content $ReviewedRealContentJson --reviewed-content-sha256 $ReviewedContentSha256 --out $FreshSupplementDocx --receipt $FreshAuthoringReceipt
```

输出和回执必须不存在。失败不覆盖原稿；若部分新文件已创建，保留供诊断并另选fresh输出，不能把失败当完成。成功回执只确认追加和原内容保持，状态仍为`APPENDED_PENDING_FULL_RENDER_AND_EVERY_PAGE_QA`。随后必须用documents的`render_docx.py`渲染，并实际检查全部页面后才交付。

回执从保存后的DOCX复读核验全部原body节点canonical序列、原137 blocks、sectPr、styles字节、每条原关系及每项旧media；其他原ZIP内容逐项保持。无需读取任何运行中raw或调用模型。
