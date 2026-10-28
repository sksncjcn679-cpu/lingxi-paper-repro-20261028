"""PROPOSAL ONLY: explicit generation-aware state audit; no raw-file CLI.

The unchanged legacy audit_state remains in the exact0d9a preimage and in the
current full_metric_audit.py. This new entry is NOT an AST-identical reuse of
that function. Nothing installs, monkeypatches or calls this from the main audit.
Inputs are in-memory rows already isolated to one recorded task unit.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import importlib.machinery
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREIMAGE = HERE / 'full_metric_audit.py.preimage'
PREIMAGE_SHA256 = '0d9a564522c8e61bf9ed19c143ad7a452415ae721d349fc62aaef79edfbc34cf'


def legacy_module():
    if hashlib.sha256(PREIMAGE.read_bytes()).hexdigest() != PREIMAGE_SHA256:
        raise ValueError('legacy full-auditor preimage changed')
    loader = importlib.machinery.SourceFileLoader('_state_lifecycle_legacy_0d9a', str(PREIMAGE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


LEGACY = legacy_module()
integer, number, sha256_string = LEGACY.integer, LEGACY.number, LEGACY.sha256_string
audit_state_legacy = LEGACY.audit_state


def interval(row):
    return integer(row.get('start_ns')) and integer(row.get('end_ns')) and row['start_ns'] <= row['end_ns']


def _other_state_rules(a, run_id, task, rows):
    """Preserved non-lifecycle predicates, explicitly separated from new joins."""
    for row in rows:
        if 'start_ns' in row and 'end_ns' in row:
            a.require(interval(row), 'STATE_BOUNDARY_INVALID', run_id, event=row.get('event'))
    consumed = [r for r in rows if r.get('event') == 'state_consumer_compute']
    releases = [r for r in rows if r.get('event') == 'state_buffer_release']
    mats = [r for r in rows if r.get('event') == 'state_buffer_materialize' and not r.get('error_type')]
    maps = [r for r in rows if r.get('event') == 'state_os_mapping']
    for con in consumed:
        a.require(con.get('read_and_compute_fused') is True,
                  'NATIVE_FUSED_READ_COMPUTE_SEMANTICS_LOST', run_id)
        parts = [con.get(k) for k in ('read_similarity_compute_ns', 'stable_sort_ns', 'reorder_ns')]
        a.require(interval(con) and all(integer(v) for v in parts)
                  and sum(parts) == con['end_ns'] - con['start_ns'],
                  'CONSUMER_COMPUTE_PARTITION_MISMATCH', run_id)
    for mat in mats:
        key = mat.get('buffer_key')
        a.require(any(r.get('buffer_key') == key and r.get('buffer_key_absent') is True for r in releases),
                  'MATERIALIZED_COMPONENT_NOT_RELEASED', run_id, buffer_key=key)
        matching = [m for m in maps if m.get('buffer_key') == key and interval(mat) and interval(m)
                    and mat['start_ns'] <= m['start_ns'] <= m['end_ns'] <= mat['end_ns']]
        a.require(integer(mat.get('mapping_event_count')) and mat['mapping_event_count'] == len(matching),
                  'MAPPING_EVENT_COUNT_MISMATCH', run_id)
        status = 'NEW_NATIVE_MAPPING_OBSERVED' if matching else 'NO_NEW_MAP_EXISTING_ARENA_OR_INPROC'
        a.require(mat.get('mapping_status') == status, 'MAPPING_REUSE_MISLABELLED', run_id)
    for release in releases:
        a.require(release.get('buffer_key_absent') is True, 'STATE_BUFFER_REMAINED_AFTER_RELEASE', run_id)
        if release.get('shared_release_group'):
            a.require(release.get('individual_uri_release_ns') is None,
                      'SHARED_RELEASE_TIMING_DUPLICATED_PER_URI', run_id)
    handoffs = [r for r in rows if r.get('event') == 'state_rpc_handoff']
    for handoff in handoffs:
        a.require(integer(handoff.get('reply_payload_bytes')) and
                  handoff.get('reply_framed_socket_bytes') == handoff['reply_payload_bytes'] + 4,
                  'STATE_RPC_FRAMING_MISMATCH', run_id)
        a.require(handoff.get('consumer_pid') in (task.get('worker_pids') or {}).values(),
                  'STATE_RPC_CONSUMER_NOT_RECORDED_WORKER', run_id)
    if handoffs and number(task.get('observable_ipc_bytes')):
        a.require(all(integer(r.get('reply_framed_socket_bytes')) for r in handoffs)
                  and sum(r['reply_framed_socket_bytes'] for r in handoffs) <= task['observable_ipc_bytes'],
                  'STATE_RPC_BYTES_EXCEED_TASK_SOCKET_TOTAL', run_id)
    return consumed, releases, mats, maps, handoffs


def _components(a, run_id, rows, mats, releases):
    """Track each physical buffer-key materialization, not an entire URI label."""
    result = []
    for mat in mats:
        if not interval(mat):
            continue  # Invalid boundaries are already explicit FAIL findings.
        key, uri = mat.get('buffer_key'), mat.get('uri')
        if not a.require(isinstance(key, str) and key.split('#', 1)[0] == uri,
                         'COMPONENT_URI_KEY_MISMATCH', run_id, buffer_key=key):
            continue
        future_puts = [r for r in mats if r is not mat and r.get('buffer_key') == key and interval(r)
                       and r['start_ns'] >= mat['end_ns']]
        next_put = min((r['start_ns'] for r in future_puts), default=None)
        following = [r for r in releases if r.get('buffer_key') == key and r.get('buffer_key_absent') is True
                     and interval(r) and r['start_ns'] >= mat['end_ns']]
        released = min(following, key=lambda r: (r['start_ns'], r['end_ns'])) if following else None
        a.require(released is not None, 'MATERIALIZATION_GENERATION_NOT_RELEASED', run_id,
                  buffer_key=key, materialize_end_ns=mat['end_ns'])
        if next_put is not None:
            a.require(released is not None and released['end_ns'] <= next_put,
                      'REMATERIALIZATION_WITHOUT_PRIOR_COMPONENT_RELEASE', run_id, buffer_key=key)
        overlapping = [r for r in mats if r is not mat and r.get('buffer_key') == key and interval(r)
                       and r['start_ns'] < mat['end_ns'] and r['end_ns'] > mat['start_ns']]
        a.require(not overlapping, 'OVERLAPPING_SAME_KEY_MATERIALIZATION', run_id, buffer_key=key)
        ends = ([released['start_ns']] if released else []) + ([next_put] if next_put is not None else [])
        result.append({'row': mat, 'key': key, 'uri': uri, 'start_ns': mat['start_ns'],
            'end_ns': mat['end_ns'], 'live_until_ns': min(ends) if ends else None,
            'release': released})
    return result


def _live(generation, left, right):
    return generation['ready_ns'] <= left and all(component['end_ns'] <= left and
        (component['live_until_ns'] is None or right <= component['live_until_ns'])
        for component in generation['components'].values())


def _consumer_hash(a, run_id, con, rows, generations):
    explicit = con.get('content_hash')
    if explicit is None and isinstance(con.get('reference'), dict):
        explicit = con['reference'].get('content_hash')
    if explicit is not None:
        a.require(sha256_string(explicit), 'CONSUMER_REFERENCE_HASH_INVALID', run_id)
        return explicit, 'consumer_reference'
    resolves = [r for r in rows if r.get('event') == 'state_resolve' and r.get('uri') == con.get('uri')
                and not r.get('error_type') and interval(r) and r['end_ns'] <= con['start_ns']]
    if resolves:
        resolved = max(resolves, key=lambda r: (r['end_ns'], r['start_ns']))
        reference = resolved.get('reference') or {}
        value = reference.get('content_hash')
        a.require(reference.get('uri') == con.get('uri') and sha256_string(value),
                  'CONSUMER_RESOLVE_REFERENCE_IDENTITY_MISSING', run_id)
        return value, 'latest_successful_resolve_reference'
    # The frozen compute event carries URI, not a new payload digest. A unique
    # producer hash can identify its observed generation, but is not presented
    # as an independent re-hash of the consumer's ndarray. Once a URI has more
    # than one observed full hash, URI alone cannot choose the content generation.
    hashes = {g['content_hash'] for g in generations if g['uri'] == con.get('uri')
              and g['ready_ns'] <= con['start_ns']}
    if len(hashes) == 1:
        return next(iter(hashes)), 'URI_only_unique_observed_producer_hash'
    a.require(False, 'CONSUMER_HASH_AMBIGUOUS_ACROSS_GENERATIONS', run_id, uri=con.get('uri'))
    return None, 'not_established'


def audit_state_lifecycles(a, run_id, task, rows, spans):
    """New explicit state entry, preserving other rules and replacing all-to-all joins.

    No phase names are examined. Every consume needs a completed true generation
    and every required physical component must survive its entire interval.
    A dedup put is an alias, never a new ready time or a substitute origin.
    """
    consumed, releases, mats, maps, handoffs = _other_state_rules(a, run_id, task, rows)
    produced = [r for r in rows if r.get('event') == 'state_produce' and
                isinstance(r.get('reference'), dict) and r['reference'].get('kind') == 'plan'
                and not r.get('error_type')]
    components = _components(a, run_id, rows, mats, releases)
    generations, aliases = [], []
    for prod in sorted(produced, key=lambda r: (r.get('end_ns', -1), r.get('start_ns', -1))):
        ref, uri = prod['reference'], prod.get('uri')
        identity_ok = a.require(uri == ref.get('uri') and sha256_string(ref.get('content_hash')),
                                'PLAN_REFERENCE_IDENTITY_MISSING', run_id)
        if not interval(prod) or not identity_ok:
            continue
        nested = [c for c in components if c['uri'] == uri and
                  prod['start_ns'] <= c['start_ns'] <= c['end_ns'] <= prod['end_ns']]
        dedup = ref.get('deduplicated')
        if dedup is True:
            a.require(ref.get('materialized_bytes') == 0 and not nested,
                      'DEDUP_REFERENCE_HAS_NEW_MATERIALIZATION', run_id, uri=uri)
            live = [g for g in generations if g['uri'] == uri and _live(g, prod['start_ns'], prod['end_ns'])]
            matched = [g for g in live if g['content_hash'] == ref['content_hash']]
            if not matched:
                a.find('FAIL', 'DEDUP_REFERENCE_HASH_MISMATCH' if live else
                       'DEDUP_WITHOUT_LIVE_MATERIALIZED_GENERATION', run_id, uri=uri)
            else:
                target = max(matched, key=lambda g: g['ready_ns'])
                aliases.append({'uri': uri, 'content_hash': ref['content_hash'],
                    'producer_end_ns': prod['end_ns'], 'generation': target['generation']})
            continue
        real = a.require(dedup is False and integer(ref.get('materialized_bytes')) and ref['materialized_bytes'] > 0,
                         'NONDEDUP_REFERENCE_WITHOUT_REAL_MATERIALIZATION', run_id, uri=uri)
        latest = {}
        for component in sorted(nested, key=lambda c: c['end_ns']):
            latest[component['key']] = component
        required = {uri + '#emb', uri + '#vec'}
        precision = str(ref.get('precision', ''))
        if ref.get('quant_scale') is not None or precision.startswith(('int8', 'int4', 'q4b')):
            required.add(uri + '#scale')
        backed = a.require(required <= set(latest), 'PLAN_PHYSICAL_COMPONENTS_MISSING', run_id,
                           uri=uri, missing_keys=sorted(required - set(latest)))
        if not real or not backed:
            continue
        generation = {'uri': uri, 'content_hash': ref['content_hash'], 'ready_ns': prod['end_ns'],
            'components': latest, 'generation': 1 + sum(g['uri'] == uri for g in generations)}
        a.require(_live(generation, prod['end_ns'], prod['end_ns']),
                  'PLAN_COMPONENT_NOT_LIVE_AT_PRODUCE_COMPLETION', run_id, uri=uri)
        generations.append(generation)

    matches = []
    for con in consumed:
        if not interval(con):
            continue
        uri = con.get('uri')
        a.require(any(p.get('uri') == uri for p in produced), 'CONSUMER_WITHOUT_PRODUCED_PLAN', run_id)
        content_hash, basis = _consumer_hash(a, run_id, con, rows, generations)
        live = [g for g in generations if g['uri'] == uri and _live(g, con['start_ns'], con['end_ns'])]
        matched = [g for g in live if g['content_hash'] == content_hash]
        if not matched:
            ready = [g for g in generations if g['uri'] == uri and g['ready_ns'] <= con['start_ns']]
            code = ('CONSUMER_REFERENCE_HASH_MISMATCH' if live else 'CONSUME_PRECEDES_PRODUCE' if not ready
                    else 'PLAN_RELEASE_OR_REPLACEMENT_PRECEDES_CONSUMPTION_END')
            a.find('FAIL', code, run_id, uri=uri, consume_start_ns=con['start_ns'], consume_end_ns=con['end_ns'])
            continue
        target = max(matched, key=lambda g: g['ready_ns'])
        matches.append({'uri': uri, 'content_hash': content_hash, 'generation': target['generation'],
            'consume_start_ns': con['start_ns'], 'consume_end_ns': con['end_ns'], 'hash_basis': basis})
    # Preserve the legacy URI-level success/failure consumption requirement. An
    # unused but cleaned-up real generation is not invented as a new task failure.
    for uri in sorted({p.get('uri') for p in produced if isinstance(p.get('uri'), str)}):
        a.require(any(r.get('uri') == uri and r.get('buffer_key_absent') is True for r in releases),
                  'PRODUCED_PLAN_NOT_RELEASED', run_id, uri=uri)
        if not any(match['uri'] == uri for match in matches):
            if task.get('success'):
                a.find('FAIL', 'SUCCESSFUL_STATE_PLAN_NOT_CONSUMED', run_id, uri=uri)
            else:
                a.unknown('FAILED_UNIT_PLAN_CONSUMPTION_NOT_OBSERVED', run_id, uri=uri)
    if task.get('state_exchange_enabled') is True and not produced:
        if task.get('success'):
            a.find('FAIL', 'STATE_ENABLED_WITHOUT_OBSERVED_PLAN', run_id)
        else:
            a.unknown('STATE_ENABLED_WITHOUT_OBSERVED_PLAN', run_id)
    return {'produced_plan_uris': len({r.get('uri') for r in produced}),
        'consumed_plan_uris': len({r.get('uri') for r in consumed}),
        'native_mapping_events': len(maps), 'state_rpc_handoffs': len(handoffs),
        'active_state_entry': 'audit_state_lifecycles_proposal_v1',
        'legacy_state_entry_retained_unchanged': True,
        'materialized_generations': [{'uri': g['uri'], 'content_hash': g['content_hash'],
            'generation': g['generation'], 'ready_ns': g['ready_ns'],
            'components': {key: {'materialize_end_ns': c['end_ns'], 'live_until_ns': c['live_until_ns']}
                           for key, c in g['components'].items()}} for g in generations],
        'dedup_aliases': aliases, 'consumption_generation_matches': matches,
        'hash_observation_limit': 'compute events record URI, not a new payload hash; reference hash is joined from successful resolve when present, otherwise only a unique observed producer hash may identify the generation'}
