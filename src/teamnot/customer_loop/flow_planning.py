"""Deterministic customer flow-pack planning."""
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import urljoin, urlparse

from teamnot.customer_loop.models import (
    CustomerFlow,
    CustomerFlowPack,
    CustomerFlowStep,
    CustomerProfile,
    CustomerReport,
    ExperienceTarget,
)

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def suggest_customer_flow_pack(
    target: ExperienceTarget,
    profile: CustomerProfile,
    routes: list[str] | None = None,
) -> CustomerFlowPack:
    planned_routes = routes or ["/"]
    flows = [_first_value_flow(planned_routes[0], profile)]
    flows.extend(_route_flow(route) for route in planned_routes[1:])
    flows.append(_error_recovery_flow(planned_routes[0]))
    flows.append(_trust_and_adoption_flow(planned_routes[0], profile))
    return CustomerFlowPack(
        name=f"{_target_name(target)} customer flow pack",
        reset_between_flows=True,
        flows=flows,
    )


def inspect_customer_flow_pack(
    target: ExperienceTarget,
    profile: CustomerProfile,
    routes: list[str] | None = None,
    wrapper_path: str | Path = "scripts/winbrowser",
    command_runner: CommandRunner | None = None,
) -> CustomerFlowPack:
    wrapper = _resolve_wrapper_path(wrapper_path)
    if not wrapper.exists():
        raise FileNotFoundError(f"Browser wrapper not found: {wrapper}")
    runner = command_runner or _default_runner
    planned_routes = routes or discover_customer_routes(
        target,
        profile,
        wrapper_path=wrapper,
        command_runner=runner,
    )
    pages = [_inspect_route(wrapper, runner, target, route) for route in planned_routes]
    flows = [_flow_from_page(page, profile, primary=index == 0) for index, page in enumerate(pages)]
    flows.append(_inspected_error_recovery_flow(pages[0]))
    flows.append(_inspected_trust_and_adoption_flow(pages, profile))
    return CustomerFlowPack(
        name=f"{_target_name(target)} inspected customer flow pack",
        reset_between_flows=True,
        flows=flows,
    )


def discover_customer_routes(
    target: ExperienceTarget,
    profile: CustomerProfile,
    max_routes: int = 5,
    wrapper_path: str | Path = "scripts/winbrowser",
    command_runner: CommandRunner | None = None,
) -> list[str]:
    wrapper = _resolve_wrapper_path(wrapper_path)
    if not wrapper.exists():
        raise FileNotFoundError(f"Browser wrapper not found: {wrapper}")
    runner = command_runner or _default_runner
    base_route = _target_route(target)
    _parse_json(_run(runner, [str(wrapper), "--action", "navigate", "--url", str(target.url)]))
    discovered = _parse_json(_run(runner, [str(wrapper), "--action", "eval", "--expr", _ROUTE_DISCOVERY_JS]))
    candidates = discovered.get("result", discovered)
    if not isinstance(candidates, list):
        candidates = []
    ranked = sorted(
        [candidate for candidate in candidates if isinstance(candidate, dict)],
        key=lambda candidate: _route_candidate_rank(candidate, profile, target),
        reverse=True,
    )
    routes = [base_route]
    for candidate in ranked:
        route = _route_from_href(str(candidate.get("href", "")), target)
        if route == "/" and base_route != "/":
            continue
        if not route or route in routes:
            continue
        routes.append(route)
        if len(routes) >= max_routes:
            break
    return routes


def make_flow_pack_runnable(flow_pack: CustomerFlowPack) -> CustomerFlowPack:
    return CustomerFlowPack(
        name=f"{flow_pack.name} runnable",
        reset_between_flows=flow_pack.reset_between_flows,
        flows=[
            CustomerFlow(
                name=flow.name,
                start_url=flow.start_url,
                steps=[_make_step_runnable(step) for step in flow.steps],
            )
            for flow in flow_pack.flows
        ],
    )


