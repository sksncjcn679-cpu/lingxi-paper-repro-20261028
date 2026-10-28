"""Read-only presentation checks and grayscale previews of actually rendered files."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
from PIL import Image, ImageOps
from pypdf import PdfReader


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')


def main():
    new = Path(__file__).resolve().parents[2]
    root = new/'figures_v3/results/render'
    out = new/'figures_v3/qa'; out.mkdir()
    render = json.loads((root/'render_receipt.json').read_text())
    checks = []
    for record in render['files']:
        path = root/record['path']
        assert sha(path) == record['sha256']
        if path.suffix != '.pdf': continue
        reader = PdfReader(path)
        assert len(reader.pages) == 1
        text = reader.pages[0].extract_text()
        assert '图'+str(record['figure']) in text and '\ufffd' not in text
        fonts = []
        for reference in reader.pages[0]['/Resources']['/Font'].get_object().values():
            font = reference.get_object()
            descendants = font.get('/DescendantFonts')
            leaves = [item.get_object() for item in descendants] if descendants else [font]
            for leaf in leaves:
                descriptor = leaf.get('/FontDescriptor')
                descriptor = descriptor.get_object() if descriptor else {}
                embedded = any(key in descriptor for key in ('/FontFile', '/FontFile2', '/FontFile3'))
                assert embedded
                fonts.append({'base_font': str(leaf.get('/BaseFont')), 'embedded': embedded})
        checks.append({'file': path.name, 'figure': record['figure'], 'width': record['width'],
                       'sha256': sha(path), 'text_extractable': True,
                       'title_found': True, 'replacement_character_absent': True, 'fonts': fonts})
    previews = []
    for width, cell in (('single',(600,1850)), ('double',(800,1000))):
        sheet = Image.new('RGB',(cell[0]*2,cell[1]*2),'white')
        for index, number in enumerate(range(10,14)):
            path = root/f'figure_{number}_zh_{width}.png'
            with Image.open(path) as opened:
                image = ImageOps.grayscale(opened).convert('RGB')
                image.thumbnail(cell)
                sheet.paste(image,((index%2)*cell[0],(index//2)*cell[1]))
        target = out/f'grayscale_{width}_actual_previews.png'
        sheet.save(target); previews.append({'path': str(target), 'sha256': sha(target)})
    equal = {}
    for number, suffix in ((10,'pairs'),(11,'timing'),(12,'seed'),(13,'stress')):
        name = f'figure_{number}_{suffix}.csv'
        equal[name] = sha(new/'figures_v2/results/data'/name) == sha(new/'figures_v3/results/data'/name)
    assert all(equal.values())
    save(out/'technical_checks.json', {'status': 'ACTUAL_FILE_CHECKS_COMPLETED_GRAYSCALE_VISUAL_REVIEW_PENDING',
        'created_utc': datetime.now(timezone.utc).isoformat(), 'source_sha256': sha(Path(__file__)),
        'render_receipt_sha256': sha(root/'render_receipt.json'), 'pdf_checks': checks,
        'v2_v3_all_saved_csv_bytes_identical': equal, 'grayscale_previews': previews,
        'new_model_calls': 0, 'statistical_calculations': 0, 'source_assets_modified': False})


if __name__ == '__main__': main()
