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
        "## Method",
        *_render_method(report),
        "",
        "## Persona Tested",
        *_render_persona(report.profile),
        "",
        "## Test Plan",
        *_render_test_plan(report.plan),
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
        "## Customer Objections",
        *_render_customer_objections(report),
        "",
        "## What Works Well",
        *_render_positives(report),
        "",
        "## Missing Capabilities For Real Adoption",
        *_render_missing_capabilities(report),
        "",
        "## Recommended Next Iteration",
        *_render_next_iteration(report),
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


def _render_method(report: CustomerReport) -> list[str]:
    if not report.evidence:
        return ["- Evidence mode: none captured."]
    primary = report.evidence[0]
    metadata = primary.metadata or {}
    probe = metadata.get("probe", {}) if isinstance(metadata.get("probe"), dict) else {}
    mobile = probe.get("mobileProbe", {}) if isinstance(probe.get("mobileProbe"), dict) else {}
    viewport = probe.get("viewport", {})
    mobile_viewport = mobile.get("viewport", {})
    lines = [
        f"- Runner: {metadata.get('runner', primary.kind) or primary.kind}",
        f"- Rubric: {metadata.get('rubric', 'manual evidence')}",
        f"- Method: {metadata.get('method', primary.kind)}",
        f"- Target: {report.target.url}",
    ]
    if viewport:
        lines.append(f"- Desktop viewport: {viewport}")
    if mobile_viewport:
        lines.append(f"- Mobile viewport: {mobile_viewport}")
    if probe.get("timingMs") is not None:
        lines.append(f"- Navigation timing: {probe.get('timingMs')} ms")
    return lines


def _render_persona(profile: CustomerProfile) -> list[str]:
    lines = [
        f"- Persona: {profile.persona}",
        f"- Role: {profile.role}",
    ]
    optional = [
        ("Seniority", profile.seniority),
        ("Domain literacy", profile.domain_literacy),
        ("Current workflow", profile.current_workflow),
        ("Buying trigger", profile.buying_trigger),
        ("Buyer/user split", profile.buyer_user_split),
        ("Trust threshold", profile.trust_threshold),
    ]
    for label, value in optional:
        if value:
            lines.append(f"- {label}: {value}")
    if profile.alternatives:
        lines.append(f"- Alternatives: {', '.join(profile.alternatives)}")
    return lines


def _render_test_plan(plan: CustomerTestPlan) -> list[str]:
    lines = [
        f"- Functional job: {plan.customer_job.functional}",
        f"- Emotional job: {plan.customer_job.emotional or 'Not specified.'}",
        f"- Social job: {plan.customer_job.social or 'Not specified.'}",
    ]
    for task in plan.tasks:
        lines.append(f"- {task.id}: {task.title}")
    return lines


def _render_customer_objections(report: CustomerReport) -> list[str]:
    objections = []
    if any(finding.trust_blocker for finding in report.findings):
        objections.append("- Can I trust this with real production data?")
    if any(finding.core_task_blocker for finding in report.findings):
        objections.append("- Can I complete the core workflow without expert help?")
    if report.scores.buying_readiness < 7:
        objections.append("- What is the pilot, support, or buying path?")
    if report.scores.output_actionability < 7:
        objections.append("- Can I explain or share the result with my team or client?")
    return objections or ["- No major customer objections were detected by this baseline probe."]


def _render_positives(report: CustomerReport) -> list[str]:
    positives = []
    if report.scores.trust_readiness >= 7:
        positives.append("- Trust/readiness cues are visible enough for an initial evaluation.")
    if report.scores.time_to_value >= 7:
        positives.append("- The page exposes a reasonably quick path toward first value.")
    if report.scores.output_actionability >= 7:
        positives.append("- The promised output appears actionable enough to evaluate further.")
    if report.scores.technical_reliability >= 7:
        positives.append("- No major baseline reliability issue was detected.")
    return positives or ["- No strong positive signal was detected by this baseline probe."]


def _render_missing_capabilities(report: CustomerReport) -> list[str]:
    missing = []
    if report.findings:
        for finding in report.findings:
            missing.append(f"- {finding.severity.value.upper()}: {finding.title}")
    raw = "\n".join(evidence.raw_excerpt for evidence in report.evidence)
    if "STEP_SKIP|primary-workflow" in raw:
        missing.append("- Full primary workflow execution needs manual evidence or a task-specific interactive runner.")
    if "STEP_SKIP|jtbd-forces" in raw:
        missing.append("- JTBD forces and buyer/user mismatch still need human/agent interpretation.")
    return missing or ["- No missing capability was detected by this baseline probe."]


def _render_next_iteration(report: CustomerReport) -> list[str]:
    blockers = [finding for finding in report.findings if finding.severity.value != "positive"]
    if not blockers:
        return ["- No customer-impact finding needs follow-up."]
    top = sorted(blockers, key=lambda finding: (
        1 if finding.trust_blocker else 0,
        1 if finding.core_task_blocker else 0,
        finding.confidence,
    ), reverse=True)[0]
    return [f"- Fix `{top.id}`: {top.recommendation or top.title}"]


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
    ]
    if result.iteration_out_dirs:
        lines.extend([
            "",
            "## Iteration Artifacts",
            *[f"- {path}" for path in result.iteration_out_dirs],
        ])
    lines.extend([
        "",
        "## Next Best Move",
    ])
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