def render_flow_refinement_report(
    inspected: CustomerFlowPack,
    runnable: CustomerFlowPack,
    report: CustomerReport | None = None,
) -> str:
    inspected_gaps = flow_pack_gaps(inspected)
    runnable_gaps = _runnable_only_gaps(runnable)
    report_gaps = _report_gaps(report) if report else []
    remaining_gaps = _dedupe([*inspected_gaps, *runnable_gaps, *report_gaps])
    gap_lines = [f"- {gap}" for gap in remaining_gaps] if remaining_gaps else ["- None detected."]
    lines = [
        "# Customer Flow Refinement Report",
        "",
        f"Inspected flow pack: {inspected.name}",
        f"Runnable flow pack: {runnable.name}",
        "",
        "## Refinements Applied",
        *_refinement_lines(inspected, runnable),
        "",
        "## Remaining Gaps",
        *gap_lines,
        "",
        "## External And Irreversible Action Policy",
        "- External downloads, installers, login, checkout, claim-offer, and account actions are verified as visible text/links unless explicitly modeled by a human-approved flow.",
        "- TODO steps are converted to checkpoints in the runnable flow so TeamNoT reports the missing customer input instead of pretending it tested it.",
        "- Screenshots remain attached to every executed step in the customer report.",
    ]
    return "\n".join(lines).strip() + "\n"


def flow_pack_gaps(flow_pack: CustomerFlowPack) -> list[str]:
    gaps: list[str] = []
    for flow in flow_pack.flows:
        for step in flow.steps:
            text = " ".join([
                step.id,
                step.action,
                step.selector,
                step.text,
                step.value,
                str(step.file or ""),
                step.description,
            ])
            if "TODO:" in text:
                gaps.append(f"{flow.name} / {step.id}: unresolved TODO remains.")
            if step.action == "checkpoint":
                gaps.append(f"{flow.name} / {step.id}: checkpoint requires human/agent interpretation.")
    return gaps


def _runnable_only_gaps(flow_pack: CustomerFlowPack) -> list[str]:
    gaps: list[str] = []
    for flow in flow_pack.flows:
        for step in flow.steps:
            if step.action != "checkpoint":
                continue
            if step.description.startswith("Skipped unresolved generated step"):
                continue
            gaps.append(f"{flow.name} / {step.id}: checkpoint requires human/agent interpretation.")
    return gaps


def _first_value_flow(route: str, profile: CustomerProfile) -> CustomerFlow:
    return CustomerFlow(
        name="Core first-value journey",
        start_url=route,
        steps=[
            CustomerFlowStep(
                id="first-screen-loaded",
                action="assert_selector",
                selector="main, body",
                description="Confirm the customer lands on a rendered product screen.",
            ),
            CustomerFlowStep(
                id="start-primary-action",
                action="click_text",
                text="TODO: primary action text",
                description=(
                    "Replace with the visible action a real "
                    f"{profile.role or profile.persona} would click first."
                ),
            ),
            CustomerFlowStep(
                id="provide-realistic-input",
                action="fill",
                selector="TODO: primary input selector",
                value="TODO: realistic customer input",
                description="Replace with realistic data for this product's main job.",
            ),
            CustomerFlowStep(
                id="submit-workflow",
                action="click_text",
                text="TODO: submit action text",
                description="Submit the core workflow.",
            ),
            CustomerFlowStep(
                id="first-value-visible",
                action="wait_for_text",
                text="TODO: result/success text",
                timeout_ms=15000,
                description="Confirm the customer reaches useful output, not just a screen transition.",
            ),
        ],
    )


def _route_flow(route: str) -> CustomerFlow:
    route_name = _route_name(route)
    return CustomerFlow(
        name=f"{route_name} journey",
        start_url=route,
        steps=[
            CustomerFlowStep(
                id="screen-loaded",
                action="assert_selector",
                selector="main, body",
                description=f"Confirm `{route}` renders as a customer-visible screen.",
            ),
            CustomerFlowStep(
                id="primary-task-started",
                action="click_text",
                text="TODO: primary action text",
                description="Replace with the main action for this screen.",
            ),
            CustomerFlowStep(
                id="task-outcome-visible",
                action="wait_for_text",
                text="TODO: expected outcome text",
                timeout_ms=15000,
                description="Confirm the customer sees a meaningful result or next step.",
            ),
        ],
    )


