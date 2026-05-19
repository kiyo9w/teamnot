"""Tests for the cost guard / safety gate."""
from __future__ import annotations

from pathlib import Path

import pytest

from teamnot.brief import (
    Brief,
    Budget,
    DefinitionOfDone,
    DoDCheck,
    ProjectSpec,
    TaskSpec,
)
from teamnot.safety import (
    BillingModel,
    BudgetExceededError,
    CostGuard,
    WorkerNotAllowedError,
    WorkerPausedError,
    WorkerTag,
    register_worker,
)


def _brief(tmp: Path, **budget_kwargs) -> Brief:
    return Brief(
        project=ProjectSpec(name="t", path=tmp),
        task=TaskSpec(id="T1", title="t", description="d"),
        definition_of_done=DefinitionOfDone(checks=[DoDCheck(run="true")]),
        budget=Budget(**budget_kwargs),
    )


def test_metered_worker_blocked_when_not_in_allow_list(tmp_path: Path):
    brief = _brief(tmp_path, max_usd=10, allowed_metered_workers=[])
    guard = CostGuard.from_brief(brief)
    with pytest.raises(WorkerNotAllowedError, match="not in allowed_metered_workers"):
        with guard.gate("minimax", estimated_usd=0.05):
            pass


def test_subscription_worker_runs_without_allow_list(tmp_path: Path):
    brief = _brief(tmp_path, max_usd=0, allowed_metered_workers=[])
    guard = CostGuard.from_brief(brief)
    with guard.gate("claude_cli", estimated_usd=0.0, note="design"):
        pass
    assert guard.spent_usd == 0
    assert len(guard.ledger.records) == 1


def test_local_worker_runs_without_allow_list(tmp_path: Path):
    brief = _brief(tmp_path, max_usd=0, allowed_metered_workers=[])
    guard = CostGuard.from_brief(brief)
    with guard.gate("ollama"):
        pass
    assert len(guard.ledger.records) == 1


def test_metered_runs_when_in_allow_list(tmp_path: Path):
    brief = _brief(tmp_path, max_usd=1.0, allowed_metered_workers=["minimax"])
    guard = CostGuard.from_brief(brief)
    with guard.gate("minimax", estimated_usd=0.10) as call:
        call.record_actual(usd=0.08)
    assert guard.spent_usd == pytest.approx(0.08)


def test_pause_threshold_refuses_new_metered(tmp_path: Path):
    brief = _brief(
        tmp_path,
        max_usd=1.0,
        allowed_metered_workers=["minimax"],
        cost_warn_pct=0.5,
        cost_pause_pct=0.7,
        cost_hard_stop_pct=0.9,
    )
    guard = CostGuard.from_brief(brief)
    with guard.gate("minimax", estimated_usd=0.6) as call:
        call.record_actual(usd=0.6)
    # 0.6 spent, 1.0 cap, pause at 0.7 → 0.6 + 0.2 = 0.8 > 0.7 → refuse
    with pytest.raises(WorkerPausedError, match="pause threshold"):
        with guard.gate("minimax", estimated_usd=0.2):
            pass


def test_hard_stop_halts_guard(tmp_path: Path):
    brief = _brief(
        tmp_path,
        max_usd=1.0,
        allowed_metered_workers=["minimax"],
        cost_warn_pct=0.5,
        cost_pause_pct=0.7,
        cost_hard_stop_pct=0.95,
    )
    guard = CostGuard.from_brief(brief)
    with guard.gate("minimax", estimated_usd=0.5) as call:
        call.record_actual(usd=0.5)
    # Trying to spend $0.6 more → 1.1 > 0.95 → hard stop
    with pytest.raises(BudgetExceededError):
        with guard.gate("minimax", estimated_usd=0.6):
            pass
    assert guard.is_halted
    # After halt even small calls fail
    with pytest.raises(BudgetExceededError, match="halted"):
        with guard.gate("minimax", estimated_usd=0.01):
            pass


def test_subscription_worker_still_runs_after_metered_paused(tmp_path: Path):
    """When metered spend is paused, subscription work must keep going."""
    brief = _brief(
        tmp_path,
        max_usd=1.0,
        allowed_metered_workers=["minimax"],
        cost_warn_pct=0.3,
        cost_pause_pct=0.5,
        cost_hard_stop_pct=1.0,
    )
    guard = CostGuard.from_brief(brief)
    with guard.gate("minimax", estimated_usd=0.4) as call:
        call.record_actual(usd=0.4)
    with pytest.raises(WorkerPausedError):
        with guard.gate("minimax", estimated_usd=0.2):
            pass
    # claude_cli is subscription — still runs
    with guard.gate("claude_cli"):
        pass


def test_unknown_worker_defaults_to_metered(tmp_path: Path):
    brief = _brief(tmp_path, allowed_metered_workers=[])
    guard = CostGuard.from_brief(brief)
    with pytest.raises(WorkerNotAllowedError):
        with guard.gate("brand_new_provider"):
            pass


def test_guard_persists_ledger(tmp_path: Path):
    brief = _brief(tmp_path, allowed_metered_workers=["minimax"])
    guard = CostGuard.from_brief(brief)
    with guard.gate("minimax", estimated_usd=0.01) as call:
        call.record_actual(usd=0.011)
    ledger_file = tmp_path / ".teamnot" / "logs" / "cost_ledger.jsonl"
    assert ledger_file.exists()
    line = ledger_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "minimax" in line
    assert "0.011" in line


def test_register_worker_idempotent():
    tag = WorkerTag(name="claude_cli", billing=BillingModel.subscription)
    register_worker(tag)  # already registered, should be a no-op
    from teamnot.safety import get_worker_tag
    assert get_worker_tag("claude_cli").billing == BillingModel.subscription
