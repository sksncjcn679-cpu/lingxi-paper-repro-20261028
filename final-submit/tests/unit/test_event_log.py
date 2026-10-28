from __future__ import annotations

import json

import pytest

from agent_runtime.runtime.event_log import EventJournal, EventJournalCorruptionError


def test_event_journal_hash_chain_and_replay(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl", durable=False)
    journal.append("task.started", trace_id="tr1", task_id="t1")
    journal.append("stage.started", trace_id="tr1", task_id="t1", stage="planner")
    journal.append(
        "message.sent",
        trace_id="tr1",
        task_id="t1",
        stage="planner",
        payload={"message_id": "m1"},
    )
    journal.append("stage.completed", trace_id="tr1", task_id="t1", stage="planner")
    journal.append("task.completed", trace_id="tr1", task_id="t1")

    events = journal.verify()
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert events[0].previous_hash == "0" * 64
    assert events[1].previous_hash == events[0].event_hash

    replay = journal.replay("t1")
    assert replay.started is True
    assert replay.stages_started == ("planner",)
    assert replay.stages_completed == ("planner",)
    assert replay.message_ids == ("m1",)
    assert replay.terminal_event == "task.completed"


def test_event_journal_detects_tampering(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path, durable=False)
    journal.append("task.started", trace_id="tr1", task_id="t1", payload={"x": 1})
    doc = json.loads(path.read_text(encoding="utf-8").strip())
    doc["payload"]["x"] = 999
    path.write_text(json.dumps(doc) + "\n", encoding="utf-8")

    with pytest.raises(EventJournalCorruptionError, match="event hash mismatch"):
        EventJournal(path, durable=False)


def test_event_journal_reopens_and_continues_chain(tmp_path):
    path = tmp_path / "events.jsonl"
    first = EventJournal(path, durable=False)
    e1 = first.append("task.started", trace_id="tr1", task_id="t1")
    second = EventJournal(path, durable=False)
    e2 = second.append("task.completed", trace_id="tr1", task_id="t1")
    assert e2.sequence == 2
    assert e2.previous_hash == e1.event_hash
    assert len(second.verify()) == 2


def test_journal_durability_modes_and_explicit_flush(tmp_path):
    for mode in ("sync", "task", "buffered"):
        journal = EventJournal(tmp_path / f"{mode}.jsonl", durability=mode)
        journal.append("task.started", trace_id="tr", task_id="tk")
        journal.append("task.completed", trace_id="tr", task_id="tk")
        journal.flush()
        assert len(journal.verify()) == 2
        assert journal.durability == mode


def test_journal_rejects_unknown_durability_mode(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="durability"):
        EventJournal(tmp_path / "x.jsonl", durability="magic")


def test_event_journal_two_stale_instances_refresh_before_append(tmp_path):
    path = tmp_path / "events.jsonl"
    first = EventJournal(path, durable=False)
    second = EventJournal(path, durable=False)

    e1 = first.append("task.started", trace_id="tr1", task_id="t1")
    e2 = second.append("stage.started", trace_id="tr1", task_id="t1", stage="planner")
    e3 = first.append("task.completed", trace_id="tr1", task_id="t1")

    assert [e1.sequence, e2.sequence, e3.sequence] == [1, 2, 3]
    verified = EventJournal(path, durable=False).verify()
    assert [event.sequence for event in verified] == [1, 2, 3]
    assert verified[1].previous_hash == verified[0].event_hash
    assert verified[2].previous_hash == verified[1].event_hash


def test_replay_events_matches_file_api_for_repeated_task_traces(tmp_path):
    from agent_runtime.runtime.event_log import replay_events

    path = tmp_path / "events.jsonl"
    journal = EventJournal(path, durable=False)
    journal.append("task.started", trace_id="old", task_id="same")
    journal.append("task.failed", trace_id="old", task_id="same")
    journal.append("task.started", trace_id="new", task_id="same")
    journal.append("task.completed", trace_id="new", task_id="same")

    events = journal.verify()
    assert replay_events(events, "same") == journal.replay("same")
    assert replay_events(events, "same", trace_id="old").terminal_event == "task.failed"


def test_event_journal_rejects_non_finite_json_values_and_empty_ids(tmp_path):
    import math

    journal = EventJournal(tmp_path / "events.jsonl", durable=False)
    with pytest.raises(ValueError, match="timestamp"):
        journal.append("task.started", trace_id="tr", task_id="t", timestamp=math.nan)
    with pytest.raises(ValueError, match="non-finite"):
        journal.append(
            "task.started", trace_id="tr", task_id="t", payload={"bad": math.inf}
        )
    with pytest.raises(ValueError, match="non-empty"):
        journal.append("task.started", trace_id="", task_id="t")


def test_journal_path_for_never_interprets_task_id_as_filesystem_path(tmp_path):
    from agent_runtime.runtime.event_log import journal_path_for

    root = tmp_path / "events"
    path = journal_path_for(root, "../../escape/../very-secret")
    assert path.parent == root
    assert path.suffix == ".jsonl"
    assert ".." not in path.name
    assert "/" not in path.name


def test_event_journal_rejects_symlink_path(tmp_path):
    import os
    from agent_runtime.runtime.event_log import EventJournal

    target = tmp_path / "real.jsonl"
    target.write_text("", encoding="utf-8")
    link = tmp_path / "journal.jsonl"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="symlink"):
        EventJournal(link)


def test_event_journal_detects_path_replaced_by_symlink_after_open(tmp_path):
    from agent_runtime.runtime.event_log import EventJournal

    path = tmp_path / "journal.jsonl"
    journal = EventJournal(path)
    journal.append("task.started", trace_id="tr", task_id="task")
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(replacement)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="symlink"):
        journal.verify()
