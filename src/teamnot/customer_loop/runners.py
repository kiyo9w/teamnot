"""Experience runners for customer-loop evidence collection."""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from teamnot.customer_loop.models import (
    CustomerEvidence,
    CustomerFinding,
    CustomerLoopRunnerError,
    CustomerProfile,
    CustomerReport,
    CustomerSeverity,
    CustomerTestPlan,
    ExperienceTarget,
)

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class ExperienceRunner(Protocol):
    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        """Collect or ingest customer evidence."""


class ManualEvidenceRunner:
    def __init__(self, evidence_path: str | Path):
        self.evidence_path = Path(evidence_path).expanduser()

    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        if not self.evidence_path.exists():
            raise CustomerLoopRunnerError(f"Manual evidence file not found: {self.evidence_path}")
        raw = self.evidence_path.read_text(encoding="utf-8")
        evidence = CustomerEvidence(
            path=str(self.evidence_path),
            observed_behavior=_first_nonempty_line(raw),
            raw_excerpt=raw[:2000],
        )
        finding = _finding_from_manual_text(raw, evidence)
        return CustomerReport(
            profile=profile,
            target=target,
            plan=plan,
            findings=[finding] if finding else [],
            evidence=[evidence],
            summary=_first_nonempty_line(raw) or "Manual evidence ingested.",
            raw_report_path=str(self.evidence_path),
        )


class OpenClawWindowsCDPRunner:
    def __init__(
        self,
        wrapper_path: str | Path = "scripts/winbrowser",
        command_runner: CommandRunner | None = None,
    ):
        self.wrapper_path = Path(wrapper_path)
        self.command_runner = command_runner or self._default_runner

    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        if not self.wrapper_path.exists():
            raise CustomerLoopRunnerError(
                "OpenClaw Windows CDP runner requires scripts/winbrowser. "
                "Install or provide the wrapper, or use --runner manual --evidence FILE."
            )
        screenshot = out_dir / "screenshots" / "openclaw-cdp.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        self._run(["--action", "status"])
        self._run(["--action", "navigate", "--url", str(target.url)])
        self._run(["--action", "screenshot", "--out", str(screenshot)])
        title = self._run(["--action", "eval", "--expr", "document.title"]).stdout.strip()
        evidence = CustomerEvidence(
            kind="browser_observation",
            path=str(screenshot),
            screenshot_paths=[str(screenshot)],
            observed_behavior=f"Browser reached {target.url}. Title: {title}",
        )
        return CustomerReport(
            profile=profile,
            target=target,
            plan=plan,
            findings=[],
            evidence=[evidence],
            summary=evidence.observed_behavior,
        )

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        command = [str(self.wrapper_path), *args]
        result = self.command_runner(command)
        if result.returncode != 0:
            raise CustomerLoopRunnerError(
                f"OpenClaw wrapper failed: {' '.join(command)}\n{result.stderr.strip()}"
            )
        return result

    @staticmethod
    def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip(" #\t")
        if cleaned:
            return cleaned
    return ""


def _finding_from_manual_text(text: str, evidence: CustomerEvidence) -> CustomerFinding | None:
    severity = CustomerSeverity.medium
    match = re.search(r"severity\s*[:|-]\s*(critical|high|medium|low|positive)", text, re.I)
    heading_match = re.search(
        r"^\s*#{2,6}\s*(critical|high|medium|low|positive)\s*[-:]\s*(.+?)\s*$",
        text,
        re.I | re.M,
    )
    if match:
        severity = CustomerSeverity(match.group(1).lower())
    elif heading_match:
        severity = CustomerSeverity(heading_match.group(1).lower())
    title = _extract_labeled(text, "title") or (heading_match.group(2).strip() if heading_match else "")
    title = title or _first_nonempty_line(text)
    if not title:
        return None
    recommendation = _extract_labeled(text, "recommendation") or _extract_labeled(text, "recommended fix")
    customer_interpretation = (
        _extract_labeled(text, "customer interpretation")
        or _extract_labeled(text, "customer impact")
    )
    return CustomerFinding(
        id="manual-001",
        title=title[:160],
        severity=severity,
        evidence=[evidence],
        customer_interpretation=customer_interpretation,
        business_impact=_extract_labeled(text, "business impact"),
        likely_frequency=_extract_labeled(text, "likely frequency"),
        recommendation=recommendation,
        confidence=0.75,
        trust_blocker="trust" in text.lower(),
        core_task_blocker=any(token in text.lower() for token in ("blocked", "cannot", "can't", "fails")),
    )


def _extract_labeled(text: str, label: str) -> str:
    pattern = rf"^\s*(?:[-*]\s*)?{re.escape(label)}\s*[:|-]\s*(.+?)\s*$"
    match = re.search(pattern, text, re.I | re.M)
    return match.group(1).strip() if match else ""
