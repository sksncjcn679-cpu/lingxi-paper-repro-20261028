from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_runtime.runtime.event_log import EventJournal


@pytest.mark.skipif(os.name != "posix", reason="cross-process flock is a POSIX/OpenEuler path")
def test_event_journal_independent_process_writers_keep_one_valid_chain(tmp_path):
    path = tmp_path / "events.jsonl"
    script = r'''
import sys
from agent_runtime.runtime.event_log import EventJournal
p, worker = sys.argv[1], sys.argv[2]
journal = EventJournal(p, durable=False)
for i in range(12):
    journal.append("worker.event", trace_id=worker, task_id="shared", payload={"i": i})
'''
    env = os.environ.copy()
    # The parent test already runs against the current source tree; propagate that exact path
    # to fresh interpreters rather than relying on any installed package.
    env["PYTHONPATH"] = os.environ.get("PYTHONPATH", "src")
    workers = [
        subprocess.Popen([sys.executable, "-c", script, str(path), f"w{i}"], env=env)
        for i in range(3)
    ]
    assert [proc.wait(timeout=30) for proc in workers] == [0, 0, 0]

    events = EventJournal(path, durable=False).verify()
    assert len(events) == 36
    assert [event.sequence for event in events] == list(range(1, 37))
    for previous, current in zip(events, events[1:]):
        assert current.previous_hash == previous.event_hash
