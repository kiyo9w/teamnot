"""Portable screenshot review contracts for customer-loop runs."""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from teamnot.customer_loop.models import (
    CustomerProfile,
    ExperienceTarget,
    ScreenshotCaptureRecord,
    VisionReviewArtifact,
    VisionScreenshotGroup,
    VisualFinding,
)


class VisionReviewer(Protocol):
    def review(self, records: list[ScreenshotCaptureRecord]) -> VisionReviewArtifact:
        """Return visual evidence metadata without requiring a metered model."""


class DeterministicScreenshotReviewer:
    """Collect screenshot metadata and conservative heuristics.

    This reviewer does not perform visual judgment. It only reports capture
    health, dimensions, grouping, and hash-level change signals.
    """

    def review(self, records: list[ScreenshotCaptureRecord]) -> VisionReviewArtifact:
        enriched = [_enrich_record(record) for record in records]
        groups: dict[str, list[ScreenshotCaptureRecord]] = defaultdict(list)
        for record in enriched:
            groups[_group_id(record)].append(record)
        heuristics: list[str] = []
        blockers: list[str] = []
        missing = [record for record in enriched if not record.success or not record.path]
        if missing:
            blockers.append(f"{len(missing)} screenshot capture(s) missing or failed.")
        zero_size = [
            record for record in enriched
            if record.success and (record.width == 0 or record.height == 0)
        ]
        if zero_size:
            blockers.append(f"{len(zero_size)} screenshot capture(s) had zero dimensions.")
        changed_groups = 0
        for screenshots in groups.values():
            hashes = {record.sha256 for record in screenshots if record.sha256}
            if len(hashes) > 1:
                changed_groups += 1
        if changed_groups:
            heuristics.append(f"{changed_groups} screenshot group(s) changed by hash across before/after captures.")
        if enriched and not heuristics:
            heuristics.append("Screenshots were captured, but deterministic metadata found no hash-level visual change.")
        return VisionReviewArtifact(
            review_kind="heuristic" if heuristics or blockers else "metadata_only",
            screenshot_count=len(enriched),
            groups=[
                VisionScreenshotGroup(group_id=group_id, screenshots=screenshots, notes=_group_notes(screenshots))
                for group_id, screenshots in sorted(groups.items())
            ],
            heuristics=heuristics,
            blockers=blockers,
        )


