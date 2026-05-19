"""Tests for the SKILL.md loader and AgentSpec."""
from __future__ import annotations

from pathlib import Path

import pytest

from teamnot.agents.spec import (
    AgentSpec,
    SkillRegistry,
    default_skills_dir,
    load_skill,
    load_skills_from_dir,
    parse_skill_md,
)


def _write_skill(dir_path: Path, frontmatter: str, body: str = "Be helpful.") -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    md = dir_path / "SKILL.md"
    md.write_text(f"---\n{frontmatter.strip()}\n---\n{body}\n", encoding="utf-8")
    return md


def test_parse_skill_md_minimal():
    txt = (
        "---\n"
        "name: tester\n"
        "role: QA Engineer\n"
        "description: Writes tests.\n"
        "---\n"
        "system prompt body\n"
    )
    spec = parse_skill_md(txt)
    assert spec.name == "tester"
    assert spec.role == "QA Engineer"
    assert spec.body.strip() == "system prompt body"
    assert spec.system_prompt == "system prompt body"


def test_parse_skill_md_rejects_no_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill_md("# just a markdown file")


def test_parse_skill_md_rejects_invalid_name():
    txt = (
        "---\n"
        "name: With Spaces\n"
        "role: r\n"
        "description: d\n"
        "---\nbody\n"
    )
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        parse_skill_md(txt)


def test_load_skill_from_directory(tmp_path: Path):
    _write_skill(
        tmp_path / "architect",
        "name: architect\nrole: Architect\ndescription: d",
        body="Design things.",
    )
    spec = load_skill(tmp_path / "architect")
    assert spec.name == "architect"
    assert spec.system_prompt == "Design things."


def test_load_skills_from_dir_skips_non_skills(tmp_path: Path):
    _write_skill(tmp_path / "tester", "name: tester\nrole: t\ndescription: d")
    (tmp_path / "README.md").write_text("readme", encoding="utf-8")
    (tmp_path / "noskill").mkdir()
    (tmp_path / "noskill" / "notes.md").write_text("notes", encoding="utf-8")
    reg = load_skills_from_dir(tmp_path)
    assert reg.names() == ["tester"]


def test_load_skills_from_dir_skips_invalid(tmp_path: Path):
    _write_skill(tmp_path / "good", "name: good\nrole: g\ndescription: d")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("not yaml at all", encoding="utf-8")
    reg = load_skills_from_dir(tmp_path)
    assert "good" in reg.names()
    assert "bad" not in reg.names()


def test_default_skills_dir_finds_bundled_skills():
    """The 7 skills shipped in `skills/` must load cleanly."""
    sd = default_skills_dir()
    if not sd.exists():
        pytest.skip(f"skills dir not at {sd}")
    reg = load_skills_from_dir(sd)
    names = set(reg.names())
    for required in {"architect", "implementer", "tester", "reviewer", "documenter"}:
        assert required in names, f"bundled skill missing: {required}"


def test_registry_get_raises_helpful_error():
    reg = SkillRegistry()
    reg.add(AgentSpec(name="x", role="r", description="d", body="b"))
    with pytest.raises(KeyError, match="Available:"):
        reg.get("y")


def test_registry_rejects_duplicate():
    reg = SkillRegistry()
    spec = AgentSpec(name="x", role="r", description="d", body="b")
    reg.add(spec)
    with pytest.raises(ValueError, match="already registered"):
        reg.add(spec)
