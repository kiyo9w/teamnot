"""Tests for the Brief Contract schema + loader."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from teamnot.brief import (
    Brief,
    BriefValidationError,
    DefinitionOfDone,
    DoDCheck,
    ProjectSpec,
    TaskSpec,
    example_brief,
    load_brief,
    save_brief,
)


def test_example_brief_round_trips(tmp_path: Path):
    """example_brief produces a valid Brief that can be saved and reloaded."""
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    brief = example_brief(project_dir)
    out = save_brief(brief, project_dir / ".teamnot" / "brief.yaml")
    reloaded = load_brief(out)
    assert reloaded.project.name == project_dir.name
    assert reloaded.project.path == project_dir.resolve()
    assert reloaded.task.title.startswith("Example")
    assert len(reloaded.definition_of_done.checks) == 4


def test_load_brief_rejects_missing_file(tmp_path: Path):
    with pytest.raises(BriefValidationError, match="not found"):
        load_brief(tmp_path / "nope.yaml")


def test_load_brief_rejects_missing_project_path(tmp_path: Path):
    brief_path = tmp_path / "brief.yaml"
    brief_path.write_text(textwrap.dedent("""
        project:
          name: ghost
          path: /this/path/does/not/exist/ever
        task:
          id: T1
          title: noop
          description: noop
        definition_of_done:
          checks:
            - run: "true"
    """), encoding="utf-8")
    with pytest.raises(BriefValidationError, match="does not exist"):
        load_brief(brief_path)


def test_dod_check_requires_exactly_one_target():
    with pytest.raises(ValueError, match="exactly one"):
        DoDCheck()  # no targets

    with pytest.raises(ValueError, match="exactly one"):
        DoDCheck(run="echo hi", file_exists="x.txt")  # two targets


def test_dod_check_auto_detects_kind():
    c = DoDCheck(run="pytest")
    assert c.kind == "run"
    assert c.name.startswith("run:")

    c = DoDCheck(file_exists="README.md", name="readme exists")
    assert c.kind == "file_exists"
    assert c.name == "readme exists"


def test_brief_defaults_branch_from_task_id(tmp_path: Path):
    project_dir = tmp_path / "p"
    project_dir.mkdir()
    brief = Brief(
        project=ProjectSpec(name="p", path=project_dir),
        task=TaskSpec(id="TASK-XYZ", title="t", description="d"),
        definition_of_done=DefinitionOfDone(checks=[DoDCheck(run="true")]),
    )
    assert brief.deliverable.branch == "feature/task-xyz"


def test_budget_threshold_ordering_enforced(tmp_path: Path):
    from pydantic import ValidationError

    from teamnot.brief import Budget
    with pytest.raises(ValidationError, match="cost thresholds"):
        Budget(cost_warn_pct=0.9, cost_pause_pct=0.5)


def test_brief_paths_resolve_inside_project(tmp_path: Path):
    project_dir = tmp_path / "p"
    project_dir.mkdir()
    brief = Brief(
        project=ProjectSpec(name="p", path=project_dir),
        task=TaskSpec(id="T1", title="t", description="d"),
        definition_of_done=DefinitionOfDone(checks=[DoDCheck(run="true")]),
    )
    assert brief.memory_path == (project_dir / ".teamnot/memory.md").resolve()
    assert brief.conventions_path == (project_dir / ".teamnot/conventions.md").resolve()
    assert brief.reports_dir == (project_dir / ".teamnot/reports").resolve()
