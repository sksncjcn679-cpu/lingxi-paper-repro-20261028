from pathlib import Path
from xml.etree import ElementTree as ET
import json
import urllib.request
import html

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)
FONT = 'SimSun'
W, H = 1564, 1006

COLOR = dict(ink='#253344', line='#465568', muted='#526176',
             work='#EAF3FA', work_head='#D9EAF5', work_edge='#658AA7',
             struct='#F1F9F6', struct_head='#DDF1E8', struct_edge='#598A7C',
             state='#F7F4FB', state_head='#EDE6F4', state_edge='#8C77A5',
             memory='#FFFCF1', memory_head='#FFF2CD', memory_edge='#A28E55',
             neutral='#F2F4F6', support='#F2F4F6', control='#39837D',
             payload='#7B629B', reuse='#947C3F', white='#FFFFFF')
BW = {k: '#FFFFFF' for k in COLOR}
BW.update(ink='#222222', line='#333333', muted='#444444',
          work='#FFFFFF',work_head='#F3F3F3',work_edge='#333333',
          struct='#FFFFFF',struct_head='#F3F3F3',struct_edge='#333333',
          state='#FFFFFF',state_head='#F3F3F3',state_edge='#333333',
          memory='#FFFFFF',memory_head='#F3F3F3',memory_edge='#333333',
          neutral='#F6F6F6',support='#F3F3F3',control='#333333',payload='#333333',reuse='#333333')

def fmt(x):
    return str(int(x)) if float(x).is_integer() else str(x)

