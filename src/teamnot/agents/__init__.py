"""Agents — role definitions loaded from Markdown skill files.

A skill is a directory under ``skills/`` containing a ``SKILL.md`` file with
YAML frontmatter that specifies the role's behavior:

    skills/
        architect/
            SKILL.md          # role spec + system prompt body
            checklist.md      # supporting reference (optional)
        implementer/
            SKILL.md
        ...

The frontmatter schema is enforced by ``AgentSpec``. The body of SKILL.md
becomes the system prompt for the role.
"""
from teamnot.agents.bus import (
    AgentMessage,
    AgentMessageBus,
    MessageIntent,
)
from teamnot.agents.spec import AgentSpec, SkillRegistry, load_skill, load_skills_from_dir

__all__ = [
    "AgentMessage",
    "AgentMessageBus",
    "AgentSpec",
    "MessageIntent",
    "SkillRegistry",
    "load_skill",
    "load_skills_from_dir",
]
