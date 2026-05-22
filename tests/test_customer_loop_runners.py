from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from teamnot.customer_loop import (
    CustomerLoopRunnerError,
    CustomerLoopRunnerName,
    CustomerProfile,
    CustomerTestPlan,
    ExperienceTarget,
    ManualEvidenceRunner,
    OpenClawWindowsCDPRunner,
    OpenClawWindowsFlowRunner,
    OpenClawWindowsInteractiveRunner,
    OpenClawWindowsResearcherRunner,
    OpenClawWindowsSessionRunner,
    PersistentWinBrowserCommandRunner,
)
from teamnot.customer_loop.artifacts import render_customer_report
from teamnot.customer_loop.models import (
    CustomerEvidence,
    CustomerFinding,
    CustomerFlow,
    CustomerFlowPack,
    CustomerFlowStep,
    CustomerLoopConfig,
    CustomerReport,
    CustomerScores,
    CustomerSeverity,
    ProductExplorationPlan,
    ProductRoute,
    ResearchActionMemory,
    SeededCookie,
    SeededCustomerState,
    SeededLocalStorageEntry,
    SeededTestAccount,
    VisionReviewArtifact,
    VisualFinding,
)
from teamnot.customer_loop.orchestrator import default_customer_test_plan
from teamnot.customer_loop.research_planning import suppress_repeated_noops
from teamnot.customer_loop.runners import (
    _attach_visual_findings,
    _path_for_windows_wrapper,
    _resolve_wrapper_path,
)


def _profile() -> CustomerProfile:
    return CustomerProfile(persona="Agency ops lead", role="operations")


def _plan(tmp_path: Path) -> tuple[ExperienceTarget, CustomerProfile, CustomerTestPlan]:
    target = ExperienceTarget(url="https://example-product.test")
    profile = _profile()
    plan = default_customer_test_plan(CustomerLoopConfig(target=target, profile=profile, out_dir=tmp_path))
    return target, profile, plan


def test_manual_evidence_runner_creates_report_shape(tmp_path: Path):
    evidence = tmp_path / "report.md"
    evidence.write_text(
        "Title: Wrong .md files accepted\nSeverity: critical\nRecommendation: Reject non-CSV files.",
        encoding="utf-8",
    )
    target, profile, plan = _plan(tmp_path)
    report = ManualEvidenceRunner(evidence).run(target, profile, plan, tmp_path / "out")
    assert report.findings[0].severity.value == "critical"
    assert report.findings[0].recommendation == "Reject non-CSV files."
    assert report.raw_report_path == str(evidence)


def test_openclaw_runner_degrades_when_wrapper_missing(tmp_path: Path):
    target, profile, plan = _plan(tmp_path)
    runner = OpenClawWindowsCDPRunner(wrapper_path=tmp_path / "missing-winbrowser")
    with pytest.raises(CustomerLoopRunnerError, match="scripts/winbrowser|manual"):
        runner.run(target, profile, plan, tmp_path / "out")


def test_openclaw_runner_resolves_workspace_wrapper(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    wrapper = workspace / "scripts" / "winbrowser"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(workspace))
    assert _resolve_wrapper_path("scripts/winbrowser") == wrapper


