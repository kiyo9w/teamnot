"""Deterministic customer flow-pack planning."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from teamnot.customer_loop.models import (
    CustomerFlow,
    CustomerFlowPack,
    CustomerFlowStep,
    CustomerProfile,
    ExperienceTarget,
)


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
