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
            functional=f"Evaluate whether {config.target.url} solves the target workflow.",
            emotional="Feel confident enough to trust the product for real work.",
            social="Be comfortable recommending the workflow to the team or buyer.",
        ),
        tasks=[
            CustomerTestTask(
                id="task-001",
                title="Attempt the core customer workflow",
                instructions="Use the product as the stated customer profile and capture blockers.",
                success_criteria=["Customer can complete the core workflow without trust blockers."],
            )
        ],
    )
