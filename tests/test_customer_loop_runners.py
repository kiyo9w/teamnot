from __future__ import annotations

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
)
from teamnot.customer_loop.artifacts import render_customer_report
from teamnot.customer_loop.models import CustomerLoopConfig
from teamnot.customer_loop.orchestrator import default_customer_test_plan
from teamnot.customer_loop.runners import _path_for_windows_wrapper, _resolve_wrapper_path


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
        if action == "eval":
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
        ["--action", "screenshot"],
    ]
    assert report.evidence[0].kind == "browser_observation"
    assert "first-impression" in report.evidence[0].raw_excerpt
    assert report.findings == []
    assert report.scores.trust_readiness >= 8


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


def test_runner_enum_values_are_stable():
    assert CustomerLoopRunnerName.manual.value == "manual"
    assert CustomerLoopRunnerName.openclaw_windows_cdp.value == "openclaw-windows-cdp"
