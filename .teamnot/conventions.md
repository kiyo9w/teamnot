# TeamNoT Self-Improvement Conventions

## Current Goal

TeamNoT is improving itself. Preserve the existing CLI-first product model:
brief-driven autonomous execution, machine-verifiable DoD, local-first safety,
cost guard, auditable reports, and project-local `.teamnot/` artifacts.

## Engineering Rules

- Keep TeamNoT portable. Do not make core imports depend on OpenClaw, Windows,
  Chrome, Playwright, Browserbase, Telegram, or any host-specific runtime.
- Put host-specific browser control behind adapter interfaces.
- Prefer deterministic tests over real browser/network tests.
- Keep subscription/local workers as the safe default; do not introduce metered
  API calls unless a brief explicitly opts in.
- Do not add secrets, tokens, credentials, or local absolute paths as defaults.
- Keep public CLI behavior documented and testable.
- Commit regularly on the feature branch after coherent milestones when the
  working tree is green enough to preserve.

## Style

- Python 3.11+.
- Pydantic models for schemas.
- Click commands for CLI.
- Rich output is fine for human-facing summaries.
- Avoid broad rewrites of unrelated TeamNoT internals.
- Add tests alongside implementation.

## Customer Loop Product Principle

The customer loop is not "another coding agent". It is an overseer loop:

1. experience the product as a realistic target customer,
2. produce evidence and customer-centered findings,
3. choose the next best move by customer impact,
4. generate a new TeamNoT brief,
5. run TeamNoT again,
6. repeat until customer-critical blockers are gone or budget/iteration limits
   stop the loop.

