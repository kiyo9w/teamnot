"""Codex CLI worker.

Wraps the local `codex exec` command as a subprocess. Like Claude CLI, this is
a subscription worker: cost guard records the call for audit/time budgeting but
does not charge per token.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from teamnot.brief import Brief
from teamnot.safety import CostGuard
from teamnot.workspace import Workspace

logger = logging.getLogger("teamnot.workers.codex_cli")

WORKER_NAME = "codex_cli"


class CodexCliNotFoundError(RuntimeError):
    pass


def find_codex_cli() -> str:
    """Locate the `codex` executable on Windows / POSIX."""
    candidates = [
        "codex",
        str(Path.home() / ".local" / "bin" / "codex"),
        str(Path.home() / ".local" / "share" / "mise" / "shims" / "codex"),
        str(Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"),
    ]
    for c in candidates:
        try:
            r = subprocess.run(
                [c, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                shell=c.endswith(".cmd"),
            )
            if r.returncode == 0:
                return c
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    raise CodexCliNotFoundError(
        "codex CLI not found. Install/login with Codex CLI, then run `codex doctor`."
    )


@dataclass
class CodexCliResult:
    output: str
    returncode: int
    stderr: str
    elapsed_s: float


class CodexCliWorker:
    """A reusable handle on Codex CLI bound to a single workspace."""

    def __init__(self, brief: Brief, workspace: Workspace, cost_guard: CostGuard):
        self.brief = brief
        self.ws = workspace
        self.guard = cost_guard
        self._cli_path: str | None = None

    @property
    def cli_path(self) -> str:
        if self._cli_path is None:
            self._cli_path = find_codex_cli()
        return self._cli_path

    def is_available(self) -> bool:
        try:
            _ = self.cli_path
            return True
        except CodexCliNotFoundError:
            return False

    def run(
        self,
        prompt: str,
        *,
        context_files: list[str] | None = None,
        model: str | None = None,
        timeout: int = 300,
        allowed_tools: list[str] | None = None,
        note: str = "",
    ) -> CodexCliResult:
        with self.guard.gate(WORKER_NAME, estimated_usd=0.0, note=note or "codex_cli") as call:
            import time as _time

            start = _time.monotonic()
            full_prompt = self._inject_context(prompt, context_files or [], allowed_tools or [])

            cmd = [
                self.cli_path,
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--color",
                "never",
                "-C",
                str(self.ws.root),
                "-",
            ]
            selected_model = model or os.environ.get("TEAMNOT_CODEX_MODEL")
            if selected_model:
                cmd[2:2] = ["--model", selected_model]

            env = os.environ.copy()
            current_home = Path(env.get("CODEX_HOME", "")).expanduser()
            default_home = Path.home() / ".codex"
            if not (current_home / "auth.json").exists() and (default_home / "auth.json").exists():
                env["CODEX_HOME"] = str(default_home)
            env.setdefault("TERM", "xterm-256color")

            with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as stdout_file, tempfile.NamedTemporaryFile(
                "w+", encoding="utf-8"
            ) as stderr_file:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                    encoding="utf-8",
                    cwd=str(self.ws.root),
                    env=env,
                    start_new_session=(os.name != "nt"),
                )
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(full_prompt)
                    proc.stdin.close()
                    returncode = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._terminate_process_tree(proc)
                    call.record_actual(usd=0.0, note=f"timeout after {timeout}s")
                    return CodexCliResult(
                        output="",
                        returncode=-1,
                        stderr=f"timeout after {timeout}s",
                        elapsed_s=_time.monotonic() - start,
                    )

                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout = stdout_file.read()
                stderr = stderr_file.read()

            call.record_actual(usd=0.0)
            return CodexCliResult(
                output=(stdout or "").strip(),
                returncode=returncode,
                stderr=(stderr or "").strip(),
                elapsed_s=_time.monotonic() - start,
            )

    def _terminate_process_tree(self, proc: subprocess.Popen[str]) -> None:
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
                return
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _inject_context(self, prompt: str, context_files: list[str], allowed_tools: list[str]) -> str:
        parts: list[str] = []
        for relative in context_files:
            text = self.ws.read_reference(relative)
            parts.append(f"=== {Path(relative).name} ===\n{text}")

        conv = self.ws.read_conventions(max_chars=4000)
        mem = self.ws.read_memory(max_chars=4000)
        if conv.strip():
            parts.append(f"=== conventions.md ===\n{conv}")
        if mem.strip():
            parts.append(f"=== memory.md ===\n{mem}")

        tool_note = ""
        if allowed_tools:
            tool_note = (
                "=== REQUESTED TOOL SURFACE ===\n"
                "The originating TeamNoT skill requested these tools: "
                + ", ".join(allowed_tools)
                + ". Stay within that intent when possible.\n\n"
            )

        if not parts:
            return tool_note + prompt
        return (
            tool_note
            + "=== CONTEXT (read before acting) ===\n"
            + "\n\n".join(parts)
            + "\n\n=== TASK ===\n"
            + prompt
        )
