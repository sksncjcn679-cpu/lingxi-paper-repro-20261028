"""Add existing editable draw.io sources without changing the prior figure package."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[2]
previous = ROOT / 'figure_package_v1/论文实验中文图表_保留原图新增四图_20261021.zip'
drawio = ROOT / 'delivery/drawio_framework_original_v1'
outdir = ROOT / 'delivery/figure_package_v2'
outdir.mkdir(exist_ok=False)
target = outdir / '论文中文图表与drawio框图_仅新增_20261021.zip'
expected = '4508663fee08fa2a91d8fb984f2c2a5899ec935f9eaffe8f2acd2dd4184133a8'
sha = lambda raw: hashlib.sha256(raw).hexdigest()
assert sha(previous.read_bytes()) == expected
entries, retained = {}, 0
with zipfile.ZipFile(previous) as old, zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as new:
    for info in old.infolist():
        data = old.read(info)
        new.writestr(info, data)
        entries[info.filename] = sha(data)
        retained += 1
    for path in sorted(p for p in drawio.rglob('*') if p.is_file()):
        name = 'drawio_framework_original_v1/' + path.relative_to(drawio).as_posix()
        assert name not in entries
        data = path.read_bytes()
        new.writestr(name, data)
        entries[name] = sha(data)
    manifest = {'status': 'APPENDED_DRAWIO_WITH_ALL_PRIOR_FIGURE_MEMBERS_RETAINED',
                'prior_package_sha256': expected, 'prior_members_retained': retained,
                'drawio_pages': 2, 'original_manuscript_or_figures_replaced': False,
                'member_sha256_excluding_this_manifest': entries}
    data = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    name = '图表与drawio追加清单.json'
    new.writestr(name, data); entries[name] = sha(data)
with zipfile.ZipFile(target) as check:
    assert check.testzip() is None
    assert len(check.infolist()) == len(entries) == len(set(check.namelist()))
    for name, digest in entries.items():
        assert sha(check.read(name)) == digest
assert sha(previous.read_bytes()) == expected
receipt = {'status': 'ACTUAL_ZIP_DECODE_CRC_AND_ALL_SHA_VERIFIED',
           'path': str(target), 'bytes': target.stat().st_size, 'sha256': sha(target.read_bytes()),
           'members': len(entries), 'prior_figure_members_byte_identical': retained,
           'editable_drawio_pages': 2, 'originals_modified': False,
           'source_drawio_copy_receipt': str(drawio / '新增收录核对.json')}
with (outdir / 'package_receipt.json').open('x', encoding='utf-8') as f:
    json.dump(receipt, f, ensure_ascii=False, indent=2); f.write('\n')
print(json.dumps(receipt, ensure_ascii=False))
