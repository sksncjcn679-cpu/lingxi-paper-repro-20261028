"""Narrow postrender numeric supplement for four shared-axis SVGs.

Never redraw, import the plotting module, open experiment raw, or alter prior QA.
The original pinned SVG primitive parser is reused; calibration, source-share
proof, frame/tick correspondence and CSV recipes are independent here.
"""
from __future__ import annotations

import argparse
import ast
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time

REPORT_SHA = 'fed72d9777f6eaf8c630e19eb33f182e3f8acd01423d48dc20a39c9c4c460dc3'
HELPER_SHA = 'e40699c6c29735f42317aefae8151ed195a41d10dfc0a965d8e13d41d63c0fd1'
PRIOR_QA_SHA = '2130572309282beabc955be5c63c9fc819471b7a81bfbd097f381f1da7ea1b7c'
TOLERANCE = .4  # SVG user units, verified equal to PDF points below
GEOMETRY_TOLERANCE = .00001
IDENTITY = (1., 0., 0., 1., 0., 0.)


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite CSV/tick coordinate; no replacement by0')
    return result


def source_share_proof(path):
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    result = {}
    for name, dimension, shape in [('draw5', 'x', (2, 1)), ('draw6', 'y', (2, 2))]:
        functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]
        if len(functions) != 1:
            raise ValueError('ambiguous pinned draw function')
        function = functions[0]
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == 'subplots']
        if len(calls) != 1 or len(calls[0].args) < 2 or tuple(ast.literal_eval(v) for v in calls[0].args[:2]) != shape:
            raise ValueError('source subplot layout differs from reviewed relation')
        keywords = {node.arg: node.value for node in calls[0].keywords}
        if 'share' + dimension not in keywords or ast.literal_eval(keywords['share' + dimension]) is not True:
            raise ValueError('source does not declare the required sharing relation')
        result[name] = {'source_sha256': sha(path), 'source_line': function.lineno,
                        'source_end_line': function.end_lineno, 'shape': shape,
                        'share_dimension': dimension, 'share_value': True,
                        'function_ast_sha256': hashlib.sha256(ast.dump(function, include_attributes=False).encode()).hexdigest(),
                        'source_text': '\n'.join(source.splitlines()[function.lineno-1:function.end_lineno])}
    return result


def matrix_for(node, parents, helper):
    lineage = []
    while node is not None:
        lineage.append(node)
        node = parents.get(node)
    matrix = IDENTITY
    for ancestor in reversed(lineage):
        matrix = helper.matrix_multiply(matrix, helper.transform_matrix(ancestor.get('transform')))
    return matrix


def ticks(data, axis, dimension, parents, helper):
    result = []
    for node in data.axes[axis].iter():
        if not isinstance(node.tag, str) or not node.get('id', '').startswith(dimension + 'tick_'):
            continue
        uses = [child for child in node.iter() if child.tag == helper.SVG_NS+'use'
                and child.get('x') is not None and child.get('y') is not None]
        if not uses:
            continue
        use = uses[0]
        x, y = helper.moved(matrix_for(use, parents, helper), finite(use.get('x')), finite(use.get('y')))
        result.append({'tick_id': node.get('id'), 'label': helper.label_of(node),
                       'numeric_label': helper.numeric_label(node), 'position_pt': x if dimension == 'x' else y})
    return result


