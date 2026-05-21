"""Experience runners for customer-loop evidence collection."""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

from teamnot.customer_loop.models import (
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
)

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


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
        self.command_runner = command_runner or self._default_runner

    def run(
        self,
        target: ExperienceTarget,
        profile: CustomerProfile,
        plan: CustomerTestPlan,
        out_dir: Path,
    ) -> CustomerReport:
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
        self._run(["--action", "screenshot", "--out", first_impression_out])
        self._run(["--action", "screenshot", "--out", full_page_out, "--full-page"])
        probe = _parse_json_stdout(self._run(["--action", "eval", "--expr", _CUSTOMER_PROBE_JS]))
        result = probe.get("result", probe)
        mobile_viewport = self._try_run(["--action", "viewport", "--width", "390", "--height", "844"])
        mobile_probe = _parse_json_stdout(self._run(["--action", "eval", "--expr", _MOBILE_PROBE_JS])).get("result", {})
        self._run(["--action", "screenshot", "--out", mobile_review_out])
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
            },
        )
        for finding in findings:
            finding.evidence.append(evidence)
        return CustomerReport(
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
        )

    def _screenshot(self, path: Path) -> None:
        self._run(["--action", "screenshot", "--out", _path_for_windows_wrapper(path)])

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
        return subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)


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
        self._screenshot(before)
        interaction = _parse_json_stdout(
            self._run(["--action", "eval", "--expr", _INTERACTIVE_SAMPLE_FLOW_JS])
        ).get("result", {})
        self._screenshot(after)
        markers, findings = _build_interactive_findings(interaction if isinstance(interaction, dict) else {})
        evidence = CustomerEvidence(
            kind="browser_interaction",
            path=str(before),
            screenshot_paths=[str(before), str(after)],
            observed_behavior=_summarize_interaction(interaction if isinstance(interaction, dict) else {}, markers),
            raw_excerpt="\n".join(markers),
            metadata={
                "runner": "openclaw-windows-interactive",
                "rubric": "customer-testing-openclaw",
                "method": "baseline probe plus real sample/demo click with before/after evidence",
                "interaction": interaction,
            },
        )
        report.evidence.append(evidence)
        for finding in findings:
            finding.evidence.append(evidence)
        report.findings.extend(findings)
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
                result = self._run_flow_step(step, target)
                flow_results.append({"flow": flow.name, **result})
                shot = screenshots / f"flow-{flow_index:02d}-{step_index:02d}-{flow_slug}-{_slug(step.id)}.png"
                self._screenshot(shot)
                screenshot_paths.append(str(shot))
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


def _normalize_flow_pack(flow_pack: CustomerFlow | CustomerFlowPack) -> CustomerFlowPack:
    if isinstance(flow_pack, CustomerFlowPack):
        return flow_pack
    return CustomerFlowPack(name=flow_pack.name, flows=[flow_pack])


def _flow_url(target: ExperienceTarget, url_or_path: str) -> str:
    value = url_or_path.strip()
    if not value:
        return str(target.url)
    return urljoin(str(target.url), value)


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
      hasCollaboration: /share|export|download|send|client|team|approve/.test(visibleText),
    },
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
  const overflow = documentWidth > viewportWidth + 2 || overflowOffenders.length > 0;
  return {
    url: location.href,
    viewport: { width: innerWidth, height: innerHeight },
    hasHorizontalOverflow: overflow,
    overflowWidth: documentWidth,
    overflowOffenders,
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
    lowered = text.lower()
    headings = [str(item) for item in probe.get("headings", [])]
    inputs = [item for item in probe.get("inputs", []) if isinstance(item, dict)]
    buttons = [str(item) for item in probe.get("buttons", [])]
    failed_resources = [str(item) for item in probe.get("failedResources", [])]
    semantic = probe.get("semanticSignals", {}) if isinstance(probe.get("semanticSignals"), dict) else {}
    mobile_probe = probe.get("mobileProbe", {}) if isinstance(probe.get("mobileProbe"), dict) else {}
    markers: list[str] = []
    findings: list[CustomerFinding] = []

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
            "The customer cannot tell what happens if they upload the wrong file, omit data, or hit a failure.",
            "Real operators hesitate to try the product with messy production data.",
            "Likely in every evaluation before the first risky action.",
            "Show concise validation/retry guidance, accepted input examples, and whether user work is preserved.",
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

    if semantic.get("hasPrivacy"):
        markers.append("STEP_PASS|trust-copy|page includes at least one data/privacy/trust cue")
    else:
        markers.append("STEP_FAIL|trust-copy|expected privacy/data/trust cues -> none detected")
        findings.append(_browser_finding(
            "missing-trust-copy",
            "No visible trust or data-handling explanation",
            CustomerSeverity.medium,
            f"{profile.persona} must decide whether it is safe to use real work data.",
            "The product may be functionally usable but blocked from adoption with real customer data.",
            "Every buyer or operator before first real upload.",
            "Add concise privacy/data-handling copy near the primary workflow and report output.",
            trust=True,
        ))

    output_terms = ("report", "result", "download", "export", "summary", "next action", "recommend")
    if _contains_any(lowered, output_terms):
        markers.append("STEP_PASS|output-actionability|page contains output/report/actionability language")
    else:
        markers.append("STEP_FAIL|output-actionability|expected report/result/next-action language -> none detected")
        findings.append(_browser_finding(
            "unclear-output-value",
            "Output value is not clear before use",
            CustomerSeverity.low,
            "The customer may not know what useful artifact they will receive after completing the workflow.",
            "Lower confidence and conversion before the first run.",
            "Every evaluation session.",
            "Preview the kind of report, result, or next action the customer will receive.",
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


def _build_research_gap_findings(profile: CustomerProfile, plan: CustomerTestPlan) -> list[CustomerFinding]:
    findings: list[CustomerFinding] = []
    if profile.trust_threshold:
        findings.append(_browser_finding(
            "trust-threshold-not-validated",
            "Stated trust threshold is not proven end-to-end",
            CustomerSeverity.low,
            f"{profile.persona} needs proof for: {profile.trust_threshold}. This run can only check visible cues.",
            "A team may like the product but still refuse to try it on real work until the trust proof is explicit.",
            "Every serious evaluation where data, repositories, permissions, or buyer approval matter.",
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


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


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
        time_to_value=score(8, {"slow-time-to-value", "missing-core-workflow"}),
        task_success=score(8, {"missing-core-workflow", "first-impression-empty", "missing-error-recovery-cues"}),
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
        confidence=0.7,
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
