"""Schemas for TeamNoT customer-loop artifacts."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


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
    openclaw_windows_session = "openclaw-windows-session"
    openclaw_windows_researcher = "openclaw-windows-researcher"


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
    url: str = ""
    file: Path | None = None
    timeout_ms: int = Field(default=10000, ge=100, le=60000)
    description: str = ""


class CustomerFlow(BaseModel):
    name: str = Field(min_length=1)
    start_url: str = ""
    steps: list[CustomerFlowStep] = Field(default_factory=list, min_length=1)


class CustomerFlowPack(BaseModel):
    name: str = "Customer flow pack"
    reset_between_flows: bool = True
    flows: list[CustomerFlow] = Field(default_factory=list, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def accept_single_flow_shape(cls, data: Any) -> Any:
        if isinstance(data, dict) and "steps" in data and "flows" not in data:
            return {
                "name": data.get("name", "Customer flow pack"),
                "flows": [data],
            }
        return data


class ProductRoute(BaseModel):
    route: str = Field(min_length=1)
    url: str = ""
    label: str = ""
    kind: str = "general"
    priority: int = Field(default=1, ge=1, le=10)
    reasons: list[str] = Field(default_factory=list)
    coverage_status: str = "planned"
    requires_auth: bool = False


class ProductJourney(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    persona: str = ""
    goal: str = ""
    routes: list[str] = Field(default_factory=list)
    priority: int = Field(default=1, ge=1, le=10)
    coverage_status: str = "planned"
    gaps: list[str] = Field(default_factory=list)


class ProductExplorationPlan(BaseModel):
    target: ExperienceTarget
    profile: CustomerProfile
    routes: list[ProductRoute] = Field(default_factory=list)
    journeys: list[ProductJourney] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    notes: str = ""


class SeededTestAccount(BaseModel):
    email: str = ""
    password: str = Field(default="", repr=False)
    login_url: str = ""
    workspace_id: str = ""
    notes: str = ""

    def redacted(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        if data.get("password"):
            data["password"] = "***REDACTED***"
        return data


class SeededCookie(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    value: str = Field(default="", repr=False)
    domain: str = ""
    path: str = "/"
    expires: float | None = None
    http_only: bool | None = Field(default=None, alias="httpOnly")
    secure: bool | None = None
    same_site: str | None = Field(default=None, alias="sameSite")

    def redacted(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        if data.get("value"):
            data["value"] = "***REDACTED***"
        return data


class SeededLocalStorageEntry(BaseModel):
    origin: str = ""
    values: dict[str, str] = Field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "values": {key: "***REDACTED***" for key in self.values},
        }


class SeededCustomerState(BaseModel):
    storage_state_path: Path | None = None
    cookies: list[SeededCookie] = Field(default_factory=list)
    local_storage: list[SeededLocalStorageEntry] = Field(default_factory=list)
    test_account: SeededTestAccount | None = None
    login_url: str = ""
    cleanup_notes: str = ""
    reset_notes: str = ""
    workspace_id: str = ""
    safety_constraints: list[str] = Field(default_factory=list)
    adapter_status: str = "not_attempted"
    unsupported_blocker: str = ""

    def redacted(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"cookies", "local_storage", "test_account"})
        data["cookies"] = [cookie.redacted() for cookie in self.cookies]
        data["local_storage"] = [entry.redacted() for entry in self.local_storage]
        data["test_account"] = self.test_account.redacted() if self.test_account else None
        return data


class BrowserRuntimeMetadata(BaseModel):
    cdp_url: str = ""
    cdp_port: int | None = None
    session_id: str = ""
    profile_dir: str = ""
    page_url: str = ""
    target_id: str = ""
    page_count: int | None = None
    pinned_target: str = ""
    screenshot_method: str = ""
    failed_primitive: str = ""
    adapter_blocker: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class ScreenshotCaptureRecord(BaseModel):
    path: str = ""
    route: str = ""
    action: str = ""
    method: str = ""
    retry_count: int = 0
    failed_primitive: str = ""
    fallback_reason: str = ""
    success: bool = False
    width: int | None = None
    height: int | None = None
    sha256: str = ""


class VisionScreenshotGroup(BaseModel):
    group_id: str
    screenshots: list[ScreenshotCaptureRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class VisionReviewArtifact(BaseModel):
    review_kind: str = "metadata_only"
    evidence_source: str = "screenshot metadata and hashes"
    screenshot_count: int = 0
    groups: list[VisionScreenshotGroup] = Field(default_factory=list)
    heuristics: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    judgment_summary: str = "Deterministic baseline collected visual metadata only; no model visual judgment was performed."


class JTBDForces(BaseModel):
    push: str = ""
    pull: str = ""
    anxiety: str = ""
    habit: str = ""
    trigger: str = ""
    success_metric: str = ""


class PersonaLensResult(BaseModel):
    lens: str
    role: str = ""
    positive_signals: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class DomainOutputOracle(BaseModel):
    name: str = "Domain output oracle"
    expected_output: str = ""
    golden_file: Path | None = None
    api_check: str = ""
    semantic_rubric: str = ""
    manual_checkpoint: str = ""
    coverage_status: str = "not_evaluated"
    notes: str = ""


class ResearchActionMemory(BaseModel):
    route: str = ""
    observation: str = ""
    chosen_action: str = ""
    reason: str = ""
    expected_signal: str = ""
    result: str = ""
    comparison: str = ""
    learned_signal: str = ""
    repeated: bool = False
    no_op: bool = False


class IterationCoverage(BaseModel):
    iteration: int = 1
    stop_reason: str = ""
    selected_finding_id: str | None = None
    new_routes: list[str] = Field(default_factory=list)
    new_actions: list[str] = Field(default_factory=list)
    new_screenshots: list[str] = Field(default_factory=list)
    new_findings: list[str] = Field(default_factory=list)
    replayed: bool = False
    new_evidence: bool = False
    teamnot_invoked: bool = False


class CustomerEvidence(BaseModel):
    kind: str = "manual_report"
    path: str = ""
    screenshot_paths: list[str] = Field(default_factory=list)
    observed_behavior: str = ""
    raw_excerpt: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    screenshot_captures: list[ScreenshotCaptureRecord] = Field(default_factory=list)


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
    seeded_state: SeededCustomerState | None = None
    browser_runtime: BrowserRuntimeMetadata | None = None
    screenshot_captures: list[ScreenshotCaptureRecord] = Field(default_factory=list)
    vision_review: VisionReviewArtifact | None = None
    persona_lenses: list[PersonaLensResult] = Field(default_factory=list)
    jtbd_forces: JTBDForces | None = None
    domain_oracles: list[DomainOutputOracle] = Field(default_factory=list)
    action_memory: list[ResearchActionMemory] = Field(default_factory=list)


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
    file_fixture_path: Path | None = None
    seeded_state_path: Path | None = None
    seeded_state: SeededCustomerState | None = None
    domain_oracle_path: Path | None = None
    domain_oracles: list[DomainOutputOracle] = Field(default_factory=list)


class CustomerLoopResult(BaseModel):
    out_dir: Path
    report: CustomerReport
    selected_finding: CustomerFinding | None = None
    generated_brief: GeneratedBrief | None = None
    stopped_reason: str = ""
    iterations_completed: int = 1
    teamnot_invoked: bool = False
    iteration_out_dirs: list[Path] = Field(default_factory=list)
    iteration_coverage: list[IterationCoverage] = Field(default_factory=list)
