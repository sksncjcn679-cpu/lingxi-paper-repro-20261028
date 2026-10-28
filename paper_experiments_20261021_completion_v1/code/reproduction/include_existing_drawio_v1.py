"""Add the user's existing editable diagrams to the new delivery, byte for byte."""
from pathlib import Path
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
source = ROOT.parent / 'output/drawio_framework_20261021'
target = ROOT / 'delivery/drawio_framework_original_v1'
if target.exists():
    raise FileExistsError('Keep existing delivery; do not overwrite')
files = sorted(p for p in source.rglob('*') if p.is_file())
assert files
digests = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
shutil.copytree(source, target)
checks = []
for relative, original_sha in digests.items():
    old, new = source / relative, target / relative
    assert hashlib.sha256(old.read_bytes()).hexdigest() == original_sha
    assert hashlib.sha256(new.read_bytes()).hexdigest() == original_sha
    checks.append({'relative_path': relative, 'bytes': new.stat().st_size,
                   'source_sha256': original_sha, 'copy_sha256': original_sha})
drawio = target / '多智能体协作框架_可编辑.drawio'
document = ET.parse(drawio).getroot()
pages = []
for page in document.findall('diagram'):
    model = page.find('mxGraphModel')
    assert model is not None
    cells = model.findall('.//mxCell')
    ids = [cell.get('id') for cell in cells]
    assert len(ids) == len(set(ids))
    pages.append({'name': page.get('name'), 'vertices': sum(c.get('vertex') == '1' for c in cells),
                  'edges': sum(c.get('edge') == '1' for c in cells)})
assert len(pages) == 2
receipt = {'status': 'EXISTING_DRAWIO_AND_PREVIEWS_ADDED_BYTE_IDENTICAL',
           'source': str(source), 'new_delivery': str(target), 'files': checks,
           'drawio_xml_pages': pages, 'source_files_modified': False,
           'original_manuscript_figure_replaced': False,
           'scope': 'Copied the existing editable source, color/monochrome PNG/SVG previews and provenance; no redraw.'}
with (target / '新增收录核对.json').open('x', encoding='utf-8') as f:
    json.dump(receipt, f, ensure_ascii=False, indent=2); f.write('\n')
with (target / '收录说明.txt').open('x', encoding='utf-8') as f:
    f.write('此目录原样收录已有draw.io框图及彩色、黑白PNG/SVG预览和生成来源。\n')
    f.write('可编辑文件含“论文彩色版”“论文黑白版”两个页面。\n')
    f.write('原文件与原论文图1均未改动；本次仅增加交付副本。逐文件SHA核对见新增收录核对.json。\n')
print(json.dumps({'copied_original_files': len(checks), 'editable_pages': len(pages),
                  'all_files_byte_identical': True, 'original_files_modified': False}, ensure_ascii=False))
