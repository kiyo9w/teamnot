"""Artifact writing for customer-loop runs."""
from __future__ import annotations

import re
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
        "## Research Lens",
        *_render_research_lens(report),
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
        "## Customer Journey Notes",
        *_render_customer_journey_notes(report),
        "",
        "## Route-By-Route Analysis",
        *_render_route_analysis(report),
        "",
        "## Product Exploration Map",
        *_render_product_exploration(report),
        "",
        "## Dimension Assessment",
        *_render_dimension_assessment(report),
        "",
        "## Researcher Observations",
        *_render_researcher_observations(report),
        "",
        "## Visual Evidence Review",
        *_render_visual_review(report),
        "",
        "## Persona And JTBD Panel",
        *_render_persona_jtbd(report),
        "",
        "## Domain Output Correctness",
        *_render_domain_oracles(report),
        "",
        "## Seeded State And Browser Runtime",
        *_render_seeded_runtime(report),
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
        "",
        "## Next Research Actions",
        *_render_next_research_actions(report),
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
        lines.append(f"- Evidence kind: {item.kind}")
        if item.observed_behavior:
            lines.append(f"  - Observed behavior: {item.observed_behavior}")
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


def _render_research_lens(report: CustomerReport) -> list[str]:
    profile = report.profile
    lens = [
        f"- Customer job being judged: {report.plan.customer_job.functional}",
        f"- User role lens: {profile.role}",
    ]
    if profile.current_workflow:
        lens.append(f"- Existing workflow to beat: {profile.current_workflow}")
    if profile.buying_trigger:
        lens.append(f"- Buying trigger: {profile.buying_trigger}")
    if profile.alternatives:
        lens.append(f"- Alternatives in the customer's head: {', '.join(profile.alternatives)}")
    if profile.trust_threshold:
        lens.append(f"- Trust threshold: {profile.trust_threshold}")
    if profile.buyer_user_split:
        lens.append(f"- Buyer/user split: {profile.buyer_user_split}")
    lens.extend(_research_gap_lens(report))
    return lens


def _research_gap_lens(report: CustomerReport) -> list[str]:
    gap_ids = {finding.id for finding in report.findings}
    gaps = []
    if "switching-forces-not-validated" in gap_ids:
        gaps.append("- Research gap: switching motivation, anxiety, and current-habit resistance still need a deeper pass.")
    if "buyer-user-fit-not-validated" in gap_ids:
        gaps.append("- Research gap: daily-user value and buyer/security approval are not yet separated enough.")
    if "trust-threshold-not-validated" in gap_ids:
        gaps.append("- Research gap: the stated trust threshold is visible as a concern but not proven end-to-end.")
    return gaps


def _render_customer_objections(report: CustomerReport) -> list[str]:
    objections = []
    raw = _raw_evidence(report)
    profile = report.profile
    if profile.trust_threshold:
        objections.append(f"- What proof satisfies this trust threshold: {profile.trust_threshold}?")
    if profile.alternatives:
        objections.append(f"- Why switch from {', '.join(profile.alternatives[:3])}?")
    if any(finding.trust_blocker for finding in report.findings):
        objections.append("- Can I trust this with real production data?")
    if any(finding.core_task_blocker for finding in report.findings):
        objections.append("- Can I complete the core workflow without expert help?")
    if "STEP_SKIP|jtbd-forces" in raw:
        objections.append("- What push, pull, anxiety, and existing habit would make this worth adopting now?")
    if "STEP_SKIP|buyer-user-mismatch" in raw:
        objections.append("- Does this satisfy both the daily user and the budget/security owner?")
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
    has_configured_flow = any(evidence.kind == "browser_flow" and "STEP_PASS|flow-" in evidence.raw_excerpt for evidence in report.evidence)
    if "STEP_SKIP|primary-workflow" in raw and not has_configured_flow:
        missing.append("- Full primary workflow execution needs manual evidence or a task-specific interactive runner.")
    if "STEP_SKIP|jtbd-forces" in raw:
        missing.append("- JTBD forces and buyer/user mismatch still need human/agent interpretation.")
    return missing or ["- No missing capability was detected by this baseline probe."]


