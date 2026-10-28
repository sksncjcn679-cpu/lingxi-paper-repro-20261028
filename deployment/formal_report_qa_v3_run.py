"""Run report-v3-only QA/rendering after the actual recovery identity gate.

This entry point never generates reports, calls a model, reads raw, or runs a
state/retrieval experiment. Original QA exit3/UNKNOWN/missing usage stay intact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

OVERLAY_SHA = '8bc0747441900db0372f1a1cd81521073fcb4e3de1faf986e6d61046c20818be'
HELPER_SHA = 'e40699c6c29735f42317aefae8151ed195a41d10dfc0a965d8e13d41d63c0fd1'
REPORT_SHA = 'fed72d9777f6eaf8c630e19eb33f182e3f8acd01423d48dc20a39c9c4c460dc3'
DATA_SELECTION_SHA = 'fc8dcde3bded0cdeb4b77c93adde80eb21b954d632a9e4459b0e90bfaf357a00'


def utc(): return datetime.now(timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def selected(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root: raise ValueError('selection path outside evidence root')
    return path


def numeric_inventory(report):
    return {path.relative_to(report).as_posix(): sha(path) for path in sorted(report.rglob('*'))
            if path.is_file() and (path.suffix == '.csv' or path.name.startswith('statistics_') and
                                  path.suffix == '.json' or path.name == 'abstract_metrics.json')}


def inventory(report):
    return {path.relative_to(report).as_posix(): sha(path) for path in sorted(report.rglob('*')) if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(); root = args.out.resolve()
    overlay_path = root / 'analysis/final_render_selection_v3.json'
    if sha(overlay_path) != OVERLAY_SHA: raise RuntimeError('prepared layout overlay identity changed')
    overlay = read(overlay_path)
    out = selected(root, overlay['report_qa_root'])
    if out.exists(): raise RuntimeError('QA destination must be new; never overwrite prior evidence')
    out.mkdir(parents=True)
    report = selected(root, overlay['report_root'])
    original = selected(root, overlay['original_data_report_root'])
    recovery = selected(root, overlay['report_recovery_root'])
    helper = Path(__file__).resolve().with_name('formal_report_qa.py')
    contract_path = root / 'analysis/report_qa_contract_v3.json'
    receipt = dict(status='CHECKING_ACTUAL_RECOVERY', started_utc=utc(), pid=os.getpid(),
        source_sha256=sha(Path(__file__)), report=str(report), old_report=str(original),
        overlay_sha256=OVERLAY_SHA, steps=[], rendered=[], paid_api_calls=0,
        raw_opened=False, formal_venv_modified=False, visual_qa='PENDING_ACTUAL_RENDER_AND_VIEW')

    def persist():
        (out/'execution_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    env = {k: v for k, v in os.environ.items() if not any(token in k.upper() for token in
           ('API_KEY', 'API_TOKEN', 'ACCESS_TOKEN', 'AUTH_TOKEN', 'PASSWORD', 'SECRET'))}
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')

    def run(name, command, timeout):
        step = dict(name=name, command=[str(value) for value in command], started_utc=utc(), timeout_sec=timeout)
        receipt['steps'].append(step); persist(); tick=time.perf_counter()
        stdout, stderr = out/(name+'.stdout.log'), out/(name+'.stderr.log')
        process = None
        try:
            with stdout.open('xb') as first, stderr.open('xb') as second:
                process=subprocess.Popen(step['command'], stdout=first, stderr=second, env=env)
                step['pid']=process.pid; persist()
                try: step['exit_code']=process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(); step.update(timed_out=True, exit_code=process.returncode)
            step['status']='PASS_EXIT0' if step.get('exit_code')==0 else 'NONZERO_RETAINED'
        except BaseException:
            if process is not None and process.poll() is None:
                process.kill(); process.wait()
                step.update(cleanup='OWNED_DIRECT_CHILD_KILLED_AND_WAITED', exit_code=process.returncode)
            raise
        finally:
            step.update(finished_utc=utc(), wall_sec=time.perf_counter()-tick,
                        stdout_sha256=sha(stdout) if stdout.is_file() else None,
                        stderr_sha256=sha(stderr) if stderr.is_file() else None)
            persist()
        return step

    fatal = None
    persist()
    try:
        controller_path=recovery/'manifest.json'; controller=read(controller_path)
        identity_path=recovery/'numerical_identity.json'; identity=read(identity_path)
        expected=overlay['numeric_reference_sha256']
        steps=controller.get('steps', [])
        if controller.get('status')!='PASS' or not steps or any(step.get('status')!='PASS' or
            step.get('exit_code')!=0 for step in steps): raise RuntimeError('actual report recovery has not passed')
        if controller.get('final_render_selection_sha256')!=OVERLAY_SHA or controller.get('report_source_sha256')!=REPORT_SHA:
            raise RuntimeError('actual recovery source/overlay identity differs')
        if controller.get('numerical_identity_sha256')!=sha(identity_path) or controller.get('numerical_identity')!=identity:
            raise RuntimeError('controller numerical identity receipt differs')
        if identity.get('status')!='PASS_EXACT_BYTES' or identity.get('numeric_files')!=42 or len(expected)!=42 or \
            identity.get('expected_sha256')!=expected or identity.get('actual_sha256')!=expected:
            raise RuntimeError('controller did not prove all42 exact numerical files')
        if numeric_inventory(original)!=expected or numeric_inventory(report)!=expected:
            raise RuntimeError('old/new actual numerical file set or SHA differs from overlay')
        if sha(selected(root, overlay['data_selection']))!=DATA_SELECTION_SHA or sha(helper)!=HELPER_SHA:
            raise RuntimeError('original data selection or QA helper changed')
        if sha(selected(root, overlay['original_visual_qa']))!=overlay['original_visual_qa_sha256'] or \
            sha(selected(root, overlay['original_qa_execution_receipt']))!=overlay['original_qa_execution_receipt_sha256']:
            raise RuntimeError('prior actual visual failure/QA evidence changed')
        contract=read(contract_path)
        if contract['entrypoint']['sha256']!=sha(Path(__file__)) or contract['static_basis']['reviewed_sha256']!=REPORT_SHA or \
            contract['layout_overlay_sha256']!=OVERLAY_SHA: raise RuntimeError('prepared QA-v3 contract identity differs')
        receipt.update(status='RUNNING_ACTUAL_QA', controller_sha256=sha(controller_path),
            numerical_identity_sha256=sha(identity_path), numerical_gate='PASS_ALL42_EXACT_BYTES_RECHECKED',
            contract_sha256=sha(contract_path), helper_sha256=HELPER_SHA,
            python_executable=sys.executable, python_version=sys.version,
            dependencies={name:importlib.metadata.version(name) for name in ('pypdf','Pillow')},
            report_inputs_before=inventory(report), original_report_inputs_before=inventory(original))
        persist()
        qa=run('original_qa_helper',[sys.executable,helper,'--report-dir',report,'--output',out/'automated_qa.json'],300)
        receipt['automated_exit_code']=qa.get('exit_code')
        from PIL import Image, ImageDraw, ImageOps
        renderer=Path(os.environ['USERPROFILE'])/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe'
        if not renderer.is_file(): raise RuntimeError('existing Poppler unavailable; no automatic installation')
        receipt['renderer']={'path':str(renderer),'sha256':sha(renderer),'dpi':300,'source':'actual PDF bytes'}
        rendered=out/'rendered'; rendered.mkdir()
        pdfs=sorted((report/'figures').glob('figure_*_*.pdf'))
        if len(pdfs)!=18: raise RuntimeError('exactly18 actual PDF files required')
        for pdf in pdfs:
            prefix=rendered/pdf.stem
            step=run('render_'+pdf.stem,[renderer,'-r','300','-png','-singlefile','-f','1','-l','1',pdf,prefix],90)
            png=prefix.with_suffix('.png')
            record={'pdf':str(pdf),'pdf_sha256':sha(pdf),'status':'FAILED_RENDER_RETAINED'}
            if step.get('exit_code')==0 and png.is_file():
                with Image.open(png) as page: dimensions=list(page.size)
                record.update(status='RENDERED_REQUIRES_VIEW',output=str(png),png_sha256=sha(png),dimensions=dimensions)
            receipt['rendered'].append(record); persist()
        for column in ('single','double'):
            paths=sorted(rendered.glob('figure_*_'+column+'.png'))
            tile_w,tile_h=(500,1050) if column=='single' else (700,700)
            for grey in (False,True):
                sheet=Image.new('RGB',(tile_w*3,tile_h*3),'#ececec'); draw=ImageDraw.Draw(sheet)
                for index,path in enumerate(paths):
                    with Image.open(path) as original_page:
                        page=original_page.convert('RGB')
                        if grey: page=ImageOps.grayscale(page).convert('RGB')
                        page.thumbnail((tile_w-16,tile_h-36),Image.Resampling.LANCZOS)
                    x=(index%3)*tile_w+8; y=(index//3)*tile_h
                    draw.text((x,y+4),path.stem,fill='black'); sheet.paste(page,(x,y+26))
                sheet.save(out/('contact_'+column+('_greyscale' if grey else '')+'.png'))
        receipt['all18_pdf_rendered']=len(receipt['rendered'])==18 and all(
            record['status']=='RENDERED_REQUIRES_VIEW' for record in receipt['rendered'])
        receipt['status']='ACTUAL_QA_RENDER_COMPLETE_REQUIRES_ORIGINAL_DETAIL_VIEW'
    except BaseException as error:
        fatal=error
        receipt.update(status='ERROR_RETAINED_FOR_DIAGNOSIS',error_type=type(error).__name__,
                       error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
    finally:
        if 'report_inputs_before' in receipt:
            receipt['report_inputs_after']=inventory(report)
            receipt['original_report_inputs_after']=inventory(original)
            receipt['report_inputs_unchanged']=receipt['report_inputs_before']==receipt['report_inputs_after']
            receipt['original_report_inputs_unchanged']=receipt['original_report_inputs_before']==receipt['original_report_inputs_after']
        receipt['finished_utc']=utc(); persist()
    print(json.dumps({'status':receipt['status'],'output':str(out),'automated_exit_code':receipt.get('automated_exit_code'),
        'all18_pdf_rendered':receipt.get('all18_pdf_rendered'),'visual_qa':receipt['visual_qa']}))
    if fatal is not None: raise fatal
    raise SystemExit(0 if receipt.get('all18_pdf_rendered') and receipt.get('report_inputs_unchanged') and
                     receipt.get('original_report_inputs_unchanged') else 2)


if __name__=='__main__': main()
