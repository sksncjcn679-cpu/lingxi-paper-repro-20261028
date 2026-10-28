"""Build a single-page native mxGraph figure, without contacting any service."""
from pathlib import Path
from xml.etree import ElementTree as E
import base64, hashlib, html, json, shutil

OUT = Path(__file__).resolve().parent
W, H = 1600, 900
FONT = 'Noto Sans SC'
INK, GRAY = '#111111', '#777777'
SOURCE_FONT = Path('E:/codex_rebuilds/chinese_presentation_inputs_v2/NotoSansSC-Regular.ttf')
OLD_VIEWER = OUT.parents[1] / 'drawio_framework_20261021/qa/viewer-static.min.js'
QA = OUT / 'qa'
QA.mkdir(exist_ok=True)
M = E.Element('mxGraphModel', dx=str(W), dy=str(H), grid='1', gridSize='10',
    page='1', pageScale='1', pageWidth=str(W), pageHeight=str(H), math='0',
    shadow='0', background='#FFFFFF')
R = E.SubElement(M, 'root')
E.SubElement(R, 'mxCell', id='0')
E.SubElement(R, 'mxCell', id='1', parent='0')
positions = {}

def cell(id, label, x, y, w, h, style='', parent='1', size=26):
    c = E.SubElement(R, 'mxCell', id=id, value=label, vertex='1', parent=parent,
        style=f'html=0;whiteSpace=wrap;rounded=0;fillColor=#FFFFFF;strokeColor={INK};'
        f'strokeWidth=1.8;fontColor={INK};fontFamily={FONT};fontSize={size};'
        'align=center;verticalAlign=middle;spacing=0;shadow=0;' + style)
    E.SubElement(c, 'mxGeometry', x=str(x), y=str(y), width=str(w), height=str(h), attrib={'as':'geometry'})
    positions[id]=(x,y,w,h)
    return id

def text(id, label, x, y, w, h=40, size=26, style='', parent='1'):
    return cell(id,label,x,y,w,h,'strokeColor=none;fillColor=none;'+style,parent,size)

def group(id,x,y,w,h):
    return cell(id,'',x,y,w,h,'group;strokeColor=none;fillColor=none;')

def path(id, pts, kind='plain', parent='1', source=None, target=None, extra=''):
    patterns={'plain':'endArrow=none;', 'control':'endArrow=block;endFill=1;endSize=9;',
              'data':'dashed=1;dashPattern=8 4;endArrow=block;endFill=0;endSize=10;strokeWidth=2.2;',
              'feedback':'dashed=1;dashPattern=2 5;endArrow=open;endFill=0;endSize=10;strokeWidth=2;'}
    at={'id':id,'style':f'html=0;rounded=0;strokeColor={INK};strokeWidth=1.8;'
        'startArrow=none;'+patterns[kind]+extra,'edge':'1','parent':parent}
    if source: at['source']=source
    if target: at['target']=target
    c=E.SubElement(R,'mxCell',**at)
    g=E.SubElement(c,'mxGeometry',relative='1',attrib={'as':'geometry'})
    E.SubElement(g,'mxPoint',x=str(pts[0][0]),y=str(pts[0][1]),attrib={'as':'sourcePoint'})
    E.SubElement(g,'mxPoint',x=str(pts[-1][0]),y=str(pts[-1][1]),attrib={'as':'targetPoint'})
    if len(pts)>2:
        a=E.SubElement(g,'Array',attrib={'as':'points'})
        for x,y in pts[1:-1]: E.SubElement(a,'mxPoint',x=str(x),y=str(y))
    return id

def rect(id,x,y,w,h,parent='1',style=''):
    return cell(id,'',x,y,w,h,style,parent)

def ellipse(id,x,y,w,h,parent='1'):
    return rect(id,x,y,w,h,parent,'ellipse;')