def _render_next_iteration(report: CustomerReport) -> list[str]:
    blockers = [finding for finding in report.findings if finding.severity.value != "positive"]
    if not blockers:
        return ["- No customer-impact finding needs follow-up."]
    top = sorted(blockers, key=lambda finding: (
        _severity_rank(finding.severity.value),
        1 if finding.core_task_blocker else 0,
        1 if finding.trust_blocker else 0,
        finding.confidence,
    ), reverse=True)[0]
    return [f"- Fix `{top.id}`: {top.recommendation or top.title}"]


def _severity_rank(value: str) -> int:
    return {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
        "positive": 0,
    }.get(value, 0)


def _render_customer_journey_notes(report: CustomerReport) -> list[str]:
    markers = _markers(report)
    notes = []
    notes.append(_journey_note(markers, "first-impression", "First impression"))
    notes.append(_journey_note(markers, "customer-promise", "Need clarity"))
    notes.append(_journey_note(markers, "core-workflow-cues", "Activation path"))
    notes.append(_journey_note(markers, "primary-workflow", "Primary workflow"))
    notes.append(_journey_note(markers, "output-actionability", "Output interpretation"))
    notes.append(_journey_note(markers, "error-recovery", "Mistake recovery"))
    notes.append(_journey_note(markers, "trust-copy", "Trust and risk"))
    notes.append(_journey_note(markers, "mobile-review", "Mobile review"))
    return [note for note in notes if note]


def _render_route_analysis(report: CustomerReport) -> list[str]:
    flow_evidence = next((item for item in report.evidence if item.kind == "browser_flow"), None)
    if not flow_evidence:
        return ["- No configured multi-route flow evidence was captured."]
    flow_pack = flow_evidence.metadata.get("flow_pack", {}) if isinstance(flow_evidence.metadata, dict) else {}
    flows = flow_pack.get("flows", []) if isinstance(flow_pack, dict) else []
    results = flow_evidence.metadata.get("flows", []) if isinstance(flow_evidence.metadata, dict) else []
    failed_by_flow = {
        str(result.get("flow")) for result in results
        if isinstance(result, dict) and result.get("passed") is False
    }
    skipped_by_flow = {
        str(result.get("flow")) for result in results
        if isinstance(result, dict) and result.get("skipped")
    }
    executed_by_flow = {
        str(result.get("flow")) for result in results
        if isinstance(result, dict) and result.get("id")
    }
    lines = []
    for flow in flows:
        if not isinstance(flow, dict):
            continue
        name = str(flow.get("name", "Unnamed flow"))
        route = str(flow.get("start_url", "") or report.target.url)
        steps = flow.get("steps", [])
        executable = sum(
            1 for step in steps
            if isinstance(step, dict) and step.get("action") not in {"checkpoint"}
        )
        checkpoints = sum(
            1 for step in steps
            if isinstance(step, dict) and step.get("action") == "checkpoint"
        )
        if name in failed_by_flow:
            status = "failed"
        elif name in skipped_by_flow:
            status = "partially covered"
        elif name in executed_by_flow:
            status = "covered"
        else:
            status = "not executed"
        lines.append(
            f"- {name} (`{route}`): {status}; {executable} executable step(s), "
            f"{checkpoints} interpretation checkpoint(s)."
        )
    return lines or ["- Multi-route evidence existed but no flow metadata was available."]