def make_model(p):
    model=ET.Element('mxGraphModel', dx='1564',dy='1006',grid='1',gridSize='10',guides='1',
        tooltips='1',connect='1',arrows='1',fold='1',page='1',pageScale='1',
        pageWidth=str(W),pageHeight=str(H),math='0',shadow='0',background='#FFFFFF')
    root=ET.SubElement(model,'root')
    ET.SubElement(root,'mxCell',id='0')
    ET.SubElement(root,'mxCell',id='1',parent='0')
    pos={}
    def node(id,label,x,y,w,h,fill=None,stroke=None,size=30,parent='1',rounded=True,extra=''):
        style=(f'rounded={int(rounded)};absoluteArcSize=1;arcSize=14;whiteSpace=wrap;html=0;'
               f'fillColor={fill or p["white"]};strokeColor={stroke or p["line"]};'
               f'strokeWidth=2;fontColor={p["ink"]};fontFamily={FONT};fontSize={size};'
               f'align=center;verticalAlign=middle;spacing=6;shadow=0;{extra}')
        c=ET.SubElement(root,'mxCell',id=id,value=label,style=style,vertex='1',parent=parent)
        ET.SubElement(c,'mxGeometry',x=fmt(x),y=fmt(y),width=fmt(w),height=fmt(h),attrib={'as':'geometry'})
        px,py=pos.get(parent,(0,0,0,0))[:2]
        pos[id]=(px+x,py+y,w,h)
        return id
    def text(id,label,x,y,w,h,size=30,parent='1',extra=''):
        return node(id,label,x,y,w,h,size=size,parent=parent,extra='strokeColor=none;fillColor=none;spacing=0;'+extra)
    def group(id,x,y,w,h):
        c=ET.SubElement(root,'mxCell',id=id,value='',style='group;',vertex='1',connectable='0',parent='1')
        ET.SubElement(c,'mxGeometry',x=fmt(x),y=fmt(y),width=fmt(w),height=fmt(h),attrib={'as':'geometry'})
        pos[id]=(x,y,w,h)
    def line(id,x1,y1,x2,y2,color=None,width=1.5,parent='1',dashed=False):
        style=f'endArrow=none;startArrow=none;html=0;strokeColor={color or p["line"]};strokeWidth={width};'
        if dashed: style+='dashed=1;dashPattern=6 4;'
        c=ET.SubElement(root,'mxCell',id=id,style=style,edge='1',parent=parent)
        g=ET.SubElement(c,'mxGeometry',relative='1',attrib={'as':'geometry'})
        ET.SubElement(g,'mxPoint',x=fmt(x1),y=fmt(y1),attrib={'as':'sourcePoint'})
        ET.SubElement(g,'mxPoint',x=fmt(x2),y=fmt(y2),attrib={'as':'targetPoint'})
    def edge(id,source,target,exit=(1,.5),entry=(0,.5),points=None,color=None,dashed=False,width=2):
        style=('edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;'
               f'html=0;endArrow=block;endFill=1;endSize=9;strokeColor={color or p["line"]};'
               f'strokeWidth={width};exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;'
               f'entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;')
        if dashed: style+='dashed=1;dashPattern=6 4;'
        c=ET.SubElement(root,'mxCell',id=id,style=style,edge='1',parent='1',source=source,target=target)
        g=ET.SubElement(c,'mxGeometry',relative='1',attrib={'as':'geometry'})
        if points:
            arr=ET.SubElement(g,'Array',attrib={'as':'points'})
            for x,y in points: ET.SubElement(arr,'mxPoint',x=fmt(x),y=fmt(y))
    def panel(id,title,x,y,w,h,key):
        group(id,x,y,w,h)
        node(id+'_body','',0,0,w,h,p[key],p[key+'_edge'],parent=id)
        node(id+'_head','',2,2,w-4,71,p[key+'_head'],p[key+'_head'],parent=id,extra='strokeWidth=0;')
        text(id+'_title',title,10,9,w-20,54,34,parent=id)
        line(id+'_divider',0,74,w,74,p[key+'_edge'],1.5,parent=id)

    # Task input and result cards are native groups of editable boxes and text.
    group('input',23,41,190,240)
    node('input_body','',0,0,190,240,p['neutral'],p['line'],parent='input')
    text('input_title','任务输入',10,13,170,51,38,parent='input')
    line('input_divider',24,71,165,71,parent='input')
    for idx,label in enumerate(['目标与约束','工具与数据','日志与证据']):
        text(f'input_item_{idx}',label,12,91+idx*50,166,39,30,parent='input')

    group('workflow',241,41,1082,240)
    node('workflow_body','',0,0,1082,240,p['work'],p['work_edge'],parent='workflow')
    text('workflow_title','多智能体任务协作',20,11,1042,52,36,parent='workflow')
    roles=[('planner','规划智能体'),('retriever','检索智能体'),('executor','执行智能体'),('reviewer','审查智能体')]
    for idx,(id,label) in enumerate(roles):
        node(id,label,25+idx*257,77,219,83,p['white'],p['work_edge'],30,parent='workflow')
    node('coordination','任务编排  ·  能力发现  ·  路由调度  ·  模型网关',24,177,1034,46,
         p['work_head'],p['work_head'],29,parent='workflow',extra='strokeWidth=0;')

    group('output',1351,41,190,240)
    node('output_body','',0,0,190,240,p['neutral'],p['line'],parent='output')
    text('output_title','任务结果',10,13,170,51,38,parent='output')
    line('output_divider',24,71,165,71,parent='output')
    for idx,label in enumerate(['决策与工件','执行结果','证据记录']):
        text(f'output_item_{idx}',label,12,91+idx*50,166,39,30,parent='output')

    panel('structured','(a) 结构化通信',23,348,478,572,'struct')
    node('message','',27,99,424,200,p['white'],p['struct_edge'],parent='structured')
    text('message_title','AgentMessage',37,103,404,47,34,parent='structured',extra='fontFamily=Times New Roman;')
    line('msg_div1',43,154,435,154,p['struct_edge'],1.3,parent='structured')
    text('message_task','任务标识  ·  源/目标  ·  动作',39,160,400,41,27,parent='structured')
    line('msg_div2',43,206,435,206,p['struct_edge'],1.3,parent='structured')
    text('message_params','参数  ·  结果  ·  状态',39,211,400,41,29,parent='structured')
    line('msg_div3',43,256,435,256,p['struct_edge'],1.3,parent='structured')
    text('message_refs','state_ref  ·  artifact_refs',36,260,406,39,30,parent='structured',extra='fontFamily=Times New Roman;')
    node('schema','模式校验',35,345,172,72,p['white'],p['struct_edge'],29,parent='structured')
    node('route','路由传递',271,345,171,72,p['white'],p['struct_edge'],29,parent='structured')
    text('uds','进程内/UDS控制通道',42,438,394,41,29,parent='structured')
    node('msg_note','控制消息承载意图、参数与引用',27,495,424,56,p['struct_head'],p['struct_edge'],26,
         parent='structured',extra='strokeWidth=1;')

    panel('state','(b) 非文本状态交换',543,348,478,572,'state')
    node('producer','生产智能体',27,110,174,77,p['white'],p['state_edge'],29,parent='state')
    node('consumer','消费智能体',277,110,174,77,p['white'],p['state_edge'],29,parent='state')
    text('descriptor_label','描述符/引用',142,188,194,41,27,parent='state')
    node('buffer','',111,253,256,136,p['white'],p['state_edge'],parent='state')
    text('buffer_title','状态缓冲区',122,267,234,40,32,parent='state')
    line('buffer_div',147,313,330,313,p['state_edge'],1.3,parent='state')
    text('buffer_content','数组  ·  结构化对象',115,328,249,40,27,parent='state')
    node('metadata','shape · dtype · size · offset',27,414,424,55,p['white'],p['state_edge'],30,parent='state',extra='fontFamily=Times New Roman;spacing=2;')
    text('lifecycle','所有权  ·  生命周期  ·  释放',27,476,424,36,28,parent='state')
    node('backends','copy / SHM / memfd',27,513,424,40,p['state_head'],p['state_edge'],30,
         parent='state',extra='strokeWidth=1;fontFamily=Times New Roman;')

    panel('memory','(c) 共享记忆复用',1062,348,478,572,'memory')
    node('query','查询',25,112,174,73,p['white'],p['memory_edge'],32,parent='memory')
    node('retrieval','混合检索',255,112,195,73,p['white'],p['memory_edge'],30,parent='memory')
    node('feedback','结果反馈',25,271,174,74,p['white'],p['memory_edge'],30,parent='memory')
    node('adoption','采用判别',255,271,195,74,p['white'],p['memory_edge'],30,parent='memory')
    text('candidate_label','候选记忆',200,208,140,39,27,parent='memory')
    node('memory_store','',27,429,423,100,p['white'],p['memory_edge'],parent='memory')
    text('store_title','持久记忆库',40,436,397,42,32,parent='memory')
    text('store_indexes','关键词  ·  标签  ·  向量索引',37,482,403,37,28,parent='memory')
    text('reuse_label','经验复用',282,386,139,38,27,parent='memory')

    node('support','运行支撑：Linux/openEuler  ·  工件存储  ·  工具执行  ·  检查点恢复',
         23,945,1518,45,p['support'],p['line'],28,extra='strokeWidth=1.5;')

    # Native connectors bind to shapes, so moving a node preserves its relationships.
    edge('flow_input','input_body','planner',exit=(1,.5),entry=(0,.5))
    for (src,_),(tgt,_) in zip(roles,roles[1:]): edge('flow_'+src,src,tgt)
    edge('flow_output','reviewer','output_body',exit=(1,.5),entry=(0,.5))
    edge('runtime_structured','workflow_body','structured_body',exit=(.0194,1),entry=(.5,0),color=p['control'])
    edge('runtime_state','workflow_body','state_body',exit=(.5,1),entry=(.5,0),color=p['payload'])
    edge('runtime_memory','workflow_body','memory_body',exit=(.980,1),entry=(.5,0),color=p['reuse'])
    edge('message_validation','message','schema',exit=(.224,1),entry=(.5,0),color=p['control'])
    edge('schema_route','schema','route',color=p['control'])
    edge('state_descriptor','producer','consumer',dashed=True,color=p['control'])
    edge('state_write','producer','buffer',exit=(.5,1),entry=(.08,0),
         points=[(657,584),(674.48,584)],color=p['payload'])
    edge('state_consume','buffer','consumer',exit=(.92,0),entry=(.5,1),
         points=[(889.52,584),(907,584)],color=p['payload'])
    edge('reference_association','message','buffer',exit=(1,.9),entry=(0,.53),
         points=[(525,628),(525,673)],dashed=True,color=p['control'])
    text('association_label','引用关联',542,628,112,35,26,extra=f'labelBackgroundColor={p["white"]};')
    edge('memory_query','query','retrieval',color=p['reuse'])
    edge('candidate_adoption','retrieval','adoption',exit=(.5,1),entry=(.5,0),color=p['reuse'])
    edge('adoption_feedback','adoption','feedback',exit=(0,.5),entry=(1,.5),color=p['reuse'])
    edge('feedback_persist','feedback','memory_store',exit=(.5,1),entry=(.2,0),color=p['reuse'])
    edge('memory_reuse','memory_store','retrieval',exit=(.815,0),entry=(1,.5),
         points=[(1434,734),(1526,734),(1526,497)],dashed=True,color=p['reuse'])
    return model