def _error_recovery_flow(route: str) -> CustomerFlow:
    return CustomerFlow(
        name="Mistake and recovery journey",
        start_url=route,
        steps=[
            CustomerFlowStep(
                id="open-risky-action",
                action="click_text",
                text="TODO: action that accepts customer input",
                description="Open the workflow where a real user can make a common mistake.",
            ),
            CustomerFlowStep(
                id="enter-invalid-input",
                action="fill",
                selector="TODO: input selector",
                value="TODO: invalid but realistic customer input",
                description="Use a realistic mistake, not a synthetic developer-only edge case.",
            ),
            CustomerFlowStep(
                id="submit-invalid-input",
                action="click_text",
                text="TODO: submit action text",
                description="Submit the invalid input.",
            ),
            CustomerFlowStep(
                id="recovery-guidance-visible",
                action="wait_for_text",
                text="TODO: customer-readable error or retry guidance",
                timeout_ms=15000,
                description="Confirm the product explains how to recover.",
            ),
            CustomerFlowStep(
                id="technical-error-hidden",
                action="assert_no_text",
                text="Traceback",
                description="Guard against leaking developer errors to customers.",
            ),
        ],
    )


def _trust_and_adoption_flow(route: str, profile: CustomerProfile) -> CustomerFlow:
    trust_text = profile.trust_threshold or "TODO: privacy, security, pricing, support, or proof text"
    return CustomerFlow(
        name="Trust and adoption journey",
        start_url=route,
        steps=[
            CustomerFlowStep(
                id="trust-cue-visible",
                action="assert_text",
                text=trust_text,
                description="Confirm the page addresses the customer's adoption risk.",
            ),
            CustomerFlowStep(
                id="support-or-next-step-visible",
                action="assert_text",
                text="TODO: support, contact, pricing, pilot, docs, or onboarding text",
                description="Confirm the customer has a clear next step after evaluation.",
            ),
        ],
    )


def _target_name(target: ExperienceTarget) -> str:
    parsed = urlparse(str(target.url))
    return parsed.hostname or "Product"


