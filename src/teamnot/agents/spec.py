"""Skill (.md) loader.

Each skill is a directory containing a ``SKILL.md`` file:

    ---
    name: architect
    role: Senior Software Architect
    description: Designs solutions; never writes implementation code.
    worker: claude_cli              # claude_cli | minimax | ollama | codex_cli
    tools: [Read, Write, Glob, Grep]
    talks_to: [implementer, reviewer]
    handoff_to: implementer
    inputs: [task, conventions, memory]
    outputs: [adr]
    timeout_s: 240
    ---
    # System prompt body (Markdown)

    You are the Architect agent for TeamNoT...

The body becomes the system prompt. Frontmatter validates into ``AgentSpec``.

Loading a directory of skills returns a ``SkillRegistry`` that the engine
hands to the pipeline so agents can be addressed by role name.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("teamnot.agents.spec")


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


class AgentSpec(BaseModel):
    """The parsed frontmatter of a SKILL.md file."""
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$",
                      description="Slug used to address this agent (e.g. 'architect')")
    role: str = Field(min_length=1, description="Human-readable role title")
    description: str = Field(min_length=1)
    worker: str = Field(
        default="claude_cli",
        description="Which worker executes this role: claude_cli | minimax | ollama | codex_cli | ...",
    )
    tools: list[str] = Field(default_factory=list)
    talks_to: list[str] = Field(default_factory=list, description="Other agent names this role can message")
    handoff_to: str | None = Field(default=None, description="Default next agent after this one finishes")
    inputs: list[str] = Field(default_factory=list, description="Context keys this agent expects")
    outputs: list[str] = Field(default_factory=list, description="Artifacts this agent produces")
    timeout_s: int = Field(default=300, ge=10, le=3600)
    metered_ok: bool = Field(
        default=False,
        description="If true, this role may use a metered worker (still gated by allow-list)",
    )

    # Filled by the loader, not the frontmatter
    body: str = ""
    source_path: str = ""

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        return v.strip().lower()

    @property
    def system_prompt(self) -> str:
        """The body of SKILL.md becomes the system prompt."""
        return self.body.strip()


@dataclass
class SkillRegistry:
    """In-memory map of agent name -> AgentSpec, loaded from disk."""
    specs: dict[str, AgentSpec] = field(default_factory=dict)
    source_dirs: list[Path] = field(default_factory=list)

    def get(self, name: str) -> AgentSpec:
        try:
            return self.specs[name]
        except KeyError as e:
            raise KeyError(
                f"No skill named '{name}'. Available: {sorted(self.specs)}"
            ) from e

    def names(self) -> list[str]:
        return sorted(self.specs)

    def add(self, spec: AgentSpec) -> None:
        if spec.name in self.specs:
            raise ValueError(f"Skill '{spec.name}' already registered (from {self.specs[spec.name].source_path})")
        self.specs[spec.name] = spec

    def __contains__(self, name: str) -> bool:
        return name in self.specs

    def __len__(self) -> int:
        return len(self.specs)


def parse_skill_md(text: str, source_path: Path | None = None) -> AgentSpec:
    """Parse a SKILL.md text into an AgentSpec.

    Raises ValueError if the frontmatter is missing or invalid.
    """
    m = _FRONTMATTER_RE.match(text.lstrip())
    if not m:
        raise ValueError(
            f"SKILL.md missing YAML frontmatter (--- block) — {source_path or '<inline>'}"
        )
    front_text, body = m.group(1), m.group(2)
    try:
        front: Any = yaml.safe_load(front_text)
    except yaml.YAMLError as e:
        raise ValueError(f"Bad YAML in {source_path}: {e}") from e
    if not isinstance(front, dict):
        raise ValueError(f"Frontmatter must be a mapping in {source_path}, got {type(front).__name__}")
    spec = AgentSpec.model_validate({**front, "body": body, "source_path": str(source_path) if source_path else ""})
    return spec


def load_skill(skill_dir: Path) -> AgentSpec:
    """Load a single skill from a directory containing SKILL.md."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"No SKILL.md in {skill_dir}")
    return parse_skill_md(skill_md.read_text(encoding="utf-8"), source_path=skill_md)


def load_skills_from_dir(root: Path) -> SkillRegistry:
    """Load every ``*/SKILL.md`` under a directory into a registry.

    Subdirectories without a SKILL.md are silently skipped — useful for
    keeping non-skill files (README, license) inside the ``skills/`` tree.
    """
    reg = SkillRegistry(source_dirs=[root])
    if not root.exists() or not root.is_dir():
        return reg
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        skill_md = sub / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            spec = load_skill(sub)
            reg.add(spec)
        except (ValueError, FileNotFoundError) as e:
            # Don't fail the whole load — log and skip the bad skill
            logger.warning("skip %s: %s", sub, e)
    return reg


def default_skills_dir() -> Path:
    """Return the bundled `skills/` directory shipped with the package.

    Resolution order:
      1. ``$TEAMNOT_SKILLS_DIR`` env var if set
      2. ``<repo>/skills`` if running from source
      3. ``<site-packages>/teamnot/_skills`` (installed location, future)
    """
    env = os.environ.get("TEAMNOT_SKILLS_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            return p

    here = Path(__file__).resolve()
    # When running from the source tree, skills live at <repo>/skills
    repo_skills = here.parents[3] / "skills"
    if repo_skills.exists():
        return repo_skills

    # Installed location (future)
    return here.parent / "_skills"
