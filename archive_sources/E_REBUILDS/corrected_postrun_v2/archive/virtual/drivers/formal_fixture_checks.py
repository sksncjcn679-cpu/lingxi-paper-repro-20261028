"""Executable read-only checks on the authored scenario fixtures; never inspect or change host services."""
from pathlib import Path
import argparse
import hashlib
import json
import re
from formal_corpus import ACTION,reference_label


def automatic_label(row):
    text=row['observations']['current_evidence']
    if row['split']=='formal':return reference_label(text)
    if row['domain']=='migration':
        if re.search(r'cannot|mismatch|AArch64.*x86_64|needs symbol GLIBC_.*but.*only',text):return 'rebuild_required'
        if re.search(r'every listed.*resolves|import probe succeeds',text):return 'compatible'
        raise ValueError('public migration evidence is not deterministically classified')
    sentences=re.split(r'(?<=[.;!])\s+',text)
    current=' '.join(s for s in sentences if not re.search(r'\bold\b|yesterday|retired|hours earlier|no longer',s,re.I))
    return reference_label(current)


def check(row,action):
    if action not in ACTION.values():raise ValueError('unsupported read-only check')
    label=automatic_label(row)
    evidence=row['observations']['current_evidence']
    return {'task_id':row['task_id'],'action':action,'current_record_matches_check':ACTION[label]==action,
        'evidence':evidence if ACTION[label]==action else None,
        'source_sha256':hashlib.sha256(evidence.encode()).hexdigest(),
        'read_only':True,'scope':'authored fixture evidence; not live host diagnosis'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('task_id');parser.add_argument('action')
    parser.add_argument('--corpus',default='/evidence/formal_20261019/corpus/automatic_validated_v1/tasks.jsonl')
    args=parser.parse_args()
    cases=[json.loads(s) for s in Path(args.corpus).read_text().splitlines()]
    row=next(r for r in cases if r['task_id']==args.task_id)
    print(json.dumps(check(row,args.action),ensure_ascii=False))
