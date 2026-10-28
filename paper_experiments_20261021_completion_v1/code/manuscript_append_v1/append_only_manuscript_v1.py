"""Append reviewed, real supplementary content to a fresh manuscript copy.

Preparation is source-only. Run the documents edit marker immediately before
actual authoring, after the coordinator reviews the real content JSON. This
tool performs no experiment, statistical analysis, prose generation or rendering.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import zipfile

SOURCE_SHA256 = "18ca54fe5a69f8abeb3c20644bcad55cf6a19798afd0059d88c0fd1000d30289"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W, "r": R, "a": A, "wp": WP}
STYLES = {"body": "B0069", "heading1": "B0104", "heading2": "B0068",
          "abstract_zh": "B0006", "abstract_en": "B0012", "caption": "B0072"}
UNFINISHED = re.compile(r"TODO|PLACEHOLDER|\{\{|待填|待绑定|尚待统计|结果待补", re.I)


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(path):
    raw = path.read_bytes()
    return {"path": str(path.resolve()), "bytes": len(raw), "sha256": sha(raw)}


def text_value(value):
    need(isinstance(value, str) and value.strip() and not UNFINISHED.search(value),
         "Reviewed supplementary text required; unfinished values prohibited")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--content", required=True, type=Path)
    parser.add_argument("--reviewed-content-sha256", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    need(not args.out.exists() and not args.receipt.exists(), "Fresh output and receipt required")
    resolved = [p.resolve() for p in (args.source, args.content, args.out, args.receipt)]
    need(len(set(resolved)) == len(resolved), "Source, content, output and receipt must be distinct")
    need(args.out.suffix.lower() == ".docx", "Output must be a new DOCX")
    source = args.source.read_bytes()
    need(sha(source) == SOURCE_SHA256, "Original manuscript identity mismatch")
    content_raw = args.content.read_bytes()
    need(sha(content_raw) == args.reviewed_content_sha256.lower(), "Reviewed content identity mismatch")
    content = json.loads(content_raw)
    need(content["schema_version"] == "manuscript-append-content-v1", "Unsupported content schema")
    need(content["source_sha256"] == SOURCE_SHA256, "Content source identity mismatch")
    need(content["actual_results_only"] is True and content["batches_not_pooled"] is True,
         "Real, separate-batch supplement required")
    evidence = []
    for item in content["evidence"]:
        actual = identity(Path(item["path"]))
        need(actual["sha256"] == item["sha256"], "Evidence identity mismatch")
        evidence.append({**actual, "scope": item["scope"]})
    need(evidence, "Reviewed real-result evidence required")
    blocks = content["blocks"]
    need(isinstance(blocks, list) and blocks and blocks[0].get("kind") == "paragraph",
         "Supplement must start with a title paragraph")

    from lxml import etree as E
    from PIL import Image

    def canon(node):
        return E.tostring(node, method="c14n")

    def ptext(node):
        return "".join(leaf.text or "" for leaf in node.iter(f"{{{W}}}t"))

    with zipfile.ZipFile(args.source) as archive:
        infos = archive.infolist()
        need(len({i.filename for i in infos}) == len(infos), "Duplicate source ZIP entry")
        parts = {info.filename: archive.read(info) for info in infos}
    document = E.fromstring(parts["word/document.xml"])
    body = document.find("w:body", NS)
    need(body is not None, "Missing document body")
    original_children = list(body)
    need(original_children and original_children[-1].tag == f"{{{W}}}sectPr"
         and len(body.findall("w:sectPr", NS)) == 1, "Expected original final section properties")
    section = original_children[-1]
    original_blocks = [n for n in original_children if n.tag in (f"{{{W}}}p", f"{{{W}}}tbl")]
    need(len(original_blocks) == 137, "Original 137-block identity boundary changed")
    locations = {f"B{i:04d}": node for i, node in enumerate(original_blocks)}
    original_canonical = [canon(node) for node in original_children]
    original_rel = E.fromstring(parts["word/_rels/document.xml.rels"])
    old_rel_canonical = [canon(node) for node in original_rel]
    types = E.fromstring(parts["[Content_Types].xml"])
    old_type_canonical = [canon(node) for node in types]
    styles_sha = sha(parts["word/styles.xml"])
    old_media = {name: sha(raw) for name, raw in parts.items() if name.startswith("word/media/")}
    added_media = {}
    operation_log = []
    figure_records = []
    next_number = {"table": 1, "figure": 1}
    next_docpr = max([int(n.get("id", "0")) for n in document.xpath("//wp:docPr", namespaces=NS)] + [0]) + 1

    def clean_ids(node):
        # Only a new clone is cleaned; every original node remains untouched.
        for leaf in node.iter():
            for key in list(leaf.attrib):
                if key.rsplit("}", 1)[-1] in ("paraId", "textId"):
                    del leaf.attrib[key]
        for bookmark in node.xpath(".//w:bookmarkStart|.//w:bookmarkEnd", namespaces=NS):
            bookmark.getparent().remove(bookmark)
        return node

    def paragraph(text, style="body", *, template=None, new_page=False):
        text_value(text)
        source_node = template if template is not None else locations[STYLES[style]]
        need(source_node.tag == f"{{{W}}}p", "Paragraph template required")
        need(not source_node.xpath(".//w:fldChar|.//w:instrText|.//w:hyperlink|.//w:drawing|.//w:object", namespaces=NS),
             "Rich source prose cannot be used as a new text template")
        node = E.Element(f"{{{W}}}p")
        props = source_node.find("w:pPr", NS)
        if props is not None:
            props = copy.deepcopy(props)
            # Do not clone section breaks or automatic numbered-list state.
            for child in list(props):
                if child.tag in (f"{{{W}}}sectPr", f"{{{W}}}numPr", f"{{{W}}}pageBreakBefore"):
                    props.remove(child)
            node.append(props)
        elif new_page:
            props = E.SubElement(node, f"{{{W}}}pPr")
        if new_page:
            E.SubElement(props, f"{{{W}}}pageBreakBefore")
        runs = source_node.findall("w:r", NS)
        dominant = max(runs, key=lambda run: len(ptext(run)), default=None)
        run = E.SubElement(node, f"{{{W}}}r")
        rpr = dominant.find("w:rPr", NS) if dominant is not None else None
        if rpr is not None:
            run.append(copy.deepcopy(rpr))
        for i, line in enumerate(text.split("\n")):
            if i:
                E.SubElement(run, f"{{{W}}}br")
            leaf = E.SubElement(run, f"{{{W}}}t")
            leaf.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            leaf.text = line
        return clean_ids(node)

    def numbered(item, kind):
        number = f"S{next_number[kind]}"
        need(item["number"] == number, "Supplemental numbering must be sequential S1 onward")
        caption = text_value(item["caption"])
        label = "表" if kind == "table" else "图"
        need(re.match(r"^(?:补充)?" + label + r"\s*" + number + r"(?!\d)", caption),
             "Caption must carry its supplied supplemental S number")
        next_number[kind] += 1
        return caption

    def table(item):
        template = locations[item.get("template_unit", "B0058")]
        need(template.tag == f"{{{W}}}tbl", "Table template required")
        need(not template.xpath(".//w:fldChar|.//w:instrText|.//w:drawing|.//w:object|.//w:vMerge|.//w:gridSpan", namespaces=NS),
             "Only a plain unmerged source table may be cloned")
        rows = item["rows"]
        need(isinstance(rows, list) and len(rows) >= 2, "Header and at least one real table row required")
        old_rows = template.findall("w:tr", NS)
        columns = len(old_rows[0].findall("w:tc", NS))
        need(all(isinstance(row, list) and len(row) == columns for row in rows),
             "Choose a source table template with the same column count")
        node = clean_ids(copy.deepcopy(template))
        for row in node.findall("w:tr", NS):
            node.remove(row)
        for i, values in enumerate(rows):
            row = clean_ids(copy.deepcopy(old_rows[0 if i == 0 else min(i, len(old_rows) - 1)]))
            for cell, value in zip(row.findall("w:tc", NS), values):
                need(isinstance(value, str), "Table values must be reviewed formatted strings; no implicit null/zero")
                source_paragraphs = cell.findall("w:p", NS)
                need(source_paragraphs, "Table cell paragraph missing")
                new = paragraph(value, template=source_paragraphs[0])
                for old in source_paragraphs:
                    cell.remove(old)
                cell.append(new)
            node.append(row)
        return node

    def picture(item):
        nonlocal next_docpr
        path = Path(item["path"])
        actual = identity(path)
        need(actual["sha256"] == item["sha256"], "Reviewed PNG identity mismatch")
        with Image.open(path) as image:
            need(image.format == "PNG", "Only final reviewed PNG figures are accepted")
            width_px, height_px = image.size
            image.verify()
        node = clean_ids(copy.deepcopy(locations["B0081"]))
        blips = node.xpath(".//a:blip[@r:embed]", namespaces=NS)
        need(len(blips) == 1, "Expected one inline source-picture template")
        need(not node.xpath(".//wp:anchor", namespaces=NS), "Floating pictures require separate layout review")
        rid = "rIdSupplement" + str(next_number["figure"] - 1)
        while original_rel.xpath("./p:Relationship[@Id=$rid]", namespaces={"p": PKG}, rid=rid):
            rid += "x"
        filename = f"supplement_{next_number['figure'] - 1:02d}_{actual['sha256'][:20]}.png"
        name = "word/media/" + filename
        need(name not in parts and name not in added_media, "New-media name collision")
        E.SubElement(original_rel, f"{{{PKG}}}Relationship", Id=rid, Type=R + "/image", Target="media/" + filename)
        blips[0].set(f"{{{R}}}embed", rid)
        added_media[name] = path.read_bytes()
        extent = node.find(".//wp:extent", NS)
        need(extent is not None, "Picture extent missing")
        width = item.get("width_emu", int(extent.get("cx")))
        need(type(width) is int and width > 0, "Positive integer figure width required")
        height = round(width * height_px / width_px)
        extent.set("cx", str(width)); extent.set("cy", str(height))
        for size in node.xpath(".//a:xfrm/a:ext", namespaces=NS):
            size.set("cx", str(width)); size.set("cy", str(height))
        for prop in node.xpath(".//wp:docPr", namespaces=NS):
            prop.set("id", str(next_docpr)); next_docpr += 1
            prop.set("name", "Supplemental figure " + item["number"])
            prop.set("descr", text_value(item["alt_text"]))
        figure_records.append({**actual, "number": item["number"], "width_px": width_px,
                               "height_px": height_px, "width_emu": width, "height_emu": height,
                               "added_media_part": name})
        return node

    for i, item in enumerate(blocks):
        kind = item["kind"]
        if kind == "paragraph":
            nodes = [paragraph(item["text"], item.get("style", "body"), new_page=(i == 0))]
        elif kind == "table":
            caption = numbered(item, kind)
            nodes = [paragraph(caption, "caption"), table(item)]
        elif kind == "figure":
            caption = numbered(item, kind)
            nodes = [picture(item), paragraph(caption, "caption")]
        else:
            raise ValueError("Only appended paragraph/table/figure blocks are supported")
        for node in nodes:
            section.addprevious(node)
        operation_log.append({"kind": kind, "appended_nodes": len(nodes), "number": item.get("number")})

    if added_media and not types.xpath("./c:Default[@Extension='png']", namespaces={"c": CT}):
        E.SubElement(types, f"{{{CT}}}Default", Extension="png", ContentType="image/png")
    need(list(body)[:len(original_children) - 1] == original_children[:-1]
         and list(body)[-1] is section, "Original body order or section placement changed")
    need([canon(n) for n in original_children] == original_canonical,
         "An original body node changed")
    need([canon(n) for n in original_rel][:len(old_rel_canonical)] == old_rel_canonical,
         "An original relationship entry changed")
    need([canon(n) for n in types][:len(old_type_canonical)] == old_type_canonical,
         "An original content-type entry changed")
    modified = {"word/document.xml"}
    parts["word/document.xml"] = E.tostring(document, xml_declaration=True, encoding="UTF-8", standalone=True)
    if added_media:
        modified.update(("word/_rels/document.xml.rels", "[Content_Types].xml"))
        parts["word/_rels/document.xml.rels"] = E.tostring(original_rel, xml_declaration=True, encoding="UTF-8", standalone=True)
        parts["[Content_Types].xml"] = E.tostring(types, xml_declaration=True, encoding="UTF-8", standalone=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for info in infos:
            archive.writestr(info, parts[info.filename])
        for name, raw in added_media.items():
            archive.writestr(name, raw)

    # Verify the saved artifact, not only the in-memory tree.
    with zipfile.ZipFile(args.source) as before, zipfile.ZipFile(args.out) as after:
        unchanged = [name for name in before.namelist() if name not in modified]
        need(all(before.read(name) == after.read(name) for name in unchanged), "Original ZIP part changed")
        restored = E.fromstring(after.read("word/document.xml")).find("w:body", NS)
        saved_children = list(restored)
        retained = saved_children[:len(original_children) - 1] + [saved_children[-1]]
        need([canon(n) for n in retained] == original_canonical, "Saved original body identity failed")
        saved_rel = E.fromstring(after.read("word/_rels/document.xml.rels"))
        need([canon(n) for n in saved_rel][:len(old_rel_canonical)] == old_rel_canonical,
             "Saved original relationship identity failed")
        need(sha(after.read("word/styles.xml")) == styles_sha, "Original styles changed")
        need(all(sha(after.read(name)) == digest for name, digest in old_media.items()), "Old media changed")
    need(sha(args.source.read_bytes()) == SOURCE_SHA256, "Original file changed during authoring")
    receipt = {
        "schema_version": "manuscript-append-authoring-receipt-v1",
        "status": "APPENDED_PENDING_FULL_RENDER_AND_EVERY_PAGE_QA",
        "source": identity(args.source), "content": identity(args.content),
        "script": identity(Path(__file__)), "output": identity(args.out), "evidence": evidence,
        "original_body_blocks": 137, "original_body_children_including_sectPr": len(original_children),
        "original_canonical_sequence_sha256": sha(b"".join(original_canonical)),
        "original_nodes": [{"position": i, "tag": node.tag, "canonical_sha256": sha(raw)}
                           for i, (node, raw) in enumerate(zip(original_children, original_canonical))],
        "all_original_nodes_order_and_canonical_identity_verified_from_saved_output": True,
        "original_styles_sha256": styles_sha, "original_styles_bytes_preserved": True,
        "original_relationships": [{"id": node.get("Id"), "canonical_sha256": sha(raw)}
                                   for node, raw in zip(list(saved_rel)[:len(old_rel_canonical)], old_rel_canonical)],
        "original_relationship_entries_preserved": True, "original_media_sha256": old_media,
        "original_media_bytes_preserved": True, "unchanged_original_zip_parts": unchanged,
        "modified_parts_append_only": sorted(modified), "added_media": sorted(added_media),
        "operations": operation_log, "figures": figure_records,
        "appended_after_original_references_before_original_final_sectPr": True,
        "original_abstracts_formulas_references_tables_figures_and_numbering_unchanged": True,
        "rendered": False, "all_pages_visually_reviewed": False,
    }
    with args.receipt.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


if __name__ == "__main__":
    main()
