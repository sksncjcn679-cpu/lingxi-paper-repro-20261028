"""Rebuild the image-first approved reference-style composition as native mxGraph."""
from pathlib import Path
from xml.etree import ElementTree as E
import ast,base64,hashlib,html,json,shutil
from fontTools.ttLib import TTFont
from fontTools import subset

OUT=Path(__file__).resolve().parent
OLD=OUT.parent/'figure1_framework'
QA=OUT/'qa';QA.mkdir(exist_ok=True)
W,H=1680,945
FONT='SimSun, Noto Sans SC'
INK,GRAY='#111111','#777777'
M=E.Element('mxGraphModel',dx=str(W),dy=str(H),grid='1',gridSize='10',page='1',pageScale='1',pageWidth=str(W),pageHeight=str(H),math='0',shadow='0',background='#FFFFFF')
R=E.SubElement(M,'root');E.SubElement(R,'mxCell',id='0');E.SubElement(R,'mxCell',id='1',parent='0')
positions={}
# Reuse only pure native-shape helpers, never execute an earlier builder.
old_source=(OLD/'build_framework.py').read_text(encoding='utf-8')
helper_names={'cell','text','group','path','rect','ellipse','icon'}
for definition in ast.parse(old_source).body:
 if isinstance(definition,ast.FunctionDef) and definition.name in helper_names:
  exec(compile(ast.Module(body=[definition],type_ignores=[]),'<native-shape-helper>','exec'),globals())

def box(id,x,y,w,h,rounded=True,dashed=False):
 return rect(id,x,y,w,h,style=('rounded=1;arcSize=7;' if rounded else '')+('dashed=1;dashPattern=6 4;' if dashed else ''))
def label(id,s,x,y,w,h=38,size=28,bold=False,align='center'):
 return text(id,s,x,y,w,h,size,'align='+align+';'+('fontStyle=1;' if bold else ''))
def line(id,pts,arrow=False,dashed=False,extra=''):
 return path(id,pts,'control' if arrow else 'plain',extra=('dashed=1;dashPattern=5 4;' if dashed else '')+extra)
def grid(id,x,y,w=76,h=76,rows=3,cols=3):
 box(id+'_body',x,y,w,h,False)
 for j in range(1,cols):line(id+'_v'+str(j),[(x+j*w/cols,y),(x+j*w/cols,y+h)])
 for j in range(1,rows):line(id+'_h'+str(j),[(x,y+j*h/rows),(x+w,y+j*h/rows)])
def db(id,x,y,w=78,h=92):
 ellipse(id+'_bottom',x,y+h-24,w,24)
 rect(id+'_cover',x,y+12,w,h-24,style='strokeColor=none;')
 line(id+'_left',[(x,y+12),(x,y+h-12)])
 line(id+'_right',[(x+w,y+12),(x+w,y+h-12)])
 for j in range(1,3):
  path(id+'_layer'+str(j),[(x,y+j*h/3),(x+w/2,y+j*h/3+12),(x+w,y+j*h/3)],extra='curved=1;')
 ellipse(id+'_top',x,y,w,24)
def person_doc(id,x,y):
 ellipse(id+'_head',x+8,y,29,29)
 path(id+'_shoulders',[(x-5,y+74),(x-5,y+54),(x+7,y+37),(x+36,y+37),(x+49,y+54),(x+49,y+74),(x-5,y+74)])
 icon('task',id+'_doc',x+42,y+20,0.86)
def document_tool(id,x,y):
 icon('task',id+'_doc',x,y,1.2)
 ellipse(id+'_gear',x+52,y+50,36,36)
 ellipse(id+'_hole',x+63,y+61,14,14)
 for j,(a,b,c,d) in enumerate([(70,42,70,50),(70,86,70,94),(44,68,52,68),(88,68,96,68),(50,48,56,54),(84,82,90,88),(50,88,56,82),(84,54,90,48)]):
  line(id+'_tick'+str(j),[(x+a,y+b),(x+c,y+d)],extra='strokeWidth=3;')
