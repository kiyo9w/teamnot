"""Cost guard and safety gates.

TeamNoT lets users mix subscription workers (Claude Code CLI via OAuth, where
a flat fee covers usage) with metered API workers (MiniMax, OpenAI, Anthropic
API — pay per token). Without a guard, an autonomous loop can quietly burn
through the API balance.

The CostGuard tracks combined metered spend and enforces three thresholds:

  * warn_pct (default 70%)   — emit a warning, continue
  * pause_pct (default 90%)  — refuse new METERED calls, keep subscription work
  * hard_stop_pct (default 100%) — halt the worker entirely

It also enforces a metered allow-list: a worker tagged `metered=True` is only
runnable if its name is listed in ``budget.allowed_metered_workers``. Empty
list means "subscription/local only" — safe default for autonomous runs.

Wire it like this:

    guard = CostGuard.from_brief(brief)
    with guard.gate(worker="minimax", estimated_usd=0.02) as call:
        result = minimax_client.completion(...)
        call.record_actual(usd=result.cost)
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from teamnot.brief import Brief, Budget

logger = logging.getLogger("teamnot.safety")


# ── Worker tagging ───────────────────────────────────────────────────────────

class BillingModel(str, Enum):
    """How a worker is billed."""
    metered = "metered"            # Pay per call (OpenAI, MiniMax, Anthropic API…)
    subscription = "subscription"  # Flat fee covers usage (Claude Code CLI OAuth, ChatGPT Plus…)
    local = "local"                # Runs locally (Ollama, llama.cpp, scripts…)


@dataclass(frozen=True)
class WorkerTag:
    """Identity + billing model of a worker. Workers register at import time."""
    name: str
    billing: BillingModel
    notes: str = ""


# Registry filled by workers/registry.py and built-in workers
_WORKER_REGISTRY: dict[str, WorkerTag] = {}


def register_worker(tag: WorkerTag) -> None:
    """Workers call this during module import to declare their billing model."""
    if tag.name in _WORKER_REGISTRY and _WORKER_REGISTRY[tag.name] != tag:
        logger.warning("Worker %s re-registered with different tag, keeping first", tag.name)
        return
    _WORKER_REGISTRY[tag.name] = tag


def get_worker_tag(name: str) -> WorkerTag:
    if name not in _WORKER_REGISTRY:
        # Unknown worker — assume metered for safety (deny by default)
        return WorkerTag(name=name, billing=BillingModel.metered, notes="unregistered, assumed metered")
    return _WORKER_REGISTRY[name]


def all_workers() -> list[WorkerTag]:
    return list(_WORKER_REGISTRY.values())


# ── Exceptions ───────────────────────────────────────────────────────────────

class BudgetExceededError(RuntimeError):
    """Raised when a call would push metered spend past the hard-stop threshold."""


class WorkerNotAllowedError(RuntimeError):
    """Raised when a metered worker is invoked but not in the allow-list."""


class WorkerPausedError(RuntimeError):
    """Raised when metered calls are paused (pause_pct reached) but the loop tries one anyway."""


# ── Cost accounting ──────────────────────────────────────────────────────────

@dataclass
class CallRecord:
    worker: str
    billing: BillingModel
    estimated_usd: float
    actual_usd: float | None
    started_at: str
    ended_at: str | None = None
    note: str = ""


@dataclass
class CostLedger:
    """Append-only record of every guarded call. Persisted next to brief."""
    records: list[CallRecord] = field(default_factory=list)

    @property
    def metered_total_usd(self) -> float:
        return sum(
            (r.actual_usd if r.actual_usd is not None else r.estimated_usd)
            for r in self.records
            if r.billing == BillingModel.metered
        )

    @property
    def metered_call_count(self) -> int:
        return sum(1 for r in self.records if r.billing == BillingModel.metered)

    def to_markdown(self) -> str:
        lines = ["| time | worker | billing | est USD | actual USD | note |",
                 "|---|---|---|---|---|---|"]
        for r in self.records:
            actual = f"{r.actual_usd:.4f}" if r.actual_usd is not None else "—"
            lines.append(
                f"| {r.started_at} | {r.worker} | {r.billing.value} | "
                f"{r.estimated_usd:.4f} | {actual} | {r.note} |"
            )
        return "\n".join(lines)


# ── The guard ────────────────────────────────────────────────────────────────

class CostGuard:
    """Enforces the budget caps before each guarded worker call.

    Thread-safe: the autonomous loop may run planner + executor in parallel
    threads (dual_planner), and both go through the same guard.
    """

    def __init__(self, budget: Budget, ledger_path: Path | None = None):
        self.budget = budget
        self.ledger = CostLedger()
        self.ledger_path = ledger_path
        self._lock = threading.RLock()
        self._halted: bool = False
        self._halt_reason: str = ""
        self._started_monotonic = time.monotonic()

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_brief(cls, brief: Brief) -> CostGuard:
        ledger_path = brief.logs_dir / "cost_ledger.jsonl"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(brief.budget, ledger_path=ledger_path)

    # ── Public checks ──────────────────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        with self._lock:
            return self._halted

    @property
    def halt_reason(self) -> str:
        with self._lock:
            return self._halt_reason

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self.ledger.metered_total_usd

    @property
    def elapsed_minutes(self) -> float:
        return (time.monotonic() - self._started_monotonic) / 60.0

    def remaining_usd(self) -> float:
        return max(0.0, self.budget.max_usd - self.spent_usd)

    def remaining_minutes(self) -> float:
        return max(0.0, self.budget.max_minutes - self.elapsed_minutes)

    def can_start_metered_call(self, estimated_usd: float) -> tuple[bool, str]:
        """Pre-flight check: can we afford this call? Returns (allowed, reason)."""
        if not self.budget.cost_guard_enabled:
            return True, "guard disabled"
        with self._lock:
            if self._halted:
                return False, f"halted: {self._halt_reason}"
            if self.elapsed_minutes >= self.budget.max_minutes:
                return False, f"time budget exhausted ({self.elapsed_minutes:.1f}/{self.budget.max_minutes} min)"
            projected = self.spent_usd + max(0.0, estimated_usd)
            if projected > self.budget.max_usd * self.budget.cost_hard_stop_pct:
                return False, (
                    f"would exceed hard-stop: ${projected:.4f} > "
                    f"${self.budget.max_usd * self.budget.cost_hard_stop_pct:.4f}"
                )
            if projected > self.budget.max_usd * self.budget.cost_pause_pct:
                return False, (
                    f"pause threshold reached: ${projected:.4f} > "
                    f"${self.budget.max_usd * self.budget.cost_pause_pct:.4f}"
                )
            return True, "ok"

    def check_worker_allowed(self, worker_name: str) -> tuple[bool, str]:
        """Allow-list check for metered workers."""
        tag = get_worker_tag(worker_name)
        if tag.billing != BillingModel.metered:
            return True, f"{worker_name} is {tag.billing.value} (not metered)"
        if not self.budget.cost_guard_enabled:
            return True, "guard disabled"
        if not self.budget.require_explicit_api_optin:
            return True, "opt-in not required"
        if worker_name in self.budget.allowed_metered_workers:
            return True, "in allow-list"
        return False, (
            f"metered worker '{worker_name}' is not in allowed_metered_workers "
            f"(allowed: {self.budget.allowed_metered_workers or 'NONE — subscription/local only'}). "
            f"Add it explicitly to opt in."
        )

    # ── Main gate ──────────────────────────────────────────────────────────

    @contextmanager
    def gate(
        self,
        worker: str,
        estimated_usd: float = 0.0,
        note: str = "",
    ) -> Iterator[GatedCall]:
        """Context manager that wraps a single worker call.

        Raises before yielding if the call is not allowed. Inside the block,
        the caller should invoke ``call.record_actual(usd=...)`` once the
        real cost is known. On exit the ledger is updated and persisted.
        """
        tag = get_worker_tag(worker)

        # 1. Allow-list check
        allowed_worker, reason_w = self.check_worker_allowed(worker)
        if not allowed_worker:
            raise WorkerNotAllowedError(reason_w)

        # 2. Budget check (only for metered)
        if tag.billing == BillingModel.metered:
            ok, reason = self.can_start_metered_call(estimated_usd)
            if not ok:
                if "hard-stop" in reason:
                    with self._lock:
                        self._halted = True
                        self._halt_reason = reason
                    raise BudgetExceededError(reason)
                raise WorkerPausedError(reason)

        # 3. Time check (applies to ALL workers)
        if self.elapsed_minutes >= self.budget.max_minutes:
            with self._lock:
                self._halted = True
                self._halt_reason = "time budget exhausted"
            raise BudgetExceededError(self._halt_reason)

        # 4. Open the record
        rec = CallRecord(
            worker=worker,
            billing=tag.billing,
            estimated_usd=max(0.0, estimated_usd),
            actual_usd=None,
            started_at=datetime.now().isoformat(timespec="seconds"),
            note=note,
        )
        call = GatedCall(rec)
        try:
            yield call
        finally:
            rec.ended_at = datetime.now().isoformat(timespec="seconds")
            with self._lock:
                self.ledger.records.append(rec)
                self._maybe_warn()
                self._persist()

    # ── Persistence ────────────────────────────────────────────────────────

    def _persist(self) -> None:
        if not self.ledger_path:
            return
        try:
            import json
            with self.ledger_path.open("a", encoding="utf-8") as f:
                rec = self.ledger.records[-1]
                f.write(
                    json.dumps(
                        {
                            "worker": rec.worker,
                            "billing": rec.billing.value,
                            "estimated_usd": rec.estimated_usd,
                            "actual_usd": rec.actual_usd,
                            "started_at": rec.started_at,
                            "ended_at": rec.ended_at,
                            "note": rec.note,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError as e:
            logger.warning("Cost ledger persist failed: %s", e)

    # ── Diagnostics ────────────────────────────────────────────────────────

    def _maybe_warn(self) -> None:
        if not self.budget.cost_guard_enabled:
            return
        used = self.spent_usd
        cap = self.budget.max_usd or 1e-9
        pct = used / cap
        if pct >= self.budget.cost_pause_pct:
            logger.warning(
                "CostGuard PAUSE — metered spend $%.4f / $%.4f (%.0f%%). "
                "New metered calls will be refused.",
                used, cap, pct * 100,
            )
        elif pct >= self.budget.cost_warn_pct:
            logger.warning(
                "CostGuard WARN — metered spend $%.4f / $%.4f (%.0f%%).",
                used, cap, pct * 100,
            )

    def status(self) -> dict:
        return {
            "halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "spent_usd": round(self.spent_usd, 6),
            "remaining_usd": round(self.remaining_usd(), 6),
            "elapsed_minutes": round(self.elapsed_minutes, 2),
            "remaining_minutes": round(self.remaining_minutes(), 2),
            "metered_calls": self.ledger.metered_call_count,
            "total_calls": len(self.ledger.records),
            "budget": {
                "max_usd": self.budget.max_usd,
                "max_minutes": self.budget.max_minutes,
                "warn_pct": self.budget.cost_warn_pct,
                "pause_pct": self.budget.cost_pause_pct,
                "hard_stop_pct": self.budget.cost_hard_stop_pct,
                "allowed_metered_workers": self.budget.allowed_metered_workers,
            },
        }


@dataclass
class GatedCall:
    record: CallRecord

    def record_actual(self, usd: float, note: str | None = None) -> None:
        """Caller fills in the real cost once known (e.g. from API response usage)."""
        self.record.actual_usd = max(0.0, float(usd))
        if note:
            self.record.note = (self.record.note + " | " if self.record.note else "") + note


# ── Built-in worker registrations ────────────────────────────────────────────

# These tags are authoritative for the workers we ship; plug-ins can register more.
register_worker(WorkerTag(
    name="claude_cli",
    billing=BillingModel.subscription,
    notes="Claude Code CLI via OAuth — covered by claude.ai subscription, no per-call billing",
))
register_worker(WorkerTag(
    name="minimax",
    billing=BillingModel.metered,
    notes="MiniMax M2.7 via api.minimax.io — pay per token",
))
register_worker(WorkerTag(
    name="openai",
    billing=BillingModel.metered,
    notes="OpenAI API — pay per token",
))
register_worker(WorkerTag(
    name="anthropic_api",
    billing=BillingModel.metered,
    notes="Anthropic API direct — pay per token (distinct from claude_cli OAuth)",
))
register_worker(WorkerTag(
    name="ollama",
    billing=BillingModel.local,
    notes="Local Ollama — no external billing",
))
register_worker(WorkerTag(
    name="codex_cli",
    billing=BillingModel.subscription,
    notes="Codex CLI via OAuth — covered by ChatGPT subscription",
))
