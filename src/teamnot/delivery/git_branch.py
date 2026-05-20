"""Git branch operations for delivery.

Designed to be safe-by-default: TeamNoT NEVER pushes, force-pushes, deletes
branches, or commits to main unless the brief explicitly opts in.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("teamnot.delivery.git")


class GitNotFoundError(RuntimeError):
    pass


# Backwards-compatible alias for code imported as `GitNotFound`.
GitNotFound = GitNotFoundError


@dataclass
class GitState:
    is_repo: bool
    current_branch: str = ""
    has_uncommitted: bool = False
    head_sha: str = ""
    notes: list[str] = field(default_factory=list)


def _run_git(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise GitNotFoundError("git executable not found in PATH") from e


def detect_repo(project_path: Path) -> GitState:
    """Inspect the project to see whether git operations are even possible."""
    try:
        rc = _run_git(["rev-parse", "--git-dir"], project_path)
    except GitNotFoundError:
        return GitState(is_repo=False, notes=["git not installed"])
    if rc.returncode != 0:
        return GitState(is_repo=False, notes=[(rc.stderr or rc.stdout).strip()])

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], project_path).stdout.strip()
    head = _run_git(["rev-parse", "HEAD"], project_path).stdout.strip()
    status = _run_git(["status", "--porcelain"], project_path).stdout
    return GitState(
        is_repo=True,
        current_branch=branch,
        head_sha=head,
        has_uncommitted=bool(status.strip()),
    )


def create_feature_branch(
    project_path: Path,
    branch: str,
    base: str = "main",
    *,
    commit_message: str | None = None,
    stage_all: bool = True,
) -> dict:
    """Create (or switch to) ``branch`` from ``base`` and optionally commit.

    Returns a JSON-able status dict. Refuses to operate on ``main``/``master``.
    """
    if branch.strip() in {"main", "master"}:
        return {"ok": False, "error": f"refusing to operate on protected branch '{branch}'"}

    state = detect_repo(project_path)
    if not state.is_repo:
        return {"ok": False, "error": "not a git repository", "state": state.notes}

    notes: list[str] = []
    if state.current_branch == branch:
        notes.append(f"already on branch {branch}")
    else:
        base_check = _run_git(["rev-parse", "--verify", base], project_path)
        start_point = base if base_check.returncode == 0 else "HEAD"
        if start_point == "HEAD":
            notes.append(f"base branch '{base}' not found locally; creating {branch} from current HEAD")

        switch = _run_git(["switch", "-c", branch, start_point], project_path)
        if switch.returncode != 0 and "already exists" in (switch.stderr or ""):
            switch = _run_git(["switch", branch], project_path)
        if switch.returncode != 0:
            return {"ok": False, "error": switch.stderr.strip() or "git switch failed"}

    if stage_all:
        add = _run_git(["add", "-A"], project_path)
        if add.returncode != 0:
            notes.append(f"git add failed: {add.stderr.strip()}")

    committed = False
    if commit_message:
        # Only commit if there's something staged
        diff = _run_git(["diff", "--cached", "--quiet"], project_path)
        if diff.returncode != 0:  # non-zero means there ARE staged changes
            commit = _run_git(["commit", "-m", commit_message], project_path)
            if commit.returncode == 0:
                committed = True
            else:
                notes.append(f"commit failed: {commit.stderr.strip()}")
        else:
            notes.append("no staged changes to commit")

    head = _run_git(["rev-parse", "HEAD"], project_path).stdout.strip()
    return {
        "ok": True,
        "branch": branch,
        "base": base,
        "committed": committed,
        "head": head,
        "notes": notes,
    }


def diff_summary(project_path: Path, base: str = "main") -> dict:
    """Summarize the diff between the current branch and ``base``."""
    state = detect_repo(project_path)
    if not state.is_repo:
        return {"ok": False, "files": [], "stats": {}, "note": "not a git repo"}

    if _run_git(["rev-parse", "--verify", base], project_path).returncode == 0:
        stat = _run_git(["diff", "--stat", f"{base}...HEAD"], project_path).stdout
        files = _run_git(["diff", "--name-only", f"{base}...HEAD"], project_path).stdout
    else:
        stat = _run_git(["show", "--stat", "--format=", "HEAD"], project_path).stdout
        files = _run_git(["show", "--name-only", "--format=", "HEAD"], project_path).stdout
    return {
        "ok": True,
        "files": [f for f in files.splitlines() if f.strip()],
        "stats_raw": stat.strip(),
    }


def push_branch(project_path: Path, branch: str, remote: str = "origin") -> dict:
    """Push the branch to a remote. Caller decides whether the brief allows this."""
    if branch.strip() in {"main", "master"}:
        return {"ok": False, "error": f"refusing to push to protected branch '{branch}'"}
    rc = _run_git(["push", "-u", remote, branch], project_path, timeout=180)
    return {
        "ok": rc.returncode == 0,
        "stdout": rc.stdout.strip(),
        "stderr": rc.stderr.strip(),
        "returncode": rc.returncode,
    }