def test_openclaw_runner_resolves_ancestor_workspace_wrapper(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    project = workspace / "teamnot"
    wrapper = workspace / "scripts" / "winbrowser"
    wrapper.parent.mkdir(parents=True)
    project.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delenv("OPENCLAW_WORKSPACE", raising=False)
    monkeypatch.chdir(project)
    assert _resolve_wrapper_path("scripts/winbrowser") == wrapper


def test_openclaw_runner_converts_absolute_paths_for_windows_wrapper(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="C:\\\\wsl\\\\artifact.png\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _path_for_windows_wrapper(tmp_path / "artifact.png") == "C:\\\\wsl\\\\artifact.png"
    assert calls[0][:2] == ["wslpath", "-w"]


def test_transient_browser_failures_are_recognized_for_retry():
    import teamnot.customer_loop.runners as runner_module

    result = subprocess.CompletedProcess(
        ["scripts/winbrowser", "--action", "navigate"],
        1,
        stdout="",
        stderr="browserType.connectOverCDP: Timeout 30000ms exceeded",
    )

    assert runner_module._transient_browser_failure(result)


def test_persistent_runner_maps_legacy_wrapper_commands_to_session_payload(monkeypatch, tmp_path: Path):
    sent_payloads: list[dict] = []
    runner = PersistentWinBrowserCommandRunner(wrapper_path=tmp_path / "winbrowser")

    def fake_request(payload, timeout=75):
        sent_payloads.append(payload)
        return {"ok": True, "action": payload["action"], "sessionId": "test-session"}

    monkeypatch.setattr(runner, "_request", fake_request)

    result = runner([
        "scripts/winbrowser",
        "--action",
        "navigate",
        "--url",
        "https://example-product.test/app",
        "--timeout",
        "12000",
    ])

    assert result.returncode == 0
    assert sent_payloads == [{
        "action": "navigate",
        "url": "https://example-product.test/app",
        "timeout": 12000,
    }]
    assert json.loads(result.stdout)["sessionId"] == "test-session"


def test_persistent_runner_maps_seeded_state_commands(monkeypatch, tmp_path: Path):
    sent_payloads: list[dict] = []
    runner = PersistentWinBrowserCommandRunner(wrapper_path=tmp_path / "winbrowser")

    def fake_request(payload, timeout=75):
        sent_payloads.append(payload)
        return {"ok": True, "action": payload["action"], "seededStateApplied": True}

    monkeypatch.setattr(runner, "_request", fake_request)

    runner([
        "scripts/winbrowser",
        "--action",
        "setCookies",
        "--cookies",
        '[{"name": "session", "value": "secret", "domain": "example.test"}]',
    ])
    runner([
        "scripts/winbrowser",
        "--action",
        "login",
        "--email",
        "customer@example.test",
        "--password",
        "secret",
        "--login-url",
        "https://example-product.test/auth/login",
        "--success-url",
        "https://example-product.test/app",
        "--workspace-id",
        "demo",
        "--timeout",
        "12000",
    ])

    assert sent_payloads == [
        {
            "action": "setCookies",
            "cookies": [{"name": "session", "value": "secret", "domain": "example.test"}],
        },
        {
            "action": "login",
            "email": "customer@example.test",
            "password": "secret",
            "loginUrl": "https://example-product.test/auth/login",
            "successUrl": "https://example-product.test/app",
            "workspaceId": "demo",
            "timeout": 12000,
        },
    ]


def test_persistent_runner_keeps_screenshots_and_eval_on_same_session(monkeypatch, tmp_path: Path):
    sent_payloads: list[dict] = []
    runner = PersistentWinBrowserCommandRunner(wrapper_path=tmp_path / "winbrowser")

    def fake_request(payload, timeout=75):
        sent_payloads.append(payload)
        return {"ok": True, "action": payload["action"]}

    monkeypatch.setattr(runner, "_request", fake_request)

    runner(["scripts/winbrowser", "--action", "eval", "--expr", "document.title"])
    runner(["scripts/winbrowser", "--action", "screenshot", "--out", "C:\\tmp\\shot.png", "--full-page"])

    assert sent_payloads == [
        {"action": "eval", "expr": "document.title"},
        {"action": "screenshot", "out": "C:\\tmp\\shot.png", "fullPage": True},
    ]


def test_openclaw_runner_can_be_mocked_when_wrapper_present(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    commands: list[list[str]] = []

    def command_runner(command):
        commands.append(list(command))
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"ok": true, "url": "https://example-product.test", "title": "Mock Product"}',
                stderr="",
            )
        if action == "viewport":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"ok": true, "viewport": {"width": 390, "height": 844}}',
                stderr="",
            )
        if action == "eval":
            if any(
                cmd[2] == "viewport"
                and "--width" in cmd
                and cmd[cmd.index("--width") + 1] == "390"
                for cmd in commands if len(cmd) > 2
            ):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=(
                        '{"ok": true, "result": {'
                        '"url": "https://example-product.test",'
                        '"viewport": {"width": 390, "height": 844},'
                        '"hasHorizontalOverflow": false,'
                        '"bodyTextLength": 300,'
                        '"firstActions": ["Run test"]'
                        "}}"
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"url": "https://example-product.test",'
                    '"title": "Mock Product",'
                    '"headings": ["Mock Product"],'
                    '"buttons": ["Run test"],'
                    '"inputs": [{"tag": "input", "type": "file", "text": "", "label": "CSV file"}],'
                    '"forms": [{"text": "Upload CSV file and run test", "controls": 2}],'
                    '"links": [],'
                    '"primaryActionText": ["Run test", "Download report"],'
                    '"bodyText": "For agency operators with risky CSV workflow problems. Upload your CSV to generate a prioritized report, download and share it with your team, retry invalid files, use sample demo data, see pricing, contact support, and trust that privacy data is local.",'
                    '"viewport": {"width": 1280, "height": 720},'
                    '"timingMs": 123,'
                    '"failedResources": [],'
                    '"hasHorizontalOverflow": false,'
                    '"focusableCount": 2,'
                    '"imagesWithoutAlt": 0,'
                    '"landmarkCount": 2,'
                    '"semanticSignals": {'
                    '"hasPricing": true, "hasSupport": true, "hasPrivacy": true,'
                    '"hasSample": true, "hasErrorRecovery": true, "hasCollaboration": true'
                    "}"
                    "}}"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    target, profile, plan = _plan(tmp_path)
    report = OpenClawWindowsCDPRunner(wrapper_path=wrapper, command_runner=command_runner).run(
        target, profile, plan, tmp_path / "out"
    )
    assert [cmd[1:3] for cmd in commands[:3]] == [
        ["--action", "status"],
        ["--action", "navigate"],
        ["--action", "viewport"],
    ]
    viewport_indexes = [index for index, cmd in enumerate(commands) if cmd[1:3] == ["--action", "viewport"]]
    screenshot_indexes = [index for index, cmd in enumerate(commands) if cmd[1:3] == ["--action", "screenshot"]]
    assert len(viewport_indexes) >= 2
    assert all(index < viewport_indexes[1] for index in screenshot_indexes[:2])
    assert any(cmd[1:3] == ["--action", "screenshot"] for cmd in commands)
    assert report.evidence[0].kind == "browser_observation"
    assert "first-impression" in report.evidence[0].raw_excerpt
    assert "STEP_SKIP|primary-workflow" in report.evidence[0].raw_excerpt
    assert "STEP_PASS|planned-task" not in report.evidence[0].raw_excerpt
    assert report.findings == []
    assert report.scores.trust_readiness >= 8
    assert report.screenshot_captures
    assert report.vision_review is not None
    assert report.vision_review.evidence_source == "screenshot metadata and hashes"


def test_researcher_seeded_state_changes_no_seeded_state_finding(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    runner = OpenClawWindowsResearcherRunner(
        wrapper_path=wrapper,
        command_runner=lambda command: subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr=""),
        seeded_state=SeededCustomerState(cookies=[SeededCookie(name="session", value="secret", domain="example.test")]),
    )
    target, profile, _plan_model = _plan(tmp_path)
    research_brain = runner._research_brain_pass(target, profile, ["/"], tmp_path / "out", runner.seeded_state)
    research_brain["seeded_state_status"] = "applied"

    _evidence, findings = __import__(
        "teamnot.customer_loop.runners",
        fromlist=["_research_brain_evidence"],
    )._research_brain_evidence(research_brain)

    assert "research-brain-no-seeded-state" not in {finding.id for finding in findings}


def test_researcher_applies_seeded_state_contract_through_browser_adapter(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    storage = tmp_path / "storage-state.json"
    storage.write_text('{"cookies": []}', encoding="utf-8")
    commands: list[list[str]] = []

    def command_runner(command):
        commands.append(list(command))
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "importStorageState":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "seededStateApplied": true}', stderr="")
        if action == "setCookies":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "cookiesApplied": 1}', stderr="")
        if action == "setLocalStorage":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "localStorageValuesApplied": 1}', stderr="")
        if action == "login":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "seededStateApplied": true, "afterUrl": "https://example-product.test/app"}', stderr="")
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    state = SeededCustomerState(
        storage_state_path=storage,
        cookies=[SeededCookie(name="session", value="secret", domain="example.test")],
        local_storage=[SeededLocalStorageEntry(origin="https://example-product.test", values={"workspace": "demo"})],
        test_account=SeededTestAccount(
            email="customer@example.test",
            password="secret",
            login_url="https://example-product.test/auth/login",
            workspace_id="demo",
        ),
    )
    target, _profile_model, _plan_model = _plan(tmp_path)

    result = OpenClawWindowsResearcherRunner(
        wrapper_path=wrapper,
        command_runner=command_runner,
        seeded_state=state,
    )._apply_seeded_state(target, state)

    assert result["status"] == "applied"
    assert state.adapter_status == "applied"
    assert [cmd[cmd.index("--action") + 1] for cmd in commands] == [
        "importStorageState",
        "setCookies",
        "setLocalStorage",
        "login",
    ]
    assert "secret" in commands[1][commands[1].index("--cookies") + 1]
    assert "secret" in commands[-1][commands[-1].index("--password") + 1]


def test_researcher_does_not_mark_login_hint_only_seeded_state_as_applied(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"ok": true, "seededStateApplied": false, '
                '"unsupportedBlocker": "loginHint records account metadata only"}'
            ),
            stderr="",
        )

    state = SeededCustomerState(
        test_account=SeededTestAccount(
            email="customer@example.test",
            login_url="https://example-product.test/auth/login",
        )
    )
    target, _profile_model, _plan_model = _plan(tmp_path)

    result = OpenClawWindowsResearcherRunner(
        wrapper_path=wrapper,
        command_runner=command_runner,
        seeded_state=state,
    )._apply_seeded_state(target, state)

    assert result["status"] == "unsupported"
    assert state.adapter_status == "unsupported"
    assert "metadata only" in state.unsupported_blocker


def test_researcher_records_seed_adapter_rejection_as_blocker(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    storage = tmp_path / "storage-state.json"
    storage.write_text('{"cookies": []}', encoding="utf-8")

    def command_runner(command):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok": false, "unsupportedBlocker": "bad storage"}',
            stderr="",
        )

    state = SeededCustomerState(storage_state_path=storage)
    target, _profile_model, _plan_model = _plan(tmp_path)

    result = OpenClawWindowsResearcherRunner(
        wrapper_path=wrapper,
        command_runner=command_runner,
        seeded_state=state,
    )._apply_seeded_state(target, state)

    assert result["status"] == "unsupported"
    assert state.adapter_status == "unsupported"
    assert state.unsupported_blocker == "bad storage"