def icon(kind,id,x,y,scale=1):
    group(id,x,y,64*scale,64*scale)
    def ln(n,pts): return path(id+'_'+n,[(a*scale,b*scale) for a,b in pts],parent=id)
    def box(n,a,b,w,h,st=''): return rect(id+'_'+n,a*scale,b*scale,w*scale,h*scale,id,st)
    def cir(n,a,b,w,h): return ellipse(id+'_'+n,a*scale,b*scale,w*scale,h*scale,id)
    if kind in ('task','result','plan'):
        box('sheet',12,5,40,52)
        if kind=='plan':
            box('clip',23,1,18,9)
            for j in range(3):
                ln('tick'+str(j),[(18,20+j*12),(21,23+j*12),(25,17+j*12)])
                ln('row'+str(j),[(30,20+j*12),(44,20+j*12)])
        elif kind=='result':
            ln('check',[(20,33),(29,42),(45,24)])
            ln('line',[(20,15),(43,15)])
        else:
            for j,l in enumerate((26,34,27)): ln('row'+str(j),[(19,18+j*12),(19+l,18+j*12)])
    elif kind=='search':
        cir('lens',5,5,38,38)
        ln('handle',[(38,38),(58,58)])
        ln('mark1',[(15,24),(32,24)])
        ln('mark2',[(24,15),(24,32)])
    elif kind=='tool':
        ln('wrench',[(10,7),(10,20),(22,28),(48,54),(56,46),(30,20),(22,8),(10,7)])
        ln('jaw',[(10,7),(20,17),(28,9)])
        cir('hole',45,44,7,7)
        ln('driver',[(7,52),(36,23)])
        ln('driverhead',[(33,15),(42,7),(49,14),(40,23),(33,15)])
    elif kind=='review':
        ln('shield',[(32,2),(55,11),(52,36),(44,49),(32,59),(20,49),(12,36),(9,11),(32,2)])
        ln('check',[(20,29),(29,38),(44,21)])
    elif kind=='message':
        box('envelope',3,12,58,40)
        ln('flap',[(3,12),(32,35),(61,12)])
        ln('leftfold',[(3,52),(22,32)])
        ln('rightfold',[(61,52),(42,32)])
    return id

# The white native background fixes the exact single-page canvas in all exports.
rect('canvas',0,0,W,H,style='strokeColor=none;')
text('heading','任务协作与机制关系',38,15,430,45,30,'align=left;fontStyle=1;')
for i,(kind,label) in enumerate([('control','控制与处理流程'),('data','载荷读写'),('feedback','反馈写回')]):
    x=(800,1100,1340)[i]
    path('legend_'+kind,[(x,39),(x+65,39)],kind)
    text('legend_text_'+kind,label,x+75,19,(200,135,150)[i],40,26,'align=left;')

roles=[('task','任务',96),('plan','规划',376),('search','检索',652),
       ('tool','执行',928),('review','审查',1204),('result','结果',1490)]
for i,(kind,label,cx) in enumerate(roles):
    icon(kind,'role_'+kind,cx-32,80)
    text('role_label_'+kind,label,cx-90,152,180,42,30)
    if i<len(roles)-1:
        path('task_flow_'+str(i),[(cx+52,112),(roles[i+1][2]-52,112)],'control')
path('top_rule',[(32,218),(1568,218)],extra='strokeColor=#777777;strokeWidth=1;')
for x in (532,1066): path('separator_'+str(x),[(x,252),(x,818)],extra='strokeColor=#BBBBBB;strokeWidth=1;')

text('title_a','(a) 结构化通信',40,249,470,48,30,'align=left;fontStyle=1;')
text('title_b','(b) 描述符与状态载荷',560,249,490,48,30,'align=left;fontStyle=1;')
text('title_c','(c) 检索、采用与记忆',1090,249,480,48,30,'align=left;fontStyle=1;')

# (a) Intent and references travel as messages; payload stays in the state buffer.
rect('message_card',58,335,432,150,style='rounded=1;arcSize=5;')
icon('message','message_icon',76,353,1.05)
text('message_name','结构化消息  mᵢⱼ',160,347,305,43,29)
text('message_fields','任务与动作 · 参数',76,402,396,37,26)
text('message_results','状态引用 ρ · 工件引用',76,440,396,35,26)
cell('validate','模式校验',58,551,177,64,'rounded=1;arcSize=5;')
cell('route','路由传递',313,551,177,64,'rounded=1;arcSize=5;')
path('msg_validate',[(148,485),(148,551)],'control',source='message_card',target='validate',
    extra='exitX=0.2083;exitY=1;entryX=0.5;entryY=0;')
path('validate_route',[(235,583),(313,583)],'control',source='validate',target='route',
    extra='exitX=1;exitY=0.5;entryX=0;entryY=0.5;')
text('channel','进程内 / UDS 控制通道',50,655,450,44,27)
text('message_formula','mᵢⱼ = 〈τ, i, j, a, p, ρ, A〉',43,741,462,46,28)
text('message_note','消息携带意图与引用',48,789,450,40,26)

