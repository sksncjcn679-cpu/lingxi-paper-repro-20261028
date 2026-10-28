"""Add Chinese-only presentation revision; copy all frozen plotting values byte-exact."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import re
import shutil


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')


def main():
    new=Path(__file__).resolve().parents[2]
    original=new/'figures_v3'
    revision=new/'figures_v4_zh_revision';revision.mkdir()
    code=revision/'code';code.mkdir(); data=revision/'data';data.mkdir()
    projection=json.loads((original/'results/data/projection_receipt.json').read_text())
    copied=[]
    for name,record in projection['outputs'].items():
        src=original/'results/data'/name;target=data/name
        assert sha(src)==record['sha256']
        shutil.copyfile(src,target);assert sha(target)==record['sha256']
        copied.append({'name':name,'source':str(src),'source_sha256':sha(src),'copy_sha256':sha(target)})
    shutil.copyfile(original/'results/data/projection_receipt.json',data/'projection_receipt.json')
    source=new/'code/figures_v3'
    for name in ('prepare_figure_data.py','base_renderer_v8.py','render_completion_figures.py'):
        shutil.copyfile(source/name,code/name)
    render=code/'render_completion_figures.py';text=render.read_text(encoding='utf-8')
    replacements={
        '图10　两个独立批次的五项配对效应':'补充图S1　独立批次的配对效应',
        '图11　复现批次耗时与互斥分解':'补充图S2　耗时与互斥分解',
        '图12　独立初始化补测的写入分布':'补充图S3　初始化写入分布',
        '图13　原压力数据的采后点态区间':'补充图S4　压力实验点态区间',
        '输入token变化（%）':'模型输入词元变化（%）',
        '运行时IPC字节变化（%）':'运行时通信字节变化（%）',
        '自动任务质量差（百分点）':'任务成功率差（百分点）',
        '客户环境端到端耗时差（s）':'客户环境端到端耗时差（秒）',
        '点为估计、误差线为95%CI；token/IPC为相对百分比，质量为百分点。':'点为估计、误差线为95%置信区间；词元与通信量为相对百分比，成功率为百分点。',
        '旧token未完成；质量为自动验证，不作不劣或加速承诺。':'词元采用服务方真实token计数，通信采用可观测IPC字节；旧批次词元指标未完成。自动验证不作不劣或加速承诺。',
        '仅新复现批次；互斥分区不重复相加；分位数不是CI。':'仅新复现批次；互斥分区不重复相加；分位数不是置信区间。',
        '客户环境单调计时与客户端SDK等待；不等于纯推理、服务方排队或完整宿主用户耗时。':'客户端接口调用等待以SDK记录；不等于纯推理、服务方排队或完整宿主用户耗时。',
        '独立720次初始化实测，每条件180次；模型/API调用0；不回填历史运行。':'独立720次初始化实测，每条件180次；模型接口调用0；不回填历史运行。',
        '历史独立离线压力批次；每格30完整原批，10000重抽，seed20261021。':'历史独立离线压力批次；每格30完整原批，10000次重抽，随机种子20261021。',
        '采后逐格百分位95%CI，非同步曲线带；原点不变；通信代理计数不是服务方token。':'采后逐格百分位95%置信区间，非同步曲线带；原点不变；通信代理词元不等于服务方用量。',
        '整体写入耗时（ms）':'整体写入耗时（毫秒）',
        '写入互斥归属耗时（ms）':'写入互斥归属耗时（毫秒）',
        '索引互斥归属耗时（ms）':'索引互斥归属耗时（毫秒）',
        '任务p95（s）':'任务95分位耗时（秒）',
        '帧与ACK字节/链':'帧与确认消息字节/链',
        '消息代理token/链':'消息代理词元数/链',
        '链长L':'链长',
    }
    applied=[]
    for before,after in replacements.items():
        assert before in text,before
        text=text.replace(before,after);applied.append({'before':before,'after':after})
    before="    handles, labels = ax.get_legend_handles_labels()\n"
    after=("    fig.axes[0].set_ylabel('互斥分区归属时间（秒）')\n"
           "    fig.axes[1].set_ylabel('模型接口等待占比')\n"
           "    fig.axes[2].set_ylabel('任务耗时分位数（秒）')\n"
           "    handles, labels = ax.get_legend_handles_labels()\n"
           "    labels = [{'p50': '中位数', 'p95': '95分位', 'p99': '99分位'}.get(label, label) for label in labels]\n"
           "    for legend in fig.legends:\n"
           "        for item in legend.get_texts():\n"
           "            item.set_text(item.get_text().replace('客户端SDK等待', '客户端模型调用等待').replace('路由IPC', '路由通信'))\n")
    assert before in text;text=text.replace(before,after)
    render.write_text(text,encoding='utf-8');ast.parse(text)
    save(revision/'revision_preparation.json',{'status':'PREPARED_CHINESE_PRESENTATION_ONLY_NOT_RENDERED',
        'created_utc':datetime.now(timezone.utc).isoformat(),'data_copies':copied,
        'all_frozen_plotting_files_byte_identical':True,'scientific_estimation_or_experiment_runs':0,
        'source_before_sha256':sha(source/'render_completion_figures.py'),'source_after_sha256':sha(render),
        'label_replacements':applied,'extra_legend_axis_changes':'Text setters only; geometry, markers, line styles, numeric transforms and all saved data unchanged.',
        'originals_modified':False,'prior_deliveries_or_feishu_touched':False,
        'figure_mapping':{'S1':10,'S2':11,'S3':12,'S4':13}})


if __name__=='__main__':main()