def test_noop_action_memory_suppresses_repeated_actions():
    actions = [{"id": "click-run"}, {"id": "click-settings"}]
    memory = [ResearchActionMemory(route="/", chosen_action="click-run", no_op=True)]

    assert suppress_repeated_noops("/", actions, memory) == [{"id": "click-settings"}]


def test_model_vision_findings_feed_back_into_customer_report():
    target = ExperienceTarget(url="https://example-product.test")
    profile = CustomerProfile(persona="Ops buyer", role="operator")
    report = CustomerReport(
        profile=profile,
        target=target,
        plan=CustomerTestPlan(target=target, customer_job={"functional": "Evaluate product"}),
        vision_review=VisionReviewArtifact(
            review_kind="model_vision",
            model_worker="codex_cli",
            visual_findings=[
                VisualFinding(
                    title="Primary action is visually hidden",
                    severity=CustomerSeverity.high,
                    customer_interpretation="The buyer cannot tell where to begin.",
                    recommendation="Raise the primary CTA above secondary navigation.",
                    action_hint="try clicking the visible primary CTA",
                    evidence_paths=["screenshots/first-impression.png"],
                    confidence=0.81,
                )
            ],
        ),
    )

    _attach_visual_findings(report)

    assert report.findings[-1].id == "vision-primary-action-is-visually-hidden"
    assert report.findings[-1].trust_blocker is True
    assert report.findings[-1].evidence[0].kind == "model_vision"
    assert report.findings[-1].evidence[0].metadata["action_hint"] == "try clicking the visible primary CTA"


def test_openclaw_runner_surfaces_persona_research_gap_findings(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    commands: list[list[str]] = []

    def command_runner(command):
        commands.append(list(command))
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "title": "Mock"}', stderr="")
        if action == "viewport":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            if any(
                cmd[2] == "viewport" and "--width" in cmd and cmd[cmd.index("--width") + 1] == "390"
                for cmd in commands if len(cmd) > 2
            ):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout='{"ok": true, "result": {"viewport": {"width": 390, "height": 844}, "hasHorizontalOverflow": false}}',
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"url": "https://example-product.test",'
                    '"title": "Secure Dev Agent",'
                    '"headings": ["Secure Dev Agent"],'
                    '"buttons": ["Start demo"],'
                    '"inputs": [],'
                    '"forms": [],'
                    '"links": [],'
                    '"primaryActionText": ["Start demo"],'
                    '"bodyText": "For security leads reviewing repository access workflow risk. Start demo to generate a report with next action, retry guidance, privacy policy, SOC2 proof, permission model, pricing, support, sample output, share with team.",'
                    '"viewport": {"width": 1280, "height": 900},'
                    '"timingMs": 123,'
                    '"failedResources": [],'
                    '"hasHorizontalOverflow": false,'
                    '"focusableCount": 1,'
                    '"imagesWithoutAlt": 0,'
                    '"landmarkCount": 2,'
                    '"semanticSignals": {'
                    '"hasPricing": true, "hasSupport": true, "hasPrivacy": true,'
                    '"hasSample": true, "hasErrorRecovery": true, "hasCollaboration": true'
                    "}"
                    "}}"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    target = ExperienceTarget(url="https://example-product.test")
    profile = CustomerProfile(
        persona="Security lead evaluating a coding agent",
        role="security lead",
        current_workflow="reviews repository access and permission risk before developer rollout",
        buying_trigger="developer team wants to adopt an agent for production repositories",
        alternatives=["Cursor", "Claude Code"],
        buyer_user_split="developer is daily user; security lead approves repository access",
        trust_threshold="SOC2 proof, permission model, and data retention policy",
    )
    plan = default_customer_test_plan(CustomerLoopConfig(target=target, profile=profile, out_dir=tmp_path))

    report = OpenClawWindowsCDPRunner(wrapper_path=wrapper, command_runner=command_runner).run(
        target, profile, plan, tmp_path / "out"
    )

    ids = {finding.id for finding in report.findings}
    assert {"trust-threshold-not-validated", "buyer-user-fit-not-validated", "switching-forces-not-validated"} <= ids
    assert report.scores.trust_readiness < 8
    assert report.scores.buying_readiness < 7


def test_openclaw_runner_reports_customer_findings_from_real_browser_probe(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "title": ""}', stderr="")
        if action == "eval":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"url": "https://example-product.test",'
                    '"title": "",'
                    '"headings": [],'
                    '"buttons": [],'
                    '"inputs": [{"tag": "button", "text": "", "label": "", "aria": "", "placeholder": "", "name": "", "disabled": true}],'
                    '"forms": [],'
                    '"links": [],'
                    '"primaryActionText": [],'
                    '"bodyText": "",'
                    '"viewport": {"width": 390, "height": 844},'
                    '"timingMs": 500,'
                    '"failedResources": ["https://example-product.test/missing.css"],'
                    '"hasHorizontalOverflow": true,'
                    '"overflowWidth": 420,'
                    '"overflowOffenders": [{"selector": "pre.log", "tag": "pre", "text": "long command output", "width": 620}],'
                    '"focusableCount": 1,'
                    '"imagesWithoutAlt": 0,'
                    '"landmarkCount": 0,'
                    '"semanticSignals": {'
                    '"hasPricing": false, "hasSupport": false, "hasPrivacy": false,'
                    '"hasSample": false, "hasErrorRecovery": false, "hasCollaboration": false'
                    "}"
                    "}}"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    target, profile, plan = _plan(tmp_path)
    report = OpenClawWindowsCDPRunner(wrapper_path=wrapper, command_runner=command_runner).run(
        target, profile, plan, tmp_path / "out"
    )
    finding_ids = {finding.id for finding in report.findings}
    assert "first-impression-empty" in finding_ids
    assert "missing-core-workflow" in finding_ids
    assert "unlabeled-controls" in finding_ids
    assert "missing-error-recovery-cues" in finding_ids
    assert "STEP_FAIL|first-impression" in report.evidence[0].raw_excerpt
    assert "overflowWidth=420" in report.evidence[0].raw_excerpt
    assert "pre.log width=620: long command output" in report.evidence[0].raw_excerpt
    assert len(report.evidence[0].screenshot_paths) == 3


