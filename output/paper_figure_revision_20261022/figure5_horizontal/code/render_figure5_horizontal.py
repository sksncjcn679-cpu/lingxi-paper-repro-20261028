"""Draw the saved historical stress points and existing pointwise CI; no estimation."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json
import math
import os


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path,default=Path('/data'))
    parser.add_argument('--font',type=Path,default=Path('/fonts/NotoSansSC-Regular.ttf'))
    parser.add_argument('--out',type=Path,default=Path('/output'))
    args=parser.parse_args()
    out=args.out; source=args.data_dir/'figure5_frozen.csv'
    binding=json.loads((args.data_dir/'source_binding.json').read_text(encoding='utf-8'))
    assert sha(source)==binding['frozen_csv']['sha256']
    font=args.font
    assert sha(font)=='7076eda296dba38bdd60a14d7259b6ed1470dddf66d1e05a88134e65e4d073f3'
    with source.open(encoding='utf-8-sig',newline='') as stream:raw=list(csv.DictReader(stream))
    assert len(raw)==30
    rows=[]
    for record in raw:
        assert record['status']=='POSTCOLLECTION_POINTWISE_CI'
        assert int(float(record['recorded_batches']))==30
        assert int(float(record['bootstrap_draws']))==10000
        assert int(float(record['master_seed']))==20261021
        assert record['provider_tokens']==''
        row={key:float(value) for key,value in record.items()
             if key not in ('status','sampling_unit','provider_tokens')}
        assert all(math.isfinite(value) for value in row.values())
        rows.append(row)
    assert {(r['length'],r['concurrency']) for r in rows}=={
        (length,concurrency) for length in (1,2,4,8,16,32) for concurrency in (1,2,4,8,16)}
    os.environ['MPLCONFIGDIR']=str(out/'mpl_cache')
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import font_manager,ft2font
    font_manager.fontManager.addfont(str(font))
    family=font_manager.FontProperties(fname=str(font)).get_name()
    titles=('(a) 任务时延','(b) 通信字节','(c) 消息代理计数')
    labels=('95分位耗时（秒）','帧与确认消息字节／链','消息代理词元数／链')
    notes='误差线：采后逐格95%置信区间；非同时置信带'
    required=''.join(titles+labels)+notes+'链长并发'
    charmap=ft2font.FT2Font(str(font)).get_charmap()
    assert all(ord(char) in charmap for char in required if '\u4e00'<=char<='\u9fff')
    matplotlib.rcParams.update({'font.family':family,'font.size':7.5,'axes.labelsize':7.5,
        'axes.titlesize':8,'xtick.labelsize':7,'ytick.labelsize':7,'legend.fontsize':7.5,
        'axes.unicode_minus':True,'pdf.fonttype':42,'svg.fonttype':'path',
        'axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.65})
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator,FuncFormatter
    width_mm,height_mm=175,61
    fig,axes=plt.subplots(1,3,figsize=(width_mm/25.4,height_mm/25.4))
    fig.subplots_adjust(left=.065,right=.99,bottom=.245,top=.735,wspace=.48)
    fields=(('task_latency_p95_sec','p95_ci95_low_sec','p95_ci95_high_sec'),
        ('native_observable_frame_ack_bytes_per_chain','frame_ack_bytes_per_chain_ci95_low','frame_ack_bytes_per_chain_ci95_high'),
        ('message_proxy_tokens_per_chain','message_proxy_tokens_per_chain_ci95_low','message_proxy_tokens_per_chain_ci95_high'))
    colors=('#172f50','#4677a2','#579e86','#8a699e','#bd874d')
    markers=('o','s','^','D','v');styles=('-','--',':','-.',(0,(3,1,1,1)))
    drawn=[]
    for panel,(ax,(point,low,high),title,ylabel) in enumerate(zip(axes,fields,titles,labels)):
        for index,concurrency in enumerate((1,2,4,8,16)):
            group=sorted((row for row in rows if row['concurrency']==concurrency),key=lambda row:row['length'])
            x=[row['length'] for row in group];y=[row[point] for row in group]
            ax.plot(x,y,linestyle=styles[index],color=colors[index],linewidth=.85,
                    marker=markers[index],markersize=3.3,markeredgewidth=.6,label=f'并发{concurrency}')
            for row in group:
                assert row[low]<=row[high]
                # Exact stored endpoints; do not reconstruct CI from standard errors.
                ax.vlines(row['length'],row[low],row[high],color=colors[index],linewidth=.7,zorder=2)
                ax.plot([row['length']],[row[low]],marker='_',markersize=3,color=colors[index],markeredgewidth=.7)
                ax.plot([row['length']],[row[high]],marker='_',markersize=3,color=colors[index],markeredgewidth=.7)
                drawn.append({'panel':panel,'length':row['length'],'concurrency':concurrency,
                              'source_point_field':point,'point':row[point],'low':row[low],'high':row[high]})
        ax.set_xscale('log',base=2);ax.set_xticks((1,2,4,8,16,32),labels=('1','2','4','8','16','32'))
        ax.set_xlim(.82,39);ax.set_ylim(bottom=0)
        ax.set_xlabel('链长',labelpad=2);ax.set_ylabel(ylabel,labelpad=3)
        ax.set_title(title,pad=5);ax.tick_params(length=2.5,pad=2)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
        if panel:ax.yaxis.set_major_formatter(FuncFormatter(lambda value,position:f'{value:.0f}'))
        ax.grid(axis='y',alpha=.2,linewidth=.5)
    handles,legend_labels=axes[0].get_legend_handles_labels()
    legend=fig.legend(handles,legend_labels,loc='upper center',bbox_to_anchor=(.52,.995),
               ncol=5,frameon=False,handlelength=2.3,columnspacing=1.7,handletextpad=.5)
    note=fig.text(.52,.025,notes,ha='center',va='bottom',fontsize=7)
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    canvas=fig.bbox
    checked=[]
    artists=[legend,note]
    for ax in axes:
        artists += [ax.title,ax.xaxis.label,ax.yaxis.label]+ax.get_xticklabels()+ax.get_yticklabels()
    for artist in artists:
        box=artist.get_window_extent(renderer)
        if artist.get_visible() and box.width and box.height:
            assert box.x0>=canvas.x0-.5 and box.y0>=canvas.y0-.5 and box.x1<=canvas.x1+.5 and box.y1<=canvas.y1+.5
            checked.append({'text':artist.get_text() if hasattr(artist,'get_text') else 'shared legend',
                            'bounds':[box.x0,box.y0,box.x1,box.y1]})
    files=[]
    for extension in ('png','pdf','svg'):
        path=out/f'figure5_horizontal_zh.{extension}'
        fig.savefig(path,dpi=600)
        files.append({'path':path.name,'sha256':sha(path),'bytes':path.stat().st_size})
    plt.close(fig)
    save(out/'drawn_values.json',{'status':'EXACT_SAVED_POINTS_AND_CI_DRAWN_NO_ESTIMATION','records':drawn})
    save(out/'render_receipt.json',{'status':'ACTUAL_RENDER_COMPLETED_VISUAL_QA_PENDING',
        'created_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':sha(Path(__file__)),
        'data_sha256':sha(source),'font_sha256':sha(font),'font_family':family,
        'matplotlib_version':matplotlib.__version__,'width_mm':width_mm,'height_mm':height_mm,'dpi':600,
        'layout':'1x3','shared_legend':'top, 5 columns','files':files,
        'drawn_values_sha256':sha(out/'drawn_values.json'),'bounds_checks':checked,
        'source_cells':30,'saved_metric_point_CI_triplets':len(drawn),'estimate_or_CI_recalculations':0,
        'experiment_runs':0,'model_API_calls':0,'visual_QA':'NOT_YET_PERFORMED'})


if __name__=='__main__':main()