def _render_product_exploration(report: CustomerReport) -> list[str]:
    exploration = _exploration_metadata(report)
    if not exploration:
        return ["- No product exploration planner artifact was attached to this report."]
    routes = exploration.get("routes", []) if isinstance(exploration.get("routes"), list) else []
    journeys = exploration.get("journeys", []) if isinstance(exploration.get("journeys"), list) else []
    gaps = exploration.get("coverage_gaps", []) if isinstance(exploration.get("coverage_gaps"), list) else []
    personas = exploration.get("personas", []) if isinstance(exploration.get("personas"), list) else []
    lines = []
    if personas:
        lines.append(f"- Personas/lenses: {', '.join(str(persona) for persona in personas)}")
    if routes:
        route_bits = []
        for route in routes[:10]:
            if not isinstance(route, dict):
                continue
            route_bits.append(
                f"{route.get('route')}[{route.get('kind')}, {route.get('coverage_status')}, p{route.get('priority')}]"
            )
        lines.append("- Route map: " + "; ".join(route_bits))
    for journey in journeys:
        if not isinstance(journey, dict):
            continue
        route_list = ", ".join(str(route) for route in journey.get("routes", [])[:4]) or "no mapped route"
        gap_list = "; ".join(str(gap) for gap in journey.get("gaps", [])[:3]) or "none"
        lines.append(
            f"- Journey `{journey.get('id')}`: {journey.get('coverage_status')} "
            f"via {route_list}; gaps: {gap_list}"
        )
    if gaps:
        lines.append("- Planner gaps: " + "; ".join(str(gap) for gap in gaps[:6]))
    return lines or ["- Product exploration metadata was present but empty."]


def _exploration_metadata(report: CustomerReport) -> dict:
    for evidence in report.evidence:
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        exploration = metadata.get("product_exploration")
        if isinstance(exploration, dict):
            return exploration
    return {}


def _render_dimension_assessment(report: CustomerReport) -> list[str]:
    markers = _markers(report)
    dimensions = [
        ("Need clarity", "customer-promise", report.scores.value),
        ("Problem-solution fit", "domain-fit", report.scores.domain_fit),
        ("Workflow fit", "primary-workflow", report.scores.task_success),
        ("Output usefulness", "output-actionability", report.scores.output_actionability),
        ("Error recovery", "error-recovery", report.scores.task_success),
        ("Trust and risk", "trust-copy", report.scores.trust_readiness),
        ("Commercial/adoption path", "adoption-readiness", report.scores.buying_readiness),
        ("Mobile and accessibility", "mobile-review", report.scores.usability),
        ("Reliability", "resource-health", report.scores.technical_reliability),
        ("Emotional confidence", "emotional-confidence", report.scores.emotional_confidence),
        ("Buyer/user fit", "buyer-user-mismatch", report.scores.buying_readiness),
    ]
    return [
        f"- {label}: {score}/10 — {_marker_status(markers, marker_id)}"
        for label, marker_id, score in dimensions
    ]


def _render_researcher_observations(report: CustomerReport) -> list[str]:
    markers = _markers(report)
    pass_markers = [marker for marker in markers if marker[0] == "STEP_PASS"]
    fail_markers = [marker for marker in markers if marker[0] == "STEP_FAIL"]
    skip_markers = [marker for marker in markers if marker[0] == "STEP_SKIP"]
    observations = []
    observations.extend(f"- Positive signal: {marker_id} — {detail}" for _, marker_id, detail in pass_markers[:8])
    observations.extend(f"- Risk signal: {marker_id} — {detail}" for _, marker_id, detail in fail_markers[:8])
    observations.extend(f"- Needs interpretation: {marker_id} — {detail}" for _, marker_id, detail in skip_markers[:8])
    return observations or ["- No structured researcher observations were captured."]


def _render_visual_review(report: CustomerReport) -> list[str]:
    review = report.vision_review
    if not review:
        return ["- No screenshot/vision artifact was attached to this report."]
    lines = [
        f"- Review kind: {review.review_kind}",
        f"- Evidence source: {review.evidence_source}",
        f"- Screenshot captures: {review.screenshot_count}",
        f"- Judgment boundary: {review.judgment_summary}",
    ]
    lines.extend(f"- Heuristic: {item}" for item in review.heuristics)
    lines.extend(f"- Blocker: {item}" for item in review.blockers)
    lines.extend(
        f"- Visual finding: {finding.severity.value.upper()} — {finding.title}: "
        f"{finding.customer_interpretation or finding.recommendation}"
        for finding in review.visual_findings
    )
    lines.extend(f"- Vision action hint: {item}" for item in review.action_hints)
    return lines