def reference_icon(id,x,y):
 ellipse(id+'_ring1',x,y+26,44,24)
 ellipse(id+'_ring2',x+23,y+4,44,24)
 line(id+'_bridge',[(x+24,y+34),(x+43,y+17)],extra='strokeWidth=4;')
 grid(id+'_grid',x+89,y,56,62,3,3)
def search_tree(id,x,y):
 ellipse(id+'_top',x+19,y+20,13,13)
 ellipse(id+'_left',x,y+62,13,13)
 ellipse(id+'_right',x+41,y+62,13,13)
 line(id+'_l',[(x+25,y+33),(x+6,y+62)])
 line(id+'_r',[(x+25,y+33),(x+47,y+62)])
 icon('search',id+'_search',x+76,y+15,1)
def feedback(id,x,y):
 path(id+'_up',[(x+7,y+54),(x+4,y+18),(x+41,y-1),(x+72,y+26)],'control',extra='curved=1;')
 path(id+'_down',[(x+76,y+28),(x+76,y+70),(x+35,y+91),(x+8,y+65)],'control',extra='curved=1;')
 line(id+'_check',[(x+24,y+43),(x+36,y+56),(x+60,y+31)],extra='strokeWidth=3;')

rect('canvas',0,0,W,H,style='strokeColor=none;')
# Left: reference-like stacked input card with an internal dashed footer.
box('in_frame',25,65,245,780)
label('in_heading','任务输入',40,88,215,48,34,True)
person_doc('in_task',103,183)
label('in_task_label','目标与约束',40,276,215,42,30)
document_tool('in_tool',97,360)
label('in_tool_label','工具与工件',40,470,215,42,30)
icon('task','in_log',100,548,1.3)
label('in_log_label','日志与证据',40,656,215,42,30)
line('in_divider',[(25,735),(270,735)],dashed=True)
label('in_footer','统一任务事实',40,773,215,45,30,True)

# Middle upper: the two information pools and the four-role coordination flow.
box('up_frame',420,40,825,385,dashed=True)
label('up_heading','协作编排与上下文组织层',440,55,785,47,34,True)
box('up_task_card',442,120,375,220)
box('up_context_card',842,120,375,220)
label('up_task_title','任务信息',457,136,345,42,30,True)
label('up_context_title','状态与记忆信息',857,136,345,42,30,True)
for j,s in enumerate(['• 任务标识 τ','• 源与目标 i, j','• 动作 a 与参数 p']):
 label('up_task_line'+str(j),s,478,186+j*46,320,40,28,align='left')
for j,s in enumerate(['• 状态引用 ρ','• 工件引用 A','• 候选集合 Hₖ(q)']):
 label('up_context_line'+str(j),s,870,186+j*46,325,40,28,align='left')
label('up_roles','规划 → 检索 → 执行 → 审查',454,360,755,46,33,True)

# Middle lower: compact icon-bearing mechanism cards, as in the user's reference.
box('lo_frame',375,490,890,370,dashed=True)
label('lo_heading','结构化通信、状态交接与记忆机制层',391,504,858,48,32,True)
cards=[(393,'消息编解码'),(608,'状态引用交接'),(823,'混合记忆检索'),(1038,'采用与反馈')]
for j,(x,title) in enumerate(cards):
 box('lo_card'+str(j),x,560,205,238)
 label('lo_title'+str(j),title,x+6,572,193,38,28,True)
icon('message','lo_message',416,631,1.05)
grid('lo_message_grid',516,637,45,51)
reference_icon('lo_reference',628,632)
search_tree('lo_search',847,620)
feedback('lo_feedback',1098,628)
for j,(s,sym) in enumerate([('模式校验与路由','mᵢⱼ'),('描述符与生命周期','ρ → d'),('关键词/标签/向量','Hₖ(q)'),('采用记录/效果归因','')]):
 x=cards[j][0]
 label('lo_subtitle'+str(j),s,x+4,714,197,40,23 if j in (2,3) else 24)
 if sym:label('lo_symbol'+str(j),sym,x+5,755,195,35,29)
