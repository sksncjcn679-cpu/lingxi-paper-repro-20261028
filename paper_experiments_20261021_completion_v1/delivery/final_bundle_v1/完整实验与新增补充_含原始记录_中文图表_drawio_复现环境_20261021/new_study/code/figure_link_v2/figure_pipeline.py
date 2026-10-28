"""One offline saved-value projection and rendering; no statistical calculation."""
from pathlib import Path
import argparse
import runpy
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, '/figure-code')
    from prepare_figure_data import prepare, sha, write_json, load
    from render_completion_figures import render
    if args.out.exists():
        raise ValueError('Retain existing pipeline outputs; a fresh path is required.')
    args.out.mkdir()
    prepare(args.manifest, args.out/'data')
    render(args.out/'data/figure_data.json', args.out/'render')
    write_json(args.out/'pipeline_receipt.json', {
        'status': 'ACTUAL_PROJECTION_AND_RENDER_COMPLETED_VISUAL_QA_PENDING',
        'source_sha256': sha(__file__),
        'binding_manifest_sha256': sha(args.manifest),
        'projection_receipt_sha256': sha(args.out/'data/projection_receipt.json'),
        'render_receipt_sha256': sha(args.out/'render/render_receipt.json'),
        'rendered_file_count': len(load(args.out/'render/render_receipt.json')['files']),
        'model_calls': 0, 'statistical_runs': 0,
        'visual_qa': 'NOT_YET_PERFORMED', 'packaging_runs': 0,
        'original_results_or_figure_assets_modified': False,
    })


if __name__ == '__main__':
    main()
