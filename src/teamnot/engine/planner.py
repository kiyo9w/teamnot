"""Dual planner — parallel decomposition with MiniMax + Claude Code CLI.

Two planning streams run in threads:
  1. MiniMax — fast, metered, can be skipped if not in allow-list
  2. Claude CLI — subscription, deeper reasoning

If both succeed, Claude reviews and consolidates. If only one runs, that plan
goes through as-is. If neither is available (allow-list empty + no CLI), the
planner returns a stub plan with the raw requirement so the rest of the
pipeline can still proceed manually.

This is the refactored `dual_planner_legacy.py` with these changes:
  * Context comes from Workspace, not TeamNoT root
  * Plans are saved into the target project's `.teamnot/plans/`
  * Every call goes through CostGuard
  * If MiniMax is not opted in (allow-list excludes "minimax"), the planner
    transparently falls back to Claude-only.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from teamnot.brief import Brief
from teamnot.safety import CostGuard, WorkerNotAllowedError, WorkerPausedError
from teamnot.workers.claude_cli import ClaudeCliWorker
from teamnot.workers.minimax import MinimaxWorker
from teamnot.workspace import Workspace

logger = logging.getLogger("teamnot.engine.planner")


@dataclass
class PlanStreamResult:
    source: str
    raw: str
    elapsed_s: float
    success: bool
    error: str = ""


@dataclass
class DualPlanResult:
    success: bool
    final_plan_raw: str
    minimax: PlanStreamResult | None = None
    claude: PlanStreamResult | None = None
    review_raw: str = ""
    plan_file: str | None = None
    total_elapsed_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    def short_summary(self) -> str:
        bits = []
        if self.minimax:
            bits.append(f"minimax={'OK' if self.minimax.success else 'FAIL'}/{self.minimax.elapsed_s:.1f}s")
        if self.claude:
            bits.append(f"claude={'OK' if self.claude.success else 'FAIL'}/{self.claude.elapsed_s:.1f}s")
        return f"DualPlan: {'OK' if self.success else 'FAIL'} ({', '.join(bits)})"


# ── Stream implementations ───────────────────────────────────────────────────

def _plan_with_minimax(
    brief: Brief,
    minimax: MinimaxWorker,
) -> PlanStreamResult:
    start = time.time()
    system = (
        "You are a senior PM/Tech Lead. Decompose the software requirement into "
        "executable tasks. Output JSON with this structure:\n"
        '{"plan_name": "...", "tasks": [{"id": "T1", "title": "...", '
        '"description": "...", "domain": "FE|BE|AI|DevOps|QA", '
        '"depends_on": [], "estimated_minutes": N, '
        '"model_suggestion": "minimax|claude|local"}], '
        '"architecture_notes": "...", "risks": ["..."], "tech_stack": ["..."]}\n'
        "Be specific. Include file paths. Vietnamese or English OK."
    )
    user = (
        f"Project: {brief.project.name} ({', '.join(brief.project.stack) or 'no stack declared'})\n"
        f"Languages: {', '.join(brief.project.language) or 'unspecified'}\n"
        f"Task: {brief.task.title}\n\n"
        f"Requirement:\n{brief.task.description}\n\n"
        f"Constraints: {brief.task.constraints.model_dump()}\n"
    )
    try:
        result = minimax.run(system=system, user=user, note="dual_plan_minimax")
        return PlanStreamResult(
            source="minimax",
            raw=result.content,
            elapsed_s=round(time.time() - start, 1),
            success=bool(result.content.strip()),
        )
    except (WorkerNotAllowedError, WorkerPausedError) as e:
        return PlanStreamResult(
            source="minimax",
            raw="",
            elapsed_s=round(time.time() - start, 1),
            success=False,
            error=f"cost guard refused: {e}",
        )
    except Exception as e:
        logger.warning("minimax planning failed: %s", e)
        return PlanStreamResult(
            source="minimax",
            raw="",
            elapsed_s=round(time.time() - start, 1),
            success=False,
            error=f"{type(e).__name__}: {e}",
        )


def _plan_with_claude(
    brief: Brief,
    claude: ClaudeCliWorker,
) -> PlanStreamResult:
    start = time.time()
    prompt = (
        f"You are a senior PM/Tech Lead for project {brief.project.name}.\n\n"
        f"Decompose this software requirement into executable tasks.\n\n"
        f"REQUIREMENT:\n{brief.task.description}\n\n"
        f"Stack: {', '.join(brief.project.stack) or 'unspecified'}\n"
        f"Languages: {', '.join(brief.project.language) or 'unspecified'}\n\n"
        f"Output ONLY valid JSON with this structure:\n"
        '{"plan_name": "...", "tasks": [{"id": "T1", "title": "...", '
        '"description": "detailed with file paths", "domain": "FE|BE|AI|DevOps|QA", '
        '"depends_on": [], "estimated_minutes": N, "model_suggestion": "...", '
        '"acceptance_criteria": ["..."]}], '
        '"architecture_notes": "...", "risks": ["..."], "tech_stack": ["..."]}\n\n'
        "Be specific about file paths, API endpoints, and dependencies. "
        "Read conventions.md and memory.md before planning."
    )
    try:
        result = claude.run(
            prompt=prompt,
            allowed_tools=["Read", "Glob", "Grep"],
            timeout=180,
            note="dual_plan_claude",
        )
        success = bool(result.output.strip()) and result.returncode == 0
        return PlanStreamResult(
            source="claude",
            raw=result.output,
            elapsed_s=round(time.time() - start, 1),
            success=success,
            error="" if success else result.stderr or f"rc={result.returncode}",
        )
    except (WorkerNotAllowedError, WorkerPausedError) as e:
        return PlanStreamResult(
            source="claude",
            raw="",
            elapsed_s=round(time.time() - start, 1),
            success=False,
            error=f"cost guard refused: {e}",
        )
    except Exception as e:
        logger.warning("claude planning failed: %s", e)
        return PlanStreamResult(
            source="claude",
            raw="",
            elapsed_s=round(time.time() - start, 1),
            success=False,
            error=f"{type(e).__name__}: {e}",
        )


def _claude_review(
    brief: Brief,
    claude: ClaudeCliWorker,
    minimax_plan: PlanStreamResult,
    claude_plan: PlanStreamResult,
) -> str:
    """Claude reviews both plans and merges. Returns final plan JSON as string."""
    prompt = (
        f"You are the Lead Architect reviewing two competing task decomposition plans.\n\n"
        f"=== ORIGINAL REQUIREMENT ===\n{brief.task.description}\n\n"
        f"=== PLAN A (MiniMax) ===\n{minimax_plan.raw[:3500]}\n\n"
        f"=== PLAN B (Claude) ===\n{claude_plan.raw[:3500]}\n\n"
        "Compare both plans, take the best of each, and output the FINAL "
        "consolidated plan as JSON with the same schema as the inputs. "
        "Include a `review_notes` field summarizing why the final plan is "
        "structured this way. Read conventions.md and memory.md first."
    )
    try:
        result = claude.run(
            prompt=prompt,
            allowed_tools=["Read", "Glob", "Grep"],
            timeout=240,
            note="dual_plan_review",
        )
        return result.output
    except Exception as e:
        logger.warning("plan review failed: %s", e)
        return claude_plan.raw  # fall back to Claude's plan


# ── Public API ───────────────────────────────────────────────────────────────

def dual_plan(
    brief: Brief,
    workspace: Workspace,
    cost_guard: CostGuard,
    claude: ClaudeCliWorker | None = None,
    minimax: MinimaxWorker | None = None,
) -> DualPlanResult:
    """Run the dual planning pipeline.

    Either worker may be ``None`` (not available). If neither is available the
    function returns a stub plan so the rest of the pipeline can decide what
    to do.
    """
    workspace.ensure()
    total_start = time.time()

    notes: list[str] = []

    # ── Pre-flight: which planners are actually usable? ────────────────────
    minimax_usable = (
        minimax is not None
        and minimax.is_available()
        and (
            not cost_guard.budget.require_explicit_api_optin
            or "minimax" in cost_guard.budget.allowed_metered_workers
        )
    )
    claude_usable = (claude is not None) and claude.is_available()

    if not minimax_usable:
        notes.append("minimax planning skipped (no API key, no opt-in, or worker missing)")
    if not claude_usable:
        notes.append("claude planning skipped (CLI not found)")

    if not minimax_usable and not claude_usable:
        stub_plan = (
            f'{{"plan_name": "{brief.task.title}", '
            f'"tasks": [{{"id": "T1", "title": "{brief.task.title}", '
            f'"description": "Implement as described in brief.task.description", '
            f'"domain": "Unknown", "depends_on": [], "estimated_minutes": 60, '
            f'"acceptance_criteria": ["See definition_of_done"]}}], '
            f'"architecture_notes": "No planner available — single-task fallback.", '
            f'"risks": ["No automated plan; manual review required."], '
            f'"tech_stack": {list(brief.project.stack)}}}'
        )
        return DualPlanResult(
            success=False,
            final_plan_raw=stub_plan,
            total_elapsed_s=round(time.time() - total_start, 1),
            notes=notes + ["no planner available — using stub plan"],
        )

    # ── Run streams in parallel ─────────────────────────────────────────────
    minimax_result: PlanStreamResult | None = None
    claude_result: PlanStreamResult | None = None

    def _run_minimax():
        nonlocal minimax_result
        minimax_result = _plan_with_minimax(brief, minimax)  # type: ignore[arg-type]

    def _run_claude():
        nonlocal claude_result
        claude_result = _plan_with_claude(brief, claude)  # type: ignore[arg-type]

    threads: list[threading.Thread] = []
    if minimax_usable:
        t = threading.Thread(target=_run_minimax, name="dual_plan_minimax")
        threads.append(t)
        t.start()
    if claude_usable:
        t = threading.Thread(target=_run_claude, name="dual_plan_claude")
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=300)

    # ── Consolidate ─────────────────────────────────────────────────────────
    if (
        claude_usable
        and claude_result and claude_result.success
        and minimax_usable
        and minimax_result and minimax_result.success
    ):
        review_raw = _claude_review(brief, claude, minimax_result, claude_result)  # type: ignore[arg-type]
        final_raw = review_raw or claude_result.raw
    elif claude_result and claude_result.success:
        review_raw = ""
        final_raw = claude_result.raw
        notes.append("minimax failed/unavailable — using claude plan directly")
    elif minimax_result and minimax_result.success:
        review_raw = ""
        final_raw = minimax_result.raw
        notes.append("claude failed/unavailable — using minimax plan directly")
    else:
        review_raw = ""
        final_raw = ""
        notes.append("both planners failed")

    success = bool(final_raw.strip())

    # ── Save plan to workspace ──────────────────────────────────────────────
    plan_file: str | None = None
    if success:
        body = f"""# Dual Plan — {brief.task.id}
> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Plan A — MiniMax
```
{(minimax_result.raw if minimax_result else 'not run')[:3000]}
```

## Plan B — Claude CLI
```
{(claude_result.raw if claude_result else 'not run')[:3000]}
```

## Final Consolidated Plan
```
{final_raw[:5000]}
```

## Notes
{chr(10).join('- ' + n for n in notes) or '- (none)'}
"""
        path = workspace.write_plan(brief.task.id, body)
        plan_file = str(path)

    return DualPlanResult(
        success=success,
        final_plan_raw=final_raw,
        minimax=minimax_result,
        claude=claude_result,
        review_raw=review_raw,
        plan_file=plan_file,
        total_elapsed_s=round(time.time() - total_start, 1),
        notes=notes,
    )
