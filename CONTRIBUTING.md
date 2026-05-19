# Contributing to TeamNoT

Thanks for considering a contribution. TeamNoT is a small, focused autonomous
agent framework — the bar for merge is "the code is obviously right and the
tests prove it." This guide describes the workflow.

## Local setup

```bash
git clone https://github.com/jenkytran/teamnot.git
cd teamnot
./install.sh --all --dev          # or .\install.ps1 -All -Dev on Windows
source .venv/bin/activate         # .\.venv\Scripts\Activate.ps1 on Windows
teamnot doctor                    # sanity-check the environment
make test                         # 73 tests should pass cold
```

## Before opening a PR

| Step | Command |
|---|---|
| Tests pass | `make test` or `pytest -q` |
| Lint clean | `make lint` or `ruff check src tests` |
| Format     | `make format` or `ruff format src tests` |
| Type check | `make typecheck` or `mypy src` |
| Doctor OK  | `teamnot doctor` |

## What lives where

```
src/teamnot/
├── brief.py            ; the Project Brief Contract (pydantic schema)
├── dod.py              ; Definition-of-Done evaluator
├── safety.py           ; CostGuard, BillingModel, ledger
├── workspace.py        ; per-project .teamnot/ state
├── memory/             ; knowledge_review and project-memory writers
├── agents/             ; SkillRegistry + AgentMessageBus
├── workers/            ; Claude CLI, MiniMax, ... adapters
├── engine/             ; planner, pipeline, worker (the orchestration)
├── delivery/           ; git branch / PR / handover
├── gateways/           ; CLI is built-in; Telegram is opt-in
└── cli/__main__.py     ; click entry point

skills/                 ; bundled SKILL.md role files
templates/brief.yaml    ; scaffold used by `teamnot init`
tests/                  ; pytest suite
```

## Coding conventions

- **Python 3.11+**. Use `from __future__ import annotations` everywhere.
- **Pydantic v2** for any new schema; `BaseModel` + `Field(...)`.
- **No silent failures.** Either raise a typed exception or return a result
  object — never both for the same function.
- **Cost-guarded** is non-negotiable: every new worker that talks to a paid
  API goes through `CostGuard.gate(...)`. Register its billing model in
  `safety.py` so the brief allow-list can gate it.
- **Tests before merge.** Bug fixes need a regression test. New features
  need at least one happy-path test and one failure-path test.
- **Subprocess safety.** Prefer `shlex.split()` + `shell=False`. The one
  place that uses `shell=True` is `dod._check_run` and the threat model is
  documented there.
- **No `sys.path` hacking.** If imports break, fix the layout.

## Commit style

Conventional commits are appreciated but not enforced:

```
feat(engine): per-skill worker routing
fix(safety): allow zero-budget runs when only subscription workers used
docs(readme): clarify Telegram gateway setup
```

## Reporting issues

Open a GitHub issue with:

1. What you ran (`teamnot --version`, the exact command, OS).
2. What you expected.
3. What happened (paste stdout/stderr; if relevant, the `.teamnot/logs/*`).
4. A minimal `brief.yaml` that reproduces the issue if possible.

## Areas where help is especially welcome

- Per-skill worker routing in `engine/pipeline.py` (currently all skills
  route through Claude CLI; a `WorkerRegistry` keyed by `spec.worker` would
  let `documenter → minimax`, `researcher → ollama`, etc.).
- More skill files: a `migrator`, `security-auditor`, `release-engineer`.
- A FastAPI HTTP gateway (the `gateways/` package has only Telegram today).
- A web dashboard backed by `.teamnot/logs/` and `.teamnot/checkpoints/`.

## Maintainer

**Trần Văn Lực (Jenky Trần)**
CEO @ Awake Drive JSC · TechLead @ HorseAI JSC
✉ tranvanluc.work@gmail.com

By contributing you agree your code is released under the project's
[MIT License](LICENSE).
