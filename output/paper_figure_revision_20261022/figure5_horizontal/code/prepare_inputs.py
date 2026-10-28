"""Copy the already frozen historical pressure plotting evidence without modification."""
from pathlib import Path
import hashlib
import json
import shutil


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def main():
    root=Path(__file__).resolve().parents[1];workspace=root.parents[2]
    new=workspace/'paper_experiments_20261021_completion_v1'
    data=root/'data';data.mkdir();fonts=root/'fonts';fonts.mkdir()
    prior=workspace/'paper_experiments_20261019_pilot/protocol_repair_20261020_v1/delivery/chinese_figures_v1'
    sources={
        'figure5_frozen.csv':new/'figures_v3/results/data/figure_13_stress.csv',
        'method_used.json':Path('E:/codex_rebuilds/completion_20261021_v1/stress_ci/output/analysis/method_used.json'),
        'point_identity.json':Path('E:/codex_rebuilds/completion_20261021_v1/stress_ci/output/analysis/point_identity.json')}
    bindings={}
    for name,path in sources.items():
        target=data/name;shutil.copyfile(path,target);assert sha(path)==sha(target)
        bindings[name]={'source_path':str(path),'sha256':sha(path),'bytes':path.stat().st_size,'copy_sha256':sha(target)}
    for name in ('NotoSansSC-Regular.ttf','OFL.txt'):
        shutil.copyfile(prior/'fonts'/name,fonts/name)
    guard=new/'code/main_postrun_v1/offline_analysis_runner.py'
    assert sha(guard)=='7a82d8b4263b127b938958bc6bca74d8db50f0517e2654e94547bd4c67f77e2d'
    shutil.copyfile(guard,root/'code/offline_analysis_runner.py')
    manifest={'status':'ACTUAL_FROZEN_PRESSURE_INPUTS_COPIED_BYTE_IDENTICAL','frozen_csv':bindings['figure5_frozen.csv'],
        'bindings':bindings,'source_plot':'historical stress, previous library figure13 / manuscript figure5; not retrieval figure05',
        'originals_modified':False,'new_model_calls':0,'new_statistics':0,
        'font_sha256':sha(fonts/'NotoSansSC-Regular.ttf')}
    (data/'source_binding.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    caption=('图5　历史压力实验点估计与采后逐格95%置信区间。'
        '（a）任务95分位耗时；（b）帧与确认消息字节/链；（c）消息代理词元数/链。'
        '链长为1、2、4、8、16、32，并发数为1、2、4、8、16，每格保留30个完整原始批次。'
        '误差线为按完整原批重抽10000次所得的已保存百分位95%点态置信区间（随机种子20261021），'
        '不是同时置信带；较窄区间可能与点标记重合。'
        '任务95分位数来自全部已记录任务的合并时延，不是各批95分位数的平均。'
        '每格30批由一个原OS进程连续执行，并非30次独立启动；批次可交换性是补充假设，不能排除顺序漂移。'
        '通信量以链数作为分母，不能据此声称并发带来通信节省；消息代理词元不是服务方真实token用量。'
        '本图仅重新排版既有离线数据和区间，无新增实验、真实模型调用或统计重算。\n')
    (root/'caption.txt').write_text(caption,encoding='utf-8')
    (root/'README.txt').write_text('图5横排新增版，175毫米×61毫米，三分面共用顶部图例。\n'
        'data保留原冻结CSV和区间方法，CSV与图库图13的来源逐字节一致。caption.txt为完整图注。\n'
        '复现：python code/run_offline.py --attempt reproduce_01；需要本机已有指定Docker镜像，不下载依赖。\n'
        '输入仅只读挂载；容器无网络，2CPU、2GiB、只读根文件系统，沿用原离线守卫。\n'
        '原图、旧结果、统计方法及先前交付包均不改动。\n',encoding='utf-8')


if __name__=='__main__':main()
