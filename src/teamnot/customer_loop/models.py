"""Schemas for TeamNoT customer-loop artifacts."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, model_validator


class CustomerLoopError(Exception):
    """Base exception for customer-loop failures."""


class CustomerLoopValidationError(CustomerLoopError):
    """Raised when customer-loop inputs cannot be loaded or validated."""


class CustomerLoopRunnerError(CustomerLoopError):
    """Raised when an experience runner cannot collect evidence."""


class CustomerSeverity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    positive = "positive"


class CustomerLoopRunnerName(str, Enum):
    manual = "manual"
    openclaw_windows_cdp = "openclaw-windows-cdp"
    openclaw_windows_interactive = "openclaw-windows-interactive"
    openclaw_windows_flow = "openclaw-windows-flow"


class CustomerProfile(BaseModel):
    persona: str = Field(min_length=1)
    role: str = Field(min_length=1)
    seniority: str = ""
    domain_literacy: str = ""
    current_workflow: str = ""
    buying_trigger: str = ""
    alternatives: list[str] = Field(default_factory=list)
    buyer_user_split: str = ""
    trust_threshold: str = ""

    @model_validator(mode="before")
    @classmethod
    def default_persona_from_role(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("persona") and data.get("role"):
            return {**data, "persona": data["role"]}
        return data


class CustomerJob(BaseModel):
    functional: str = Field(min_length=1)
    emotional: str = ""
    social: str = ""
    importance: int = Field(default=5, ge=1, le=10)


class ExperienceTarget(BaseModel):
    url: HttpUrl | str
    device: str = "desktop"
    browser: str = "chrome"
    context: str = ""


class CustomerTestTask(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    instructions: str = ""
    success_criteria: list[str] = Field(default_factory=list)


class CustomerTestPlan(BaseModel):
    target: ExperienceTarget
    customer_job: CustomerJob
    tasks: list[CustomerTestTask] = Field(default_factory=list)
    notes: str = ""


class CustomerFlowStep(BaseModel):
    id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    selector: str = ""
    text: str = ""
    value: str = ""
    file: Path | None = None
    timeout_ms: int = Field(default=10000, ge=100, le=60000)
    description: str = ""


class CustomerFlow(BaseModel):
    name: str = Field(min_length=1)
    steps: list[CustomerFlowStep] = Field(default_factory=list)


class CustomerEvidence(BaseModel):
    kind: str = "manual_report"
    path: str = ""
    screenshot_paths: list[str] = Field(default_factory=list)
    observed_behavior: str = ""
    raw_excerpt: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CustomerFinding(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    severity: CustomerSeverity = CustomerSeverity.medium
    evidence: list[CustomerEvidence] = Field(default_factory=list)
    customer_interpretation: str = ""
    business_impact: str = ""
    likely_frequency: str = ""
    recommendation: str = ""
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    trust_blocker: bool = False
    core_task_blocker: bool = False


class CustomerScores(BaseModel):
    job_importance: int = Field(default=5, ge=1, le=10)
    value: int = Field(default=5, ge=1, le=10)
    time_to_value: int = Field(default=5, ge=1, le=10)
    task_success: int = Field(default=5, ge=1, le=10)
    usability: int = Field(default=5, ge=1, le=10)
    trust_readiness: int = Field(default=5, ge=1, le=10)
    output_actionability: int = Field(default=5, ge=1, le=10)
    domain_fit: int = Field(default=5, ge=1, le=10)
    buying_readiness: int = Field(default=5, ge=1, le=10)
    retention_likelihood: int = Field(default=5, ge=1, le=10)
    emotional_confidence: int = Field(default=5, ge=1, le=10)
    technical_reliability: int = Field(default=5, ge=1, le=10)


class CustomerReport(BaseModel):
    profile: CustomerProfile
    target: ExperienceTarget
    plan: CustomerTestPlan
    findings: list[CustomerFinding] = Field(default_factory=list)
    scores: CustomerScores = Field(default_factory=CustomerScores)
    evidence: list[CustomerEvidence] = Field(default_factory=list)
    summary: str = ""
    raw_report_path: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GeneratedBrief(BaseModel):
    path: str = ""
    task_id: str
    title: str
    selected_finding_id: str | None = None
    yaml: dict[str, Any]


class CustomerLoopConfig(BaseModel):
    target: ExperienceTarget
    profile: CustomerProfile
    out_dir: Path
    max_iterations: int = Field(default=1, ge=1, le=20)
    severity_threshold: CustomerSeverity = CustomerSeverity.high
    run_teamnot: bool = False
    runner: CustomerLoopRunnerName = CustomerLoopRunnerName.manual
    evidence_path: Path | None = None
    previous_brief_path: Path | None = None
    flow_path: Path | None = None


class CustomerLoopResult(BaseModel):
    out_dir: Path
    report: CustomerReport
    selected_finding: CustomerFinding | None = None
    generated_brief: GeneratedBrief | None = None
    stopped_reason: str = ""
    iterations_completed: int = 1
    teamnot_invoked: bool = False
    iteration_out_dirs: list[Path] = Field(default_factory=list)
