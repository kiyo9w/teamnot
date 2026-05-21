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
            _render_field("Customer interpretation", finding.customer_interpretation),
            _render_field("Business impact", finding.business_impact),
            _render_field("Likely frequency", finding.likely_frequency),
            _render_field("Recommendation", finding.recommendation),
            f"- Confidence: {finding.confidence:.2f}",
        ])
    lines.extend([
        "",
        "## Scores",
        f"- Job importance: {report.scores.job_importance}/10",
        f"- Customer value: {report.scores.value}/10",
        f"- Time-to-value: {report.scores.time_to_value}/10",
        f"- Task success: {report.scores.task_success}/10",
        f"- Usability: {report.scores.usability}/10",
        f"- Trust/readiness: {report.scores.trust_readiness}/10",
        f"- Output actionability: {report.scores.output_actionability}/10",
        f"- Domain fit: {report.scores.domain_fit}/10",
        f"- Buying readiness: {report.scores.buying_readiness}/10",
        f"- Retention likelihood: {report.scores.retention_likelihood}/10",
        f"- Emotional confidence: {report.scores.emotional_confidence}/10",
        f"- Technical reliability: {report.scores.technical_reliability}/10",
        "",
        "## Evidence",
    ])
    if not report.evidence:
        lines.append("No evidence captured.")
    for item in report.evidence:
        if item.observed_behavior:
            lines.append(f"- {item.observed_behavior}")
        for screenshot in item.screenshot_paths:
            lines.append(f"  - Screenshot: {screenshot}")
        if item.raw_excerpt:
            lines.extend(["", "```text", item.raw_excerpt.strip(), "```"])
    return "\n".join(lines).strip() + "\n"


def _render_field(label: str, value: str) -> str:
    value = value.strip() if value else "Not specified."
    if "\n" not in value:
        return f"- {label}: {value}"
    indented = "\n".join(f"  {line}" if line else "" for line in value.splitlines())
    return f"- {label}:\n{indented}"


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
