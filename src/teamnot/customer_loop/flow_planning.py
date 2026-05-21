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
    planned_routes = routes or ["/"]
    pages = [_inspect_route(wrapper, runner, target, route) for route in planned_routes]
    flows = [_flow_from_page(page, profile, primary=index == 0) for index, page in enumerate(pages)]
    flows.append(_inspected_error_recovery_flow(pages[0]))
    flows.append(_inspected_trust_and_adoption_flow(pages, profile))
    return CustomerFlowPack(
        name=f"{_target_name(target)} inspected customer flow pack",
        reset_between_flows=True,
        flows=flows,
    )


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
        steps.append(CustomerFlowStep(
            id="start-primary-action",
            action="click_text" if action.get("text") else "click",
            selector=action.get("selector", ""),
            text=action.get("text", ""),
            description=f"Click the visible customer action `{action.get('text') or action.get('selector')}`.",
        ))
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
        steps.append(CustomerFlowStep(
            id="submit-invalid-input",
            action="click_text" if action.get("text") else "click",
            selector=action.get("selector", ""),
            text=action.get("text", ""),
            description="Submit the invalid customer input.",
        ))
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
    trust_cue = _best_cue(cue for page in pages for cue in page.get("trustCues", []))
    adoption_cue = _best_cue(cue for page in pages for cue in page.get("adoptionCues", []))
    return CustomerFlow(
        name="Trust and adoption journey",
        start_url=pages[0].get("route", "/") if pages else "/",
        steps=[
            CustomerFlowStep(
                id="trust-cue-visible",
                action="assert_text",
                text=trust_cue or profile.trust_threshold or "TODO: privacy, security, pricing, support, or proof text",
                description="Confirm the page addresses the customer's adoption risk.",
            ),
            CustomerFlowStep(
                id="support-or-next-step-visible",
                action="assert_text",
                text=adoption_cue or "TODO: support, contact, pricing, pilot, docs, or onboarding text",
                description="Confirm the customer has a clear next step after evaluation.",
            ),
        ],
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


def _input_is_customer_workflow(input_control: dict) -> bool:
    input_type = str(input_control.get("type", "")).lower()
    label = " ".join(str(input_control.get(key, "")) for key in ("label", "placeholder", "name", "selector")).lower()
    if input_type == "hidden":
        return False
    if "search" in label:
        return False
    return True


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
    return primary_score + nav_penalty, button_score, len(text)


def _first(items) -> dict | str | None:
    for item in items:
        if item:
            return item
    return None


def _best_cue(items) -> str | None:
    cues = [str(item).strip() for item in items if str(item).strip()]
    useful = [cue for cue in cues if len(cue) <= 160]
    return useful[0] if useful else None


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
    .map((el) => ({
      text: textOf(el) || el.getAttribute("aria-label") || el.getAttribute("value") || "",
      selector: selectorFor(el),
      tag: el.tagName.toLowerCase(),
      href: el.href || "",
    }))
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
