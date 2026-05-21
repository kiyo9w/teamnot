from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from teamnot.cli.__main__ import main
from teamnot.customer_loop import (
    CustomerProfile,
    ExperienceTarget,
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