def _route_name(route: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", route.strip("/")).strip()
    return cleaned.title() if cleaned else "Home"


def _target_route(target: ExperienceTarget) -> str:
    parsed = urlparse(str(target.url))
    return parsed.path or "/"


def _route_from_href(href: str, target: ExperienceTarget) -> str:
    if not href:
        return ""
    parsed_target = urlparse(str(target.url))
    parsed = urlparse(href)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return ""
    if parsed.scheme and parsed.netloc and parsed.netloc != parsed_target.netloc:
        return ""
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def _flow_from_page(page: dict, profile: CustomerProfile, primary: bool) -> CustomerFlow:
    action = _best_action(page.get("actions", []))
    input_control = _first([control for control in page.get("inputs", []) if _input_is_customer_workflow(control)])
    result_text = _best_cue(page.get("resultCues", [])) or "TODO: expected result/success text"
    flow_name = "Core first-value journey" if primary else f"{_route_name(page.get('route', '/'))} journey"
    steps = [
        CustomerFlowStep(
            id="screen-loaded",
            action="assert_selector",
            selector=page.get("mainSelector") or "main, body",
            description="Confirm the customer lands on a rendered product screen.",
        )
    ]
    if input_control and input_control.get("type") == "file":
        steps.append(CustomerFlowStep(
            id="provide-realistic-file",
            action="upload",
            selector=input_control.get("selector", "input[type=file]"),
            file=Path("TODO: absolute path to realistic customer file"),
            description="Select a realistic customer file for this product's main job.",
        ))
    elif input_control:
        steps.append(CustomerFlowStep(
            id="provide-realistic-input",
            action="fill",
            selector=input_control.get("selector", "TODO: primary input selector"),
            value=_sample_value_for_input(input_control),
            description=f"Enter realistic input as {profile.role or profile.persona}.",
        ))
    if action:
        steps.append(_step_for_action("start-primary-action", action, "Click the primary customer action."))
    else:
        steps.append(CustomerFlowStep(
            id="identify-primary-action",
            action="checkpoint",
            description="No visible button/link action was detected on this route; inspect manually.",
        ))
    steps.append(CustomerFlowStep(
        id="outcome-visible",
        action="wait_for_text",
        text=result_text,
        timeout_ms=15000,
        description="Confirm the customer reaches useful output or a clear next step.",
    ))
    return CustomerFlow(name=flow_name, start_url=page.get("route", "/"), steps=steps)


def _inspected_error_recovery_flow(page: dict) -> CustomerFlow:
    input_control = _first([control for control in page.get("inputs", []) if _input_is_customer_workflow(control)])
    action = _best_action(page.get("actions", []))
    steps = [
        CustomerFlowStep(
            id="open-risky-action",
            action="checkpoint",
            description="Use the inspected controls below to exercise a realistic mistake/recovery path.",
        )
    ]
    if input_control and input_control.get("type") != "file":
        steps.append(CustomerFlowStep(
            id="enter-invalid-input",
            action="fill",
            selector=input_control.get("selector", "TODO: input selector"),
            value="TODO: invalid but realistic customer input",
            description="Use an input mistake that a real customer would make.",
        ))
    if action:
        steps.append(_step_for_action("submit-invalid-input", action, "Submit the invalid customer input."))
    steps.extend([
        CustomerFlowStep(
            id="recovery-guidance-visible",
            action="wait_for_text",
            text=_best_cue(page.get("recoveryCues", [])) or "TODO: customer-readable error or retry guidance",
            timeout_ms=15000,
            description="Confirm the product explains how to recover.",
        ),
        CustomerFlowStep(
            id="technical-error-hidden",
            action="assert_no_text",
            text="Traceback",
            description="Guard against leaking developer errors to customers.",
        ),
    ])
    return CustomerFlow(name="Mistake and recovery journey", start_url=page.get("route", "/"), steps=steps)


def _inspected_trust_and_adoption_flow(pages: list[dict], profile: CustomerProfile) -> CustomerFlow:
    trust_route, trust_cue = _best_routed_cue(pages, "trustCues")
    adoption_route, adoption_cue = _best_routed_cue(pages, "adoptionCues")
    start_url = trust_route or adoption_route or (pages[0].get("route", "/") if pages else "/")
    trust_step = (
        CustomerFlowStep(
            id="trust-cue-visible",
            action="assert_text",
            text=trust_cue,
            description="Confirm the page addresses the customer's adoption risk.",
        )
        if trust_cue
        else CustomerFlowStep(
            id="trust-cue-visible",
            action="checkpoint",
            description=(
                "No concrete trust cue was inferred from the page. "
                f"Customer trust threshold: {profile.trust_threshold or 'not specified'}"
            ),
        )
    )
    adoption_step = (
        CustomerFlowStep(
            id="support-or-next-step-visible",
            action="assert_text",
            text=adoption_cue,
            description="Confirm the customer has a clear next step after evaluation.",
        )
        if adoption_cue and adoption_route == start_url
        else CustomerFlowStep(
            id="support-or-next-step-visible",
            action="checkpoint",
            description=(
                f"Concrete adoption cue was found on `{adoption_route}`, not `{start_url}`; "
                "model a separate adoption flow if this route matters."
                if adoption_cue and adoption_route
                else "No concrete support, pricing, pilot, docs, or onboarding cue was inferred from the page."
            ),
        )
    )
    return CustomerFlow(
        name="Trust and adoption journey",
        start_url=start_url,
        steps=[trust_step, adoption_step],
    )


def _inspect_route(
    wrapper: Path,
    runner: CommandRunner,
    target: ExperienceTarget,
    route: str,
) -> dict:
    url = urljoin(str(target.url), route)
    _parse_json(_run(runner, [str(wrapper), "--action", "navigate", "--url", url]))
    inspected = _parse_json(_run(runner, [str(wrapper), "--action", "eval", "--expr", _FLOW_INSPECT_JS]))
    page = inspected.get("result", inspected)
    if not isinstance(page, dict):
        page = {}
    page["route"] = route
    page["url"] = url
    return page


def _sample_value_for_input(input_control: dict) -> str:
    input_type = input_control.get("type", "")
    label = " ".join(str(input_control.get(key, "")) for key in ("label", "placeholder", "name")).lower()
    if input_type == "email" or "email" in label:
        return "customer@example.com"
    if input_type == "number":
        return "42"
    if "name" in label:
        return "Q2 Migration"
    if "search" in label:
        return "customer data"
    return "TODO: realistic customer input"


def _step_for_action(step_id: str, action: dict, fallback_description: str) -> CustomerFlowStep:
    text = action.get("text", "")
    selector = action.get("selector", "")
    href = action.get("href", "")
    if _requires_human_approval(action):
        return CustomerFlowStep(
            id=step_id,
            action="assert_text",
            selector="",
            text=text,
            description=(
                f"External/irreversible CTA detected ({href or text}); verify visibility only. "
                "Model the click explicitly in a human-approved flow if needed."
            ),
        )
    return CustomerFlowStep(
        id=step_id,
        action="click_text" if text else "click",
        selector=selector,
        text=text,
        description=f"{fallback_description} `{text or selector}`.",
    )


def _requires_human_approval(action: dict) -> bool:
    text = str(action.get("text", "")).lower()
    href = str(action.get("href", "")).lower()
    risky_terms = (
        "download", "installer", "login", "log in", "sign in", "sign up",
        "claim", "checkout", "purchase", "buy", "contact sales",
    )
    return bool(href and not href.startswith("#") and any(term in f"{text} {href}" for term in risky_terms))


def _make_step_runnable(step: CustomerFlowStep) -> CustomerFlowStep:
    text = " ".join([
        step.selector,
        step.text,
        step.value,
        str(step.file or ""),
        step.description,
    ])
    if "TODO:" not in text:
        return step
    return CustomerFlowStep(
        id=step.id,
        action="checkpoint",
        description=(
            f"Skipped unresolved generated step `{step.action}`. "
            f"Original selector/text/value: {step.selector or step.text or step.value or step.file or 'not specified'}"
        ),
    )


def _refinement_lines(inspected: CustomerFlowPack, runnable: CustomerFlowPack) -> list[str]:
    lines: list[str] = []
    for inspected_flow, runnable_flow in zip(inspected.flows, runnable.flows, strict=False):
        for inspected_step, runnable_step in zip(inspected_flow.steps, runnable_flow.steps, strict=False):
            if inspected_step != runnable_step:
                lines.append(
                    f"- {inspected_flow.name} / {inspected_step.id}: `{inspected_step.action}` -> `{runnable_step.action}`"
                )
    return lines or ["- No automatic refinements were needed."]


def _report_gaps(report: CustomerReport | None) -> list[str]:
    if not report:
        return []
    gaps = [f"customer finding: {finding.severity.value} - {finding.title}" for finding in report.findings]
    raw = "\n".join(evidence.raw_excerpt for evidence in report.evidence)
    for line in raw.splitlines():
        if line.startswith("STEP_FAIL|"):
            gaps.append(f"failed step: {line}")
    return gaps


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _input_is_customer_workflow(input_control: dict) -> bool:
    input_type = str(input_control.get("type", "")).lower()
    label = " ".join(str(input_control.get(key, "")) for key in ("label", "placeholder", "name", "selector")).lower()
    if input_type == "hidden":
        return False
    if "search" in label:
        return False
    return True


def _domain_terms_from_profile(profile: CustomerProfile, target: ExperienceTarget) -> list[str]:
    sources = [
        profile.persona,
        profile.role,
        profile.domain_literacy,
        profile.current_workflow,
        profile.buying_trigger,
        profile.buyer_user_split,
        profile.trust_threshold,
        target.context,
        *(profile.alternatives or []),
    ]
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "their", "your",
        "user", "customer", "buyer", "product", "needs", "clear", "real",
    }
    terms: list[str] = []
    for source in sources:
        for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(source).lower()):
            if raw not in stop and raw not in terms:
                terms.append(raw)
    return terms[:16]


