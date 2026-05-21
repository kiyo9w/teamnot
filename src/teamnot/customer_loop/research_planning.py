"""Deterministic planner helpers for autonomous customer research."""
from __future__ import annotations

from collections.abc import Iterable

from teamnot.customer_loop.models import (
    CustomerReport,
    CustomerSeverity,
    DomainOutputOracle,
    IterationCoverage,
    JTBDForces,
    PersonaLensResult,
    ResearchActionMemory,
)

PRODUCT_TERMS = (
    "run", "start", "create", "submit", "generate", "analyze", "analyse",
    "upload", "invite", "settings", "billing", "dashboard", "report", "save",
)
LOW_VALUE_TERMS = (
    "footer", "privacy", "terms", "docs", "blog", "github", "menu",
    "navigation", "devtools", "home",
)


def rank_customer_actions(actions: Iterable[dict]) -> list[dict]:
    return sorted(actions, key=customer_action_score, reverse=True)


def customer_action_score(action: dict) -> tuple[int, int, str]:
    text = " ".join(str(action.get(key, "")) for key in ("text", "selector", "id", "kind")).lower()
    product = sum(1 for term in PRODUCT_TERMS if term in text)
    low_value = sum(1 for term in LOW_VALUE_TERMS if term in text)
    main = 1 if action.get("inMain") or action.get("kind") in {"filled_submit", "empty_submit"} else 0
    return (product * 3 + main - low_value * 4, len(text), text)


def suppress_repeated_noops(
    route: str,
    actions: list[dict],
    memory: list[ResearchActionMemory],
) -> list[dict]:
    noops = {
        item.chosen_action for item in memory
        if item.route == route and (item.no_op or item.repeated)
    }
    filtered = [
        action for action in actions
        if str(action.get("id") or action.get("text") or action.get("selector")) not in noops
    ]
    return filtered or actions[:1]


def action_memory_from_result(
    route: str,
    observation: str,
    action: dict,
    result: dict,
    repeated: bool = False,
) -> ResearchActionMemory:
    action_id = str(action.get("id") or action.get("text") or action.get("selector") or action.get("kind") or "action")
    changed = bool(
        result.get("url_changed")
        or result.get("text_changed")
        or result.get("visual_changed")
        or result.get("urlChanged")
        or result.get("textChanged")
    )
    return ResearchActionMemory(
        route=route,
        observation=observation[:500],
        chosen_action=action_id,
        reason=str(action.get("reason") or action.get("goal") or "Highest-ranked customer action."),
        expected_signal=str(action.get("expected_signal") or "URL, text, screenshot hash, or workflow state changes."),
        result=str(result.get("summary") or result.get("result") or ("changed" if changed else "no observable change"))[:500],
        comparison=(
            f"url_changed={bool(result.get('url_changed'))}; "
            f"text_changed={bool(result.get('text_changed'))}; "
            f"visual_changed={bool(result.get('visual_changed'))}"
        ),
        learned_signal="observable change" if changed else "no new signal",
        repeated=repeated,
        no_op=not changed,
    )


def synthesize_persona_panel(report: CustomerReport) -> list[PersonaLensResult]:
    profile = report.profile
    blockers = [finding.title for finding in report.findings if finding.severity != CustomerSeverity.positive]
    positive = [finding.title for finding in report.findings if finding.severity == CustomerSeverity.positive]
    lenses = [
        PersonaLensResult(
            lens="daily_user",
            role=profile.role or profile.persona,
            positive_signals=positive[:3],
            blockers=[item for item in blockers if "workflow" in item.lower() or "form" in item.lower()][:3] or blockers[:2],
        ),
        PersonaLensResult(
            lens="buyer_manager",
            role="budget owner / manager",
            blockers=[item for item in blockers if "trust" in item.lower() or "pricing" in item.lower()][:3] or blockers[:2],
        ),
        PersonaLensResult(
            lens="security_platform",
            role="security / platform reviewer",
            blockers=[item for item in blockers if "auth" in item.lower() or "state" in item.lower() or "trust" in item.lower()][:3],
        ),
        PersonaLensResult(
            lens="finance_procurement",
            role="finance / procurement",
            blockers=[item for item in blockers if "billing" in item.lower() or "pricing" in item.lower()][:3],
        ),
    ]
    if lenses[0].blockers and (lenses[1].blockers or lenses[2].blockers):
        conflict = "Daily-user workflow value is not enough while buyer/security blockers remain."
        lenses[0].conflicts.append(conflict)
        lenses[1].conflicts.append(conflict)
    return lenses


