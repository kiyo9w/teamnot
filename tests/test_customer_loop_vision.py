from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

from teamnot.customer_loop import (
    CodexCliVisionReviewer,
    CustomerProfile,
    DeterministicScreenshotReviewer,
    ExperienceTarget,
    ScreenshotCaptureRecord,
)


def _write_png_header(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def test_deterministic_screenshot_reviewer_groups_and_enriches_captures(tmp_path: Path):
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    _write_png_header(before, 320, 200)
    _write_png_header(after, 320, 200)

    review = DeterministicScreenshotReviewer().review([
        ScreenshotCaptureRecord(path=str(before), route="/reports", action="run", success=True, sha256="a"),
        ScreenshotCaptureRecord(path=str(after), route="/reports", action="run", success=True, sha256="b"),
    ])

    assert review.review_kind == "heuristic"
    assert review.screenshot_count == 2
    assert review.groups[0].group_id == "/reports:run"
    assert review.groups[0].screenshots[0].width == 320
    assert "Hash changed" in review.groups[0].notes[0]
    assert "no model visual judgment" in review.judgment_summary


def test_deterministic_screenshot_reviewer_surfaces_missing_capture_as_blocker(tmp_path: Path):
    missing = tmp_path / "missing.png"

    review = DeterministicScreenshotReviewer().review([
        ScreenshotCaptureRecord(path=str(missing), route="/reports", action="run", success=True),
    ])

    assert review.review_kind == "heuristic"
    assert review.groups[0].screenshots[0].success is False
    assert review.blockers == ["1 screenshot capture(s) missing or failed."]


def test_codex_vision_reviewer_attaches_images_and_parses_visual_judgment(tmp_path: Path):
    image = tmp_path / "screen.png"
    _write_png_header(image, 1280, 900)
    calls = []

    def command_runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({
                "judgment_summary": "Visual model reviewed the app.",
                "visual_findings": [{
                    "title": "CTA is visually buried",
                    "severity": "medium",
                    "customer_interpretation": "The customer may not know where to start.",
                    "recommendation": "Increase visual priority of the primary action.",
                    "action_hint": "click the primary CTA",
                    "evidence_paths": [str(image)],
                    "confidence": 0.82,
                }],
                "action_hints": ["try the primary CTA"],
            }),
            stderr="",
        )

    review = CodexCliVisionReviewer(
        target=ExperienceTarget(url="https://example-product.test"),
        profile=CustomerProfile(persona="Operator", role="ops"),
        cli_path="codex",
        command_runner=command_runner,
    ).review([
        ScreenshotCaptureRecord(path=str(image), route="/", action="first_impression", success=True),
    ])

    assert review.review_kind == "model_vision"
    assert review.model_worker == "codex_cli"
    assert review.visual_findings[0].title == "CTA is visually buried"
    assert review.action_hints == ["try the primary CTA"]
    command, kwargs = calls[0]
    assert ["--image", str(image)] == command[command.index("--image"):command.index("--image") + 2]
    assert "Return ONLY JSON" in kwargs["input"]


def test_codex_vision_reviewer_keeps_deterministic_artifact_when_worker_fails(tmp_path: Path):
    image = tmp_path / "screen.png"
    _write_png_header(image, 320, 200)

    def command_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="not logged in")

    review = CodexCliVisionReviewer(command_runner=command_runner).review([
        ScreenshotCaptureRecord(path=str(image), route="/", action="first_impression", success=True),
    ])

    assert review.review_kind == "model_vision_blocked"
    assert review.screenshot_count == 1
    assert "not logged in" in review.blockers[-1]
