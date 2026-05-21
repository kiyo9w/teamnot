<div align="center">

# TeamNoT

**Autonomous AI development workforce. Hand it a brief, get a finished branch.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#status)
[![Tests: 73 passing](https://img.shields.io/badge/tests-73%20passing-brightgreen.svg)](#testing)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)

*Team No Time* — give a project brief, sleep, wake up to a working deliverable.

</div>

---

## TL;DR

You write a `brief.yaml` with **what to build**, **what "done" means**, and **how much money to spend**.
TeamNoT spawns a multi-agent team (architect → implementer → tester → reviewer → documenter),
loops until the Definition of Done passes, then hands you a feature branch and a report.

It will not silently burn your API balance — every metered call passes a
three-stage **cost guard** (warn → pause → hard stop) backed by an explicit
allow-list.

```bash
git clone https://github.com/jenkytran/teamnot.git
cd teamnot && ./install.sh           # or .\install.ps1 on Windows
source .venv/bin/activate

cd /path/to/your/project
teamnot init                          # scaffolds .teamnot/brief.yaml
$EDITOR .teamnot/brief.yaml           # describe task + Definition of Done
teamnot doctor                        # check environment
teamnot review --brief .teamnot/brief.yaml   # audit project context
teamnot run    --brief .teamnot/brief.yaml   # autonomous run
```

---

## Why TeamNoT?

The autonomous-coding-agent space is crowded. Here's how TeamNoT positions
itself:

| Tool | Mental model | Strength | Where TeamNoT differs |
|---|---|---|---|
| [Aider](https://aider.chat) | Pair-programmer with chat | Tight feedback loop | Aider is interactive; TeamNoT is *fire-and-forget* with a written DoD contract |
| [OpenDevin](https://github.com/All-Hands-AI/OpenDevin) | Sandboxed browser+terminal agent | Capable, general | Heavyweight runtime; TeamNoT runs on the user's own machine with no container, scoped to one project |
| [AutoGen](https://microsoft.github.io/autogen) / [CrewAI](https://www.crewai.com) | Multi-agent SDK | Flexible building blocks | Frameworks, not products. TeamNoT *is* a runnable product with a brief schema, CLI, and Telegram bot |
| [Devin](https://devin.ai) | Hosted SaaS engineer | Polished UX | Closed source, opaque billing. TeamNoT runs locally, makes every call auditable in a JSONL ledger |

TeamNoT exists because we wanted **one CLI invocation** to spin up a small team
of cooperating agents on a real project, with **hard guarantees** on what they
won't do (overspend, push to `main`, deploy, leak secrets).

---

## Features

- 🔁 **Customer Loop** — ingest or run a customer test, write structured
  `.teamnot/customer-loop/` artifacts, choose the next best customer-impact
  move, and generate a follow-up TeamNoT brief. Browser evidence supports both
  deterministic readiness probes, an opt-in sample/demo interactive runner, and
  generated or configured multi-journey browser flow packs, plus seeded
  account/state contracts, screenshot capture metadata, deterministic vision
  review artifacts, and iteration coverage. It does not claim model visual
  judgment unless a future vision worker explicitly provides it. See
  [docs/customer-loop.md](docs/customer-loop.md).
- 📜 **Project Brief Contract** — single `.teamnot/brief.yaml` carries the
  whole job: project, task, Definition of Done, deliverable, budget, allowed
  metered workers.
- ✅ **Definition of Done evaluator** — six check kinds: shell commands,
  files, file contents, HTTP endpoints, custom scripts, LLM judge. Machine
  checks gate the LLM judge so you never pay to review code that already
  failed lint.
- 💸 **CostGuard, on by default** — warn → pause → hard-stop on metered
  spend, JSONL ledger of every call, explicit allow-list (empty by default
  means subscription + local workers only).
- 🤖 **7 bundled agent roles** as Markdown skill files — `coordinator`,
  `architect`, `implementer`, `tester`, `reviewer`, `documenter`,
  `researcher`. Override per project at `<project>/.teamnot/skills/`.
- 🚌 **Typed agent message bus** — agents talk through structured
  `AgentMessage` objects with intents (`request_info`, `reply`, `review`,
  `reject`, `handoff`, `blocker`), full transcript persisted as JSONL.
- 🔁 **DoD-driven pipeline** — loop halts the moment DoD passes; deterministic
  `RuleCoordinator` by default, no LLM cost for orchestration.
- 🧠 **Knowledge-gap review** — refuses to run when critical context is
  missing (no stack info, no machine check in DoD, missing references).
- 📦 **Workspace isolation** — `.teamnot/` lives in the *target project*,
  not in TeamNoT. Per-project memory, plans, reports, checkpoints, logs.
- 🌿 **Safe delivery** — feature branch by default, never touches `main`,
  never force-pushes, never deploys.
- 💬 **Telegram gateway** — `pip install teamnot[telegram]`, point a bot at
  a workspaces folder, run jobs from chat.
- 🩺 **`teamnot doctor`** — environment check that tells you what's missing
  and how to install it.

---

## Architecture

```
                ┌──────────────────────────────────────────────┐
                │  Project (anywhere on disk)                  │
                │  ┌────────────────────────────────────────┐  │
                │  │ .teamnot/                              │  │
                │  │   brief.yaml          ← input          │  │
                │  │   conventions.md      ← rules          │  │
                │  │   memory.md           ← accumulated    │  │
                │  │   plans/<task>.md     ← dual plan      │  │
                │  │   reports/<task>.md   ← final report   │  │
                │  │   qa_reports/         ← ADR + review   │  │
                │  │   checkpoints/        ← per-phase JSON │  │
                │  │   logs/messages.jsonl ← transcript     │  │
                │  │   logs/cost_ledger.jsonl ← every call  │  │
                │  └────────────────────────────────────────┘  │
                └──────────────────────────────────────────────┘
                                  ▲
                                  │ reads + writes
                                  │
┌─────────────────────────────────┴────────────────────────────────────┐
│  teamnot run --brief …                                               │
│                                                                      │
│  1. Workspace.lock()         coarse mutex                            │
│  2. knowledge_review         block if critical gaps                  │
│  3. CostGuard.from_brief()   warn / pause / hard-stop on metered     │
│  4. dual_plan()              MiniMax ‖ Claude CLI → consolidate      │
│  5. Pipeline:                                                        │
│        while iteration < max_dod_attempts:                           │
│            spec   = SkillRegistry.get(next_agent)                    │
│            result = invoker(spec, prompt)   # cost-gated worker      │
│            dod    = DoDEvaluator.evaluate()                          │
│            if dod.all_passed: break                                  │
│            next_agent = coordinator.next_agent(dod, last_agent, …)   │
│  6. handover()               feature_branch | PR | files | tarball   │
│  7. report → stdout | file | telegram | webhook                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Installation

### One-shot installers

```bash
# Linux / macOS
./install.sh                    # core only
./install.sh --telegram         # + Telegram gateway
./install.sh --all --dev        # everything + dev tools (ruff, pytest, mypy)

# Windows (PowerShell)
.\install.ps1                   # core only
.\install.ps1 -WithTelegram     # + Telegram gateway
.\install.ps1 -All -Dev         # everything + dev tools
```

Both scripts:

1. Find a Python 3.11+ interpreter (or error helpfully if none).
2. Create `.venv/` next to the script.
3. `pip install -e .` with whichever extras you asked for.
4. Run `teamnot doctor` as a final check.

### Manual install (if you prefer)

```bash
python3 -m venv .venv
source .venv/bin/activate                        # .\.venv\Scripts\Activate.ps1 on Windows
pip install -e ".[telegram,dev]"                 # pick the extras you need
teamnot doctor
```

### From PyPI (planned)

```bash
pipx install teamnot[telegram]                   # not yet published
```

### External dependencies

| Tool | Required? | Install |
|---|---|---|
| Python 3.11+ | yes | https://python.org |
| git | yes | https://git-scm.com |
| Claude Code CLI | recommended | `npm install -g @anthropic-ai/claude-code`, then `claude login` |
| `gh` CLI | optional | https://cli.github.com (only for `deliverable.type: pull_request`) |
| Ollama | optional | https://ollama.ai (for fully local runs) |

`teamnot doctor` checks every one of these and tells you what's missing.

---

## Quickstart

### 1. Bootstrap a project

```bash
cd /path/to/your/project
teamnot init                                    # creates .teamnot/{brief.yaml,memory.md,conventions.md}
```

### 2. Edit the brief

`.teamnot/brief.yaml` (excerpt):

```yaml
project:
  name: my-service
  path: .
  language: [python]
  stack: [fastapi, postgres]

task:
  id: TASK-2026-05-19-001
  title: Add /health endpoint
  description: |
    Add GET /health that returns {"status":"ok"} with a 200 status code.
    Wire it into the existing FastAPI app at src/main.py. Add a test.

definition_of_done:
  require_all_pass: true
  llm_judge_required: true
  checks:
    - run: "ruff check ."
    - run: "pytest -q"
    - http_check: { url: "http://localhost:8000/health", status: 200 }
      required: false                          # nice-to-have, won't block
    - llm_judge: |
        Verify the implementation matches the task description, has no
        obvious security issues, and follows .teamnot/conventions.md.

deliverable:
  type: feature_branch                         # feature_branch | pull_request | files | tarball | report_only
  base: main
  push_remote: false
  report_to: stdout                            # stdout | file | telegram | webhook

budget:
  max_minutes: 120
  max_usd: 5.0
  allowed_metered_workers: []                  # empty = subscription/local only (safest)
  cost_warn_pct: 0.7
  cost_pause_pct: 0.9
  cost_hard_stop_pct: 1.0
  llm_judge_estimated_usd: 0.01
```

### 3. Fill in `.teamnot/conventions.md`

This is where you tell agents the house style. The knowledge review will
**block** the run if conventions is still the scaffold for a non-trivial task.

### 4. Run

```bash
teamnot review --brief .teamnot/brief.yaml      # audit context first (free)
teamnot run    --brief .teamnot/brief.yaml      # the real run
```

Watch the report at `.teamnot/reports/<task_id>.md`, the cost ledger at
`.teamnot/logs/cost_ledger.jsonl`, and the agent transcript at
`.teamnot/logs/messages.jsonl`.

---

## CLI reference

```
teamnot doctor                                    Environment health check
teamnot init                                      Scaffold .teamnot/ in a project
teamnot validate --brief PATH                     Parse + validate the brief
teamnot review   --brief PATH                     Knowledge-gap audit (refuses on blockers)
teamnot dod      --brief PATH [--skip-judge]      Run DoD checks only (no agents)
teamnot run      --brief PATH                     Full autonomous run
                 [--skip-review]                  bypass the knowledge gap blocker check
                 [--no-plan]                      skip dual planning
                 [--no-pipeline]                  skip the multi-agent pipeline
                 [--no-deliver]                   skip git branch / handover
                 [--skills DIR]                   override skills directory
                 [--wait-lock SECONDS]            wait for workspace lock instead of failing
teamnot resume   --brief PATH                     Show latest checkpoint
teamnot status   --brief PATH                     Cost-guard snapshot
teamnot logs     --brief PATH [--transcript|--ledger] [--lines N]
teamnot skills   list  [--dir DIR] [--json]       List registered skills
teamnot skills   show  NAME                       Print a SKILL.md body
teamnot skills   path                             Print the active skills directory
teamnot workers                                   List workers + billing model
teamnot telegram --workspaces DIR [--token …]     Run the Telegram gateway
```

---

## Roles and skills

A skill is a directory containing a `SKILL.md` with YAML frontmatter + a
Markdown body. The body becomes the system prompt for that role.

| Role | Worker | Hands off to | Metered OK |
|---|---|---|---|
| `coordinator` | claude_cli | architect | no |
| `architect`   | claude_cli | implementer | no |
| `implementer` | claude_cli | tester | no |
| `tester`      | claude_cli | reviewer | no |
| `reviewer`    | claude_cli | documenter | yes |
| `documenter`  | minimax    | — | yes |
| `researcher`  | minimax    | architect | yes |

```bash
teamnot skills list                                 # see all of them
teamnot skills show architect                       # print one
```

Override any of them per-project by dropping a `SKILL.md` into
`<project>/.teamnot/skills/<name>/SKILL.md`. TeamNoT searches in order:
`$TEAMNOT_SKILLS_DIR` → `<project>/.teamnot/skills/` → bundled `skills/`.

---

## Cost guard

The most important safety guarantee. Every worker is tagged with a
**billing model**:

| Billing | Counts toward budget? | Examples |
|---|---|---|
| `metered`      | yes, against `max_usd` | `minimax`, `openai`, `anthropic_api` |
| `subscription` | no — flat fee covers usage | `claude_cli` (OAuth), `codex_cli` (OAuth) |
| `local`        | no — runs on your hardware | `ollama` |

Metered workers are **denied by default**. To opt one in:

```yaml
budget:
  allowed_metered_workers: [minimax]
```

The guard enforces three thresholds:

- **warn** (default 0.7) — log a warning, continue.
- **pause** (default 0.9) — refuse new metered calls; subscription work
  keeps running.
- **hard_stop** (default 1.0) — halt the worker. No further calls of any
  kind.

Every call is recorded to `.teamnot/logs/cost_ledger.jsonl` for audit.

---

## Telegram gateway

```bash
pip install -e ".[telegram]"

teamnot telegram \
    --workspaces /path/to/workspaces \
    --token "$TELEGRAM_BOT_TOKEN" \
    --allow-chat 12345678
```

A *workspaces directory* contains one sub-folder per project, each holding
its own `.teamnot/brief.yaml`. From Telegram:

```
/projects                       list available projects
/run <project>                  start a run
/status <project>               latest report
```

If the brief's `deliverable.report_to: telegram`, the report comes back into
the chat.

---

## Testing

```bash
make test                                          # all 73 tests
pytest tests/test_dod.py -q                       # one suite
pytest -k cost_guard                              # filter
```

The test suite covers:

- Brief schema validation + edge cases
- DoD evaluator for all six check kinds (incl. cost-guarded judge)
- CostGuard allow-list, three-threshold gating, ledger persistence
- Workspace isolation, lock mutex, checkpoint round-trip
- Knowledge-gap review (8 rules)
- Agent message bus (intents, reply correlation, JSONL persistence)
- Skill loading from Markdown
- Pipeline (rule coordinator, retries, max-iterations, no-skills guard)
- Delivery (git branch creation, refuses `main`, diff summary, handover types)

---

## Status

**Alpha (v2.0.0a1).** Core flows are tested and stable on Windows + Python
3.12. Linux/macOS are supported by the install script and exercise the same
code paths, but the public test matrix is currently Windows-only. The
roadmap below is what's gating a beta cut.

### Roadmap

- [ ] Per-skill worker routing in `engine/pipeline.py` so `documenter` →
      `minimax`, `researcher` → `ollama`, etc., instead of all skills going
      through Claude CLI.
- [ ] HTTP gateway (FastAPI) under `gateways/http.py`.
- [ ] LLM-backed coordinator skill (currently `RuleCoordinator` is the
      deterministic default).
- [ ] Web dashboard reading `.teamnot/logs/` + checkpoints.
- [ ] Real resume-from-checkpoint instead of the current "preserve
      artifacts" semantics.
- [ ] Publish on PyPI.

> **About v1.** The original crewAI + PraisonAI prototype that grew into v2
> is preserved on disk under `legacy/` (gitignored). It is kept locally for
> historical reference only — v2 is the supported release.

---

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The bar for
merge is "the code is obviously right and the tests prove it."

---

## Acknowledgements

TeamNoT stands on the shoulders of:

- [crewAI](https://www.crewai.com) and [PraisonAI](https://github.com/MervinPraison/PraisonAI)
  — the multi-agent framework lineage the original prototype used.
- [pydantic](https://docs.pydantic.dev) — Brief schema validation.
- [aiogram](https://docs.aiogram.dev) — Telegram gateway.
- [Click](https://click.palletsprojects.com) and [Rich](https://rich.readthedocs.io)
  — the CLI surface.

---

## License

[MIT](LICENSE) © 2026 Trần Văn Lực (Jenky Trần)

## Maintainer

**Trần Văn Lực** (Jenky Trần)
CEO @ [Awake Drive JSC](#) · TechLead @ [HorseAI JSC](#)
✉ tranvanluc.work@gmail.com

> *"I built TeamNoT for the nights when I wanted to ship one more feature
> before bed but couldn't keep my eyes open. Now the team works while I sleep."*