def synthesize_jtbd_forces(report: CustomerReport) -> JTBDForces:
    profile = report.profile
    return JTBDForces(
        push=profile.current_workflow or "Current workflow pain was not deeply validated.",
        pull=report.plan.customer_job.functional,
        anxiety=profile.trust_threshold or "Trust and adoption anxiety requires more evidence.",
        habit=", ".join(profile.alternatives) if profile.alternatives else "Existing habit/alternative not specified.",
        trigger=profile.buying_trigger or "Buying trigger not specified.",
        success_metric="Customer reaches useful output and can justify it to a teammate or buyer.",
    )


def evaluate_domain_oracles(report: CustomerReport, oracles: list[DomainOutputOracle]) -> list[DomainOutputOracle]:
    if not oracles:
        return [
            DomainOutputOracle(
                name="No domain oracle configured",
                coverage_status="coverage_gap",
                manual_checkpoint="Add expected output, golden file, API check, semantic rubric, or manual checkpoint.",
                notes="Generic UI evidence cannot prove domain-output correctness.",
            )
        ]
    raw = "\n".join(evidence.raw_excerpt for evidence in report.evidence).lower()
    evaluated: list[DomainOutputOracle] = []
    for oracle in oracles:
        expected = oracle.expected_output.lower().strip()
        status = "pass" if expected and expected in raw else "manual_checkpoint"
        if oracle.golden_file or oracle.api_check or oracle.semantic_rubric or oracle.manual_checkpoint:
            status = "manual_checkpoint" if status != "pass" else status
        evaluated.append(oracle.model_copy(update={"coverage_status": status}))
    return evaluated


def compare_iteration_coverage(
    iteration: int,
    report: CustomerReport,
    previous: IterationCoverage | list[IterationCoverage] | None = None,
) -> IterationCoverage:
    routes = _routes(report)
    actions = _actions(report)
    screenshots = [shot for evidence in report.evidence for shot in evidence.screenshot_paths]
    findings = [finding.id for finding in report.findings]
    previous_items = previous if isinstance(previous, list) else ([previous] if previous else [])
    previous_routes = {route for item in previous_items for route in item.new_routes}
    previous_actions = {action for item in previous_items for action in item.new_actions}
    previous_screenshots = {shot for item in previous_items for shot in item.new_screenshots}
    previous_findings = {finding for item in previous_items for finding in item.new_findings}
    new_routes = [route for route in routes if route not in previous_routes]
    new_actions = [action for action in actions if action not in previous_actions]
    new_screenshots = [shot for shot in screenshots if shot not in previous_screenshots]
    new_findings = [finding for finding in findings if finding not in previous_findings]
    new_evidence = bool(new_routes or new_actions or new_screenshots or new_findings)
    return IterationCoverage(
        iteration=iteration,
        selected_finding_id=findings[0] if findings else None,
        new_routes=new_routes,
        new_actions=new_actions,
        new_screenshots=new_screenshots,
        new_findings=new_findings,
        replayed=not new_evidence and bool(previous_items),
        new_evidence=new_evidence,
    )


def _routes(report: CustomerReport) -> list[str]:
    routes: list[str] = []
    for evidence in report.evidence:
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        for key in ("screen_exploration", "research_brain"):
            value = metadata.get(key, {})
            if isinstance(value, dict):
                routes.extend(str(route) for route in value.get("routes_discovered", []) if route)
    return list(dict.fromkeys(routes))


def _actions(report: CustomerReport) -> list[str]:
    actions: list[str] = []
    for item in report.action_memory:
        if item.chosen_action:
            actions.append(f"{item.route}:{item.chosen_action}")
    return list(dict.fromkeys(actions))
