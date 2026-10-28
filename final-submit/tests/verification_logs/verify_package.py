"""Read-only verification of every packaged file and history object."""
from pathlib import Path
import hashlib
import json
import sys

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'FILE_MANIFEST.json').read_text(encoding='utf-8'))
for name, expected in manifest.items():
    path = root / name
    assert path.is_file(), f'missing file: {name}'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, f'changed file: {name}'
for row in json.loads((root / 'history/catalog.json').read_text(encoding='utf-8')):
    path = root / row['object_path']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
sys.path.insert(0, str(root / 'src'))
from agent_runtime.evaluation.runner import _source_tree_sha256
info = json.loads((root / 'PACKAGE_INFO.json').read_text(encoding='utf-8'))
assert _source_tree_sha256(root) == info['source_tree_sha256'], 'runtime source identity changed'
print(f'PASS: {len(manifest)} files, history objects and target-tested source identity')