def frame(data, axis, parents, helper):
    axis_node = data.axes[axis]
    if matrix_for(axis_node, parents, helper) != IDENTITY:
        raise ValueError('nonidentity axes ancestor transform needs separate treatment')
    references = {child.get('clip-path') for child in axis_node.iter()
                  if isinstance(child.tag, str) and child.get('clip-path')}
    if len(references) != 1:
        raise ValueError('axis does not have one independently identifiable data clip frame')
    reference = next(iter(references))
    match = re.fullmatch(r'url\(#([^)]*)\)', reference)
    if not match:
        raise ValueError('unsupported clip reference')
    clips = [node for node in data.root.iter() if node.tag == helper.SVG_NS+'clipPath' and node.get('id') == match[1]]
    rectangles = [node for clip in clips for node in clip if node.tag == helper.SVG_NS+'rect']
    if len(rectangles) != 1 or matrix_for(rectangles[0], parents, helper) != IDENTITY:
        raise ValueError('clip frame is not one untransformed rectangle')
    rect = rectangles[0]
    x, y, width, height = [finite(rect.get(key)) for key in ('x', 'y', 'width', 'height')]
    expected_corners = {(x, y), (x+width, y), (x+width, y+height), (x, y+height)}
    patch_proof = []
    for child in axis_node:
        if not child.get('id', '').startswith('patch_'):
            continue
        for path in child.iter():
            if path.tag != helper.SVG_NS+'path':
                continue
            points = [helper.moved(matrix_for(path, parents, helper), a, b)
                      for a, b in helper.path_points(path.get('d'))]
            if len(points) >= 4 and all(any(max(abs(a-c), abs(b-d)) <= GEOMETRY_TOLERANCE for a, b in points)
                                       for c, d in expected_corners):
                patch_proof.append(child.get('id'))
    if len(patch_proof) != 1:
        raise ValueError('actual clip rectangle not independently matched by one actual axes-background patch')
    return {'x': x, 'y': y, 'width': width, 'height': height,
            'clip_id': match[1], 'background_patch_id': patch_proof[0]}


def fit(visible, logarithmic):
    labeled = [row for row in visible if row['numeric_label'] is not None]
    if len(labeled) < 2:
        raise ValueError('insufficient visible numeric donor ticks')
    xy = [(math.log10(finite(row['numeric_label'])) if logarithmic else finite(row['numeric_label']),
           row['position_pt']) for row in labeled]
    xx, yy = zip(*xy)
    mx, my = sum(xx)/len(xx), sum(yy)/len(yy)
    variance = sum((x-mx)**2 for x in xx)
    if variance <= 0:
        raise ValueError('donor tick values have zero variance')
    slope = sum((x-mx)*(y-my) for x, y in xy)/variance
    intercept = my-slope*mx
    residual = max(abs(slope*x+intercept-y) for x, y in xy)
    if residual > .35:
        raise ValueError('donor actual ticks do not fit one numeric transform')
    return {'slope': slope, 'intercept': intercept, 'log10': logarithmic,
            'visible_tick_count': len(labeled), 'max_fit_residual_pt': residual, 'visible_ticks': labeled}


def calibrations(data, number, helper, proof):
    parents = {child: node for node in data.root.iter() for child in node}
    frames = [frame(data, index, parents, helper) for index in range(len(data.axes))]
    records = {}
    for axis in range(len(data.axes)):
        for dimension in ('x', 'y'):
            observed = ticks(data, axis, dimension, parents, helper)
            visible = [row for row in observed if row['numeric_label'] is not None]
            donor = axis
            sharing = None
            if len(visible) < 2:
                donor = 1 if number == 5 and axis == 0 and dimension == 'x' else axis-1 if number == 6 and axis in (1, 3) and dimension == 'y' else None
                if donor is None:
                    raise ValueError('unexpected hidden axis; no guessed calibration')
                declared = proof['draw5' if number == 5 else 'draw6']
                if declared['share_dimension'] != dimension or declared['share_value'] is not True:
                    raise ValueError('required source sharing relation absent')
                donor_ticks = ticks(data, donor, dimension, parents, helper)
                aa = sorted(row['position_pt'] for row in observed)
                bb = sorted(row['position_pt'] for row in donor_ticks)
                bound_keys = ('x', 'width') if dimension == 'x' else ('y', 'height')
                frame_delta = max(abs(frames[axis][key]-frames[donor][key]) for key in bound_keys)
                if not aa or len(aa) != len(bb):
                    raise ValueError('actual hidden and donor tick marks differ in count')
                tick_delta = max(abs(a-b) for a, b in zip(aa, bb))
                if frame_delta > GEOMETRY_TOLERANCE or tick_delta > GEOMETRY_TOLERANCE:
                    raise ValueError('actual shared-frame/tick equality not established')
                sharing = {'source_function': 'draw5' if number == 5 else 'draw6',
                           'hidden_axis': axis, 'visible_donor_axis': donor,
                           'dimension': dimension, 'actual_frame_delta_pt': frame_delta,
                           'all_actual_tick_positions_count': len(aa), 'max_tick_position_delta_pt': tick_delta,
                           'hidden_ticks': observed, 'donor_ticks': donor_ticks,
                           'proof': 'Source declares sharing AND actual clip/background frames align on this dimension AND every actual tick mark position agrees. No transfer based solely on configuration.'}
                visible = donor_ticks
            record = fit(visible, dimension == 'x')
            record.update(axis=axis, dimension=dimension, donor_axis=donor, shared_axis_evidence=sharing)
            records[f'{axis}:{dimension}'] = record
    return records, frames


