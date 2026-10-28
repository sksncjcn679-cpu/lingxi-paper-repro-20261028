from agent_runtime.protocol.routine import PredictorContext, Routine, compress


def test_learned_and_negotiated_routines_encode_absent_fields_identically():
    local = Routine({"^": {"sta": 1, "cf": 0.9, "st": "s0", "t": 2}})
    negotiated = Routine.from_json(local.to_json())
    frame = {"v": "1.3", "sq": 1, "st": "s0"}
    left = compress(dict(frame), PredictorContext(routine=local))
    right = compress(dict(frame), PredictorContext(routine=negotiated))
    assert left == right
    assert left["nx"] == sorted(left["nx"])
