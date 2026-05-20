"""DoD-driven multi-agent pipeline.

The pipeline runs a small finite state machine that dispatches one agent at a
time, then re-evaluates the DoD. It halts the moment the DoD passes (or the
cost guard halts, or the retry budget is exhausted).

States and transitions (rule-based default coordinator):

    plan ─► architect ─► implementer ─► tester ─► reviewer ─► documenter ─► done
                                            ▲                    │
                                            └─── REJECT ─────────┘
                                            ▲
                                            │ DoD machine fail
                                            └── implementer (retry)

A skill-driven coordinator can override this by emitting `next_agent`
decisions via the bus; the pipeline obeys those if a `coordinator` skill is
registered.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from teamnot.agents.bus import AgentMessageBus, MessageIntent
from teamnot.agents.spec import AgentSpec, SkillRegistry
from teamnot.brief import Brief
from teamnot.dod import DoDEvaluator, DoDResult
from teamnot.safety import CostGuard
from teamnot.workers.claude_cli import ClaudeCliResult, ClaudeCliWorker
from teamnot.workers.codex_cli import CodexCliWorker
from teamnot.workspace import Workspace

logger = logging.getLogger("teamnot.engine.pipeline")


class PipelineOutcome(str, Enum):
    dod_passed = "dod_passed"
    blocked_budget = "blocked_budget"
    blocked_retries = "blocked_retries"
    blocked_no_skill = "blocked_no_skill"
    error = "error"


@dataclass
class AgentTurn:
    agent: str
    started_at: float
    ended_at: float
    success: bool
    output_preview: str
    error: str = ""

    @property
    def duration_s(self) -> float:
        return round(self.ended_at - self.started_at, 2)


@dataclass
class PipelineResult:
    outcome: PipelineOutcome
    turns: list[AgentTurn] = field(default_factory=list)
    dod_result: DoDResult | None = None
    iterations: int = 0
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        return (
            f"Pipeline: {self.outcome.value} | "
            f"{self.iterations} iter | "
            f"{len(self.turns)} agent turn(s)"
        )


# ── Default rule-based coordinator ───────────────────────────────────────────

class RuleCoordinator:
    """A deterministic coordinator that decides the next agent from DoD state.

    The pipeline uses this when no LLM-backed coordinator skill is configured —
    it costs nothing to run and is easy to test.
    """

    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self._reject_streak: int = 0

    def first_agent(self) -> str | None:
        # Architect always goes first if available
        for name in ("architect", "implementer"):
            if name in self.registry:
                return name
        return None

    def next_agent(
        self,
        *,
        last_agent: str,
        last_turn_ok: bool,
        dod: DoDResult,
        reject_streak: int,
        max_retries: int,
    ) -> str | None:
        """Return the next agent name, or None to stop."""
        if dod.all_passed:
            # Final pass: documenter, then stop
            if last_agent != "documenter" and "documenter" in self.registry:
                return "documenter"
            return None

        if reject_streak >= max_retries:
            return None

        def first_available(*candidates: str) -> str | None:
            for c in candidates:
                if c and c in self.registry:
                    return c
            return None

        # If a machine check failed → back to implementer (after architect first iter)
        if dod.failed_required and any(r.check.kind != "llm_judge" for r in dod.failed_required):
            if last_agent in ("architect", "tester"):
                return first_available("implementer")
            if last_agent == "implementer":
                # Implementer just ran; let the tester verify, else go straight to reviewer
                return first_available("tester", "reviewer", "implementer")
            if last_agent == "reviewer":
                return first_available("implementer")
            return first_available("implementer")

        # Judge rejected → reviewer suggests remediation, then implementer
        if dod.failed_required and any(r.check.kind == "llm_judge" for r in dod.failed_required):
            if last_agent == "reviewer":
                return first_available("implementer")
            return first_available("reviewer", "implementer")

        # Default forward chain
        chain = ["architect", "implementer", "tester", "reviewer", "documenter"]
        try:
            idx = chain.index(last_agent)
            for nxt in chain[idx + 1:]:
                if nxt in self.registry:
                    return nxt
        except ValueError:
            pass
        return None


# ── Pipeline ─────────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    max_iterations: int = 10
    max_consecutive_failures: int = 3
    pause_between_iterations_s: float = 0.0


WorkerInvoker = Callable[[AgentSpec, str], ClaudeCliResult]
"""Callable that turns (agent_spec, user_prompt) into a worker call result.

