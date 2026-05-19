"""Tests for the DoD-driven multi-agent Pipeline.

These tests use a stub invoker so no real workers are called.
"""
from __future__ import annotations

from pathlib import Path

from teamnot.agents.bus import AgentMessageBus
from teamnot.agents.spec import AgentSpec, SkillRegistry
from teamnot.brief import (
    Brief,
    DefinitionOfDone,
    DoDCheck,
    ProjectSpec,
    TaskSpec,
)
from teamnot.dod import DoDEvaluator
from teamnot.engine.pipeline import (
    Pipeline,
    PipelineConfig,
    PipelineOutcome,
)
from teamnot.safety import CostGuard
from teamnot.workers.claude_cli import ClaudeCliResult
from teamnot.workspace import Workspace


def _brief(tmp: Path, marker_file: str = "marker.txt") -> Brief:
    return Brief(
        project=ProjectSpec(name="t", path=tmp),
        task=TaskSpec(id="T1", title="t", description="create the marker file"),
        definition_of_done=DefinitionOfDone(
            checks=[DoDCheck(file_exists=marker_file, name="marker present")],
            llm_judge_required=False,
        ),
    )


def _registry(*names: str) -> SkillRegistry:
    reg = SkillRegistry()
    for n in names:
        reg.add(AgentSpec(name=n, role=n.title(), description=f"{n} agent", body=f"system: {n}"))
    return reg


def test_pipeline_halts_when_dod_passes_after_implementer(tmp_path: Path):
    """One pass: implementer creates the marker, DoD passes, documenter wraps up."""
    marker = tmp_path / "marker.txt"

    def invoker(spec, prompt):
        if spec.name == "implementer":
            marker.write_text("done", encoding="utf-8")
        return ClaudeCliResult(output=f"{spec.name} OK", returncode=0, stderr="", elapsed_s=0.01)

    brief = _brief(tmp_path)
    ws = Workspace(brief)
    ws.ensure()
    guard = CostGuard.from_brief(brief)
    bus = AgentMessageBus(log_path=ws.logs_dir / "msg.jsonl")
    p = Pipeline(
        brief=brief, workspace=ws, cost_guard=guard,
        registry=_registry("architect", "implementer", "tester", "reviewer", "documenter"),
        bus=bus, dod_evaluator=DoDEvaluator(brief), invoker=invoker,
        config=PipelineConfig(max_iterations=10, max_consecutive_failures=3),
    )
    result = p.run()
    assert result.outcome == PipelineOutcome.dod_passed, result.notes
    # architect, implementer, then documenter (DoD passed after implementer)
    agents = [t.agent for t in result.turns]
    assert "implementer" in agents
    assert agents[-1] == "documenter"


def test_pipeline_retries_implementer_when_machine_check_fails(tmp_path: Path):
    """Implementer fails twice (no marker) then succeeds on third try."""
    marker = tmp_path / "marker.txt"
    impl_calls = {"n": 0}

    def invoker(spec, prompt):
        if spec.name == "implementer":
            impl_calls["n"] += 1
            if impl_calls["n"] >= 3:
                marker.write_text("ok", encoding="utf-8")
        return ClaudeCliResult(output=f"{spec.name} OK", returncode=0, stderr="", elapsed_s=0.01)

    brief = _brief(tmp_path)
    ws = Workspace(brief)
    ws.ensure()
    guard = CostGuard.from_brief(brief)
    bus = AgentMessageBus(log_path=ws.logs_dir / "msg.jsonl")
    p = Pipeline(
        brief=brief, workspace=ws, cost_guard=guard,
        registry=_registry("architect", "implementer", "tester", "documenter"),
        bus=bus, dod_evaluator=DoDEvaluator(brief), invoker=invoker,
        config=PipelineConfig(max_iterations=15, max_consecutive_failures=10),
    )
    result = p.run()
    assert result.outcome == PipelineOutcome.dod_passed, result.notes
    assert impl_calls["n"] >= 3


def test_pipeline_blocks_after_max_retries(tmp_path: Path):
    """Implementer never creates the marker → blocked_retries after consecutive failures."""
    def invoker(spec, prompt):
        # Implementer "succeeds" but produces nothing useful
        return ClaudeCliResult(
            output="" if spec.name == "implementer" else f"{spec.name} OK",
            returncode=1 if spec.name == "implementer" else 0,
            stderr="exit 1",
            elapsed_s=0.01,
        )

    brief = _brief(tmp_path)
    ws = Workspace(brief)
    ws.ensure()
    guard = CostGuard.from_brief(brief)
    bus = AgentMessageBus()
    p = Pipeline(
        brief=brief, workspace=ws, cost_guard=guard,
        registry=_registry("architect", "implementer"),
        bus=bus, dod_evaluator=DoDEvaluator(brief), invoker=invoker,
        config=PipelineConfig(max_iterations=5, max_consecutive_failures=2),
    )
    result = p.run()
    assert result.outcome == PipelineOutcome.blocked_retries
    assert any("failures" in n or "no next agent" in n for n in result.notes)


def test_pipeline_blocks_with_no_skills(tmp_path: Path):
    def invoker(spec, prompt):  # never called
        raise AssertionError("invoker should not run")

    brief = _brief(tmp_path)
    ws = Workspace(brief)
    ws.ensure()
    guard = CostGuard.from_brief(brief)
    bus = AgentMessageBus()
    p = Pipeline(
        brief=brief, workspace=ws, cost_guard=guard,
        registry=SkillRegistry(),
        bus=bus, dod_evaluator=DoDEvaluator(brief), invoker=invoker,
    )
    result = p.run()
    assert result.outcome == PipelineOutcome.blocked_no_skill


def test_pipeline_emits_transcript(tmp_path: Path):
    marker = tmp_path / "marker.txt"

    def invoker(spec, prompt):
        if spec.name == "implementer":
            marker.write_text("ok", encoding="utf-8")
        return ClaudeCliResult(output="OK", returncode=0, stderr="", elapsed_s=0.0)

    brief = _brief(tmp_path)
    ws = Workspace(brief)
    ws.ensure()
    guard = CostGuard.from_brief(brief)
    bus = AgentMessageBus(log_path=ws.logs_dir / "msg.jsonl")
    p = Pipeline(
        brief=brief, workspace=ws, cost_guard=guard,
        registry=_registry("architect", "implementer", "documenter"),
        bus=bus, dod_evaluator=DoDEvaluator(brief), invoker=invoker,
    )
    p.run()
    # Pipeline → agent + agent → pipeline messages should both exist
    senders = {m.sender for m in bus.all_messages()}
    recipients = {m.recipient for m in bus.all_messages()}
    assert "pipeline" in senders
    assert "pipeline" in recipients
    assert "architect" in senders
    assert "implementer" in senders
