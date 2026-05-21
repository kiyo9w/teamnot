from __future__ import annotations

from pathlib import Path

from teamnot.brief import load_brief
from teamnot.customer_loop import (
    CustomerEvidence,
    CustomerJob,
    CustomerLoopConfig,
    CustomerLoopOrchestrator,
    CustomerProfile,
    CustomerReport,
    CustomerSeverity,
    CustomerTestPlan,
    ExperienceTarget,
    OpenClawWindowsCDPRunner,
    ScreenshotCaptureRecord,
    SeededCookie,
    SeededCustomerState,
    SeededTestAccount,
    VisionReviewArtifact,
    write_report_artifacts,
)


def test_customer_loop_ingests_markdown_heading_findings_from_manual_report(tmp_path: Path):
    profile = CustomerProfile(
        persona="Shopify agency operations lead",
        role="operations lead",
        trust_threshold="Must not produce confident reports from wrong input.",
    )
    evidence = tmp_path / "customer-report.md"
    evidence.write_text(
        "\n".join(
            [
                "# Customer Testing Report",
                "## Findings",
                "### Critical - Wrong file types can produce successful reports",
                "Customer impact: The operator may forward a misleading report.",
                "Business impact: Blocks trust in production usage.",
                "Recommended fix: Reject non-CSV uploads before analysis.",
                "Confidence: High.",
            ]
        ),
        encoding="utf-8",
    )

    result = CustomerLoopOrchestrator().run(
        CustomerLoopConfig(
            target=ExperienceTarget(url="https://example-product.test"),
            profile=profile,
            evidence_path=evidence,
            out_dir=tmp_path / "out",
            severity_threshold=CustomerSeverity.high,
        )
    )

    assert result.selected_finding is not None
    assert result.selected_finding.severity is CustomerSeverity.critical
    assert "Wrong file types" in result.selected_finding.title
    assert result.generated_brief is not None
    generated = load_brief(tmp_path / "out" / "generated_brief.yaml")
    assert "Reject non-CSV uploads" in generated.task.description


def test_openclaw_adapter_is_importable_without_browser_runtime():
    assert OpenClawWindowsCDPRunner.__name__ == "OpenClawWindowsCDPRunner"


def test_report_artifacts_write_redacted_seeded_state_and_vision_metadata(tmp_path: Path):
    profile = CustomerProfile(persona="Agency ops lead", role="operations")
    target = ExperienceTarget(url="https://example-product.test")
    plan = CustomerTestPlan(target=target, customer_job=CustomerJob(functional="Review generated report"))
    capture = ScreenshotCaptureRecord(path="screenshots/run.png", route="/reports", action="run", success=True)
    report = CustomerReport(
        profile=profile,
        target=target,
        plan=plan,
        evidence=[CustomerEvidence(kind="browser_research", screenshot_captures=[capture])],
        seeded_state=SeededCustomerState(
            cookies=[SeededCookie(name="session", value="secret", domain="example.test")],
            test_account=SeededTestAccount(email="customer@example.test", password="secret"),
            adapter_status="applied",
        ),
        screenshot_captures=[capture],
        vision_review=VisionReviewArtifact(screenshot_count=1),
    )

    write_report_artifacts(tmp_path / "out", profile, plan, report)

    seeded_text = (tmp_path / "out" / "seeded_state_metadata.yaml").read_text(encoding="utf-8")
    report_text = (tmp_path / "out" / "customer_report.md").read_text(encoding="utf-8")
    assert "secret" not in seeded_text
    assert "***REDACTED***" in seeded_text
    assert (tmp_path / "out" / "vision_review.yaml").exists()
    assert "Visual Evidence Review" in report_text
    assert "no model visual judgment" in report_text
