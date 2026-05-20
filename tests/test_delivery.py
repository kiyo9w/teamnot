"""Tests for the git branch + handover delivery."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from teamnot.brief import (
    Brief,
    DefinitionOfDone,
    Deliverable,
    DeliverableType,
    DoDCheck,
    ProjectSpec,
    ReportTarget,
    TaskSpec,
)
from teamnot.delivery.git_branch import (
    create_feature_branch,
    detect_repo,
    diff_summary,
)
from teamnot.delivery.handover import handover

GIT = shutil.which("git")
skip_if_no_git = pytest.mark.skipif(GIT is None, reason="git not installed")


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A clean git repo with one initial commit on `main`."""
    if not GIT:
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    return repo


@skip_if_no_git
def test_detect_repo_returns_clean_state(git_repo: Path):
    state = detect_repo(git_repo)
    assert state.is_repo
    assert state.current_branch == "main"
    assert state.head_sha
    assert not state.has_uncommitted


@skip_if_no_git
def test_detect_repo_on_non_repo_dir(tmp_path: Path):
    state = detect_repo(tmp_path)
    assert not state.is_repo


@skip_if_no_git
def test_create_feature_branch_with_no_changes_creates_branch_no_commit(git_repo: Path):
    result = create_feature_branch(git_repo, branch="feature/x", base="main",
                                   commit_message="msg", stage_all=False)
    assert result["ok"], result
    assert not result["committed"], "no changes -> no commit"
    state = detect_repo(git_repo)
    assert state.current_branch == "feature/x"


@skip_if_no_git
def test_create_feature_branch_commits_when_changes_present(git_repo: Path):
    (git_repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    result = create_feature_branch(git_repo, branch="feature/y", base="main",
                                   commit_message="add new.py")
    assert result["ok"]
    assert result["committed"]
    # Diff against main should show new.py
    diff = diff_summary(git_repo, base="main")
    assert "new.py" in diff["files"]


@skip_if_no_git
def test_create_feature_branch_falls_back_to_head_when_base_missing(git_repo: Path):
    subprocess.run(["git", "branch", "-m", "main", "work"], cwd=git_repo, check=True)
    (git_repo / "new.py").write_text("x = 1\n", encoding="utf-8")

    result = create_feature_branch(
        git_repo,
        branch="feature/no-main",
        base="main",
        commit_message="add new.py",
    )

    assert result["ok"], result
    assert result["committed"]
    assert result["base_missing"] is True
    assert result["start_point"] == "HEAD"
    assert any("base branch 'main' not found locally" in note for note in result["notes"])
    assert detect_repo(git_repo).current_branch == "feature/no-main"

    diff = diff_summary(git_repo, base="main")
    assert diff["base_missing"] is True
    assert "limited to HEAD commit" in diff["note"]


@skip_if_no_git
def test_create_feature_branch_refuses_main(git_repo: Path):
    result = create_feature_branch(git_repo, branch="main", base="main")
    assert not result["ok"]
    assert "protected" in result["error"]


@skip_if_no_git
def test_handover_feature_branch_end_to_end(git_repo: Path, tmp_path: Path):
    (git_repo / "feature.py").write_text("def f(): pass\n", encoding="utf-8")
    brief = Brief(
        project=ProjectSpec(name="t", path=git_repo),
        task=TaskSpec(id="TASK-Z1", title="Add f", description="add feature.py with f()"),
        definition_of_done=DefinitionOfDone(checks=[DoDCheck(file_exists="feature.py")]),
        deliverable=Deliverable(
            type=DeliverableType.feature_branch,
            base="main",
            push_remote=False,
            report_to=ReportTarget.file,
            report_path=str(tmp_path / "report.md"),
        ),
    )
    report_path = tmp_path / "report-source.md"
    report_path.write_text("# report\n", encoding="utf-8")
    h = handover(brief, success=True, report_body="# report\n", report_path=report_path)
    assert h.ok
    assert h.deliverable_type == "feature_branch"
    state = detect_repo(git_repo)
    assert state.current_branch == "feature/task-z1"
    # Report routed to file
    assert (tmp_path / "report.md").exists()


def test_handover_files_works_without_git(tmp_path: Path):
    # No git init — but the diff lookup should still degrade gracefully
    (tmp_path / "src.py").write_text("x = 1", encoding="utf-8")
    brief = Brief(
        project=ProjectSpec(name="t", path=tmp_path),
        task=TaskSpec(id="T1", title="t", description="d"),
        definition_of_done=DefinitionOfDone(checks=[DoDCheck(file_exists="src.py")]),
        deliverable=Deliverable(type=DeliverableType.files, report_to=ReportTarget.stdout),
    )
    h = handover(brief, success=True, report_body="ok", report_path=tmp_path / "r.md")
    assert h.deliverable_type == "files"
    # Not ok because no git diff — but no crash
    assert "diff" in h.artifacts


def test_handover_report_only(tmp_path: Path):
    brief = Brief(
        project=ProjectSpec(name="t", path=tmp_path),
        task=TaskSpec(id="T1", title="t", description="d"),
        definition_of_done=DefinitionOfDone(checks=[DoDCheck(file_exists="anything")]),
        deliverable=Deliverable(type=DeliverableType.report_only,
                                report_to=ReportTarget.file,
                                report_path=str(tmp_path / "out.md")),
    )
    h = handover(brief, success=True, report_body="# body\n",
                 report_path=tmp_path / "src.md")
    assert h.ok
    assert h.deliverable_type == "report_only"
    assert (tmp_path / "out.md").read_text(encoding="utf-8") == "# body\n"
