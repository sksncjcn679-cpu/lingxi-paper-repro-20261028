"""Future authoring only: patch a fresh DOCX copy, preserving unrelated ZIP parts.

No statistics, model calls or rendering. Run the documents edit marker immediately
before this authoring command, only after timed experiments have actually ended.
The final patch JSON supplies reviewed prose/table values and hash-bound Chinese PNGs.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import posixpath
import re
import zipfile

SOURCE_SHA256 = "18ca54fe5a69f8abeb3c20644bcad55cf6a19798afd0059d88c0fd1000d30289"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w":W,"r":R,"m":M,"a":A,"wp":WP}
SOURCE_METADATA_UNITS = {"B0000","B0001","B0003","B0004","B0005","B0008","B0010","B0011"}
SOURCE_EQUATION_UNITS = {"B0032","B0037","B0043","B0048","B0063"}  # source formulas are drawings
UNFINISHED = re.compile(r"TODO|PLACEHOLDER|\{\{|待填|待绑定|尚待统计|结果待补",re.I)


def sha(raw): return hashlib.sha256(raw).hexdigest()
def identity(path):
    raw=path.read_bytes(); return {"path":str(path.resolve()),"bytes":len(raw),"sha256":sha(raw)}
def save_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8",newline="\n")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--units",type=Path,required=True)
    parser.add_argument("--patch",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--receipt",type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists() or args.receipt.exists() or args.out.resolve()==args.source.resolve():
        raise ValueError("fresh output/receipt required; source is preserve-only")
    from lxml import etree as E
    from PIL import Image
    source=args.source.read_bytes(); assert sha(source)==SOURCE_SHA256
    patch=json.loads(args.patch.read_text(encoding="utf-8-sig"))
    assert patch["schema_version"]=="manuscript-final-patch-v1" and patch["source_sha256"]==SOURCE_SHA256
    assert patch["timed_experiments_actual_terminal"] is True
    assert patch["batches_not_pooled"] is True
    evidence=[]
    for item in patch["evidence"]:
        actual=identity(Path(item["path"])); assert actual["sha256"]==item["sha256"]
        evidence.append({**actual,"scope":item["scope"]})
    assert any(item["scope"]=="new_independent_main_replication" for item in evidence)
    with zipfile.ZipFile(args.source) as archive:
        infos=archive.infolist(); parts={item.filename:archive.read(item) for item in infos}
    document=E.fromstring(parts["word/document.xml"])
    body=document.find("w:body",NS)
    blocks=[node for node in body if node.tag in (f"{{{W}}}p",f"{{{W}}}tbl")]
    units=json.loads(args.units.read_text(encoding="utf-8-sig"))
    # Extraction omits four image-only paragraphs but retains original body indexes.
    assert len(blocks)==137 and len(units)==133
    locations={f"B{index:04d}":node for index,node in enumerate(blocks)}
    expected={row["unit"]:row for row in units}
    def ptext(p):
        return "".join(node.text or "" if node.tag==f"{{{W}}}t" else "\t" if node.tag==f"{{{W}}}tab" else "\n"
                       for node in p.iter() if node.tag in (f"{{{W}}}t",f"{{{W}}}tab",f"{{{W}}}br",f"{{{W}}}cr"))
    def check(unit):
        node=locations[unit]
        if expected[unit]["kind"]=="paragraph": assert ptext(node)==expected[unit]["text"]
        return node
    for row in units:
        if row["kind"]=="paragraph": check(row["unit"])
    def canon(node): return E.tostring(node,method="c14n")
    protected={unit:canon(node) for unit,node in locations.items()
               if unit in SOURCE_METADATA_UNITS or unit in SOURCE_EQUATION_UNITS or unit>="B0106" or node.xpath(".//m:oMath|.//m:oMathPara",namespaces=NS)}
    original_math=[canon(node) for node in document.xpath("//m:oMath|//m:oMathPara",namespaces=NS)]
    def prose(template,text):
        assert isinstance(text,str) and not UNFINISHED.search(text)
        if template.xpath(".//m:oMath|.//m:oMathPara|.//w:fldChar|.//w:instrText|.//w:hyperlink|.//w:drawing|.//w:object",namespaces=NS):
            raise ValueError("rich source paragraph requires a separately reviewed text-node patch")
        node=copy.deepcopy(template)
        runs=template.findall("w:r",NS)
        dominant=max(runs,key=lambda run:len(ptext(run)),default=None)
        rpr=dominant.find("w:rPr",NS) if dominant is not None else None
        for child in list(node):
            if child.tag not in (f"{{{W}}}pPr",f"{{{W}}}bookmarkStart",f"{{{W}}}bookmarkEnd"):
                node.remove(child)
        run=E.SubElement(node,f"{{{W}}}r")
        if rpr is not None: run.append(copy.deepcopy(rpr))
        for index,line in enumerate(text.split("\n")):
            if index: E.SubElement(run,f"{{{W}}}br")
            leaf=E.SubElement(run,f"{{{W}}}t"); leaf.set("{http://www.w3.org/XML/1998/namespace}space","preserve");leaf.text=line
        return node
    def replace_node(original,new):
        original.getparent().replace(original,new)
    rels=E.fromstring(parts["word/_rels/document.xml.rels"])
    types=E.fromstring(parts["[Content_Types].xml"])
    modified={"word/document.xml"}; added={}; cursors={}; operation_log=[]
    def after(anchor,nodes):
        current=cursors.get(id(anchor),anchor)
        for node in nodes: current.addnext(node);current=node
        cursors[id(anchor)]=current
    def picture(template,item):
        path=Path(item["image_path"]); actual=identity(path);assert actual["sha256"]==item["image_sha256"]
        with Image.open(path) as image:
            assert image.format=="PNG";width_px,height_px=image.size
        node=copy.deepcopy(template)
        blips=node.xpath(".//a:blip[@r:embed]",namespaces=NS);assert len(blips)==1
        rid="rIdManuscript"+str(len(added)+1)
        while rels.xpath("./p:Relationship[@Id=$rid]",namespaces={"p":PKG},rid=rid): rid+="x"
        filename="manuscript_"+actual["sha256"][:20]+".png";target="media/"+filename
        E.SubElement(rels,f"{{{PKG}}}Relationship",Id=rid,Type=R+"/image",Target=target)
        blips[0].set(f"{{{R}}}embed",rid)
        added["word/"+target]=path.read_bytes()
        extent=node.find(".//wp:extent",NS);assert extent is not None
        width=int(item.get("width_emu",extent.get("cx")));height=round(width*height_px/width_px)
        extent.set("cx",str(width));extent.set("cy",str(height))
        for size in node.xpath(".//a:xfrm/a:ext",namespaces=NS): size.set("cx",str(width));size.set("cy",str(height))
        for prop in node.xpath(".//wp:docPr",namespaces=NS):
            maximum=max([int(p.get("id","0")) for p in document.xpath("//wp:docPr",namespaces=NS)]+[0])
            prop.set("id",str(maximum+1+len(added)));prop.set("descr",item["alt_text"])
        if not types.xpath("./c:Default[@Extension='png']",namespaces={"c":CT}):
            E.SubElement(types,f"{{{CT}}}Default",Extension="png",ContentType="image/png")
        modified.update(("word/_rels/document.xml.rels","[Content_Types].xml"))
        return node
    for op in patch["operations"]:
        kind=op["op"];unit=op.get("unit")
        if unit in protected: raise ValueError("protected original metadata/equation/reference slot")
        if kind=="replace_paragraph":
            old=check(unit);new=prose(old,op["text"]);replace_node(old,new);locations[unit]=new
        elif kind=="replace_table":
            table=locations[unit];assert table.tag==f"{{{W}}}tbl"
            old_rows=table.findall("w:tr",NS);rows=op["rows"];assert rows and len(rows[0])==len(old_rows[0].findall("w:tc",NS))
            if table.xpath(".//m:oMath|.//w:fldChar|.//w:drawing|.//w:bookmarkStart|.//w:bookmarkEnd",namespaces=NS): raise ValueError("protected rich table")
            originals=[copy.deepcopy(row) for row in old_rows]
            for row in old_rows: table.remove(row)
            for index,values in enumerate(rows):
                row=copy.deepcopy(originals[0 if index==0 else min(index,len(originals)-1)])
                cells=row.findall("w:tc",NS);assert len(cells)==len(values)
                for cell,text in zip(cells,values):
                    paragraphs=cell.findall("w:p",NS);assert paragraphs
                    new=prose(paragraphs[0],str(text))
                    for p in paragraphs: cell.remove(p)
                    cell.append(new)
                table.append(row)
        elif kind=="insert_paragraphs_after":
            template=check(op["template_unit"])
            if template.xpath(".//w:bookmarkStart|.//w:bookmarkEnd",namespaces=NS): raise ValueError("cloned prose template has anchored IDs")
            after(locations[unit],[prose(template,text) for text in op["texts"]])
        elif kind=="copy_history_before_references":
            anchor=locations["B0106"]
            for item in op["blocks"]:
                source_node=check(item["unit"]);node=copy.deepcopy(source_node)
                if "text" in item: node=prose(source_node,item["text"])
                if node.xpath(".//w:bookmarkStart|.//w:bookmarkEnd|.//w:fldChar|.//w:drawing",namespaces=NS):
                    raise ValueError("history copy needs explicit rich-ID treatment")
                anchor.addprevious(node)
        elif kind in ("replace_picture","insert_figure_after"):
            node=picture(locations[op.get("template_unit",unit)],op)
            if kind=="replace_picture":
                old=locations[unit];replace_node(old,node);locations[unit]=node
            else:
                caption=prose(check(op["caption_template_unit"]),op["caption"])
                after(locations[unit],[node,caption])
        else: raise ValueError("unsupported narrow operation")
        operation_log.append({"op":kind,"unit":unit})
    for unit,value in protected.items(): assert canon(locations[unit])==value and locations[unit].getparent() is body
    assert [canon(node) for node in document.xpath("//m:oMath|//m:oMathPara",namespaces=NS)]==original_math
    parts["word/document.xml"]=E.tostring(document,xml_declaration=True,encoding="UTF-8",standalone=True)
    if "word/_rels/document.xml.rels" in modified: parts["word/_rels/document.xml.rels"]=E.tostring(rels,xml_declaration=True,encoding="UTF-8",standalone=True)
    if "[Content_Types].xml" in modified: parts["[Content_Types].xml"]=E.tostring(types,xml_declaration=True,encoding="UTF-8",standalone=True)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.out,"x",compression=zipfile.ZIP_DEFLATED) as archive:
        for info in infos: archive.writestr(info,parts[info.filename])
        for name,raw in added.items(): archive.writestr(name,raw)
    with zipfile.ZipFile(args.source) as before,zipfile.ZipFile(args.out) as after_archive:
        unchanged=[name for name in before.namelist() if name not in modified]
        assert all(before.read(name)==after_archive.read(name) for name in unchanged)
    assert sha(args.source.read_bytes())==SOURCE_SHA256
    save_json(args.receipt,{"schema_version":"manuscript-authoring-receipt-v1","status":"AUTHORED_PENDING_FULL_RENDER_AND_EVERY_PAGE_QA",
                           "source":identity(args.source),"script":identity(Path(__file__)),"units":identity(args.units),"patch":identity(args.patch),
                           "evidence":evidence,"operations":operation_log,"modified_parts":sorted(modified),"added_media":sorted(added),
                           "unchanged_original_parts_verified":len(unchanged),"protected_original_units":sorted(protected),"equations_preserved":True,
                           "author_metadata_original_fields_preserved":True,"output":identity(args.out),"rendered":False,"all_pages_visually_reviewed":False})


if __name__=="__main__": main()