def _render_persona_jtbd(report: CustomerReport) -> list[str]:
    lines: list[str] = []
    if report.persona_lenses:
        for lens in report.persona_lenses:
            blocker = "; ".join(lens.blockers[:3]) or "none detected by deterministic panel"
            conflicts = "; ".join(lens.conflicts[:2]) or "none"
            lines.append(f"- {lens.lens} ({lens.role}): blockers: {blocker}; conflicts: {conflicts}.")
    else:
        lines.append("- No multi-persona panel artifact was attached.")
    if report.jtbd_forces:
        forces = report.jtbd_forces
        lines.extend([
            f"- Push: {forces.push}",
            f"- Pull: {forces.pull}",
            f"- Anxiety: {forces.anxiety}",
            f"- Habit: {forces.habit}",
            f"- Trigger: {forces.trigger}",
            f"- Success metric: {forces.success_metric}",
        ])
    else:
        lines.append("- JTBD forces were not synthesized for this run.")
    return lines


def _render_domain_oracles(report: CustomerReport) -> list[str]:
    if not report.domain_oracles:
        return ["- No domain-output oracle artifact was attached."]
    return [
        f"- {oracle.name}: {oracle.coverage_status}; "
        f"{oracle.semantic_rubric or oracle.manual_checkpoint or oracle.expected_output or oracle.notes or 'No rubric detail.'}"
        for oracle in report.domain_oracles
    ]


def _render_seeded_runtime(report: CustomerReport) -> list[str]:
    lines: list[str] = []
    if report.seeded_state:
        state = report.seeded_state
        lines.append(f"- Seeded state status: {state.adapter_status}")
        if state.unsupported_blocker:
            lines.append(f"- Seeded state blocker: {state.unsupported_blocker}")
        if state.cleanup_notes:
            lines.append(f"- Cleanup notes: {state.cleanup_notes}")
        if state.reset_notes:
            lines.append(f"- Reset notes: {state.reset_notes}")
    else:
        lines.append("- Seeded state: none provided.")
    if report.browser_runtime:
        runtime = report.browser_runtime
        lines.extend([
            f"- CDP URL: {runtime.cdp_url or 'not reported'}",
            f"- CDP port: {runtime.cdp_port if runtime.cdp_port is not None else 'not reported'}",
            f"- Session id: {runtime.session_id or 'not reported'}",
            f"- Page URL: {runtime.page_url or 'not reported'}",
            f"- Screenshot method: {runtime.screenshot_method or 'not reported'}",
        ])
        if runtime.failed_primitive:
            lines.append(f"- Failed primitive: {runtime.failed_primitive}")
        if runtime.adapter_blocker:
            lines.append(f"- Adapter blocker: {runtime.adapter_blocker}")
    else:
        lines.append("- Browser runtime metadata: not reported.")
    return lines


def _render_next_research_actions(report: CustomerReport) -> list[str]:
    actions = []
    raw = _raw_evidence(report)
    if "STEP_SKIP|jtbd-forces" in raw:
        actions.append("- Run a JTBD pass: push, pull, anxiety, habit, trigger, and success metric.")
    if "STEP_SKIP|buyer-user-mismatch" in raw:
        actions.append("- Add a buyer/security/manager persona and compare objections against the daily user.")
    exploration = _exploration_metadata(report)
    if exploration:
        gaps = exploration.get("coverage_gaps", []) if isinstance(exploration.get("coverage_gaps"), list) else []
        if any("Auth/account state" in str(gap) for gap in gaps):
            actions.append("- Provide a seeded test account, cleanup policy, and state reset plan for authenticated journeys.")
        if any("multi-persona" in str(gap).lower() for gap in gaps):
            actions.append("- Run a multi-persona panel and compare daily-user, buyer, and security objections.")
        if any("domain fixtures" in str(gap).lower() for gap in gaps):
            actions.append("- Add domain fixtures or oracle checks before claiming output correctness.")
    if "STEP_FAIL|mobile-review" in raw:
        actions.append("- Re-run the highest-value flow on phone width after fixing mobile layout offenders.")
    if "STEP_FAIL|error-recovery" in raw:
        actions.append("- Exercise a realistic invalid-input or failed-action path instead of only checking recovery copy.")
    if any(evidence.kind == "browser_flow" for evidence in report.evidence):
        actions.append("- Deepen the auto-discovered route map with auth/stateful screens if a test account is available.")
    return actions or ["- No additional research action was triggered by this run."]


