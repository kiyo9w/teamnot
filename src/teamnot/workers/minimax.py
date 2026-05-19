"""MiniMax M2.7 worker — a metered API client.

Wraps the MiniMax M2.7 chat completion endpoint via litellm. Metered: every
call passes through the CostGuard, which estimates spend before the call and
records actual spend after.

Pricing is approximate — MiniMax doesn't always return per-call USD, so we
fall back to a token-based estimate. Better an over-estimate than under.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from teamnot.safety import CostGuard

logger = logging.getLogger("teamnot.workers.minimax")

WORKER_NAME = "minimax"
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
MINIMAX_DEFAULT_MODEL = "MiniMax-M2.7"

# Rough pricing — adjust when MiniMax publishes a stable rate card.
# We keep estimates HIGH so the cost guard errs on the safe side.
_PRICE_PER_1K_INPUT_USD = 0.0030
_PRICE_PER_1K_OUTPUT_USD = 0.0030


@dataclass
class MinimaxResult:
    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_usd: float
    elapsed_s: float


class MinimaxWorker:
    """Bound to a single workspace + cost guard. Reuse per task."""

    def __init__(
        self,
        cost_guard: CostGuard,
        api_key: str | None = None,
        base_url: str = MINIMAX_BASE_URL,
        model: str = MINIMAX_DEFAULT_MODEL,
    ):
        self.guard = cost_guard
        self.api_key = api_key or os.getenv("MINIMAX_API_KEY")
        self.base_url = base_url
        self.model = model

    def is_available(self) -> bool:
        return bool(self.api_key)

    # ── Core call ─────────────────────────────────────────────────────────

    def run(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        reasoning: bool = False,
        note: str = "",
    ) -> MinimaxResult:
        """Single chat completion, cost-guarded.

        Raises WorkerNotAllowedError if `minimax` is not in the brief's
        allow-list, or BudgetExceededError / WorkerPausedError if the cost
        guard refuses the call.
        """
        if not self.api_key:
            raise RuntimeError("MINIMAX_API_KEY not set")

        # Estimate cost from the prompt size — adapt if MiniMax adds a token
        # counter helper. For now: 1 token ~ 4 chars.
        char_count = len(system) + len(user)
        est_input_tokens = max(1, char_count // 4)
        est_output_tokens = max(1, max_tokens // 2)
        est_usd = (
            est_input_tokens / 1000 * _PRICE_PER_1K_INPUT_USD
            + est_output_tokens / 1000 * _PRICE_PER_1K_OUTPUT_USD
        )

        with self.guard.gate(WORKER_NAME, estimated_usd=est_usd, note=note or "minimax") as call:
            import time as _time
            start = _time.monotonic()

            extra_body: dict[str, Any] = {"reasoning": reasoning}
            try:
                import litellm
            except ImportError as e:
                call.record_actual(usd=0.0, note="litellm missing")
                raise RuntimeError(
                    "litellm not installed. Install with: pip install litellm"
                ) from e

            response = litellm.completion(
                model=f"openai/{self.model}",
                api_key=self.api_key,
                api_base=self.base_url,
                extra_body=extra_body,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            elapsed = _time.monotonic() - start

            content = (response.choices[0].message.content or "").strip()
            usage = getattr(response, "usage", None)
            prompt_tokens = getattr(usage, "prompt_tokens", est_input_tokens)
            completion_tokens = getattr(usage, "completion_tokens", est_output_tokens)
            total_tokens = prompt_tokens + completion_tokens

            actual_usd = (
                prompt_tokens / 1000 * _PRICE_PER_1K_INPUT_USD
                + completion_tokens / 1000 * _PRICE_PER_1K_OUTPUT_USD
            )
            call.record_actual(usd=actual_usd, note=f"{total_tokens} tokens")

            return MinimaxResult(
                content=content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_usd=actual_usd,
                elapsed_s=elapsed,
            )
