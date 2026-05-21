from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import teamnot.cli.__main__ as cli
from teamnot.cli.__main__ import main
from teamnot.customer_loop.models import (
    CustomerEvidence,
    CustomerFlow,
    CustomerFlowPack,
    CustomerFlowStep,
    CustomerReport,
)


def test_customer_loop_commands_are_visible_in_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "customer-test" in result.output
    assert "customer-loop" in result.output
    assert "customer-flow-plan" in result.output
    assert "customer-flow-inspect" in result.output
    assert "customer-explore" in result.output
    assert "customer-flow-session" in result.output


def test_customer_test_help_exposes_required_options():
    result = CliRunner().invoke(main, ["customer-test", "--help"])
    assert result.exit_code == 0
    for option in ["--target", "--profile", "--out", "--runner", "--evidence", "--flow", "--seeded-state", "--domain-oracle"]:
        assert option in result.output
    assert "openclaw-windows-interactive" in result.output
    assert "openclaw-windows-flow" in result.output


def test_customer_loop_help_exposes_required_options():
    result = CliRunner().invoke(main, ["customer-loop", "--help"])
    assert result.exit_code == 0
    for option in [
        "--target",
        "--profile",
        "--out",
        "--max-iterations",
        "--severity-threshold",
        "--run-teamnot",
        "--no-run-teamnot",
        "--runner",
        "--evidence",
        "--flow",
        "--seeded-state",
        "--domain-oracle",
    ]:
        assert option in result.output
    assert "openclaw-windows-interactive" in result.output
    assert "openclaw-windows-flow" in result.output


def test_customer_flow_plan_help_exposes_required_options():
    result = CliRunner().invoke(main, ["customer-flow-plan", "--help"])
    assert result.exit_code == 0
    for option in ["--target", "--profile", "--route", "--out"]:
        assert option in result.output


def test_customer_flow_inspect_help_exposes_required_options():
    result = CliRunner().invoke(main, ["customer-flow-inspect", "--help"])
    assert result.exit_code == 0
    for option in ["--target", "--profile", "--route", "--out", "--wrapper"]:
        assert option in result.output


