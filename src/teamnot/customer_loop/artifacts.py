"""Artifact writing for customer-loop runs."""
from __future__ import annotations

from pathlib import Path

from teamnot.customer_loop.io import save_json, save_yaml
from teamnot.customer_loop.models import (
    CustomerLoopResult,
    CustomerProfile,
    CustomerReport,
    CustomerTestPlan,
    GeneratedBrief,
)


def ensure_artifact_dirs(out_dir: str | Path) -> Path:
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    (out / "screenshots").mkdir(exist_ok=True)
    return out


def render_customer_report(report: CustomerReport) -> str:
    lines = [
        f"# Customer report — {report.profile.persona}",
        "",
        f"Target: {report.target.url}",
        "",
        report.summary or "No summary provided.",
        "",
        "## Findings",
    ]
    if not report.findings:
        lines.append("No customer-impact findings were identified.")
    for finding in report.findings:
        lines.extend([
            "",
            f"### {finding.severity.value.upper()}: {finding.title}",
            "",
            f"- Customer interpretation: {finding.customer_interpretation or 'Not specified.'}",
            f"- Business impact: {finding.business_impact or 'Not specified.'}",
            f"- Likely frequency: {finding.likely_frequency or 'Not specified.'}",
            f"- Recommendation: {finding.recommendation or 'Not specified.'}",
            f"- Confidence: {finding.confidence:.2f}",
        ])
    return "\n".join(lines).strip() + "\n"


def render_loop_summary(result: CustomerLoopResult) -> str:
    selected = result.selected_finding
    lines = [
        "# Customer Loop Summary",
        "",
        f"Target: {result.report.target.url}",
        f"Iterations completed: {result.iterations_completed}",
        f"Stopped reason: {result.stopped_reason}",
        f"TeamNoT invoked: {'yes' if result.teamnot_invoked else 'no'}",
        "",
        "## Next Best Move",
    ]
    if selected:
        lines.extend([
            f"{selected.severity.value.upper()}: {selected.title}",
            "",
            selected.recommendation or "No recommendation provided.",
        ])
    else:
        lines.append("No finding met the configured severity threshold.")
    return "\n".join(lines).strip() + "\n"


def write_report_artifacts(
    out_dir: str | Path,
    profile: CustomerProfile,
    plan: CustomerTestPlan,
    report: CustomerReport,
) -> Path:
    out = ensure_artifact_dirs(out_dir)
    save_yaml(profile, out / "customer_profile.yaml")
    save_yaml(plan, out / "customer_test_plan.yaml")
    save_json(report, out / "customer_report.json")
    (out / "customer_report.md").write_text(render_customer_report(report), encoding="utf-8")
    return out


def write_generated_brief(out_dir: str | Path, generated: GeneratedBrief) -> Path:
    out = ensure_artifact_dirs(out_dir)
    path = save_yaml(generated.yaml, out / "generated_brief.yaml")
    generated.path = str(path)
    return path


def write_loop_summary(result: CustomerLoopResult) -> Path:
    out = ensure_artifact_dirs(result.out_dir)
    path = out / "loop_summary.md"
    path.write_text(render_loop_summary(result), encoding="utf-8")
    return path
