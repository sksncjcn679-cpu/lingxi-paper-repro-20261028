"""fable7 Protocol 1.4: context-predictive header compression, routines, reference table."""

from __future__ import annotations

import json

import pytest

from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.protocol.routine import (
    ROUTINES,
    PredictorContext,
    RefTable,
    Routine,
    compress,
    expand,
    reset_routine_registry,
)
from agent_runtime.protocol.session_codec import SessionWireCodec



def _pipeline(trace="trace_aaaaaaaaaaaa", task="task_bbbbbbbbbb"):
    """Request/reply pipeline orchestrator->planner->retriever->executor->reviewer->orchestrator."""
    agents = ["orchestrator", "planner", "retriever", "executor", "reviewer"]
    actions = [ActionType.PLAN, ActionType.RETRIEVE, ActionType.EXECUTE, ActionType.REVIEW]
    REF_A = f"state://{task}/" + "ab" * 16
    msgs = []
    prev = None
    seq = 1
    for i, action in enumerate(actions):
        src = agents[i] if i else "orchestrator"
        req = AgentMessage(
            trace_id=trace, task_id=task, step_id=f"s{i}", source_agent=src, target_agent=agents[i + 1],
            action_type=action, capability="task_planning" if i == 0 else "retrieval",
            parent_message_id=prev.message_id if prev else None, causation_id=prev.message_id if prev else None,
            sequence_no=seq, state_reference=REF_A if i else None, input_parameters={"k": i, "ref": REF_A},
        )
        seq += 1
        rep = AgentMessage(
            trace_id=trace, task_id=task, step_id=f"s{i}", source_agent=agents[i + 1], target_agent=src,
            action_type=action, capability=req.capability, status=MessageStatus.OK,
            parent_message_id=req.message_id, causation_id=req.message_id, sequence_no=seq,
            state_reference=REF_A, result={"v": i},
        )
        seq += 1
        msgs += [req, rep]
        prev = rep
    return msgs


def _roundtrip(msgs, routine=None):
    enc = SessionWireCodec(predictive=True, routine=routine)
    dec = SessionWireCodec(predictive=True, routine=routine)
    ref_enc, ref_dec = SessionWireCodec(), SessionWireCodec()
    tokens = 0
    for m in msgs:
        text, binary = enc.encode_dual(m)
        tokens += len(text)
        got = dec.decode_msgpack(binary).model_dump(mode="json")
        want = ref_dec.decode_msgpack(ref_enc.encode_dual(m)[1]).model_dump(mode="json")
        assert got == want
    return enc, tokens


def test_cphc_is_lossless_without_and_with_routine():
    msgs = _pipeline()
    enc, cold = _roundtrip(msgs)
    routine = Routine.learn(enc.encoder_observed)
    enc2, warm = _roundtrip(_pipeline(trace="trace_cccccccccccc", task="task_dddddddddd"), routine)
    assert warm < cold
    assert enc2.prediction_stats["explicit_fields"] <= 2
    assert enc2.prediction_stats["reference_table_hits"] > 0


def test_cphc_misprediction_is_still_lossless():
    msgs = _pipeline()
    enc, _ = _roundtrip(msgs)
    routine = Routine.learn(enc.encoder_observed)
    odd = _pipeline(trace="trace_eeeeeeeeeeee", task="task_ffffffffff")
    odd[3] = odd[3].model_copy(update={"status": MessageStatus.ERROR, "target_agent": "reviewer", "sequence_no": 99})
    _roundtrip(odd, routine)


def test_predicted_but_absent_fields_use_nx():
    ctx_e, ctx_d = PredictorContext(), PredictorContext()
    first = {"st": 0, "s": 0, "t": 1, "a": 3, "c": 0, "sq": 1}
    reply_without_status = {"st": 0, "s": 1, "t": 0, "a": 3, "c": 0, "sq": 2, "pm": -1}
    for frame in (first, reply_without_status):
        wire = compress(dict(frame), ctx_e)
        assert expand(json.loads(json.dumps(wire)), ctx_d) == frame
    assert "nx" in wire and "sta" in wire["nx"] and "ca" in wire["nx"]


def test_reference_table_escapes_caret_strings():
    enc, dec = RefTable(), RefTable()
    ref = "~s" + "A" * 22
    value = {"a": ref, "b": [ref, "^3", "^^x", "plain"], "c": ref}
    wire = enc.encode(value)
    assert wire["c"] == "^0" and wire["b"][1] == "^^3"
    assert dec.decode(json.loads(json.dumps(wire))) == value
    with pytest.raises(ValueError):
        RefTable().decode("^7")


