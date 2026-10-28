"""Read-only QA of existing formal_report outputs; never load raw experiment data.

This module does not render figures, rerun statistics or import formal_report.
SVG data coordinates are independently calibrated from rendered tick positions.
Structural/text/numeric PASS never claims clipping, legibility or visual PASS.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
import zlib


ARMS = ("T0", "S0", "SS0", "T1", "S1", "SS1")
PAIRS = (("T0", "S0"), ("S0", "SS0"), ("T0", "T1"), ("S0", "S1"), ("SS0", "SS1"))
COLORS = {"T0": "#183153", "T1": "#183153", "S0": "#087f8c", "S1": "#087f8c", "SS0": "#6356a5", "SS1": "#6356a5"}
HEIGHTS = {1: 3.6, 2: 5.3, 3: 6.1, 4: 5.0, 5: 4.8, 6: 5.3, 7: 5.4, 8: 6.3, 9: 5.1}
SVG_NS = "{http://www.w3.org/2000/svg}"
MISSING = {"", "null", "None", "NA", "N/A"}


def sha(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        answer = float(value)
    except (ValueError, TypeError):
        return None
    return answer if math.isfinite(answer) else None


def close(a, b, *, atol=1e-8, rtol=1e-8):
    x, y = num(a), num(b)
    return x is not None and y is not None and math.isclose(x, y, abs_tol=atol, rel_tol=rtol)


def decoded(value):
    if isinstance(value, str) and value[:1] in ("[", "{"):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def equivalent(a, b):
    if a is None or a == "":
        return b is None or b == ""
    if num(a) is not None and num(b) is not None:
        return close(a, b)
    return decoded(a) == decoded(b)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        if len(set(fields)) != len(fields):
            raise ValueError("duplicate CSV headers")
        rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError("CSV row has more cells than headers")
    return fields, rows


def png_info(path):
    info = {}
    with path.open("rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError("invalid PNG signature")
        while True:
            head = stream.read(8)
            if len(head) != 8:
                raise ValueError("truncated PNG")
            length, kind = struct.unpack(">I4s", head)
            if length > 256 * 1024 * 1024:
                raise ValueError("unreasonable PNG chunk")
            payload, crc = stream.read(length), stream.read(4)
            if len(payload) != length or len(crc) != 4:
                raise ValueError("truncated PNG chunk")
            if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack(">I", crc)[0]:
                raise ValueError("PNG chunk CRC mismatch")
            if kind == b"IHDR":
                info["width_px"], info["height_px"] = struct.unpack(">II", payload[:8])
            elif kind == b"pHYs" and len(payload) == 9:
                x, y, unit = struct.unpack(">IIB", payload)
                info.update(dpi_x=x * .0254 if unit == 1 else None,
                            dpi_y=y * .0254 if unit == 1 else None)
            elif kind == b"IEND":
                return info


def pdf_info(path):
    # No converter process and no installation. Prefer an existing optional parser.
    result = {"visual_review": "PENDING_RENDER_AND_VIEW", "engine": None}
    try:
        from pypdf import PdfReader
    except ImportError:
        data = path.read_bytes()
        if not data.startswith(b"%PDF-"):
            raise ValueError("invalid PDF signature")
        match = re.search(rb"/MediaBox\s*\[\s*([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)\s*\]", data)
        if match:
            left, bottom, right, top = map(float, match.groups())
            result.update(width_pt=right-left, height_pt=top-bottom)
        result.update(engine="stdlib_signature_only", searchable_text="UNKNOWN_PDF_TEXT_ENGINE_UNAVAILABLE",
                      embedded_font_status="UNKNOWN_NOT_PARSED", pages=None)
        return result
    reader = PdfReader(path)
    result.update(engine="pypdf", pages=len(reader.pages))
    texts, fonts, images = [], [], 0
    for page in reader.pages:
        result.update(width_pt=float(page.mediabox.width), height_pt=float(page.mediabox.height))
        texts.append(page.extract_text() or "")
        resources = page.get("/Resources", {})
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        font_map = resources.get("/Font", {})
        font_map = font_map.get_object() if hasattr(font_map, "get_object") else font_map
        for name, reference in font_map.items():
            font = reference.get_object()
            descendants = font.get("/DescendantFonts", [])
            candidates = [font] + [item.get_object() for item in descendants]
            descriptors = [candidate.get("/FontDescriptor") for candidate in candidates if candidate.get("/FontDescriptor")]
            embedded = any(any(key in descriptor.get_object() for key in ("/FontFile", "/FontFile2", "/FontFile3"))
                           for descriptor in descriptors)
            fonts.append({"resource": str(name), "basefont": str(font.get("/BaseFont")),
                          "subtype": str(font.get("/Subtype")), "embedded": embedded,
                          "to_unicode": "/ToUnicode" in font})
        objects = resources.get("/XObject", {})
        objects = objects.get_object() if hasattr(objects, "get_object") else objects
        for reference in objects.values():
            if reference.get_object().get("/Subtype") == "/Image":
                images += 1
    text = "\n".join(texts)
    result.update(searchable_text="PASS" if len(re.sub(r"\s", "", text)) >= 20 else "FAIL",
                  extracted_nonspace_characters=len(re.sub(r"\s", "", text)),
                  fonts=fonts, embedded_font_status="PASS" if fonts and all(f["embedded"] for f in fonts) else "FAIL",
                  image_xobject_count=images)
    return result


def style_map(node, inherited=None):
    answer = dict(inherited or {})
    for item in node.get("style", "").split(";"):
        if ":" in item:
            key, value = item.split(":", 1)
            answer[key.strip()] = value.strip().lower()
    for key in ("fill", "stroke"):
        if node.get(key) is not None:
            answer[key] = node.get(key).lower()
    return answer


def matrix_multiply(a, b):
    return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])


def transform_matrix(text):
    result = (1., 0., 0., 1., 0., 0.)
    for name, payload in re.findall(r"([A-Za-z]+)\(([^)]*)\)", text or ""):
        values = [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", payload)]
        if name == "matrix" and len(values) == 6:
            current = tuple(values)
        elif name == "translate" and values:
            current = (1., 0., 0., 1., values[0], values[1] if len(values)>1 else 0.)
        elif name == "scale" and values:
            current = (values[0], 0., 0., values[1] if len(values)>1 else values[0], 0., 0.)
        elif name == "rotate" and values:
            angle = math.radians(values[0]); c, s = math.cos(angle), math.sin(angle)
            current = (c, s, -s, c, 0., 0.)
            if len(values) == 3:
                x, y = values[1:]
                current = matrix_multiply((1,0,0,1,x,y), matrix_multiply(current, (1,0,0,1,-x,-y)))
        else:
            raise ValueError("unsupported SVG transform: " + name)
        result = matrix_multiply(result, current)
    return result


def moved(matrix, x, y):
    return matrix[0]*x+matrix[2]*y+matrix[4], matrix[1]*x+matrix[3]*y+matrix[5]


def path_points(value):
    # Data lines/errorbars/bar rectangles emitted by Matplotlib use absolute M/L.
    # Never interpret arbitrary curves/glyph outlines as numeric data coordinates.
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", value or "")
    answer, index, command = [], 0, None
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]; index += 1
        if command in {"M", "L"} and index+1 < len(tokens) and not tokens[index].isalpha() and not tokens[index+1].isalpha():
            answer.append((float(tokens[index]), float(tokens[index+1]))); index += 2
        elif command in {"z", "Z"}:
            command = None
        else:
            break
    return answer


def label_of(tick):
    nodes = [node for node in tick.iter() if isinstance(node.tag, str) and node.tag == SVG_NS+"text"]
    return "".join("".join(node.itertext()) for node in nodes).strip().replace("−", "-")


def numeric_label(tick):
    text = label_of(tick)
    plain = num(text)
    # MathText comments preserve explicit exponents even when SVG tspans flatten.
    comments = " ".join(node.text or "" for node in tick.iter() if node.tag is ET.Comment).replace("−", "-")
    match = re.search(r"(\d+(?:\.\d+)?)\^\{(-?\d+)\}", comments)
    if match:
        return float(match.group(1))**int(match.group(2))
    # Font spans with base + superscript are available when comments were omitted.
    spans = [node for node in tick.iter() if isinstance(node.tag, str) and node.tag == SVG_NS+"tspan"]
    if len(spans) == 2:
        base, exponent = ("".join(node.itertext()).strip().replace("−", "-") for node in spans)
        if num(base) is not None and re.fullmatch(r"-?\d+", exponent or ""):
            return float(base)**int(exponent)
    return plain


class SVGData:
    def __init__(self, path):
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        self.root = ET.fromstring(path.read_bytes(), parser=parser)
        self.axes = [node for node in self.root.iter() if isinstance(node.tag, str) and re.fullmatch(r"axes_\d+", node.get("id", ""))]
        self.points, self.markers = [], []
        for axis in self.axes:
            points, markers = [], []
            for child in axis:
                name = child.get("id", "")
                if not name.startswith(("line2d_", "PathCollection_", "LineCollection_", "patch_")):
                    continue
                if not any(node.get("clip-path") for node in child.iter()):
                    continue
                def collect(node, matrix=(1.,0.,0.,1.,0.,0.), inherited=None, defs=False):
                    if not isinstance(node.tag, str):
                        return
                    defs = defs or node.tag == SVG_NS+"defs"
                    if defs:
                        return
                    current = matrix_multiply(matrix, transform_matrix(node.get("transform")))
                    styling = style_map(node, inherited)
                    if node.tag == SVG_NS+"use":
                        if num(node.get("x")) is not None and num(node.get("y")) is not None:
                            xy = moved(current, float(node.get("x")), float(node.get("y")))
                            points.append(xy); markers.append((xy, styling))
                    elif node.tag == SVG_NS+"path":
                        points.extend(moved(current, x, y) for x, y in path_points(node.get("d")))
                    for descendant in node:
                        collect(descendant, current, styling, defs)
                collect(child)
            self.points.append(points); self.markers.append(markers)

    def info(self):
        view = [float(v) for v in self.root.get("viewBox", "").split()]
        texts = [node for node in self.root.iter() if isinstance(node.tag, str) and node.tag == SVG_NS+"text"]
        sizes = []
        for node in self.root.iter():
            if not isinstance(node.tag, str):
                continue
            # Matplotlib commonly emits either font-size or CSS shorthand font.
            for value in re.findall(r"(?:font-size\s*:\s*|font\s*:\s*[^;]*?)([\d.]+)px", node.get("style", "")):
                sizes.append(float(value))
        return {"viewBox": view, "width": self.root.get("width"), "height": self.root.get("height"),
                "axes": len(self.axes), "searchable_text_nodes": len(texts),
                "font_sizes_px_as_svg_user_units": sorted(set(sizes)),
                "clip_paths": sum(node.tag == SVG_NS+"clipPath" for node in self.root.iter()),
                "visual_review": "PENDING_RENDER_AND_VIEW; text anchors/fonts/clip paths do not prove no glyph or legend clipping"}

    def calibrated(self, axis_index, dimension, *, log=False, categories=None):
        if axis_index >= len(self.axes):
            raise ValueError("SVG axis absent")
        matches = []
        for axis_group in self.axes[axis_index]:
            if not axis_group.get("id", "").startswith("matplotlib.axis_"):
                continue
            ticks = [child for child in axis_group if child.get("id", "").startswith(dimension+"tick_")]
            if not ticks:
                continue
            offset = " ".join("".join(child.itertext()) for child in axis_group
                              if child.get("id", "").startswith("text_"))
            if re.search(r"\b1e[-+]?\d|\\times|×\s*10", offset):
                raise ValueError("unsupported numeric-axis offset; no guessed calibration")
        for tick in self.axes[axis_index].iter():
            if not isinstance(tick.tag, str) or not tick.get("id", "").startswith(dimension+"tick_"):
                continue
            uses = [node for node in tick.iter() if isinstance(node.tag, str) and node.tag == SVG_NS+"use" and node.get(dimension) is not None]
            if not uses:
                continue
            label = label_of(tick)
            value = categories.get(label) if categories is not None else numeric_label(tick)
            if value is None or (log and value <= 0):
                continue
            matches.append((math.log10(value) if log else float(value), float(uses[0].get(dimension))))
        if len(matches) < 2 or len({item[0] for item in matches}) < 2:
            raise ValueError("insufficient parsed SVG "+dimension+" ticks")
        xx, yy = zip(*matches); mx, my = sum(xx)/len(xx), sum(yy)/len(yy)
        slope = sum((x-mx)*(y-my) for x,y in matches)/sum((x-mx)**2 for x in xx)
        intercept = my-slope*mx
        if max(abs(slope*x+intercept-y) for x,y in matches) > .35:
            raise ValueError("SVG ticks not consistently calibrated (possible offset/unsupported scale)")
        return lambda value: slope*(math.log10(value) if log else value)+intercept


def coordinate_recipe(number, rows):
    """Expected data coordinates from report CSV, not from the plotting module."""
    expected = []
    def point(axis, x, y, tag, *, xlog=False, ylog=False, xcats=None, ycats=None, color=None, fill=None):
        if num(x) is not None and num(y) is not None:
            expected.append({"axis": axis, "x": float(x), "y": float(y), "tag": tag,
                             "xlog": xlog, "ylog": ylog, "xcats": xcats, "ycats": ycats,
                             "color": color, "fill": fill})
    arm_index = {arm: index for index,arm in enumerate(ARMS)}
    if number == 2:
        categories = {a+"→"+b:index for index,(a,b) in enumerate(PAIRS)}
        metrics = ("provider_input_tokens", "observable_ipc_bytes", "local_state_materialized_bytes")
        for row in rows:
            metric = row.get("metric")
            if metric not in metrics:
                continue
            axis = metrics.index(metric); factor = 1 if axis == 0 else 1024
            pair = row.get("baseline", "")+"→"+row.get("target", "")
            if pair not in categories or num(row.get("value")) is None:
                continue
            base = dict(ycats=categories)
            point(axis, float(row["value"])/factor, categories[pair], row.get("kind"), **base,
                  color=COLORS.get(row.get("target")) if row.get("kind")=="effect" else None,
                  fill=("white" if row.get("target", "").endswith("0") else COLORS.get(row.get("target"))) if row.get("kind")=="effect" else None)
            if row.get("kind") == "effect":
                for bound in ("ci95_low", "ci95_high"):
                    if num(row.get(bound)) is not None:
                        point(axis, float(row[bound])/factor, categories[pair], bound, **base)
    elif number == 3:
        domains = sorted({row.get("domain") for row in rows if row.get("domain")})
        for row in rows:
            if row.get("kind") != "success_rate":
                continue
            axis, arm = domains.index(row["domain"]), row["arm"]
            for key in ("value", "ci95_low", "ci95_high"):
                point(axis, arm_index[arm], num(row.get(key)), key, xcats=arm_index,
                      color=COLORS[arm] if key=="value" else None,
                      fill=(COLORS[arm] if arm.endswith("1") else "white") if key=="value" else None)
        categories = sorted({row["failure_type"] for row in rows if row.get("kind")=="failure_type"})
        for arm in ARMS:
            left=0.
            for category in categories:
                right=left+sum(float(row["count"]) for row in rows if row.get("kind")=="failure_type" and row.get("arm")==arm and row.get("failure_type")==category)
                for x in (left,right):
                    for y in (arm_index[arm]-.4,arm_index[arm]+.4):
                        point(4,x,y,"failure_stack_corner",ycats=arm_index)
                left=right
    elif number == 4:
        categories = sorted({row["category"] for row in rows if row.get("kind")=="exclusive_mean"})
        for arm in ARMS:
            left = 0.
            for category in categories:
                selected = [r for r in rows if r.get("kind")=="exclusive_mean" and r.get("arm")==arm and r.get("category")==category]
                if not selected or num(selected[0].get("value_sec")) is None:
                    continue
                right = left+float(selected[0]["value_sec"])
                for x in (left,right):
                    for y in (arm_index[arm]-.4,arm_index[arm]+.4):
                        point(0,x,y,"stack_rectangle_corner",ycats=arm_index)
                left = right
        for row in rows:
            if row.get("kind")=="wall_percentile":
                point(1,arm_index[row["arm"]],num(row.get("value_sec")),"wall_p"+row.get("percentile", ""),xcats=arm_index)
    elif number == 5:
        for row in rows:
            for key in ("p50_ms","p95_ms"):
                point(0 if row.get("condition")=="cold" else 1,float(row["logical_bytes"])/1024,num(row.get(key)),key,xlog=True)
    elif number == 6:
        distributions = ("gaussian","sparse","heavy_tail","near_tie")
        for row in rows:
            for key in ("decision_rate","wilson95_low","wilson95_high"):
                point(distributions.index(row["distribution"]),num(row.get("encoded_embedding_scale_bytes_mean")),num(row.get(key)),key,xlog=True)
    elif number == 7:
        for axis,metric in enumerate(("recall_at_10","mrr","false_retrieval","latency_p95_ms")):
            low,high = ("latency_p95_ci95_low_ms","latency_p95_ci95_high_ms") if axis==3 else (metric+"_ci95_low",metric+"_ci95_high")
            for row in rows:
                for key in (metric,low,high):
                    point(axis,num(row.get("pool_size")),num(row.get(key)),key,xlog=True)
    elif number == 8:
        for row in rows:
            x = num(row.get("length"))
            for key in ("throughput_mean_tasks_per_sec","throughput_ci95_low","throughput_ci95_high"):
                point(0,x,num(row.get(key)),key,xlog=True)
            point(1,x,num(row.get("task_latency_p95_sec")),"latency_p95",xlog=True)
            value = num(row.get("native_observable_frame_ack_bytes_per_chain"))
            point(2,x,value/1024 if value is not None else None,"frame_ack_KiB",xlog=True)
    elif number == 9:
        systems = {"native T0":0,"AutoGen":1,"LangGraph":2}
        index = {"native_T0":0,"autogen":1,"langgraph":2}
        metrics = ("success","provider_input_tokens","wall_sec")
        for row in rows:
            for key in ("value","ci95_low","ci95_high"):
                point(metrics.index(row["metric"]),index[row["system"]],num(row.get(key)),key,xcats=systems)
    return expected


class QA:
    def __init__(self, directory):
        self.directory = directory.resolve()
        self.checks = []
        self.figures, self.tables = {}, {}
        self.loaded = {}

    def add(self, name, status, **details):
        if status not in {"PASS","FAIL","UNKNOWN","INCOMPLETE","NOT_APPLICABLE"}:
            raise ValueError(status)
        self.checks.append({"check":name,"status":status,**details})

    def expect(self, name, condition, **details):
        self.add(name,"PASS" if condition else "FAIL",**details)

    def json(self, relative):
        path = self.directory/relative
        if not path.exists():
            self.add("required_file:"+relative,"INCOMPLETE")
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON "+value)))
        except (ValueError,OSError) as exc:
            self.add("parse:"+relative,"FAIL",error=str(exc)); return {}

    def csv(self, relative):
        path = self.directory/relative
        if not path.exists():
            self.add("required_file:"+relative,"INCOMPLETE"); return [],[]
        try:
            fields,rows = read_csv(path)
            self.expect("CSV_headers:"+relative,bool(fields) and len(fields)==len(set(fields)))
            bad = [{"row":index+2,"field":key,"value":value} for index,row in enumerate(rows) for key,value in row.items()
                   if isinstance(value,str) and value.lower() in {"nan","inf","-inf","infinity","-infinity"}]
            self.expect("CSV_finite_or_explicit_missing:"+relative,not bad,examples=bad[:8])
            self.loaded[relative]=rows
            return fields,rows
        except (ValueError,OSError,csv.Error) as exc:
            self.add("parse:"+relative,"FAIL",error=str(exc)); return [],[]

    def schema(self, label, fields, required, active):
        if active:
            self.expect(label,set(required)<=set(fields),missing=sorted(set(required)-set(fields)))

    def source_contract(self, key, metadata, availability):
        paths = metadata.get("source_paths",[])
        declared = availability.get("source_sha256",{})
        complete = metadata.get("status")=="COMPLETE"
        self.expect(key+":source_paths_schema",isinstance(paths,list) and bool(paths) and all(isinstance(p,str) for p in paths))
        if not isinstance(paths,list):paths=[]
        for source in paths:
            value = declared.get(source)
            valid = isinstance(value,str) and bool(re.fullmatch(r"[a-fA-F0-9]{64}",value))
            self.add(key+":source_hash:"+source,"PASS" if valid else "FAIL" if complete else "INCOMPLETE",
                     scope="DECLARED_HASH_LINK_ONLY; external input content is not opened",declared_sha256=value)
            # Only hash matching files inside the report. Never traverse raw inputs.
            normalized = source.replace("\\","/")
            matches=[]
            for folder in ("analysis","figures","tables"):
                marker="/"+folder+"/"
                if marker in normalized:
                    candidate=self.directory/folder/normalized.rsplit(marker,1)[1]
                    if candidate.exists() and candidate.resolve().is_relative_to(self.directory):
                        matches.append(candidate)
            if len(matches)==1 and valid:
                self.expect(key+":internal_source_bytes:"+source,sha(matches[0])==value)

    def file_structure(self):
        availability = self.json("analysis/report_availability.json")
        self.availability = availability
        self.expect("registered_figure_keys",set(availability.get("figures",{}))=={f"figure_{i:02}" for i in range(1,10)})
        self.expect("registered_table_keys",set(availability.get("tables",{}))=={f"table_{i:02}" for i in range(1,7)})
        style=availability.get("plot_style",{})
        self.expect("declared_dimensions_and_DPI",close(style.get("single_column_inches"),3.35) and close(style.get("double_column_inches"),7) and style.get("raster_dpi")==600)
        self.expect("declared_arm_palette",{k:str(v).lower() for k,v in style.get("arm_colors",{}).items()}==COLORS)
        self.expect("visual_not_claimed_by_rebuilder","PENDING" in availability.get("visual_qa", ""))
        for name in ("claims_to_evidence.md","README_reproduce.md"):
            path=self.directory/name
            self.add(name+":exists","PASS" if path.is_file() and path.stat().st_size>40 else "INCOMPLETE")
        claims_path=self.directory/"claims_to_evidence.md"
        if claims_path.is_file():
            linked=re.findall(r"^\|\s*((?:figure|table)_\d{2})\s*\|\s*(COMPLETE|INCOMPLETE)\s*\|",claims_path.read_text(encoding="utf-8-sig"),re.M)
            expected={**{k:v.get("status") for k,v in availability.get("figures",{}).items()},**{k:v.get("status") for k,v in availability.get("tables",{}).items()}}
            self.expect("claims_index_matches_all_output_statuses",len(linked)==15 and len(dict(linked))==15 and dict(linked)==expected)
        schemas={1:("from","to","path","meaning"),2:("kind","metric","baseline","target","value","ci95_low","ci95_high","n_unique_tasks","n_chains","planned_pairs"),
                 3:("kind","domain","arm","value","ci95_low","ci95_high","planned_units","unique_tasks","chains"),
                 4:("kind","arm","value_sec","n_task_units"),5:("path","condition","logical_bytes","n","p50_ms","p95_ms"),
                 6:("dimension","distribution","precision","unique_states","encoded_embedding_scale_bytes_mean","decision_rate","wilson95_low","wilson95_high"),
                 7:("pool_size","mode","recall_at_10","mrr","false_retrieval","latency_p95_ms"),
                 8:("length","concurrency","recorded_batches","throughput_mean_tasks_per_sec","throughput_ci95_low","throughput_ci95_high","task_latency_p95_sec","native_observable_frame_ack_bytes_per_chain"),
                 9:("system","metric","value","ci95_low","ci95_high","status","planned_units","observed_units","unique_tasks","chains","scope")}
        for number in range(1,10):
            key=f"figure_{number:02}"
            metadata=self.json("figures/"+key+".json")
            self.figures[key]=metadata
            self.expect(key+":metadata_matches_index",metadata==availability.get("figures",{}).get(key))
            fields,rows=self.csv("figures/"+key+".csv")
            complete=metadata.get("status")=="COMPLETE"
            self.expect(key+":status_schema",metadata.get("status") in {"COMPLETE","INCOMPLETE"})
            self.expect(key+":caption_present",isinstance(metadata.get("caption"),str) and len(metadata.get("caption", ""))>40)
            self.expect(key+":CSV_row_count",len(rows)==metadata.get("csv_rows"))
            self.schema(key+":CSV_schema",fields,schemas[number],complete)
            self.source_contract(key,metadata,availability)
            names={f"{key}_{column}.{ext}" for column in ("single","double") for ext in ("pdf","svg","png")}
            declared={str(path).replace("\\","/").rsplit("/",1)[-1] for path in metadata.get("exports",[])}
            expected=names if complete else set()
            self.expect(key+":export_registry",declared==expected and len(metadata.get("exports",[]))==len(expected))
            if not complete:
                self.add(key+":data_completion","INCOMPLETE",reason="Declared incomplete, never converted into a numerical0 or visual PASS")
                self.expect(key+":no_stale_exports",not any((self.directory/"figures"/name).exists() for name in names))
                continue
            for column,width in (("single",3.35),("double",7.0)):
                height=HEIGHTS[number]*(1.12 if column=="single" else 1.)
                for ext in ("pdf","svg","png"):
                    path=self.directory/"figures"/f"{key}_{column}.{ext}"
                    if not path.exists():
                        self.add(path.name+":exists","FAIL"); continue
                    try:
                        if ext=="png":
                            info=png_info(path)
                            valid=abs(info.get("width_px",-1)-width*600)<=1 and abs(info.get("height_px",-1)-height*600)<=1 and close(info.get("dpi_x"),600,atol=.02) and close(info.get("dpi_y"),600,atol=.02)
                            self.expect(path.name+":dimensions_DPI_CRC",valid,metadata=info)
                        elif ext=="pdf":
                            info=pdf_info(path)
                            self.add(path.name+":geometry","PASS" if close(info.get("width_pt"),width*72,atol=.05) and close(info.get("height_pt"),height*72,atol=.05) else "UNKNOWN" if info.get("width_pt") is None else "FAIL",metadata=info)
                            self.add(path.name+":searchable_text",info.get("searchable_text") if info.get("searchable_text") in {"PASS","FAIL"} else "UNKNOWN")
                            self.add(path.name+":embedded_fonts",info.get("embedded_font_status") if info.get("embedded_font_status") in {"PASS","FAIL"} else "UNKNOWN")
                            self.add(path.name+":one_vector_page","PASS" if info.get("pages")==1 and info.get("image_xobject_count")==0 else "UNKNOWN" if info.get("pages") is None else "FAIL")
                        else:
                            data=SVGData(path); info=data.info(); view=info.get("viewBox",[])
                            self.expect(path.name+":canvas_and_text",len(view)==4 and close(view[2],width*72,atol=.05) and close(view[3],height*72,atol=.05) and info["searchable_text_nodes"]>0,metadata=info)
                            self.svg_coordinates(number,rows,data,path.name)
                    except Exception as exc:
                        self.add(path.name+":parse","FAIL",error=type(exc).__name__+": "+str(exc))
        table_schemas={1:("section","field","value","status","source"),2:("arm","communication","state","memory","same_task_roles_model_verifier"),
                       3:("arm","status","planned_units","observed_terminal_units","success_rate","success_ci95_low","success_ci95_high","provider_input_tokens_mean","observable_ipc_bytes_mean","wall_sec_mean"),
                       4:("section",),5:("section","status"),6:("section","status")}
        for number in range(1,7):
            key=f"table_{number:02}"; metadata=availability.get("tables",{}).get(key,{})
            fields,rows=self.csv("tables/"+key+".csv"); self.tables[key]=rows
            self.expect(key+":status_caption",metadata.get("status") in {"COMPLETE","INCOMPLETE"} and bool(metadata.get("caption")))
            self.expect(key+":row_count",len(rows)==metadata.get("rows"))
            self.schema(key+":schema",fields,table_schemas[number],metadata.get("status")=="COMPLETE")
            self.source_contract(key,metadata,availability)
            if metadata.get("status")!="COMPLETE":self.add(key+":data_completion","INCOMPLETE")
        return availability

    def svg_coordinates(self, number, rows, data, label):
        if number==1:
            self.add(label+":numeric_coordinates","NOT_APPLICABLE",reason="Schematic edges have no numeric axes; layout/arrow semantics require render/view")
            return
        recipe=coordinate_recipe(number,rows)
        cache,missing,uncalibrated,style_failures={},[],[],[]
        for item in recipe:
            axis=item["axis"]
            cache_key=(axis,item["xlog"],item["ylog"],tuple((item["xcats"] or {}).items()),tuple((item["ycats"] or {}).items()))
            if cache_key not in cache:
                try:
                    cache[cache_key]=(data.calibrated(axis,"x",log=item["xlog"],categories=item["xcats"]),
                                      data.calibrated(axis,"y",log=item["ylog"],categories=item["ycats"]))
                except ValueError as exc:
                    cache[cache_key]=None;uncalibrated.append({"axis":axis,"error":str(exc)})
            if cache[cache_key] is None:continue
            xx,yy=cache[cache_key][0](item["x"]),cache[cache_key][1](item["y"])
            matches=[xy for xy in data.points[axis] if abs(xy[0]-xx)<=.4 and abs(xy[1]-yy)<=.4]
            if not matches:
                missing.append({"axis":axis,"data_x":item["x"],"data_y":item["y"],"SVG_x":xx,"SVG_y":yy,"tag":item["tag"]})
            if item["color"]:
                def normalized(color):
                    return {"white":"#ffffff","#fff":"#ffffff"}.get(color,color)
                styled=[style for xy,style in data.markers[axis] if abs(xy[0]-xx)<=.4 and abs(xy[1]-yy)<=.4]
                if not any(normalized(style.get("stroke"))==normalized(item["color"]) and normalized(style.get("fill"))==normalized(item["fill"]) for style in styled):
                    style_failures.append({"axis":axis,"x":item["x"],"y":item["y"],"expected_stroke":item["color"],"expected_fill":item["fill"],"observed":styled[:5]})
        self.add(label+":SVG_actual_coordinates","FAIL" if missing else "UNKNOWN" if uncalibrated or not recipe else "PASS",
                 expected_positions=len(recipe),missing_count=len(missing),examples=missing[:12],uncalibrated=uncalibrated,
                 scope="Actual SVG M/L paths/use positions vs independently tick-calibrated CSV values and CI endpoints; .4 SVG point tolerance. This is not a visual or pixel review.")
        if number in {2,3}:
            self.add(label+":SVG_rendered_arm_color_and_fill","FAIL" if style_failures else "UNKNOWN" if uncalibrated else "PASS",examples=style_failures[:12],failure_count=len(style_failures),
                     scope="Actual SVG use fill/stroke at data coordinates; open white memory-off circles and colored memory-on markers. Shape legibility still pending render/view.")

    def copy_join(self, figure, table, section, keys):
        rows=self.loaded.get(f"figures/figure_{figure:02}.csv",[])
        target=[r for r in self.tables.get(f"table_{table:02}",[]) if r.get("section")==section]
        def identity(row):return tuple(row.get(k) for k in keys)
        grouped=defaultdict(list)
        for row in target:grouped[identity(row)].append(row)
        failures=[]
        for row in rows:
            options=grouped.get(identity(row),[])
            if len(options)!=1 or any(not equivalent(value,options[0].get(key)) for key,value in row.items()):
                failures.append(identity(row))
        self.add(f"figure_{figure:02}:table_{table:02}_cell_lineage","PASS" if rows and not failures and len(rows)==len(target) else "FAIL" if failures else "INCOMPLETE",
                 keys=keys,source_rows=len(rows),target_rows=len(target),examples=failures[:10],scope="Independently parsed exported CSV cells; no raw table opened")

    def numerical_lineage(self):
        stats=self.json("analysis/statistics_main.json")
        effects={}
        for stratum in stats.get("strata",[]):
            for pair in stratum.get("comparisons",[]):
                identity=(pair["baseline"],pair["target"])
                if identity in effects:self.add("primary_effect_key_uniqueness","FAIL",pair=identity)
                effects[identity]=pair
        abstract=self.json("analysis/abstract_metrics.json")
        self.expect("abstract_noninferiority_gate",abstract.get("quality_noninferiority_wording")=="WITHHELD_BY_PROSPECTIVE_GATE")
        if effects:
            pairs={(p.get("baseline"),p.get("target")):p for p in abstract.get("prespecified_pairs",[])}
            self.expect("abstract_uses_current_primary_family",set(pairs)==set(effects) and all(pairs[k].get("quality")==v.get("quality") and pairs[k].get("metrics")==v.get("metrics") for k,v in effects.items()))
        main={r.get("arm"):r for r in self.tables.get("table_03",[])}
        failures=[];checks=0
        for row in self.loaded.get("figures/figure_02.csv",[]):
            pair=effects.get((row.get("baseline"),row.get("target")),{})
            item=pair.get("metrics",{}).get(row.get("metric"),{})
            if row.get("kind")=="effect":
                comparison={"value":item.get("paired_difference"),"relative_change":item.get("relative_change"),
                            "ci95_low":(item.get("difference_ci95") or [None,None])[0],"ci95_high":(item.get("difference_ci95") or [None,None])[1],
                            "p_holm":item.get("p_holm"),"n_unique_tasks":item.get("unique_tasks"),"n_chains":item.get("chain_count"),"planned_pairs":pair.get("planned_paired_units")}
                checks+=len(comparison)
                if any(not equivalent(row.get(k),v) for k,v in comparison.items()):failures.append({"pair":row.get("comparison"),"metric":row.get("metric"),"kind":"primary_effect"})
                expected_n=108 if (row.get("baseline"),row.get("target"))==("S0","SS0") else 120
                if not close(row.get("n_unique_tasks"),expected_n):failures.append({"pair":row.get("comparison"),"kind":"original_protocol_state108_other120"})
                # All120 table means cannot directly verify a state108 primary effect.
                if expected_n==120:
                    key=row["metric"]+"_mean";a=main.get(row["baseline"],{});b=main.get(row["target"],{})
                    if num(a.get(key)) is not None and num(b.get(key)) is not None:
                        checks+=1
                        if not close(float(b[key])-float(a[key]),row.get("value")):failures.append({"pair":row.get("comparison"),"metric":row.get("metric"),"kind":"table03_mean_difference"})
            elif row.get("kind")=="chain_point":
                values=[r["difference"] for r in item.get("task_pair_distribution",[]) if r.get("chain_id")==row.get("chain_id")]
                checks+=1
                if not values or not close(sum(values)/len(values),row.get("value")):failures.append({"pair":row.get("comparison"),"kind":"chain_point","chain":row.get("chain_id")})
        self.add("figure02_primary_statistics_and_table03_lineage","FAIL" if failures else "PASS" if checks else "INCOMPLETE",numeric_checks=checks,examples=failures[:15],scope="Only protocol-aligned main comparisons; old all120 replay archive never used to select p_holm. State108 is not compared to all120 arm mean difference.")
        family=[(m.get("p_raw"),m.get("p_holm")) for pair in effects.values() for name,m in pair.get("metrics",{}).items() if name in {"provider_input_tokens","observable_ipc_bytes"}]
        if effects:
            self.expect("one_primary_ten_family_members",len(effects)==5 and len(family)==10)
            if any(num(raw) is None or num(adjusted) is None for raw,adjusted in family):
                self.add("primary_Holm_values","INCOMPLETE",reason="Unknown/missing authoritative costs or reported p values stay unknown")
            else:
                order=sorted(range(len(family)),key=lambda i:float(family[i][0]));previous=0.;bad=[]
                for rank,index in enumerate(order):
                    raw,adjusted=map(float,family[index]);expected=min(1.,max(previous,(len(family)-rank)*raw));previous=expected
                    if not 0<=raw<=1 or not close(adjusted,expected):bad.append({"raw":raw,"reported_adjusted":adjusted,"Holm_from_reported_raw":expected})
                self.expect("single_ten_Holm_arithmetic_from_reported_raw",not bad,errors=bad,scope="Small deterministic consistency check only; no bootstrap, raw effects or inference rerun")
        quality=self.loaded.get("figures/figure_03.csv",[])
        for arm,table in main.items():
            selected=[r for r in quality if r.get("arm")==arm and r.get("kind")=="success_rate"]
            if selected and all(num(r.get("planned_units")) is not None and num(r.get("value")) is not None for r in selected):
                denom=sum(float(r["planned_units"]) for r in selected)
                success=sum(float(r["planned_units"])*float(r["value"]) for r in selected)
                self.expect("figure03_table03_success:"+arm,denom>0 and close(denom,table.get("planned_units")) and close(success/denom,table.get("success_rate")))
                failed=sum(float(r["count"]) for r in quality if r.get("arm")==arm and r.get("kind")=="failure_type")
                self.expect("figure03_failure_denominator:"+arm,close(denom-success,failed,atol=1e-6))
            timing=[r for r in self.loaded.get("figures/figure_04.csv",[]) if r.get("arm")==arm]
            exclusive=[num(r.get("value_sec")) for r in timing if r.get("kind")=="exclusive_mean"]
            if exclusive and all(v is not None for v in exclusive):
                self.expect("figure04_exclusive_sum_table03_wall:"+arm,all(v>=0 for v in exclusive) and close(sum(exclusive),table.get("wall_sec_mean"),atol=1e-6))
            for row in timing:
                if row.get("kind")=="wall_percentile":
                    self.expect("figure04_table03_p"+row["percentile"]+":"+arm,close(row.get("value_sec"),table.get("wall_p"+row["percentile"]+"_sec")))
        self.copy_join(5,4,"state_paths",("path","condition","logical_bytes"))
        self.copy_join(6,4,"quantization",("dimension","distribution","precision"))
        self.copy_join(7,5,"offline_retrieval",("pool_size","mode"))
        for row in self.tables.get("table_05",[]):
            if row.get("section")=="formal_paired_memory_effect":
                pair=effects.get((row.get("baseline"),row.get("target")),{})
                quality=pair.get("quality",{});metrics=pair.get("metrics",{})
                expected={"quality_difference":quality.get("paired_difference"),
                          "quality_conservative_ci95":quality.get("quality_conservative_ci95"),
                          "quality_one_sided_lower95":quality.get("quality_difference_one_sided_lower95"),
                          "input_token_effect":metrics.get("provider_input_tokens"),"IPC_effect":metrics.get("observable_ipc_bytes")}
                self.expect("table05_primary_paired_memory:"+str(row.get("target")),bool(pair) and all(equivalent(row.get(k),v) for k,v in expected.items()))
            elif row.get("section")=="formal_main_memory" and row.get("status")=="COMPLETE":
                for numerator,denominator,rate in (("automatic_related_candidate_queries","candidate_queries","candidate_related_rate_query_denominator"),
                                                   ("actually_adopted_units","eligible_nonempty_store_units","adoption_rate_nonempty_store_denominator")):
                    n,d,r=num(row.get(numerator)),num(row.get(denominator)),num(row.get(rate))
                    valid=n is not None and d is not None and 0<=n<=d and (close(r,n/d) if d>0 else r is None)
                    self.expect("table05_denominator:"+row.get("arm", "")+":"+rate,valid)
        faults=[r for r in self.tables.get("table_06",[]) if r.get("section")=="offline_fault_recovery"]
        if faults:
            self.expect("table06_fault_trial_denominators",len(faults)==6 and all(close(r.get("planned_trials"),30) and close(r.get("recorded_trials"),30) for r in faults))
            for index,row in enumerate(faults):
                planned,successes,rate=num(row.get("planned_trials")),num(row.get("recovery_successes")),num(row.get("recovery_rate_planned_denominator"))
                self.expect("table06_fault_recovery_arithmetic:"+str(row.get("fault",index)),planned is not None and planned>0 and successes is not None and 0<=successes<=planned and close(rate,successes/planned))
        stress=self.loaded.get("figures/figure_08.csv",[])
        if self.figures.get("figure_08",{}).get("status")=="COMPLETE":
            ids={(r.get("length"),r.get("concurrency")) for r in stress}
            self.expect("figure08_full_scale_cells",len(ids)==len(stress)==30 and len({a for a,b in ids})==6 and {b for a,b in ids}=={"1","2","4","8","16"} and all(close(r.get("recorded_batches"),30) for r in stress))
        refs=self.loaded.get("figures/figure_09.csv",[])
        ext={r.get("system"):r for r in self.tables.get("table_06",[]) if r.get("section")=="real_external_reference"}
        for row in refs:
            table=ext.get(row.get("system"))
            if table and row.get("status")=="COMPLETE":
                self.expect("figure09_table06_denominators:"+row["system"]+":"+row["metric"],close(row.get("planned_units"),table.get("planned_units")) and close(row.get("observed_units"),table.get("observed_units")))
                if row["metric"]=="success" and num(table.get("planned_units")):
                    self.expect("figure09_table06_success:"+row["system"],close(row.get("value"),float(table["successful_units"])/float(table["planned_units"])))
            if row.get("status")=="COMPLETE" and num(row.get("value")) is None:
                self.add("figure09_unknown_metric:"+row["system"]+":"+row["metric"],"UNKNOWN",reason="Missing provider metric remains unknown; it must not turn into plotted0")
        self.add("external_and_stress_unavailable_independent_means","NOT_APPLICABLE",reason="Report-only QA cannot recompute raw external token/latency means, native60 subset means or stress estimates; CSV→SVG positions checked, raw-source content intentionally not opened")

    def checksums(self):
        path=self.directory/"checksums.sha256"
        if not path.exists():self.add("report_checksums","INCOMPLETE");return
        seen=set();bad=[]
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match=re.fullmatch(r"([a-fA-F0-9]{64})  (.+)",line)
            if not match:bad.append({"line":line,"reason":"invalid checksum schema"});continue
            digest,relative=match.groups();relative=relative.replace("\\","/");candidate=(self.directory/relative).resolve()
            if relative in seen or not candidate.is_relative_to(self.directory) or not candidate.is_file():
                bad.append({"path":relative,"reason":"duplicate/unsafe/missing"});continue
            seen.add(relative)
            if sha(candidate)!=digest.lower():bad.append({"path":relative,"reason":"hash mismatch"})
        actual={str(p.relative_to(self.directory)) for p in self.directory.rglob("*") if p.is_file() and p.name!="checksums.sha256"}
        # Normalize separators for a report moved between Windows and Linux.
        unlisted={v.replace("\\","/") for v in actual}-{v.replace("\\","/") for v in seen}
        self.expect("report_checksums",not bad and not unlisted,checked_files=len(seen),errors=bad[:15],unlisted=sorted(unlisted)[:15])

    def run(self):
        if not self.directory.is_dir():
            self.add("report_directory","INCOMPLETE",path=str(self.directory))
        elif not (self.directory/"analysis/report_availability.json").is_file():
            self.add("report_availability","INCOMPLETE",reason="Report has not produced its authoritative output index; no effect numbers inferred")
        else:
            for operation in (self.file_structure,self.numerical_lineage,self.checksums):
                try:operation()
                except Exception as exc:
                    self.add("QA_stage:"+operation.__name__,"FAIL",error=type(exc).__name__+": "+str(exc))
        counts=Counter(check["status"] for check in self.checks)
        status="FAIL" if counts["FAIL"] else "INCOMPLETE" if counts["INCOMPLETE"] or counts["UNKNOWN"] else "AUTOMATED_PASS_VISUAL_PENDING"
        return {"material_passport":{"artifact_type":"report_file_numeric_QA","report_directory":str(self.directory),
                    "formal_raw_read":False,"formal_statistics_rerun":False,"report_edited":False,
                    "rendering_executed":False,"model_calls":0},
                "status":status,"check_counts":dict(counts),"checks":self.checks,
                "source_content_freshness":"EXTERNAL_INPUT_HASHES_DECLARED_ONLY; separate full measurement audit required",
                "visual_qa":"PENDING_ACTUAL_RENDER_AND_VIEW",
                "visual_pending":["render both column PDF/SVG outputs with a real renderer and view actual pages",
                    "inspect glyph/font embedding appearance, minimum readable type, legends, overlaps and clipping",
                    "confirm memory circle/square shapes and greyscale distinction at final publication size",
                    "confirm CI labels/units, explanatory sample denominators, scope and errorbar interpretation"],
                "limits":["Coordinate checks calibrate actual SVG axes; unsupported/missing tick mappings are UNKNOWN, never a fabricated PASS.",
                    "Coordinate matching can confirm rendered positions/CI endpoints; it does not prove artist identity, every label, PDF/SVG visual identity or absence of overlap.",
                    "PDF searchable text is an actual parser check when pypdf exists; otherwise UNKNOWN. Embedded-font metadata is not a glyph/layout review.",
                    "Source SHA links in report metadata are validated. External sources and formal raw are not opened by this report-only helper."]}


def audit(report_dir):
    """Return a report-only derived QA object; no files are written by this API."""
    return QA(Path(report_dir)).run()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True,help="new JSON target outside report directory; refuses overwrite")
    args=parser.parse_args()
    root=args.report_dir.resolve();target=args.output.resolve()
    if target.exists() or target.is_relative_to(root):
        parser.error("--output must be a new path outside the report; existing report remains read-only")
    result=audit(root)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("x",encoding="utf-8") as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write("\n")
    print(json.dumps({"status":result["status"],"output":str(target),"visual_qa":result["visual_qa"]},ensure_ascii=False))
    raise SystemExit(2 if result["status"]=="FAIL" else 3 if result["status"]=="INCOMPLETE" else 0)


if __name__=="__main__":main()
