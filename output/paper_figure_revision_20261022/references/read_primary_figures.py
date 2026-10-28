"""Read public primary papers for visual design research; not publication content."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import urllib.request
import subprocess
from pypdf import PdfReader

OUT=Path(__file__).resolve().parent
PAPERS=[
 ('ray_osdi2018','https://www.usenix.org/system/files/osdi18-moritz.pdf',6),
 ('vllm_sosp2023','https://arxiv.org/pdf/2309.06180',4),
 ('metagpt_iclr2024','https://proceedings.iclr.cc/paper_files/paper/2024/file/6507b115562bb0a305f1958ccc87355a-Paper-Conference.pdf',1),
]

def load(item):
 key,url,page_index=item
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
 with urllib.request.urlopen(req,timeout=60) as response:data=response.read()
 assert data.startswith(b'%PDF'),key
 pdf=OUT/(key+'.pdf')
 with pdf.open('xb') as f:f.write(data)
 doc=PdfReader(pdf)
 png=OUT/(key+'_figure_page.png')
 command=['C:/Users/暴瘦/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe','-f',str(page_index+1),'-l',str(page_index+1),'-r','120','-singlefile','-png',str(pdf),str(png.with_suffix(''))]
 result=subprocess.run(command,capture_output=True)
 (OUT/(key+'_render_stderr.log')).write_bytes(result.stderr)
 assert result.returncode==0, key
 text=doc.pages[page_index].extract_text()
 (OUT/(key+'_figure_page.txt')).write_text(text,encoding='utf-8')
 record={'key':key,'url':url,'pdf_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'page_index':page_index,'page_count':len(doc.pages),'preview_path':str(png),'actual_render_exit_code':result.returncode,'scope':'local primary-source design inspection only; not embedded in original work or uploaded'}
 print(json.dumps(record,ensure_ascii=False),flush=True)
 return record

if __name__=='__main__':
 with ThreadPoolExecutor(max_workers=3) as pool:records=list(pool.map(load,PAPERS))
 with (OUT/'primary_source_read_receipt.json').open('x',encoding='utf-8') as f:json.dump(records,f,ensure_ascii=False,indent=2)