def test_routine_document_digest_and_validation():
    routine = Routine.learn([{"st": 0, "s": 0, "t": 1, "a": 3, "c": 0}])
    again = Routine.from_json(routine.to_json())
    assert again.digest == routine.digest
    with pytest.raises(ValueError):
        Routine.from_json(json.dumps({"cphc": 1, "tr": {"^": {"evil": 1}}}))


def test_non_predictive_decoder_rejects_cphc_frames():
    enc = SessionWireCodec(predictive=True)
    msgs = _pipeline()
    enc.encode_dual(msgs[0])
    _, binary = enc.encode_dual(msgs[1])
    plain = SessionWireCodec()
    plain.decode_msgpack(SessionWireCodec().encode_dual(msgs[0])[1])
    with pytest.raises(Exception):
        plain.decode_msgpack(binary)


def test_uds_router_negotiates_uploads_once_and_verifies_every_frame(tmp_path):
    import socket

    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX unavailable")
    from agent_runtime.config import load_config
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.evaluation.runner import ExperimentConfig, run_once

    cfg = ExperimentConfig(
        "SS", "x", "structured", True, False, True, structured_wire_codec="session_msgpack",
        control_transport="uds", state_shared_memory=True, state_precision="adaptive",
    )
    row = run_once(cfg, _self_contained_tasks, 42)
    assert row["ipc_verified"] == row["ipc_frames"] == row["message_count"]
    assert row["routine_uploads"] == 1          # learned after task 1, uploaded once
    assert row["routine_references"] >= 2       # later tasks reference the digest only
    assert row["routine_upload_tokens"] > 0     # the upload is charged to the totals
    assert row["cphc_reference_hits"] > 0


def test_header_prediction_can_be_disabled_for_ablation(monkeypatch):
    from agent_runtime.config import load_config

    monkeypatch.delenv("AGENT_HEADER_PREDICTION", raising=False)
    assert load_config(load_env=False).header_prediction is True
    monkeypatch.setenv("AGENT_HEADER_PREDICTION", "0")
    assert load_config(load_env=False).header_prediction is False
    reset_routine_registry()
    assert ROUTINES.latest("x") is None


def test_router_requests_unknown_routine_and_accepts_upload():
    import socket
    import threading

    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX unavailable")
    from agent_runtime.scheduler import control_transport as ct
    from agent_runtime.scheduler.uds import recv_frame, send_frame

    routine = Routine.learn([{"st": 0, "s": 0, "t": 1, "a": 3, "c": 0}])
    ct._ROUTER_ROUTINES.pop(routine.digest, None)

    def serve(hello: bytes) -> bytes:
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        t = threading.Thread(target=ct._serve_connection, args=(b,), daemon=True)
        t.start()
        send_frame(a, hello)
        reply = recv_frame(a)
        a.close()
        t.join(2)
        return reply

    base = "structured\0session_msgpack\x001\0cphc\0" + routine.digest
    assert serve(base.encode()) == ct._NEED_ROUTINE
    assert serve((base + "\0" + routine.to_json()).encode()) == ct._HELLO_OK
    assert serve(base.encode()) == ct._HELLO_OK  # now cached by digest
    tampered = base.replace(routine.digest, "0" * 24) + "\0" + routine.to_json()
    assert serve(tampered.encode()).startswith(bytes([ct._ACK_ERR]))


@pytest.mark.skipif(not hasattr(__import__('socket'), 'AF_UNIX'), reason='AF_UNIX unavailable')
def test_many_pipelined_frames_reassemble_over_one_buffer():
    import socket

    from agent_runtime.scheduler.uds import buffered_bytes, recv_frame, send_frame

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    import threading as _th

    frames = [bytes([i % 251]) * (i * 37 % 5000) for i in range(200)]
    sender = _th.Thread(target=lambda: [send_frame(a, f) for f in frames], daemon=True)
    sender.start()  # a single thread would deadlock once the socket buffer fills
    got = [recv_frame(b) for _ in frames]
    sender.join(5)
    assert got == frames and buffered_bytes(b) == 0
    big = b"x" * (3 * 1024 * 1024)
    import threading

    t = threading.Thread(target=send_frame, args=(a, big))
    t.start()
    assert recv_frame(b) == big
    t.join()
    a.close(); b.close()
