"""Workspace — per-project state isolation.

The cardinal rule from CLAUDE.md is "tri thức dự án phải nằm trong project đích,
TeamNoT chỉ là worker." A Workspace materializes that rule: every read/write of
project context goes through it, scoped to the target project's `.teamnot/`
directory. TeamNoT itself never accumulates context across projects.

A Workspace gives you:

  * Stable paths into `.teamnot/{memory,plans,reports,logs,checkpoints}/...`
  * Append-only project memory writer (memory.md)
  * Checkpoint reader/writer for resumability
  * Conventions + memory readers that other modules consume as plain strings
  * A lock file so two TeamNoT workers don't trample one project at once

Everything in here is filesystem-backed and side-effecting.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from teamnot.brief import Brief

logger = logging.getLogger("teamnot.workspace")


class WorkspaceLockError(RuntimeError):
    """Raised when another TeamNoT worker is already holding the workspace lock."""


@dataclass
class CheckpointRecord:
    task_id: str
    phase: str
    status: str
    payload: dict
    saved_at: str


class Workspace:
    """A handle on `.teamnot/` inside a target project.

    Construct one per ``Brief``; never share across briefs.
    """

    def __init__(self, brief: Brief):
        self.brief = brief
        self.root: Path = brief.project.path
        self.tn_dir: Path = self.root / ".teamnot"
        self._lock_handle: Path | None = None

    # ── Layout (idempotent) ───────────────────────────────────────────────

    def ensure(self) -> None:
        """Create `.teamnot/` and its standard sub-dirs if missing."""
        for sub in ("plans", "reports", "logs", "checkpoints", "qa_reports"):
            (self.tn_dir / sub).mkdir(parents=True, exist_ok=True)
        if not self.memory_path.exists():
            self.memory_path.write_text(
                f"# Project memory ({self.brief.project.name})\n\n"
                f"> Managed by TeamNoT. Patterns, gotchas, decisions accumulated across tasks.\n",
                encoding="utf-8",
            )
        if not self.conventions_path.exists():
            self.conventions_path.write_text(
                f"# Project conventions ({self.brief.project.name})\n\n"
                f"> Fill in: code style, naming, framework patterns, test layout, "
                f"branching, security rules. Agents read this before coding.\n",
                encoding="utf-8",
            )

    # ── Paths ─────────────────────────────────────────────────────────────

    @property
    def memory_path(self) -> Path:
        return self.brief.memory_path

    @property
    def conventions_path(self) -> Path:
        return self.brief.conventions_path

    @property
    def plans_dir(self) -> Path:
        return self.tn_dir / "plans"

    @property
    def reports_dir(self) -> Path:
        return self.tn_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.tn_dir / "logs"

    @property
    def checkpoints_dir(self) -> Path:
        return self.tn_dir / "checkpoints"

    @property
    def qa_reports_dir(self) -> Path:
        return self.tn_dir / "qa_reports"

    @property
    def lock_path(self) -> Path:
        return self.tn_dir / "worker.lock"

    # ── Readers ───────────────────────────────────────────────────────────

    def read_memory(self, max_chars: int = 8000) -> str:
        if not self.memory_path.exists():
            return ""
        text = self.memory_path.read_text(encoding="utf-8")
        if len(text) > max_chars:
            return text[:max_chars] + "\n... (truncated)"
        return text

    def read_conventions(self, max_chars: int = 8000) -> str:
        if not self.conventions_path.exists():
            return ""
        text = self.conventions_path.read_text(encoding="utf-8")
        if len(text) > max_chars:
            return text[:max_chars] + "\n... (truncated)"
        return text

    def read_reference(self, relative_or_url: str, max_chars: int = 4000) -> str:
        """Read a reference file from the project (URLs are returned as a placeholder)."""
        if relative_or_url.startswith(("http://", "https://")):
            return f"[reference URL — fetch separately: {relative_or_url}]"
        p = self.brief.absolute(relative_or_url)
        if not p.exists() or not p.is_file():
            return f"[reference not found: {relative_or_url}]"
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + "\n... (truncated)"
        return text

    # ── Memory writes ────────────────────────────────────────────────────

    def append_memory(self, section: str, body: str, source: str = "TeamNoT") -> None:
        """Append a dated section to the project's `memory.md`.

        Always appends — never overwrites — so the project keeps an audit
        trail of accumulated learning.
        """
        self.ensure()
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n\n## {stamp} — {section}  _(by {source})_\n\n{body.rstrip()}\n"
        with self.memory_path.open("a", encoding="utf-8") as f:
            f.write(entry)

    # ── Checkpoints (resumability) ───────────────────────────────────────

    def save_checkpoint(self, task_id: str, phase: str, status: str, payload: dict) -> Path:
        """Snapshot a pipeline phase so the loop can resume after a crash/reboot."""
        self.ensure()
        rec = CheckpointRecord(
            task_id=task_id,
            phase=phase,
            status=status,
            payload=payload,
            saved_at=datetime.now().isoformat(timespec="seconds"),
        )
        safe_phase = phase.lower().replace(" ", "-")
        path = self.checkpoints_dir / f"{task_id}__{safe_phase}.json"
        path.write_text(
            json.dumps(
                {
                    "task_id": rec.task_id,
                    "phase": rec.phase,
                    "status": rec.status,
                    "payload": rec.payload,
                    "saved_at": rec.saved_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def load_checkpoint(self, task_id: str, phase: str) -> CheckpointRecord | None:
        safe_phase = phase.lower().replace(" ", "-")
        path = self.checkpoints_dir / f"{task_id}__{safe_phase}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return CheckpointRecord(**data)

    def latest_checkpoint(self, task_id: str) -> CheckpointRecord | None:
        files = sorted(self.checkpoints_dir.glob(f"{task_id}__*.json"))
        if not files:
            return None
        data = json.loads(files[-1].read_text(encoding="utf-8"))
        return CheckpointRecord(**data)

    # ── Reports ──────────────────────────────────────────────────────────

    def write_report(self, task_id: str, body: str) -> Path:
        self.ensure()
        path = self.reports_dir / f"{task_id}.md"
        path.write_text(body, encoding="utf-8")
        return path

    def write_plan(self, task_id: str, body: str) -> Path:
        self.ensure()
        path = self.plans_dir / f"{task_id}.md"
        path.write_text(body, encoding="utf-8")
        return path

    def write_qa_report(self, task_id: str, body: str) -> Path:
        self.ensure()
        path = self.qa_reports_dir / f"{task_id}.md"
        path.write_text(body, encoding="utf-8")
        return path

    # ── Mutex ────────────────────────────────────────────────────────────

    @contextmanager
    def lock(self, owner: str = "teamnot", wait_s: float = 0.0) -> Iterator[Path]:
        """Acquire a coarse mutex over this workspace.

        Two workers running against the same project will corrupt each other's
        branches, memory, and checkpoints. The lock prevents that.

        If another lock exists and ``wait_s`` is zero, raises ``WorkspaceLockError``.
        """
        self.ensure()
        deadline = time.monotonic() + wait_s
        while True:
            try:
                # O_EXCL is atomic — only one process can create the file
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(
                        fd,
                        json.dumps(
                            {"owner": owner, "pid": os.getpid(),
                             "started_at": datetime.now().isoformat(timespec="seconds")}
                        ).encode("utf-8"),
                    )
                finally:
                    os.close(fd)
                break
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    existing = self.lock_path.read_text(encoding="utf-8", errors="replace")
                    raise WorkspaceLockError(
                        f"Workspace {self.tn_dir} is locked by another worker:\n  {existing}\n"
                        f"Delete {self.lock_path} if you are sure no worker is running."
                    ) from exc
                time.sleep(0.5)

        try:
            yield self.lock_path
        finally:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    # ── Diagnostics ──────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Cheap, JSON-able view of the workspace state."""
        return {
            "project": self.brief.project.name,
            "path": str(self.root),
            "tn_dir_exists": self.tn_dir.exists(),
            "memory_exists": self.memory_path.exists(),
            "memory_chars": (
                self.memory_path.stat().st_size if self.memory_path.exists() else 0
            ),
            "conventions_exists": self.conventions_path.exists(),
            "conventions_chars": (
                self.conventions_path.stat().st_size if self.conventions_path.exists() else 0
            ),
            "plans_count": len(list(self.plans_dir.glob("*"))) if self.plans_dir.exists() else 0,
            "reports_count": len(list(self.reports_dir.glob("*"))) if self.reports_dir.exists() else 0,
            "checkpoints_count": (
                len(list(self.checkpoints_dir.glob("*"))) if self.checkpoints_dir.exists() else 0
            ),
            "locked": self.lock_path.exists(),
        }
