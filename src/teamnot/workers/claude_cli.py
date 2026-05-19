"""Claude Code CLI worker.

Wraps the local `claude` CLI binary (OAuth, no API key) as a subprocess. Counts
as a SUBSCRIPTION worker — the user pays a flat fee for claude.ai access so the
cost guard does not charge per call. The guard still records every call for
audit and still enforces the time budget.

This is a port of the legacy `claude_worker_legacy.py`, redone to consume a
Workspace (so context comes from the target project, not TeamNoT root) and the
CostGuard (so calls are audited and time-budgeted).
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from teamnot.brief import Brief
from teamnot.safety import CostGuard
from teamnot.workspace import Workspace

logger = logging.getLogger("teamnot.workers.claude_cli")

WORKER_NAME = "claude_cli"


class ClaudeCliNotFoundError(RuntimeError):
    pass


# Backwards-compatible alias.
ClaudeCliNotFound = ClaudeCliNotFoundError


def find_claude_cli() -> str:
    """Locate the `claude` executable on Windows / POSIX."""
    candidates = [
        "claude",
        str(Path.home() / ".local" / "bin" / "claude"),
        str(Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"),
        r"C:\Program Files\Claude\claude.exe",
    ]
    for c in candidates:
        try:
            r = subprocess.run(
                [c, "--version"],
                capture_output=True, text=True, timeout=10,
                shell=c.endswith(".cmd"),
            )
            if r.returncode == 0:
                return c
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    raise ClaudeCliNotFoundError(
        "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code\n"
        "Then run: claude login"
    )


@dataclass
class ClaudeCliResult:
    output: str
    returncode: int
    stderr: str
    elapsed_s: float


class ClaudeCliWorker:
    """A reusable handle on the Claude Code CLI bound to a single workspace."""

    def __init__(self, brief: Brief, workspace: Workspace, cost_guard: CostGuard):
        self.brief = brief
        self.ws = workspace
        self.guard = cost_guard
        self._cli_path: str | None = None

    # ── Bootstrapping ─────────────────────────────────────────────────────

    @property
    def cli_path(self) -> str:
        if self._cli_path is None:
            self._cli_path = find_claude_cli()
        return self._cli_path

    def is_available(self) -> bool:
        try:
            _ = self.cli_path
            return True
        except ClaudeCliNotFoundError:
            return False

    # ── Core call ─────────────────────────────────────────────────────────

    def run(
        self,
        prompt: str,
        *,
        context_files: list[str] | None = None,
        model: str = "sonnet",
        timeout: int = 300,
        allowed_tools: list[str] | None = None,
        note: str = "",
    ) -> ClaudeCliResult:
        """Run a single prompt through the Claude Code CLI.

        Context files are resolved relative to the target project (the
        Workspace's root), NOT to TeamNoT itself — this is the central change
        from the legacy worker.
        """
        # Cost guard gate — subscription, but still counts for the audit log
        # and time budget.
        with self.guard.gate(WORKER_NAME, estimated_usd=0.0, note=note or "claude_cli") as call:
            import time as _time
            start = _time.monotonic()

            full_prompt = self._inject_context(prompt, context_files or [])

            if allowed_tools is None:
                allowed_tools = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

            # Claude Code CLI uses OAuth via claude.ai. ANTHROPIC_API_KEY must
            # be stripped — otherwise the CLI switches to direct API mode and
            # bills per token outside the subscription. See feedback_claude_cli_auth.
            env = os.environ.copy()
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)

            cmd = [
                self.cli_path,
                "-p",
                "--model", model,
                "--no-session-persistence",
                "--permission-mode", "acceptEdits",
                "--output-format", "text",
                "--allowedTools", ",".join(allowed_tools),
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    input=full_prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=str(self.ws.root),
                    timeout=timeout,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                call.record_actual(usd=0.0, note=f"timeout after {timeout}s")
                return ClaudeCliResult(
                    output="",
                    returncode=-1,
                    stderr=f"timeout after {timeout}s",
                    elapsed_s=_time.monotonic() - start,
                )

            call.record_actual(usd=0.0)
            return ClaudeCliResult(
                output=(proc.stdout or "").strip(),
                returncode=proc.returncode,
                stderr=(proc.stderr or "").strip(),
                elapsed_s=_time.monotonic() - start,
            )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _inject_context(self, prompt: str, context_files: list[str]) -> str:
        """Prepend project context to the prompt."""
        parts: list[str] = []
        for relative in context_files:
            text = self.ws.read_reference(relative)
            parts.append(f"=== {Path(relative).name} ===\n{text}")
        # Always include the conventions + memory if they exist and are non-empty
        conv = self.ws.read_conventions(max_chars=4000)
        mem = self.ws.read_memory(max_chars=4000)
        if conv.strip():
            parts.append(f"=== conventions.md ===\n{conv}")
        if mem.strip():
            parts.append(f"=== memory.md ===\n{mem}")

        if not parts:
            return prompt
        return (
            "=== CONTEXT (read before acting) ===\n"
            + "\n\n".join(parts)
            + "\n\n=== TASK ===\n"
            + prompt
        )
