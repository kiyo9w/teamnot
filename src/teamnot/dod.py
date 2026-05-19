"""Definition-of-Done evaluator.

The DoDEvaluator runs every check declared in ``brief.definition_of_done`` and
decides whether the autonomous loop can stop. It is the single source of truth
for "are we done yet?" — agents do not vote, the DoD does.

Six check kinds are supported:
    * run             — execute a shell command, gate on exit code + stdout
    * file_exists     — assert a path exists in the project
    * file_contains   — assert a file contains a substring
    * http_check      — hit a URL, expect a status code (and optional body)
    * custom_script   — run a project-supplied script, gate on exit code
    * llm_judge       — hand the diff to an LLM, gate on APPROVE/REJECT

Machine checks run first (cheap, deterministic). LLM judges run only after
machine checks pass — so we never spend an API call to "review" code that
already failed lint or tests.
"""
from __future__ import annotations

import logging
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from teamnot.brief import Brief, DefinitionOfDone, DoDCheck

if TYPE_CHECKING:
    from teamnot.safety import CostGuard

logger = logging.getLogger("teamnot.dod")


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    check: DoDCheck
    passed: bool
    output: str = ""
    error: str = ""
    duration_s: float = 0.0
    skipped: bool = False
    skip_reason: str = ""

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"

    def to_md(self) -> str:
        head = f"- [{self.status}] {self.check.name} ({self.duration_s:.1f}s)"
        if self.skipped:
            head += f" — skipped: {self.skip_reason}"
        if not self.passed and not self.skipped:
            err = (self.error or self.output or "no output").strip().splitlines()[:3]
            head += "\n  " + "\n  ".join(err)
        return head


@dataclass
class DoDResult:
    """Aggregate result of running the full DoD."""
    all_passed: bool
    machine_passed: bool
    judge_passed: bool
    results: list[CheckResult] = field(default_factory=list)
    failed_required: list[CheckResult] = field(default_factory=list)
    failed_optional: list[CheckResult] = field(default_factory=list)
    duration_s: float = 0.0

    def summary(self) -> str:
        head = f"DoD: {'PASS' if self.all_passed else 'FAIL'} "
        head += f"(machine={'OK' if self.machine_passed else 'FAIL'}, "
        head += f"judge={'OK' if self.judge_passed else 'FAIL/SKIP'}, "
        head += f"{self.duration_s:.1f}s)"
        return head

    def to_md(self) -> str:
        lines = [f"## {self.summary()}", ""]
        for r in self.results:
            lines.append(r.to_md())
        if self.failed_required:
            lines.append("")
            lines.append("### Required failures (must fix)")
            for r in self.failed_required:
                lines.append(f"- {r.check.name}")
        return "\n".join(lines)


# ── LLM judge protocol ───────────────────────────────────────────────────────

LLMJudgeFn = Callable[[str, str], tuple[bool, str]]
"""A function (prompt, project_diff) -> (approved, reason).

Pluggable so tests can use a stub and runtime can use the real worker.
"""


def default_llm_judge_unavailable(_prompt: str, _diff: str) -> tuple[bool, str]:
    """Placeholder used when no judge has been wired up. Always rejects."""
    return False, "no LLM judge wired (pass one to DoDEvaluator)"


# ── Evaluator ────────────────────────────────────────────────────────────────

