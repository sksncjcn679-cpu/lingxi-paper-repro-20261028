"""Read-only standard-library verification of the extracted small supplement."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024*1024), b''):
            h.update(data)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    root = parser.parse_args().root.resolve()
    manifest = json.loads((root/'control_supplement_manifest.json').read_text(encoding='utf-8'))
    for member in manifest['members']:
        rel = PurePosixPath(member['member'])
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Unsafe manifest member')
        path = root/str(rel)
        if path.is_symlink() or not path.is_file() or path.stat().st_size != member['bytes'] or digest(path) != member['sha256']:
            raise ValueError(f'Control supplement identity differs: {rel}')
    print(f"PASS: {len(manifest['members'])} copied small control inputs match; no rebuild/API executed.")


if __name__ == '__main__':
    main()