def test_openclaw_runner_wraps_timeout_as_customer_loop_error(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        raise subprocess.TimeoutExpired(command, timeout=30)

    target, profile, plan = _plan(tmp_path)
    runner = OpenClawWindowsCDPRunner(wrapper_path=wrapper, command_runner=command_runner)
    with pytest.raises(CustomerLoopRunnerError, match="timed out|manual"):
        runner.run(target, profile, plan, tmp_path / "out")


def test_openclaw_interactive_runner_clicks_sample_flow(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    commands: list[list[str]] = []

    def command_runner(command):
        commands.append(list(command))
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        expr = command[-1] if "--expr" in command else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "title": "Mock"}', stderr="")
        if action == "viewport":
            width = command[command.index("--width") + 1] if "--width" in command else "1280"
            height = command[command.index("--height") + 1] if "--height" in command else "900"
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f'{{"ok": true, "viewport": {{"width": {width}, "height": {height}}}}}',
                stderr="",
            )
        if action == "eval" and "sample-demo" in expr:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"action": "sample-demo",'
                    '"clicked": true,'
                    '"actionText": "Run sample report",'
                    '"changed": true,'
                    '"before": {"bodyTextLength": 100, "statusText": ""},'
                    '"after": {"bodyTextLength": 500, "statusText": "Completed", "downloadEnabled": true, "resultText": "Verdict"}'
                    "}}"
                ),
                stderr="",
            )
        if action == "eval" and any(
            cmd[2] == "viewport" and "--width" in cmd and cmd[cmd.index("--width") + 1] == "390"
            for cmd in commands if len(cmd) > 2
        ):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"url": "https://example-product.test",'
                    '"viewport": {"width": 390, "height": 844},'
                    '"hasHorizontalOverflow": false,'
                    '"bodyTextLength": 300,'
                    '"firstActions": ["Run sample report"]'
                    "}}"
                ),
                stderr="",
            )
        if action == "eval":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"url": "https://example-product.test",'
                    '"title": "Mock Product",'
                    '"headings": ["Mock Product"],'
                    '"buttons": ["Run sample report"],'
                    '"inputs": [{"tag": "input", "type": "file", "text": "", "label": "CSV file"}],'
                    '"forms": [{"text": "Upload CSV file and run sample report", "controls": 2}],'
                    '"links": [],'
                    '"primaryActionText": ["Run sample report", "Download report"],'
                    '"bodyText": "For agency operators with risky CSV workflow problems. Run sample report to generate a report, download and share it with your team, retry invalid files, use sample demo data, see pricing, contact support, and trust that privacy data is local.",'
                    '"viewport": {"width": 1280, "height": 900},'
                    '"timingMs": 123,'
                    '"failedResources": [],'
                    '"hasHorizontalOverflow": false,'
                    '"focusableCount": 2,'
                    '"imagesWithoutAlt": 0,'
                    '"landmarkCount": 2,'
                    '"semanticSignals": {'
                    '"hasPricing": true, "hasSupport": true, "hasPrivacy": true,'
                    '"hasSample": true, "hasErrorRecovery": true, "hasCollaboration": true'
                    "}"
                    "}}"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    target, profile, plan = _plan(tmp_path)
    report = OpenClawWindowsInteractiveRunner(wrapper_path=wrapper, command_runner=command_runner).run(
        target, profile, plan, tmp_path / "out"
    )
    assert len(report.evidence) == 2
    assert report.evidence[1].kind == "browser_interaction"
    assert "STEP_PASS|interactive-sample-flow" in report.evidence[1].raw_excerpt
    assert "interactive-before.png" in report.evidence[1].screenshot_paths[0]
    assert report.findings == []


def test_openclaw_flow_runner_executes_configured_customer_steps(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    csv = tmp_path / "products.csv"
    csv.write_text("Handle,Title\nexample,Example\n", encoding="utf-8")
    commands: list[list[str]] = []

    def command_runner(command):
        commands.append(list(command))
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "title": "Mock"}', stderr="")
        if action == "viewport":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "upload":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "files": ["products.csv"]}', stderr="")
        if action == "eval" and "--expr" in command and "step.action" in command[-1]:
            if "wait_for_text" in command[-1]:
                return subprocess.CompletedProcess(
                    command, 0, stdout='{"ok": true, "result": {"passed": true, "summary": "found text: Blockers"}}', stderr=""
                )
            return subprocess.CompletedProcess(
                command, 0, stdout='{"ok": true, "result": {"passed": true, "summary": "clicked Run preflight"}}', stderr=""
            )
        if action == "eval":
            if any(
                cmd[2] == "viewport" and "--width" in cmd and cmd[cmd.index("--width") + 1] == "390"
                for cmd in commands if len(cmd) > 2
            ):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=(
                        '{"ok": true, "result": {'
                        '"url": "https://example-product.test",'
                        '"viewport": {"width": 390, "height": 844},'
                        '"hasHorizontalOverflow": false,'
                        '"bodyTextLength": 300,'
                        '"firstActions": ["Run preflight"]'
                        "}}"
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"url": "https://example-product.test",'
                    '"title": "Mock Product",'
                    '"headings": ["Mock Product"],'
                    '"buttons": ["Run preflight"],'
                    '"inputs": [{"tag": "input", "type": "file", "text": "", "label": "CSV file"}],'
                    '"forms": [{"text": "Upload CSV file and run preflight", "controls": 2}],'
                    '"links": [],'
                    '"primaryActionText": ["Run preflight", "Download report"],'
                    '"bodyText": "For agency operators with risky CSV workflow problems. Upload CSV, run preflight, fix invalid files, download report, share with team, privacy data local, pricing support demo.",'
                    '"viewport": {"width": 1280, "height": 900},'
                    '"timingMs": 123,'
                    '"failedResources": [],'
                    '"hasHorizontalOverflow": false,'
                    '"focusableCount": 2,'
                    '"imagesWithoutAlt": 0,'
                    '"landmarkCount": 2,'
                    '"semanticSignals": {'
                    '"hasPricing": true, "hasSupport": true, "hasPrivacy": true,'
                    '"hasSample": true, "hasErrorRecovery": true, "hasCollaboration": true'
                    "}"
                    "}}"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow = CustomerFlow(
        name="Upload CSV and render report",
        steps=[
            CustomerFlowStep(id="upload-csv", action="upload", selector="#csv-file", file=csv),
            CustomerFlowStep(id="run-preflight", action="click", selector="button[type=submit]"),
            CustomerFlowStep(id="see-blockers", action="wait_for_text", text="Blockers"),
        ],
    )
    target, profile, plan = _plan(tmp_path)
    report = OpenClawWindowsFlowRunner(flow, wrapper_path=wrapper, command_runner=command_runner).run(
        target, profile, plan, tmp_path / "out"
    )

    assert report.evidence[1].kind == "browser_flow"
    assert "STEP_PASS|primary-workflow|configured customer flow runner" in report.evidence[0].raw_excerpt
    assert "STEP_PASS|flow-upload-csv-and-render-report-upload-csv" in report.evidence[1].raw_excerpt
    assert "STEP_PASS|flow-upload-csv-and-render-report-see-blockers" in report.evidence[1].raw_excerpt
    assert len(report.evidence[1].screenshot_paths) == 3
    assert any(cmd[1:3] == ["--action", "upload"] for cmd in commands)
    assert report.findings == []


def test_openclaw_flow_runner_explains_missing_upload_wrapper_support(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    csv = tmp_path / "products.csv"
    csv.write_text("Handle,Title\nexample,Example\n", encoding="utf-8")
    commands: list[list[str]] = []

    def command_runner(command):
        commands.append(list(command))
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "title": "Mock"}', stderr="")
        if action == "viewport":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "upload":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="unknown action upload")
        if action == "eval":
            if any(
                cmd[2] == "viewport" and "--width" in cmd and cmd[cmd.index("--width") + 1] == "390"
                for cmd in commands if len(cmd) > 2
            ):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout='{"ok": true, "result": {"viewport": {"width": 390, "height": 844}, "hasHorizontalOverflow": false}}',
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"title": "Mock Product",'
                    '"headings": ["Mock Product"],'
                    '"buttons": ["Run preflight"],'
                    '"inputs": [{"tag": "input", "type": "file", "label": "CSV file"}],'
                    '"bodyText": "Upload CSV, run preflight, fix invalid files, download report, privacy support demo team.",'
                    '"semanticSignals": {"hasPricing": true, "hasSupport": true, "hasPrivacy": true, "hasSample": true, "hasErrorRecovery": true, "hasCollaboration": true},'
                    '"failedResources": [], "hasHorizontalOverflow": false, "focusableCount": 2, "imagesWithoutAlt": 0, "landmarkCount": 2'
                    "}}"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow = CustomerFlow(
        name="Upload CSV",
        steps=[CustomerFlowStep(id="upload-csv", action="upload", selector="#csv-file", file=csv)],
    )
    target, profile, plan = _plan(tmp_path)

    with pytest.raises(CustomerLoopRunnerError, match="requires browser wrapper upload support"):
        OpenClawWindowsFlowRunner(flow, wrapper_path=wrapper, command_runner=command_runner).run(
            target, profile, plan, tmp_path / "out"
        )


