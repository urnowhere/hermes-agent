from __future__ import annotations

from plugins.formsy.constraint_keeper.spool import EvidenceSpool


def test_spool_preserves_fifo_pending_events_per_run(tmp_path):
    spool = EvidenceSpool(tmp_path)

    spool.append(task_id="task/one", run_id="run:one", event={"sequence": 2, "event_id": "e2"})
    spool.append(task_id="task/one", run_id="run:one", event={"sequence": 1, "event_id": "e1"})

    assert [event["event_id"] for event in spool.pending("task/one", "run:one")] == ["e1", "e2"]


def test_spool_ack_removes_event_from_pending(tmp_path):
    spool = EvidenceSpool(tmp_path)
    spool.append(task_id="task", run_id="run", event={"sequence": 1, "event_id": "e1"})
    spool.append(task_id="task", run_id="run", event={"sequence": 2, "event_id": "e2"})

    spool.mark_acked(task_id="task", run_id="run", event_id="e1")

    assert [event["event_id"] for event in spool.pending("task", "run")] == ["e2"]
