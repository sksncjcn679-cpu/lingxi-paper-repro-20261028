"""Read-only audit of every causal ancestor, rather than only the direct parent."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def audit(corpus):
    before = corpus.read_bytes()
    rows = [json.loads(line) for line in before.splitlines() if line.strip()]
    tasks = {row['task_id']: row for row in rows}
    if len(tasks) != 144 or len(rows) != 144:
        raise ValueError('expected all144 unique frozen cases')
    pairs = []
    equal = []
    for task in rows:
        pending = list(task['parent_ids'])
        seen = set()
        while pending:
            identity = pending.pop()
            if identity in seen:
                continue
            seen.add(identity)
            parent = tasks[identity]
            if parent['chain_id'] != task['chain_id'] or parent['observations']['checkpoint'] >= task['observations']['checkpoint']:
                raise ValueError('invalid causal ancestor')
            pending.extend(parent['parent_ids'])
            item = {'task_id': task['task_id'], 'ancestor_id': identity, 'chain_id': task['chain_id'],
                    'split': task['split'], 'direct_parent': identity in task['parent_ids'],
                    'task_expected_sha256': sha(canonical(task['expected'])),
                    'ancestor_expected_sha256': sha(canonical(parent['expected'])),
                    'equal_expected': task['expected'] == parent['expected'],
                    'current_evidence_equal': task['observations']['current_evidence'] == parent['observations']['current_evidence']}
            pairs.append(item)
            if item['equal_expected']:
                equal.append(item)
    if corpus.read_bytes() != before:
        raise RuntimeError('frozen corpus changed during audit')
    return {'status': 'CONTRADICTION_ALL_ANCESTOR_ANSWER_DISTINCTNESS',
            'created_utc': datetime.now(timezone.utc).isoformat(), 'source_sha256': sha(Path(__file__).read_bytes()),
            'corpus': str(corpus), 'corpus_sha256': sha(before), 'task_count': len(rows),
            'chain_count': len({row['chain_id'] for row in rows}), 'causal_ancestor_pairs': len(pairs),
            'equal_expected_pairs': len(equal), 'equal_pairs_by_split': dict(Counter(item['split'] for item in equal)),
            'equal_direct_parent_pairs': sum(item['direct_parent'] for item in equal),
            'scope': 'Complete expected diagnosis/action equality over transitive parent ancestry; not actual model-output equality or proof of cache use',
            'original_requirement': 'Original protocol3.1: later task final answers differ from earlier tasks, avoiding exact answer caching as knowledge transfer',
            'previous_check_boundary': 'Earlier direct-parent audit found2 pilot edges but did not establish all-ancestor distinctness in the formal120',
            'raw_or_corpus_modified': False, 'model_calls': 0, 'equal_pairs': equal, 'all_ancestor_pairs': pairs}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('new evidence output required')
    result = audit(args.corpus)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: result[key] for key in ('status', 'task_count', 'causal_ancestor_pairs',
          'equal_expected_pairs', 'equal_pairs_by_split', 'equal_direct_parent_pairs', 'corpus_sha256')}))