def test_openclaw_flow_runner_executes_multi_screen_non_upload_flow_pack(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    commands: list[list[str]] = []

    def command_runner(command):
        commands.append(list(command))
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "title": "Dashboard"}', stderr="")
        if action == "viewport":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval" and "--expr" in command and "step.action" in command[-1]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"ok": true, "result": {"passed": true, "summary": "generic step passed"}}',
                stderr="",
            )
        if action == "eval":
            if any(
                cmd[2] == "viewport" and "--width" in cmd and cmd[cmd.index("--width") + 1] == "390"
                for cmd in commands if len(cmd) > 2
            ):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=(
                        '{"ok": true, "result": {'
                        '"url": "https://example-product.test",'
                        '"viewport": {"width": 390, "height": 844},'
                        '"hasHorizontalOverflow": false,'
                        '"bodyTextLength": 300,'
                        '"firstActions": ["New project", "Invite"]'
                        "}}"
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"url": "https://example-product.test",'
                    '"title": "Mock SaaS",'
                    '"headings": ["Projects"],'
                    '"buttons": ["New project", "Invite"],'
                    '"inputs": [{"tag": "input", "type": "text", "text": "", "label": "Project name"}],'
                    '"forms": [{"text": "New project Project name", "controls": 2}],'
                    '"links": [],'
                    '"primaryActionText": ["New project", "Invite"],'
                    '"bodyText": "For operators managing project workflow problems, invite teammates, settings, billing, privacy, support, demo, retry and collaboration. Create a project to get a result report, export summary, recommendations, and next action for your manager or buyer.",'
                    '"viewport": {"width": 1280, "height": 900},'
                    '"timingMs": 123,'
                    '"failedResources": [],'
                    '"hasHorizontalOverflow": false,'
                    '"focusableCount": 3,'
                    '"imagesWithoutAlt": 0,'
                    '"landmarkCount": 2,'
                    '"semanticSignals": {'
                    '"hasPricing": true, "hasSupport": true, "hasPrivacy": true,'
                    '"hasSample": true, "hasErrorRecovery": true, "hasCollaboration": true'
                    "}"
                    "}}"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = CustomerFlowPack(
        name="SaaS operator journeys",
        flows=[
            CustomerFlow(
                name="Create project",
                start_url="/projects",
                steps=[
                    CustomerFlowStep(id="open-new-project", action="click_text", text="New project"),
                    CustomerFlowStep(id="name-project", action="fill", selector="#project-name", value="Q2 Migration"),
                    CustomerFlowStep(id="save-project", action="click_text", text="Create"),
                    CustomerFlowStep(id="project-created", action="wait_for_url", url="/projects/"),
                ],
            ),
            CustomerFlow(
                name="Invite teammate",
                start_url="/settings/team",
                steps=[
                    CustomerFlowStep(id="fill-email", action="fill", selector="input[type=email]", value="ops@example.com"),
                    CustomerFlowStep(id="select-role", action="select", selector="#role", value="viewer"),
                    CustomerFlowStep(id="send-invite", action="click_text", text="Invite"),
                    CustomerFlowStep(id="invite-confirmed", action="assert_text", text="Invitation sent"),
                ],
            ),
        ],
    )
    target, profile, plan = _plan(tmp_path)
    report = OpenClawWindowsFlowRunner(flow_pack, wrapper_path=wrapper, command_runner=command_runner).run(
        target, profile, plan, tmp_path / "out"
    )

    assert report.evidence[1].kind == "browser_flow"
    assert "STEP_PASS|primary-workflow|configured customer flow runner" in report.evidence[0].raw_excerpt
    assert "SaaS operator journeys" in report.evidence[1].observed_behavior
    assert "STEP_PASS|flow-create-project-open-new-project" in report.evidence[1].raw_excerpt
    assert "STEP_PASS|flow-invite-teammate-invite-confirmed" in report.evidence[1].raw_excerpt
    assert len(report.evidence[1].screenshot_paths) == 8
    navigated_urls = [cmd[cmd.index("--url") + 1] for cmd in commands if "--url" in cmd]
    assert "https://example-product.test/projects" in navigated_urls
    assert "https://example-product.test/settings/team" in navigated_urls
    assert report.findings == []


def test_openclaw_flow_runner_continues_other_flows_after_step_failure(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    commands: list[list[str]] = []
    step_calls = 0

    def command_runner(command):
        nonlocal step_calls
        commands.append(list(command))
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "title": "Dashboard"}', stderr="")
        if action == "viewport":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval" and "--expr" in command and "step.action" in command[-1]:
            step_calls += 1
            if step_calls == 1:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout='{"ok": true, "result": {"passed": false, "summary": "missing create button"}}',
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"ok": true, "result": {"passed": true, "summary": "invite sent"}}',
                stderr="",
            )
        if action == "eval":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"title": "Mock SaaS",'
                    '"headings": ["Projects"],'
                    '"buttons": ["Create project", "Invite"],'
                    '"inputs": [],'
                    '"forms": [],'
                    '"links": [],'
                    '"primaryActionText": ["Create project", "Invite"],'
                    '"bodyText": "Create projects, invite teammates, privacy, support, demo, retry, result report.",'
                    '"viewport": {"width": 1280, "height": 900},'
                    '"timingMs": 123,'
                    '"failedResources": [],'
                    '"hasHorizontalOverflow": false,'
                    '"focusableCount": 2,'
                    '"imagesWithoutAlt": 0,'
                    '"landmarkCount": 2,'
                    '"semanticSignals": {'
                    '"hasPricing": true, "hasSupport": true, "hasPrivacy": true,'
                    '"hasSample": true, "hasErrorRecovery": true, "hasCollaboration": true'
                    "}"
                    "}}"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow_pack = CustomerFlowPack(
        name="SaaS operator journeys",
        flows=[
            CustomerFlow(
                name="Create project",
                start_url="/projects",
                steps=[CustomerFlowStep(id="create-project", action="click_text", text="Create project")],
            ),
            CustomerFlow(
                name="Invite teammate",
                start_url="/settings/team",
                steps=[CustomerFlowStep(id="send-invite", action="click_text", text="Invite")],
            ),
        ],
    )
    target, profile, plan = _plan(tmp_path)
    report = OpenClawWindowsFlowRunner(flow_pack, wrapper_path=wrapper, command_runner=command_runner).run(
        target, profile, plan, tmp_path / "out"
    )

    raw = report.evidence[1].raw_excerpt
    assert "STEP_FAIL|flow-create-project-create-project|missing create button" in raw
    assert "STEP_PASS|flow-invite-teammate-send-invite|invite sent" in raw
    assert len(report.evidence[1].screenshot_paths) == 2
    navigated_urls = [cmd[cmd.index("--url") + 1] for cmd in commands if "--url" in cmd]
    assert "https://example-product.test/projects" in navigated_urls
    assert "https://example-product.test/settings/team" in navigated_urls
    assert "flow-create-project-create-project-failed" in {finding.id for finding in report.findings}


