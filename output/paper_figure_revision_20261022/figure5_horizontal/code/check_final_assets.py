"""Check saved metric projection and actual horizontal outputs; no estimates computed."""
from pathlib import Path
from datetime import datetime,timezone
import csv
import hashlib
import json
from PIL import Image,ImageOps
from pypdf import PdfReader


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')


def main():
    root=Path(__file__).resolve().parents[1];out=root/'render_local_v2'
    qa=root/'qa';qa.mkdir()
    binding=json.loads((root/'data/source_binding.json').read_text())
    source=Path(binding['frozen_csv']['source_path'])
    assert sha(source)==sha(root/'data/figure5_frozen.csv')==binding['frozen_csv']['sha256']
    with (root/'data/figure5_frozen.csv').open(encoding='utf-8-sig',newline='') as f:
        source_rows={(float(row['length']),float(row['concurrency'])):row for row in csv.DictReader(f)}
    fields=(('task_latency_p95_sec','p95_ci95_low_sec','p95_ci95_high_sec'),
        ('native_observable_frame_ack_bytes_per_chain','frame_ack_bytes_per_chain_ci95_low','frame_ack_bytes_per_chain_ci95_high'),
        ('message_proxy_tokens_per_chain','message_proxy_tokens_per_chain_ci95_low','message_proxy_tokens_per_chain_ci95_high'))
    drawn=json.loads((out/'drawn_values.json').read_text())['records']
    assert len(drawn)==90 and len({(r['panel'],r['length'],r['concurrency']) for r in drawn})==90
    for row in drawn:
        original=source_rows[(row['length'],row['concurrency'])]
        point,low,high=fields[row['panel']]
        assert row['source_point_field']==point
        assert [row['point'],row['low'],row['high']]==[float(original[x]) for x in (point,low,high)]
    receipt=json.loads((out/'render_receipt.json').read_text())
    for record in receipt['files']:assert sha(out/record['path'])==record['sha256']
    png=out/'figure5_horizontal_zh.png'
    with Image.open(png) as image:
        assert image.size==(4133,1440)
        dpi=image.info.get('dpi');assert dpi and all(abs(v-600)<.1 for v in dpi)
        size=list(image.size)
        ImageOps.grayscale(image).save(qa/'figure5_horizontal_grayscale_preview.png')
    pdf=out/'figure5_horizontal_zh.pdf';reader=PdfReader(pdf)
    assert len(reader.pages)==1
    page=reader.pages[0];text=page.extract_text()
    for expected in ('任务时延','通信字节','消息代理计数','95%置信区间'):
        assert expected in text,expected
    assert '\ufffd' not in text
    page_mm=[float(page.mediabox.width)*25.4/72,float(page.mediabox.height)*25.4/72]
    assert abs(page_mm[0]-175)<.01 and abs(page_mm[1]-61)<.01
    fonts=[]
    for reference in page['/Resources']['/Font'].get_object().values():
        font=reference.get_object();descendants=font.get('/DescendantFonts')
        for leaf in [item.get_object() for item in descendants] if descendants else [font]:
            descriptor=leaf.get('/FontDescriptor');descriptor=descriptor.get_object() if descriptor else {}
            embedded=any(key in descriptor for key in ('/FontFile','/FontFile2','/FontFile3'))
            assert embedded
            fonts.append({'font':str(leaf.get('/BaseFont')),'embedded':embedded})
    save(qa/'technical_checks.json',{'status':'ACTUAL_TECHNICAL_CHECKS_COMPLETE_GRAYSCALE_REVIEW_PENDING',
        'created_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':sha(Path(__file__)),
        'frozen_input_source_and_copy_sha256':sha(source),'saved_point_CI_triplets_exact':90,
        'source_cells':30,'source_csv_unchanged':True,'png_pixels':size,'png_dpi':dpi,
        'pdf_page_mm':page_mm,'pdf_chinese_text_extractable':True,'pdf_fonts':fonts,
        'render_receipt_sha256':sha(out/'render_receipt.json'),
        'grayscale_preview_sha256':sha(qa/'figure5_horizontal_grayscale_preview.png'),
        'model_calls':0,'statistical_recalculations':0,'experiments':0})


if __name__=='__main__':main()
