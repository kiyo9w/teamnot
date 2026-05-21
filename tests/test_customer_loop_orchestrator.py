from __future__ import annotations

from pathlib import Path

from teamnot.brief import load_brief
from teamnot.customer_loop import (
    CustomerEvidence,
    CustomerFinding,
    CustomerLoopConfig,
    CustomerLoopOrchestrator,
    CustomerProfile,
    CustomerReport,
    CustomerSeverity,
    CustomerTestPlan,
    ExperienceTarget,
    select_next_best_move,
)
from teamnot.customer_loop.orchestrator import default_customer_test_plan
from teamnot.customer_loop.runners import ExperienceRunner


def _profile() -> CustomerProfile:
    return CustomerProfile(persona="Agency ops lead", role="operations")


def _report(findings: list[CustomerFinding]) -> CustomerReport:
    target = ExperienceTarget(url="https://example-product.test")
    config = CustomerLoopConfig(target=target, profile=_profile(), out_dir=Path("."))
    plan: CustomerTestPlan = default_customer_test_plan(config)
    return CustomerReport(profile=_profile(), target=target, plan=plan, findings=findings)


def test_critical_finding_outranks_high_trust_blocker():
    high = CustomerFinding(
        id="high",
        title="High trust blocker",
        severity=CustomerSeverity.high,
        trust_blocker=True,
    )
    critical = CustomerFinding(id="critical", title="Critical task failure", severity="critical")
    assert select_next_best_move(_report([high, critical])).id == "critical"


def test_trust_and_core_blockers_outrank_polish_at_same_severity():
    polish = CustomerFinding(id="polish", title="Polish issue", severity="high", confidence=1.0)
    core = CustomerFinding(
        id="core",
        title="Cannot complete workflow",
        severity="high",
        core_task_blocker=True,
        confidence=0.4,
    )
    assert select_next_best_move(_report([polish, core])).id == "core"


def test_severity_threshold_can_stop_generation(tmp_path: Path):
    evidence = tmp_path / "evidence.md"
    evidence.write_text("Title: Minor wording issue\nSeverity: medium\n", encoding="utf-8")
    config = CustomerLoopConfig(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=_profile(),
        out_dir=tmp_path / "out",
        evidence_path=evidence,
        severity_threshold=CustomerSeverity.high,
    )
    result = CustomerLoopOrchestrator().run(config)
    assert result.generated_brief is None
    assert not (tmp_path / "out" / "generated_brief.yaml").exists()
    assert (tmp_path / "out" / "loop_summary.md").exists()


def test_orchestrator_generates_valid_teamnot_brief(tmp_path: Path):
    evidence = tmp_path / "evidence.md"
    evidence.write_text(
        "\n".join([
            "Title: Blank report preview after success",
            "Severity: high",
            "Customer interpretation: The output is not trustworthy.",
            "Business impact: Agency lead cannot use it with clients.",
            "Likely frequency: Every completed upload.",
            "Recommendation: Render the report preview after successful analysis.",
        ]),
        encoding="utf-8",
    )
    config = CustomerLoopConfig(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=_profile(),
        out_dir=tmp_path / "out",
        evidence_path=evidence,
    )
    result = CustomerLoopOrchestrator().run(config)
    brief_path = tmp_path / "out" / "generated_brief.yaml"
    assert result.generated_brief is not None
    loaded = load_brief(brief_path)
    assert "Blank report preview" in loaded.task.title
    assert "Required behavior" in loaded.task.description
    assert loaded.task.constraints.no_deploy is True
    assert loaded.budget.allowed_metered_workers == []


def test_run_teamnot_is_safe_default(tmp_path: Path):
    evidence = tmp_path / "evidence.md"
    evidence.write_text("Title: Trust blocker\nSeverity: critical\n", encoding="utf-8")
    called = {"value": False}

    def hook(path: Path) -> None:
        called["value"] = True

    config = CustomerLoopConfig(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=_profile(),
        out_dir=tmp_path / "out",
        evidence_path=evidence,
        run_teamnot=False,
    )
    result = CustomerLoopOrchestrator(run_teamnot_hook=hook).run(config)
    assert result.teamnot_invoked is False
    assert called["value"] is False


def test_generated_report_preserves_evidence_reference():
    finding = CustomerFinding(
        id="f1",
        title="Wrong file accepted",
        severity="critical",
        evidence=[CustomerEvidence(path=".teamnot/customer-testing/report.md")],
    )
    assert ".teamnot/customer-testing/report.md" in finding.evidence[0].path


def test_generated_brief_sanitizes_finding_id_for_git_branch(tmp_path: Path):
    evidence = tmp_path / "evidence.md"
    evidence.write_text("Title: Bad branch chars\nSeverity: critical\n", encoding="utf-8")
    config = CustomerLoopConfig(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=_profile(),
        out_dir=tmp_path / "out",
        evidence_path=evidence,
    )
    result = CustomerLoopOrchestrator().run(config)
    assert result.generated_brief is not None

    result.report.findings[0].id = "bad/id .. lock"
    from teamnot.customer_loop.brief_generation import generate_followup_brief

    generated = generate_followup_brief(result.report, result.report.findings[0], tmp_path / "out")
    assert generated.task_id == "CUSTOMER-LOOP-BAD-ID-LOCK"
    assert generated.yaml["deliverable"]["branch"] == "feature/customer-loop-bad-id-lock"


