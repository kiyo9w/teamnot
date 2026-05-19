# Changelog

All notable changes to TeamNoT are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/) and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.0.0a1] — 2026-05-19

The full v2 rewrite. Packaged as a pip-installable Python package, brief-driven,
cost-guarded, multi-project capable. Backwards-incompatible with the v1 scripts
in `*_legacy.py`.

### Added

- **Project Brief Contract** — `.teamnot/brief.yaml` pydantic schema (project,
  task, definition_of_done, deliverable, budget).
- **Definition of Done evaluator** — six check kinds (`run`, `file_exists`,
  `file_contains`, `http_check`, `custom_script`, `llm_judge`). Machine checks
  run first; the LLM judge runs only after they pass to save API spend.
- **CostGuard** with three thresholds (warn / pause / hard-stop) plus a
  `allowed_metered_workers` allow-list (empty by default — subscription/local
  only).
- **Workspace isolation** per target project (`.teamnot/` lives in the project
  root, not in the TeamNoT repo). Includes mutex lock, per-phase checkpoints,
  append-only memory.md writer.
- **Knowledge-gap review** — rule-based audit before pipeline runs; blockers
  refuse the run, warnings/info show in the report.
- **Agent message bus** — typed `AgentMessage` with intents
  (`request_info`, `request_work`, `reply`, `review`, `approve`, `reject`,
  `handoff`, `blocker`), reply correlation, JSONL transcript.
- **Skills system** — Markdown `SKILL.md` files with YAML frontmatter; seven
  bundled skills (coordinator, architect, implementer, tester, reviewer,
  documenter, researcher). Per-project override at `<project>/.teamnot/skills/`.
- **Multi-agent pipeline** — `RuleCoordinator` drives the implement → test →
  review → document loop, halting on DoD pass. Pluggable for an LLM-backed
  coordinator.
- **Workers** — `ClaudeCliWorker` (subprocess + OAuth, subscription) and
  `MinimaxWorker` (litellm + token-pricing estimate, metered).
- **Delivery** — feature_branch / pull_request (`gh`) / files / tarball /
  report_only; report routing to stdout / file / telegram / webhook.
- **Telegram gateway** — `aiogram` v3 bot, opt-in via `pip install teamnot[telegram]`.
- **CLI** — `init`, `validate`, `review`, `dod`, `run`, `resume`, `status`,
  `logs`, `skills (list|show|path)`, `workers`, `doctor`, `telegram`.
- **Installer scripts** — `install.ps1` (Windows), `install.sh` (Linux/macOS),
  `Makefile` (Unix). One command from clone to `teamnot doctor` green.

### Changed

- Architecture moved from "TeamNoT root is the source of truth" to "project
  is the source of truth, TeamNoT is a stateless worker."

### Removed

- v1 entry points (`teamnot.py`, `crew_teamnot.py`, `dual_planner.py`,
  `claude_worker.py`, `cli.py`) — kept on disk as `*_legacy.py` for reference;
  will be removed once v2 is verified in production.

## [1.x] — pre-2026-05-19

The single-project crewAI + PraisonAI prototype. Preserved as `*_legacy.py`.
