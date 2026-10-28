"""Record the completed eight-PNG and two-grayscale actual visual inspection."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import shutil


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')


def main():
    new = Path(__file__).resolve().parents[2]
    root = new/'figures_v3/results/render'; qa = new/'figures_v3/qa'
    tech = json.loads((qa/'technical_checks.json').read_text())
    render = json.loads((root/'render_receipt.json').read_text())
    for width in ('single', 'double'):
        rows = [r for r in render['files'] if r['width'] == width]
        save(qa/f'visual_qa_{width}.json', {
            'schema_version': 'completion-figure-visual-qa-v1', 'status': 'PASS_WITH_LIMITATIONS',
            'width': width, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'actual_new_views': [10,11,12,13], 'render_receipt_sha256': sha(root/'render_receipt.json'),
            'unresolved_P1_P2': [],
            'actual_visual_inspection': 'Each final v3 PNG viewed individually with view_image; both actual grayscale contact previews also viewed.',
            'checks': {
                'chinese_font_rendered_without_missing_glyph_boxes': True,
                'titles_axis_labels_and_notes_within_canvas': True,
                'all_legends_clear_of_observed_points': True,
                'figure11_single_SS1_p99_visible_after_v2_legend_occlusion_fix': True,
                'old_five_token_comparisons_explicitly_missing_not_zero': True,
                'negative_quality_results_retained_and_visible': True,
                'estimates_and_95CI_drawn_from_saved_values_no_recalculation': True,
                'figure11_quantiles_not_presented_as_CI': True,
                'figure12_all_points_inclusive_and_exclusive_separate_no_addition': True,
                'figure13_pointwise_postcollection_CI_labelled': True,
                'grayscale_shapes_hatches_and_line_styles_inspected': True,
                'all_four_v2_v3_CSV_bytes_identical': all(tech['v2_v3_all_saved_csv_bytes_identical'].values()),
            },
            'pdf_checks': [r for r in tech['pdf_checks'] if r['width'] == width],
            'files': rows,
            'grayscale_preview': [r for r in tech['grayscale_previews'] if f'_{width}_' in r['path']][0],
            'limitations': ['Very narrow saved token/IPC/communication intervals may coincide with point markers; precise endpoints remain in CSV.',
                'Some original pressure communication curves overlap because saved values coincide; no positional displacement or manufactured separation.',
                'Single-column figures are tall and footnotes are small; use double-column PNG/PDF for the manuscript supplement.',
                'Visual PASS covers presentation only, not noninferiority, speed-up, general Linux accuracy, or whole-experiment completion.'],
            'paper_appendix_mapping': {f'S{i-9}': f'library figure {i}' for i in range(10,14)},
            'model_calls': 0, 'statistical_runs': 0,
        })
    data = new/'figures_v3/results/data'
    save(data/'paper_appendix_figure_mapping.json', {
        'status': 'APPEND_ONLY_SUPPLEMENT_FIGURE_MAPPING',
        'original_manuscript_figures_1_through_4': 'Preserved unchanged; no renumbering, move or replacement.',
        'original_figure_library_1_through_9': 'Preserved byte for byte in original_delivery.',
        'mapping': [{'manuscript_label': f'图S{i-9}', 'library_figure': i,
                     'double_png': f'completion_figures/figure_{i}_zh_double.png',
                     'sha256': sha(root/f'figure_{i}_zh_double.png')}
                    for i in range(10,14)]})
    additions = {
        'actual_input_manifest.json': new/'logs/figure_link_v3/run_control/actual_input_manifest.json',
        'actual_host_binding_map.json': new/'logs/figure_link_v3/run_control/actual_host_binding_map.json',
        'actual_render_execution_receipt.json': new/'logs/figure_link_v3/execution_receipt.json',
        'actual_offline_guard_result.json': new/'figures_v3/guard_result.json',
        'actual_pipeline_receipt.json': new/'figures_v3/results/pipeline_receipt.json',
        'actual_asset_technical_checks.json': qa/'technical_checks.json',
    }
    for name, source in additions.items():
        target = data/name
        assert not target.exists()
        shutil.copyfile(source,target)
        assert sha(target) == sha(source)


if __name__ == '__main__': main()