def test_openclaw_flow_runner_marks_checkpoints_as_skipped_not_passed(tmp_path: Path):
    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def command_runner(command):
        action = command[2] if len(command) > 2 and command[1] == "--action" else ""
        if action == "navigate":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true, "title": "Dashboard"}', stderr="")
        if action == "viewport":
            return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")
        if action == "eval":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"ok": true, "result": {'
                    '"title": "Mock SaaS",'
                    '"headings": ["Projects"],'
                    '"buttons": ["Create project"],'
                    '"inputs": [],'
                    '"forms": [],'
                    '"links": [],'
                    '"primaryActionText": ["Create project"],'
                    '"bodyText": "Create projects, privacy, support, demo, retry, result report.",'
                    '"viewport": {"width": 1280, "height": 900},'
                    '"timingMs": 123,'
                    '"failedResources": [],'
                    '"hasHorizontalOverflow": false,'
                    '"focusableCount": 1,'
                    '"imagesWithoutAlt": 0,'
                    '"landmarkCount": 2,'
                    '"semanticSignals": {'
                    '"hasPricing": true, "hasSupport": true, "hasPrivacy": true,'
                    '"hasSample": true, "hasErrorRecovery": true, "hasCollaboration": true'
                    "}"
                    "}}"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}', stderr="")

    flow = CustomerFlow(
        name="Buyer proof review",
        start_url="/security",
        steps=[
            CustomerFlowStep(
                id="security-owner-judgment",
                action="checkpoint",
                description="Security owner must judge whether proof is sufficient.",
            )
        ],
    )
    target, profile, plan = _plan(tmp_path)
    report = OpenClawWindowsFlowRunner(flow, wrapper_path=wrapper, command_runner=command_runner).run(
        target, profile, plan, tmp_path / "out"
    )

    raw = report.evidence[1].raw_excerpt
    assert "STEP_SKIP|flow-buyer-proof-review-security-owner-judgment|Security owner must judge" in raw
    assert "STEP_PASS|flow-buyer-proof-review-security-owner-judgment" not in raw
    assert "flow-buyer-proof-review-security-owner-judgment-failed" not in {finding.id for finding in report.findings}
    assert "1 skip" in report.evidence[1].observed_behavior


def test_manual_evidence_labeled_blocker_fields_override_loose_heuristics(tmp_path: Path):
    evidence = tmp_path / "report.md"
    evidence.write_text(
        "\n".join([
            "Title: Trust copy is clear",
            "Severity: high",
            "Customer interpretation: I trust this result.",
            "Trust blocker: no",
            "Core task blocker: yes",
        ]),
        encoding="utf-8",
    )
    target, profile, plan = _plan(tmp_path)
    report = ManualEvidenceRunner(evidence).run(target, profile, plan, tmp_path / "out")
    assert report.findings[0].trust_blocker is False
    assert report.findings[0].core_task_blocker is True


def test_manual_evidence_extracts_markdown_label_blocks(tmp_path: Path):
    evidence = tmp_path / "report.md"
    evidence.write_text(
        "\n".join([
            "### Critical - Wrong file types can produce successful reports",
            "",
            "Customer interpretation:",
            "",
            "The customer sees Completed and assumes the product understood their input.",
            "This is dangerous because it creates a credible-looking report.",
            "",
            "Business/product impact:",
            "",
            "This blocks trust and production usage.",
            "",
            "Likely frequency:",
            "",
            "Medium. Dragging the wrong file is common in messy migration folders containing",
            "exports, notes, reports, screenshots, and fixture files.",
            "",
            "Recommended fix:",
            "",
            "- Reject upload filenames that do not end in `.csv`.",
            "- Add a customer-friendly retry error.",
        ]),
        encoding="utf-8",
    )
    target, profile, plan = _plan(tmp_path)
    report = ManualEvidenceRunner(evidence).run(target, profile, plan, tmp_path / "out")
    finding = report.findings[0]
    assert finding.business_impact == "This blocks trust and production usage."
    assert "fixture files" in finding.likely_frequency
    assert "credible-looking report" in finding.customer_interpretation
    assert "customer-friendly retry error" in finding.recommendation

    rendered = render_customer_report(report)
    assert "- Recommendation:\n  - Reject upload filenames" in rendered
    assert "## Method" in rendered
    assert "## Persona Tested" in rendered
    assert "## Test Plan" in rendered
    assert "## OpenClaw Skill Coverage" in rendered
    assert "## Dimension Assessment" in rendered
    assert "## Researcher Observations" in rendered
    assert "## Customer Journey Notes" in rendered
    assert "## Customer Objections" in rendered
    assert "## Recommended Next Iteration" in rendered


def test_customer_report_renders_multidimensional_research_synthesis(tmp_path: Path):
    target, profile, plan = _plan(tmp_path)
    profile.alternatives = ["Cursor", "Claude Code"]
    profile.trust_threshold = "Needs proof before real repository access"
    report = CustomerReport(
        profile=profile,
        target=target,
        plan=plan,
        scores=CustomerScores(task_success=6, usability=6, trust_readiness=6, buying_readiness=6),
        evidence=[
            CustomerEvidence(
                kind="browser_observation",
                observed_behavior="Baseline probe completed.",
                raw_excerpt="\n".join([
                    "STEP_PASS|first-impression|clear product heading",
                    "STEP_PASS|customer-promise|specific customer problem",
                    "STEP_FAIL|error-recovery|no recovery copy",
                    "STEP_SKIP|jtbd-forces|requires interpretation",
                    "STEP_SKIP|buyer-user-mismatch|requires budget owner review",
                ]),
            ),
            CustomerEvidence(
                kind="browser_flow",
                observed_behavior="Configured flow pack executed.",
                raw_excerpt="STEP_PASS|flow-create-project-start|started customer flow",
                metadata={
                    "flow_pack": {
                        "flows": [
                            {
                                "name": "Create project",
                                "start_url": "/projects",
                                "steps": [
                                    {"id": "screen", "action": "assert_selector"},
                                    {"id": "outcome", "action": "checkpoint"},
                                ],
                            },
                            {
                                "name": "Invite teammate",
                                "start_url": "/settings/team",
                                "steps": [
                                    {"id": "screen", "action": "assert_selector"},
                                ],
                            }
                        ]
                    },
                    "flows": [
                        {"flow": "Create project", "id": "screen", "passed": True},
                        {"flow": "Create project", "id": "outcome", "passed": None, "skipped": True},
                    ],
                },
            ),
        ],
    )

    rendered = render_customer_report(report)

    assert "## Customer Journey Notes" in rendered
    assert "## Research Lens" in rendered
    assert "Alternatives in the customer's head: Cursor, Claude Code" in rendered
    assert "Trust threshold: Needs proof before real repository access" in rendered
    assert "- First impression: passed: clear product heading" in rendered
    assert "## Route-By-Route Analysis" in rendered
    assert "model-vision" in rendered
    assert "Create project (`/projects`): partially covered; 1 executable step(s), 1 interpretation checkpoint(s)." in rendered
    assert "Invite teammate (`/settings/team`): not executed; 1 executable step(s), 0 interpretation checkpoint(s)." in rendered
    assert "## Dimension Assessment" in rendered
    assert "- Error recovery: 6/10 — failed: no recovery copy" in rendered
    assert "## Researcher Observations" in rendered
    assert "- Positive signal: first-impression — clear product heading" in rendered
    assert "- Risk signal: error-recovery — no recovery copy" in rendered
    assert "- Needs interpretation: jtbd-forces — requires interpretation" in rendered
    assert "What proof satisfies this trust threshold" in rendered
    assert "Why switch from Cursor, Claude Code?" in rendered
    assert "## Next Research Actions" in rendered
    assert "Run a JTBD pass" in rendered