class DoDEvaluator:
    """Runs every DoD check declared in a brief.

    Parameters
    ----------
    brief : Brief
        The brief whose DoD to evaluate.
    llm_judge : LLMJudgeFn | None
        Function used for ``llm_judge`` checks. If None, judge checks fail.
    cost_guard : CostGuard | None
        Optional guard wrapped around llm_judge calls so a runaway judge
        cannot burn the API budget.
    diff_provider : Callable[[], str] | None
        Function that returns the current diff (e.g. ``git diff``) handed to
        the LLM judge. If None, the judge gets an empty diff.
    """

    def __init__(
        self,
        brief: Brief,
        llm_judge: LLMJudgeFn | None = None,
        cost_guard: CostGuard | None = None,
        diff_provider: Callable[[], str] | None = None,
    ):
        self.brief = brief
        self.dod: DefinitionOfDone = brief.definition_of_done
        self.llm_judge = llm_judge or default_llm_judge_unavailable
        self.cost_guard = cost_guard
        self.diff_provider = diff_provider or (lambda: "")

    # ── Entry point ────────────────────────────────────────────────────────

    def evaluate(self) -> DoDResult:
        """Run every check. Machine checks first, then judge checks."""
        t0 = time.monotonic()
        results: list[CheckResult] = []

        # Phase 1 — machine checks
        for check in self.dod.machine_checks():
            results.append(self._run_check(check))

        machine_required_failed = [
            r for r in results if r.check.required and not r.passed and not r.skipped
        ]
        machine_passed = not machine_required_failed

        # Phase 2 — judge checks (only if machine passed OR not require_all_pass)
        judge_results: list[CheckResult] = []
        run_judges = machine_passed or not self.dod.require_all_pass
        for check in self.dod.judge_checks():
            if not run_judges:
                judge_results.append(CheckResult(
                    check=check, passed=False, skipped=True,
                    skip_reason="machine checks failed; skipping judge to save API spend",
                ))
            else:
                judge_results.append(self._run_check(check))
        results.extend(judge_results)

        judge_required_failed = [
            r for r in judge_results if r.check.required and not r.passed and not r.skipped
        ]
        judge_passed = not judge_required_failed if self.dod.llm_judge_required else True

        failed_required = [r for r in results if r.check.required and not r.passed and not r.skipped]
        failed_optional = [r for r in results if not r.check.required and not r.passed and not r.skipped]

        all_passed = (
            machine_passed and judge_passed
            if self.dod.require_all_pass
            else any(r.passed for r in results)
        )

        return DoDResult(
            all_passed=all_passed,
            machine_passed=machine_passed,
            judge_passed=judge_passed,
            results=results,
            failed_required=failed_required,
            failed_optional=failed_optional,
            duration_s=round(time.monotonic() - t0, 2),
        )

    # ── Per-check dispatch ────────────────────────────────────────────────

    def _run_check(self, check: DoDCheck) -> CheckResult:
        t0 = time.monotonic()
        try:
            if check.kind == "run":
                ok, out, err = self._check_run(check)
            elif check.kind == "file_exists":
                ok, out, err = self._check_file_exists(check)
            elif check.kind == "file_contains":
                ok, out, err = self._check_file_contains(check)
            elif check.kind == "http_check":
                ok, out, err = self._check_http(check)
            elif check.kind == "custom_script":
                ok, out, err = self._check_custom_script(check)
            elif check.kind == "llm_judge":
                ok, out, err = self._check_llm_judge(check)
            else:
                ok, out, err = False, "", f"unknown check kind: {check.kind}"
        except Exception as e:
            ok, out, err = False, "", f"check raised: {type(e).__name__}: {e}"

        return CheckResult(
            check=check,
            passed=ok,
            output=out,
            error=err,
            duration_s=round(time.monotonic() - t0, 2),
        )

    # ── Check implementations ─────────────────────────────────────────────

    def _resolve_cwd(self, check: DoDCheck) -> Path:
        if check.cwd:
            return self.brief.absolute(check.cwd)
        return self.brief.project.path

    def _check_run(self, check: DoDCheck) -> tuple[bool, str, str]:
        assert check.run is not None
        cmd = check.run
        cwd = self._resolve_cwd(check)
        # NOTE on `shell=True`: DoD checks live in the *user's own* brief inside
        # their *own* repo. The threat model treats the brief author as trusted
        # — same trust level as a Makefile or pre-commit hook in the project.
        # We do NOT consume arbitrary commands from network input. If you need
        # a hardened mode in the future, switch this to shlex.split() and gate
        # multi-command operators (&&, ||, ;) behind a `Brief.budget.allow_shell` flag.
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=check.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return False, "", f"timeout after {check.timeout_s}s"

        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        exit_ok = (check.expect_exit is None) or (proc.returncode == check.expect_exit)
        stdout_ok = True
        if check.expect_stdout_contains:
            stdout_ok = check.expect_stdout_contains in (proc.stdout or "")
        regex_ok = True
        if check.expect_stdout_regex:
            regex_ok = re.search(check.expect_stdout_regex, proc.stdout or "") is not None

        ok = exit_ok and stdout_ok and regex_ok
        err = "" if ok else (
            f"exit={proc.returncode} (expected {check.expect_exit}); "
            f"stdout_contains={stdout_ok}; regex={regex_ok}"
        )
        return ok, out, err

    def _check_file_exists(self, check: DoDCheck) -> tuple[bool, str, str]:
        assert check.file_exists is not None
        path = self.brief.absolute(check.file_exists)
        if path.exists():
            return True, f"found: {path}", ""
        return False, "", f"missing: {path}"

    def _check_file_contains(self, check: DoDCheck) -> tuple[bool, str, str]:
        assert check.file_contains is not None
        missing: list[str] = []
        for rel, needle in check.file_contains.items():
            path = self.brief.absolute(rel)
            if not path.exists():
                missing.append(f"{rel}: file missing")
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                missing.append(f"{rel}: read failed ({e})")
                continue
            if needle not in content:
                missing.append(f"{rel}: missing substring {needle!r}")
        if missing:
            return False, "", "; ".join(missing)
        return True, f"all {len(check.file_contains)} substrings present", ""

    def _check_http(self, check: DoDCheck) -> tuple[bool, str, str]:
        assert check.http_check is not None
        spec = check.http_check
        url = spec.get("url")
        if not url:
            return False, "", "http_check missing url"
        method = (spec.get("method") or "GET").upper()
        expect_status = int(spec.get("status", 200))
        timeout = float(spec.get("timeout_s", check.timeout_s))
        body_contains = spec.get("body_contains")

        try:
            import httpx
        except ImportError:
            return False, "", "httpx not installed — install with `pip install httpx`"
        try:
            r = httpx.request(method, url, timeout=timeout)
        except Exception as e:
            return False, "", f"request failed: {type(e).__name__}: {e}"

        ok_status = r.status_code == expect_status
        body_ok = True
        if body_contains:
            body_ok = body_contains in r.text
        if ok_status and body_ok:
            return True, f"{method} {url} → {r.status_code}", ""
        return False, "", (
            f"status={r.status_code} (expected {expect_status}); "
            f"body_contains={body_ok}"
        )

    def _check_custom_script(self, check: DoDCheck) -> tuple[bool, str, str]:
        assert check.custom_script is not None
        script_path = self.brief.absolute(check.custom_script)
        if not script_path.exists():
            return False, "", f"script not found: {script_path}"

        cmd: list[str] | str
        if script_path.suffix in {".sh", ""}:
            cmd = ["bash", str(script_path)]
        elif script_path.suffix == ".ps1":
            cmd = ["powershell", "-NoProfile", "-File", str(script_path)]
        elif script_path.suffix == ".py":
            cmd = ["python", str(script_path)]
        else:
            cmd = shlex.split(str(script_path))

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._resolve_cwd(check)),
                capture_output=True,
                text=True,
                timeout=check.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return False, "", f"script timeout after {check.timeout_s}s"
        ok = proc.returncode == (check.expect_exit if check.expect_exit is not None else 0)
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        err = "" if ok else f"exit={proc.returncode}"
        return ok, out, err

    def _check_llm_judge(self, check: DoDCheck) -> tuple[bool, str, str]:
        assert check.llm_judge is not None
        diff = ""
        try:
            diff = self.diff_provider()
        except Exception as e:
            logger.warning("diff_provider failed: %s", e)

        # Gate the judge call through the cost guard if available.
        if self.cost_guard is not None:
            try:
                from teamnot.safety import BillingModel, get_worker_tag

                # The judge worker name is conventionally "llm_judge". The caller
                # may register a tag for it; otherwise it's treated as metered
                # (deny-by-default for safety).
                tag = get_worker_tag("llm_judge")
                if tag.billing == BillingModel.metered:
                    est = self.brief.budget.llm_judge_estimated_usd
                    with self.cost_guard.gate("llm_judge", estimated_usd=est, note="dod judge"):
                        approved, reason = self.llm_judge(check.llm_judge, diff)
                else:
                    approved, reason = self.llm_judge(check.llm_judge, diff)
            except Exception as e:
                return False, "", f"judge gate refused: {type(e).__name__}: {e}"
        else:
            approved, reason = self.llm_judge(check.llm_judge, diff)
        if approved:
            return True, f"judge APPROVE: {reason}", ""
        return False, "", f"judge REJECT: {reason}"
