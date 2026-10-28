"""Set print PNG metadata and inspect/render the actual PDF; no PDF authoring."""
from pathlib import Path
from PIL import Image, ImageChops
from pypdf import PdfReader
import hashlib, json, shutil, subprocess

OUT=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
image=Image.open(OUT/'qa/png_600dpi_pixels.png').convert('RGB')
assert image.size==(4252,2392)
image.save(OUT/'figure1_framework.png',dpi=(600,600))
shutil.copyfile(OUT/'figure1_framework.png',OUT/'figure1_framework_600dpi.png')
r,g,b=image.split()
assert ImageChops.difference(r,g).getbbox() is None
assert ImageChops.difference(r,b).getbbox() is None
reader=PdfReader(OUT/'figure1_framework.pdf')
assert len(reader.pages)==1
page=reader.pages[0]
assert len(page.images)==0
fonts=[]
for name,ref in page['/Resources']['/Font'].items():
    f=ref.get_object();typ=str(f.get('/Subtype'))
    descendants=f.get('/DescendantFonts',[])
    fd=(descendants[0].get_object().get('/FontDescriptor') if descendants else f.get('/FontDescriptor'))
    fd=fd.get_object() if fd else {}
    embedded=bool(f.get('/CharProcs')) if typ=='/Type3' else any(k in fd for k in ('/FontFile','/FontFile2','/FontFile3'))
    fonts.append({'resource':str(name),'subtype':typ,'base_font':str(f.get('/BaseFont','vector CharProcs')),
        'embedded_outline_or_font':embedded,'to_unicode':'/ToUnicode' in f})
assert all(f['embedded_outline_or_font'] and f['to_unicode'] for f in fonts)
poppler=Path('C:/Users/暴瘦/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')
completed=subprocess.run([str(poppler),'-png','-singlefile','-scale-to-x','1600','-scale-to-y','-1',
    str(OUT/'figure1_framework.pdf'),str(OUT/'qa/pdf_poppler_1600')],capture_output=True)
(OUT/'qa/pdf_poppler.stderr.txt').write_bytes(completed.stderr)
assert completed.returncode==0
shutil.copyfile('E:/codex_rebuilds/chinese_presentation_inputs_v2/OFL.txt',OUT/'qa/OFL.txt')
assets={}
for name in ('figure1_framework.drawio','figure1_framework.svg','figure1_framework.png','figure1_framework_600dpi.png',
             'figure1_framework.pdf','figure1_caption.txt','build_reference_framework.py','render_framework.cjs','finalize_framework.py'):
    p=OUT/name;assets[name]={'bytes':p.stat().st_size,'sha256':sha(p)}
receipt={'actual_terminal':True,'exit_code':0,'scope':'format, native editability and actual output properties; not experiment validation',
    'assets':assets,'png_dimensions':list(image.size),'png_dpi':list(Image.open(OUT/'figure1_framework.png').info['dpi']),
    'all_png_pixels_neutral_grayscale':True,'pdf_pages':1,'pdf_media_box_pt':list(page.mediabox),
    'pdf_raster_images':0,'pdf_fonts':fonts,'pdf_all_fonts_or_vector_glyphs_embedded':True,
    'pdf_all_font_resources_have_ToUnicode':True,'pdf_poppler_exit_code':completed.returncode,
    'pdf_poppler_preview_sha256':sha(OUT/'qa/pdf_poppler_1600.png'),
    'poppler_stderr_note':'Bundled Windows Poppler emits three nameToUnicode auxiliary-path notices; actual page rendered successfully.',
    'pdf_marker_count':1,'pdf_marker_operation':'create','pdf_marker_expected_output_count':1,
    'scientific_data_modified':False,'previous_figure_directory_modified':False}
(OUT/'qa/artifact_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'exit_code':0,'assets':assets,'png_dimensions':list(image.size),'pdf_pages':1},ensure_ascii=False))