def test_customer_report_next_iteration_prioritizes_severity_over_research_gap(tmp_path: Path):
    target, profile, plan = _plan(tmp_path)
    report = CustomerReport(
        profile=profile,
        target=target,
        plan=plan,
        findings=[
            CustomerFinding(
                id="trust-threshold-not-validated",
                title="Trust threshold not validated",
                severity=CustomerSeverity.low,
                trust_blocker=True,
                recommendation="Run trust proof pass.",
            ),
            CustomerFinding(
                id="missing-error-recovery-cues",
                title="Mistake recovery is not visible",
                severity=CustomerSeverity.medium,
                recommendation="Show retry guidance.",
            ),
        ],
    )

    rendered = render_customer_report(report)

    assert "Fix `missing-error-recovery-cues`: Show retry guidance." in rendered


def test_runner_enum_values_are_stable():
    assert CustomerLoopRunnerName.manual.value == "manual"
    assert CustomerLoopRunnerName.openclaw_windows_cdp.value == "openclaw-windows-cdp"
    assert CustomerLoopRunnerName.openclaw_windows_interactive.value == "openclaw-windows-interactive"
    assert CustomerLoopRunnerName.openclaw_windows_flow.value == "openclaw-windows-flow"
    assert CustomerLoopRunnerName.openclaw_windows_session.value == "openclaw-windows-session"
    assert CustomerLoopRunnerName.openclaw_windows_researcher.value == "openclaw-windows-researcher"


def test_openclaw_session_runner_explores_and_writes_fresh_flow_artifacts(tmp_path: Path, monkeypatch):
    import teamnot.customer_loop.runners as runner_module

    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    target, profile, plan = _plan(tmp_path)
    exploration = ProductExplorationPlan(
        target=target,
        profile=profile,
        routes=[ProductRoute(route="/", label="Home", priority=10)],
        journeys=[],
    )
    inspected = CustomerFlowPack(
        name="Inspected",
        flows=[CustomerFlow(name="Home", steps=[CustomerFlowStep(id="loaded", action="assert_selector", selector="main")])],
    )
    runnable = CustomerFlowPack(
        name="Runnable",
        flows=[CustomerFlow(name="Home", steps=[CustomerFlowStep(id="loaded", action="assert_selector", selector="body")])],
    )
    calls: list[str] = []

    monkeypatch.setattr(runner_module, "explore_product", lambda *args, **kwargs: calls.append("explore") or exploration)
    monkeypatch.setattr(runner_module, "routes_from_exploration", lambda plan: calls.append("routes") or ["/"])
    monkeypatch.setattr(runner_module, "inspect_customer_flow_pack", lambda *args, **kwargs: calls.append("inspect") or inspected)
    monkeypatch.setattr(
        runner_module,
        "make_flow_pack_runnable",
        lambda flow_pack, **kwargs: calls.append("runnable") or runnable,
    )
    monkeypatch.setattr(runner_module, "render_flow_refinement_report", lambda *args, **kwargs: "# report\n")
    monkeypatch.setattr(
        OpenClawWindowsSessionRunner,
        "_explore_screens",
        lambda self, target, routes, out_dir: calls.append("screens") or {
            "method": "mock screen exploration",
            "routes_seeded": routes,
            "routes_discovered": routes,
            "actions_executed": 1,
            "routes": [
                {
                    "route": "/",
                    "entry_screenshot": str(tmp_path / "out" / "screenshots" / "screen.png"),
                    "actions": [
                        {
                            "action": {"text": "Start", "selector": "button"},
                            "url_changed": True,
                            "text_changed": True,
                            "visual_changed": True,
                            "before_screenshot": str(tmp_path / "out" / "screenshots" / "before.png"),
                            "after_screenshot": str(tmp_path / "out" / "screenshots" / "after.png"),
                        }
                    ],
                }
            ],
        },
    )

    class FakeFlowRunner:
        def __init__(self, flow_pack, **kwargs):
            self.flow_pack = flow_pack

        def run(self, target, profile, plan, out_dir):
            return CustomerReport(
                profile=profile,
                target=target,
                plan=plan,
                evidence=[
                    CustomerEvidence(kind="browser_observation", metadata={}),
                    CustomerEvidence(kind="browser_flow", metadata={}),
                ],
            )

    monkeypatch.setattr(runner_module, "OpenClawWindowsFlowRunner", FakeFlowRunner)

    report = OpenClawWindowsSessionRunner(wrapper_path=wrapper).run(target, profile, plan, tmp_path / "out")

    assert calls == ["explore", "routes", "inspect", "runnable", "screens"]
    assert (tmp_path / "out" / "product_exploration.yaml").exists()
    assert (tmp_path / "out" / "screen_exploration.yaml").exists()
    assert (tmp_path / "out" / "inspected_flow.yaml").exists()
    assert (tmp_path / "out" / "runnable_flow.yaml").exists()
    assert (tmp_path / "out" / "flow_refinement_report.md").exists()
    assert report.evidence[0].metadata["runner"] == "openclaw-windows-session"
    assert report.evidence[1].metadata["product_exploration"]["routes"][0]["route"] == "/"
    assert report.evidence[1].metadata["screen_exploration"]["actions_executed"] == 1
    assert report.evidence[2].kind == "browser_screen_exploration"