# (b) Descriptor transmission is distinct from actual payload write and read.
cell('producer','生产智能体',565,350,171,64,'rounded=1;arcSize=5;')
cell('consumer','消费智能体',868,350,171,64,'rounded=1;arcSize=5;')
path('descriptor_transfer',[(736,382),(868,382)],'control',source='producer',target='consumer',
    extra='exitX=1;exitY=0.5;entryX=0;entryY=0.5;')
text('descriptor_label','ρ 关联 d',736,315,133,40,26)
rect('state_buffer',650,511,303,117,style='rounded=1;arcSize=5;')
for row in range(2):
    for col in range(5):
        rect('array_'+str(row)+'_'+str(col),674+col*53,531+row*43,40,29,
            style='strokeColor=#444444;strokeWidth=1.3;')
path('state_write',[(701,414),(701,466),(741,466),(741,511)],'data',source='producer',target='state_buffer',
    extra='exitX=0.79532;exitY=1;entryX=0.30033;entryY=0;')
path('state_read',[(902,511),(902,466),(881,466),(881,414)],'data',source='state_buffer',target='consumer',
    extra='exitX=0.832;exitY=0;entryX=0.07602;entryY=1;')
text('write_label','写入载荷',558,432,145,36,26)
text('read_label','读取载荷',909,432,145,36,26)
text('buffer_label','状态缓冲区',637,640,330,43,29)
text('descriptor_fields','名称·形状·类型·字节数·偏移',556,698,492,41,26)
text('state_backends','复制 / SHM / memfd',566,746,471,42,27)
text('lifetime','所有权与生命周期约束',566,790,471,38,26)

# (c) Retrieval candidates are not automatically adopted; feedback has attribution.
cell('query','查询 q',1090,350,148,64,'rounded=1;arcSize=5;')
cell('retrieve','混合检索',1348,350,185,64,'rounded=1;arcSize=5;')
cell('candidate','候选 Hₖ(q)',1348,485,185,64,'rounded=1;arcSize=5;')
cell('adopt','执行采用',1090,485,185,64,'rounded=1;arcSize=5;')
cell('feedback','可归因反馈',1090,625,185,64,'rounded=1;arcSize=5;')
cell('memory_store','',1348,607,185,104,'strokeColor=none;fillColor=none;')
# Native ellipse/side pieces avoid a viewer-specific cylinder size parameter.
ellipse('memory_bottom',1348,682,185,30)
rect('memory_body',1348,622,185,75,style='strokeColor=none;')
path('memory_left',[(1348,622),(1348,697)])
path('memory_right',[(1533,622),(1533,697)])
ellipse('memory_top',1348,607,185,30)
text('store_label','持久记忆库',1354,640,173,42,26)
path('query_retrieve',[(1238,382),(1348,382)],'control',source='query',target='retrieve',
    extra='exitX=1;exitY=0.5;entryX=0;entryY=0.5;')
path('retrieve_candidate',[(1440,414),(1440,485)],'control',source='retrieve',target='candidate',
    extra='exitX=0.5;exitY=1;entryX=0.5;entryY=0;')
path('candidate_adopt',[(1348,517),(1275,517)],'control',source='candidate',target='adopt',
    extra='exitX=0;exitY=0.5;entryX=1;entryY=0.5;')
path('adopt_feedback',[(1182,549),(1182,625)],'feedback',source='adopt',target='feedback',
    extra='exitX=0.5;exitY=1;entryX=0.5;entryY=0;')
path('persist_feedback',[(1275,657),(1348,657)],'feedback',source='feedback',target='memory_store',
    extra='exitX=1;exitY=0.5;entryX=0;entryY=0.5;')
path('memory_retrieve',[(1533,659),(1561,659),(1561,382),(1533,382)],'control',
    source='memory_store',target='retrieve',extra='exitX=1;exitY=0.5;entryX=1;entryY=0.5;')
text('store_metadata','元数据与索引',1328,713,226,35,26)
text('retrieve_formula','Hₖ(q) = retrieve(q, k)',1092,753,454,40,28)
text('candidate_note','候选不等于采用或收益',1092,790,454,38,26)

path('footer_rule',[(32,839),(1568,839)],extra='strokeColor=#777777;strokeWidth=1;')
text('runtime','运行支撑：任务编排 · 模型网关 · 工具与工件存储 · 检查点恢复',32,851,1536,40,26)

