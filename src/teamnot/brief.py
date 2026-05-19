"""Project Brief Contract — the only input TeamNoT needs.

A Brief tells TeamNoT:
  - project: where the target project lives, what stack it uses
  - task: what to build, with constraints
  - definition_of_done: machine-verifiable checks + optional LLM judge
  - deliverable: how to hand the result back to the user
  - budget: time, money, retry caps

Briefs are YAML files under `.teamnot/brief.yaml` inside the target project.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class BriefValidationError(Exception):
    """Raised when a brief file fails schema or sanity validation."""


# ── Project ──────────────────────────────────────────────────────────────────

class ProjectSpec(BaseModel):
    """Where the target project lives and how it is built."""
    name: str = Field(min_length=1, description="Slug-ish project name")
    path: Path = Field(description="Absolute path to the project root")
    language: list[str] = Field(default_factory=list, description="Primary languages, e.g. [python, typescript]")
    stack: list[str] = Field(default_factory=list, description="Frameworks/libraries, e.g. [fastapi, nextjs, postgres]")
    conventions_file: str | None = Field(
        default=None,
        description="Optional path (relative to project) to a conventions doc. Defaults to .teamnot/conventions.md if present.",
    )
    memory_file: str | None = Field(
        default=None,
        description="Path (relative to project) where TeamNoT writes accumulated learnings. Defaults to .teamnot/memory.md.",
    )

    @field_validator("path", mode="before")
    @classmethod
    def _resolve_path(cls, v: Any) -> Path:
        return Path(str(v)).expanduser().resolve()


# ── Task ─────────────────────────────────────────────────────────────────────

class TaskConstraints(BaseModel):
    """Hard rules TeamNoT must not break. All default to safe."""
    no_deploy: bool = True
    no_main_commit: bool = True
    no_secrets_in_code: bool = True
    no_force_push: bool = True
    no_destructive_git: bool = True
    extra: list[str] = Field(default_factory=list, description="Free-form additional constraints")


class TaskSpec(BaseModel):
    """The actual work to be done."""
    id: str = Field(min_length=1, description="Task ID, e.g. TASK-2026-05-19-001")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1, description="Detailed requirement, natural language OK")
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    references: list[str] = Field(
        default_factory=list,
        description="Files/URLs/issues to read before starting",
    )


# ── Definition of Done ───────────────────────────────────────────────────────

DoDCheckType = Literal["run", "file_exists", "file_contains", "http_check", "custom_script", "llm_judge"]


class DoDCheck(BaseModel):
    """A single Definition-of-Done check.

    Exactly one of {run, file_exists, file_contains, http_check, custom_script, llm_judge}
    must be set per check.
    """
    name: str = Field(default="", description="Human-readable name, e.g. 'lint passes'")
    kind: DoDCheckType | None = Field(default=None, description="Auto-detected from set fields if omitted")

    # run: execute shell command, expect specific exit / stdout pattern
    run: str | None = None
    expect_exit: int | None = Field(default=0, description="Expected exit code for `run`")
    expect_stdout_contains: str | None = None
    expect_stdout_regex: str | None = None

    # file_exists: assert a path exists (relative to project root)
    file_exists: str | None = None

    # file_contains: assert a file contains a substring
    file_contains: dict[str, str] | None = Field(
        default=None,
        description="Map {path: substring}",
    )

    # http_check: hit an endpoint, expect status
    http_check: dict[str, Any] | None = Field(
        default=None,
        description="Map {url, status, method?, timeout_s?, body_contains?}",
    )

    # custom_script: a shell script in the project that must exit 0
    custom_script: str | None = None

    # llm_judge: hand the diff + DoD prompt to an LLM, expect APPROVE
    llm_judge: str | None = Field(
        default=None,
        description="Prompt for the LLM judge. The judge sees the diff + this prompt and must output APPROVE/REJECT.",
    )

    # Common
    cwd: str | None = Field(default=None, description="Working dir for `run`/`custom_script`, relative to project root")
    timeout_s: int = Field(default=120, ge=1, le=3600)
    required: bool = Field(default=True, description="If false, failure is a warning, not a blocker")

    @model_validator(mode="after")
    def _exactly_one_target(self) -> DoDCheck:
        targets = {
            "run": self.run,
            "file_exists": self.file_exists,
            "file_contains": self.file_contains,
            "http_check": self.http_check,
            "custom_script": self.custom_script,
            "llm_judge": self.llm_judge,
        }
        set_keys = [k for k, v in targets.items() if v is not None]
        if len(set_keys) != 1:
            raise ValueError(
                f"DoDCheck must set exactly one of {list(targets)}; got {set_keys or 'none'}"
            )
        if self.kind is None:
            self.kind = set_keys[0]  # type: ignore[assignment]
        if not self.name:
            self.name = f"{self.kind}: {set_keys[0]}"
        return self


class DefinitionOfDone(BaseModel):
    """List of DoD checks. The pipeline keeps looping until all required ones pass."""
    checks: list[DoDCheck] = Field(min_length=1)
    require_all_pass: bool = Field(
        default=True,
        description="If true (default), every required check must pass. If false, at least one must pass.",
    )
    llm_judge_required: bool = Field(
        default=False,
        description="If true, even when all machine checks pass, an LLM judge must also approve.",
    )

    def machine_checks(self) -> list[DoDCheck]:
        return [c for c in self.checks if c.kind != "llm_judge"]

    def judge_checks(self) -> list[DoDCheck]:
        return [c for c in self.checks if c.kind == "llm_judge"]


# ── Deliverable ──────────────────────────────────────────────────────────────

class DeliverableType(str, Enum):
    feature_branch = "feature_branch"
    pull_request = "pull_request"
    files = "files"
    tarball = "tarball"
    report_only = "report_only"


class ReportTarget(str, Enum):
    stdout = "stdout"
    file = "file"
    telegram = "telegram"
    webhook = "webhook"


class Deliverable(BaseModel):
    type: DeliverableType = DeliverableType.feature_branch
    branch: str | None = Field(default=None, description="Branch name, defaults to feature/<task.id>")
    base: str = Field(default="main")
    push_remote: bool = Field(default=False, description="If true, push the branch to origin after success")
    report_to: ReportTarget = Field(default=ReportTarget.stdout)
    report_path: str | None = Field(default=None, description="If report_to=file, where to write")
    webhook_url: str | None = None
    telegram_chat_id: str | None = None


# ── Budget ───────────────────────────────────────────────────────────────────

class Budget(BaseModel):
    """Time/money/retry caps. The cost guard enforces max_usd as a HARD stop for
    metered (pay-per-call API) workers — once usage hits the cap, no new metered
    calls are started. Subscription workers (e.g. Claude Code CLI via OAuth) do
    not bill per call and are gated by ``max_minutes`` instead.
    """
    max_minutes: int = Field(default=120, ge=1, le=24 * 60)
    max_usd: float = Field(
        default=5.0,
        ge=0,
        description="HARD cap on combined API spend across all metered workers.",
    )
    max_retries: int = Field(default=5, ge=1, le=20)
    max_dod_attempts: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum times the DoD loop will re-attempt the task before declaring BLOCKED",
    )

    # ── Cost-guard fine controls ────────────────────────────────────────────
    cost_guard_enabled: bool = Field(
        default=True,
        description="Master switch for the cost guard. Disable only for offline/subscription-only runs.",
    )
    cost_warn_pct: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Log a warning when metered usage crosses this fraction of max_usd.",
    )
    cost_pause_pct: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description=(
            "When metered usage crosses this fraction of max_usd, refuse to start "
            "new metered calls (pause). Subscription workers can still run."
        ),
    )
    cost_hard_stop_pct: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "When metered usage crosses this fraction of max_usd, halt the whole "
            "worker — no further calls of any kind. Brief is marked BLOCKED_BUDGET."
        ),
    )
    require_explicit_api_optin: bool = Field(
        default=True,
        description=(
            "If true, the worker refuses to use metered API workers unless the user "
            "explicitly opts in via the `allowed_metered_workers` allow-list."
        ),
    )
    allowed_metered_workers: list[str] = Field(
        default_factory=list,
        description=(
            "Allow-list of metered worker names that may be used (e.g. ['minimax', 'openai']). "
            "Empty list means NO metered workers are allowed; subscription/local workers only."
        ),
    )
    llm_judge_estimated_usd: float = Field(
        default=0.01,
        ge=0.0,
        le=10.0,
        description="Per-call USD estimate used by the cost guard when an llm_judge check fires.",
    )

    @model_validator(mode="after")
    def _check_thresholds(self) -> Budget:
        if not (self.cost_warn_pct <= self.cost_pause_pct <= self.cost_hard_stop_pct):
            raise ValueError(
                "cost thresholds must satisfy: warn_pct <= pause_pct <= hard_stop_pct "
                f"(got {self.cost_warn_pct}, {self.cost_pause_pct}, {self.cost_hard_stop_pct})"
            )
        return self


# ── Brief root ───────────────────────────────────────────────────────────────

class Brief(BaseModel):
    """The complete contract handed to a TeamNoT worker."""
    schema_version: str = Field(default="2.0", description="Brief schema version")
    project: ProjectSpec
    task: TaskSpec
    definition_of_done: DefinitionOfDone
    deliverable: Deliverable = Field(default_factory=Deliverable)
    budget: Budget = Field(default_factory=Budget)

    # Free-form, agent-readable extras
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Provenance (filled by load_brief)
    _source_path: Path | None = None

    @model_validator(mode="after")
    def _set_defaults(self) -> Brief:
        # Default branch name
        if self.deliverable.branch is None:
            safe_id = self.task.id.lower().replace(" ", "-").replace("_", "-")
            self.deliverable.branch = f"feature/{safe_id}"

        # Default memory file
        if self.project.memory_file is None:
            self.project.memory_file = ".teamnot/memory.md"

        # Default conventions file
        if self.project.conventions_file is None:
            self.project.conventions_file = ".teamnot/conventions.md"

        return self

    def absolute(self, relative_path: str) -> Path:
        """Resolve a project-relative path to an absolute one."""
        return (self.project.path / relative_path).resolve()

    @property
    def memory_path(self) -> Path:
        return self.absolute(self.project.memory_file or ".teamnot/memory.md")

    @property
    def conventions_path(self) -> Path:
        return self.absolute(self.project.conventions_file or ".teamnot/conventions.md")

    @property
    def reports_dir(self) -> Path:
        return self.absolute(".teamnot/reports")

    @property
    def plans_dir(self) -> Path:
        return self.absolute(".teamnot/plans")

    @property
    def logs_dir(self) -> Path:
        return self.absolute(".teamnot/logs")


# ── Loader / saver ───────────────────────────────────────────────────────────

def load_brief(path: str | Path) -> Brief:
    """Load and validate a brief from a YAML file.

    Raises BriefValidationError on schema mismatch or sanity failure.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise BriefValidationError(f"Brief file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise BriefValidationError(f"YAML parse error in {p}: {e}") from e
    if not isinstance(raw, dict):
        raise BriefValidationError(f"Brief root must be a mapping, got {type(raw).__name__}")

    try:
        brief = Brief.model_validate(raw)
    except ValidationError as e:
        raise BriefValidationError(f"Brief schema validation failed:\n{e}") from e

    # Sanity: project path must exist
    if not brief.project.path.exists():
        raise BriefValidationError(
            f"project.path does not exist: {brief.project.path}\n"
            f"Create the directory or fix the path in {p}"
        )

    brief._source_path = p
    return brief


def save_brief(brief: Brief, path: str | Path) -> Path:
    """Serialize a brief back to YAML. Used by `teamnot init`."""
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = brief.model_dump(mode="json", exclude={"_source_path"})
    # Stable key order for diffability
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def example_brief(project_path: Path | None = None) -> Brief:
    """Build a minimal example brief, used by `teamnot init` and tests."""
    project_path = project_path or Path.cwd()
    task_id = f"TASK-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    return Brief(
        project=ProjectSpec(
            name=project_path.name,
            path=project_path,
            language=["python"],
            stack=["fastapi"],
        ),
        task=TaskSpec(
            id=task_id,
            title="Example: add /health endpoint",
            description="Add a GET /health endpoint that returns {'status': 'ok'} to the FastAPI app.",
        ),
        definition_of_done=DefinitionOfDone(
            checks=[
                DoDCheck(name="lint passes", run="ruff check ."),
                DoDCheck(name="tests pass", run="pytest -q"),
                DoDCheck(
                    name="health endpoint responds",
                    http_check={"url": "http://localhost:8000/health", "status": 200, "timeout_s": 5},
                    required=False,
                ),
                DoDCheck(
                    name="reviewer approval",
                    llm_judge=(
                        "Check that the implementation matches the task description, "
                        "has no obvious security issues, and follows the project conventions. "
                        "Output APPROVE or REJECT with a one-line reason."
                    ),
                ),
            ],
            llm_judge_required=True,
        ),
    )
