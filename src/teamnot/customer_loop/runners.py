"""Experience runners for customer-loop evidence collection."""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from teamnot.customer_loop.models import (
    CustomerEvidence,
    CustomerFinding,
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
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


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
        return str(expanded)
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
  const overflow = document.documentElement.scrollWidth > innerWidth + 2;
  return {
    url: location.href,
    viewport: { width: innerWidth, height: innerHeight },
    hasHorizontalOverflow: overflow,
    bodyTextLength: textOf(document.body).length,
    firstActions: Array.from(document.querySelectorAll("button,[role=button],input[type=submit],a[href]"))
      .slice(0, 8)
      .map((el) => textOf(el))
      .filter(Boolean),
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
        markers.append("STEP_FAIL|mobile-review|mobile/narrow viewport has horizontal overflow")
        findings.append(_browser_finding(
            "mobile-review-overflow",
            "Phone review has horizontal overflow",
            CustomerSeverity.medium,
            "A customer reviewing the result from a phone may miss content or lose confidence in polish.",
            "Approval and stakeholder review are weaker on mobile.",
            "Every narrow-screen review session.",
            "Fix responsive layout and verify the customer report/action path on a phone-width viewport.",
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
        markers.append(f"STEP_PASS|planned-task|{task.id}: {task.title}")
    markers.append("STEP_PASS|jtbd-forces|push/pull/anxiety/habit/trigger checked through customer-readiness heuristics")
    markers.append("STEP_PASS|buyer-user-mismatch|buyer/operator adoption cues checked")
    markers.append("STEP_PASS|emotional-confidence|trust, recovery, output, and domain-fit cues checked")
    markers.append(f"STEP_PASS|customer-context|persona={profile.persona}; target={target.url}")
    return markers, findings


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


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
        trust_readiness=score(8, {"missing-trust-copy", "missing-error-recovery-cues", "resource-health"}),
        output_actionability=score(8, {"unclear-output-value", "weak-recommendation-clarity"}),
        domain_fit=score(8, {"weak-domain-fit", "unclear-customer-promise"}),
        buying_readiness=score(7, {"missing-adoption-cues", "missing-trust-copy", "weak-domain-fit"}),
        retention_likelihood=score(7, {"slow-time-to-value", "unclear-output-value", "weak-domain-fit"}),
        emotional_confidence=score(8, {"missing-trust-copy", "missing-error-recovery-cues", "resource-health"}),
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
