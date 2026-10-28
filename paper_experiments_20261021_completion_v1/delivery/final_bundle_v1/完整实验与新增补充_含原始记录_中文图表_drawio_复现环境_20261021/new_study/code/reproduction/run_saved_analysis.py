"""Reproduce the captured new batch offline into a fresh output directory.

This only changes the container's transport name. Frozen numerical sources,
registered task populations, seeds, recorded inputs and analysis API stay intact.
It never starts a model call or overwrites the saved study.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import sys
import uuid

CONTROLLER_SHA = '008c49789fc1177f9faae2eb82da291a333b7afa07f207e13832fdc7fc880b54'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--study-root', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args()
    root = args.study_root.resolve()
    controller = root / 'code/main_postrun_v1/run_after_main_terminal_v1.py'
    if hashlib.sha256(controller.read_bytes()).hexdigest() != CONTROLLER_SHA:
        raise ValueError('Frozen offline controller source identity differs')
    if args.out.exists():
        raise ValueError('A fresh output directory is required')
    if not args.out.parent.is_dir():
        raise ValueError('Output parent must already exist')
    spec = importlib.util.spec_from_file_location('saved_completion_controller', controller)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.NAME = 'lingxi-supplement-offline-reproduce-' + uuid.uuid4().hex[:12]
    sys.argv = [str(controller), '--study', str(root / 'main_batch'),
                '--native', str(root / 'code/native'),
                '--completion-receipt', str(root / 'main_collection_terminal.json'),
                '--out', str(args.out.resolve()), '--confirmed-main-terminal']
    module.main()


if __name__ == '__main__':
    main()
