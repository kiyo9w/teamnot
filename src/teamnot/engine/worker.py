"""The top-level Worker — runs a Brief from start to finish.

This is the S2 minimum viable engine. It:
  1. Locks the workspace so two workers can't trample one project
  2. Runs the knowledge review — refuses to start if there are blockers
  3. Builds CostGuard + worker handles
  4. Dual-plans and saves the plan
  5. Runs the DoD evaluator against the current state (S2 stub)
  6. Writes a report and snapshot

S3 fills in the implement/test/review/document loop between (4) and (5). The
loop will live in a `pipeline.py` next to this file; for now the engine just
demonstrates the contract end-to-end so the user can already drive everything
that doesn't require code generation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from teamnot.agents.bus import AgentMessageBus
from teamnot.agents.spec import SkillRegistry, default_skills_dir, load_skills_from_dir
from teamnot.brief import Brief, load_brief
from teamnot.delivery.handover import HandoverResult, handover
from teamnot.dod import DoDEvaluator, DoDResult
from teamnot.engine.pipeline import (
    Pipeline,
    PipelineConfig,
    PipelineOutcome,
    PipelineResult,
    routed_cli_invoker,
)
from teamnot.engine.planner import DualPlanResult, dual_plan
from teamnot.memory.knowledge_review import KnowledgeReview, review_workspace
from teamnot.safety import CostGuard
from teamnot.workers.claude_cli import ClaudeCliWorker
from teamnot.workers.codex_cli import CodexCliWorker
from teamnot.workers.minimax import MinimaxWorker
from teamnot.workspace import Workspace, WorkspaceLockError

logger = logging.getLogger("teamnot.engine.worker")


class WorkerStatus(str, Enum):
    pending = "pending"
    blocked_knowledge = "blocked_knowledge"
    planning = "planning"
    plan_failed = "plan_failed"
    pipeline_skipped = "pipeline_skipped"     # no skills / pipeline disabled
    blocked_retries = "blocked_retries"
    dod_failed = "dod_failed"
    dod_passed = "dod_passed"
    blocked_budget = "blocked_budget"
    error = "error"


@dataclass
class WorkerResult:
    status: WorkerStatus
    summary: str
    brief_path: str | None = None
    knowledge: KnowledgeReview | None = None
    plan: DualPlanResult | None = None
    dod: DoDResult | None = None
    pipeline: PipelineResult | None = None
    cost: dict | None = None
    report_path: str | None = None
    handover: HandoverResult | None = None
    error: str = ""
    extras: dict = field(default_factory=dict)


class Worker:
    """One Brief, one Worker. Construct, then call run_until_done()."""

    def __init__(self, brief: Brief):
        self.brief = brief
        self.ws = Workspace(brief)

    @classmethod
    def from_path(cls, brief_path: str | Path) -> Worker:
        return cls(load_brief(brief_path))

    # ── Main entry ────────────────────────────────────────────────────────

    def run_until_done(
        self,
        *,
        skip_review: bool = False,
        plan: bool = True,
        run_pipeline: bool = True,
        skills_dir: Path | None = None,
        deliver: bool = True,
        wait_for_lock_s: float = 0.0,
    ) -> WorkerResult:
        """End-to-end run. Returns whatever the loop converges on.

        S2 contract:
          - knowledge review must pass (unless skip_review)
          - dual plan runs and is saved to .teamnot/plans/
          - DoD evaluated against the current project state
          - report written to .teamnot/reports/

        The implement → test → review loop lands in S3.
        """
        self.ws.ensure()

        try:
            with self.ws.lock(owner="teamnot.worker", wait_s=wait_for_lock_s):
                return self._run_inside_lock(
                    skip_review=skip_review,
                    plan=plan,
                    run_pipeline=run_pipeline,
                    skills_dir=skills_dir,
                    deliver=deliver,
                )
        except WorkspaceLockError as e:
            return WorkerResult(
                status=WorkerStatus.error,
                summary="workspace locked",
                error=str(e),
            )

    # ── Inside the lock ───────────────────────────────────────────────────

    def _run_inside_lock(
        self,
        *,
        skip_review: bool,
        plan: bool,
        run_pipeline: bool,
        skills_dir: Path | None,
        deliver: bool,
    ) -> WorkerResult:
        # 1. Knowledge review
        review = review_workspace(self.brief, self.ws)
        if not skip_review and not review.can_proceed:
            self.ws.write_report(
                self.brief.task.id,
                self._render_report(review=review, status=WorkerStatus.blocked_knowledge),
            )
            return WorkerResult(
                status=WorkerStatus.blocked_knowledge,
                summary=review.summary(),
                knowledge=review,
                brief_path=str(self.brief._source_path) if self.brief._source_path else None,
            )

        # 2. Build runtime services
        guard = CostGuard.from_brief(self.brief)
        claude = ClaudeCliWorker(self.brief, self.ws, guard)
        codex = CodexCliWorker(self.brief, self.ws, guard)
        minimax = MinimaxWorker(guard)

        # 3. Dual plan
        plan_result: DualPlanResult | None = None
        if plan:
            try:
                plan_result = dual_plan(self.brief, self.ws, guard, claude, minimax, codex)
                self.ws.save_checkpoint(
                    self.brief.task.id,
                    phase="01-plan",
                    status="DONE" if plan_result.success else "FAILED",
                    payload={
                        "plan_file": plan_result.plan_file,
                        "elapsed_s": plan_result.total_elapsed_s,
                        "notes": plan_result.notes,
                    },
                )
            except Exception as e:
                logger.exception("dual plan crashed")
                self.ws.write_report(
                    self.brief.task.id,
                    self._render_report(
                        review=review,
                        status=WorkerStatus.plan_failed,
                        error=str(e),
                        cost=guard.status(),
                    ),
                )
                return WorkerResult(
                    status=WorkerStatus.error,
                    summary="dual plan crashed",
                    error=f"{type(e).__name__}: {e}",
                    knowledge=review,
                    cost=guard.status(),
                )

        # 4. DoD evaluator
        dod_eval = DoDEvaluator(self.brief, cost_guard=guard)

        # 5. Multi-agent pipeline (S3): the loop iterates implement → test →
        # review → re-evaluate DoD, halting when DoD passes.
        pipeline_result: PipelineResult | None = None
        if run_pipeline:
            registry = self._load_registry(skills_dir)
            if registry:
                bus = AgentMessageBus(log_path=self.ws.logs_dir / "messages.jsonl")
                pipeline = Pipeline(
                    brief=self.brief,
                    workspace=self.ws,
                    cost_guard=guard,
                    registry=registry,
                    bus=bus,
                    dod_evaluator=dod_eval,
                    invoker=routed_cli_invoker(claude=claude, codex=codex),
                    config=PipelineConfig(
                        max_iterations=self.brief.budget.max_dod_attempts,
                        max_consecutive_failures=self.brief.budget.max_retries,
                    ),
                )
                try:
                    pipeline_result = pipeline.run()
                    self.ws.save_checkpoint(
                        self.brief.task.id,
                        phase="50-pipeline",
                        status=pipeline_result.outcome.value,
                        payload={
                            "iterations": pipeline_result.iterations,
                            "turns": len(pipeline_result.turns),
                            "notes": pipeline_result.notes,
                        },
                    )
                    # Persist transcript
                    (self.ws.logs_dir / f"{self.brief.task.id}__transcript.md").write_text(
                        bus.to_md(), encoding="utf-8"
                    )
                except Exception as e:
                    logger.exception("pipeline crashed")
                    pipeline_result = PipelineResult(
                        outcome=PipelineOutcome.error,
                        error=f"{type(e).__name__}: {e}",
                    )

        # 6. Final DoD evaluation
        dod_result = dod_eval.evaluate()
        self.ws.save_checkpoint(
            self.brief.task.id,
            phase="99-dod",
            status="PASS" if dod_result.all_passed else "FAIL",
            payload={"summary": dod_result.summary()},
        )

        # 7. Status decision
        if guard.is_halted:
            status = WorkerStatus.blocked_budget
        elif dod_result.all_passed:
            status = WorkerStatus.dod_passed
        elif pipeline_result and pipeline_result.outcome == PipelineOutcome.blocked_retries:
            status = WorkerStatus.blocked_retries
        elif plan_result and not plan_result.success:
            status = WorkerStatus.plan_failed
        elif pipeline_result is None:
            status = WorkerStatus.pipeline_skipped
        else:
            status = WorkerStatus.dod_failed

        # 8. Report
        report_body = self._render_report(
            review=review,
            plan=plan_result,
            dod=dod_result,
            pipeline=pipeline_result,
            status=status,
            cost=guard.status(),
        )
        report_path = self.ws.write_report(self.brief.task.id, report_body)

        # 9. Handover
        handover_result: HandoverResult | None = None
        if deliver:
            try:
                handover_result = handover(
                    self.brief,
                    success=(status == WorkerStatus.dod_passed),
                    report_body=report_body,
                    report_path=report_path,
                )
            except Exception as e:
                logger.exception("handover failed")
                handover_result = HandoverResult(
                    ok=False,
                    deliverable_type=self.brief.deliverable.type.value,
                    summary=f"handover crashed: {type(e).__name__}: {e}",
                )

        return WorkerResult(
            status=status,
            summary=dod_result.summary(),
            brief_path=str(self.brief._source_path) if self.brief._source_path else None,
            knowledge=review,
            plan=plan_result,
            dod=dod_result,
            pipeline=pipeline_result,
            cost=guard.status(),
            report_path=str(report_path),
            handover=handover_result,
        )

    # ── Skills loader ────────────────────────────────────────────────────

    def _load_registry(self, override: Path | None) -> SkillRegistry | None:
        """Load the skill registry from disk. Returns None when none are found."""
        candidates: list[Path] = []
        if override:
            candidates.append(override.expanduser().resolve())
        # Per-project skills override the global ones
        candidates.append(self.brief.project.path / ".teamnot" / "skills")
        candidates.append(default_skills_dir())

        registry: SkillRegistry | None = None
        for c in candidates:
            if c.exists():
                reg = load_skills_from_dir(c)
                if reg.specs:
                    registry = reg
                    break
        return registry

    # ── Reporting ─────────────────────────────────────────────────────────

    def _render_report(
        self,
        *,
        review: KnowledgeReview | None = None,
        plan: DualPlanResult | None = None,
        dod: DoDResult | None = None,
        pipeline: PipelineResult | None = None,
        status: WorkerStatus = WorkerStatus.pending,
        cost: dict | None = None,
        error: str = "",
    ) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        parts = [
            f"# TeamNoT report — {self.brief.task.id}",
            "",
            f"- **Project:** {self.brief.project.name}  (`{self.brief.project.path}`)",
            f"- **Task:** {self.brief.task.title}",
            f"- **Status:** `{status.value}`",
            f"- **Generated:** {now}",
        ]
        if error:
            parts += ["", "## Error", "", f"```\n{error}\n```"]
        if review is not None:
            parts += ["", review.to_md()]
        if plan is not None:
            parts += [
                "",
                "## Plan",
                "",
                f"- success: **{plan.success}**",
                f"- total elapsed: {plan.total_elapsed_s}s",
                f"- file: `{plan.plan_file}`",
            ]
            if plan.notes:
                parts.append("- notes:")
                parts.extend(f"  - {n}" for n in plan.notes)
        if pipeline is not None:
            parts += [
                "",
                "## Pipeline",
                "",
                f"- outcome: **{pipeline.outcome.value}**",
                f"- iterations: {pipeline.iterations}",
                f"- agent turns: {len(pipeline.turns)}",
            ]
            for t in pipeline.turns:
                parts.append(
                    f"  - `{t.agent}` ({t.duration_s}s) "
                    f"{'OK' if t.success else 'FAIL'}"
                )
            if pipeline.notes:
                parts.append("- notes:")
                parts.extend(f"  - {n}" for n in pipeline.notes)
        if dod is not None:
            parts += ["", dod.to_md()]
        if cost is not None:
            parts += [
                "",
                "## Cost guard",
                "",
                f"- spent: ${cost['spent_usd']} / ${cost['budget']['max_usd']}",
                f"- elapsed: {cost['elapsed_minutes']} / {cost['budget']['max_minutes']} min",
                f"- metered calls: {cost['metered_calls']}",
                f"- total calls: {cost['total_calls']}",
                f"- halted: {cost['halted']}",
            ]
            if cost.get("halt_reason"):
                parts.append(f"- halt reason: {cost['halt_reason']}")
        parts += [
            "",
            "## Task description",
            "",
            self.brief.task.description.strip(),
        ]
        return "\n".join(parts) + "\n"