def test_openclaw_researcher_runner_writes_research_brain_artifacts(tmp_path: Path, monkeypatch):
    import teamnot.customer_loop.runners as runner_module

    wrapper = tmp_path / "scripts" / "winbrowser"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    seeded = tmp_path / "seeded-state.yaml"
    seeded.write_text("email: test@example.com\n", encoding="utf-8")
    target, profile, plan = _plan(tmp_path)
    exploration = ProductExplorationPlan(
        target=target,
        profile=profile,
        routes=[ProductRoute(route="/", label="Home", priority=10)],
        journeys=[],
    )
    inspected = CustomerFlowPack(
        name="Inspected",
        flows=[CustomerFlow(name="Home", steps=[CustomerFlowStep(id="loaded", action="assert_selector", selector="main")])],
    )
    runnable = CustomerFlowPack(
        name="Runnable",
        flows=[CustomerFlow(name="Home", steps=[CustomerFlowStep(id="loaded", action="assert_selector", selector="body")])],
    )
    calls: list[str] = []

    monkeypatch.setattr(runner_module, "explore_product", lambda *args, **kwargs: calls.append("explore") or exploration)
    monkeypatch.setattr(runner_module, "routes_from_exploration", lambda plan, **kwargs: calls.append("routes") or ["/"])
    monkeypatch.setattr(runner_module, "inspect_customer_flow_pack", lambda *args, **kwargs: calls.append("inspect") or inspected)
    monkeypatch.setattr(
        runner_module,
        "make_flow_pack_runnable",
        lambda flow_pack, **kwargs: calls.append("runnable") or runnable,
    )
    monkeypatch.setattr(runner_module, "render_flow_refinement_report", lambda *args, **kwargs: "# report\n")
    monkeypatch.setattr(
        OpenClawWindowsResearcherRunner,
        "_explore_screens",
        lambda self, target, routes, out_dir: calls.append("screens") or {
            "method": "mock screen exploration",
            "routes_discovered": ["/", "/auth/login"],
            "actions_executed": 1,
            "routes": [],
        },
    )
    monkeypatch.setattr(
        OpenClawWindowsResearcherRunner,
        "_research_brain_pass",
        lambda self, target, profile, routes, out_dir, seeded_state=None: calls.append("brain") or {
            "method": "mock research brain",
            "seeded_state_path": str(seeded),
            "seeded_state_status": "applied" if seeded_state else "absent",
            "routes_discovered": ["/app"],
            "actions_executed": 2,
            "routes": [
                {
                    "route": "/",
                    "entry_screenshot": "observe.png",
                    "actions": [
                        {
                            "action": {"id": "filled-submit-form-0", "kind": "filled_submit"},
                            "url_changed": True,
                            "text_changed": True,
                            "visual_changed": True,
                            "before_screenshot": "before.png",
                            "after_screenshot": "after.png",
                            "before_screenshot_ok": True,
                            "after_screenshot_ok": True,
                        }
                    ],
                }
            ],
        },
    )

    class FakeFlowRunner:
        def __init__(self, flow_pack, **kwargs):
            self.flow_pack = flow_pack

        def run(self, target, profile, plan, out_dir):
            return CustomerReport(
                profile=profile,
                target=target,
                plan=plan,
                evidence=[
                    CustomerEvidence(kind="browser_observation", metadata={}),
                    CustomerEvidence(kind="browser_flow", metadata={}),
                ],
            )

    monkeypatch.setattr(runner_module, "OpenClawWindowsFlowRunner", FakeFlowRunner)

    report = OpenClawWindowsResearcherRunner(wrapper_path=wrapper, seeded_state_path=seeded).run(
        target,
        profile,
        plan,
        tmp_path / "out",
    )

    assert calls == ["explore", "routes", "screens", "brain", "inspect", "runnable"]
    assert (tmp_path / "out" / "research_brain.yaml").exists()
    assert report.evidence[0].metadata["runner"] == "openclaw-windows-researcher"
    assert report.evidence[1].metadata["research_brain"]["actions_executed"] == 2
    assert report.evidence[3].kind == "browser_research_brain"
    assert not any(finding.id == "research-brain-no-seeded-state" for finding in report.findings)


def test_screen_exploration_evidence_fails_when_actions_do_not_change_screen():
    import teamnot.customer_loop.runners as runner_module

    evidence, findings = runner_module._screen_exploration_evidence(
        {
            "method": "mock screen exploration",
            "routes_discovered": ["/"],
            "routes": [
                {
                    "route": "/",
                    "entry_screenshot": "entry.png",
                    "actions": [
                        {
                            "action": {"text": "Start", "selector": "button"},
                            "url_changed": False,
                            "text_changed": False,
                            "visual_changed": False,
                            "before_screenshot": "before.png",
                            "after_screenshot": "after.png",
                        }
                    ],
                }
            ],
        }
    )

    assert evidence.kind == "browser_screen_exploration"
    assert "STEP_FAIL|screen-action" in evidence.raw_excerpt
    assert {finding.id for finding in findings} == {
        "screen-exploration-no-observable-change",
        "screen-exploration-entry-route-only",
    }


def test_screen_action_filter_rejects_blank_links_and_external_links():
    import teamnot.customer_loop.runners as runner_module

    target = ExperienceTarget(url="http://127.0.0.1:3000/")

    assert not runner_module._safe_screen_action(
        {"tag": "a", "text": "", "href": "http://127.0.0.1:3000/"},
        target,
    )
    assert not runner_module._safe_screen_action(
        {"tag": "a", "text": "GitHub Repo", "href": "https://github.com/example/repo"},
        target,
    )
    assert runner_module._safe_screen_action(
        {"tag": "a", "text": "Log In", "href": "http://127.0.0.1:3000/auth/login"},
        target,
    )


def test_research_brain_evidence_flags_missing_seeded_state_and_visual_flakiness():
    import teamnot.customer_loop.runners as runner_module

    evidence, findings = runner_module._research_brain_evidence(
        {
            "method": "mock research brain",
            "routes_discovered": ["/", "/auth/login"],
            "routes": [
                {
                    "route": "/auth/login",
                    "entry_screenshot": "entry.png",
                    "actions": [
                        {
                            "action": {"id": "empty-submit-form-0", "kind": "empty_submit"},
                            "url_changed": False,
                            "text_changed": True,
                            "visual_changed": False,
                            "before_screenshot": "before.png",
                            "after_screenshot": "after.png",
                            "before_screenshot_ok": False,
                            "after_screenshot_ok": True,
                        }
                    ],
                }
            ],
        }
    )

    assert evidence.kind == "browser_research_brain"
    assert "STEP_PASS|research-auth-login-01" in evidence.raw_excerpt
    assert {finding.id for finding in findings} == {
        "research-brain-no-realistic-form-submit",
        "research-brain-no-seeded-state",
        "research-brain-screenshot-evidence-flaky",
    }


def test_research_actions_plan_empty_and_filled_submit_before_clicks():
    import teamnot.customer_loop.runners as runner_module

    actions = runner_module._research_actions_from_observation(
        {
            "forms": [
                {
                    "index": 0,
                    "submitText": "Register",
                    "inputs": [{"name": "email", "type": "email"}],
                }
            ],
            "actions": [
                {"tag": "a", "text": "Profile", "href": "http://127.0.0.1:3000/profile"},
                {"tag": "a", "text": "GitHub Repo", "href": "https://github.com/example/repo"},
            ],
        },
        ExperienceTarget(url="http://127.0.0.1:3000/"),
    )

    assert [action["kind"] for action in actions[:2]] == ["empty_submit", "filled_submit"]
    assert any(action["kind"] == "click" for action in actions)
    assert all("GitHub" not in action.get("id", "") for action in actions)
