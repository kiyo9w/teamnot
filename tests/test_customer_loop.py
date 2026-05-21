from __future__ import annotations

from pathlib import Path

from teamnot.brief import load_brief
from teamnot.customer_loop import (
    BrowserRuntimeMetadata,
    CustomerEvidence,
    CustomerJob,
    CustomerLoopConfig,
    CustomerLoopOrchestrator,
    CustomerProfile,
    CustomerReport,
    CustomerSeverity,
    CustomerTestPlan,
    DeterministicScreenshotReviewer,
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
        browser_runtime=BrowserRuntimeMetadata(
            cdp_url="http://127.0.0.1:18801",
            cdp_port=18801,
            session_id="teamnot-customer-test",
            page_url="https://example-product.test/app",
            screenshot_method="playwright-with-fallback",
        ),
        screenshot_captures=[capture],
        vision_review=VisionReviewArtifact(screenshot_count=1),
    )

    write_report_artifacts(tmp_path / "out", profile, plan, report)

    seeded_text = (tmp_path / "out" / "seeded_state_metadata.yaml").read_text(encoding="utf-8")
    report_text = (tmp_path / "out" / "customer_report.md").read_text(encoding="utf-8")
    assert "secret" not in seeded_text
    assert "***REDACTED***" in seeded_text
    assert "18801" in (tmp_path / "out" / "browser_runtime.yaml").read_text(encoding="utf-8")
    assert (tmp_path / "out" / "vision_review.yaml").exists()
    assert "Visual Evidence Review" in report_text
    assert "no model visual judgment" in report_text
    assert "CDP port: 18801" in report_text


def test_deterministic_screenshot_reviewer_groups_captures_and_reads_png_dimensions(tmp_path: Path):
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    before.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (390).to_bytes(4, "big") + (844).to_bytes(4, "big"))
    after.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (390).to_bytes(4, "big") + (844).to_bytes(4, "big"))

    review = DeterministicScreenshotReviewer().review([
        ScreenshotCaptureRecord(path=str(before), route="/reports", action="submit", success=True, sha256="before"),
        ScreenshotCaptureRecord(path=str(after), route="/reports", action="submit", success=True, sha256="after"),
    ])

    assert review.screenshot_count == 2
    assert review.review_kind == "heuristic"
    assert review.groups[0].group_id == "/reports:submit"
    assert review.groups[0].screenshots[0].width == 390
    assert review.groups[0].screenshots[0].height == 844
    assert "Hash changed within this screenshot group." in review.groups[0].notes
    assert "1 screenshot group(s) changed by hash across before/after captures." in review.heuristics
    assert review.judgment_summary.endswith("no model visual judgment was performed.")


def test_deterministic_screenshot_reviewer_surfaces_missing_and_blank_capture_blockers(tmp_path: Path):
    missing = tmp_path / "missing.png"

    review = DeterministicScreenshotReviewer().review([
        ScreenshotCaptureRecord(path=str(missing), route="/", action="observe", success=True),
        ScreenshotCaptureRecord(path="", route="/", action="blank", success=True, width=0, height=0),
    ])

    assert review.screenshot_count == 2
    assert review.review_kind == "heuristic"
    assert "2 screenshot capture(s) missing or failed." in review.blockers
    assert "1 screenshot capture(s) had zero dimensions." in review.blockers
    assert any("At least one capture failed." in group.notes for group in review.groups)