class CodexCliVisionReviewer:
    """Attach screenshots to Codex CLI and merge model visual judgment into the review.

    The deterministic reviewer remains the base layer so customer-loop tests and
    reports are still useful when the CLI is unavailable or the model output is
    malformed.
    """

    def __init__(
        self,
        target: ExperienceTarget | None = None,
        profile: CustomerProfile | None = None,
        *,
        cli_path: str = "codex",
        model: str | None = None,
        timeout: int = 240,
        max_images: int = 8,
        command_runner=None,
    ):
        self.target = target
        self.profile = profile
        self.cli_path = cli_path
        self.model = model or os.environ.get("TEAMNOT_VISION_MODEL")
        self.timeout = timeout
        self.max_images = max_images
        self.command_runner = command_runner or subprocess.run

    def review(self, records: list[ScreenshotCaptureRecord]) -> VisionReviewArtifact:
        base = DeterministicScreenshotReviewer().review(records)
        images = _reviewable_images(base.groups, self.max_images)
        if not images:
            return base.model_copy(update={
                "review_kind": "model_vision_unavailable",
                "model_worker": "codex_cli",
                "blockers": [*base.blockers, "No successful screenshot files were available for model vision review."],
            })
        command = [self.cli_path, "exec"]
        if self.model:
            command.extend(["--model", self.model])
        command.extend([
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "-C",
            str(Path.cwd()),
        ])
        for image in images:
            command.extend(["--image", image])
        command.append("-")
        try:
            result = self.command_runner(
                command,
                input=_vision_prompt(self.target, self.profile, images),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                env=_vision_worker_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _model_blocked_review(base, f"Codex vision worker failed before judgment: {exc}")
        if result.returncode != 0:
            return _model_blocked_review(
                base,
                f"Codex vision worker exited {result.returncode}: {(result.stderr or result.stdout)[:500]}",
            )
        parsed = _parse_model_json(result.stdout or "")
        if not parsed:
            return _model_blocked_review(
                base,
                f"Codex vision worker returned non-JSON output: {(result.stdout or '')[:500]}",
            )
        findings = [
            VisualFinding.model_validate(item)
            for item in parsed.get("visual_findings", [])
            if isinstance(item, dict) and item.get("title")
        ]
        action_hints = [str(item) for item in parsed.get("action_hints", []) if str(item).strip()]
        summary = str(parsed.get("judgment_summary") or "").strip()
        return base.model_copy(update={
            "review_kind": "model_vision",
            "evidence_source": "screenshot pixels reviewed by Codex CLI vision worker plus deterministic metadata",
            "model_worker": "codex_cli",
            "visual_findings": findings,
            "action_hints": action_hints,
            "judgment_summary": summary or "Codex CLI reviewed screenshots and produced model visual judgment.",
        })


def reviewer_from_environment(
    target: ExperienceTarget | None = None,
    profile: CustomerProfile | None = None,
) -> VisionReviewer:
    worker = os.environ.get("TEAMNOT_VISION_WORKER", "").strip().lower()
    enabled = os.environ.get("TEAMNOT_ENABLE_MODEL_VISION", "").strip().lower() in {"1", "true", "yes"}
    if worker in {"codex", "codex_cli"} or enabled:
        return CodexCliVisionReviewer(target=target, profile=profile)
    return DeterministicScreenshotReviewer()


def _enrich_record(record: ScreenshotCaptureRecord) -> ScreenshotCaptureRecord:
    if not record.path:
        return record
    path = Path(record.path)
    if not path.exists():
        return record.model_copy(update={"success": False})
    width, height = _png_size(path)
    return record.model_copy(update={"width": record.width or width, "height": record.height or height})


def _png_size(path: Path) -> tuple[int | None, int | None]:
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None, None
    if len(header) >= 24 and header.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", header[16:24])
    return None, None


def _group_id(record: ScreenshotCaptureRecord) -> str:
    route = record.route or "unrouted"
    action = record.action or Path(record.path).stem if record.path else "capture"
    return f"{route}:{action}"


def _group_notes(screenshots: list[ScreenshotCaptureRecord]) -> list[str]:
    notes: list[str] = []
    if any(record.fallback_reason for record in screenshots):
        notes.append("At least one capture used fallback screenshot metadata.")
    hashes = {record.sha256 for record in screenshots if record.sha256}
    if len(hashes) > 1:
        notes.append("Hash changed within this screenshot group.")
    if any(not record.success for record in screenshots):
        notes.append("At least one capture failed.")
    return notes


def _reviewable_images(groups: list[VisionScreenshotGroup], max_images: int) -> list[str]:
    selected: list[str] = []
    for group in groups:
        for record in group.screenshots:
            if record.success and record.path and Path(record.path).exists():
                selected.append(record.path)
                break
        if len(selected) >= max_images:
            break
    return selected


def _vision_prompt(
    target: ExperienceTarget | None,
    profile: CustomerProfile | None,
    images: list[str],
) -> str:
    return (
        "You are TeamNoT's customer research vision brain. Review the attached product "
        "screenshots like a real buyer/operator, not like a DOM smoke test.\n\n"
        f"Target: {target.url if target else 'unknown'}\n"
        f"Customer persona: {profile.persona if profile else 'unknown'}\n"
        f"Role: {profile.role if profile else 'unknown'}\n"
        f"Trust threshold: {profile.trust_threshold if profile else 'unknown'}\n"
        f"Images: {', '.join(images)}\n\n"
        "Return ONLY JSON with this shape:\n"
        "{\n"
        '  "judgment_summary": "one concise visual judgment boundary and verdict",\n'
        '  "visual_findings": [\n'
        "    {\n"
        '      "title": "customer-visible visual issue or positive signal",\n'
        '      "severity": "critical|high|medium|low|positive",\n'
        '      "customer_interpretation": "what a customer would infer from the pixels",\n'
        '      "recommendation": "specific UI/product change or validation action",\n'
        '      "action_hint": "next browser action the researcher should try",\n'
        '      "evidence_paths": ["matching screenshot path"],\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ],\n"
        '  "action_hints": ["next visual/customer action to try"]\n'
        "}\n"
    )


def _parse_model_json(text: str) -> dict | None:
    stripped = text.strip()
    candidates = [stripped]
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    candidates.extend(fenced)
    brace = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _model_blocked_review(base: VisionReviewArtifact, blocker: str) -> VisionReviewArtifact:
    return base.model_copy(update={
        "review_kind": "model_vision_blocked",
        "model_worker": "codex_cli",
        "blockers": [*base.blockers, blocker],
        "judgment_summary": (
            "Deterministic screenshot metadata was collected, but model visual judgment was requested "
            "and did not complete."
        ),
    })


def _vision_worker_env() -> dict[str, str]:
    env = os.environ.copy()
    current_home = Path(env.get("CODEX_HOME", "")).expanduser()
    default_home = Path.home() / ".codex"
    if not (current_home / "auth.json").exists() and (default_home / "auth.json").exists():
        env["CODEX_HOME"] = str(default_home)
    env.setdefault("TERM", "xterm-256color")
    return env