def actual_points(data, helper):
    """Keep data-artist provenance and exclude grid, axis ticks, defs and legends."""
    axes = []
    for axis in data.axes:
        result = []
        for child in axis:
            artist = child.get('id', '')
            if not artist.startswith(('line2d_', 'LineCollection_', 'PathCollection_')):
                continue
            if not any(node.get('clip-path') for node in child.iter()):
                continue
            def walk(node, matrix=IDENTITY):
                if not isinstance(node.tag, str) or node.tag == helper.SVG_NS+'defs':
                    return
                current = helper.matrix_multiply(matrix, helper.transform_matrix(node.get('transform')))
                if node.tag == helper.SVG_NS+'use' and node.get('x') is not None and node.get('y') is not None:
                    x, y = helper.moved(current, finite(node.get('x')), finite(node.get('y')))
                    result.append({'x': x, 'y': y, 'artist_id': artist, 'element': 'use'})
                elif node.tag == helper.SVG_NS+'path':
                    for x, y in helper.path_points(node.get('d')):
                        x, y = helper.moved(current, x, y)
                        result.append({'x': x, 'y': y, 'artist_id': artist, 'element': 'path'})
                for descendant in node:
                    walk(descendant, current)
            walk(child)
        axes.append(result)
    return axes


def expected_points(number, rows):
    expected = []
    keys = ('condition', 'path', 'logical_bytes') if number == 5 else ('distribution', 'precision', 'dimension')
    expected_rows = 42 if number == 5 else 64
    if len(rows) != expected_rows or len({tuple(row[key] for key in keys) for row in rows}) != expected_rows:
        raise ValueError('CSV row count or unique identities differ from current exported matrix')
    for line, row in enumerate(rows, 2):
        if number == 5:
            if row['condition'] not in ('cold', 'hot'):
                raise ValueError('unknown state condition')
            axis, x, metrics = (0 if row['condition'] == 'cold' else 1), finite(row['logical_bytes'])/1024, ('p50_ms', 'p95_ms')
        else:
            axis = ('gaussian', 'sparse', 'heavy_tail', 'near_tie').index(row['distribution'])
            x, metrics = finite(row['encoded_embedding_scale_bytes_mean']), ('decision_rate', 'wilson95_low', 'wilson95_high')
        for metric in metrics:
            y = finite(row[metric])
            if x <= 0:
                raise ValueError('logarithmic CSV x coordinate is not positive')
            expected.append({'csv_line': line, 'identity': json.dumps({key: row[key] for key in keys}, sort_keys=True),
                             'axis': axis, 'x': x, 'y': y, 'metric': metric})
    return expected


