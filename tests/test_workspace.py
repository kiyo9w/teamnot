"""Tests for the per-project Workspace."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from teamnot.brief import (
    Brief,
    DefinitionOfDone,
    DoDCheck,
    ProjectSpec,
    TaskSpec,
)
from teamnot.workspace import Workspace, WorkspaceLockError


def _brief(tmp: Path) -> Brief:
    return Brief(
        project=ProjectSpec(name="proj", path=tmp),
        task=TaskSpec(id="TASK-A", title="t", description="d"),
        definition_of_done=DefinitionOfDone(checks=[DoDCheck(run="true")]),
    )


def test_ensure_creates_layout(tmp_path: Path):
    ws = Workspace(_brief(tmp_path))
    ws.ensure()
    for sub in ("plans", "reports", "logs", "checkpoints", "qa_reports"):
        assert (tmp_path / ".teamnot" / sub).is_dir()
    assert ws.memory_path.exists()
    assert ws.conventions_path.exists()


def test_memory_writes_append_only(tmp_path: Path):
    ws = Workspace(_brief(tmp_path))
    ws.append_memory("learning-FIRST", "use field_validator instead of validator")
    ws.append_memory("learning-SECOND", "force reconfigure stdout on Windows")
    body = ws.memory_path.read_text(encoding="utf-8")
    assert "learning-FIRST" in body
    assert "learning-SECOND" in body
    # Order preserved (sections appended in call order)
    assert body.index("learning-FIRST") < body.index("learning-SECOND")


def test_checkpoint_save_and_load(tmp_path: Path):
    ws = Workspace(_brief(tmp_path))
    path = ws.save_checkpoint("TASK-A", "PLAN", "DONE", {"plan_file": "p.md"})
    assert path.exists()
    rec = ws.load_checkpoint("TASK-A", "PLAN")
    assert rec is not None
    assert rec.task_id == "TASK-A"
    assert rec.phase == "PLAN"
    assert rec.status == "DONE"
    assert rec.payload == {"plan_file": "p.md"}


def test_latest_checkpoint_returns_last_alphabetically(tmp_path: Path):
    ws = Workspace(_brief(tmp_path))
    ws.save_checkpoint("TASK-A", "01-plan", "DONE", {})
    ws.save_checkpoint("TASK-A", "02-implement", "DONE", {})
    ws.save_checkpoint("TASK-A", "03-test", "DONE", {})
    latest = ws.latest_checkpoint("TASK-A")
    assert latest is not None
    assert latest.phase == "03-test"


def test_lock_excludes_concurrent_workers(tmp_path: Path):
    ws = Workspace(_brief(tmp_path))
    ws.ensure()
    with ws.lock(owner="worker-1"):
        with pytest.raises(WorkspaceLockError, match="locked by another worker"):
            with ws.lock(owner="worker-2"):
                pytest.fail("should not have acquired the lock")


def test_lock_releases_on_exit(tmp_path: Path):
    ws = Workspace(_brief(tmp_path))
    with ws.lock(owner="w1"):
        pass
    # Lock file gone, can re-acquire
    with ws.lock(owner="w2"):
        pass


def test_lock_waits_when_wait_s_set(tmp_path: Path):
    ws = Workspace(_brief(tmp_path))
    ws.ensure()
    ok: list[bool] = []

    def hold():
        with ws.lock(owner="holder"):
            import time
            time.sleep(0.3)
        ok.append(True)

    t = threading.Thread(target=hold)
    t.start()
    # Race: thread starts ~immediately, second lock waits up to 5s
    import time
    time.sleep(0.05)
    with ws.lock(owner="waiter", wait_s=5.0):
        ok.append(True)
    t.join(timeout=5)
    assert len(ok) == 2


def test_snapshot_returns_jsonable_dict(tmp_path: Path):
    ws = Workspace(_brief(tmp_path))
    ws.ensure()
    ws.append_memory("note", "x")
    snap = ws.snapshot()
    assert snap["project"] == "proj"
    assert snap["tn_dir_exists"] is True
    assert snap["memory_exists"] is True
    assert snap["memory_chars"] > 0
    assert snap["locked"] is False