models=[('论文彩色版',make_model(COLOR)),('原图黑白版',make_model(BW))]
mx=ET.Element('mxfile',host='app.diagrams.net',modified='2026-10-21T00:00:00.000Z',agent='Codex',
              version='24.7.17',type='device',pages='2')
for i,(name,model) in enumerate(models):
    d=ET.SubElement(mx,'diagram',id=f'framework-page-{i+1}',name=name)
    d.append(model)
ET.indent(mx)
target=OUT/'多智能体协作框架_可编辑.drawio'
ET.ElementTree(mx).write(target,encoding='utf-8',xml_declaration=True)

# The official diagrams.net viewer renders the same native mxGraph model locally.
viewer=OUT/'qa'/'viewer-static.min.js'
viewer.parent.mkdir(exist_ok=True)
if not viewer.exists():
    req=urllib.request.Request('https://viewer.diagrams.net/js/viewer-static.min.js',headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=45) as response: viewer.write_bytes(response.read())
for i,(name,model) in enumerate(models):
    xml=ET.tostring(model,encoding='unicode')
    config=json.dumps({'highlight':'#000000','nav':False,'resize':False,'toolbar':'','edit':None,
                       'xml':xml},ensure_ascii=False)
    page='<!DOCTYPE html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:white} .mxgraph{width:1564px;height:1006px;}</style></head><body>'
    page+=f'<div class="mxgraph" data-mxgraph="{html.escape(config,quote=True)}"></div>'
    page+='<script>window.onDrawioViewerLoad=function(){GraphViewer.createViewerForElement(document.querySelector(".mxgraph"),function(v){window.qaViewer=v;});};</script>'
    page+='<script src="viewer-static.min.js"></script></body></html>'
    (OUT/'qa'/f'page-{i+1}.html').write_text(page,encoding='utf-8')

cells=models[0][1].find('root').findall('mxCell')
ids={c.get('id') for c in cells}
for c in cells:
    for attr in ('parent','source','target'):
        ref=c.get(attr)
        if ref is not None: assert ref in ids,(c.get('id'),attr,ref)
assert not any('image=' in c.get('style','') for c in cells)
report={'source_image':'source/image78.png','pages':[x[0] for x in models],
        'cells_per_page':len(cells),'native_shapes_and_text':sum(c.get('vertex')=='1' for c in cells),
        'native_edges':sum(c.get('edge')=='1' for c in cells),'embedded_raster_images':0,
        'all_connector_references_valid':True,'canvas':[W,H],
        'palette_reference':'https://my.feishu.cn/wiki/RUKyw1TQuiOGyYkZEQjcQQLunMc'}
(OUT/'qa'/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))
print(str(target).encode('unicode_escape').decode())
