from __future__ import annotations

from pathlib import Path

from teamnot.brief import load_brief
from teamnot.customer_loop import (
    CustomerLoopConfig,
    CustomerLoopOrchestrator,
    CustomerProfile,
    CustomerSeverity,
    ExperienceTarget,
    OpenClawWindowsCDPRunner,
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
