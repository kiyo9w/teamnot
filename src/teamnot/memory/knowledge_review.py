"""Knowledge gap review.

Before TeamNoT spends a single API call planning a task, the knowledge review
checks whether the brief + project have enough context for an autonomous agent
to work without guessing. If a critical gap is present (no stack info, no DoD
machine check, empty conventions on a non-trivial task) the pipeline refuses to
start and tells the user exactly what to add.

This is the "review tri thức dự án khi bắt đầu và cho người dùng biết rằng họ
đang thiếu gì" feature the user asked for. It is intentionally rule-based, not
LLM-based — gap detection must be free and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from teamnot.brief import Brief
from teamnot.workspace import Workspace


class GapSeverity(str, Enum):
    blocker = "blocker"   # pipeline refuses to start
    warning = "warning"   # pipeline runs, but flags the user
    info = "info"         # nice-to-have


@dataclass
class KnowledgeGap:
    code: str
    severity: GapSeverity
    title: str
    detail: str
    suggestion: str

    def to_md(self) -> str:
        emoji = {"blocker": "[BLOCKER]", "warning": "[WARN]", "info": "[INFO]"}[self.severity.value]
        return (
            f"### {emoji} {self.title}\n"
            f"  - **detail:** {self.detail}\n"
            f"  - **fix:** {self.suggestion}\n"
        )


@dataclass
class KnowledgeReview:
    gaps: list[KnowledgeGap] = field(default_factory=list)

    @property
    def blockers(self) -> list[KnowledgeGap]:
        return [g for g in self.gaps if g.severity == GapSeverity.blocker]

    @property
    def warnings(self) -> list[KnowledgeGap]:
        return [g for g in self.gaps if g.severity == GapSeverity.warning]

    @property
    def infos(self) -> list[KnowledgeGap]:
        return [g for g in self.gaps if g.severity == GapSeverity.info]

    @property
    def can_proceed(self) -> bool:
        return not self.blockers

    def summary(self) -> str:
        if not self.gaps:
            return "Knowledge review: OK — no gaps."
        return (
            f"Knowledge review: {len(self.blockers)} blocker(s), "
            f"{len(self.warnings)} warning(s), {len(self.infos)} info."
        )

    def to_md(self) -> str:
        lines = [f"## {self.summary()}", ""]
        for g in self.blockers + self.warnings + self.infos:
            lines.append(g.to_md())
        if not self.gaps:
            lines.append("_All checks passed. Ready to run._")
        return "\n".join(lines)


# ── Rule helpers ─────────────────────────────────────────────────────────────

_DEFAULT_CONVENTIONS_PROBES = (
    "# Project conventions",
    "Fill in:",
    "code style",
    "naming",
)


def _is_default_conventions(text: str) -> bool:
    """Tells whether conventions.md is still the scaffold (no real content)."""
    body = text.strip()
    if len(body) < 250:
        return True
    # Strip the scaffold header and see what's left
    for probe in _DEFAULT_CONVENTIONS_PROBES:
        body = body.replace(probe, "")
    return len(body.strip()) < 150


def _description_word_count(brief: Brief) -> int:
    return len(brief.task.description.split())


# ── Main entry ───────────────────────────────────────────────────────────────

def review_workspace(brief: Brief, workspace: Workspace | None = None) -> KnowledgeReview:
    """Scan a Brief + Workspace and return every detected gap.

    Pure I/O + rule application. No network calls, no LLM.
    """
    ws = workspace or Workspace(brief)
    ws.ensure()  # create skeleton if missing — does not overwrite

    gaps: list[KnowledgeGap] = []

    # ── 1. project.stack / language ────────────────────────────────────────
    if not brief.project.stack:
        gaps.append(KnowledgeGap(
            code="NO_STACK",
            severity=GapSeverity.warning,
            title="Brief does not declare a tech stack",
            detail="brief.project.stack is empty — agents will guess framework conventions.",
            suggestion="Add e.g. `stack: [fastapi, postgres]` to brief.yaml under `project:`.",
        ))
    if not brief.project.language:
        gaps.append(KnowledgeGap(
            code="NO_LANGUAGE",
            severity=GapSeverity.warning,
            title="Brief does not declare programming languages",
            detail="brief.project.language is empty — agents may pick the wrong runtime.",
            suggestion="Add e.g. `language: [python]` under `project:` in brief.yaml.",
        ))

    # ── 2. conventions.md ──────────────────────────────────────────────────
    if not ws.conventions_path.exists():
        gaps.append(KnowledgeGap(
            code="NO_CONVENTIONS_FILE",
            severity=GapSeverity.warning,
            title="No conventions file",
            detail=f"{ws.conventions_path} is missing.",
            suggestion="Run `teamnot init` to scaffold, then fill it with style/naming rules.",
        ))
    else:
        conv_text = ws.conventions_path.read_text(encoding="utf-8", errors="replace")
        if _is_default_conventions(conv_text):
            # Severity escalates with task complexity
            wc = _description_word_count(brief)
            sev = GapSeverity.blocker if wc > 40 else GapSeverity.warning
            gaps.append(KnowledgeGap(
                code="EMPTY_CONVENTIONS",
                severity=sev,
                title="Conventions file is still the scaffold",
                detail=(
                    f"{ws.conventions_path} has no real content. "
                    f"Task description is {wc} words — "
                    f"{'too detailed' if sev == GapSeverity.blocker else 'simple enough'} "
                    f"to risk going in the wrong direction without conventions."
                ),
                suggestion=(
                    "Fill conventions.md with: code style, file layout, naming, test layout, "
                    "git workflow, security rules. Then re-run."
                ),
            ))

    # ── 3. memory.md ───────────────────────────────────────────────────────
    if not ws.memory_path.exists():
        gaps.append(KnowledgeGap(
            code="NO_MEMORY_FILE",
            severity=GapSeverity.info,
            title="No project memory file yet",
            detail=f"{ws.memory_path} will be created on first run.",
            suggestion="No action required; TeamNoT seeds it automatically.",
        ))

    # ── 4. Definition of Done ──────────────────────────────────────────────
    machine_count = len(brief.definition_of_done.machine_checks())
    judge_count = len(brief.definition_of_done.judge_checks())
    if machine_count == 0:
        gaps.append(KnowledgeGap(
            code="NO_MACHINE_DOD",
            severity=GapSeverity.blocker,
            title="DoD has no machine-verifiable check",
            detail=(
                "definition_of_done contains only LLM judges. The loop has no "
                "objective halt condition — it will burn API spend until the budget "
                "runs out."
            ),
            suggestion=(
                "Add at least one `run:` or `file_exists:` check, e.g. `run: pytest -q` "
                "or `run: ruff check .`."
            ),
        ))
    if judge_count == 0 and brief.definition_of_done.llm_judge_required:
        gaps.append(KnowledgeGap(
            code="JUDGE_REQUIRED_BUT_MISSING",
            severity=GapSeverity.blocker,
            title="llm_judge_required is true but no llm_judge check is defined",
            detail="Brief asks for an LLM judge but no `llm_judge:` check exists.",
            suggestion="Add an `llm_judge:` check or set `llm_judge_required: false`.",
        ))

    # ── 5. Task description ────────────────────────────────────────────────
    if _description_word_count(brief) < 8:
        gaps.append(KnowledgeGap(
            code="THIN_TASK_DESCRIPTION",
            severity=GapSeverity.warning,
            title="Task description is very short",
            detail=(
                f"Only {_description_word_count(brief)} words. The planner will have "
                f"to invent details — high risk of building the wrong thing."
            ),
            suggestion="Expand the task description: inputs, outputs, edge cases, examples.",
        ))

    # ── 6. References ──────────────────────────────────────────────────────
    missing_refs: list[str] = []
    for ref in brief.task.references:
        if ref.startswith(("http://", "https://")):
            continue
        if not brief.absolute(ref).exists():
            missing_refs.append(ref)
    if missing_refs:
        gaps.append(KnowledgeGap(
            code="MISSING_REFERENCES",
            severity=GapSeverity.warning,
            title="Brief references files that don't exist",
            detail="Missing: " + ", ".join(missing_refs),
            suggestion="Create the files or remove them from `task.references`.",
        ))

    # ── 7. Cost guard sanity ───────────────────────────────────────────────
    b = brief.budget
    if (
        b.cost_guard_enabled
        and b.require_explicit_api_optin
        and not b.allowed_metered_workers
        and judge_count > 0
        and not _judge_is_subscription_only(brief)
    ):
        gaps.append(KnowledgeGap(
            code="JUDGE_NEEDS_METERED_OPTIN",
            severity=GapSeverity.warning,
            title="LLM judge configured but no metered workers opted in",
            detail=(
                "definition_of_done has an llm_judge check, but "
                "budget.allowed_metered_workers is empty. If the judge resolves to a "
                "metered worker the call will be refused at runtime."
            ),
            suggestion=(
                "Either wire a subscription/local LLM judge (Claude CLI, Ollama) or add "
                "the metered worker explicitly, e.g. `allowed_metered_workers: [minimax]`."
            ),
        ))

    # ── 8. Project actually has files? ─────────────────────────────────────
    if _is_empty_project(brief.project.path):
        gaps.append(KnowledgeGap(
            code="EMPTY_PROJECT",
            severity=GapSeverity.info,
            title="Project directory is essentially empty",
            detail=f"{brief.project.path} has no source files yet.",
            suggestion=(
                "If you want TeamNoT to scaffold a project from scratch, "
                "make sure the brief describes the structure clearly."
            ),
        ))

    return KnowledgeReview(gaps=gaps)


def _judge_is_subscription_only(brief: Brief) -> bool:
    """Heuristic: assume judge runs through Claude CLI (subscription) by default.

    Once the runtime can configure which worker the judge resolves to, this
    function should consult that config. For now, we conservatively say YES
    when conventions/notes mention claude_cli, otherwise NO.
    """
    hints = (brief.notes or "") + " " + str(brief.metadata)
    return any(h in hints.lower() for h in ("claude_cli", "claude code cli", "subscription judge"))


def _is_empty_project(path: Path, threshold: int = 3) -> bool:
    """True if the project has fewer than ``threshold`` non-hidden files (excluding .teamnot)."""
    if not path.exists():
        return True
    count = 0
    for p in path.iterdir():
        if p.name.startswith(".") or p.name == ".teamnot":
            continue
        count += 1
        if count >= threshold:
            return False
    return True
