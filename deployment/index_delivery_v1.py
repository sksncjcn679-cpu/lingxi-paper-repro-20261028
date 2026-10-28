"""Index actually executed old-study artifacts without changing prior selectors."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil

BASE=Path(__file__).resolve().parents[1]/'paper_experiments_20261019_pilot'
OUT=BASE/'formal_20261019'
DEST=OUT/'analysis/delivery_index_v1'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def main():
    if DEST.exists():raise RuntimeError('new index version required')
    DEST.mkdir()
    shutil.copyfile(Path(__file__),DEST/'helper.py')
    targets={
        'original_data_selection':OUT/'analysis/final_artifact_selection_v2.json',
        'publication_layout_selection':OUT/'analysis/final_render_selection_v3.json',
        'schema':OUT/'analysis/schema_validation_v2/schema_validation_audit.json',
        'publication_visual_qa':OUT/'analysis/report_qa_v4/visual_and_numeric_findings.json',
        'publication_numerical_identity':OUT/'analysis/report_numeric_serialization_audit_v1/audit.json',
        'shared_axis_numeric_supplement':OUT/'analysis/report_shared_axis_numeric_audit_v1/audit.json',
        'scalability_scatter_qa':OUT/'analysis/scalability_scatter_supplement_v4/visual_qa.json',
        'independent_rebuild':BASE/'rebuild_formal_20261019_v2/rebuild_manifest.json',
        'rebuilt_pdf_qa':BASE/'rebuild_formal_20261019_v2/pdf_pixel_qa_v1/audit.json',
        'source_bundle_audit':BASE/'source_package_formal_20261019_v2/source_package_audit.json',
        'raw_archive_audit':BASE/'raw_archive_formal_20261019_v1/archive_audit.json',
        'raw_archive_readonly':BASE/'raw_archive_formal_20261019_v1.readonly_verification.json',
    }
    entries={}
    for name,path in targets.items():
        d=json.loads(path.read_text(encoding='utf-8'))
        entries[name]={'path':path.relative_to(BASE).as_posix(),'sha256':sha(path),'recorded_status':d.get('status')}
    raw=json.loads(targets['raw_archive_audit'].read_text());source=json.loads(targets['source_bundle_audit'].read_text())
    assert raw['status']=='PASS_OPAQUE_RAW_ARCHIVE_AND_FULL_READBACK' and raw['source_and_archive_files']==239
    assert source['status']=='SOURCE_BUNDLE_CREATED_REQUIRES_RAW_ARCHIVE_AND_FINAL_QA'
    raw_file=BASE/'raw_archive_formal_20261019_v1/original_raw.tar.gz'
    source_file=BASE/'source_package_formal_20261019_v2/source_bundle.tar.gz'
    assert sha(raw_file)==raw['archive_sha256'] and sha(source_file)==source['archive']['sha256']
    raw_inventory=BASE/'source_package_formal_20261019_v2/bundle/metadata/original_raw_inventory.json'
    assert sha(raw_inventory)==raw['inventory_sha256']
    publication=OUT/'analysis/postrun_verification_v1/report_v3'
    assert len(list((publication/'figures').rglob('*.pdf')))==18
    assert len(list((publication/'tables').glob('table_??.csv')))==6
    index={'status':'EXECUTED_ARTIFACT_INDEX_WITH_RETAINED_RESEARCH_LIMITS','created_utc':datetime.now(timezone.utc).isoformat(),
           'purpose':'operational pointers to actual subsequent outcomes; no re-selection by effects and no rewriting historical PREPARED/FAILED manifests',
           'entries':entries,'publication_report_root':publication.relative_to(BASE).as_posix(),
           'raw_archive':{'path':raw_file.relative_to(BASE).as_posix(),'sha256':raw['archive_sha256'],'files':239,'opaque_readback':'PASS'},
           'source_archive':{'path':source_file.relative_to(BASE).as_posix(),'sha256':source['archive']['sha256'],'raw_bodies_included':False},
           'root_documents_changed_after_packaging':'current README/claims add subsequent receipts; packaged older snapshots retained unchanged',
           'unresolved':['C004: formal75/public16 repeated ancestral final-answer pairs in the old corpus',
                         'two confirmatory token comparisons INCOMPLETE; failed-attempt usage unknown',
                         'original main per-URI phase timing and memory index-only latency gaps',
                         'final original155 requirement review not complete'],
           'new_study_policy':'separate prospective task/contract/instrumentation repair; no old paid replay, no pooling and no usage backfill',
           'goal_completion':'NOT_CERTIFIED','paid_api_calls':0}
    (DEST/'executed_artifact_index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    selected=set(targets.values())|{raw_file,source_file,raw_inventory,DEST/'helper.py',DEST/'executed_artifact_index.json',
                                  OUT/'README_reproduce.md',OUT/'claims_to_evidence.md'}
    selected.update(p for p in publication.rglob('*') if p.is_file())
    selected.update(p for p in (OUT/'analysis/scalability_scatter_supplement_v4').rglob('*') if p.is_file())
    selected.update(p for p in (BASE/'raw_archive_formal_20261019_v1').iterdir() if p.is_file())
    checks=''.join(f'{sha(path)}  {path.relative_to(BASE).as_posix()}\n' for path in sorted(selected))
    (DEST/'checksums.sha256').write_text(checks,encoding='utf-8')
    print(json.dumps({'status':index['status'],'checked_artifact_files':len(selected),
                      'index_sha256':sha(DEST/'executed_artifact_index.json'),'checksums_sha256':sha(DEST/'checksums.sha256'),
                      'goal_completion':index['goal_completion']}))


if __name__=='__main__':main()
