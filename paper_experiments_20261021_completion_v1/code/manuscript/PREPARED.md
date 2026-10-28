# 论文另存更新入口

当前只准备源码、原稿定位和依赖；未运行作者操作标记、未生成DOCX、未渲染。主实验真实终止并取得根审阅的分析JSON与中文PNG后再执行。

`replacement_positions.json`列出摘要、4.1表2、4.2、5.1—5.2、5.4、5.5、6—7的原XML位置及批次边界。提取文件有133条非空记录，但原文有137个正文block；4个图像段被提取器跳过，因此更新器使用B编号中的原XML下标。5个公式本身是图片，全部保护；作者/单位/邮箱/中图号、参考文献、样式和非目标包部件保留。

最终patch须为`manuscript-final-patch-v1`，包含原稿SHA、`timed_experiments_actual_terminal=true`、`batches_not_pooled=true`、带path/sha256/scope的evidence，以及operations。evidence必须包含`new_independent_main_replication`。支持操作：`replace_paragraph`、`replace_table`、`insert_paragraphs_after`、`copy_history_before_references`、`replace_picture`、`insert_figure_after`；所有unit引用来自原稿，历史表复制必须先于原位替换。段落text/表格rows必须为已核真实结果；PNG必须给image_path/image_sha256/alt_text。新图caption与caption_template_unit另列。原作者元数据占位保持原样，不编造。

以下为未来入口，当前不执行。先用官方Node成功执行一次documents编辑marker，紧接作者命令；不要提前或重复marker：

```powershell
$runtime = 'C:/Users/暴瘦/.cache/codex-runtimes/codex-primary-runtime/dependencies'
$skill = 'C:/Users/暴瘦/.codex/plugins/cache/openai-primary-runtime/documents/26.1017.11052/skills/documents'
& "$runtime/node/bin/node.exe" "$skill/container_tools/mark_artifact_operation_started.mjs" --operation-kind edit --expected-output-count 1 --output-format docx
& "$runtime/python/python.exe" -X utf8 -B paper_experiments_20261021_completion_v1/code/manuscript/update_manuscript_v1.py --source $originalDocx --units .local/manuscript_gap_review_20261021_v1/manuscript_units.json --patch $finalReviewedPatch --out $freshUpdatedDocx --receipt $freshAuthoringReceipt
& "$runtime/python/python.exe" -X utf8 "$skill/render_docx.py" $freshUpdatedDocx --output_dir $freshRenderDirectory --emit_pdf
```

只使用已加载的官方Python/Node及其lxml、Pillow依赖。已定位Codex管理的LibreOffice：`C:/Users/暴瘦/.codex/runtime/libreoffice/app/program/soffice.exe`；渲染时显式将该目录置于PATH首位，不使用其他桌面LibreOffice。先验证原稿渲染，再逐页检查更新稿全部PNG，修复后重新渲染；作者收据的`rendered=false`不代表可交付。最终仅交付已通过逐页QA的新DOCX及用户要求的独立中文图，原DOCX不覆盖。