label('lo_footer','消息携带意图与引用，载荷和记忆分开管理',390,813,860,36,29)

# Right: distinct resources, never a GPU/CPU or state-to-memory tier transition.
box('st_frame',1380,65,275,360)
label('st_title','状态缓冲区',1394,88,247,45,33,True)
grid('st_grid',1474,153,85,85)
for j,s in enumerate(['• 数组与结构化对象','• 复制 / SHM / memfd','• 所有权与生命周期']):
 label('st_line'+str(j),s,1398,270+j*43,247,38,24,align='left')
box('me_frame',1380,505,275,340)
label('me_title','持久记忆库',1394,524,247,45,33,True)
db('me_database',1480,585,76,94)
for j,s in enumerate(['• SQLite 元数据','• 关键词/标签/向量索引','• 采用记录与归因反馈']):
 label('me_line'+str(j),s,1392,703+j*40,257,37,23,align='left')

# Bind the four major numbered links to their native group frames.
path('link_input',[(270,213),(413,213)],'control',source='in_frame',target='up_frame',extra='exitX=1;exitY=0.18974;entryX=0;entryY=0.44935;')
label('link_number1','①',310,225,60,36,32)
label('link_label1','任务进入与\n角色分派',277,272,136,67,26)
path('link_mechanism',[(803,425),(803,485)],'control',source='up_frame',target='lo_frame',extra='exitX=0.46424;exitY=1;entryX=0.4809;entryY=0;')
label('link_number2','②',738,437,58,37,32)
label('link_label2','消息构造与机制调用',816,440,370,34,26,align='left')
path('link_state',[(1245,213),(1375,213)],'control',source='up_frame',target='st_frame',extra='exitX=1;exitY=0.44935;entryX=0;entryY=0.41111;')
label('link_number3','③',1282,225,60,36,32)
label('link_label3','引用传递与\n载荷访问',1249,270,128,67,25)
path('link_memory_write',[(1265,710),(1374,710)],'control',source='lo_frame',target='me_frame',extra='exitX=1;exitY=0.59459;entryX=0;entryY=0.60294;')
path('link_memory_read',[(1380,735),(1270,735)],'control',source='me_frame',target='lo_frame',extra='exitX=0;exitY=0.67647;entryX=1;entryY=0.66216;')
label('link_number4','④',1290,591,60,37,32)
label('link_label4','检索读取\n反馈写回',1269,635,109,67,25)
label('caption','图1  多智能体协作的结构化交互、状态交换与共享记忆机制',235,887,1235,42,31,True)

# Group every compartment so it moves as a real editable unit.
containers=[('input_group','in_',25,65,245,780),('upper_group','up_',420,40,825,385),('lower_group','lo_',375,490,890,370),('state_group','st_',1380,65,275,360),('memory_group','me_',1380,505,275,340)]
for gid,prefix,gx,gy,gw,gh in containers:
 group(gid,gx,gy,gw,gh)
 for c in list(R):
  if not c.get('id','').startswith(prefix) or c.get('parent')!='1':continue
  c.set('parent',gid);g=c.find('mxGeometry')
  if c.get('vertex')=='1':
   g.set('x',str(float(g.get('x','0'))-gx));g.set('y',str(float(g.get('y','0'))-gy))
  elif c.get('edge')=='1':
   for q in g.iter('mxPoint'):
    q.set('x',str(float(q.get('x','0'))-gx));q.set('y',str(float(q.get('y','0'))-gy))

mx=E.Element('mxfile',host='app.diagrams.net',agent='Codex',version='24.7.17',type='device',pages='1')
E.SubElement(mx,'diagram',id='figure1-reference-style-20261022-v3',name='图1 参考样式黑白新版').append(M)
E.indent(mx);target=OUT/'figure1_framework.drawio'
E.ElementTree(mx).write(target,encoding='utf-8',xml_declaration=True)

