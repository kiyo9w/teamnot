from __future__ import annotations

import struct
from pathlib import Path

from teamnot.customer_loop import DeterministicScreenshotReviewer, ScreenshotCaptureRecord


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
