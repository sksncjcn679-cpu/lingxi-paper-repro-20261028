"""Integrated source wiring + the existing23 pure-data lifecycle fixtures only.

Does not run main(), MetadataIndex, a real unit, a runtime, a model or a full audit.
All output stays in a fresh child of this integration evidence directory.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
CURRENT = BASE.parent / 'full_metric_audit.py'
PREIMAGE = HERE / 'full_metric_audit.py.before'
PROPOSAL = BASE / 'proposed_state_audit.py'
CURRENT_SHA256 = '7c4cc24bb84274bf96a4c05a00d489491798a84455bd596702db762c4977295f'
PREIMAGE_SHA256 = '0d9a564522c8e61bf9ed19c143ad7a452415ae721d349fc62aaef79edfbc34cf'
PROPOSAL_SHA256 = 'a3771a3c964c4d94abb953391fa203ff85ac9f0cf57b92aa024654bd0be64c62'
SIX = ('interval', '_other_state_rules', '_components', '_live', '_consumer_hash', 'audit_state_lifecycles')
NINE = ('digest', 'integer', 'number', 'sha256_string', 'Audit', 'groups', 'audit_usage', 'audit_spans', 'audit_state')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(name, path):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def definitions(tree):
    return {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def same(left, right):
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


def pure_assignment(tree):
    return next(node for node in tree.body if isinstance(node, ast.Assign) and
                any(isinstance(target, ast.Name) and target.id == 'PURE_REUSED' for target in node.targets))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='fixture_validation_v1')
    args = parser.parse_args()
    if not args.version or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.version):
        raise ValueError('new local child-directory name required')
    if (digest(CURRENT) != CURRENT_SHA256 or digest(PREIMAGE) != PREIMAGE_SHA256
            or digest(PROPOSAL) != PROPOSAL_SHA256):
        raise ValueError('integration source identity changed')
    before = ast.parse(PREIMAGE.read_text(encoding='utf-8'))
    current = ast.parse(CURRENT.read_text(encoding='utf-8'))
    proposal = ast.parse(PROPOSAL.read_text(encoding='utf-8'))
    old_defs, new_defs, proposed_defs = definitions(before), definitions(current), definitions(proposal)
    nine_equal = {name: same(old_defs[name], new_defs[name]) for name in NINE}
    six_equal = {name: same(proposed_defs[name], new_defs[name]) for name in SIX}
    pure_equal = same(pure_assignment(before), pure_assignment(current))
    changed = sorted(name for name in old_defs if not same(old_defs[name], new_defs[name]))
    active = [node for node in ast.walk(new_defs['audit_native']) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name) and node.func.id == 'audit_state_lifecycles']
    legacy_calls = [node for node in ast.walk(current) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == 'audit_state']
    versions = [keyword.value.value for node in ast.walk(current) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument'
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == '--version'
                for keyword in node.keywords if keyword.arg == 'default' and isinstance(keyword.value, ast.Constant)]
    external_imports = [node for node in ast.walk(current) if isinstance(node, ast.ImportFrom)
                        and node.module and 'state_lifecycle_audit_correction_v1' in node.module]
    if not (all(nine_equal.values()) and all(six_equal.values()) and pure_equal
            and changed == ['audit_native', 'main'] and len(active) == 1 and not legacy_calls
            and versions == ['full_metric_audit_v3'] and not external_imports):
        raise ValueError('integration AST/source wiring invariants failed')
    # The exact prior fixture class is reused; only its subject adapter changes.
    # This never invokes the prior validator's main or overwrites its receipt.
    fixture_source = module('_state_fixture_definitions_reused', BASE / 'validate_fixtures.py')
    integrated = module('_integrated_full_metric_audit_source', CURRENT)
    adapter = SimpleNamespace(LEGACY=integrated,
        audit_state_lifecycles=integrated.audit_state_lifecycles,
        audit_state_legacy=integrated.audit_state)
    class IntegratedFixtures(fixture_source.StateLifecycleFixtures):
        @classmethod
        def setUpClass(cls):
            cls.module = adapter
            cls.results = []
    destination = HERE / args.version
    destination.mkdir(exist_ok=False)
    transcript = io.StringIO()
    result = unittest.TextTestRunner(stream=transcript, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(IntegratedFixtures))
    (destination / 'unittest_transcript.txt').write_text(transcript.getvalue(), encoding='utf-8')
    (destination / 'fixture_cases.json').write_text(json.dumps(IntegratedFixtures.results,
        ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    unchanged = digest(CURRENT) == CURRENT_SHA256 and digest(PREIMAGE) == PREIMAGE_SHA256
    receipt = {'created_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'PASS' if result.wasSuccessful() and unchanged else 'FAIL',
        'tests_run': result.testsRun, 'fixture_cases': len(IntegratedFixtures.results),
        'failures': len(result.failures), 'errors': len(result.errors),
        'integrated_auditor_sha256': CURRENT_SHA256, 'preimage_sha256': PREIMAGE_SHA256,
        'reviewed_proposal_sha256': PROPOSAL_SHA256,
        'new_six_definitions_AST_same_as_proposal': six_equal,
        'legacy_nine_definitions_AST_unchanged': nine_equal, 'PURE_REUSED_assignment_AST_unchanged': pure_equal,
        'changed_old_definitions': changed,
        'active_state_callsite': 'audit_native calls audit_state_lifecycles exactly once',
        'legacy_state_ast_retained_but_not_active': not legacy_calls,
        'default_version': versions[0], 'external_proposal_imports': len(external_imports),
        'current_source_unchanged_during_fixtures': unchanged,
        'scope': 'integrated source AST/wiring and reused23 in-memory fixtures only; no real raw, model, runtime, Docker, full audit, statistics or downstream binding change',
        'validation_source_sha256': digest(Path(__file__)),
        'reused_fixture_source_sha256': digest(BASE / 'validate_fixtures.py'),
        'files': {p.name: digest(p) for p in sorted(destination.iterdir()) if p.is_file()}}
    with (destination / 'receipt.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'status': receipt['status'], 'tests_run': result.testsRun,
                      'fixture_cases': len(IntegratedFixtures.results), 'source_sha256': CURRENT_SHA256}))
    raise SystemExit(0 if receipt['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