def _best_action(actions: list[dict]) -> dict | None:
    ranked = sorted(actions, key=_action_rank, reverse=True)
    return ranked[0] if ranked else None


def _action_rank(action: dict) -> tuple[int, int, int]:
    text = str(action.get("text", "")).lower()
    tag = str(action.get("tag", "")).lower()
    selector = str(action.get("selector", "")).lower()
    primary_terms = (
        "run", "start", "create", "submit", "generate", "analyze", "analyse",
        "import", "preflight", "upload", "continue", "save", "send", "invite",
        "download", "explore", "contact", "try", "claim", "install",
    )
    nav_terms = (
        "skip to", "home", "menu", "research", "products", "business",
        "developers", "company", "foundation", "how it works", "pricing",
        "docs", "privacy", "terms", "learn",
    )
    primary_score = 2 if any(term in text for term in primary_terms) else 0
    button_score = 1 if tag == "button" or "submit" in selector or "button" in selector else 0
    nav_penalty = -2 if any(term in text for term in nav_terms) else 0
    page_region_score = 3 if action.get("inMain") else 0
    if action.get("inFooter"):
        page_region_score -= 4
    if action.get("inHeader") or action.get("inNav"):
        page_region_score -= 2
    return primary_score + nav_penalty + page_region_score, button_score, len(text)


