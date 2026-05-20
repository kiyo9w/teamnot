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
