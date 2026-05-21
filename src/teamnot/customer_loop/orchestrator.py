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
from teamnot.customer_loop.io import load_domain_oracles, load_model, load_seeded_state
from teamnot.customer_loop.models import (
    CustomerFinding,
    CustomerFlowPack,
    CustomerLoopConfig,
    CustomerLoopResult,
    CustomerLoopRunnerError,
    CustomerLoopRunnerName,
    CustomerReport,
    CustomerSeverity,
)
from teamnot.customer_loop.research_planning import (
    compare_iteration_coverage,
    evaluate_domain_oracles,
)
from teamnot.customer_loop.runners import (
    ManualEvidenceRunner,
    OpenClawWindowsCDPRunner,
    OpenClawWindowsFlowRunner,
    OpenClawWindowsInteractiveRunner,
    OpenClawWindowsResearcherRunner,
    OpenClawWindowsSessionRunner,
)

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
        if config.seeded_state_path and config.seeded_state is None:
            config.seeded_state = load_seeded_state(config.seeded_state_path)
        if config.domain_oracle_path and not config.domain_oracles:
            config.domain_oracles = load_domain_oracles(config.domain_oracle_path)
        runner = self._runner(config)
        report: CustomerReport | None = None
        selected: CustomerFinding | None = None
        generated = None
        teamnot_invoked = False
        stopped_reason = "no finding met severity threshold"
        iteration_out_dirs: list[Path] = []
        iterations_completed = 0
        last_invoked_finding_id: str | None = None
        iteration_coverage = []

        for iteration in range(1, config.max_iterations + 1):
            iteration_dir = _iteration_dir(out_dir, iteration, config.max_iterations)
            iteration_out_dirs.append(iteration_dir)
            report = runner.run(config.target, config.profile, plan, iteration_dir)
            _attach_seeded_state_contract(report, config)
            report.domain_oracles = evaluate_domain_oracles(report, config.domain_oracles)
            coverage = compare_iteration_coverage(
                iteration,
                report,
                iteration_coverage[-1] if iteration_coverage else None,
            )
            write_report_artifacts(iteration_dir, config.profile, plan, report)
            selected = select_next_best_move(report, config.severity_threshold)
            coverage.selected_finding_id = selected.id if selected else None
            generated = None
            iterations_completed = iteration
            if not selected:
                stopped_reason = "no finding met severity threshold"
                coverage.stop_reason = stopped_reason
                iteration_coverage.append(coverage)
                break
            if config.run_teamnot and last_invoked_finding_id == selected.id:
                stopped_reason = f"repeated finding after TeamNoT run: {selected.id}"
                coverage.stop_reason = stopped_reason
                coverage.replayed = True
                iteration_coverage.append(coverage)
                break
            previous = load_brief(config.previous_brief_path) if config.previous_brief_path else None
            generated = generate_followup_brief(report, selected, iteration_dir, previous)
            brief_path = write_generated_brief(iteration_dir, generated)
            stopped_reason = "generated follow-up brief"
            if not config.run_teamnot:
                coverage.stop_reason = stopped_reason
                iteration_coverage.append(coverage)
                break
            if self.run_teamnot_hook:
                try:
                    self.run_teamnot_hook(brief_path)
                except Exception as exc:
                    teamnot_invoked = True
                    stopped_reason = f"TeamNoT invocation failed: {exc}"
                    coverage.teamnot_invoked = True
                    coverage.stop_reason = stopped_reason
                    iteration_coverage.append(coverage)
                    break
            teamnot_invoked = True
            coverage.teamnot_invoked = True
            coverage.stop_reason = "TeamNoT invoked; retesting"
            iteration_coverage.append(coverage)
            last_invoked_finding_id = selected.id
            stopped_reason = "generated follow-up brief and invoked TeamNoT"
        else:
            stopped_reason = "max iterations reached"
            if iteration_coverage:
                iteration_coverage[-1].stop_reason = stopped_reason

        if report is None:
            raise RuntimeError("customer-loop did not execute any iterations")

        if config.max_iterations > 1 and generated:
            write_generated_brief(out_dir, generated)
        if config.max_iterations > 1:
            write_report_artifacts(out_dir, config.profile, plan, report)
        result = CustomerLoopResult(
            out_dir=out_dir,
            report=report,
            selected_finding=selected,
            generated_brief=generated,
            stopped_reason=stopped_reason,
            iterations_completed=iterations_completed,
            teamnot_invoked=teamnot_invoked,
            iteration_out_dirs=iteration_out_dirs,
            iteration_coverage=iteration_coverage,
        )
        write_loop_summary(result)
        return result

    def _runner(self, config: CustomerLoopConfig):
        if config.runner == CustomerLoopRunnerName.manual:
            if config.evidence_path is None:
                raise CustomerLoopRunnerError("--evidence is required for manual customer-loop mode")
            return ManualEvidenceRunner(config.evidence_path)
        if config.runner == CustomerLoopRunnerName.openclaw_windows_flow:
            if config.flow_path is None:
                raise CustomerLoopRunnerError("--flow is required for openclaw-windows-flow mode")
            return OpenClawWindowsFlowRunner(load_model(config.flow_path, CustomerFlowPack))
        if config.runner == CustomerLoopRunnerName.openclaw_windows_interactive:
            return OpenClawWindowsInteractiveRunner()
        if config.runner == CustomerLoopRunnerName.openclaw_windows_session:
            return OpenClawWindowsSessionRunner(file_fixture_path=config.file_fixture_path)
        if config.runner == CustomerLoopRunnerName.openclaw_windows_researcher:
            return OpenClawWindowsResearcherRunner(
                file_fixture_path=config.file_fixture_path,
                seeded_state_path=config.seeded_state_path,
                seeded_state=config.seeded_state,
            )
        return OpenClawWindowsCDPRunner()


def _attach_seeded_state_contract(report: CustomerReport, config: CustomerLoopConfig) -> None:
    if not config.seeded_state:
        return
    if report.seeded_state:
        return
    state = config.seeded_state.model_copy(deep=True)
    state.adapter_status = "unsupported"
    state.unsupported_blocker = (
        f"Runner {config.runner.value} recorded the seeded-state contract but does not apply "
        "cookies, localStorage, storageState, or login hints. Use openclaw-windows-researcher "
        "for adapter-level seeded-state application."
    )
    report.seeded_state = state


def _iteration_dir(out_dir: Path, iteration: int, max_iterations: int) -> Path:
    if max_iterations <= 1:
        return out_dir
    return out_dir / f"iteration-{iteration:02d}"


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