def test_customer_test_passes_seeded_state_to_researcher(tmp_path: Path, monkeypatch):
    profile = tmp_path / "profile.yaml"
    profile.write_text("persona: Admin\nrole: workspace admin\n", encoding="utf-8")
    seeded = tmp_path / "seeded.yaml"
    seeded.write_text(
        "test_account:\n  email: customer@example.test\n  password: secret\nsafety_constraints:\n  - test workspace only\n",
        encoding="utf-8",
    )
    oracle = tmp_path / "oracle.yaml"
    oracle.write_text("name: Expected report\nexpected_output: ok\n", encoding="utf-8")
    captured = {}

    class FakeResearcher:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, target, profile_model, plan, out_dir):
            return CustomerReport(profile=profile_model, target=target, plan=plan, summary="ok")

    monkeypatch.setattr(cli, "OpenClawWindowsResearcherRunner", FakeResearcher)

    result = CliRunner().invoke(
        main,
        [
            "customer-test",
            "--target",
            "https://example-product.test",
            "--profile",
            str(profile),
            "--out",
            str(tmp_path / "out"),
            "--runner",
            "openclaw-windows-researcher",
            "--seeded-state",
            str(seeded),
            "--domain-oracle",
            str(oracle),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["seeded_state_path"] == seeded
    assert captured["seeded_state"].test_account.email == "customer@example.test"


def test_customer_test_records_seeded_state_when_runner_cannot_apply_it(tmp_path: Path):
    profile = tmp_path / "profile.yaml"
    profile.write_text("persona: Admin\nrole: workspace admin\n", encoding="utf-8")
    evidence = tmp_path / "evidence.md"
    evidence.write_text("Title: Minor issue\nSeverity: low\n", encoding="utf-8")
    seeded = tmp_path / "seeded.yaml"
    seeded.write_text(
        "test_account:\n  email: customer@example.test\n  password: secret\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"

    result = CliRunner().invoke(
        main,
        [
            "customer-test",
            "--target",
            "https://example-product.test",
            "--profile",
            str(profile),
            "--out",
            str(out),
            "--runner",
            "manual",
            "--evidence",
            str(evidence),
            "--seeded-state",
            str(seeded),
        ],
    )

    assert result.exit_code == 0, result.output
    metadata = (out / "seeded_state_metadata.yaml").read_text(encoding="utf-8")
    assert "unsupported" in metadata
    assert "***REDACTED***" in metadata
    assert "secret" not in metadata


def test_customer_explore_help_exposes_required_options():
    result = CliRunner().invoke(main, ["customer-explore", "--help"])
    assert result.exit_code == 0
    for option in ["--target", "--profile", "--out", "--max-routes", "--wrapper"]:
        assert option in result.output


def test_customer_flow_session_help_exposes_required_options():
    result = CliRunner().invoke(main, ["customer-flow-session", "--help"])
    assert result.exit_code == 0
    for option in ["--target", "--profile", "--route", "--out", "--wrapper", "--seeded-state", "--domain-oracle"]:
        assert option in result.output


def test_customer_flow_session_passes_custom_wrapper_to_runner(tmp_path: Path, monkeypatch):
    profile = tmp_path / "profile.yaml"
    profile.write_text("persona: Developer\nrole: engineer\n", encoding="utf-8")
    out = tmp_path / "session"
    wrapper = tmp_path / "custom-winbrowser"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    captured: dict[str, Path] = {}
    flow_pack = CustomerFlowPack(
        name="Inspected",
        flows=[CustomerFlow(name="Core", steps=[CustomerFlowStep(id="loaded", action="assert_selector", selector="main")])],
    )

    monkeypatch.setattr(cli, "inspect_customer_flow_pack", lambda *args, **kwargs: flow_pack)
    monkeypatch.setattr(cli, "write_report_artifacts", lambda *args, **kwargs: None)

    class FakeFlowRunner:
        def __init__(self, flow_pack, wrapper_path):
            captured["wrapper_path"] = wrapper_path

        def run(self, target, profile_model, plan, out_dir):
            return CustomerReport(profile=profile_model, target=target, plan=plan, summary="ok")

    monkeypatch.setattr(cli, "OpenClawWindowsFlowRunner", FakeFlowRunner)

    result = CliRunner().invoke(
        main,
        [
            "customer-flow-session",
            "--target",
            "https://example-product.test",
            "--profile",
            str(profile),
            "--route",
            "/",
            "--out",
            str(out),
            "--wrapper",
            str(wrapper),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["wrapper_path"] == wrapper


def test_customer_flow_session_writes_exploration_when_routes_are_unseeded(tmp_path: Path, monkeypatch):
    profile = tmp_path / "profile.yaml"
    profile.write_text("persona: Developer\nrole: engineer\n", encoding="utf-8")
    out = tmp_path / "session"
    wrapper = tmp_path / "custom-winbrowser"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    flow_pack = CustomerFlowPack(
        name="Inspected",
        flows=[CustomerFlow(name="Core", steps=[CustomerFlowStep(id="loaded", action="assert_selector", selector="main")])],
    )
    captured: dict[str, object] = {}

    class FakeExploration:
        def model_dump(self, mode="python"):
            return {
                "routes": [{"route": "/", "kind": "landing", "coverage_status": "planned", "priority": 10}],
                "journeys": [{"id": "first-value", "coverage_status": "planned", "routes": ["/"], "gaps": []}],
                "coverage_gaps": ["Add domain fixtures/oracles before claiming output correctness."],
                "personas": ["Developer"],
            }

    exploration = FakeExploration()
    monkeypatch.setattr(cli, "explore_product", lambda *args, **kwargs: exploration)
    monkeypatch.setattr(cli, "routes_from_exploration", lambda plan: ["/"])
    monkeypatch.setattr(cli, "inspect_customer_flow_pack", lambda *args, **kwargs: flow_pack)

    def fake_save_yaml(data, path):
        captured[str(Path(path).name)] = data
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("ok: true\n", encoding="utf-8")
        return path

    monkeypatch.setattr(cli, "save_yaml", fake_save_yaml)

    class FakeFlowRunner:
        def __init__(self, flow_pack, wrapper_path):
            pass

        def run(self, target, profile_model, plan, out_dir):
            report = CustomerReport(profile=profile_model, target=target, plan=plan, summary="ok")
            report.evidence.append(CustomerEvidence(kind="browser_flow"))
            return report

    monkeypatch.setattr(cli, "OpenClawWindowsFlowRunner", FakeFlowRunner)

    result = CliRunner().invoke(
        main,
        [
            "customer-flow-session",
            "--target",
            "https://example-product.test",
            "--profile",
            str(profile),
            "--out",
            str(out),
            "--wrapper",
            str(wrapper),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "product_exploration.yaml" in captured


def test_customer_loop_manual_mode_writes_artifacts(tmp_path: Path):
    profile = tmp_path / "profile.yaml"
    profile.write_text("persona: Agency ops lead\nrole: operations\n", encoding="utf-8")
    evidence = tmp_path / "evidence.md"
    evidence.write_text(
        "Title: Blank preview\nSeverity: high\nRecommendation: Show the report preview.",
        encoding="utf-8",
    )
    out = tmp_path / "loop"
    result = CliRunner().invoke(
        main,
        [
            "customer-loop",
            "--target",
            "https://example-product.test",
            "--profile",
            str(profile),
            "--evidence",
            str(evidence),
            "--runner",
            "manual",
            "--out",
            str(out),
            "--no-run-teamnot",
        ],
    )
    assert result.exit_code == 0, result.output
    for name in [
        "customer_profile.yaml",
        "customer_test_plan.yaml",
        "customer_report.md",
        "customer_report.json",
        "generated_brief.yaml",
        "loop_summary.md",
    ]:
        assert (out / name).exists()
    assert (out / "screenshots").is_dir()
