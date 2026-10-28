"""Record actual color/grayscale visual review and expose byte-identical final assets."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import shutil


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')


def main():
    root=Path(__file__).resolve().parents[1];out=root/'render_local_v2';qa=root/'qa'
    technical=json.loads((qa/'technical_checks.json').read_text())
    execution=json.loads((root/'logs_local_v2/execution_receipt.json').read_text())
    assert execution['actual_exit_code']==0 and execution['sources_and_frozen_inputs_unchanged'] is True
    save(qa/'visual_qa.json',{'status':'PASS_PRESENTATION_AND_EXACT_SAVED_VALUE_PROJECTION',
        'created_utc':datetime.now(timezone.utc).isoformat(),
        'actual_review':'Viewed final color PNG and actual grayscale preview with view_image; parent also viewed final color PNG and approved layout.',
        'render_receipt_sha256':sha(out/'render_receipt.json'),'technical_checks_sha256':sha(qa/'technical_checks.json'),
        'checks':{'three_panels_horizontal':True,'size_175_by_61_mm':True,'shared_top_legend_clear_of_lines':True,
            'panel_labels_a_b_c':True,'chinese_font_no_missing_glyphs':True,'axis_labels_ticks_notes_inside_canvas':True,
            'symbols_and_line_styles_distinct_in_grayscale':True,'exact_saved_point_CI_triplets':90,
            'pdf_chinese_searchable_and_font_embedded':True,'no_new_estimation_or_experiment':True},
        'unresolved_P1_P2':[],
        'limitations':['Panels (b)/(c) contain genuinely overlapping concurrency curves; saved values were not offset to manufacture separation.',
            'Some saved pointwise intervals are narrower than marker size; exact endpoints remain in frozen CSV.',
            'This is layout QA, not a new statistical or experimental validation.'],
        'caption_boundaries_verified':['postcollection pointwise95 CI, not simultaneous band','30 batches in the same original OS process per cell',
            'narrow intervals may overlap markers','offline fixture only; no real model calls','proxy token count is not billed provider tokens'],
        'model_calls':0,'experiment_runs':0,'statistical_recalculations':0})
    exposed=[]
    for extension in ('png','pdf','svg'):
        source=out/f'figure5_horizontal_zh.{extension}';target=root/source.name
        assert not target.exists();shutil.copyfile(source,target);assert sha(source)==sha(target)
        exposed.append({'path':target.name,'sha256':sha(target),'bytes':target.stat().st_size})
    (root/'README.txt').write_text('图5横排新增版：175毫米×61毫米，600dpi PNG及矢量PDF/SVG。\n'
        '最终图位于本目录 figure5_horizontal_zh.png/.pdf/.svg；与实际 render_local_v2 输出逐字节一致。\n'
        'data保留原冻结CSV、区间方法与原点一致性证据。90个点估计/区间三元组逐一核对相同。\n'
        'caption.txt保留完整科学边界；qa保留实际彩色/灰阶视觉检查、PDF字体和分辨率核对。\n'
        '复现：D:/miniconda/python.exe -X utf8 -B code/run_local.py --attempt reproduce_01。\n'
        '本次使用现成 matplotlib 3.7.1、Noto Sans SC字体，未安装依赖、未启动或修复Docker。\n'
        'logs_v1保存Docker引擎未开启时的失败，logs_local_v1保存捆绑Python缺matplotlib的失败，最终成功为logs_local_v2。\n'
        '仅图5排版，不恢复昨日取消的图表修订，不改原图、统计、旧交付包或飞书。\n',encoding='utf-8')
    members={p.relative_to(root).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size}
             for p in sorted(root.rglob('*')) if p.is_file() and 'mpl_cache' not in p.parts}
    save(root/'delivery_manifest.json',{'status':'ACTUAL_FIGURE5_HORIZONTAL_DELIVERED_WITH_QA',
        'created_utc':datetime.now(timezone.utc).isoformat(),'main_assets':exposed,'files':members,
        'source_csv_sha256':technical['frozen_input_source_and_copy_sha256'],
        'saved_point_CI_triplets_exact':90,'source_cells':30,'original_inputs_or_figures_modified':False,
        'actual_local_execution_receipt_sha256':sha(root/'logs_local_v2/execution_receipt.json'),
        'actual_qa_receipt_sha256':sha(qa/'visual_qa.json'),'new_model_calls':0,'new_experiments':0,'new_statistics':0})
    print(json.dumps({'status':'ACTUAL_FIGURE5_HORIZONTAL_DELIVERED_WITH_QA','assets':exposed},ensure_ascii=False))


if __name__=='__main__':main()
