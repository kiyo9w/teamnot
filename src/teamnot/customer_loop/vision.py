"""Portable screenshot review contracts for customer-loop runs."""
from __future__ import annotations

import struct
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from teamnot.customer_loop.models import (
    ScreenshotCaptureRecord,
    VisionReviewArtifact,
    VisionScreenshotGroup,
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