def _markers(report: CustomerReport) -> list[tuple[str, str, str]]:
    markers: list[tuple[str, str, str]] = []
    for line in _raw_evidence(report).splitlines():
        match = re.match(r"^(STEP_(?:PASS|FAIL|SKIP))\|([^|]+)\|(.*)$", line.strip())
        if match:
            markers.append((match.group(1), match.group(2), match.group(3)))
    return markers


def _raw_evidence(report: CustomerReport) -> str:
    return "\n".join(evidence.raw_excerpt for evidence in report.evidence)


def _journey_note(markers: list[tuple[str, str, str]], marker_id: str, label: str) -> str:
    status = _marker_status(markers, marker_id)
    return f"- {label}: {status}" if status != "not covered by this run" else ""


def _marker_status(markers: list[tuple[str, str, str]], marker_id: str) -> str:
    for status, current_id, detail in markers:
        if current_id == marker_id or current_id.endswith(f"-{marker_id}") or marker_id in current_id:
            label = {
                "STEP_PASS": "passed",
                "STEP_FAIL": "failed",
                "STEP_SKIP": "needs interpretation",
            }.get(status, status.lower())
            return f"{label}: {detail}"
    return "not covered by this run"


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
    if result.iteration_coverage:
        lines.extend(["", "## Iteration Coverage"])
        for coverage in result.iteration_coverage:
            lines.append(
                f"- Iteration {coverage.iteration}: "
                f"new_evidence={coverage.new_evidence}, replayed={coverage.replayed}, "
                f"selected={coverage.selected_finding_id or 'none'}, stop={coverage.stop_reason or 'n/a'}"
            )
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
    if report.seeded_state:
        save_yaml(report.seeded_state.redacted(), out / "seeded_state_metadata.yaml")
    if report.browser_runtime:
        save_yaml(report.browser_runtime, out / "browser_runtime.yaml")
    if report.screenshot_captures:
        save_yaml({"captures": [record.model_dump(mode="json") for record in report.screenshot_captures]}, out / "screenshot_captures.yaml")
    if report.vision_review:
        save_yaml(report.vision_review, out / "vision_review.yaml")
    if report.persona_lenses or report.jtbd_forces:
        save_yaml(
            {
                "persona_lenses": [lens.model_dump(mode="json") for lens in report.persona_lenses],
                "jtbd_forces": report.jtbd_forces.model_dump(mode="json") if report.jtbd_forces else None,
            },
            out / "persona_jtbd_panel.yaml",
        )
    if report.domain_oracles:
        save_yaml({"oracles": [oracle.model_dump(mode="json") for oracle in report.domain_oracles]}, out / "domain_oracles.yaml")
    if report.action_memory:
        save_yaml({"action_memory": [item.model_dump(mode="json") for item in report.action_memory]}, out / "research_action_memory.yaml")
    (out / "customer_report.md").write_text(render_customer_report(report), encoding="utf-8")
    return out


def write_generated_brief(out_dir: str | Path, generated: GeneratedBrief) -> Path:
    out = ensure_artifact_dirs(out_dir)
    path = save_yaml(generated.yaml, out / "generated_brief.yaml")
    generated.path = str(path)
    return path


def write_loop_summary(result: CustomerLoopResult) -> Path:
    out = ensure_artifact_dirs(result.out_dir)
    if result.iteration_coverage:
        save_yaml(
            {"iterations": [coverage.model_dump(mode="json") for coverage in result.iteration_coverage]},
            out / "iteration_coverage.yaml",
        )
    path = out / "loop_summary.md"
    path.write_text(render_loop_summary(result), encoding="utf-8")
    return path
