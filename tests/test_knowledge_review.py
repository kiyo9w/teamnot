"""Tests for the knowledge-gap review."""
from __future__ import annotations

from pathlib import Path

from teamnot.brief import (
    Brief,
    DefinitionOfDone,
    DoDCheck,
    ProjectSpec,
    TaskSpec,
)
from teamnot.memory.knowledge_review import review_workspace
from teamnot.workspace import Workspace


def _minimal_brief(tmp: Path, **overrides) -> Brief:
    project_kwargs = dict(name="p", path=tmp)
    project_kwargs.update(overrides.pop("project", {}))
    task_kwargs = dict(id="T1", title="t", description="add a /health endpoint")
    task_kwargs.update(overrides.pop("task", {}))
    dod_kwargs = dict(checks=[DoDCheck(run="true")])
    dod_kwargs.update(overrides.pop("definition_of_done", {}))
    return Brief(
        project=ProjectSpec(**project_kwargs),
        task=TaskSpec(**task_kwargs),
        definition_of_done=DefinitionOfDone(**dod_kwargs),
        **overrides,
    )


def test_no_blockers_on_minimal_simple_brief(tmp_path: Path):
    """Short task description + scaffold conventions = warnings, not blockers."""
    brief = _minimal_brief(tmp_path)
    Workspace(brief).ensure()
    review = review_workspace(brief)
    assert review.can_proceed
    # We expect at least a warning for empty stack/conventions
    codes = {g.code for g in review.gaps}
    assert "NO_STACK" in codes
    assert "EMPTY_CONVENTIONS" in codes


def test_long_task_with_empty_conventions_is_blocker(tmp_path: Path):
    """Detailed task description without conventions is dangerous — block."""
    brief = _minimal_brief(
        tmp_path,
        task={
            "id": "T1",
            "title": "Big refactor",
            "description": (
                "Refactor the authentication subsystem to use JWT bearer tokens instead "
                "of stateful sessions, introduce role-based access control across every "
                "admin endpoint, migrate existing session-based browser clients to the "
                "new flow without breaking active users, document the migration in a new "
                "ADR, and add comprehensive integration tests covering token issuance, "
                "refresh, expiry, revocation, and replay attempts."
            ),
        },
    )
    Workspace(brief).ensure()
    review = review_workspace(brief)
    codes_blocker = {g.code for g in review.blockers}
    assert "EMPTY_CONVENTIONS" in codes_blocker
    assert not review.can_proceed


def test_only_judge_no_machine_check_is_blocker(tmp_path: Path):
    """A DoD with only an llm_judge has no objective halt — must block."""
    brief = _minimal_brief(
        tmp_path,
        definition_of_done={
            "checks": [DoDCheck(llm_judge="approve please")],
            "llm_judge_required": True,
        },
    )
    Workspace(brief).ensure()
    review = review_workspace(brief)
    codes_blocker = {g.code for g in review.blockers}
    assert "NO_MACHINE_DOD" in codes_blocker
    assert not review.can_proceed


def test_judge_required_but_no_judge_check_is_blocker(tmp_path: Path):
    brief = _minimal_brief(
        tmp_path,
        definition_of_done={
            "checks": [DoDCheck(run="echo ok")],
            "llm_judge_required": True,
        },
    )
    Workspace(brief).ensure()
    review = review_workspace(brief)
    codes_blocker = {g.code for g in review.blockers}
    assert "JUDGE_REQUIRED_BUT_MISSING" in codes_blocker


def test_thin_description_is_warning(tmp_path: Path):
    brief = _minimal_brief(
        tmp_path,
        task={"id": "T1", "title": "x", "description": "do it"},
    )
    Workspace(brief).ensure()
    review = review_workspace(brief)
    codes = {g.code for g in review.warnings}
    assert "THIN_TASK_DESCRIPTION" in codes


def test_missing_references_is_warning(tmp_path: Path):
    brief = _minimal_brief(
        tmp_path,
        task={
            "id": "T1",
            "title": "Read this spec",
            "description": "Follow the specification document attached.",
            "references": ["docs/spec.md", "https://example.com/spec.html"],
        },
    )
    Workspace(brief).ensure()
    review = review_workspace(brief)
    codes = {g.code for g in review.warnings}
    assert "MISSING_REFERENCES" in codes
    # URL ref doesn't count as missing
    detail = next(g.detail for g in review.warnings if g.code == "MISSING_REFERENCES")
    assert "docs/spec.md" in detail
    assert "example.com" not in detail


def test_filled_brief_has_no_warnings_for_stack_or_conventions(tmp_path: Path):
    brief = _minimal_brief(
        tmp_path,
        project={
            "name": "p",
            "path": tmp_path,
            "language": ["python"],
            "stack": ["fastapi", "postgres"],
        },
        task={
            "id": "T1",
            "title": "add /health endpoint",
            "description": (
                "Add a GET /health endpoint that returns {'status': 'ok'}. "
                "Include a pytest test that verifies the 200 status and body shape. "
                "Wire the endpoint into the existing FastAPI app at src/main.py."
            ),
        },
    )
    ws = Workspace(brief)
    ws.ensure()
    # Make conventions look real
    ws.conventions_path.write_text(
        "# conventions\n\n"
        "- FastAPI routers go under src/api/routes.\n"
        "- Tests live under tests/api and use pytest fixtures from tests/conftest.py.\n"
        "- All endpoints return JSON, never plain text.\n"
        "- Type hints required on every function signature.\n"
        "- Use async def for all I/O endpoints.\n"
        "- Use Pydantic v2 BaseModel for request and response shapes.\n",
        encoding="utf-8",
    )
    review = review_workspace(brief, ws)
    codes = {g.code for g in review.gaps}
    assert "NO_STACK" not in codes
    assert "NO_LANGUAGE" not in codes
    assert "EMPTY_CONVENTIONS" not in codes
    assert review.can_proceed


def test_empty_project_is_info_not_blocker(tmp_path: Path):
    brief = _minimal_brief(tmp_path)
    Workspace(brief).ensure()
    review = review_workspace(brief)
    codes_info = {g.code for g in review.infos}
    # tmp_path has only .teamnot subdir at this point, which we exclude
    assert "EMPTY_PROJECT" in codes_info
