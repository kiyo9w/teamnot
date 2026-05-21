"""Deterministic customer-loop orchestration."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from teamnot.brief import load_brief
from teamnot.customer_loop.artifacts import (
    ensure_artifact_dirs,
    write_generated_brief,
    write_loop_summary,
    write_report_artifacts,
)
from teamnot.customer_loop.brief_generation import generate_followup_brief
from teamnot.customer_loop.models import (
    CustomerFinding,
    CustomerLoopConfig,
    CustomerLoopResult,
    CustomerLoopRunnerName,
    CustomerReport,
    CustomerSeverity,
)
from teamnot.customer_loop.runners import ManualEvidenceRunner, OpenClawWindowsCDPRunner

RunTeamNoT = Callable[[Path], None]

SEVERITY_RANK = {
    CustomerSeverity.critical: 4,
    CustomerSeverity.high: 3,
    CustomerSeverity.medium: 2,
    CustomerSeverity.low: 1,
    CustomerSeverity.positive: 0,
}


class CustomerLoopOrchestrator:
    def __init__(self, run_teamnot_hook: RunTeamNoT | None = None):
        self.run_teamnot_hook = run_teamnot_hook

    def run(self, config: CustomerLoopConfig) -> CustomerLoopResult:
        out_dir = ensure_artifact_dirs(config.out_dir)
        plan = default_customer_test_plan(config)
        runner = self._runner(config)
        report = runner.run(config.target, config.profile, plan, out_dir)
        write_report_artifacts(out_dir, config.profile, plan, report)
        selected = select_next_best_move(report, config.severity_threshold)
        generated = None
        teamnot_invoked = False
        stopped_reason = "no finding met severity threshold"
        if selected:
            previous = load_brief(config.previous_brief_path) if config.previous_brief_path else None
            generated = generate_followup_brief(report, selected, out_dir, previous)
            brief_path = write_generated_brief(out_dir, generated)
            stopped_reason = "generated follow-up brief"
            if config.run_teamnot:
                if self.run_teamnot_hook:
                    self.run_teamnot_hook(brief_path)
                teamnot_invoked = True
                stopped_reason = "generated follow-up brief and invoked TeamNoT"
        result = CustomerLoopResult(
            out_dir=out_dir,
            report=report,
            selected_finding=selected,
            generated_brief=generated,
            stopped_reason=stopped_reason,
            iterations_completed=1,
            teamnot_invoked=teamnot_invoked,
        )
        write_loop_summary(result)
        return result

    def _runner(self, config: CustomerLoopConfig):
        if config.runner == CustomerLoopRunnerName.manual:
            if config.evidence_path is None:
                from teamnot.customer_loop.models import CustomerLoopRunnerError

                raise CustomerLoopRunnerError("--evidence is required for manual customer-loop mode")
            return ManualEvidenceRunner(config.evidence_path)
        return OpenClawWindowsCDPRunner()


def select_next_best_move(
    report: CustomerReport,
    severity_threshold: CustomerSeverity = CustomerSeverity.high,
) -> CustomerFinding | None:
    threshold = SEVERITY_RANK[severity_threshold]
    candidates = [
        finding for finding in report.findings
        if SEVERITY_RANK[finding.severity] >= threshold and finding.severity != CustomerSeverity.positive
    ]
    if not candidates:
        return None
    return sorted(candidates, key=_finding_rank, reverse=True)[0]


def _finding_rank(finding: CustomerFinding) -> tuple[int, int, int, float]:
    return (
        SEVERITY_RANK[finding.severity],
        1 if finding.trust_blocker else 0,
        1 if finding.core_task_blocker else 0,
        finding.confidence,
    )


def default_customer_test_plan(config: CustomerLoopConfig):
    from teamnot.customer_loop.models import CustomerJob, CustomerTestPlan, CustomerTestTask

    return CustomerTestPlan(
        target=config.target,
        customer_job=CustomerJob(
            functional=(
                f"Complete the product's core workflow at {config.target.url} and decide "
                "whether it solves the customer problem without developer help."
            ),
            emotional="Feel safer and more confident after using the product in real work.",
            social="Be able to justify the result to a teammate, manager, client, or buyer.",
            importance=8,
        ),
        tasks=[
            CustomerTestTask(
                id="first-impression",
                title="Judge the first 30-second impression",
                instructions="Decide whether the target customer can tell who this is for, what job it solves, and what to do first.",
                success_criteria=["Purpose, audience, promise, and first action are clear without reading external docs."],
            ),
            CustomerTestTask(
                id="primary-workflow",
                title="Attempt the core workflow",
                instructions="Use realistic customer input and record blockers, confusing moments, and before/after evidence.",
                success_criteria=["The customer can complete the main job and reach a useful result without developer knowledge."],
            ),
            CustomerTestTask(
                id="output-actionability",
                title="Evaluate the result as a decision artifact",
                instructions="Check whether the output is specific, prioritized, explainable, exportable, and usable by a real operator.",
                success_criteria=["The customer can act on the output and explain it to someone else."],
            ),
            CustomerTestTask(
                id="error-recovery",
                title="Check mistake and recovery paths",
                instructions="Look for invalid-input handling, retry guidance, preserved work, and customer-friendly failure language.",
                success_criteria=["Mistakes produce clear next actions instead of dead ends or technical-only errors."],
            ),
            CustomerTestTask(
                id="trust-adoption",
                title="Assess trust, risk, and adoption blockers",
                instructions="Check data handling, privacy, proof, onboarding, support, pricing/packaging, domain fit, and buyer objections.",
                success_criteria=["A buyer or operator has enough confidence to try this with real work data."],
            ),
            CustomerTestTask(
                id="mobile-accessibility-reliability",
                title="Cover mobile review, accessibility basics, and reliability",
                instructions="Check phone-review suitability, labels/headings/focus cues, layout overflow, load time, failed resources, and runtime breakage.",
                success_criteria=["The product remains readable, operable, and credible outside the ideal desktop path."],
            ),
        ],
        notes=(
            "Use the customer-testing-openclaw rubric: plan across persona/JTBD, functional flows, "
            "adversarial/error cases, trust/adoption/domain fit, and coverage gaps. Emit STEP_PASS, "
            "STEP_FAIL, or STEP_SKIP markers with evidence. Judge from the customer perspective, not from "
            "implementation correctness."
        ),
    )
