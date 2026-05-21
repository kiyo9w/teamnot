from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from teamnot.cli.__main__ import main
from teamnot.customer_loop import (
    CustomerProfile,
    ExperienceTarget,
    discover_customer_routes,
    explore_product,
    flow_pack_gaps,
    inspect_customer_flow_pack,
    make_flow_pack_runnable,
    render_flow_refinement_report,
    routes_from_exploration,
    save_yaml,
    suggest_customer_flow_pack,
)
from teamnot.customer_loop.models import CustomerFlow, CustomerFlowPack, CustomerFlowStep
from teamnot.customer_loop.research_planning import rank_customer_actions


def test_suggest_customer_flow_pack_is_multi_journey_and_route_agnostic():
    profile = CustomerProfile(
        persona="Operations manager",
        role="operator",
        trust_threshold="SOC2 and data retention policy",
    )
    flow_pack = suggest_customer_flow_pack(
        ExperienceTarget(url="https://example-product.test"),
        profile,
        ["/", "/app/projects", "/settings/team"],
    )

    assert flow_pack.reset_between_flows is True
    assert [flow.name for flow in flow_pack.flows] == [
        "Core first-value journey",
        "App Projects journey",
        "Settings Team journey",
        "Mistake and recovery journey",
        "Trust and adoption journey",
    ]
    actions = {step.action for flow in flow_pack.flows for step in flow.steps}
    assert {"click_text", "fill", "wait_for_text", "assert_no_text", "assert_text"} <= actions
    assert all("csv" not in flow.model_dump_json().lower() for flow in flow_pack.flows)