def evaluate(svg, csv_path, number, helper, proof):
    data = helper.SVGData(svg)
    if len(data.axes) != (2 if number == 5 else 4):
        raise ValueError('unexpected actual SVG axes count')
    viewbox = [finite(value) for value in data.root.get('viewBox').split()]
    for key, expected in [('width', viewbox[2]), ('height', viewbox[3])]:
        text = data.root.get(key, '')
        if not text.endswith('pt') or abs(finite(text[:-2])-expected) > .00001:
            raise ValueError('SVG user units not independently verified as points')
    calibration, frames = calibrations(data, number, helper, proof)
    observed = actual_points(data, helper)
    with csv_path.open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    recipe = expected_points(number, rows)
    matches = []
    for item in recipe:
        axis = item['axis']
        cx, cy = calibration[f'{axis}:x'], calibration[f'{axis}:y']
        x = cx['slope']*math.log10(item['x'])+cx['intercept']
        y = cy['slope']*item['y']+cy['intercept']
        def eligible(point):
            if item['metric'].startswith('wilson95_'):
                return point['artist_id'].startswith('LineCollection_') and point['element'] == 'path'
            return point['artist_id'].startswith('line2d_') and point['element'] == ('path' if item['metric'] == 'p95_ms' else 'use')
        candidates = [point for point in observed[axis] if eligible(point)]
        if not candidates:
            raise ValueError('no actual appropriate data artist for expected point or CI endpoint')
        nearest = min(candidates, key=lambda point: max(abs(point['x']-x), abs(point['y']-y)))
        error = max(abs(nearest['x']-x), abs(nearest['y']-y))
        matches.append({'file': svg.name, **item, 'expected_svg_x_pt': x, 'expected_svg_y_pt': y,
                        'actual_svg_x_pt': nearest['x'], 'actual_svg_y_pt': nearest['y'],
                        'actual_artist_id': nearest['artist_id'], 'actual_element': nearest['element'],
                        'max_abs_coordinate_error_pt': error, 'status': 'PASS' if error <= TOLERANCE else 'FAIL'})
    failures = [row for row in matches if row['status'] != 'PASS']
    result = {'file': svg.name, 'status': 'PASS' if not failures else 'FAIL', 'expected_positions': len(recipe),
              'checked_positions': len(matches), 'mismatched_positions': len(failures),
              'max_coordinate_error_pt': max(row['max_abs_coordinate_error_pt'] for row in matches),
              'metric_counts': {metric: sum(row['metric'] == metric for row in matches) for metric in sorted({row['metric'] for row in matches})},
              'actual_axes_frames': frames, 'calibration': calibration,
              'scope': 'All CSV p50/p95 points for figure5; all decision-rate markers and actual errorbar-stem Wilson CI endpoints for figure6. Actual appropriate artist kind required; nearest point within .4pt. Coincident coordinates need not uniquely identify one coincident series.'}
    return result, matches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', required=True, type=Path)
    parser.add_argument('--report-source', required=True, type=Path)
    parser.add_argument('--qa-helper', required=True, type=Path)
    parser.add_argument('--prior-qa', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    report, destination = args.report_dir.resolve(), args.destination.resolve()
    if destination.exists() or destination.is_relative_to(report):
        raise ValueError('supplement target must be new and outside report')
    destination.mkdir(parents=True)
    receipt = {'status': 'RUNNING', 'started_utc': utc(), 'pid': os.getpid(),
               'command': [sys.executable, *sys.argv], 'source_sha256': sha(Path(__file__)),
               'container_operations': 0, 'provider_calls': 0, 'raw_opened': False,
               'plotting_module_imported_or_figures_regenerated': False, 'prior_QA_modified': False}
    tick = time.perf_counter()
    before = {}
    results, matches = [], []
    code = 2
    try:
        inputs = [args.report_source, args.qa_helper, args.prior_qa]
        inputs += [report/'figures'/f'figure_{number:02}.{extension}' for number in (5, 6) for extension in ('csv',)]
        inputs += [report/'figures'/f'figure_{number:02}_{size}.svg' for number in (5, 6) for size in ('single', 'double')]
        before = {str(path.resolve()): sha(path) for path in inputs}
        if sha(args.report_source) != REPORT_SHA or sha(args.qa_helper) != HELPER_SHA or sha(args.prior_qa) != PRIOR_QA_SHA:
            raise ValueError('reviewed report/parser/prior-QA identity differs')
        proof = source_share_proof(args.report_source)
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location('pinned_original_svg_primitives', args.qa_helper)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)  # definitions only; audit()/main() never called
        for number in (5, 6):
            for size in ('single', 'double'):
                svg = report/'figures'/f'figure_{number:02}_{size}.svg'
                try:
                    result, current = evaluate(svg, report/'figures'/f'figure_{number:02}.csv', number, helper, proof)
                    results.append(result)
                    matches.extend(current)
                except (ValueError, KeyError, IndexError) as error:
                    results.append({'file': svg.name, 'status': 'UNKNOWN', 'reason': str(error), 'no_guessed_calibration': True})
        after = {name: sha(Path(name)) for name in before}
        stable = before == after
        code = 2 if not stable or any(row['status'] == 'FAIL' for row in results) else 3 if any(row['status'] == 'UNKNOWN' for row in results) else 0
        audit = {'status': 'PASS_SHARED_AXIS_NUMERIC_SUPPLEMENT' if code == 0 else 'FAIL' if code == 2 else 'UNKNOWN',
                 'tolerance_svg_pt': TOLERANCE, 'shared_frame_and_tick_tolerance_pt': GEOMETRY_TOLERANCE,
                 'actual_svg_files': 4, 'expected_positions': 552,
                 'checked_positions': len(matches), 'mismatched_positions': sum(row['status'] != 'PASS' for row in matches),
                 'max_coordinate_error_pt': max((row['max_abs_coordinate_error_pt'] for row in matches), default=None),
                 'file_results': results, 'source_sharing_proof': proof, 'input_sha256_before': before,
                 'input_sha256_after': after, 'inputs_unchanged': stable,
                 'original_QA_status': 'Original213057 findings/e406 helper histories remain unchanged:351PASS/3N_A/4UNKNOWN/1INCOMPLETE. This separately evidenced supplement resolves calibration only; missing usage remains INCOMPLETE.',
                 'measurement_boundary': 'Generated main report SVG/CSV only; no raw re-analysis, state phase supplement, provider usage repair, visual rerender or statistical recomputation.',
                 'parser_independence_boundary': 'Pinned e406 XML/path/transform/numeric-label primitives reused without alteration; independent tick regression, actual shared-frame/tick correspondence, data-artist provenance and CSV recipes implemented here.'}
        if code == 0 and (len(matches) != 552 or len(results) != 4):
            raise ValueError('complete supplement expected552 positions over exactly4SVGs')
        dump(destination/'audit.json', audit)
        with (destination/'coordinate_matches.csv').open('x', newline='', encoding='utf-8') as stream:
            fields = list(matches[0]) if matches else ['file', 'status']
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(matches)
        receipt.update(status=audit['status'], exit_code=code, input_sha256_before=before,
                       input_sha256_after=after, inputs_unchanged=stable,
                       audit_sha256=sha(destination/'audit.json'), matches_sha256=sha(destination/'coordinate_matches.csv'))
    except Exception as error:
        receipt.update(status='ERROR_RETAINED', exit_code=2, error_type=type(error).__name__, error=str(error))
        code = 2
    finally:
        receipt.update(finished_utc=utc(), wall_sec=time.perf_counter()-tick)
        dump(destination/'execution_receipt.json', receipt)
    print(json.dumps({'status': receipt['status'], 'exit_code': code, 'destination': str(destination)}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