def _route_candidate_rank(candidate: dict, profile: CustomerProfile, target: ExperienceTarget) -> tuple[int, int, int, int]:
    text = str(candidate.get("text", "")).lower()
    href = str(candidate.get("href", "")).lower()
    path = _route_from_href(str(candidate.get("href", "")), target).lower()
    combined = f"{text} {href} {path}"
    profile_terms = _domain_terms_from_profile(profile, target)
    product_terms = (
        "app", "dashboard", "project", "workspace", "settings", "team", "invite",
        "docs", "pricing", "security", "privacy", "support", "demo", "trial",
        "start", "try", "work", "download", "install", "onboarding",
    )
    low_value_terms = (
        "terms", "privacy-policy", "cookie", "rss", "careers", "brand", "news",
        "podcast", "livestream", "foundation", "about",
    )
    semantic_score = sum(1 for term in [*profile_terms, *product_terms] if term in combined)
    region_score = 3 if candidate.get("inMain") else 0
    if candidate.get("inFooter"):
        region_score -= 4
    if candidate.get("inHeader") or candidate.get("inNav"):
        region_score -= 1
    low_value_penalty = -4 if any(term in combined for term in low_value_terms) else 0
    depth = len([part for part in path.split("/") if part])
    return semantic_score + region_score + low_value_penalty, -depth, len(text), -len(path)


def _first(items) -> dict | str | None:
    for item in items:
        if item:
            return item
    return None


def _best_cue(items) -> str | None:
    cues = [str(item).strip() for item in items if str(item).strip()]
    useful = [cue for cue in cues if len(cue) <= 160]
    return useful[0] if useful else None


def _best_routed_cue(pages: list[dict], key: str) -> tuple[str, str | None]:
    for page in pages:
        cue = _best_cue(page.get(key, []))
        if cue:
            return str(page.get("route", "/")), cue
    return "", None


def _run(runner: CommandRunner, command: list[str]) -> subprocess.CompletedProcess[str]:
    return runner(command)


def _parse_json(result: subprocess.CompletedProcess[str]) -> dict:
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Browser wrapper failed")
    parsed = json.loads((result.stdout or "").strip() or "{}")
    if isinstance(parsed, dict) and parsed.get("ok") is False:
        raise RuntimeError(str(parsed))
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _resolve_wrapper_path(wrapper_path: str | Path) -> Path:
    path = Path(wrapper_path).expanduser()
    if path.is_absolute() or path.exists():
        return path
    candidates = [
        Path(os.environ.get("OPENCLAW_WORKSPACE", "")) / path if os.environ.get("OPENCLAW_WORKSPACE") else None,
        Path.cwd() / path,
        *(parent / path for parent in Path.cwd().parents),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return path


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)


