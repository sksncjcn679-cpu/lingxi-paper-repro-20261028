"""fable8: schema key atoms + columnar records — lossless property tests (seeded fuzz)."""

from __future__ import annotations

import json
import random

import pytest

from agent_runtime.protocol.schema_atoms import KEY_ATOMS, decode_value, encode_value

TRICKY_KEYS = ["$", "$$", "$t", "$r", "$m", "$1", "$$1", "^0", "~s", "", "plan", "operands", "x", "y", "name"]
TRICKY_STRS = ["$1", "^0", "^^x", "~s" + "A" * 22, "", "中文", "\u0000", "$t"]


def _rand(rng: random.Random, depth: int = 0):
    r = rng.random()
    if depth > 3 or r < 0.35:
        return rng.choice([None, True, False, 0, -7, 3.5, rng.randint(0, 10**9), rng.choice(TRICKY_STRS)])
    if r < 0.6:
        n = rng.randint(0, 5)
        if rng.random() < 0.5:  # record list, sometimes heterogeneous / reordered
            base = rng.sample(TRICKY_KEYS, rng.randint(1, 4))
            rows = []
            for _ in range(n + 2):
                keys = [k for k in base if rng.random() < 0.8] or base[:1]
                if rng.random() < 0.1:
                    keys = list(reversed(keys))
                rows.append({k: _rand(rng, depth + 1) for k in keys})
            return rows
        return [_rand(rng, depth + 1) for _ in range(n)]
    return {k: _rand(rng, depth + 1) for k in rng.sample(TRICKY_KEYS, rng.randint(0, 5))}


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=False, ensure_ascii=False) == json.dumps(b, sort_keys=False, ensure_ascii=False)


@pytest.mark.parametrize("seed", range(40))
def test_encode_decode_is_exact_including_key_order(seed):
    rng = random.Random(seed)
    for _ in range(50):
        v = _rand(rng)
        wire = json.loads(json.dumps(encode_value(v), ensure_ascii=False))
        assert _same(decode_value(wire), v)


def test_atoms_and_columnar_actually_apply():
    rows = [{"name": f"p{i}", "kind": "py", "status": "ok"} for i in range(5)]
    e = encode_value({"plan": {"operation": "x"}, "rows": rows})
    assert "$14" in e and "$11" in e["$14"]
    assert set(e["rows"]) == {"$t", "$r"}
    het = rows[:2] + [{"name": "q", "kind": "c", "abi": "i686", "status": "rebuild"}]
    assert "$m" in encode_value(het)


def test_malformed_inputs_are_rejected():
    for bad in ({"$999": 1}, {"$x": 1}, {"$t": ["a", "b"], "$r": [[1, 2]]},
                {"$t": ["a", "b"], "$r": [[1], [2], [3]]}, {"$t": ["a", "b"], "$r": [[1, 2]] * 3, "$m": [0, 1, 1]},
                {"$t": ["a", "a"], "$r": [[1, 2]] * 3}, {"$t": ["a", "b"], "$r": [[1, 2]] * 3, "junk": 1}):
        with pytest.raises(ValueError):
            decode_value(bad)


def test_atom_table_is_backed_by_capability_schemas():
    from agent_runtime.config import load_config
    from agent_runtime.runtime.builder import build_default_agents

    declared = set()
    for agent in build_default_agents(load_config(load_env=False)).values():
        for cap in agent.capability_specs:
            declared |= set(cap.input_schema) | set(cap.output_schema)
    runtime_fields = {"workflow_state_ref", "plan_ref", "facts_ref", "evidence_ref", "items", "answer",
                      "topic", "question", "defect", "memory_fast_path",
                      "stage", "plan_ref", "execution", "has_value", "value_type", "review", "count", "keys"}
    assert set(KEY_ATOMS) <= declared | runtime_fields
    assert len(set(KEY_ATOMS)) == len(KEY_ATOMS)


@pytest.mark.parametrize("seed", range(10))
def test_full_session_codec_roundtrip_with_fuzzed_payloads(seed):
    from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
    from agent_runtime.protocol.session_codec import SessionWireCodec

    rng = random.Random(1000 + seed)
    enc = SessionWireCodec(predictive=True, schema_atoms=True)
    dec = SessionWireCodec(predictive=True, schema_atoms=True)
    ref_enc, ref_dec = SessionWireCodec(), SessionWireCodec()
    prev = None
    for i in range(12):
        payload = {k: _rand(rng) for k in rng.sample(TRICKY_KEYS + ["plan", "facts"], 3) if k}
        m = AgentMessage(
            trace_id="trace_aaaaaaaaaaaa", task_id="task_bbbbbbbbbb", step_id=f"s{i}",
            source_agent="planner", target_agent="retriever", action_type=ActionType.RETRIEVE,
            capability="retrieval", sequence_no=i + 1, status=MessageStatus.OK if i % 2 else MessageStatus.PENDING,
            parent_message_id=prev, input_parameters=payload, result=({"summary": payload} if i % 2 else None),
        )
        prev = m.message_id
        got = dec.decode_msgpack(enc.encode_dual(m)[1]).model_dump(mode="json")
        want = ref_dec.decode_msgpack(ref_enc.encode_dual(m)[1]).model_dump(mode="json")
        assert json.dumps(got, ensure_ascii=False) == json.dumps(want, ensure_ascii=False)


def test_llm_view_expands_atoms_but_wire_keeps_them():
    from agent_runtime.protocol.message import ActionType, AgentMessage
    from agent_runtime.observability.metrics import MetricsCollector
    from agent_runtime.providers.tokenizer import get_tokenizer
    from agent_runtime.runtime.channel import Channel

    ch = Channel("structured", MetricsCollector(), get_tokenizer(), structured_wire_codec="session_msgpack", header_prediction=True, schema_atoms=True)
    m = AgentMessage(trace_id="trace_aaaaaaaaaaaa", task_id="task_bbbbbbbbbb", step_id="s0", source_agent="orchestrator",
                     target_agent="planner", action_type=ActionType.PLAN, capability="task_planning",
                     input_parameters={"description": "修复服务", "operands": ["a", "b"]})
    ch.send(m)
    wire = ch.last_wire_by_id[m.message_id]
    view = ch.llm_view(m.message_id)
    assert '"$2"' in wire and "description" not in wire
    assert '"description":"修复服务"' in view and '"$2"' not in view
    ch.close()