# Embed small glyph subsets, rather than full CJK fonts, for a compact SVG/PDF.
texts=[c.get('value','') for c in R if c.get('value')]
needed=''.join(texts)+'编辑验证'
font_specs=[('SimSun',Path('C:/Windows/Fonts/simsun.ttc'),0),('Noto Sans SC',OLD/'qa/framework_noto_regular.ttf',None)]
css=''
fonts=[]
for i,(family,src,number) in enumerate(font_specs):
 font=TTFont(src,fontNumber=number) if number is not None else TTFont(src)
 opts=subset.Options();opts.layout_features=['*'];opts.name_IDs=['*'];opts.name_legacy=True
 sub=subset.Subsetter(options=opts);sub.populate(text=needed);sub.subset(font)
 dst=QA/f'font_subset_{i}.ttf';font.save(dst)
 encoded=base64.b64encode(dst.read_bytes()).decode('ascii')
 css+="@font-face{font-family:'"+family+"';src:url(data:font/ttf;base64,"+encoded+") format('truetype');font-style:normal;font-weight:400;}"
 fonts.append({'family':family,'bytes':dst.stat().st_size,'sha256':hashlib.sha256(dst.read_bytes()).hexdigest()})
shutil.copyfile(OLD/'qa/viewer-static.min.js',QA/'viewer-static.min.js')
cfg=json.dumps({'nav':False,'resize':False,'toolbar':'','edit':None,'xml':E.tostring(M,encoding='unicode')},ensure_ascii=False)
page='<!doctype html><html><head><meta charset="utf-8"><style>'+css+f'html,body{{margin:0;background:white;width:{W}px;height:{H}px;}}.mxgraph{{width:{W}px;height:{H}px;}}</style></head><body>'
page+='<div class="mxgraph" data-mxgraph="'+html.escape(cfg,quote=True)+'"></div><script>window.onDrawioViewerLoad=function(){GraphViewer.createViewerForElement(document.querySelector(".mxgraph"),v=>window.qaViewer=v);};</script><script src="viewer-static.min.js"></script></body></html>'
(QA/'native_viewer.html').write_text(page,encoding='utf-8');(QA/'font_css.txt').write_text(css,encoding='utf-8')
cells=R.findall('mxCell');ids=[c.get('id') for c in cells];assert len(ids)==len(set(ids))
for c in cells:
 for field in ['parent','source','target']:
  if c.get(field):assert c.get(field) in ids,(c.get('id'),field)
assert not any('image=' in c.get('style','') for c in cells)
caption='图1 多智能体协作的结构化交互、状态交换与共享记忆机制。左侧为统一任务输入；中间上层组织任务、状态和记忆上下文并编排规划、检索、执行、审查四角色，下层展开消息编解码、状态引用交接、混合检索与采用反馈；右侧分别为状态缓冲区和持久记忆库。①为任务进入与角色分派，②为消息构造与机制调用，③为引用传递与载荷访问，④为记忆检索读取和可归因反馈写回。mᵢⱼ为结构化消息核心字段的投影，ρ为状态引用，d为关联的共享数组描述符，Hₖ(q)为查询返回的候选集合。编号箭头包含控制与本地逻辑调用，不将所有路径解释为网络消息；候选检索与执行采用仍分别记录。状态资源与持久记忆用途不同，图中不设置两者间的直接迁移关系。'
(OUT/'figure1_caption.txt').write_text(caption+'\n',encoding='utf-8')
receipt={'status':'NATIVE_REFERENCE_STYLE_BUILD_COMPLETE','canvas':[W,H],'native_vertices':sum(c.get('vertex')=='1' for c in cells),'native_edges':sum(c.get('edge')=='1' for c in cells),'compartment_groups':len(containers),'embedded_raster_cells':0,'minimum_body_text_px':23,'fonts':fonts,'drawio_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'reference_bitmap':'../figure1_reference_style_image_v2/图1_参考样式_生图版.png','old_versions_modified':False,'figure5_modified':False}
(QA/'build_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(receipt,ensure_ascii=False))
