"""Layout-only repair of newly appended blocks; original source stays immutable."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from lxml import etree as E

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
SOURCE = '18ca54fe5a69f8abeb3c20644bcad55cf6a19798afd0059d88c0fd1000d30289'
CONTENT = '50cf360f72159c6e1384156451714f551febaa02bf8302bdd3968153d3e0f6a5'
WIDTHS = {'S1': [27,32,41], 'S2': [13,18,18,18,17,16],
          'S3': [12,15,15,23,23,12], 'S4': [9,12,20,20,23,16],
          'S5': [10,15,15,15,20,25], 'S6': [33,30,37],
          'S7': [6,6,10,22,28,28], 'S8': [23,30,47], 'S9': [36,44,20]}

def sha(b): return hashlib.sha256(b).hexdigest()
def canon(n): return E.tostring(n, method='c14n')
def ident(p):
    b=p.read_bytes(); return {'path':str(p.resolve()), 'bytes':len(b), 'sha256':sha(b)}
def ensure(n, tag):
    c=n.find('w:'+tag,NS)
    if c is None: c=E.SubElement(n,'{'+W+'}'+tag)
    return c
def setp(p, tag, **values):
    props=p.find('w:pPr',NS)
    if props is None: props=E.Element('{'+W+'}pPr');p.insert(0,props)
    node=ensure(props,tag)
    for k,v in values.items(): node.set('{'+W+'}'+k,str(v))
    return node

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--original',required=True,type=Path)
    ap.add_argument('--input',required=True,type=Path);ap.add_argument('--content',required=True,type=Path)
    ap.add_argument('--out',required=True,type=Path);ap.add_argument('--receipt',required=True,type=Path)
    a=ap.parse_args();assert not a.out.exists() and not a.receipt.exists()
    assert len({p.resolve() for p in (a.original,a.input,a.content,a.out,a.receipt)})==5
    assert sha(a.original.read_bytes())==SOURCE and sha(a.content.read_bytes())==CONTENT
    content=json.loads(a.content.read_bytes())
    with zipfile.ZipFile(a.input) as z:
        infos=z.infolist();parts={i.filename:z.read(i) for i in infos}
    with zipfile.ZipFile(a.original) as z:
        origdoc=E.fromstring(z.read('word/document.xml'));origbody=origdoc.find('w:body',NS)
        original=[canon(n) for n in origbody]
    doc=E.fromstring(parts['word/document.xml']);body=doc.find('w:body',NS)
    assert len(original)==138 and [canon(n) for n in list(body)[:137]+[body[-1]]]==original
    appended=list(body)[137:-1]
    texts=lambda nodes:[[n.text or '' for n in node.iter('{'+W+'}t')] for node in nodes]
    before_text=texts(appended)
    old_images=[n.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                for n in doc.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}blip')]
    section=body[-1];pg=section.find('w:pgSz',NS);m=section.find('w:pgMar',NS)
    width=int(pg.get('{'+W+'}w'))-int(m.get('{'+W+'}left'))-int(m.get('{'+W+'}right'))
    def caption(p, keep):
        setp(p,'keepNext',val='1' if keep else '0');setp(p,'keepLines',val='1')
        setp(p,'spacing',before=100,after=50);setp(p,'snapToGrid',val='0')
    def table(n, number):
        props=ensure(n,'tblPr');ensure(props,'tblLayout').set('{'+W+'}type','fixed')
        tw=ensure(props,'tblW');tw.set('{'+W+'}type','dxa');tw.set('{'+W+'}w',str(width))
        weights=WIDTHS[number];sizes=[round(width*x/sum(weights)) for x in weights];sizes[-1]+=width-sum(sizes)
        grid=ensure(n,'tblGrid')
        for child in list(grid):grid.remove(child)
        for v in sizes:E.SubElement(grid,'{'+W+'}gridCol',{'{'+W+'}w':str(v)})
        borders=ensure(props,'tblBorders')
        for child in list(borders):borders.remove(child)
        for side in ('top','left','bottom','right','insideH','insideV'):
            E.SubElement(borders,'{'+W+'}'+side,{'{'+W+'}val':'single','{'+W+'}sz':'4','{'+W+'}color':'D9D9D9'})
        margins=ensure(props,'tblCellMar')
        for side,v in (('top',30),('bottom',30),('left',65),('right',65)):
            c=ensure(margins,side);c.set('{'+W+'}w',str(v));c.set('{'+W+'}type','dxa')
        for i,row in enumerate(n.findall('w:tr',NS)):
            rp=ensure(row,'trPr')
            for c in list(rp):
                if c.tag in ('{'+W+'}trHeight','{'+W+'}tblHeader','{'+W+'}cantSplit'):rp.remove(c)
            ensure(rp,'cantSplit')
            if i==0:ensure(rp,'tblHeader')
            for j,cell in enumerate(row.findall('w:tc',NS)):
                cp=ensure(cell,'tcPr')
                for c in list(cp):
                    if c.tag=='{'+W+'}tcBorders':cp.remove(c)
                cw=ensure(cp,'tcW');cw.set('{'+W+'}type','dxa');cw.set('{'+W+'}w',str(sizes[j]))
                ensure(cp,'vAlign').set('{'+W+'}val','center')
                for p in cell.findall('w:p',NS):
                    setp(p,'ind',left=0,right=0,firstLine=0,firstLineChars=0,start=0,end=0)
                    setp(p,'spacing',before=0,after=0,line=200,lineRule='auto');setp(p,'snapToGrid',val='0')
                    setp(p,'keepNext',val='0');setp(p,'keepLines',val='1')
                    setp(p,'jc',val='left' if len(weights)==3 or j==0 else 'center')
                    for run in p.findall('w:r',NS):
                        pr=ensure(run,'rPr');ensure(pr,'sz').set('{'+W+'}val','18');ensure(pr,'szCs').set('{'+W+'}val','18')
    pos=0
    for b in content['blocks']:
        kind=b['kind'];node=appended[pos]
        if kind=='paragraph':
            setp(node,'widowControl',val='1')
            if b.get('style')=='body':
                setp(node,'spacing',line=260,lineRule='exact',after=30);setp(node,'snapToGrid',val='0')
            elif b.get('style') in ('heading1','heading2'):
                setp(node,'keepNext',val='1')
            pos+=1
        elif kind=='table':
            caption(node,True);table(appended[pos+1],b['number']);pos+=2
        else:
            assert kind=='figure';setp(node,'keepNext',val='1');setp(node,'spacing',before=0,after=30)
            caption(appended[pos+1],False);pos+=2
    assert pos==len(appended) and texts(appended)==before_text
    assert [canon(n) for n in list(body)[:137]+[body[-1]]]==original
    assert old_images==[n.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                        for n in doc.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}blip')]
    parts['word/document.xml']=E.tostring(doc,xml_declaration=True,encoding='UTF-8',standalone=True)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.receipt.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(a.out,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for info in infos:z.writestr(info,parts[info.filename])
    with zipfile.ZipFile(a.input) as before,zipfile.ZipFile(a.out) as after:
        unchanged=[name for name in before.namelist() if name!='word/document.xml']
        assert all(before.read(name)==after.read(name) for name in unchanged)
        saved=E.fromstring(after.read('word/document.xml')).find('w:body',NS)
        assert [canon(n) for n in list(saved)[:137]+[saved[-1]]]==original
        assert texts(list(saved)[137:-1])==before_text
    receipt={'status':'LAYOUT_ONLY_APPENDED_BLOCKS_REPAIRED_PENDING_RENDER_QA',
        'original':ident(a.original),'input':ident(a.input),'content':ident(a.content),'script':ident(Path(__file__)),'output':ident(a.out),
        'original_137_blocks_order_canonical_and_final_sectPr_preserved':True,
        'styles_relationships_media_and_all_non_document_parts_byte_identical_to_first_authoring':True,
        'appended_text_values_order_and_image_relationships_unchanged':True,
        'changes':['new table widths/padding/light grid; repeated headers; no row splitting or fixed heights',
                   'remove inherited per-cell last-row heavy borders in new tables only',
                   'keep new table captions with following table; keep new images with captions',
                   'new body paragraphs13pt exact leading; disable new paragraph grid snap'],
        'body_width_twips':width,'rendered':False,'visual_QA_complete':False}
    with a.receipt.open('x',encoding='utf-8') as f:json.dump(receipt,f,ensure_ascii=False,indent=2);f.write('\n')

if __name__=='__main__':main()