class _SequenceRunner(ExperienceRunner):
    def __init__(self, reports: list[CustomerReport]):
        self.reports = reports
        self.out_dirs: list[Path] = []

    def run(self, target, profile, plan, out_dir):
        self.out_dirs.append(out_dir)
        index = min(len(self.out_dirs) - 1, len(self.reports) - 1)
        return self.reports[index]


class _LoopOrchestrator(CustomerLoopOrchestrator):
    def __init__(self, runner: ExperienceRunner, run_teamnot_hook=None):
        super().__init__(run_teamnot_hook=run_teamnot_hook)
        self.runner = runner

    def _runner(self, config):
        return self.runner


def test_orchestrator_can_invoke_teamnot_and_retest_until_clean(tmp_path: Path):
    finding = CustomerFinding(
        id="accessibility",
        title="Interactive controls lack accessible names",
        severity=CustomerSeverity.medium,
        recommendation="Add accessible names.",
    )
    dirty = _report([finding])
    clean = _report([])
    runner = _SequenceRunner([dirty, clean])
    invoked: list[Path] = []

    config = CustomerLoopConfig(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=_profile(),
        out_dir=tmp_path / "out",
        max_iterations=3,
        severity_threshold=CustomerSeverity.medium,
        run_teamnot=True,
    )
    result = _LoopOrchestrator(runner, run_teamnot_hook=invoked.append).run(config)

    assert result.iterations_completed == 2
    assert result.selected_finding is None
    assert result.teamnot_invoked is True
    assert result.stopped_reason == "no finding met severity threshold"
    assert len(invoked) == 1
    assert invoked[0].name == "generated_brief.yaml"
    assert [path.name for path in result.iteration_out_dirs] == ["iteration-01", "iteration-02"]
    assert (tmp_path / "out" / "iteration-01" / "generated_brief.yaml").exists()
    assert (tmp_path / "out" / "iteration-02" / "customer_report.md").exists()
    assert "iteration-01" in (tmp_path / "out" / "loop_summary.md").read_text(encoding="utf-8")


def test_orchestrator_stops_at_max_iterations_when_findings_remain(tmp_path: Path):
    finding = CustomerFinding(id="blocking", title="Still blocked", severity=CustomerSeverity.high)
    runner = _SequenceRunner([_report([finding]), _report([finding])])

    config = CustomerLoopConfig(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=_profile(),
        out_dir=tmp_path / "out",
        max_iterations=2,
        severity_threshold=CustomerSeverity.medium,
        run_teamnot=True,
    )
    result = _LoopOrchestrator(runner, run_teamnot_hook=lambda path: None).run(config)

    assert result.iterations_completed == 2
    assert result.selected_finding is not None
    assert result.stopped_reason == "repeated finding after TeamNoT run: blocking"


def test_orchestrator_retries_different_findings_until_max_iterations(tmp_path: Path):
    first = CustomerFinding(id="first", title="First issue", severity=CustomerSeverity.high)
    second = CustomerFinding(id="second", title="Second issue", severity=CustomerSeverity.high)
    runner = _SequenceRunner([_report([first]), _report([second])])

    config = CustomerLoopConfig(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=_profile(),
        out_dir=tmp_path / "out",
        max_iterations=2,
        severity_threshold=CustomerSeverity.medium,
        run_teamnot=True,
    )
    result = _LoopOrchestrator(runner, run_teamnot_hook=lambda path: None).run(config)

    assert result.iterations_completed == 2
    assert result.selected_finding is not None
    assert result.stopped_reason == "max iterations reached"


def test_orchestrator_writes_summary_when_teamnot_invocation_fails(tmp_path: Path):
    finding = CustomerFinding(id="blocking", title="Still blocked", severity=CustomerSeverity.high)
    runner = _SequenceRunner([_report([finding])])

    def fail(_path: Path) -> None:
        raise RuntimeError("workspace locked")

    config = CustomerLoopConfig(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=_profile(),
        out_dir=tmp_path / "out",
        max_iterations=3,
        severity_threshold=CustomerSeverity.medium,
        run_teamnot=True,
    )
    result = _LoopOrchestrator(runner, run_teamnot_hook=fail).run(config)

    assert result.iterations_completed == 1
    assert result.teamnot_invoked is True
    assert result.stopped_reason == "TeamNoT invocation failed: workspace locked"
    assert "workspace locked" in (tmp_path / "out" / "loop_summary.md").read_text(encoding="utf-8")


def test_generated_followup_brief_requires_source_change(tmp_path: Path):
    evidence = tmp_path / "evidence.md"
    evidence.write_text("Title: Missing trust copy\nSeverity: high\n", encoding="utf-8")
    config = CustomerLoopConfig(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=_profile(),
        out_dir=tmp_path / "out",
        evidence_path=evidence,
    )

    result = CustomerLoopOrchestrator().run(config)
    assert result.generated_brief is not None
    loaded = load_brief(tmp_path / "out" / "generated_brief.yaml")
    check_names = {check.name for check in loaded.definition_of_done.checks}

    assert "source change exists outside TeamNoT artifacts" in check_names
    assert str((tmp_path / "out" / "customer_report.json").resolve()) in {
        check.file_exists for check in loaded.definition_of_done.checks
    }
