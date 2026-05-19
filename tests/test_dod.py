"""Tests for the DoD evaluator."""
from __future__ import annotations

from pathlib import Path

from teamnot.brief import (
    Brief,
    DefinitionOfDone,
    DoDCheck,
    ProjectSpec,
    TaskSpec,
)
from teamnot.dod import DoDEvaluator


def _brief(tmp: Path, checks: list[DoDCheck], judge_required: bool = False) -> Brief:
    return Brief(
        project=ProjectSpec(name="t", path=tmp),
        task=TaskSpec(id="T1", title="t", description="d"),
        definition_of_done=DefinitionOfDone(checks=checks, llm_judge_required=judge_required),
    )


def test_run_check_passes_on_exit_0(tmp_path: Path):
    brief = _brief(tmp_path, [DoDCheck(run="echo hello", name="echo")])
    result = DoDEvaluator(brief).evaluate()
    assert result.all_passed, result.summary()
    assert result.results[0].passed


def test_run_check_fails_on_nonzero_exit(tmp_path: Path):
    brief = _brief(tmp_path, [DoDCheck(run="exit 3", name="fail")])
    result = DoDEvaluator(brief).evaluate()
    assert not result.all_passed
    assert "exit=3" in result.results[0].error


def test_file_exists_check(tmp_path: Path):
    (tmp_path / "marker.txt").write_text("ok", encoding="utf-8")
    brief = _brief(tmp_path, [
        DoDCheck(file_exists="marker.txt", name="marker"),
        DoDCheck(file_exists="missing.txt", name="missing"),
    ])
    result = DoDEvaluator(brief).evaluate()
    assert not result.all_passed
    assert result.results[0].passed
    assert not result.results[1].passed
    assert "missing" in result.results[1].error


def test_file_contains_check(tmp_path: Path):
    (tmp_path / "src.py").write_text("def health():\n    return 'ok'\n", encoding="utf-8")
    brief = _brief(tmp_path, [
        DoDCheck(file_contains={"src.py": "def health"}, name="has health"),
        DoDCheck(file_contains={"src.py": "MISSING"}, name="missing substring"),
    ])
    result = DoDEvaluator(brief).evaluate()
    assert result.results[0].passed
    assert not result.results[1].passed


def test_optional_check_does_not_block(tmp_path: Path):
    brief = _brief(tmp_path, [
        DoDCheck(run="echo ok", name="ok"),
        DoDCheck(run="exit 1", name="optional fail", required=False),
    ])
    result = DoDEvaluator(brief).evaluate()
    assert result.all_passed, "required passed → DoD passes despite optional failure"
    assert result.machine_passed
    assert len(result.failed_optional) == 1


def test_judge_skipped_when_machine_fails(tmp_path: Path):
    """Critical for cost safety: don't burn API spend judging broken code."""
    calls: list[str] = []

    def stub_judge(prompt: str, diff: str) -> tuple[bool, str]:
        calls.append(prompt)
        return True, "approved"

    brief = _brief(tmp_path, [
        DoDCheck(run="exit 1", name="lint fail"),
        DoDCheck(llm_judge="please approve", name="judge"),
    ], judge_required=True)
    result = DoDEvaluator(brief, llm_judge=stub_judge).evaluate()
    assert not result.all_passed
    assert not calls, "judge must NOT be called when machine checks fail"
    judge_result = next(r for r in result.results if r.check.kind == "llm_judge")
    assert judge_result.skipped
    assert "save API spend" in judge_result.skip_reason


def test_judge_runs_after_machine_passes(tmp_path: Path):
    def stub_judge(prompt: str, diff: str) -> tuple[bool, str]:
        return True, "looks good"

    brief = _brief(tmp_path, [
        DoDCheck(run="echo ok", name="lint"),
        DoDCheck(llm_judge="please approve", name="judge"),
    ], judge_required=True)
    result = DoDEvaluator(brief, llm_judge=stub_judge).evaluate()
    assert result.all_passed
    assert result.judge_passed


def test_judge_reject_blocks_when_required(tmp_path: Path):
    def stub_judge(prompt: str, diff: str) -> tuple[bool, str]:
        return False, "nope"

    brief = _brief(tmp_path, [
        DoDCheck(run="echo ok", name="lint"),
        DoDCheck(llm_judge="please approve", name="judge"),
    ], judge_required=True)
    result = DoDEvaluator(brief, llm_judge=stub_judge).evaluate()
    assert not result.all_passed
    assert not result.judge_passed