def test_customer_flow_plan_cli_writes_starter_yaml(tmp_path: Path):
    profile_path = tmp_path / "profile.yaml"
    out_path = tmp_path / "customer_flow.yaml"
    save_yaml(CustomerProfile(persona="B2B admin", role="workspace admin"), profile_path)

    result = CliRunner().invoke(
        main,
        [
            "customer-flow-plan",
            "--target",
            "https://example-product.test",
            "--profile",
            str(profile_path),
            "--route",
            "/app/projects",
            "--route",
            "/settings/team",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0
    written = out_path.read_text(encoding="utf-8")
    assert "flows:" in written
    assert "Core first-value journey" in written
    assert "Settings Team journey" in written
    assert "TODO: primary action text" in written


def test_inspect_customer_flow_pack_uses_browser_dom_controls(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    commands: list[list[str]] = []

    def command_runner(command):
        commands.append(list(command))
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            result = {
                "title": "Mock SaaS",
                "heading": "Projects",
                "mainSelector": "main",
                "actions": [{"text": "New project", "selector": "#new-project", "tag": "button"}],
                "inputs": [{"selector": "#project-name", "type": "text", "label": "Project name"}],
                "resultCues": ["Project dashboard shows the result summary."],
                "recoveryCues": ["Fix invalid fields and try again."],
                "trustCues": ["Privacy policy protects customer data."],
                "adoptionCues": ["Contact support for onboarding."],
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = inspect_customer_flow_pack(
        ExperienceTarget(url="https://example-product.test"),
        CustomerProfile(persona="B2B admin", role="workspace admin"),
        ["/app/projects"],
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    core = flow_pack.flows[0]
    assert core.name == "Core first-value journey"
    assert core.start_url == "/app/projects"
    assert any(step.selector == "#project-name" and step.action == "fill" for step in core.steps)
    assert any(step.text == "New project" and step.action == "click_text" for step in core.steps)
    assert any(step.text == "Project dashboard shows the result summary." for step in core.steps)
    assert any(cmd[1:3] == ["--action", "navigate"] for cmd in commands)


def test_inspected_flow_prioritizes_workflow_buttons_over_nav_links(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            result = {
                "mainSelector": "main",
                "actions": [
                    {"text": "How it works", "selector": "a", "tag": "a"},
                    {"text": "Run preflight", "selector": "button[type=submit]", "tag": "button"},
                ],
                "inputs": [{"selector": "#csv-file", "type": "file", "label": "CSV file"}],
                "resultCues": ["Blockers to fix before import"],
                "recoveryCues": [],
                "trustCues": ["Privacy The local API analyzes the uploaded CSV."],
                "adoptionCues": [],
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = inspect_customer_flow_pack(
        ExperienceTarget(url="https://example-product.test"),
        CustomerProfile(persona="Shopify operator", role="operations"),
        ["/"],
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    core = flow_pack.flows[0]
    assert any(step.action == "upload" and step.selector == "#csv-file" for step in core.steps)
    assert any(step.action == "click_text" and step.text == "Run preflight" for step in core.steps)
    assert all(step.text != "How it works" for step in core.steps if step.action == "click_text")


def test_inspected_flow_uses_todo_for_overly_broad_result_cues(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            result = {
                "mainSelector": "main",
                "actions": [{"text": "Run analysis", "selector": "button[type=submit]", "tag": "button"}],
                "inputs": [],
                "resultCues": ["Result " + ("too broad " * 40)],
                "recoveryCues": [],
                "trustCues": [],
                "adoptionCues": [],
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = inspect_customer_flow_pack(
        ExperienceTarget(url="https://example-product.test"),
        CustomerProfile(persona="Operator", role="operator"),
        ["/"],
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    assert any(step.id == "outcome-visible" and step.text == "TODO: expected result/success text" for step in flow_pack.flows[0].steps)


def test_inspected_flow_ignores_nav_search_and_prioritizes_cta(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            result = {
                "mainSelector": "main",
                "actions": [
                    {"text": "Skip to main content", "selector": "a", "tag": "a"},
                    {"text": "Developers menu", "selector": "button", "tag": "button"},
                    {"text": "Download for Windows", "selector": "a", "tag": "a"},
                    {"text": "Explore Codex for work", "selector": "a", "tag": "a"},
                ],
                "inputs": [{"selector": "input[type=text]", "type": "text", "label": "Search"}],
                "resultCues": [],
                "recoveryCues": [],
                "trustCues": [],
                "adoptionCues": [],
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = inspect_customer_flow_pack(
        ExperienceTarget(url="https://example-product.test"),
        CustomerProfile(persona="Developer", role="engineer"),
        ["/"],
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    core = flow_pack.flows[0]
    assert not any(step.action == "fill" for step in core.steps)
    assert any(
        step.action == "click_text" and step.text in {"Download for Windows", "Explore Codex for work"}
        for step in core.steps
    )
    assert all(step.text != "Developers menu" for step in core.steps if step.action == "click_text")
    assert all(step.text != "Skip to main content" for step in core.steps if step.action == "click_text")


def test_customer_action_ranking_prefers_product_actions_over_footer_nav():
    ranked = rank_customer_actions([
        {"id": "privacy", "text": "Privacy policy", "in_footer": True},
        {"id": "run", "text": "Run report", "kind": "click", "inMain": True},
        {"id": "menu", "text": "Menu", "in_nav": True},
    ])

    assert ranked[0]["id"] == "run"


def test_inspected_flow_prioritizes_main_product_cta_over_footer_links(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            result = {
                "mainSelector": "main",
                "actions": [
                    {"text": "Explore ChatGPT", "selector": "a", "tag": "a", "inFooter": True},
                    {"text": "Explore Codex for work", "selector": "a", "tag": "a", "inMain": True},
                ],
                "inputs": [],
                "resultCues": [],
                "recoveryCues": [],
                "trustCues": [],
                "adoptionCues": [],
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = inspect_customer_flow_pack(
        ExperienceTarget(url="https://example-product.test"),
        CustomerProfile(persona="Developer", role="engineer"),
        ["/"],
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    core = flow_pack.flows[0]
    assert any(step.action == "click_text" and step.text == "Explore Codex for work" for step in core.steps)
    assert all(step.text != "Explore ChatGPT" for step in core.steps if step.action == "click_text")


def test_discover_customer_routes_prioritizes_main_workflow_routes(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            result = [
                {"text": "Careers", "href": "https://example-product.test/careers/", "inFooter": True},
                {"text": "Example Home", "href": "https://example-product.test/", "inHeader": True},
                {"text": "Create project", "href": "https://example-product.test/app/projects", "inMain": True},
                {"text": "Invite team", "href": "https://example-product.test/settings/team", "inMain": True},
                {"text": "Privacy Policy", "href": "https://example-product.test/policies/privacy-policy", "inFooter": True},
                {"text": "External docs", "href": "https://docs.example.test/", "inMain": True},
                {"text": "Email sales", "href": "mailto:sales@example-product.test", "inMain": True},
                {"text": "Open menu", "href": "javascript:void(0)", "inMain": True},
            ]
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    routes = discover_customer_routes(
        ExperienceTarget(url="https://example-product.test/product"),
        CustomerProfile(
            persona="Workspace admin",
            role="admin",
            current_workflow="create projects and invite team members",
        ),
        max_routes=3,
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    assert routes[0] == "/product"
    assert set(routes[1:]) == {"/app/projects", "/settings/team"}
    assert "/" not in routes
    assert "/sales@example-product.test" not in routes
    assert "/void(0)" not in routes


def test_explore_product_maps_journeys_and_coverage_gaps(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            result = [
                {"text": "Start building", "href": "https://example-product.test/app/projects", "inMain": True},
                {"text": "Security", "href": "https://example-product.test/security", "inMain": True},
                {"text": "Safety", "href": "https://example-product.test/safety", "inMain": True},
                {"text": "Pricing", "href": "https://example-product.test/pricing", "inHeader": True},
                {"text": "Sign in", "href": "https://example-product.test/login", "inHeader": True},
                {"text": "Docs", "href": "https://example-product.test/docs/quickstart", "inMain": True},
                {"text": "Contact sales", "href": "https://example-product.test/contact", "inFooter": True},
            ]
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    plan = explore_product(
        ExperienceTarget(url="https://example-product.test/product"),
        CustomerProfile(
            persona="Security lead",
            role="security lead",
            current_workflow="review repository access",
            buyer_user_split="developer uses it, security approves it",
            trust_threshold="SOC2 and permission model",
            alternatives=["Cursor"],
        ),
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    route_by_path = {route.route: route for route in plan.routes}
    assert route_by_path["/product"].kind == "landing"
    assert route_by_path["/security"].kind == "trust"
    assert route_by_path["/safety"].kind == "trust"
    assert route_by_path["/login"].requires_auth is True
    assert any(journey.id == "trust-adoption" for journey in plan.journeys)
    assert any(journey.id == "stateful-product" and journey.coverage_status == "blocked" for journey in plan.journeys)
    assert "daily user" in plan.personas
    assert any("Auth/account state" in gap for gap in plan.coverage_gaps)
    assert any("multi-persona" in gap for gap in plan.coverage_gaps)
    selected = routes_from_exploration(plan)
    assert "/login" not in selected
    assert "/security" in selected


def test_inspect_customer_flow_pack_discovers_routes_when_unseeded(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    inspected_routes: list[str] = []

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            inspected_routes.append(command[command.index("--url") + 1])
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            expr = command[-1]
            if "a[href],button,[role=button]" in expr:
                result = [
                    {"text": "Create project", "href": "https://example-product.test/app/projects", "inMain": True},
                ]
            else:
                result = {
                    "mainSelector": "main",
                    "actions": [{"text": "Create project", "selector": "button", "tag": "button", "inMain": True}],
                    "inputs": [],
                    "resultCues": [],
                    "recoveryCues": [],
                    "trustCues": [],
                    "adoptionCues": [],
                }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = inspect_customer_flow_pack(
        ExperienceTarget(url="https://example-product.test/product"),
        CustomerProfile(persona="Workspace admin", role="admin"),
        None,
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    assert [flow.start_url for flow in flow_pack.flows[:2]] == ["/product", "/app/projects"]
    assert "https://example-product.test/app/projects" in inspected_routes


def test_trust_flow_starts_on_route_that_contains_inferred_trust_cue(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    current_url = ""

    def command_runner(command):
        nonlocal current_url
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            current_url = command[command.index("--url") + 1]
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            result = {
                "mainSelector": "main",
                "actions": [{"text": "Start", "selector": "button", "tag": "button", "inMain": True}],
                "inputs": [],
                "resultCues": [],
                "recoveryCues": [],
                "trustCues": ["SOC2 proof is available."] if "/security" in current_url else [],
                "adoptionCues": ["Contact support for onboarding."] if "/security" not in current_url else [],
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = inspect_customer_flow_pack(
        ExperienceTarget(url="https://example-product.test"),
        CustomerProfile(persona="Security lead", role="buyer"),
        ["/", "/security"],
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    trust_flow = flow_pack.flows[-1]
    assert trust_flow.start_url == "/security"
    assert trust_flow.steps[0].text == "SOC2 proof is available."
    assert trust_flow.steps[1].action == "checkpoint"


def test_make_flow_pack_runnable_converts_todos_to_checkpoints():
    inspected = CustomerFlowPack(
        name="Product inspected flow pack",
        flows=[
            CustomerFlow(
                name="Core",
                steps=[
                    CustomerFlowStep(id="loaded", action="assert_selector", selector="main"),
                    CustomerFlowStep(id="outcome", action="wait_for_text", text="TODO: expected result/success text"),
                ],
            )
        ],
    )

    runnable = make_flow_pack_runnable(inspected)

    assert runnable.flows[0].steps[0].action == "assert_selector"
    assert runnable.flows[0].steps[1].action == "checkpoint"
    assert "unresolved generated step" in runnable.flows[0].steps[1].description
    assert flow_pack_gaps(inspected) == ["Core / outcome: unresolved TODO remains."]
    refinement = render_flow_refinement_report(inspected, runnable)
    assert "wait_for_text` -> `checkpoint" in refinement
    assert "Core / outcome: unresolved TODO remains." in refinement
    assert "Core / outcome: checkpoint requires human/agent interpretation." not in refinement
    assert "External And Irreversible Action Policy" in refinement


def test_inspected_trust_threshold_does_not_become_page_assertion(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            result = {
                "mainSelector": "main",
                "actions": [{"text": "Try in your IDE", "selector": "button", "tag": "button"}],
                "inputs": [],
                "resultCues": [],
                "recoveryCues": [],
                "trustCues": [],
                "adoptionCues": [],
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"ok": True, "result": result}),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = inspect_customer_flow_pack(
        ExperienceTarget(url="https://example-product.test"),
        CustomerProfile(persona="Developer", role="engineer", trust_threshold="needs SOC2 proof"),
        ["/"],
        wrapper_path=wrapper,
        command_runner=command_runner,
    )

    trust_flow = flow_pack.flows[-1]
    assert trust_flow.steps[0].action == "checkpoint"
    assert trust_flow.steps[0].text == ""
    assert "needs SOC2 proof" in trust_flow.steps[0].description


def test_customer_flow_inspect_cli_reports_missing_wrapper(tmp_path: Path):
    profile_path = tmp_path / "profile.yaml"
    save_yaml(CustomerProfile(persona="B2B admin", role="workspace admin"), profile_path)

    result = CliRunner().invoke(
        main,
        [
            "customer-flow-inspect",
            "--target",
            "https://example-product.test",
            "--profile",
            str(profile_path),
            "--route",
            "/",
            "--out",
            str(tmp_path / "customer_flow.yaml"),
            "--wrapper",
            str(tmp_path / "missing-winbrowser"),
        ],
    )

    assert result.exit_code == 1
    assert "Browser wrapper not found" in result.output
