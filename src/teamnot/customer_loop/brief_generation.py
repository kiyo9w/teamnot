"""Deterministic follow-up TeamNoT brief generation."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from teamnot.brief import Brief
from teamnot.customer_loop.models import CustomerFinding, CustomerReport, GeneratedBrief


def generate_followup_brief(
    report: CustomerReport,
    finding: CustomerFinding,
    out_dir: Path,
    previous_brief: Brief | None = None,
) -> GeneratedBrief:
    project_path = previous_brief.project.path if previous_brief else Path.cwd()
    project_name = previous_brief.project.name if previous_brief else project_path.name
    finding_suffix = re.sub(r"[^A-Za-z0-9-]+", "-", finding.id).strip("-").upper() or "FINDING"
    task_id = f"CUSTOMER-LOOP-{finding_suffix}"
    description = f"""Customer Loop selected this as the next highest-impact move.

Target: {report.target.url}
Customer persona: {report.profile.persona} ({report.profile.role})
Finding severity: {finding.severity.value}
Finding: {finding.title}

Customer interpretation:
{finding.customer_interpretation or 'Not specified in the evidence.'}

Business impact:
{finding.business_impact or 'Not specified in the evidence.'}

Likely frequency:
{finding.likely_frequency or 'Not specified in the evidence.'}

Required behavior:
{finding.recommendation or 'Remove the customer-impact blocker described by the finding.'}

Evidence:
{_evidence_refs(finding)}
"""
    data: dict[str, Any] = {
        "schema_version": "2.0",
        "project": {
            "name": project_name,
            "path": str(project_path),
            "language": previous_brief.project.language if previous_brief else [],
            "stack": previous_brief.project.stack if previous_brief else [],
        },
        "task": {
            "id": task_id,
            "title": f"Customer Loop: {finding.title}",
            "description": description,
            "constraints": {
                "no_deploy": True,
                "no_main_commit": True,
                "no_secrets_in_code": True,
                "no_force_push": True,
                "no_destructive_git": True,
                "extra": [
                    "Do not broaden scope beyond the selected customer-impact finding.",
                    "Keep OpenClaw/browser control optional and outside TeamNoT core imports.",
                    "Preserve existing CLI behavior and deterministic tests.",
                ],
            },
            "references": [
                str(out_dir / "customer_report.md"),
                str(out_dir / "customer_report.json"),
            ],
        },
        "definition_of_done": {
            "checks": [
                {"name": "tests pass", "run": "pytest -q"},
                {"name": "lint passes", "run": "ruff check ."},
                {
                    "name": "customer report reference exists",
                    "file_exists": str(out_dir / "customer_report.json"),
                    "required": False,
                },
            ],
            "llm_judge_required": False,
        },
        "deliverable": {
            "type": "feature_branch",
            "branch": f"feature/{task_id.lower()}",
            "push_remote": False,
        },
        "budget": {
            "max_minutes": 120,
            "max_usd": 0,
            "allowed_metered_workers": [],
            "require_explicit_api_optin": True,
        },
        "notes": (
            "Non-goals: do not add broad polish, deployment, metered API calls, or unrelated "
            "refactors before the selected customer-critical issue is addressed."
        ),
        "metadata": {
            "source": "teamnot.customer_loop",
            "selected_finding_id": finding.id,
            "severity": finding.severity.value,
        },
    }
    return GeneratedBrief(
        task_id=task_id,
        title=data["task"]["title"],
        selected_finding_id=finding.id,
        yaml=data,
    )


def _evidence_refs(finding: CustomerFinding) -> str:
    refs: list[str] = []
    for item in finding.evidence:
        if item.path:
            refs.append(f"- {item.path}")
        refs.extend(f"- screenshot: {path}" for path in item.screenshot_paths)
    return "\n".join(refs) if refs else "- See customer_report.json"