_FLOW_INSPECT_JS = r"""(() => {
  const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  const selectorFor = (el) => {
    if (!el) return "";
    if (el.id) return `#${CSS.escape(el.id)}`;
    const testId = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testId) return `[data-testid="${CSS.escape(testId)}"],[data-test="${CSS.escape(testId)}"]`;
    const name = el.getAttribute("name");
    if (name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
    const type = el.getAttribute("type");
    if (type) return `${el.tagName.toLowerCase()}[type="${CSS.escape(type)}"]`;
    return el.tagName.toLowerCase();
  };
  const controls = Array.from(document.querySelectorAll("button,[role=button],a[href],input[type=button],input[type=submit]"))
    .filter((el) => el.offsetParent !== null)
    .slice(0, 40)
    .map((el) => {
      const rect = el.getBoundingClientRect();
      return {
        text: textOf(el) || el.getAttribute("aria-label") || el.getAttribute("value") || "",
        selector: selectorFor(el),
        tag: el.tagName.toLowerCase(),
        href: el.href || "",
        inMain: Boolean(el.closest("main,[role=main]")),
        inHeader: Boolean(el.closest("header,[role=banner]")),
        inNav: Boolean(el.closest("nav,[role=navigation]")),
        inFooter: Boolean(el.closest("footer,[role=contentinfo]")),
        top: Math.round(rect.top),
      };
    })
    .filter((item) => item.text || item.selector);
  const inputs = Array.from(document.querySelectorAll("input,textarea,select"))
    .filter((el) => el.offsetParent !== null || el.getAttribute("type") === "file")
    .slice(0, 12)
    .map((el) => {
      const id = el.getAttribute("id") || "";
      const label = id ? textOf(document.querySelector(`label[for="${CSS.escape(id)}"]`)) : "";
      return {
        selector: selectorFor(el),
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute("type") || el.tagName.toLowerCase(),
        label,
        placeholder: el.getAttribute("placeholder") || "",
        name: el.getAttribute("name") || "",
      };
    });
  const body = textOf(document.body);
  const sentences = body.split(/(?<=[.!?])\s+|\n+/).map((s) => s.trim()).filter(Boolean);
  const pickCue = (words) => sentences.filter((s) => words.some((word) => s.toLowerCase().includes(word))).slice(0, 5);
  return {
    title: document.title || "",
    heading: textOf(document.querySelector("h1,h2")) || "",
    mainSelector: document.querySelector("main") ? "main" : "body",
    actions: controls,
    inputs,
    resultCues: pickCue(["result", "report", "dashboard", "summary", "success", "complete", "download", "export"]),
    recoveryCues: pickCue(["error", "invalid", "required", "retry", "try again", "fix", "failed"]),
    trustCues: pickCue(["privacy", "secure", "security", "data", "trust", "proof", "soc", "gdpr"]),
    adoptionCues: pickCue(["pricing", "support", "contact", "demo", "trial", "docs", "onboarding", "book"]),
  };
})()"""

_ROUTE_DISCOVERY_JS = r"""(() => {
  const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  return Array.from(document.querySelectorAll("a[href],button,[role=button]"))
    .filter((el) => el.offsetParent !== null)
    .slice(0, 120)
    .map((el) => {
      const rect = el.getBoundingClientRect();
      return {
        text: textOf(el) || el.getAttribute("aria-label") || el.getAttribute("title") || "",
        href: el.href || el.getAttribute("data-href") || "",
        tag: el.tagName.toLowerCase(),
        inMain: Boolean(el.closest("main,[role=main]")),
        inHeader: Boolean(el.closest("header,[role=banner]")),
        inNav: Boolean(el.closest("nav,[role=navigation]")),
        inFooter: Boolean(el.closest("footer,[role=contentinfo]")),
        top: Math.round(rect.top),
      };
    })
    .filter((item) => item.href && item.text);
})()"""
