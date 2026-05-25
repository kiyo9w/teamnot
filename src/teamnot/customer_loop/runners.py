"""Experience runners for customer-loop evidence collection."""
from __future__ import annotations

import json
import os
import re
import select
import subprocess
import unicodedata
from collections.abc import Callable, Sequence
from hashlib import sha256
from inspect import signature
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlparse

from teamnot.customer_loop.flow_planning import (
    explore_product,
    inspect_customer_flow_pack,
    make_flow_pack_runnable,
    render_flow_refinement_report,
    routes_from_exploration,
    suggest_customer_flow_pack,
)
from teamnot.customer_loop.io import save_yaml
from teamnot.customer_loop.models import (
    BrowserRuntimeMetadata,
    CustomerEvidence,
    CustomerFinding,
    CustomerFlow,
    CustomerFlowPack,
    CustomerFlowStep,
    CustomerLoopRunnerError,
    CustomerProfile,
    CustomerReport,
    CustomerScores,
    CustomerSeverity,
    CustomerTestPlan,
    ExperienceTarget,
    ProductExplorationPlan,
    ProductRoute,
    ResearchActionMemory,
    ScreenshotCaptureRecord,
    SeededCustomerState,
    VisionReviewArtifact,
)
from teamnot.customer_loop.research_planning import (
    action_memory_from_result,
    rank_customer_actions,
    suppress_repeated_noops,
    synthesize_jtbd_forces,
    synthesize_persona_panel,
)
from teamnot.customer_loop.vision import reviewer_from_environment

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class PersistentWinBrowserCommandRunner:
    """Keep one Windows Chrome/CDP session alive for a whole customer run."""

    def __init__(
        self,
        wrapper_path: str | Path = "scripts/winbrowser",
        node_path: str | Path | None = None,
        cdp_url: str | None = None,
    ):
        self.wrapper_path = Path(wrapper_path)
        self.node_path = str(
            node_path
            or os.environ.get("TEAMNOT_WINDOWS_NODE")
            or "/mnt/c/Program Files/nodejs/node.exe"
        )
        self.cdp_url = cdp_url or os.environ.get("TEAMNOT_CDP_URL") or "http://127.0.0.1:18801"
        self._explicit_cdp_url = bool(cdp_url or os.environ.get("TEAMNOT_CDP_URL"))
        self.script_path = Path(__file__).with_name("winbrowser_session.mjs")
        self._process: subprocess.Popen[str] | None = None
        self._counter = 0

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        command_list = list(command)
        payload = self._payload_from_command(command_list)
        try:
            response = self._request(payload)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(command_list, 1, stdout="", stderr=str(exc))
        stdout = json.dumps(response)
        return subprocess.CompletedProcess(
            command_list,
            0 if response.get("ok") is not False else 1,
            stdout=stdout,
            stderr="" if response.get("ok") is not False else str(response.get("error", response)),
        )

    def close(self) -> None:
        if not self._process:
            return
        try:
            self._request({"action": "close"}, timeout=10)
        except Exception:
            self._process.terminate()
        finally:
            self._process = None

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process and self._process.poll() is None:
            return self._process
        script = _path_for_windows_wrapper(self.script_path)
        args = [
                self.node_path,
                script,
                "--cdp",
                self.cdp_url,
                "--session-id",
                f"teamnot-customer-{os.getpid()}",
        ]
        if not self._explicit_cdp_url:
            args.append("--cleanup-targets")
        self._process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return self._process

    def _request(self, payload: dict, timeout: int = 75) -> dict:
        process = self._ensure_process()
        if process.stdin is None or process.stdout is None:
            raise OSError("persistent browser session did not expose stdio")
        self._counter += 1
        request = {"id": self._counter, **payload}
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        line = _readline_with_timeout(process, timeout=timeout)
        try:
            parsed = json.loads(line.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise OSError(f"persistent browser session returned non-JSON output: {line[:200]}") from exc
        return parsed if isinstance(parsed, dict) else {"ok": True, "result": parsed}

    @staticmethod
    def _payload_from_command(command: list[str]) -> dict:
        args = command[1:]
        action = _arg_value(args, "--action", args[0] if args else "status")
        payload: dict = {"action": action}
        if "--cdp" in args:
            payload["cdp"] = _arg_value(args, "--cdp")
        if action == "navigate":
            payload["url"] = _arg_value(args, "--url")
            payload["timeout"] = int(_arg_value(args, "--timeout", "30000") or "30000")
        elif action == "screenshot":
            payload["out"] = _arg_value(args, "--out")
            payload["fullPage"] = "--full-page" in args
        elif action == "importStorageState":
            payload["path"] = _arg_value(args, "--path")
        elif action == "setCookies":
            payload["cookies"] = _json_arg(args, "--cookies", [])
        elif action == "setLocalStorage":
            payload["entries"] = _json_arg(args, "--entries", [])
        elif action == "loginHint":
            payload["email"] = _arg_value(args, "--email")
            payload["password"] = _arg_value(args, "--password")
            payload["loginUrl"] = _arg_value(args, "--login-url")
            payload["workspaceId"] = _arg_value(args, "--workspace-id")
        elif action == "login":
            payload["email"] = _arg_value(args, "--email")
            payload["password"] = _arg_value(args, "--password")
            payload["loginUrl"] = _arg_value(args, "--login-url")
            payload["successUrl"] = _arg_value(args, "--success-url")
            payload["workspaceId"] = _arg_value(args, "--workspace-id")
            payload["timeout"] = int(_arg_value(args, "--timeout", "30000") or "30000")
        elif action == "assistLogin":
            payload["url"] = _arg_value(args, "--url")
            payload["loginUrl"] = _arg_value(args, "--login-url")
            payload["successUrl"] = _arg_value(args, "--success-url")
            payload["email"] = _arg_value(args, "--email")
            payload["timeout"] = int(_arg_value(args, "--timeout", "30000") or "30000")
        elif action == "viewport":
            payload["width"] = int(_arg_value(args, "--width", "390") or "390")
            payload["height"] = int(_arg_value(args, "--height", "844") or "844")
        elif action == "upload":
            payload["selector"] = _arg_value(args, "--selector")
            payload["file"] = _arg_value(args, "--file")
            payload["timeout"] = int(_arg_value(args, "--timeout", "30000") or "30000")
        elif action == "cookies":
            urls = _arg_value(args, "--urls")
            payload["urls"] = [url.strip() for url in urls.split(",") if url.strip()] if urls else []
        elif action == "eval":
            payload["expr"] = _arg_value(args, "--expr")
        return payload


class ExperienceRunner(Protocol):
    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        """Collect or ingest customer evidence."""


class ManualEvidenceRunner:
    def __init__(self, evidence_path: str | Path):
        self.evidence_path = Path(evidence_path).expanduser()

    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        if not self.evidence_path.exists():
            raise CustomerLoopRunnerError(f"Manual evidence file not found: {self.evidence_path}")
        raw = self.evidence_path.read_text(encoding="utf-8")
        evidence = CustomerEvidence(
            path=str(self.evidence_path),
            observed_behavior=_first_nonempty_line(raw),
            raw_excerpt=raw[:2000],
        )
        finding = _finding_from_manual_text(raw, evidence)
        return CustomerReport(
            profile=profile,
            target=target,
            plan=plan,
            findings=[finding] if finding else [],
            evidence=[evidence],
            summary=_first_nonempty_line(raw) or "Manual evidence ingested.",
            raw_report_path=str(self.evidence_path),
        )


class OpenClawWindowsCDPRunner:
    def __init__(
        self,
        wrapper_path: str | Path = "scripts/winbrowser",
        command_runner: CommandRunner | None = None,
    ):
        self.wrapper_path = _resolve_wrapper_path(wrapper_path)
        self._owns_persistent_session = command_runner is None
        self.command_runner = command_runner or PersistentWinBrowserCommandRunner(self.wrapper_path)

    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        try:
            if not self.wrapper_path.exists():
                raise CustomerLoopRunnerError(
                    "OpenClaw Windows CDP runner requires scripts/winbrowser. "
                    "Install or provide the wrapper, or use --runner manual --evidence FILE."
                )
            screenshots = out_dir / "screenshots"
            screenshots.mkdir(parents=True, exist_ok=True)
            first_impression = screenshots / "first-impression.png"
            full_page = screenshots / "full-page.png"
            mobile_review = screenshots / "mobile-review.png"
            first_impression_out = _path_for_windows_wrapper(first_impression)
            full_page_out = _path_for_windows_wrapper(full_page)
            mobile_review_out = _path_for_windows_wrapper(mobile_review)
            self._run(["--action", "status"])
            navigate = _parse_json_stdout(self._run(["--action", "navigate", "--url", str(target.url)]))
            self._try_run(["--action", "viewport", "--width", "1280", "--height", "900"])
            probe = _parse_json_stdout(self._run(["--action", "eval", "--expr", _CUSTOMER_PROBE_JS]))
            result = probe.get("result", probe)
            desktop_records = [
                self._try_screenshot_record(first_impression, first_impression_out, route="/", action="first_impression"),
                self._try_screenshot_record(full_page, full_page_out, full_page=True, route="/", action="full_page"),
            ]
            mobile_viewport = self._try_run(["--action", "viewport", "--width", "390", "--height", "844"])
            mobile_probe = _parse_json_stdout(self._run(["--action", "eval", "--expr", _MOBILE_PROBE_JS])).get("result", {})
            screenshot_records = [
                *desktop_records,
                self._try_screenshot_record(mobile_review, mobile_review_out, route="/", action="mobile_review"),
            ]
            screenshot_status = {
                "first_impression": screenshot_records[0].model_dump(mode="json"),
                "full_page": screenshot_records[1].model_dump(mode="json"),
                "mobile_review": screenshot_records[2].model_dump(mode="json"),
            }
            if isinstance(mobile_probe, dict):
                result["mobileProbe"] = mobile_probe
                result["mobileViewport"] = _parse_json_stdout(mobile_viewport) if mobile_viewport else {
                    "ok": False,
                    "reason": "scripts/winbrowser did not support viewport action",
                }
            markers, findings = _build_customer_findings(result, target, profile, plan)
            scores = _score_customer_readiness(result, findings)
            evidence = CustomerEvidence(
                kind="browser_observation",
                path=str(first_impression),
                screenshot_paths=[str(first_impression), str(full_page), str(mobile_review)],
                observed_behavior=_summarize_probe(result, markers),
                raw_excerpt="\n".join(markers),
                screenshot_captures=screenshot_records,
                metadata={
                    "runner": "openclaw-windows-cdp",
                    "rubric": "customer-testing-openclaw",
                    "method": "real Windows Chrome/CDP customer-readiness probe",
                    "evidence_hierarchy": [
                        "deterministic DOM/eval/performance checks",
                        "first-impression and full-page screenshots",
                        "mobile-review screenshot after viewport probe",
                        "customer-impact findings",
                    ],
                    "navigate": navigate,
                    "probe": result,
                    "scores": scores.model_dump(),
                    "screenshot_status": screenshot_status,
                },
            )
            for finding in findings:
                finding.evidence.append(evidence)
            browser_runtime = _runtime_from_response(navigate)
            _attach_screenshot_runtime(browser_runtime, screenshot_records)
            report = CustomerReport(
                profile=profile,
                target=target,
                plan=plan,
                findings=findings,
                scores=scores,
                evidence=[evidence],
                summary=(
                    "Customer-testing-openclaw browser test completed with Windows CDP. "
                    f"{len(findings)} customer-impact finding(s) identified."
                ),
                browser_runtime=browser_runtime,
                screenshot_captures=screenshot_records,
                vision_review=_review_screenshots(screenshot_records, target, profile),
            )
            _attach_visual_findings(report)
            _attach_customer_panels(report)
            return report
        finally:
            if self._owns_persistent_session and isinstance(self.command_runner, PersistentWinBrowserCommandRunner):
                self.command_runner.close()

    def _screenshot(self, path: Path) -> None:
        self._run(["--action", "screenshot", "--out", _path_for_windows_wrapper(path)])

    def _try_screenshot(self, path: Path, out: str | None = None, full_page: bool = False) -> bool:
        return self._try_screenshot_record(path, out=out, full_page=full_page).success

    def _try_screenshot_record(
        self,
        path: Path,
        out: str | None = None,
        full_page: bool = False,
        route: str = "",
        action: str = "",
    ) -> ScreenshotCaptureRecord:
        args = ["--action", "screenshot", "--out", out or _path_for_windows_wrapper(path)]
        if full_page:
            args.append("--full-page")
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        parsed: dict = {}
        failed = ""
        try:
            parsed = _parse_json_stdout(self._run(args))
        except CustomerLoopRunnerError as exc:
            failed = str(exc)
        success = path.exists()
        return ScreenshotCaptureRecord(
            path=str(path),
            route=route,
            action=action,
            method=str(parsed.get("method", "playwright" if success else "")),
            retry_count=int(parsed.get("retryCount", 0) or parsed.get("retry_count", 0) or 0),
            failed_primitive="screenshot" if failed else str(parsed.get("failedPrimitive", "")),
            fallback_reason=str(parsed.get("fallbackReason", "")),
            success=success,
            sha256=_file_sha256(path),
        )

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        command = [str(self.wrapper_path), *args]
        try:
            result = self.command_runner(command)
        except subprocess.TimeoutExpired as exc:
            raise CustomerLoopRunnerError(
                f"OpenClaw wrapper timed out: {' '.join(command)}. "
                "Use --runner manual --evidence FILE or retry after checking the Windows CDP bridge."
            ) from exc
        if result.returncode != 0:
            raise CustomerLoopRunnerError(
                f"OpenClaw wrapper failed: {' '.join(command)}\n{result.stderr.strip()}"
            )
        return result

    def _try_run(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        command = [str(self.wrapper_path), *args]
        try:
            result = self.command_runner(command)
        except subprocess.TimeoutExpired:
            return None
        if result.returncode != 0:
            return None
        parsed = _parse_json_stdout(result)
        if parsed.get("ok") is False:
            return None
        return result

    @staticmethod
    def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        action_pair = tuple(list(command)[1:3])
        timeout_seconds = 15 if action_pair == ("--action", "screenshot") else 60
        attempts = 2 if action_pair in {
            ("--action", "status"),
            ("--action", "navigate"),
            ("--action", "eval"),
            ("--action", "screenshot"),
        } else 1
        result: subprocess.CompletedProcess[str] | None = None
        for attempt in range(attempts):
            guarded_command = ["timeout", "--kill-after=5s", f"{timeout_seconds}s", *command]
            result = subprocess.run(
                guarded_command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 10,
                check=False,
            )
            if result.returncode == 0:
                return result
            if attempt == attempts - 1 or not _transient_browser_failure(result):
                return result
        return result or subprocess.CompletedProcess(command, 1, stdout="", stderr="browser command did not run")


class OpenClawWindowsInteractiveRunner(OpenClawWindowsCDPRunner):
    """Run the baseline CDP probe plus a generic customer-visible interaction."""

    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        report = super().run(target, profile, plan, out_dir)
        if report.evidence:
            report.evidence[0].metadata["runner"] = "openclaw-windows-interactive"
            report.evidence[0].metadata["method"] = (
                "real Windows Chrome/CDP customer-readiness probe plus sample/demo interaction"
            )
        screenshots = out_dir / "screenshots"
        before = screenshots / "interactive-before.png"
        after = screenshots / "interactive-after.png"
        self._try_run(["--action", "viewport", "--width", "1280", "--height", "900"])
        before_record = self._try_screenshot_record(before, route="/", action="interactive-before")
        before_screenshot_ok = before_record.success
        interaction = _parse_json_stdout(
            self._run(["--action", "eval", "--expr", _INTERACTIVE_SAMPLE_FLOW_JS])
        ).get("result", {})
        after_record = self._try_screenshot_record(after, route="/", action="interactive-after")
        after_screenshot_ok = after_record.success
        markers, findings = _build_interactive_findings(interaction if isinstance(interaction, dict) else {})
        evidence = CustomerEvidence(
            kind="browser_interaction",
            path=str(before),
            screenshot_paths=[str(before), str(after)],
            observed_behavior=_summarize_interaction(interaction if isinstance(interaction, dict) else {}, markers),
            raw_excerpt="\n".join(markers),
            screenshot_captures=[before_record, after_record],
            metadata={
                "runner": "openclaw-windows-interactive",
                "rubric": "customer-testing-openclaw",
                "method": "baseline probe plus real sample/demo click with before/after evidence",
                "interaction": interaction,
                "screenshot_status": {
                    "before": before_screenshot_ok,
                    "after": after_screenshot_ok,
                },
            },
        )
        report.evidence.append(evidence)
        for finding in findings:
            finding.evidence.append(evidence)
        report.findings.extend(findings)
        captures = _collect_screenshot_captures(report)
        report.screenshot_captures = captures
        report.vision_review = _review_screenshots(captures, target, profile)
        _attach_visual_findings(report)
        _attach_customer_panels(report)
        report.scores = _score_customer_readiness(
            report.evidence[0].metadata.get("probe", {}) if report.evidence else {},
            report.findings,
        )
        report.summary = (
            "Customer-testing-openclaw interactive browser test completed with Windows CDP. "
            f"{len(report.findings)} customer-impact finding(s) identified."
        )
        return report


class OpenClawWindowsFlowRunner(OpenClawWindowsInteractiveRunner):
    """Run the baseline probe plus a configured task-specific customer flow."""

    def __init__(
        self,
        flow_pack: CustomerFlow | CustomerFlowPack,
        wrapper_path: str | Path = "scripts/winbrowser",
        command_runner: CommandRunner | None = None,
    ):
        super().__init__(wrapper_path=wrapper_path, command_runner=command_runner)
        self.flow_pack = _normalize_flow_pack(flow_pack)

    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        report = OpenClawWindowsCDPRunner.run(self, target, profile, plan, out_dir)
        if report.evidence:
            report.evidence[0].metadata["runner"] = "openclaw-windows-flow"
            report.evidence[0].metadata["method"] = (
                "real Windows Chrome/CDP customer-readiness probe plus configured customer flow"
            )
            report.evidence[0].raw_excerpt = _mark_primary_workflow_covered_by_flow(report.evidence[0].raw_excerpt)
        screenshots = out_dir / "screenshots"
        self._try_run(["--action", "viewport", "--width", "1280", "--height", "900"])
        markers: list[str] = []
        findings: list[CustomerFinding] = []
        flow_results: list[dict] = []
        screenshot_paths: list[str] = []
        screenshot_captures: list[ScreenshotCaptureRecord] = []
        for flow_index, flow in enumerate(self.flow_pack.flows, start=1):
            flow_slug = _slug(flow.name)
            if self.flow_pack.reset_between_flows or flow.start_url:
                start_url = _flow_url(target, flow.start_url)
                navigate = _parse_json_stdout(self._run(["--action", "navigate", "--url", start_url]))
                flow_results.append({
                    "flow": flow.name,
                    "event": "navigate",
                    "url": start_url,
                    "result": navigate,
                })
            markers.append(f"STEP_PASS|flow-{flow_slug}-start|started customer flow: {flow.name}")
            for step_index, step in enumerate(flow.steps, start=1):
                try:
                    result = self._run_flow_step(step, target)
                except CustomerLoopRunnerError as exc:
                    if step.action == "upload":
                        raise
                    result = {
                        "id": step.id,
                        "action": step.action,
                        "passed": False,
                        "summary": f"flow step failed at browser layer: {exc}",
                    }
                flow_results.append({"flow": flow.name, **result})
                shot = screenshots / f"flow-{flow_index:02d}-{step_index:02d}-{flow_slug}-{_slug(step.id)}.png"
                screenshot_record = self._try_screenshot_record(shot, route=flow.start_url, action=step.id)
                screenshot_ok = screenshot_record.success
                result["screenshot_ok"] = screenshot_ok
                result["screenshot_capture"] = screenshot_record.model_dump(mode="json")
                screenshot_paths.append(str(shot))
                screenshot_captures.append(screenshot_record)
                marker_id = f"flow-{flow_slug}-{step.id}"
                if result.get("passed"):
                    markers.append(f"STEP_PASS|{marker_id}|{result.get('summary', step.action)}")
                    continue
                if result.get("skipped"):
                    markers.append(f"STEP_SKIP|{marker_id}|{result.get('summary', step.action)}")
                    continue
                markers.append(f"STEP_FAIL|{marker_id}|{result.get('summary', step.action)}")
                findings.append(_browser_finding(
                    f"flow-{flow_slug}-{_slug(step.id)}-failed",
                    f"Configured customer flow step failed: {flow.name} / {step.id}",
                    CustomerSeverity.high,
                    "The customer cannot complete the configured real workflow step.",
                    "The product may look ready in a demo path but still fail in the actual customer workflow.",
                    "Every customer attempting this configured workflow.",
                    f"Fix the product or flow target so `{flow.name}` step `{step.id}` can complete.",
                    core=True,
                ))
                break
        evidence = CustomerEvidence(
            kind="browser_flow",
            path=screenshot_paths[0] if screenshot_paths else "",
            screenshot_paths=screenshot_paths,
            observed_behavior=_summarize_flow_pack(self.flow_pack, markers),
            raw_excerpt="\n".join(markers),
            screenshot_captures=screenshot_captures,
            metadata={
                "runner": "openclaw-windows-flow",
                "rubric": "customer-testing-openclaw",
                "method": "configured browser flow pack with per-step screenshots",
                "flow_pack": self.flow_pack.model_dump(mode="json"),
                "flows": flow_results,
            },
        )
        report.evidence.append(evidence)
        for finding in findings:
            finding.evidence.append(evidence)
        report.findings.extend(findings)
        captures = _collect_screenshot_captures(report)
        report.screenshot_captures = captures
        report.vision_review = _review_screenshots(captures, target, profile)
        _attach_visual_findings(report)
        _attach_customer_panels(report)
        report.scores = _score_customer_readiness(
            report.evidence[0].metadata.get("probe", {}) if report.evidence else {},
            report.findings,
        )
        report.summary = (
            "Customer-testing-openclaw configured browser flow completed with Windows CDP. "
            f"{len(report.findings)} customer-impact finding(s) identified."
        )
        return report

    def _run_flow_step(self, step: CustomerFlowStep, target: ExperienceTarget) -> dict:
        if step.action == "navigate":
            target_url = _flow_url(target, step.url or step.value or step.text)
            navigated = _parse_json_stdout(self._run(["--action", "navigate", "--url", target_url]))
            return {
                "id": step.id,
                "action": step.action,
                "passed": True,
                "summary": f"navigated to {target_url}",
                "result": navigated,
            }
        if step.action == "upload":
            if step.file is None:
                raise CustomerLoopRunnerError(f"Flow step {step.id} upload requires file")
            try:
                uploaded = _parse_json_stdout(self._run([
                    "--action", "upload",
                    "--selector", step.selector,
                    "--file", _path_for_windows_wrapper(step.file),
                ]))
            except CustomerLoopRunnerError as exc:
                raise CustomerLoopRunnerError(
                    f"Flow step {step.id} requires browser wrapper upload support (`--action upload`). "
                    "Update the OpenClaw Windows browser wrapper or replace this generated step with "
                    "manual evidence before claiming full workflow coverage. "
                    f"Original error: {exc}"
                ) from exc
            return {
                "id": step.id,
                "action": step.action,
                "passed": True,
                "summary": f"uploaded {step.file} into {step.selector}",
                "result": uploaded,
            }
        if step.action == "checkpoint":
            return {
                "id": step.id,
                "action": step.action,
                "passed": None,
                "skipped": True,
                "summary": step.description or step.id,
            }
        result = _parse_json_stdout(
            self._run(["--action", "eval", "--expr", _flow_step_expr(step)])
        ).get("result", {})
        if not isinstance(result, dict):
            result = {"passed": False, "summary": f"{step.action} returned non-object result", "raw": result}
        return {"id": step.id, "action": step.action, **result}


class OpenClawWindowsSessionRunner(OpenClawWindowsCDPRunner):
    """Explore the current product, derive flows, execute them, and report in one runner call."""

    def __init__(
        self,
        wrapper_path: str | Path = "scripts/winbrowser",
        command_runner: CommandRunner | None = None,
        file_fixture_path: str | Path | None = None,
    ):
        super().__init__(wrapper_path=wrapper_path, command_runner=command_runner)
        self._owns_persistent_session = command_runner is None
        if command_runner is None:
            self.command_runner = PersistentWinBrowserCommandRunner(self.wrapper_path)
        self.file_fixture_path = Path(file_fixture_path).expanduser() if file_fixture_path else None

    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        try:
            exploration = _safe_explore_product(target, profile, self.wrapper_path, self.command_runner)
            planned_routes = routes_from_exploration(exploration)
            inspected = _safe_inspect_customer_flow_pack(
                target,
                profile,
                planned_routes,
                self.wrapper_path,
                self.command_runner,
            )
            runnable = make_flow_pack_runnable(inspected, file_fixture_path=self.file_fixture_path)
            screen_exploration = self._explore_screens(target, planned_routes, out_dir)
            save_yaml(exploration, out_dir / "product_exploration.yaml")
            save_yaml(screen_exploration, out_dir / "screen_exploration.yaml")
            save_yaml(inspected, out_dir / "inspected_flow.yaml")
            save_yaml(runnable, out_dir / "runnable_flow.yaml")

            try:
                report = OpenClawWindowsFlowRunner(
                    runnable,
                    wrapper_path=self.wrapper_path,
                    command_runner=self.command_runner,
                ).run(target, profile, plan, out_dir)
            except CustomerLoopRunnerError as exc:
                report = _degraded_browser_report(target, profile, plan, "openclaw-windows-session", exc)
            if report.evidence:
                report.evidence[0].metadata["runner"] = "openclaw-windows-session"
                report.evidence[0].metadata["method"] = (
                    "fresh product exploration, inspected flow planning, and real Windows Chrome flow execution"
                )
                report.evidence[-1].metadata["product_exploration"] = exploration.model_dump(mode="json")
                report.evidence[-1].metadata["screen_exploration"] = screen_exploration
                report.evidence[-1].metadata["inspected_flow_pack"] = inspected.model_dump(mode="json")
                report.evidence[-1].metadata["runnable_flow_pack_path"] = str(out_dir / "runnable_flow.yaml")
            screen_evidence, screen_findings = _screen_exploration_evidence(screen_exploration)
            report.evidence.append(screen_evidence)
            for finding in screen_findings:
                finding.evidence.append(screen_evidence)
            report.findings.extend(screen_findings)
            captures = _collect_screenshot_captures(report)
            report.screenshot_captures = captures
            report.vision_review = _review_screenshots(captures, target, profile)
            _attach_visual_findings(report)
            _attach_customer_panels(report)
            (out_dir / "flow_refinement_report.md").write_text(
                render_flow_refinement_report(inspected, runnable, report),
                encoding="utf-8",
            )
            report.summary = (
                "Customer-testing-openclaw session completed with fresh product exploration, "
                "flow inspection, and real Windows CDP flow execution. "
                f"{_product_finding_count(report)} customer-impact finding(s) identified; "
                f"{len(report.findings) - _product_finding_count(report)} TeamNoT coverage note(s) separated."
            )
            return report
        finally:
            if self._owns_persistent_session and isinstance(self.command_runner, PersistentWinBrowserCommandRunner):
                self.command_runner.close()

    def _explore_screens(
        self,
        target: ExperienceTarget,
        routes: list[str],
        out_dir: Path,
    ) -> dict:
        screenshots = out_dir / "screenshots" / "screen-exploration"
        screenshots.mkdir(parents=True, exist_ok=True)
        route_results: list[dict] = []
        discovered_routes = set(routes)
        action_budget = 12
        actions_used = 0
        self._try_run(["--action", "viewport", "--width", "1280", "--height", "900"])
        for route_index, route in enumerate(routes[:5], start=1):
            if actions_used >= action_budget:
                break
            route_url = _flow_url(target, route)
            try:
                navigate = _parse_json_stdout(self._run(["--action", "navigate", "--url", route_url]))
            except CustomerLoopRunnerError as exc:
                route_results.append({
                    "route": route,
                    "url": route_url,
                    "navigate": {"ok": False, "error": str(exc)},
                    "entry_screenshot": "",
                    "entry_screenshot_ok": False,
                    "entry_hash": "",
                    "visible_action_count": 0,
                    "safe_action_count": 0,
                    "actions": [],
                })
                continue
            entry_shot = screenshots / f"route-{route_index:02d}-{_slug(route)}-entry.png"
            entry_record = self._capture_screenshot_for_exploration(entry_shot, route=route, action="entry")
            entry_screenshot_ok = entry_record.success
            entry_hash = _file_sha256(entry_shot)
            candidates_raw = _parse_json_stdout(
                self._run(["--action", "eval", "--expr", _SCREEN_ACTION_DISCOVERY_JS])
            ).get("result", [])
            candidates = candidates_raw if isinstance(candidates_raw, list) else []
            safe_candidates = _rank_screen_actions([
                candidate for candidate in candidates
                if isinstance(candidate, dict) and _safe_screen_action(candidate, target)
            ])
            action_results: list[dict] = []
            for action_index, candidate in enumerate(safe_candidates[:4], start=1):
                if actions_used >= action_budget:
                    break
                actions_used += 1
                before_shot = screenshots / (
                    f"route-{route_index:02d}-action-{action_index:02d}-{_slug(candidate.get('text', '') or candidate.get('selector', 'action'))}-before.png"
                )
                after_shot = screenshots / (
                    f"route-{route_index:02d}-action-{action_index:02d}-{_slug(candidate.get('text', '') or candidate.get('selector', 'action'))}-after.png"
                )
                try:
                    self._parse_or_reset_navigation(target, route)
                except CustomerLoopRunnerError:
                    pass
                before_record = self._capture_screenshot_for_exploration(
                    before_shot,
                    route=route,
                    action=f"{candidate.get('text', '') or candidate.get('selector', 'action')}-before",
                )
                before_screenshot_ok = before_record.success
                before_hash = _file_sha256(before_shot)
                try:
                    click_result = _parse_json_stdout(
                        self._run(["--action", "eval", "--expr", _screen_action_click_expr(candidate)])
                    ).get("result", {})
                except CustomerLoopRunnerError as exc:
                    click_result = {
                        "ok": False,
                        "summary": f"screen action failed: {exc}",
                        "beforeUrl": route_url,
                        "afterUrl": route_url,
                        "beforeTextSample": "",
                        "afterTextSample": "",
                    }
                after_record = self._capture_screenshot_for_exploration(
                    after_shot,
                    route=route,
                    action=f"{candidate.get('text', '') or candidate.get('selector', 'action')}-after",
                )
                after_screenshot_ok = after_record.success
                after_hash = _file_sha256(after_shot)
                if not isinstance(click_result, dict):
                    click_result = {"ok": False, "summary": "screen action returned non-object result"}
                after_url = str(click_result.get("afterUrl", ""))
                route_after = _route_from_url(after_url, target)
                if route_after:
                    discovered_routes.add(route_after)
                visual_changed = bool(before_hash and after_hash and before_hash != after_hash)
                text_changed = click_result.get("beforeTextSample") != click_result.get("afterTextSample")
                url_changed = click_result.get("beforeUrl") != click_result.get("afterUrl")
                action_results.append({
                    "action": _clean_screen_action(candidate),
                    "passed": bool(click_result.get("ok")),
                    "url_changed": bool(url_changed),
                    "text_changed": bool(text_changed),
                    "visual_changed": visual_changed,
                    "before_screenshot": str(before_shot),
                    "after_screenshot": str(after_shot),
                    "before_screenshot_ok": before_screenshot_ok,
                    "after_screenshot_ok": after_screenshot_ok,
                    "before_hash": before_hash,
                    "after_hash": after_hash,
                    "screenshot_captures": [
                        before_record.model_dump(mode="json"),
                        after_record.model_dump(mode="json"),
                    ],
                    "result": click_result,
                })
            route_results.append({
                "route": route,
                "url": route_url,
                "navigate": navigate,
                "entry_screenshot": str(entry_shot),
                "entry_screenshot_ok": entry_screenshot_ok,
                "entry_screenshot_capture": entry_record.model_dump(mode="json"),
                "entry_hash": entry_hash,
                "visible_action_count": len(candidates),
                "safe_action_count": len(safe_candidates),
                "actions": action_results,
            })
        return {
            "method": "Windows Chrome/CDP screen exploration with screenshots before and after safe customer actions",
            "routes_seeded": routes,
            "routes_discovered": sorted(discovered_routes),
            "action_budget": action_budget,
            "actions_executed": actions_used,
            "routes": route_results,
        }

    def _parse_or_reset_navigation(self, target: ExperienceTarget, route: str) -> dict:
        return _parse_json_stdout(self._run(["--action", "navigate", "--url", _flow_url(target, route)]))

    def _try_screenshot_for_exploration(self, path: Path) -> bool:
        return self._capture_screenshot_for_exploration(path).success

    def _capture_screenshot_for_exploration(
        self,
        path: Path,
        route: str = "",
        action: str = "",
    ) -> ScreenshotCaptureRecord:
        try:
            return self._try_screenshot_record(path, route=route, action=action)
        except CustomerLoopRunnerError:
            return ScreenshotCaptureRecord(
                path=str(path),
                route=route,
                action=action,
                failed_primitive="screenshot",
                success=False,
            )


class OpenClawWindowsResearcherRunner(OpenClawWindowsSessionRunner):
    """Run a broader customer-research loop with observation, planning, and branch evidence."""

    def __init__(
        self,
        wrapper_path: str | Path = "scripts/winbrowser",
        command_runner: CommandRunner | None = None,
        file_fixture_path: str | Path | None = None,
        seeded_state_path: str | Path | None = None,
        seeded_state: SeededCustomerState | None = None,
    ):
        super().__init__(
            wrapper_path=wrapper_path,
            command_runner=command_runner,
            file_fixture_path=file_fixture_path,
        )
        self.seeded_state_path = Path(seeded_state_path).expanduser() if seeded_state_path else None
        self.seeded_state = seeded_state

    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
        try:
            seeded_state = self._seeded_state_contract()
            seeded_state_result = self._apply_seeded_state(target, seeded_state) if seeded_state else None
            browser_auth_result = self._attempt_browser_assisted_auth(target, seeded_state)
            exploration = _safe_explore_product(target, profile, self.wrapper_path, self.command_runner)
            planned_routes = routes_from_exploration(exploration, max_routes=9)
            research_brain_method = self._research_brain_pass
            supports_browser_auth = "browser_auth_result" in signature(research_brain_method).parameters
            screen_exploration: dict | None = None
            if supports_browser_auth:
                research_brain = research_brain_method(
                    target,
                    profile,
                    planned_routes,
                    out_dir,
                    seeded_state,
                    browser_auth_result=browser_auth_result,
                )
            else:
                screen_exploration = self._explore_screens(target, planned_routes, out_dir)
                research_brain = research_brain_method(target, profile, planned_routes, out_dir, seeded_state)
            if seeded_state_result:
                research_brain["seeded_state_application"] = seeded_state_result
            research_brain["browser_assisted_auth"] = browser_auth_result
            save_yaml(exploration, out_dir / "product_exploration.yaml")
            save_yaml(research_brain, out_dir / "research_brain.yaml")
            auth_blocker = _missing_required_auth_blocker(research_brain, seeded_state)
            if auth_blocker:
                raise CustomerLoopRunnerError(auth_blocker)
            routes_for_flow = _dedupe_strings([
                *planned_routes,
                *((screen_exploration or {}).get("routes_discovered", [])),
                *research_brain.get("routes_discovered", []),
            ])[:9]
            if screen_exploration is None:
                screen_exploration = self._explore_screens(target, routes_for_flow, out_dir)
            routes_for_flow = _dedupe_strings([
                *routes_for_flow,
                *screen_exploration.get("routes_discovered", []),
            ])[:9]
            save_yaml(screen_exploration, out_dir / "screen_exploration.yaml")
            inspected = _safe_inspect_customer_flow_pack(
                target,
                profile,
                routes_for_flow,
                self.wrapper_path,
                self.command_runner,
            )
            runnable = make_flow_pack_runnable(inspected, file_fixture_path=self.file_fixture_path)
            save_yaml(inspected, out_dir / "inspected_flow.yaml")
            save_yaml(runnable, out_dir / "runnable_flow.yaml")

            try:
                report = OpenClawWindowsFlowRunner(
                    runnable,
                    wrapper_path=self.wrapper_path,
                    command_runner=self.command_runner,
                ).run(target, profile, plan, out_dir)
            except CustomerLoopRunnerError as exc:
                report = _degraded_browser_report(target, profile, plan, "openclaw-windows-researcher", exc)
            if report.evidence:
                report.evidence[0].metadata["runner"] = "openclaw-windows-researcher"
                report.evidence[0].metadata["method"] = (
                    "agentic customer researcher runtime: route exploration, screen actions, form/adversarial branches, "
                    "and configured flow execution in real Windows Chrome/CDP"
                )
                report.evidence[-1].metadata["product_exploration"] = exploration.model_dump(mode="json")
                report.evidence[-1].metadata["screen_exploration"] = screen_exploration
                report.evidence[-1].metadata["research_brain"] = research_brain
                report.evidence[-1].metadata["inspected_flow_pack"] = inspected.model_dump(mode="json")
                report.evidence[-1].metadata["runnable_flow_pack_path"] = str(out_dir / "runnable_flow.yaml")
            for evidence, findings in (
                _screen_exploration_evidence(screen_exploration),
                _research_brain_evidence(research_brain),
            ):
                report.evidence.append(evidence)
                for finding in findings:
                    finding.evidence.append(evidence)
                report.findings.extend(findings)
            report.seeded_state = seeded_state
            if not report.seeded_state and research_brain.get("seeded_state_status") == "browser_context":
                report.seeded_state = SeededCustomerState(
                    adapter_status="browser_context",
                    cleanup_notes="Authenticated state was reused from the attached browser context.",
                )
            report.action_memory = [
                ResearchActionMemory.model_validate(item)
                for item in research_brain.get("action_memory", [])
                if isinstance(item, dict)
            ]
            captures = _collect_screenshot_captures(report)
            report.screenshot_captures = captures
            report.vision_review = _review_screenshots(captures, target, profile)
            _attach_visual_findings(report)
            report.browser_runtime = _merge_runtime(report.browser_runtime, research_brain.get("browser_runtime"))
            _reconcile_product_findings_from_research_depth(report)
            _attach_customer_panels(report)
            (out_dir / "flow_refinement_report.md").write_text(
                render_flow_refinement_report(inspected, runnable, report),
                encoding="utf-8",
            )
            report.summary = (
                "Customer-testing-openclaw researcher run completed with product exploration, "
                "screen-level action evidence, form/adversarial branch testing, and real Windows CDP flow execution. "
                f"{_product_finding_count(report)} customer-impact finding(s) identified; "
                f"{len(report.findings) - _product_finding_count(report)} TeamNoT coverage note(s) separated."
            )
            return report
        finally:
            if self._owns_persistent_session and isinstance(self.command_runner, PersistentWinBrowserCommandRunner):
                self.command_runner.close()

    def _research_brain_pass(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        routes: list[str],
        out_dir: Path,
        seeded_state: SeededCustomerState | None = None,
        browser_auth_result: dict | None = None,
    ) -> dict:
        screenshots = out_dir / "screenshots" / "research-brain"
        screenshots.mkdir(parents=True, exist_ok=True)
        action_budget = 18
        actions_used = 0
        route_results: list[dict] = []
        discovered_routes = set(routes)
        hypotheses = _research_hypotheses(profile)
        action_memory: list[ResearchActionMemory] = []
        browser_runtime: BrowserRuntimeMetadata | None = None
        browser_auth_result = browser_auth_result or self._attempt_browser_assisted_auth(target, seeded_state)
        self._try_run(["--action", "viewport", "--width", "1280", "--height", "900"])
        for route_index, route in enumerate(routes[:6], start=1):
            if actions_used >= action_budget:
                break
            route_url = _flow_url(target, route)
            try:
                navigate = _parse_json_stdout(self._run(["--action", "navigate", "--url", route_url]))
                browser_runtime = _merge_runtime(browser_runtime, navigate)
                observation = _parse_json_stdout(
                    self._run(["--action", "eval", "--expr", _RESEARCH_OBSERVE_JS])
                ).get("result", {})
            except CustomerLoopRunnerError as exc:
                route_results.append({
                    "route": route,
                    "url": route_url,
                    "navigate": {"ok": False, "error": str(exc)},
                    "observation": {},
                    "entry_screenshot": "",
                    "entry_screenshot_ok": False,
                    "planned_actions": [],
                    "actions": [],
                })
                continue
            if not isinstance(observation, dict):
                observation = {}
            entry_shot = screenshots / f"route-{route_index:02d}-{_slug(route)}-observe.png"
            entry_record = self._capture_screenshot_for_exploration(entry_shot, route=route, action="observe")
            entry_screenshot_ok = entry_record.success
            planned_actions = suppress_repeated_noops(
                route,
                _research_actions_from_observation(observation, target),
                action_memory,
            )
            action_results: list[dict] = []
            for action_index, action in enumerate(planned_actions[:5], start=1):
                if actions_used >= action_budget:
                    break
                actions_used += 1
                before_shot = screenshots / (
                    f"route-{route_index:02d}-action-{action_index:02d}-{_slug(action.get('id', 'action'))}-before.png"
                )
                after_shot = screenshots / (
                    f"route-{route_index:02d}-action-{action_index:02d}-{_slug(action.get('id', 'action'))}-after.png"
                )
                try:
                    self._parse_or_reset_navigation(target, route)
                except CustomerLoopRunnerError:
                    pass
                before_record = self._capture_screenshot_for_exploration(
                    before_shot,
                    route=route,
                    action=f"{action.get('id', 'action')}-before",
                )
                before_screenshot_ok = before_record.success
                before_hash = _file_sha256(before_shot)
                try:
                    result = _parse_json_stdout(
                        self._run(["--action", "eval", "--expr", _research_action_expr(action)])
                    ).get("result", {})
                except CustomerLoopRunnerError as exc:
                    result = {
                        "ok": False,
                        "summary": f"research action failed: {exc}",
                        "beforeUrl": route_url,
                        "afterUrl": route_url,
                        "beforeTextSample": "",
                        "afterTextSample": "",
                    }
                after_record = self._capture_screenshot_for_exploration(
                    after_shot,
                    route=route,
                    action=f"{action.get('id', 'action')}-after",
                )
                after_screenshot_ok = after_record.success
                after_hash = _file_sha256(after_shot)
                if not isinstance(result, dict):
                    result = {"ok": False, "summary": "research action returned non-object result"}
                route_after = _route_from_url(str(result.get("afterUrl", "")), target)
                if route_after:
                    discovered_routes.add(route_after)
                action_result = {
                    "action": action,
                    "passed": bool(result.get("ok")),
                    "url_changed": result.get("beforeUrl") != result.get("afterUrl"),
                    "text_changed": result.get("beforeTextSample") != result.get("afterTextSample"),
                    "visual_changed": bool(before_hash and after_hash and before_hash != after_hash),
                    "before_screenshot": str(before_shot),
                    "after_screenshot": str(after_shot),
                    "before_screenshot_ok": before_screenshot_ok,
                    "after_screenshot_ok": after_screenshot_ok,
                    "screenshot_captures": [
                        before_record.model_dump(mode="json"),
                        after_record.model_dump(mode="json"),
                    ],
                    "result": result,
                }
                action_results.append(action_result)
                action_memory.append(action_memory_from_result(route, str(observation), action, action_result))
            route_results.append({
                "route": route,
                "url": route_url,
                "navigate": navigate,
                "observation": observation,
                "entry_screenshot": str(entry_shot),
                "entry_screenshot_ok": entry_screenshot_ok,
                "entry_screenshot_capture": entry_record.model_dump(mode="json"),
                "planned_actions": planned_actions,
                "actions": action_results,
            })
        return {
            "method": "agentic customer researcher loop using Windows CDP observations, planned branches, realistic form fills, and before/after evidence",
            "seeded_state_path": str(self.seeded_state_path) if self.seeded_state_path else "",
            "state_policy": (
                "seeded account/state available" if seeded_state else
                "authenticated browser context reused" if _seed_result_applied(browser_auth_result) else
                "no seeded account/state provided; authenticated product depth remains a coverage gap"
            ),
            "seeded_state_status": (
                seeded_state.adapter_status if seeded_state else
                "browser_context" if _seed_result_applied(browser_auth_result) else
                "absent"
            ),
            "browser_assisted_auth": browser_auth_result,
            "browser_runtime": browser_runtime.model_dump(mode="json") if browser_runtime else {},
            "action_memory": [item.model_dump(mode="json") for item in action_memory],
            "hypotheses": hypotheses,
            "routes_seeded": routes,
            "routes_discovered": sorted(discovered_routes),
            "action_budget": action_budget,
            "actions_executed": actions_used,
            "routes": route_results,
        }

    def _seeded_state_contract(self) -> SeededCustomerState | None:
        if self.seeded_state:
            return self.seeded_state
        if self.seeded_state_path:
            from teamnot.customer_loop.io import load_seeded_state

            state = load_seeded_state(self.seeded_state_path)
            if state.storage_state_path and not state.storage_state_path.is_absolute():
                state.storage_state_path = (self.seeded_state_path.parent / state.storage_state_path).resolve()
            return state
        return None

    def _attempt_browser_assisted_auth(
        self,
        target: ExperienceTarget,
        seeded_state: SeededCustomerState | None,
    ) -> dict:
        if seeded_state and seeded_state.adapter_status == "applied":
            return {"ok": True, "action": "assistLogin", "seededStateApplied": True, "reason": "seeded state already applied"}
        account = seeded_state.test_account if seeded_state else None
        args = [
            "--action", "assistLogin",
            "--url", str(target.url),
            "--success-url", str(target.url),
        ]
        if account and account.email:
            args.extend(["--email", account.email])
        return self._try_seed_command(args)

    def _apply_seeded_state(self, target: ExperienceTarget, seeded_state: SeededCustomerState) -> dict:
        results: list[dict] = []
        status = "unsupported"
        blocker = ""
        if seeded_state.storage_state_path:
            results.append(self._try_seed_command([
                "--action", "importStorageState",
                "--path", _path_for_windows_wrapper(seeded_state.storage_state_path),
            ]))
        if seeded_state.cookies:
            results.append(self._try_seed_command([
                "--action", "setCookies",
                "--cookies", json.dumps([cookie.model_dump(mode="json", by_alias=True) for cookie in seeded_state.cookies]),
            ]))
        if seeded_state.local_storage:
            results.append(self._try_seed_command([
                "--action", "setLocalStorage",
                "--entries", json.dumps([entry.model_dump(mode="json") for entry in seeded_state.local_storage]),
            ]))
        account = seeded_state.test_account
        if account:
            login_url = account.login_url or seeded_state.login_url or str(target.url)
            if account.email and account.password:
                results.append(self._try_seed_command([
                    "--action", "login",
                    "--email", account.email,
                    "--password", account.password,
                    "--login-url", login_url,
                    "--success-url", str(target.url),
                    "--workspace-id", account.workspace_id or seeded_state.workspace_id,
                ]))
            else:
                results.append(self._try_seed_command([
                    "--action", "loginHint",
                    "--email", account.email,
                    "--login-url", login_url,
                    "--workspace-id", account.workspace_id or seeded_state.workspace_id,
                ]))
        if results and any(_seed_result_applied(item) for item in results):
            status = "applied"
        elif results:
            blocker = "; ".join(str(item.get("unsupportedBlocker") or item.get("error") or item) for item in results)
        else:
            status = "metadata_only"
            blocker = "Seeded state fixture did not include importable storage, cookies, localStorage, or login hint."
        seeded_state.adapter_status = status
        seeded_state.unsupported_blocker = blocker
        return {"status": status, "unsupported_blocker": blocker, "results": results}

    def _try_seed_command(self, args: list[str]) -> dict:
        command = [str(self.wrapper_path), *args]
        action = args[1] if len(args) > 1 and args[0] == "--action" else args[0]
        try:
            result = self.command_runner(command)
        except subprocess.TimeoutExpired:
            return {"ok": False, "action": action, "unsupportedBlocker": f"adapter timed out during {action}"}
        if result.returncode != 0:
            parsed = _parse_seed_stdout(result, action)
            if parsed:
                return parsed
            return {
                "ok": False,
                "action": action,
                "unsupportedBlocker": result.stderr.strip() or f"adapter did not accept {action}",
            }
        try:
            parsed = json.loads((result.stdout or "").strip() or "{}")
        except json.JSONDecodeError:
            return {
                "ok": False,
                "action": action,
                "unsupportedBlocker": f"adapter returned non-JSON output for {action}: {result.stdout[:200]}",
            }
        return parsed if isinstance(parsed, dict) else {"ok": True, "action": action, "result": parsed}


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip(" #\t")
        if cleaned:
            return cleaned
    return ""


def _parse_json_stdout(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        parsed = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError as exc:
        raise CustomerLoopRunnerError(f"OpenClaw wrapper returned non-JSON output: {result.stdout[:200]}") from exc
    if isinstance(parsed, dict) and parsed.get("ok") is False:
        raise CustomerLoopRunnerError(f"OpenClaw wrapper reported failure: {parsed}")
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _arg_value(args: Sequence[str], name: str, default: str = "") -> str:
    try:
        index = list(args).index(name)
    except ValueError:
        return default
    return str(args[index + 1]) if index + 1 < len(args) else default


def _json_arg(args: Sequence[str], name: str, default):
    raw = _arg_value(args, name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _seed_result_applied(result: dict) -> bool:
    if result.get("seededStateApplied") is True:
        return True
    for key in ("cookiesApplied", "localStorageValuesApplied", "localStorageOriginsApplied"):
        value = result.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return True
    return False


def _parse_seed_stdout(result: subprocess.CompletedProcess[str], action: str) -> dict:
    try:
        parsed = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {"ok": True, "action": action, "result": parsed}
    parsed.setdefault("action", action)
    return parsed


def _runtime_from_response(response: dict | None) -> BrowserRuntimeMetadata:
    data = response or {}
    cdp = str(data.get("cdp", "") or data.get("cdpUrl", ""))
    port = None
    if cdp:
        try:
            parsed_port = urlparse(cdp).port
            port = int(parsed_port) if parsed_port else None
        except ValueError:
            port = None
    return BrowserRuntimeMetadata(
        cdp_url=cdp,
        cdp_port=port,
        session_id=str(data.get("sessionId", "") or data.get("session_id", "")),
        profile_dir=str(data.get("profileDir", "") or data.get("userDataDir", "")),
        page_url=str(data.get("url", "") or data.get("dedicatedUrl", "")),
        target_id=str(data.get("targetId", "") or data.get("target_id", "")),
        page_count=int(data["pages"]) if isinstance(data.get("pages"), int) else None,
        pinned_target=str(data.get("pinnedTarget", "")),
        screenshot_method=str(data.get("method", "")),
        failed_primitive=str(data.get("failedPrimitive", "")),
        adapter_blocker=str(data.get("unsupportedBlocker", "") or data.get("error", "")),
        raw=data,
    )


def _merge_runtime(current: BrowserRuntimeMetadata | None, response: dict | None) -> BrowserRuntimeMetadata:
    incoming = _runtime_from_response(response)
    if current is None:
        return incoming
    data = current.model_dump()
    for key, value in incoming.model_dump().items():
        if value not in ("", None, {}, []):
            data[key] = value
    return BrowserRuntimeMetadata.model_validate(data)


def _attach_screenshot_runtime(
    runtime: BrowserRuntimeMetadata,
    screenshots: Sequence[ScreenshotCaptureRecord],
) -> None:
    for capture in screenshots:
        if capture.method and not runtime.screenshot_method:
            runtime.screenshot_method = capture.method
        if capture.failed_primitive and not runtime.failed_primitive:
            runtime.failed_primitive = capture.failed_primitive
        if runtime.screenshot_method and runtime.failed_primitive:
            return


def _attach_customer_panels(report: CustomerReport) -> None:
    report.persona_lenses = synthesize_persona_panel(report)
    report.jtbd_forces = synthesize_jtbd_forces(report)


def _product_finding_count(report: CustomerReport) -> int:
    return len([
        finding for finding in report.findings
        if not finding.id.startswith(("screen-exploration-", "research-brain-", "browser-runtime-"))
    ])


def _review_screenshots(
    captures: list[ScreenshotCaptureRecord],
    target: ExperienceTarget,
    profile: CustomerProfile,
) -> VisionReviewArtifact:
    return reviewer_from_environment(target=target, profile=profile).review(captures)


def _attach_visual_findings(report: CustomerReport) -> None:
    review = report.vision_review
    if not review or not review.visual_findings:
        return
    existing_ids = {finding.id for finding in report.findings}
    for index, visual in enumerate(review.visual_findings, start=1):
        finding_id = f"vision-{_slug(visual.title) or index}"
        if finding_id in existing_ids:
            continue
        evidence = CustomerEvidence(
            kind="model_vision",
            path=visual.evidence_paths[0] if visual.evidence_paths else "",
            screenshot_paths=visual.evidence_paths,
            observed_behavior=visual.customer_interpretation or visual.title,
            raw_excerpt=visual.recommendation,
            metadata={
                "runner": "model-vision",
                "model_worker": review.model_worker,
                "review_kind": review.review_kind,
                "action_hint": visual.action_hint,
            },
        )
        report.evidence.append(evidence)
        report.findings.append(_browser_finding(
            finding_id,
            visual.title,
            visual.severity,
            visual.customer_interpretation,
            "The visual presentation can change customer trust, comprehension, or ability to proceed.",
            "Every customer who encounters the reviewed screen state.",
            visual.recommendation or visual.action_hint,
            confidence=visual.confidence,
            trust=visual.severity in {CustomerSeverity.critical, CustomerSeverity.high},
            core=visual.severity in {CustomerSeverity.critical, CustomerSeverity.high, CustomerSeverity.medium},
        ))
        report.findings[-1].evidence.append(evidence)
        existing_ids.add(finding_id)


def _reconcile_product_findings_from_research_depth(report: CustomerReport) -> None:
    deep_text = _searchable_text(_observed_research_text(report))
    if not deep_text:
        return
    suppress_ids: set[str] = set()
    trust_terms = [*_TRUST_CUE_TERMS, *_profile_trust_terms(report.profile)]
    if _contains_any(deep_text, trust_terms):
        suppress_ids.add("missing-trust-copy")
    if _contains_any(deep_text, _ADOPTION_CUE_TERMS):
        suppress_ids.add("missing-adoption-cues")
    if _contains_any(deep_text, _ACTIONABILITY_CUE_TERMS):
        suppress_ids.add("weak-recommendation-clarity")
    if not suppress_ids:
        return
    report.findings = [finding for finding in report.findings if finding.id not in suppress_ids]
    report.scores = _score_customer_readiness({"failedResources": []}, report.findings)
    for evidence in report.evidence:
        if not evidence.raw_excerpt:
            continue
        raw = evidence.raw_excerpt
        if "missing-trust-copy" in suppress_ids:
            raw = raw.replace(
                "STEP_FAIL|trust-copy|expected privacy/data/trust cues -> none detected",
                "STEP_PASS|trust-copy|trust/safety cues found in observed route evidence",
            )
        if "missing-adoption-cues" in suppress_ids:
            raw = raw.replace(
                "STEP_FAIL|adoption-readiness|expected pricing/support/sample/demo/onboarding cues -> none detected",
                "STEP_PASS|adoption-readiness|support/contact/onboarding cues found in observed route evidence",
            )
        if "weak-recommendation-clarity" in suppress_ids:
            raw = raw.replace(
                "STEP_FAIL|recommendation-clarity|expected share/export/team/client cues -> none detected",
                "STEP_PASS|recommendation-clarity|action/share/output cues found in observed route evidence",
            )
        evidence.raw_excerpt = raw


def _observed_research_text(report: CustomerReport) -> str:
    parts: list[str] = []
    for evidence in report.evidence:
        metadata = evidence.metadata or {}
        screen_exploration = metadata.get("screen_exploration")
        if isinstance(screen_exploration, dict):
            parts.append(_screen_exploration_observed_text(screen_exploration))
        research_brain = metadata.get("research_brain")
        if isinstance(research_brain, dict):
            parts.append(_research_brain_observed_text(research_brain))
    return " ".join(parts)


def _screen_exploration_observed_text(screen_exploration: dict) -> str:
    parts: list[str] = []
    for route in screen_exploration.get("routes", []):
        if not isinstance(route, dict):
            continue
        observation = route.get("observation")
        if isinstance(observation, dict):
            parts.extend(_observed_text_fields(observation))
        for action in route.get("actions", []):
            if not isinstance(action, dict):
                continue
            parts.extend(_observed_text_fields(action))
            result = action.get("result")
            if isinstance(result, dict):
                parts.extend(_observed_text_fields(result))
    return " ".join(parts)


def _research_brain_observed_text(research_brain: dict) -> str:
    parts: list[str] = []
    for route in research_brain.get("routes", []):
        if not isinstance(route, dict):
            continue
        observation = route.get("observation")
        if isinstance(observation, dict):
            parts.extend(_observed_text_fields(observation))
        for action in route.get("actions", []):
            if isinstance(action, dict):
                parts.extend(_observed_text_fields(action))
    return " ".join(parts)


def _observed_text_fields(value: dict) -> list[str]:
    fields = (
        "bodyText",
        "bodyTextSample",
        "visibleText",
        "text",
        "beforeTextSample",
        "afterTextSample",
        "title",
        "headings",
        "buttons",
        "links",
        "primaryActionText",
    )
    parts: list[str] = []
    for field in fields:
        current = value.get(field)
        if isinstance(current, str):
            parts.append(current)
        elif isinstance(current, list):
            parts.extend(str(item) for item in current if isinstance(item, (str, int, float)))
    return parts


def _collect_screenshot_captures(report: CustomerReport) -> list[ScreenshotCaptureRecord]:
    captures: list[ScreenshotCaptureRecord] = []
    for evidence in report.evidence:
        captures.extend(evidence.screenshot_captures)
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ScreenshotCaptureRecord] = []
    for capture in captures:
        key = (capture.path, capture.route, capture.action)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(capture)
    return deduped


def _readline_with_timeout(process: subprocess.Popen[str], timeout: int) -> str:
    if process.stdout is None:
        raise OSError("process stdout is not available")
    ready, _, _ = select.select([process.stdout], [], [], timeout)
    if not ready:
        raise subprocess.TimeoutExpired(process.args, timeout=timeout)
    line = process.stdout.readline()
    if not line:
        stderr = ""
        if process.stderr is not None:
            try:
                stderr = process.stderr.read()
            except Exception:
                stderr = ""
        raise OSError(f"persistent browser session exited unexpectedly: {stderr[:500]}")
    return line


def _transient_browser_failure(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    return (
        "timeouterror" in text
        or "timeout" in text
        or "connectovercdp" in text
        or "connection closed" in text
        or result.returncode in {124, 137, 143}
    )


def _mark_primary_workflow_covered_by_flow(raw_excerpt: str) -> str:
    replacements = {
        "STEP_SKIP|planned-task|primary-workflow: planned by rubric but not interactively executed by the deterministic runner": (
            "STEP_PASS|planned-task|primary-workflow: executed by the configured customer flow runner"
        ),
        "STEP_SKIP|primary-workflow|deterministic runner detects workflow cues but does not complete upload/download flows; use manual evidence for full task execution": (
            "STEP_PASS|primary-workflow|configured customer flow runner executes the product-specific workflow"
        ),
    }
    updated = raw_excerpt
    for before, after in replacements.items():
        updated = updated.replace(before, after)
    return updated


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


def _path_for_windows_wrapper(path: Path) -> str:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = expanded.resolve()
    try:
        converted = subprocess.run(
            ["wslpath", "-w", str(expanded)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return str(expanded)
    return converted.stdout.strip() if converted.returncode == 0 and converted.stdout.strip() else str(expanded)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "step"


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_flow_pack(flow_pack: CustomerFlow | CustomerFlowPack) -> CustomerFlowPack:
    if isinstance(flow_pack, CustomerFlowPack):
        return flow_pack
    return CustomerFlowPack(name=flow_pack.name, flows=[flow_pack])


def _safe_explore_product(
    target: ExperienceTarget,
    profile: CustomerProfile,
    wrapper_path: Path,
    command_runner: CommandRunner,
) -> ProductExplorationPlan:
    try:
        return explore_product(
            target,
            profile,
            wrapper_path=wrapper_path,
            command_runner=command_runner,
        )
    except (RuntimeError, CustomerLoopRunnerError, FileNotFoundError) as exc:
        base_route = _route_from_url(str(target.url), target) or "/"
        return ProductExplorationPlan(
            target=target,
            profile=profile,
            routes=[
                ProductRoute(
                    route=base_route,
                    url=str(target.url),
                    label="Entry route",
                    kind="landing",
                    priority=10,
                    reasons=["fallback after browser route exploration failed"],
                    coverage_status="partial",
                )
            ],
            journeys=[],
            personas=[profile.persona],
            coverage_gaps=[f"Browser route exploration failed: {str(exc).splitlines()[0][:240]}"],
            notes="Fallback exploration plan generated so the customer run can continue and report evidence instead of crashing.",
        )


def _safe_inspect_customer_flow_pack(
    target: ExperienceTarget,
    profile: CustomerProfile,
    routes: list[str],
    wrapper_path: Path,
    command_runner: CommandRunner,
) -> CustomerFlowPack:
    try:
        return inspect_customer_flow_pack(
            target,
            profile,
            routes,
            wrapper_path=wrapper_path,
            command_runner=command_runner,
        )
    except (RuntimeError, CustomerLoopRunnerError, FileNotFoundError):
        return suggest_customer_flow_pack(target, profile, routes=routes or ["/"])


def _degraded_browser_report(
    target: ExperienceTarget,
    profile: CustomerProfile,
    plan: CustomerTestPlan,
    runner_name: str,
    error: Exception,
) -> CustomerReport:
    evidence = CustomerEvidence(
        kind="browser_runtime_failure",
        observed_behavior=f"{runner_name} browser runtime failed before full evidence collection.",
        raw_excerpt=str(error),
        metadata={
            "runner": runner_name,
            "rubric": "customer-testing-openclaw",
            "method": "degraded browser failure report",
            "error": str(error),
        },
    )
    finding = _browser_finding(
        "browser-runtime-failed",
        "Browser/CDP runtime failed before full customer evidence collection",
        CustomerSeverity.high,
        "The customer researcher could not complete the run because the browser control layer failed.",
        "TeamNoT cannot claim customer-readiness when the evidence collector itself is unstable.",
        "Every customer-loop run on this environment until the CDP/screenshot primitive is stable.",
        "Harden the Windows CDP wrapper, retry failed primitives, or switch to a stable browser backend before merge.",
        core=True,
    )
    finding.evidence.append(evidence)
    return CustomerReport(
        profile=profile,
        target=target,
        plan=plan,
        findings=[finding],
        evidence=[evidence],
        summary=f"{runner_name} produced a degraded report because browser runtime failed.",
    )


def _flow_url(target: ExperienceTarget, url_or_path: str) -> str:
    value = url_or_path.strip()
    if not value:
        return str(target.url)
    return urljoin(str(target.url), value)


def _route_from_url(url: str, target: ExperienceTarget) -> str:
    if not url:
        return ""
    parsed_target = urlparse(str(target.url))
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc and parsed.netloc != parsed_target.netloc:
        return ""
    if parsed.fragment.startswith("/"):
        return parsed.fragment
    path = parsed.path or "/"
    return path if path.startswith("/") else f"/{path}"


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    return sha256(path.read_bytes()).hexdigest()


def _clean_screen_action(action: dict) -> dict:
    return {
        "text": str(action.get("text", ""))[:160],
        "selector": str(action.get("selector", ""))[:240],
        "href": str(action.get("href", ""))[:240],
        "tag": str(action.get("tag", "")),
        "role": str(action.get("role", "")),
        "in_main": bool(action.get("inMain")),
        "in_nav": bool(action.get("inNav")),
        "in_header": bool(action.get("inHeader")),
        "in_footer": bool(action.get("inFooter")),
        "top": action.get("top"),
    }


def _safe_screen_action(action: dict, target: ExperienceTarget) -> bool:
    text = str(action.get("text", "")).lower()
    href = str(action.get("href", "")).lower()
    combined = f"{text} {href}"
    unsafe_terms = (
        "delete", "remove", "destroy", "purchase", "buy", "checkout", "pay",
        "subscribe", "logout", "sign out", "download", "installer",
    )
    low_value_terms = (
        "skip", "github", "repo", "devtools", "developer tools", "open tanstack",
        "cookie", "terms", "privacy policy",
    )
    if action.get("disabled"):
        return False
    if any(term in combined for term in unsafe_terms):
        return False
    if any(term in combined for term in low_value_terms):
        return False
    if href:
        parsed = urlparse(href)
        target_netloc = urlparse(str(target.url)).netloc
        if parsed.scheme in {"http", "https"} and parsed.netloc and parsed.netloc != target_netloc:
            return False
    if str(action.get("tag", "")).lower() == "a" and not text:
        return False
    return bool(text or action.get("selector"))


def _rank_screen_actions(actions: list[dict]) -> list[dict]:
    return sorted(actions, key=_screen_action_rank, reverse=True)


def _screen_action_rank(action: dict) -> tuple[int, int, int, int]:
    text = str(action.get("text", "")).lower()
    selector = str(action.get("selector", "")).lower()
    href = str(action.get("href", "")).lower()
    combined = f"{text} {selector}"
    primary_terms = (
        "get started", "start", "try", "demo", "create", "register", "sign up",
        "login", "log in", "continue", "submit", "save", "new", "add", "open",
        "view", "details", "profile", "settings", "dashboard", "account", "activity",
        "search", "filter", "compare", "share", "export", "download", "approve",
        "upload", "import", "connect", "invite", "contact", "book", "reserve",
        "apply", "request", "message",
        "chi tiết", "hồ sơ", "tài khoản", "của tôi", "phòng của tôi", "hoạt động",
        "đăng", "góp ý", "liên hệ", "tải lên", "nhập", "kết nối", "mời",
        "đặt", "ứng tuyển", "yêu cầu", "nhắn",
    )
    low_value_terms = (
        "skip", "menu", "github", "repo", "terms", "privacy", "cookie",
        "tất cả",
    )
    primary_score = sum(2 for term in primary_terms if term in combined)
    has_primary_intent = primary_score > 0
    stateful_chip_penalty = -4 if (
        not has_primary_intent
        and not href
        and str(action.get("tag", "")).lower() in {"button", "input"}
        and len(text) <= 32
        and len(text.split()) <= 4
    ) else 0
    region_score = 3 if action.get("inMain") else 0
    if action.get("inNav") or action.get("inHeader"):
        region_score -= 1
    if action.get("inFooter"):
        region_score -= 3
    low_value_penalty = -4 if any(term in combined for term in low_value_terms) else 0
    top = action.get("top")
    top_score = -abs(int(top)) if isinstance(top, int) else 0
    return primary_score + region_score + low_value_penalty + stateful_chip_penalty, len(text), top_score, -len(selector)


def _screen_exploration_evidence(screen_exploration: dict) -> tuple[CustomerEvidence, list[CustomerFinding]]:
    actions = [
        action
        for route in screen_exploration.get("routes", [])
        if isinstance(route, dict)
        for action in route.get("actions", [])
        if isinstance(action, dict)
    ]
    changed_actions = [
        action for action in actions
        if action.get("url_changed") or action.get("text_changed") or action.get("visual_changed")
    ]
    failed_actions = [action for action in actions if action.get("passed") is False]
    screenshot_paths: list[str] = []
    screenshot_captures: list[ScreenshotCaptureRecord] = []
    markers: list[str] = []
    for route in screen_exploration.get("routes", []):
        if not isinstance(route, dict):
            continue
        if route.get("entry_screenshot"):
            screenshot_paths.append(str(route["entry_screenshot"]))
        if isinstance(route.get("entry_screenshot_capture"), dict):
            screenshot_captures.append(ScreenshotCaptureRecord.model_validate(route["entry_screenshot_capture"]))
        if not route.get("actions"):
            markers.append(
                f"STEP_SKIP|screen-route-{_slug(str(route.get('route', 'route')))}|"
                f"no safe visible actions executed on {route.get('route', '')}"
            )
        for index, action in enumerate(route.get("actions", []), start=1):
            if not isinstance(action, dict):
                continue
            screenshot_paths.extend([
                str(action.get("before_screenshot", "")),
                str(action.get("after_screenshot", "")),
            ])
            screenshot_captures.extend(
                ScreenshotCaptureRecord.model_validate(record)
                for record in action.get("screenshot_captures", [])
                if isinstance(record, dict)
            )
            changed = action.get("url_changed") or action.get("text_changed") or action.get("visual_changed")
            marker = "STEP_PASS" if changed else "STEP_FAIL"
            action_text = action.get("action", {}).get("text") or action.get("action", {}).get("selector", "action")
            markers.append(
                f"{marker}|screen-action-{_slug(str(route.get('route', 'route')))}-{index:02d}|"
                f"{action_text}: url_changed={bool(action.get('url_changed'))}, "
                f"text_changed={bool(action.get('text_changed'))}, visual_changed={bool(action.get('visual_changed'))}"
            )
    screenshot_paths = [path for path in screenshot_paths if path]
    evidence = CustomerEvidence(
        kind="browser_screen_exploration",
        path=screenshot_paths[0] if screenshot_paths else "",
        screenshot_paths=screenshot_paths,
        observed_behavior=(
            f"Executed {len(actions)} safe screen action(s) across "
            f"{len(screen_exploration.get('routes', []))} route(s); "
            f"{len(changed_actions)} produced observable URL/text/screenshot change."
        ),
        raw_excerpt="\n".join(markers),
        screenshot_captures=screenshot_captures,
        metadata={
            "runner": "openclaw-windows-session",
            "rubric": "customer-testing-openclaw",
            "method": screen_exploration.get("method", ""),
            "screen_exploration": screen_exploration,
        },
    )
    findings: list[CustomerFinding] = []
    if not actions:
        findings.append(_browser_finding(
            "screen-exploration-no-actions",
            "Screen exploration found no safe customer actions to exercise",
            CustomerSeverity.high,
            "The runner stayed at observation level instead of behaving like a customer who tries the product.",
            "TeamNoT may overstate readiness because it did not actually attempt meaningful interaction.",
            "Every product without a pre-authored flow pack.",
            "Improve route/action discovery or provide product flow hints so the runner can execute real user journeys.",
            core=True,
        ))
    elif not changed_actions:
        findings.append(_browser_finding(
            "screen-exploration-no-observable-change",
            "Customer actions produced no observable screen change",
            CustomerSeverity.high,
            "The runner clicked visible actions but did not observe a changed URL, text state, or screenshot.",
            "A loop can falsely look complete while still being stuck on the same surface.",
            "Every product whose key flows require multi-step state, auth, or custom controls.",
            "Treat this as an exploration failure and add deeper action planning or seeded state before merge.",
            core=True,
        ))
    if failed_actions:
        findings.append(_browser_finding(
            "screen-exploration-action-failures",
            "Some screen exploration actions failed at runtime",
            CustomerSeverity.medium,
            "The runner attempted customer-visible actions but the browser action primitive failed.",
            "Coverage may be incomplete even when route discovery succeeds.",
            "Products or environments where CDP/eval/screenshot intermittently fails.",
            "Keep the loop alive, record failed actions as evidence, and retry or choose alternate branches.",
        ))
    discovered_routes = set(screen_exploration.get("routes_discovered", []))
    if len(discovered_routes) <= 1 and not changed_actions:
        findings.append(_browser_finding(
            "screen-exploration-entry-route-only",
            "Exploration did not escape the entry route",
            CustomerSeverity.medium,
            "The customer-style session did not build evidence across multiple product screens.",
            "The feature remains biased toward landing-page assessment for larger products.",
            "Products with dashboards, auth, settings, records, or multi-screen workflows.",
            "Use screen-driven route expansion and seeded auth/account flows before claiming general product coverage.",
        ))
    return evidence, findings


def _research_hypotheses(profile: CustomerProfile) -> list[str]:
    hypotheses = [
        "Can the customer understand the product promise from the first screen?",
        "Can the customer start a realistic workflow without developer guidance?",
        "Can the customer recover from empty or invalid input?",
        "Does the product expose proof for trust, state, permissions, and adoption risk?",
    ]
    if profile.buyer_user_split:
        hypotheses.append("Do daily-user and buyer/security concerns diverge?")
    if profile.current_workflow or profile.alternatives:
        hypotheses.append("Is there enough evidence to switch away from the current workflow?")
    return hypotheses


def _research_actions_from_observation(observation: dict, target: ExperienceTarget) -> list[dict]:
    actions: list[dict] = []
    forms = observation.get("forms", []) if isinstance(observation.get("forms"), list) else []
    for form in forms[:2]:
        if not isinstance(form, dict):
            continue
        form_index = int(form.get("index", 0))
        if form.get("submitSelector") or form.get("submitText"):
            actions.append({
                "id": f"empty-submit-form-{form_index}",
                "kind": "empty_submit",
                "form_index": form_index,
                "goal": "Exercise mistake recovery by submitting required fields empty.",
            })
            if form.get("inputs"):
                actions.append({
                    "id": f"filled-submit-form-{form_index}",
                    "kind": "filled_submit",
                    "form_index": form_index,
                    "goal": "Exercise the realistic first-value form path with generated customer input.",
                })
    raw_actions = observation.get("actions", []) if isinstance(observation.get("actions"), list) else []
    safe_actions = rank_customer_actions(_rank_screen_actions([
        action for action in raw_actions
        if isinstance(action, dict) and _safe_screen_action(action, target)
    ]))
    for action in safe_actions[:4]:
        clean = _clean_screen_action(action)
        if clean.get("text", "").lower() in {"register", "log in", "login"} and actions:
            continue
        actions.append({
            "id": f"click-{_slug(clean.get('text') or clean.get('selector') or 'action')}",
            "kind": "click",
            "goal": "Explore the next visible customer branch.",
            "reason": "Ranked as a product/customer action over navigation, footer, or developer links.",
            "action": clean,
        })
    return actions[:6]


def _research_brain_evidence(research_brain: dict) -> tuple[CustomerEvidence, list[CustomerFinding]]:
    routes = research_brain.get("routes", []) if isinstance(research_brain.get("routes"), list) else []
    actions = [
        action
        for route in routes
        if isinstance(route, dict)
        for action in route.get("actions", [])
        if isinstance(action, dict)
    ]
    filled_submit_actions = [
        action for action in actions
        if action.get("action", {}).get("kind") == "filled_submit"
    ]
    empty_submit_actions = [
        action for action in actions
        if action.get("action", {}).get("kind") == "empty_submit"
    ]
    changed_actions = [
        action for action in actions
        if action.get("url_changed") or action.get("text_changed") or action.get("visual_changed")
    ]
    failed_actions = [action for action in actions if action.get("passed") is False]
    screenshot_failures = [
        action for action in actions
        if not action.get("before_screenshot_ok") or not action.get("after_screenshot_ok")
    ]
    markers: list[str] = []
    screenshot_paths: list[str] = []
    screenshot_captures: list[ScreenshotCaptureRecord] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        if route.get("entry_screenshot"):
            screenshot_paths.append(str(route["entry_screenshot"]))
        if isinstance(route.get("entry_screenshot_capture"), dict):
            screenshot_captures.append(ScreenshotCaptureRecord.model_validate(route["entry_screenshot_capture"]))
        for index, action in enumerate(route.get("actions", []), start=1):
            if not isinstance(action, dict):
                continue
            screenshot_paths.extend([
                str(action.get("before_screenshot", "")),
                str(action.get("after_screenshot", "")),
            ])
            screenshot_captures.extend(
                ScreenshotCaptureRecord.model_validate(record)
                for record in action.get("screenshot_captures", [])
                if isinstance(record, dict)
            )
            marker = "STEP_PASS" if (
                action.get("url_changed") or action.get("text_changed") or action.get("visual_changed")
            ) else "STEP_FAIL"
            planned = action.get("action", {})
            markers.append(
                f"{marker}|research-{_slug(str(route.get('route', 'route')))}-{index:02d}|"
                f"{planned.get('kind', 'action')} {planned.get('id', '')}: "
                f"url_changed={bool(action.get('url_changed'))}, "
                f"text_changed={bool(action.get('text_changed'))}, "
                f"visual_changed={bool(action.get('visual_changed'))}"
            )
    screenshot_paths = [path for path in screenshot_paths if path]
    evidence = CustomerEvidence(
        kind="browser_research_brain",
        path=screenshot_paths[0] if screenshot_paths else "",
        screenshot_paths=screenshot_paths,
        observed_behavior=(
            f"Research brain executed {len(actions)} planned branch action(s) across "
            f"{len(routes)} route(s); {len(changed_actions)} produced observable change."
        ),
        raw_excerpt="\n".join(markers),
        screenshot_captures=screenshot_captures,
        metadata={
            "runner": "openclaw-windows-researcher",
            "rubric": "customer-testing-openclaw",
            "method": research_brain.get("method", ""),
            "research_brain": research_brain,
        },
    )
    findings: list[CustomerFinding] = []
    if not actions:
        findings.append(_browser_finding(
            "research-brain-no-branch-actions",
            "Research brain could not plan executable customer branches",
            CustomerSeverity.high,
            "The system did not move from observation into realistic customer behavior.",
            "The run can still overclaim readiness because it has not tried enough product behavior.",
            "Every product without explicit flow configuration.",
            "Improve observation/action planning or provide seeded state and product flow hints.",
            core=True,
        ))
    if actions and not changed_actions:
        findings.append(_browser_finding(
            "research-brain-no-observable-branch-change",
            "Research brain actions produced no observable branch change",
            CustomerSeverity.high,
            "The product was clicked or filled but the researcher could not observe meaningful progress.",
            "The customer loop may replay shallow actions without learning anything new.",
            "Products with custom controls, auth walls, or stateful flows.",
            "Treat unchanged branches as failed research and choose a different journey or provide seeded state.",
            core=True,
        ))
    if failed_actions:
        findings.append(_browser_finding(
            "research-brain-action-failures",
            "Some planned research actions failed at runtime",
            CustomerSeverity.medium,
            "The research brain attempted a branch but the browser/CDP primitive failed before producing strong evidence.",
            "The run may miss reachable product behavior if action-level failures are not retried or bypassed.",
            "Every flaky CDP, auth, custom-control, or long-running product branch.",
            "Record the failed action, continue other branches, and retry with a more stable primitive or seeded state.",
            core=True,
        ))
    has_form_surface = any(
        route.get("observation", {}).get("forms")
        for route in routes
        if isinstance(route, dict) and isinstance(route.get("observation"), dict)
    )
    has_input_surface = any(
        route.get("observation", {}).get("inputs")
        for route in routes
        if isinstance(route, dict) and isinstance(route.get("observation"), dict)
    )
    if not filled_submit_actions and (empty_submit_actions or has_form_surface or has_input_surface):
        findings.append(_browser_finding(
            "research-brain-no-realistic-form-submit",
            "No realistic filled form submission was executed",
            CustomerSeverity.medium,
            "The run did not behave like a user who supplies their own task data.",
            "It may miss activation, validation, and output problems that appear only after real input.",
            "Products whose first value requires forms, uploads, auth, or generated output.",
            "Add form-planning, fixtures, seeded accounts, or product-specific input generation before claiming full coverage.",
            core=True,
        ))
    seeded_status = str(research_brain.get("seeded_state_status", "absent"))
    auth_required = _research_brain_auth_gate_detected(research_brain)
    if seeded_status in {"absent", "unsupported"} and auth_required:
        findings.append(_browser_finding(
            "research-brain-no-seeded-state",
            "No usable seeded account or state was available for authenticated depth",
            CustomerSeverity.medium,
            "The researcher can detect auth but cannot enter the app like a real customer with an account.",
            "Coverage stays pre-auth for dashboards, records, settings, team flows, and saved state.",
            "Every product whose value lives behind login or workspace state.",
            "Provide a seeded account/state fixture plus cleanup policy so the researcher can test post-auth journeys.",
            core=True,
        ))
    if screenshot_failures:
        findings.append(_browser_finding(
            "research-brain-screenshot-evidence-flaky",
            "Screenshot evidence was incomplete for some research actions",
            CustomerSeverity.medium,
            "The run had to rely on URL/text evidence where visual before/after evidence should exist.",
            "The system remains weaker than the customer-testing skill when screenshot capture is unstable.",
            "Any visual/layout/trust judgment that depends on screenshots.",
            "Harden the Windows screenshot primitive or retry/fallback capture before marking visual evidence complete.",
        ))
    return evidence, findings


def _missing_required_auth_blocker(
    research_brain: dict,
    seeded_state: SeededCustomerState | None,
) -> str:
    seeded_status = str(research_brain.get("seeded_state_status", "absent"))
    if seeded_status in {"applied", "browser_context"}:
        return ""
    if seeded_state and seeded_state.adapter_status == "applied":
        return ""
    if not _research_brain_auth_gate_detected(research_brain):
        return ""
    cdp = ""
    runtime = research_brain.get("browser_runtime")
    if isinstance(runtime, dict):
        cdp = str(runtime.get("cdp_url") or runtime.get("cdp") or "")
    target_url = _first_auth_gate_url(research_brain)
    return (
        "Authentication is required before customer testing can continue. "
        "Log in with a real or seeded test account in the browser context TeamNoT is attached to"
        f"{f' ({cdp})' if cdp else ''}"
        f"{f' and retry from {target_url}' if target_url else ''}. "
        "TeamNoT stopped instead of producing a shallow pre-auth report."
    )


def _research_brain_auth_gate_detected(research_brain: dict) -> bool:
    gate_terms = (
        "login required", "log in required", "sign in required",
        "please log in", "please sign in", "sign in to continue", "log in to continue",
        "login to continue", "must be logged in", "requires an account",
        "authentication required", "unauthorized", "forbidden",
        "đăng nhập để", "vui lòng đăng nhập", "cần đăng nhập", "yêu cầu đăng nhập",
        "đăng nhập trước", "phải đăng nhập",
    )
    for route in research_brain.get("routes", []):
        if not isinstance(route, dict):
            continue
        route_text = " ".join([
            str(route.get("route") or ""),
            *_observed_text_fields(route.get("observation") or {}),
        ])
        if _contains_any(route_text, gate_terms) or _auth_route(str(route.get("route") or "")):
            return True
        for action in route.get("actions", []) if isinstance(route.get("actions"), list) else []:
            if not isinstance(action, dict):
                continue
            result = action.get("result") if isinstance(action.get("result"), dict) else {}
            action_text = " ".join([
                str(action.get("summary") or ""),
                *_observed_text_fields(action),
                *_observed_text_fields(result),
            ])
            if _contains_any(action_text, gate_terms):
                return True
            after_url = str(result.get("afterUrl") or result.get("url") or "")
            if _auth_route(after_url):
                return True
    return False


def _first_auth_gate_url(research_brain: dict) -> str:
    for route in research_brain.get("routes", []):
        if not isinstance(route, dict):
            continue
        route_name = str(route.get("route") or "")
        if _auth_route(route_name):
            return route_name
        for action in route.get("actions", []) if isinstance(route.get("actions"), list) else []:
            if not isinstance(action, dict):
                continue
            result = action.get("result") if isinstance(action.get("result"), dict) else {}
            after_url = str(result.get("afterUrl") or result.get("url") or "")
            if _auth_route(after_url):
                return after_url
    return ""


def _auth_route(url_or_path: str) -> bool:
    value = url_or_path.lower()
    return any(term in value for term in ("/login", "/log-in", "/signin", "/sign-in", "/auth"))


def _research_brain_auth_required(research_brain: dict) -> bool:
    text_parts: list[str] = []
    for route in research_brain.get("routes", []):
        if not isinstance(route, dict):
            continue
        observation = route.get("observation", {})
        if isinstance(observation, dict):
            text_parts.append(str(observation.get("bodyTextSample", "")))
            for action in observation.get("actions", []) if isinstance(observation.get("actions"), list) else []:
                if isinstance(action, dict):
                    text_parts.append(str(action.get("text", "")))
        for action in route.get("actions", []) if isinstance(route.get("actions"), list) else []:
            if not isinstance(action, dict):
                continue
            result = action.get("result", {})
            if isinstance(result, dict):
                text_parts.extend([
                    str(result.get("beforeTextSample", "")),
                    str(result.get("afterTextSample", "")),
                    str(result.get("summary", "")),
                ])
    combined = " ".join(text_parts).lower()
    return _contains_any(combined, (
        "login", "log in", "sign in", "auth", "account", "workspace",
        "đăng nhập", "tài khoản",
    ))


def _research_action_expr(action: dict) -> str:
    payload = json.dumps(action)
    return f"""(async () => {{
      const action = {payload};
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\\s+/g, " ").trim();
      const visible = (el) => Boolean(el && el.offsetParent !== null);
      const forms = Array.from(document.querySelectorAll("form"));
      const beforeUrl = location.href;
      const beforeTitle = document.title || "";
      const beforeTextSample = textOf(document.body).slice(0, 1400);
      const sampleFor = (el) => {{
        const type = String(el.getAttribute("type") || el.tagName || "").toLowerCase();
        const label = [
          el.getAttribute("name") || "",
          el.getAttribute("placeholder") || "",
          el.getAttribute("aria-label") || "",
          el.id || "",
        ].join(" ").toLowerCase();
        if (type.includes("email") || label.includes("email")) return "customer.researcher@example.com";
        if (type.includes("password") || label.includes("password")) return "CustomerTest123!";
        if (label.includes("first")) return "Customer";
        if (label.includes("last")) return "Researcher";
        if (label.includes("team")) return "Customer Research Team";
        if (type.includes("number")) return "42";
        if (type.includes("checkbox") || type.includes("radio")) return true;
        return "Customer research test input";
      }};
      const submit = async (form) => {{
        const submitter = form.querySelector('button[type="submit"],input[type="submit"]')
          || Array.from(form.querySelectorAll("button,input[type=button]"))
            .find((el) => /submit|register|create|continue|save|log in|login|sign up/i.test(textOf(el) || el.value || ""));
        if (!submitter) return {{ ok: false, summary: "form submit control not found" }};
        submitter.click();
        await wait(900);
        return {{ ok: true, summary: `submitted form with ${{textOf(submitter) || submitter.value || "submit"}}` }};
      }};
      if (action.kind === "empty_submit" || action.kind === "filled_submit") {{
        const form = forms[action.form_index || 0];
        if (!form) return {{ ok: false, beforeUrl, afterUrl: location.href, summary: `form not found: ${{action.form_index || 0}}` }};
        if (action.kind === "filled_submit") {{
          for (const el of Array.from(form.querySelectorAll("input,textarea,select"))) {{
            const type = String(el.getAttribute("type") || "").toLowerCase();
            if (type === "hidden" || type === "file" || el.disabled) continue;
            const sample = sampleFor(el);
            if (typeof sample === "boolean") {{
              el.checked = sample;
            }} else {{
              el.focus();
              el.value = sample;
            }}
            el.dispatchEvent(new Event("input", {{ bubbles: true }}));
            el.dispatchEvent(new Event("change", {{ bubbles: true }}));
          }}
        }}
        const submitted = await submit(form);
        return {{
          ...submitted,
          beforeUrl,
          afterUrl: location.href,
          beforeTitle,
          afterTitle: document.title || "",
          beforeTextSample,
          afterTextSample: textOf(document.body).slice(0, 1400),
        }};
      }}
      if (action.kind === "click") {{
        const inner = action.action || {{}};
        const href = inner.href || "";
        const text = (inner.text || "").toLowerCase();
        const selector = inner.selector || "";
        let target = null;
        if (href) {{
          target = Array.from(document.querySelectorAll("a[href]"))
            .find((el) => visible(el) && el.href === href && (!text || textOf(el).toLowerCase().includes(text)));
        }}
        if (!target && selector && !["a", "button"].includes(selector)) target = document.querySelector(selector);
        if (!target && text) {{
          target = Array.from(document.querySelectorAll("button,[role=button],a[href],input[type=button],input[type=submit]"))
            .find((el) => visible(el) && textOf(el).toLowerCase().includes(text));
        }}
        if (!target) return {{ ok: false, beforeUrl, afterUrl: location.href, summary: `click target not found: ${{inner.text || selector}}` }};
        target.scrollIntoView({{ block: "center", inline: "center" }});
        await wait(100);
        target.click();
        await wait(900);
        return {{
          ok: true,
          beforeUrl,
          afterUrl: location.href,
          beforeTitle,
          afterTitle: document.title || "",
          beforeTextSample,
          afterTextSample: textOf(document.body).slice(0, 1400),
          summary: `clicked ${{textOf(target) || selector}}`,
        }};
      }}
      return {{ ok: false, beforeUrl, afterUrl: location.href, summary: `unsupported research action: ${{action.kind}}` }};
    }})()"""


def _screen_action_click_expr(action: dict) -> str:
    payload = json.dumps(_clean_screen_action(action))
    return f"""(async () => {{
      const action = {payload};
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\\s+/g, " ").trim();
      const visible = (el) => Boolean(el && el.offsetParent !== null);
      const selector = action.selector || "";
      const text = (action.text || "").toLowerCase();
      const href = action.href || "";
      const beforeUrl = location.href;
      const beforeTitle = document.title || "";
      const beforeTextSample = textOf(document.body).slice(0, 1200);
      let target = null;
      if (href) {{
        target = Array.from(document.querySelectorAll("a[href]"))
          .find((el) => visible(el) && el.href === href && (!text || textOf(el).toLowerCase().includes(text)));
      }}
      if (!target && selector && !["a", "button"].includes(selector)) target = document.querySelector(selector);
      if (!target && text) {{
        target = Array.from(document.querySelectorAll("button,[role=button],a[href],input[type=button],input[type=submit]"))
          .find((el) => visible(el) && textOf(el).toLowerCase().includes(text));
      }}
      if (!target) {{
        return {{ ok: false, beforeUrl, afterUrl: location.href, summary: `action target not found: ${{action.text || action.selector}}` }};
      }}
      target.scrollIntoView({{ block: "center", inline: "center" }});
      await wait(100);
      target.click();
      await wait(800);
      return {{
        ok: true,
        beforeUrl,
        afterUrl: location.href,
        beforeTitle,
        afterTitle: document.title || "",
        beforeTextSample,
        afterTextSample: textOf(document.body).slice(0, 1200),
        summary: `clicked ${{textOf(target) || action.selector}}`,
      }};
    }})()"""


def _flow_step_expr(step: CustomerFlowStep) -> str:
    payload = json.dumps({
        "id": step.id,
        "action": step.action,
        "selector": step.selector,
        "text": step.text,
        "value": step.value,
        "url": step.url,
        "timeoutMs": step.timeout_ms,
    })
    return f"""(async () => {{
      const step = {payload};
      const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\\s+/g, " ").trim();
      const find = (selector) => selector ? document.querySelector(selector) : null;
      const visible = (el) => Boolean(el && el.offsetParent !== null);
      const allControls = () => Array.from(document.querySelectorAll("button,[role=button],a[href],input[type=button],input[type=submit]"));
      const byText = (text) => {{
        const needle = String(text || "").toLowerCase();
        return allControls().find((el) => visible(el) && textOf(el).toLowerCase().includes(needle));
      }};
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const waitFor = async (predicate) => {{
        const start = Date.now();
        while (Date.now() - start < step.timeoutMs) {{
          const result = predicate();
          if (result) return result;
          await wait(250);
        }}
        return null;
      }};
      if (step.action === "fill") {{
        const el = find(step.selector);
        if (!el) return {{ passed: false, summary: `selector not found: ${{step.selector}}` }};
        el.focus();
        el.value = step.value;
        el.dispatchEvent(new Event("input", {{ bubbles: true }}));
        el.dispatchEvent(new Event("change", {{ bubbles: true }}));
        return {{ passed: true, summary: `filled ${{step.selector}}` }};
      }}
      if (step.action === "select") {{
        const el = find(step.selector);
        if (!el) return {{ passed: false, summary: `selector not found: ${{step.selector}}` }};
        el.value = step.value;
        el.dispatchEvent(new Event("input", {{ bubbles: true }}));
        el.dispatchEvent(new Event("change", {{ bubbles: true }}));
        return {{ passed: true, summary: `selected ${{step.value}} in ${{step.selector}}` }};
      }}
      if (step.action === "check" || step.action === "uncheck") {{
        const el = find(step.selector);
        if (!el) return {{ passed: false, summary: `selector not found: ${{step.selector}}` }};
        el.checked = step.action === "check";
        el.dispatchEvent(new Event("input", {{ bubbles: true }}));
        el.dispatchEvent(new Event("change", {{ bubbles: true }}));
        return {{ passed: true, summary: `${{step.action}}ed ${{step.selector}}` }};
      }}
      if (step.action === "click") {{
        const el = find(step.selector);
        if (!el) return {{ passed: false, summary: `selector not found: ${{step.selector}}` }};
        el.click();
        return {{ passed: true, summary: `clicked ${{textOf(el) || step.selector}}` }};
      }}
      if (step.action === "click_text") {{
        const el = byText(step.text || step.value);
        if (!el) return {{ passed: false, summary: `visible action not found by text: ${{step.text || step.value}}` }};
        el.click();
        return {{ passed: true, summary: `clicked text: ${{textOf(el)}}` }};
      }}
      if (step.action === "press") {{
        const el = find(step.selector) || document.activeElement || document.body;
        el.dispatchEvent(new KeyboardEvent("keydown", {{ key: step.value, bubbles: true }}));
        el.dispatchEvent(new KeyboardEvent("keyup", {{ key: step.value, bubbles: true }}));
        return {{ passed: true, summary: `pressed ${{step.value}}` }};
      }}
      if (step.action === "wait_ms") {{
        await wait(Number(step.value || step.timeoutMs || 1000));
        return {{ passed: true, summary: `waited ${{step.value || step.timeoutMs}}ms` }};
      }}
      if (step.action === "wait_for_text") {{
        const found = await waitFor(() => document.body && textOf(document.body).includes(step.text));
        return found
          ? {{ passed: true, summary: `found text: ${{step.text}}` }}
          : {{ passed: false, summary: `expected text not found: ${{step.text}}` }};
      }}
      if (step.action === "wait_for_text_absent") {{
        const gone = await waitFor(() => document.body && !textOf(document.body).includes(step.text));
        return gone
          ? {{ passed: true, summary: `text absent: ${{step.text}}` }}
          : {{ passed: false, summary: `text remained visible: ${{step.text}}` }};
      }}
      if (step.action === "wait_for_selector") {{
        const found = await waitFor(() => find(step.selector));
        return found
          ? {{ passed: true, summary: `found selector: ${{step.selector}}` }}
          : {{ passed: false, summary: `selector not found before timeout: ${{step.selector}}` }};
      }}
      if (step.action === "wait_for_selector_hidden") {{
        const hidden = await waitFor(() => {{
          const el = find(step.selector);
          return !el || !visible(el);
        }});
        return hidden
          ? {{ passed: true, summary: `selector hidden: ${{step.selector}}` }}
          : {{ passed: false, summary: `selector remained visible: ${{step.selector}}` }};
      }}
      if (step.action === "wait_for_enabled") {{
        const found = await waitFor(() => {{
          const el = find(step.selector);
          return el && !el.disabled && el.getAttribute("aria-disabled") !== "true" ? el : null;
        }});
        return found
          ? {{ passed: true, summary: `enabled selector: ${{step.selector}}` }}
          : {{ passed: false, summary: `selector was not enabled: ${{step.selector}}` }};
      }}
      if (step.action === "wait_for_url") {{
        const found = await waitFor(() => location.href.includes(step.url || step.value || step.text));
        return found
          ? {{ passed: true, summary: `url matched: ${{location.href}}` }}
          : {{ passed: false, summary: `url did not match: ${{step.url || step.value || step.text}}` }};
      }}
      if (step.action === "assert_text") {{
        const found = document.body && textOf(document.body).includes(step.text);
        return found
          ? {{ passed: true, summary: `asserted text: ${{step.text}}` }}
          : {{ passed: false, summary: `missing expected text: ${{step.text}}` }};
      }}
      if (step.action === "assert_no_text") {{
        const found = document.body && textOf(document.body).includes(step.text);
        return !found
          ? {{ passed: true, summary: `asserted text absent: ${{step.text}}` }}
          : {{ passed: false, summary: `unexpected text present: ${{step.text}}` }};
      }}
      if (step.action === "assert_selector") {{
        return find(step.selector)
          ? {{ passed: true, summary: `asserted selector: ${{step.selector}}` }}
          : {{ passed: false, summary: `missing selector: ${{step.selector}}` }};
      }}
      if (step.action === "checkpoint") {{
        return {{ passed: true, summary: step.description || step.id }};
      }}
      return {{ passed: false, summary: `unsupported action: ${{step.action}}` }};
    }})()"""


_CUSTOMER_PROBE_JS = r"""(() => {
  const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  const pick = (selector, limit = 20) =>
    Array.from(document.querySelectorAll(selector)).slice(0, limit).map((el) => textOf(el)).filter(Boolean);
  const controls = Array.from(document.querySelectorAll("input, textarea, select, button")).slice(0, 40).map((el) => {
    const id = el.getAttribute("id") || "";
    const label = id ? textOf(document.querySelector(`label[for="${CSS.escape(id)}"]`)) : "";
    const aria = el.getAttribute("aria-label") || el.getAttribute("title") || "";
    const placeholder = el.getAttribute("placeholder") || "";
    return {
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute("type") || "",
      text: textOf(el),
      label,
      aria,
      placeholder,
      name: el.getAttribute("name") || "",
      role: el.getAttribute("role") || "",
      disabled: Boolean(el.disabled),
    };
  });
  const links = Array.from(document.querySelectorAll("a[href]")).slice(0, 30).map((el) => ({
    text: textOf(el),
    href: el.href,
  }));
  const bodyText = textOf(document.body).slice(0, 12000);
  const visibleText = bodyText.toLowerCase();
  const forms = Array.from(document.querySelectorAll("form")).slice(0, 20).map((form) => ({
    text: textOf(form).slice(0, 1000),
    action: form.getAttribute("action") || "",
    method: form.getAttribute("method") || "",
    controls: form.querySelectorAll("input, textarea, select, button").length,
  }));
  const imagesWithoutAlt = Array.from(document.querySelectorAll("img")).filter((img) => !img.getAttribute("alt")).length;
  const headingsByLevel = Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6")).slice(0, 40).map((el) => ({
    level: Number(el.tagName.slice(1)),
    text: textOf(el),
  }));
  const landmarkCount = document.querySelectorAll("main,nav,header,footer,aside,[role=main],[role=navigation],[role=banner],[role=contentinfo]").length;
  const primaryActionText = pick("button,[role=button],input[type=submit],a[href]", 12);
  const perf = performance.getEntriesByType("navigation")[0];
  const failedResources = performance.getEntriesByType("resource")
    .filter((r) => {
      const name = String(r.name || "");
      if (!name || name.startsWith("data:")) return false;
      try {
        if (new URL(name, location.href).origin !== location.origin) return false;
      } catch {
        return false;
      }
      return r.transferSize === 0 && r.decodedBodySize === 0;
    })
    .slice(0, 20)
    .map((r) => r.name);
  return {
    url: location.href,
    title: document.title,
    headings: pick("h1,h2,h3", 30),
    headingsByLevel,
    buttons: pick("button,[role=button],input[type=submit]", 30),
    inputs: controls,
    forms,
    links,
    primaryActionText,
    bodyText,
    viewport: { width: innerWidth, height: innerHeight },
    timingMs: perf ? Math.round(perf.duration) : null,
    failedResources,
    hasHorizontalOverflow: document.documentElement.scrollWidth > innerWidth + 2,
    focusableCount: document.querySelectorAll("a[href],button,input,textarea,select,[tabindex]").length,
    imagesWithoutAlt,
    landmarkCount,
    semanticSignals: {
      hasPricing: /pricing|price|plan|trial|pilot|quote|book|demo/.test(visibleText),
      hasSupport: /support|contact|help|docs|email|chat|faq/.test(visibleText),
      hasPrivacy: /privacy|secure|security|data|local|not stored|delete|client/.test(visibleText),
      hasSample: /sample|demo|example|try it|template/.test(visibleText),
      hasErrorRecovery: /error|invalid|required|try again|retry|fix|failed|missing/.test(visibleText),
      hasTrustCue: /privacy|secure|security|data|verified|verification|trusted|safe|safety|report abuse|policy|permission|compliance|audit|encrypted|riêng tư|bảo mật|dữ liệu|an toàn|xác minh|chính sách|quyền/.test(visibleText),
      hasOutcomeCue: /report|result|download|export|summary|next action|recommend|save|share|send|compare|contact|review|feedback|apply|approve|báo cáo|kết quả|tải|xuất|lưu|chia sẻ|gửi|liên hệ|đánh giá|phản hồi|duyệt/.test(visibleText),
      hasCollaboration: /share|export|download|send|client|team|approve/.test(visibleText),
    },
  };
})()"""

_SCREEN_ACTION_DISCOVERY_JS = r"""(() => {
  const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  const selectorFor = (el) => {
    if (!el) return "";
    if (el.id) return `#${CSS.escape(el.id)}`;
    const testId = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testId) return `[data-testid="${CSS.escape(testId)}"],[data-test="${CSS.escape(testId)}"]`;
    const name = el.getAttribute("name");
    if (name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
    const href = el.getAttribute("href");
    if (href) return `${el.tagName.toLowerCase()}[href="${CSS.escape(href)}"]`;
    const type = el.getAttribute("type");
    if (type) return `${el.tagName.toLowerCase()}[type="${CSS.escape(type)}"]`;
    const aria = el.getAttribute("aria-label");
    if (aria) return `${el.tagName.toLowerCase()}[aria-label="${CSS.escape(aria)}"]`;
    return el.tagName.toLowerCase();
  };
  return Array.from(document.querySelectorAll("button,[role=button],a[href],input[type=button],input[type=submit]"))
    .filter((el) => el.offsetParent !== null)
    .slice(0, 80)
    .map((el) => {
      const rect = el.getBoundingClientRect();
      const anchor = el.closest("a[href]");
      return {
        text: textOf(el) || el.getAttribute("aria-label") || el.getAttribute("value") || el.getAttribute("title") || "",
        selector: selectorFor(el),
        href: el.href || anchor?.href || "",
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute("role") || "",
        disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
        inMain: Boolean(el.closest("main,[role=main]")),
        inHeader: Boolean(el.closest("header,[role=banner]")),
        inNav: Boolean(el.closest("nav,[role=navigation]")),
        inFooter: Boolean(el.closest("footer,[role=contentinfo]")),
        top: Math.round(rect.top),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    })
    .filter((item) => item.text || item.href || item.selector);
})()"""

_RESEARCH_OBSERVE_JS = r"""(() => {
  const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  const selectorFor = (el) => {
    if (!el) return "";
    if (el.id) return `#${CSS.escape(el.id)}`;
    const testId = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testId) return `[data-testid="${CSS.escape(testId)}"],[data-test="${CSS.escape(testId)}"]`;
    const name = el.getAttribute("name");
    if (name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
    const href = el.getAttribute("href");
    if (href) return `${el.tagName.toLowerCase()}[href="${CSS.escape(href)}"]`;
    const type = el.getAttribute("type");
    if (type) return `${el.tagName.toLowerCase()}[type="${CSS.escape(type)}"]`;
    const aria = el.getAttribute("aria-label");
    if (aria) return `${el.tagName.toLowerCase()}[aria-label="${CSS.escape(aria)}"]`;
    return el.tagName.toLowerCase();
  };
  const controls = Array.from(document.querySelectorAll("button,[role=button],a[href],input[type=button],input[type=submit]"))
    .filter((el) => el.offsetParent !== null)
    .slice(0, 80)
    .map((el) => {
      const rect = el.getBoundingClientRect();
      const anchor = el.closest("a[href]");
      return {
        text: textOf(el) || el.getAttribute("aria-label") || el.getAttribute("value") || el.getAttribute("title") || "",
        selector: selectorFor(el),
        href: el.href || anchor?.href || "",
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute("role") || "",
        disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
        inMain: Boolean(el.closest("main,[role=main]")),
        inHeader: Boolean(el.closest("header,[role=banner]")),
        inNav: Boolean(el.closest("nav,[role=navigation]")),
        inFooter: Boolean(el.closest("footer,[role=contentinfo]")),
        top: Math.round(rect.top),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    })
    .filter((item) => item.text || item.href || item.selector);
  const forms = Array.from(document.querySelectorAll("form")).slice(0, 8).map((form, index) => {
    const submitter = form.querySelector('button[type="submit"],input[type="submit"]')
      || Array.from(form.querySelectorAll("button,input[type=button]"))
        .find((el) => /submit|register|create|continue|save|log in|login|sign up/i.test(textOf(el) || el.value || ""));
    const inputs = Array.from(form.querySelectorAll("input,textarea,select")).slice(0, 16).map((el) => ({
      selector: selectorFor(el),
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute("type") || el.tagName.toLowerCase(),
      name: el.getAttribute("name") || "",
      placeholder: el.getAttribute("placeholder") || "",
      aria: el.getAttribute("aria-label") || "",
      required: Boolean(el.required || el.getAttribute("aria-required") === "true"),
      disabled: Boolean(el.disabled),
    }));
    return {
      index,
      text: textOf(form).slice(0, 1000),
      inputCount: inputs.length,
      requiredCount: inputs.filter((input) => input.required).length,
      inputs,
      submitSelector: submitter ? selectorFor(submitter) : "",
      submitText: submitter ? (textOf(submitter) || submitter.getAttribute("value") || "") : "",
    };
  });
  const bodyText = textOf(document.body);
  const headings = Array.from(document.querySelectorAll("h1,h2,h3")).slice(0, 20).map(textOf).filter(Boolean);
  return {
    url: location.href,
    title: document.title || "",
    headings,
    bodyTextSample: bodyText.slice(0, 3000),
    forms,
    actions: controls,
    focusableCount: document.querySelectorAll("a[href],button,input,textarea,select,[tabindex]").length,
    hasAuthText: /login|log in|register|sign up|password|email address/i.test(bodyText),
    hasDashboardText: /dashboard|settings|profile|users|team|discussion|project/i.test(bodyText),
    hasTrustText: /privacy|security|permission|data|terms|policy/i.test(bodyText),
  };
})()"""

_MOBILE_PROBE_JS = r"""(() => {
  const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  const viewportWidth = innerWidth;
  const documentWidth = Math.max(
    document.documentElement.scrollWidth || 0,
    document.body ? document.body.scrollWidth || 0 : 0
  );
  const offenderSelector = (el) => {
    if (!el) return "";
    if (el.id) return `${el.tagName.toLowerCase()}#${el.id}`;
    const testId = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testId) return `${el.tagName.toLowerCase()}[data-testid="${testId}"]`;
    const cls = String(el.className || "").trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".");
    return cls ? `${el.tagName.toLowerCase()}.${cls}` : el.tagName.toLowerCase();
  };
  const overflowOffenders = Array.from(document.querySelectorAll("body *"))
    .map((el) => {
      const rect = el.getBoundingClientRect();
      return {
        selector: offenderSelector(el),
        tag: el.tagName.toLowerCase(),
        text: textOf(el).slice(0, 80),
        left: Math.round(rect.left),
        right: Math.round(rect.right),
        width: Math.round(rect.width),
      };
    })
    .filter((item) => item.width > viewportWidth + 2 || item.left < -2 || item.right > viewportWidth + 2)
    .sort((a, b) => b.width - a.width)
    .slice(0, 8);
  const hasDocumentOverflow = documentWidth > viewportWidth + 2;
  return {
    url: location.href,
    viewport: { width: innerWidth, height: innerHeight },
    hasHorizontalOverflow: hasDocumentOverflow,
    overflowWidth: documentWidth,
    overflowOffenders: hasDocumentOverflow ? overflowOffenders : [],
    decorativeOverflowCandidates: hasDocumentOverflow ? [] : overflowOffenders,
    bodyTextLength: textOf(document.body).length,
    firstActions: Array.from(document.querySelectorAll("button,[role=button],input[type=submit],a[href]"))
      .slice(0, 8)
      .map((el) => textOf(el))
      .filter(Boolean),
  };
})()"""

_INTERACTIVE_SAMPLE_FLOW_JS = r"""(async () => {
  const textOf = (el) => (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  const state = (label) => {
    const bodyText = textOf(document.body);
    const download = Array.from(document.querySelectorAll("button,a,input"))
      .find((el) => /download|export|report/i.test(textOf(el) || el.value || el.getAttribute("aria-label") || ""));
    return {
      label,
      title: document.title,
      bodyTextLength: bodyText.length,
      bodyTextSample: bodyText.slice(0, 2000),
      statusText: textOf(document.querySelector("#status,[role=status],.status,.alert,.error")),
      resultText: textOf(document.querySelector("#verdict,#result,#results,.result,.results,.report,.report-preview")),
      downloadEnabled: download ? !Boolean(download.disabled || download.getAttribute("aria-disabled") === "true") : false,
      downloadText: download ? textOf(download) || download.value || download.getAttribute("aria-label") || "" : "",
      buttons: Array.from(document.querySelectorAll("button,[role=button],input[type=submit],a[href]"))
        .slice(0, 20)
        .map((el) => textOf(el) || el.value || el.getAttribute("aria-label") || "")
        .filter(Boolean),
    };
  };
  const before = state("before");
  const actionTextOf = (el) => textOf(el) || el.value || el.getAttribute("aria-label") || el.getAttribute("title") || "";
  const primaryCandidates = Array.from(document.querySelectorAll("button,[role=button],input[type=submit]"));
  const linkCandidates = Array.from(document.querySelectorAll("a[href]"))
    .filter((el) => /run sample|run demo|try sample|try demo/i.test(actionTextOf(el)));
  const candidates = [...primaryCandidates, ...linkCandidates];
  const sample = candidates.find((el) => /run sample|run demo|try sample|try demo|sample report|demo report/i.test(actionTextOf(el)))
    || primaryCandidates.find((el) => /sample|demo|example/i.test(actionTextOf(el)));
  if (!sample) {
    return { action: "sample-demo", clicked: false, reason: "no sample/demo action found", before };
  }
  const actionText = actionTextOf(sample);
  sample.click();
  let after = state("after-click");
  const start = Date.now();
  while (Date.now() - start < 10000) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    after = state("after-wait");
    if (
      after.downloadEnabled ||
      /completed|complete|done|success|verdict|blocker|warning|next action|report/i.test(after.bodyTextSample + " " + after.statusText + " " + after.resultText)
    ) {
      break;
    }
  }
  return {
    action: "sample-demo",
    clicked: true,
    actionText,
    before,
    after,
    changed: after.bodyTextLength !== before.bodyTextLength || after.statusText !== before.statusText || after.resultText !== before.resultText,
  };
})()"""


def _build_customer_findings(
    probe: dict,
    target: ExperienceTarget,
    profile: CustomerProfile,
    plan: CustomerTestPlan,
) -> tuple[list[str], list[CustomerFinding]]:
    text = str(probe.get("bodyText", ""))
    lowered = _searchable_text(text)
    headings = [str(item) for item in probe.get("headings", [])]
    inputs = [item for item in probe.get("inputs", []) if isinstance(item, dict)]
    buttons = [str(item) for item in probe.get("buttons", [])]
    failed_resources = [str(item) for item in probe.get("failedResources", [])]
    semantic = probe.get("semanticSignals", {}) if isinstance(probe.get("semanticSignals"), dict) else {}
    mobile_probe = probe.get("mobileProbe", {}) if isinstance(probe.get("mobileProbe"), dict) else {}
    markers: list[str] = []
    findings: list[CustomerFinding] = []

    unreachable_reason = _target_unreachable_reason(probe, target)
    if unreachable_reason:
        markers.append(f"STEP_FAIL|target-reachability|{unreachable_reason}")
        markers.append(
            "STEP_SKIP|product-ux-classification|"
            "target did not load as the product, so generic UX findings are suppressed"
        )
        findings.append(_browser_finding(
            "target-unreachable",
            "Target product could not be reached",
            CustomerSeverity.high,
            "The customer never reaches the product experience, so this run cannot judge the app's actual UX.",
            "Customer Loop would otherwise risk misclassifying a network/site-protection failure as product usability defects.",
            "Every run where the target lands on a browser error page or non-product interstitial.",
            "Classify this run as target_unreachable/site_protected/auth_required first, then retry with a reachable target or seeded state before filing product UX bugs.",
            trust=True,
            core=True,
        ))
        findings.extend(_build_research_gap_findings(profile, plan))
        return markers, findings

    if headings or text:
        markers.append(
            "STEP_PASS|first-impression|"
            f"title={probe.get('title', '')!s}; headings={'; '.join(headings[:5]) or 'none'}"
        )
    else:
        markers.append("STEP_FAIL|first-impression|expected readable page content -> found empty DOM")
        findings.append(_browser_finding(
            "first-impression-empty",
            "Page has no readable first-impression content",
            CustomerSeverity.critical,
            "The customer lands on the page but cannot understand what this product is for.",
            "Activation fails before the customer can try the promised workflow.",
            "Every first visit.",
            "Render a clear product promise, primary job, and first action above the fold.",
            trust=True,
            core=True,
        ))

    promise_terms = (
        "problem", "pain", "save", "risk", "prevent", "avoid", "mistake", "workflow",
        "for teams", "for agencies", "customer", "operator", "buyer",
        *_domain_terms(profile, target, plan),
    )
    if _contains_any(lowered, promise_terms):
        markers.append("STEP_PASS|customer-promise|page explains a customer problem, audience, or promised outcome")
    else:
        markers.append("STEP_FAIL|customer-promise|expected customer problem/audience/outcome language -> none detected")
        findings.append(_browser_finding(
            "unclear-customer-promise",
            "Customer promise is too generic or absent",
            CustomerSeverity.medium,
            "The customer may not recognize that this product is for their specific job.",
            "Activation and buying readiness drop because the page does not prove relevance quickly.",
            "Every first-time visitor.",
            "State the target customer, painful job, and concrete outcome near the first action.",
        ))

    has_actionable_control = any(_control_is_actionable(control) for control in inputs) or bool(buttons)
    if has_actionable_control:
        markers.append("STEP_PASS|core-workflow-cues|page exposes form/button controls for a customer action")
    else:
        markers.append("STEP_FAIL|core-workflow-cues|expected form/button controls -> none detected")
        findings.append(_browser_finding(
            "missing-core-workflow",
            "No obvious customer action is available",
            CustomerSeverity.high,
            "The page reads like information rather than a tool the customer can use.",
            "The customer cannot complete the main job without guessing where to start.",
            "Every user attempting the core workflow.",
            "Expose a clear primary input/action path for the core customer workflow.",
            core=True,
        ))

    if semantic.get("hasErrorRecovery"):
        markers.append("STEP_PASS|error-recovery|page includes invalid/error/retry/fix language")
    else:
        markers.append("STEP_FAIL|error-recovery|expected customer-readable mistake/retry guidance -> none detected")
        findings.append(_browser_finding(
            "missing-error-recovery-cues",
            "Mistake recovery is not visible before use",
            CustomerSeverity.medium,
            "The customer cannot tell what happens after a wrong choice, invalid input, empty result, failed contact step, or other risky action.",
            "Customers hesitate to proceed when recovery, reset, and next-step guidance are unclear.",
            "Likely in every evaluation before the first irreversible or high-effort action.",
            "Show concise retry/reset guidance, empty-state next steps, and whether the customer's context is preserved.",
        ))

    unnamed_controls = [
        control for control in inputs
        if control.get("tag") in {"input", "textarea", "select", "button"}
        and not any(str(control.get(key, "")).strip() for key in ("text", "label", "aria", "placeholder", "name"))
        and control.get("type") not in {"hidden"}
    ]
    if unnamed_controls:
        markers.append(
            "STEP_FAIL|accessibility-basics|"
            f"{len(unnamed_controls)} control(s) lack visible/aria/name labels"
        )
        findings.append(_browser_finding(
            "unlabeled-controls",
            "Interactive controls lack accessible names",
            CustomerSeverity.medium,
            "A non-technical customer using keyboard or assistive tooling may not know what a control does.",
            "Accessibility and usability regress for operational users reviewing the product quickly.",
            "Any session involving keyboard or assistive technology.",
            "Add visible labels or aria-label/title/name attributes to every interactive control.",
        ))
    else:
        markers.append("STEP_PASS|accessibility-basics|interactive controls have basic names or labels")

    if semantic.get("hasPrivacy") or semantic.get("hasTrustCue") or _contains_any(lowered, _TRUST_CUE_TERMS):
        markers.append("STEP_PASS|trust-copy|page includes at least one data/privacy/trust cue")
    else:
        markers.append("STEP_FAIL|trust-copy|expected privacy/data/trust cues -> none detected")
        findings.append(_browser_finding(
            "missing-trust-copy",
            "No visible trust or data-handling explanation",
            CustomerSeverity.medium,
            f"{profile.persona} must decide whether the product is safe enough for the stated trust threshold: {profile.trust_threshold or 'real customer usage'}.",
            "The product may be functionally usable but still blocked by safety, source, privacy, money, or decision-risk concerns.",
            "Every serious evaluator before they share personal information, act on a result, pay, or rely on the product.",
            "Add concise trust proof near the primary workflow: source/verification, privacy or data handling, risk boundaries, and support/escalation path.",
            trust=True,
        ))

    output_terms = (
        "report", "result", "download", "export", "summary", "next action", "recommend",
        "save", "share", "send", "compare", "contact", "review", "feedback", "apply", "approve",
        "price", "fee", "location", "address", "verified", "phone", "appointment", "booking",
        "báo cáo", "kết quả", "tải", "xuất", "lưu", "chia sẻ", "gửi", "liên hệ", "đánh giá", "phản hồi",
        "giá", "phí", "địa chỉ", "xác minh", "số điện thoại", "đặt lịch", "hẹn",
    )
    if semantic.get("hasOutcomeCue") or _contains_any(lowered, output_terms):
        markers.append("STEP_PASS|output-actionability|page contains output/report/actionability language")
    else:
        markers.append("STEP_FAIL|output-actionability|expected report/result/next-action language -> none detected")
        findings.append(_browser_finding(
            "unclear-output-value",
            "Output value is not clear before use",
            CustomerSeverity.low,
            "The customer may not know what decision, result, saved state, contact path, or next action they will get after completing the workflow.",
            "Lower confidence and conversion before the first run.",
            "Every evaluation session.",
            "Preview the kind of result, decision data, saved item, contact path, or next action the customer will receive.",
        ))

    if semantic.get("hasPricing") or semantic.get("hasSupport") or semantic.get("hasSample"):
        markers.append("STEP_PASS|adoption-readiness|page includes pricing/support/sample/demo/onboarding cues")
    else:
        markers.append("STEP_FAIL|adoption-readiness|expected pricing/support/sample/demo/onboarding cues -> none detected")
        findings.append(_browser_finding(
            "missing-adoption-cues",
            "No visible onboarding, sample, support, or commercial path",
            CustomerSeverity.low,
            "The customer may understand the tool but not know how to pilot it, get help, or buy it.",
            "Buyer readiness stays weak after initial interest.",
            "Every evaluation by a buyer, manager, or team lead.",
            "Add a sample/demo path plus a clear support or next-step commercial route.",
        ))

    domain_terms = _domain_terms(profile, target, plan)
    if not domain_terms:
        markers.append("STEP_SKIP|domain-fit|profile/target did not provide domain-specific terms")
    elif any(term in lowered for term in domain_terms):
        markers.append(f"STEP_PASS|domain-fit|page matches domain term(s): {', '.join(domain_terms[:5])}")
    else:
        markers.append(
            "STEP_FAIL|domain-fit|"
            f"expected domain/workflow terms -> missing {', '.join(domain_terms[:5])}"
        )
        findings.append(_browser_finding(
            "weak-domain-fit",
            "Domain language does not match the stated customer workflow",
            CustomerSeverity.medium,
            f"{profile.persona} may not see their real workflow reflected in the product.",
            "The product feels like a generic demo instead of a credible replacement for the current workflow.",
            "Every customer in the configured domain.",
            "Mirror the customer's workflow terms, inputs, constraints, and current alternatives in the UI and output.",
        ))

    if semantic.get("hasSample") or (_contains_any(lowered, output_terms) and has_actionable_control):
        markers.append("STEP_PASS|time-to-value|page exposes a quick path toward sample/demo/result value")
    else:
        markers.append("STEP_FAIL|time-to-value|expected quick sample/demo/result path -> none detected")
        findings.append(_browser_finding(
            "slow-time-to-value",
            "Time-to-first-value is not obvious",
            CustomerSeverity.low,
            "The customer cannot quickly see how to reach a useful first result.",
            "Fewer evaluators will complete the first run before deciding whether this is worth attention.",
            "Every first evaluation session.",
            "Provide a sample run, template, or clearly labeled shortest path to the first useful output.",
        ))

    if semantic.get("hasCollaboration"):
        markers.append("STEP_PASS|recommendation-clarity|page includes share/export/client/team decision cues")
    else:
        markers.append("STEP_FAIL|recommendation-clarity|expected share/export/team/client cues -> none detected")
        findings.append(_browser_finding(
            "weak-recommendation-clarity",
            "Result is not clearly shareable or explainable",
            CustomerSeverity.low,
            "The daily user may struggle to justify the result to a teammate, client, manager, or buyer.",
            "Adoption slows when the output cannot travel through the customer's approval workflow.",
            "Every workflow requiring review or approval.",
            "Make the output easy to export, share, cite, or explain with evidence-backed next actions.",
        ))

    if probe.get("hasHorizontalOverflow"):
        markers.append("STEP_FAIL|layout-overflow|document is wider than viewport")
        findings.append(_browser_finding(
            "horizontal-overflow",
            "Page has horizontal overflow",
            CustomerSeverity.medium,
            "The customer may need to pan sideways or miss content on the current viewport.",
            "Mobile and narrow-screen review becomes less trustworthy.",
            "Any small-screen or split-screen session.",
            "Fix responsive layout so the document width stays within the viewport.",
        ))
    else:
        markers.append("STEP_PASS|layout-overflow|no horizontal overflow detected")

    mobile_viewport = probe.get("mobileViewport", {}) if isinstance(probe.get("mobileViewport"), dict) else {}
    mobile_width = (mobile_probe.get("viewport") or {}).get("width") if isinstance(mobile_probe.get("viewport"), dict) else None
    if mobile_viewport.get("ok") is False or (isinstance(mobile_width, int) and mobile_width > 500):
        markers.append(
            "STEP_SKIP|mobile-review|"
            "mobile viewport could not be set through scripts/winbrowser; screenshot is desktop-sized"
        )
        findings.append(_browser_finding(
            "mobile-review-not-executed",
            "Mobile review could not run at a phone-width viewport",
            CustomerSeverity.low,
            "The test does not prove the product can be reviewed on a phone.",
            "Customer Loop evidence is weaker for mobile approval or stakeholder review.",
            "Every run on wrappers without viewport support.",
            "Use a browser wrapper that supports viewport resizing before claiming mobile coverage.",
        ))
    elif mobile_probe.get("hasHorizontalOverflow"):
        overflow_detail = _mobile_overflow_detail(mobile_probe)
        markers.append(f"STEP_FAIL|mobile-review|mobile/narrow viewport has horizontal overflow{overflow_detail}")
        findings.append(_browser_finding(
            "mobile-review-overflow",
            "Phone review has horizontal overflow",
            CustomerSeverity.medium,
            "A customer reviewing the result from a phone may miss content or lose confidence in polish.",
            "Approval and stakeholder review are weaker on mobile.",
            "Every narrow-screen review session.",
            "Fix the overflowing element(s), then verify the customer report/action path on a phone-width viewport.",
        ))
    elif mobile_probe:
        markers.append(
            "STEP_PASS|mobile-review|"
            f"mobile probe captured viewport={mobile_probe.get('viewport', {})} with no overflow"
        )
    else:
        markers.append("STEP_SKIP|mobile-review|mobile viewport probe returned no data")

    if failed_resources:
        markers.append(f"STEP_FAIL|resource-health|{len(failed_resources)} zero-size resource(s) detected")
        findings.append(_browser_finding(
            "resource-health",
            "Some page resources appear failed or empty",
            CustomerSeverity.low,
            "The customer may see missing styles, images, scripts, or credibility cues.",
            "Trust and polish are weaker if visible resources fail.",
            "Depends on cache/network path.",
            "Inspect failed resources and remove broken references or serve them correctly.",
        ))
    else:
        markers.append("STEP_PASS|resource-health|no obvious failed resources detected via performance entries")

    for task in plan.tasks:
        markers.append(
            "STEP_SKIP|planned-task|"
            f"{task.id}: planned by rubric but not interactively executed by the deterministic runner"
        )
    markers.append(
        "STEP_SKIP|primary-workflow|"
        "deterministic runner detects workflow cues but does not complete upload/download flows; use manual evidence for full task execution"
    )
    markers.append(
        "STEP_SKIP|jtbd-forces|"
        "semantic customer-readiness cues checked, but push/pull/anxiety/habit require human/agent interpretation"
    )
    markers.append(
        "STEP_SKIP|buyer-user-mismatch|"
        "buyer/operator cues checked heuristically; budget-owner validation requires manual customer-test evidence"
    )
    markers.append(
        "STEP_SKIP|emotional-confidence|"
        "trust, recovery, output, and domain-fit cues checked heuristically; customer confidence requires manual interpretation"
    )
    markers.append(f"STEP_SKIP|customer-context|configured persona={profile.persona}; target={target.url}")
    findings.extend(_build_research_gap_findings(profile, plan))
    return markers, findings


_TRUST_CUE_TERMS = (
    "privacy", "secure", "security", "data", "not stored", "delete", "client",
    "verified", "verification", "trusted", "safe", "safety", "report abuse",
    "policy", "permission", "compliance", "audit", "encrypted", "retention",
    "riêng tư", "bảo mật", "dữ liệu", "an toàn", "xác minh", "chính sách", "quyền",
    "nguồn", "xác thực", "chống lừa đảo", "cảnh báo", "bảo vệ",
)
_ADOPTION_CUE_TERMS = (
    "pricing", "price", "plan", "trial", "pilot", "quote", "book", "demo",
    "sample", "support", "contact", "help", "docs", "email", "chat", "faq",
    "onboarding", "sales", "learn more", "get started",
    "giá", "gói", "dùng thử", "demo", "mẫu", "hỗ trợ", "liên hệ", "trợ giúp",
    "tài liệu", "email", "chat", "câu hỏi thường gặp", "bắt đầu",
)
_ACTIONABILITY_CUE_TERMS = (
    "share", "export", "download", "send", "client", "team", "approve", "save",
    "copy", "link", "report", "result", "summary", "next action", "recommend",
    "compare", "contact", "review", "feedback",
    "chia sẻ", "xuất", "tải", "gửi", "khách hàng", "đội", "duyệt", "lưu",
    "sao chép", "liên kết", "báo cáo", "kết quả", "tóm tắt", "đề xuất",
    "so sánh", "liên hệ", "đánh giá", "phản hồi",
)


def _profile_trust_terms(profile: CustomerProfile) -> list[str]:
    source = " ".join(str(value or "") for value in (profile.trust_threshold, profile.buyer_user_split))
    stop = {
        "the", "and", "for", "with", "that", "this", "must", "need", "needs",
        "proof", "real", "work", "data", "before", "after", "from", "their",
    }
    terms: list[str] = []
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]{4,}", source.lower()):
        if raw not in stop and raw not in terms:
            terms.append(raw)
    return terms[:8]


def _build_research_gap_findings(profile: CustomerProfile, plan: CustomerTestPlan) -> list[CustomerFinding]:
    findings: list[CustomerFinding] = []
    if profile.trust_threshold:
        findings.append(_browser_finding(
            "trust-threshold-not-validated",
            "Stated trust threshold is not proven end-to-end",
            CustomerSeverity.low,
            f"{profile.persona} needs proof for: {profile.trust_threshold}. This run can only check visible cues.",
            "A customer may like the product but still refuse to rely on it until the trust proof is explicit.",
            "Every serious evaluation where personal information, money, permissions, safety, or buyer approval matter.",
            "Add or test a dedicated trust path that proves the threshold with concrete docs, examples, policy, or workflow evidence.",
            trust=True,
        ))
    if profile.buyer_user_split:
        findings.append(_browser_finding(
            "buyer-user-fit-not-validated",
            "Buyer and daily-user concerns are not separately validated",
            CustomerSeverity.low,
            f"The run uses one persona, but the configured buyer/user split is: {profile.buyer_user_split}.",
            "A product can satisfy the operator and still fail manager, security, procurement, or platform-owner approval.",
            "Every purchase or rollout where the user is not the only decision maker.",
            "Run a second buyer/security/manager pass and compare objections against the daily user's workflow value.",
            trust=True,
        ))
    if profile.current_workflow or profile.buying_trigger or profile.alternatives:
        findings.append(_browser_finding(
            "switching-forces-not-validated",
            "Switching motivation and anxiety are not deeply validated",
            CustomerSeverity.low,
            "The browser run can see content and interactions, but it has not modeled push, pull, anxiety, habit, and success metric like a real customer interview.",
            "The report may identify usability issues but still miss why a customer would switch, delay, or reject adoption.",
            "Every product evaluation where alternatives, habits, or internal rollout risk matter.",
            "Run a JTBD pass that states the current habit, trigger, desired progress, anxiety, and proof needed to switch.",
        ))
    return findings


def _target_unreachable_reason(probe: dict, target: ExperienceTarget) -> str:
    url = str(probe.get("url", "") or "")
    title = str(probe.get("title", "") or "")
    body = str(probe.get("bodyText", "") or "").lower()
    if url.startswith("chrome-error://"):
        return f"browser error page reached instead of target; final_url={url}; title={title or target.url}"
    error_terms = (
        "this site can't be reached",
        "this site can’t be reached",
        "dns_probe",
        "err_name_not_resolved",
        "err_connection",
        "err_timed_out",
        "err_ssl",
    )
    if any(term in body for term in error_terms):
        return f"browser/network error text detected at final_url={url or target.url}"
    parsed_target = urlparse(str(target.url))
    parsed_final = urlparse(url)
    if parsed_final.scheme in {"http", "https"} and parsed_target.netloc and parsed_final.netloc:
        final_path = parsed_final.path.lower()
        target_path = parsed_target.path.lower()
        auth_path = any(term in final_path for term in ("/login", "/log-in", "/signin", "/sign-in", "/auth"))
        if auth_path and final_path != target_path:
            return f"target redirected to login before product access; final_url={url}"
    return ""


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    folded = _ascii_fold(lowered)
    return any(
        term
        and (str(term).lower() in lowered or _ascii_fold(str(term).lower()) in folded)
        for term in terms
    )


def _searchable_text(text: str) -> str:
    lowered = str(text).lower()
    folded = _ascii_fold(lowered)
    return f"{lowered} {folded}" if folded != lowered else lowered


def _ascii_fold(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def _mobile_overflow_detail(mobile_probe: dict) -> str:
    detail_parts: list[str] = []
    if mobile_probe.get("overflowWidth"):
        detail_parts.append(f"overflowWidth={mobile_probe.get('overflowWidth')}")
    offenders = mobile_probe.get("overflowOffenders")
    if isinstance(offenders, list):
        offender_texts: list[str] = []
        for offender in offenders[:3]:
            if not isinstance(offender, dict):
                continue
            label = str(offender.get("selector") or offender.get("tag") or "element")
            text = str(offender.get("text") or "").strip()
            width = offender.get("width")
            offender_texts.append(f"{label} width={width}: {text[:60]}".strip())
        if offender_texts:
            detail_parts.append("offenders=" + " | ".join(offender_texts))
    return "; " + "; ".join(detail_parts) if detail_parts else ""


def _domain_terms(profile: CustomerProfile, target: ExperienceTarget, plan: CustomerTestPlan) -> list[str]:
    sources = [
        profile.role,
        profile.domain_literacy,
        profile.current_workflow,
        profile.buying_trigger,
        profile.buyer_user_split,
        profile.trust_threshold,
        target.context,
        plan.customer_job.functional,
        *(profile.alternatives or []),
    ]
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "their", "your", "whether",
        "product", "customer", "workflow", "target", "evaluate", "complete", "real", "work",
        "user", "buyer", "role", "data", "result", "team", "manager", "client",
    }
    terms: list[str] = []
    for source in sources:
        for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(source).lower()):
            if raw not in stop and raw not in terms:
                terms.append(raw)
    return terms[:12]


def _score_customer_readiness(probe: dict, findings: list[CustomerFinding]) -> CustomerScores:
    penalties = {
        CustomerSeverity.critical: 4,
        CustomerSeverity.high: 3,
        CustomerSeverity.medium: 2,
        CustomerSeverity.low: 1,
        CustomerSeverity.positive: 0,
    }
    ids = {finding.id for finding in findings}

    def score(base: int, relevant: set[str]) -> int:
        value = base
        for finding in findings:
            if finding.id in relevant:
                value -= penalties[finding.severity]
        return max(1, min(10, value))

    timing = probe.get("timingMs")
    reliability_base = 9
    if isinstance(timing, int) and timing > 5000:
        reliability_base -= 2
    if probe.get("failedResources"):
        reliability_base -= 1

    return CustomerScores(
        job_importance=8,
        value=score(8, {"unclear-customer-promise", "weak-domain-fit", "unclear-output-value"}),
        time_to_value=score(8, {"slow-time-to-value", "missing-core-workflow", "target-unreachable"}),
        task_success=score(8, {
            "missing-core-workflow",
            "first-impression-empty",
            "missing-error-recovery-cues",
            "target-unreachable",
        }),
        usability=score(8, {"unlabeled-controls", "horizontal-overflow", "mobile-review-overflow"}),
        trust_readiness=score(8, {
            "missing-trust-copy",
            "missing-error-recovery-cues",
            "resource-health",
            "trust-threshold-not-validated",
        }),
        output_actionability=score(8, {"unclear-output-value", "weak-recommendation-clarity"}),
        domain_fit=score(8, {"weak-domain-fit", "unclear-customer-promise"}),
        buying_readiness=score(7, {
            "missing-adoption-cues",
            "missing-trust-copy",
            "weak-domain-fit",
            "buyer-user-fit-not-validated",
            "trust-threshold-not-validated",
        }),
        retention_likelihood=score(7, {
            "slow-time-to-value",
            "unclear-output-value",
            "weak-domain-fit",
            "switching-forces-not-validated",
        }),
        emotional_confidence=score(8, {
            "missing-trust-copy",
            "missing-error-recovery-cues",
            "resource-health",
            "switching-forces-not-validated",
            "target-unreachable",
        }),
        technical_reliability=max(1, min(10, reliability_base - (1 if "resource-health" in ids else 0))),
    )


def _control_is_actionable(control: dict) -> bool:
    if control.get("disabled"):
        return False
    tag = str(control.get("tag", ""))
    input_type = str(control.get("type", "")).lower()
    if tag in {"button", "textarea", "select"}:
        return True
    return tag == "input" and input_type not in {"hidden", "checkbox", "radio"}


def _browser_finding(
    finding_id: str,
    title: str,
    severity: CustomerSeverity,
    customer_interpretation: str,
    business_impact: str,
    likely_frequency: str,
    recommendation: str,
    *,
    confidence: float = 0.7,
    trust: bool = False,
    core: bool = False,
) -> CustomerFinding:
    return CustomerFinding(
        id=finding_id,
        title=title,
        severity=severity,
        customer_interpretation=customer_interpretation,
        business_impact=business_impact,
        likely_frequency=likely_frequency,
        recommendation=recommendation,
        confidence=confidence,
        trust_blocker=trust,
        core_task_blocker=core,
    )


def _summarize_probe(probe: dict, markers: list[str]) -> str:
    headings = "; ".join(str(item) for item in probe.get("headings", [])[:3])
    failed = len([marker for marker in markers if marker.startswith("STEP_FAIL|")])
    passed = len([marker for marker in markers if marker.startswith("STEP_PASS|")])
    return (
        f"Rich customer browser probe completed for {probe.get('url', '')}. "
        f"Title: {probe.get('title', '')}. Headings: {headings or 'none'}. "
        f"Markers: {passed} pass, {failed} fail."
    )


def _build_interactive_findings(interaction: dict) -> tuple[list[str], list[CustomerFinding]]:
    markers: list[str] = []
    findings: list[CustomerFinding] = []
    if not interaction.get("clicked"):
        reason = interaction.get("reason", "no interactive action was executed")
        markers.append(f"STEP_SKIP|interactive-sample-flow|{reason}")
        findings.append(_browser_finding(
            "interactive-flow-not-available",
            "No sample or demo action is available for automated customer flow testing",
            CustomerSeverity.low,
            "The customer has no quick, low-risk way to see the product produce value.",
            "Time-to-value and evaluation confidence are weaker without a sample path.",
            "Every first-time evaluation where the customer does not have prepared input.",
            "Add a sample/demo action or provide a task-specific interactive runner configuration.",
        ))
        return markers, findings

    after = interaction.get("after", {}) if isinstance(interaction.get("after"), dict) else {}
    produced_result = bool(
        after.get("downloadEnabled")
        or re.search(
            r"completed|complete|done|success|verdict|blocker|warning|next action|report",
            " ".join(str(after.get(key, "")) for key in ("bodyTextSample", "statusText", "resultText")),
            re.I,
        )
    )
    if produced_result:
        markers.append(
            "STEP_PASS|interactive-sample-flow|"
            f"clicked {interaction.get('actionText', 'sample/demo')} and observed result/download cues"
        )
    else:
        markers.append(
            "STEP_FAIL|interactive-sample-flow|"
            "expected visible result/download cues after sample/demo click -> none detected"
        )
        findings.append(_browser_finding(
            "interactive-sample-flow-no-result",
            "Sample or demo action does not produce a visible customer result",
            CustomerSeverity.high,
            "The customer clicks a low-risk first action but does not get clear proof of value.",
            "Activation is blocked because the first interactive path fails to create an understandable result.",
            "Every first-time user relying on the sample path.",
            "Make the sample/demo action render a visible result, next actions, and report/download cue.",
            core=True,
        ))
    return markers, findings


def _summarize_interaction(interaction: dict, markers: list[str]) -> str:
    failed = len([marker for marker in markers if marker.startswith("STEP_FAIL|")])
    passed = len([marker for marker in markers if marker.startswith("STEP_PASS|")])
    skipped = len([marker for marker in markers if marker.startswith("STEP_SKIP|")])
    if interaction.get("clicked"):
        return (
            f"Interactive sample/demo flow clicked {interaction.get('actionText', 'an action')}. "
            f"Markers: {passed} pass, {failed} fail, {skipped} skip."
        )
    return (
        f"Interactive sample/demo flow skipped: {interaction.get('reason', 'no action found')}. "
        f"Markers: {passed} pass, {failed} fail, {skipped} skip."
    )


def _summarize_flow_pack(flow_pack: CustomerFlowPack, markers: list[str]) -> str:
    failed = len([marker for marker in markers if marker.startswith("STEP_FAIL|")])
    passed = len([marker for marker in markers if marker.startswith("STEP_PASS|")])
    skipped = len([marker for marker in markers if marker.startswith("STEP_SKIP|")])
    return (
        f"Configured customer flow pack `{flow_pack.name}` executed across {len(flow_pack.flows)} flow(s). "
        f"Markers: {passed} pass, {failed} fail, {skipped} skip."
    )


def _finding_from_manual_text(text: str, evidence: CustomerEvidence) -> CustomerFinding | None:
    severity = CustomerSeverity.medium
    match = re.search(r"severity\s*[:|-]\s*(critical|high|medium|low|positive)", text, re.I)
    heading_match = re.search(
        r"^\s*#{2,6}\s*(critical|high|medium|low|positive)\s*[-:]\s*(.+?)\s*$",
        text,
        re.I | re.M,
    )
    if match:
        severity = CustomerSeverity(match.group(1).lower())
    elif heading_match:
        severity = CustomerSeverity(heading_match.group(1).lower())
    title = _extract_labeled(text, "title") or (heading_match.group(2).strip() if heading_match else "")
    title = title or _first_nonempty_line(text)
    if not title:
        return None
    recommendation = _extract_labeled(text, "recommendation") or _extract_labeled(text, "recommended fix")
    customer_interpretation = (
        _extract_labeled(text, "customer interpretation")
        or _extract_labeled(text, "customer impact")
    )
    trust_blocker = _extract_labeled_bool(text, "trust blocker", default="trust" in text.lower())
    core_task_blocker = _extract_labeled_bool(
        text,
        "core task blocker",
        default=any(token in text.lower() for token in ("blocked", "cannot", "can't", "fails")),
    )
    return CustomerFinding(
        id="manual-001",
        title=title[:160],
        severity=severity,
        evidence=[evidence],
        customer_interpretation=customer_interpretation,
        business_impact=(
            _extract_labeled(text, "business impact")
            or _extract_labeled(text, "business/product impact")
        ),
        likely_frequency=_extract_labeled(text, "likely frequency"),
        recommendation=recommendation,
        confidence=0.75,
        trust_blocker=trust_blocker,
        core_task_blocker=core_task_blocker,
    )


def _extract_labeled(text: str, label: str) -> str:
    pattern = rf"^[ \t]*(?:[-*][ \t]*)?{re.escape(label)}[ \t]*[:|-][ \t]*(.*?)[ \t]*$"
    match = re.search(pattern, text, re.I | re.M)
    if not match:
        return ""
    inline = match.group(1).strip()
    if inline:
        return inline

    lines = text[match.end():].splitlines()
    block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped and not block:
            continue
        if not stripped:
            break
        if re.match(r"^#{1,6}\s+", stripped):
            break
        if re.match(r"^[A-Za-z][A-Za-z /-]{1,60}\s*:\s*$", stripped):
            break
        block.append(stripped)
    return _normalize_extracted_block(block)


def _normalize_extracted_block(lines: list[str]) -> str:
    if not lines:
        return ""
    if any(line.startswith(("-", "*")) for line in lines):
        return "\n".join(lines)
    return " ".join(lines)


def _extract_labeled_bool(text: str, label: str, *, default: bool = False) -> bool:
    raw = _extract_labeled(text, label)
    if not raw:
        return default
    return raw.lower().split()[0].strip(".,;") in {"yes", "true", "1", "y"}