mx=E.Element('mxfile',host='app.diagrams.net',agent='Codex',version='24.7.17',type='device',pages='1')
dia=E.SubElement(mx,'diagram',id='figure1-framework-20261022',name='图1 黑白学术框架')
dia.append(M)
E.indent(mx)
drawio=OUT/'figure1_framework.drawio'
E.ElementTree(mx).write(drawio,encoding='utf-8',xml_declaration=True)

# The reviewed static 400-weight font is embedded unchanged for offline viewing.
font_path=QA/'framework_noto_regular.ttf'
shutil.copyfile(SOURCE_FONT,font_path)
font_b64=base64.b64encode(font_path.read_bytes()).decode('ascii')
font_css=f"@font-face{{font-family:'{FONT}';src:url(data:font/ttf;base64,{font_b64}) format('truetype');font-weight:400;font-style:normal;}}"
shutil.copyfile(OLD_VIEWER,QA/'viewer-static.min.js')
cfg=json.dumps({'nav':False,'resize':False,'toolbar':'','edit':None,
    'xml':E.tostring(M,encoding='unicode')},ensure_ascii=False)
page='<!doctype html><html><head><meta charset="utf-8"><style>'+font_css+f'html,body{{margin:0;background:white;width:{W}px;height:{H}px;}} .mxgraph{{width:{W}px;height:{H}px;}}'
page+='</style></head><body><div class="mxgraph" data-mxgraph="'+html.escape(cfg,quote=True)+'"></div>'
page+='<script>window.onDrawioViewerLoad=function(){GraphViewer.createViewerForElement(document.querySelector(".mxgraph"),v=>window.qaViewer=v);};</script><script src="viewer-static.min.js"></script></body></html>'
(QA/'native_viewer.html').write_text(page,encoding='utf-8')
(QA/'font_css.txt').write_text(font_css,encoding='utf-8')

caption=('图1 多智能体协作与三机制的实现关系。上方中间四个图标分别表示规划、检索、执行与审查智能体，首尾的任务与结果图标表示输入和产物；下方分别展开结构化消息、描述符引用与状态载荷读写、候选检索及采用后的可归因反馈。'
    '实线表示控制与处理流程，其中(a)/(b)含控制消息传递，(c)为本地逻辑调用、候选选择和执行采用流程，并不把所有实线视为 AgentMessage 网络消息；长虚线表示载荷读写，点线表示依据审查结果的可归因反馈及写回。mᵢⱼ 的核心字段投影为〈τ,i,j,a,p,ρ,A〉，依次表示任务标识、源智能体、目标智能体、动作类型、输入参数、状态引用和工件引用；结果和状态字段另行记录。'
    'ρ 承载 StateRef 状态引用；d 表示相关联的共享数组描述符 SharedArrayDescriptor，其核心字段为 name、shape、dtype、nbytes 和 offset；状态引用和描述符均不等于真实载荷。'
    'Hₖ(q)=retrieve(q,k) 表示混合检索返回的候选列表，不表示已采用或已获收益。状态缓冲区保存内存载荷；持久记忆库保存 SQLite 元数据及相关索引，两者用途不同。'
    '控制通道按运行拓扑可为进程内或 UDS；SHM/memfd 状态交换适用于同一宿主的受支持进程拓扑，copy 路径仍可发生复制。图示不表示跨机共享、全程零拷贝、人工评分回路或普遍性能收益。')
(OUT/'figure1_caption.txt').write_text(caption+'\n',encoding='utf-8')

cells=R.findall('mxCell');ids={c.get('id') for c in cells}
for c in cells:
    for a in ('parent','source','target'):
        if c.get(a): assert c.get(a) in ids
assert not any('image=' in c.get('style','') for c in cells)
receipt={'operation':'native_figure_build','canvas':[W,H],'pages':1,
    'native_vertices':sum(c.get('vertex')=='1' for c in cells),
    'native_edges':sum(c.get('edge')=='1' for c in cells),
    'raster_image_cells':0,'minimum_text_px':26,'font_weight':400,
    'font_source':str(SOURCE_FONT),'font_source_sha256':hashlib.sha256(SOURCE_FONT.read_bytes()).hexdigest(),
    'font_embedded_sha256':hashlib.sha256(font_path.read_bytes()).hexdigest(),
    'drawio_sha256':hashlib.sha256(drawio.read_bytes()).hexdigest(),
    'viewer_sha256':hashlib.sha256((QA/'viewer-static.min.js').read_bytes()).hexdigest(),
    'old_sources_modified':False,'network_required':False}
(QA/'build_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(receipt,ensure_ascii=False))
