from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from teamnot.cli.__main__ import main
from teamnot.customer_loop import (
    CustomerProfile,
    ExperienceTarget,
    inspect_customer_flow_pack,
    save_yaml,
    suggest_customer_flow_pack,
)


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