The pipeline does not know which underlying worker each agent uses — it asks
the invoker to dispatch. Tests can swap in a stub.
"""


class Pipeline:
    """Drives the multi-agent loop. One per task."""

    def __init__(
        self,
        brief: Brief,
        workspace: Workspace,
        cost_guard: CostGuard,
        registry: SkillRegistry,
        bus: AgentMessageBus,
        dod_evaluator: DoDEvaluator,
        invoker: WorkerInvoker,
        config: PipelineConfig | None = None,
    ):
        self.brief = brief
        self.ws = workspace
        self.guard = cost_guard
        self.registry = registry
        self.bus = bus
        self.dod = dod_evaluator
        self.invoker = invoker
        self.config = config or PipelineConfig(
            max_iterations=brief.budget.max_dod_attempts,
            max_consecutive_failures=brief.budget.max_retries,
        )
        self.coordinator = RuleCoordinator(registry)

    # ── Entry ──────────────────────────────────────────────────────────────

    def run(self) -> PipelineResult:
        if not self.registry:
            return PipelineResult(
                outcome=PipelineOutcome.blocked_no_skill,
                error="no skills registered — load from `skills/` first",
            )

        turns: list[AgentTurn] = []
        notes: list[str] = []
        reject_streak = 0
        last_agent: str = ""

        next_agent = self.coordinator.first_agent()
        if not next_agent:
            return PipelineResult(
                outcome=PipelineOutcome.blocked_no_skill,
                error="no architect or implementer skill registered",
            )

        for it in range(1, self.config.max_iterations + 1):
            if self.guard.is_halted:
                return PipelineResult(
                    outcome=PipelineOutcome.blocked_budget,
                    turns=turns,
                    iterations=it - 1,
                    notes=notes + [f"cost guard halted: {self.guard.halt_reason}"],
                )

            spec = self.registry.get(next_agent)
            self.bus.send(
                sender="pipeline",
                recipient=next_agent,
                intent=MessageIntent.request_work,
                subject=f"iteration {it}: do your part",
                payload={"task_id": self.brief.task.id, "iteration": it},
            )
            turn = self._dispatch(spec, iteration=it)
            turns.append(turn)
            self.ws.save_checkpoint(
                self.brief.task.id,
                phase=f"agent-{it:02d}-{next_agent}",
                status="OK" if turn.success else "FAIL",
                payload={
                    "duration_s": turn.duration_s,
                    "preview": turn.output_preview[:200],
                    "error": turn.error[:200],
                },
            )

            # Re-evaluate DoD
            dod_result = self.dod.evaluate()
            if dod_result.all_passed:
                # Optional: documenter handoff
                if (
                    next_agent != "documenter"
                    and "documenter" in self.registry
                ):
                    doc_spec = self.registry.get("documenter")
                    doc_turn = self._dispatch(doc_spec, iteration=it + 1)
                    turns.append(doc_turn)
                return PipelineResult(
                    outcome=PipelineOutcome.dod_passed,
                    turns=turns,
                    dod_result=dod_result,
                    iterations=it,
                    notes=notes,
                )

            # Track consecutive failures
            if turn.success:
                reject_streak = 0
            else:
                reject_streak += 1
                if reject_streak >= self.config.max_consecutive_failures:
                    return PipelineResult(
                        outcome=PipelineOutcome.blocked_retries,
                        turns=turns,
                        dod_result=dod_result,
                        iterations=it,
                        notes=notes + [f"{reject_streak} consecutive failures"],
                    )

            last_agent = next_agent
            next_agent_candidate = self.coordinator.next_agent(
                last_agent=last_agent,
                last_turn_ok=turn.success,
                dod=dod_result,
                reject_streak=reject_streak,
                max_retries=self.config.max_consecutive_failures,
            )
            if not next_agent_candidate:
                return PipelineResult(
                    outcome=PipelineOutcome.blocked_retries,
                    turns=turns,
                    dod_result=dod_result,
                    iterations=it,
                    notes=notes + ["coordinator returned no next agent"],
                )
            next_agent = next_agent_candidate

            if self.config.pause_between_iterations_s > 0:
                time.sleep(self.config.pause_between_iterations_s)

        return PipelineResult(
            outcome=PipelineOutcome.blocked_retries,
            turns=turns,
            iterations=self.config.max_iterations,
            notes=notes + ["max_iterations reached"],
        )

    # ── Single-agent dispatch ─────────────────────────────────────────────

    def _dispatch(self, spec: AgentSpec, iteration: int) -> AgentTurn:
        start = time.monotonic()
        user_prompt = self._build_user_prompt(spec, iteration)
        try:
            result = self.invoker(spec, user_prompt)
            success = (result.returncode == 0) and bool(result.output.strip())
            self.bus.send(
                sender=spec.name,
                recipient="pipeline",
                intent=MessageIntent.handoff if success else MessageIntent.blocker,
                subject=f"iteration {iteration} {'ok' if success else 'failed'}",
                payload={
                    "elapsed_s": round(result.elapsed_s, 2),
                    "returncode": result.returncode,
                    "preview": result.output[:200],
                    "stderr": result.stderr[:200],
                },
            )
            return AgentTurn(
                agent=spec.name,
                started_at=start,
                ended_at=time.monotonic(),
                success=success,
                output_preview=result.output[:400],
                error="" if success else (result.stderr or f"rc={result.returncode}"),
            )
        except Exception as e:
            self.bus.send(
                sender=spec.name,
                recipient="pipeline",
                intent=MessageIntent.blocker,
                subject="crashed",
                payload={"error": f"{type(e).__name__}: {e}"},
            )
            return AgentTurn(
                agent=spec.name,
                started_at=start,
                ended_at=time.monotonic(),
                success=False,
                output_preview="",
                error=f"{type(e).__name__}: {e}",
            )

    def _build_user_prompt(self, spec: AgentSpec, iteration: int) -> str:
        bits = [
            f"# Task: {self.brief.task.title}",
            f"Iteration: {iteration}",
            f"Agent role: {spec.role}",
            "",
            "## Brief task description",
            self.brief.task.description,
            "",
            "## Constraints",
            str(self.brief.task.constraints.model_dump()),
            "",
            "## Recent transcript (last 6 messages)",
        ]
        recent = self.bus.all_messages()[-6:]
        for m in recent:
            bits.append(m.to_md())
        return "\n".join(bits)


# ── Default invoker using the Claude CLI worker ──────────────────────────────

def claude_cli_invoker(claude: ClaudeCliWorker) -> WorkerInvoker:
    """Return a WorkerInvoker that runs every agent through the Claude CLI.

    This is the default path until S3.next adds proper per-worker routing —
    every skill is sent to Claude with its SKILL.md body as the system prompt.
    """
    def _invoke(spec: AgentSpec, user_prompt: str) -> ClaudeCliResult:
        full_prompt = (
            "=== SYSTEM PROMPT (from SKILL.md) ===\n"
            + spec.system_prompt
            + "\n\n=== USER PROMPT ===\n"
            + user_prompt
        )
        return claude.run(
            prompt=full_prompt,
            timeout=spec.timeout_s,
            allowed_tools=spec.tools or None,
            note=f"agent:{spec.name}",
        )
    return _invoke


def routed_cli_invoker(
    *,
    claude: ClaudeCliWorker | None = None,
    codex: CodexCliWorker | None = None,
) -> WorkerInvoker:
    """Return a WorkerInvoker that dispatches by each skill's `worker` field."""

    def _invoke(spec: AgentSpec, user_prompt: str) -> ClaudeCliResult:
        full_prompt = (
            "=== SYSTEM PROMPT (from SKILL.md) ===\n"
            + spec.system_prompt
            + "\n\n=== USER PROMPT ===\n"
            + user_prompt
        )
        if spec.worker == "codex_cli":
            if codex is None:
                raise RuntimeError("codex_cli worker requested, but CodexCliWorker is not configured")
            return codex.run(
                prompt=full_prompt,
                timeout=spec.timeout_s,
                allowed_tools=spec.tools or None,
                note=f"agent:{spec.name}",
            )
        if spec.worker == "claude_cli":
            if claude is None:
                raise RuntimeError("claude_cli worker requested, but ClaudeCliWorker is not configured")
            return claude.run(
                prompt=full_prompt,
                timeout=spec.timeout_s,
                allowed_tools=spec.tools or None,
                note=f"agent:{spec.name}",
            )
        raise RuntimeError(
            f"worker '{spec.worker}' is not supported by the CLI pipeline yet "
            "(supported: claude_cli, codex_cli)"
        )

    return _invoke
