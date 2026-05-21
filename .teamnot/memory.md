# TeamNoT Project Memory

## Context: Customer Loop Productization

On 2026-05-20, TeamNoT was used against a real Shopify CSV Preflight product.
The effective workflow was:

1. TeamNoT built a product from a long final-state brief.
2. The product was deployed through a Cloudflare tunnel for phone review.
3. A customer-testing skill used Windows Chrome CDP/OpenClaw wrappers to behave
   like a realistic Shopify agency operations lead.
4. The tester produced customer-centered reports with evidence, screenshots,
   objections, severity, scores, and recommended next iterations.
5. An overseer interpreted the report, selected the highest-leverage next move,
   wrote a new TeamNoT brief, and ran TeamNoT again.
6. This loop found and fixed multiple high-impact trust blockers:
   - upload/file picker friction,
   - local-path defaults in customer UI,
   - false media hard failures when live probing was disabled,
   - wrong file classes producing confident reports,
   - blank report preview after success.

The user wants this workflow productized as a first-class TeamNoT capability.

## Customer Loop Productization Implementation — 2026-05-20

Patterns applied:

- Customer Loop was implemented as a higher-level package around the existing
  brief/DoD engine, not as another pipeline worker role.
- Core schemas, artifact IO, deterministic overseer selection, brief generation,
  and CLI entry points live under `src/teamnot/customer_loop/` and
  `src/teamnot/cli/__main__.py`.
- Browser-specific behavior stays behind the `ExperienceRunner` protocol. The
  OpenClaw Windows CDP runner shells out to `scripts/winbrowser` only when that
  adapter is selected, and missing wrappers produce a readable runner error.
- Manual evidence mode is the safe baseline. It can normalize an existing
  customer report into project-local `.teamnot/customer-loop/` artifacts without
  a browser, network, deployment, or metered model call.
- The overseer logic is deterministic: severity ranking is critical, high,
  medium, low, positive, with trust/core-task blockers preferred ahead of polish
  at the same severity.
- Generated follow-up briefs include report/evidence context, precise required
  behavior, non-goals, safety constraints, and machine-verifiable DoD so the
  next `teamnot run` remains auditable.

Gotchas discovered:

- The tester pass initially found failures in manual evidence CLI behavior and
  generated brief validity. The follow-up implementer pass fixed those before
  final documentation.
- `pytest` is not available on the shell `PATH` here; use
  `.venv/bin/python -m pytest` for verification in this workspace.
- The working tree also contains unrelated uncommitted Codex-worker integration
  edits. Do not conflate those with the committed customer-loop productization
  commit or revert them while working on customer-loop documentation.

Decisions worth remembering:

- Keep `--no-run-teamnot` as the default for `teamnot customer-loop`; recursive
  TeamNoT execution requires explicit `--run-teamnot` opt-in.
- Store customer-loop evidence under the target project's local `.teamnot/`
  artifacts or an explicit `--out` directory. Do not default browser evidence
  into TeamNoT's own source tree when evaluating another product.
- Future LLM-based customer-report synthesis can be added later, but it should
  remain optional and behind a worker boundary so deterministic tests and local
  safety remain the default.

## Customer Researcher Hardening Pass — 2026-05-22

Patterns applied:

- Customer Loop was hardened as a portable research contract: seeded state,
  browser runtime metadata, screenshot captures, vision review, action memory,
  persona/JTBD panels, domain oracles, and iteration coverage are represented as
  typed artifacts rather than loose report text.
- The real browser path stayed behind the Windows/CDP adapter. Core TeamNoT
  imports still do not require OpenClaw, Windows, Chrome, Playwright,
  Browserbase, or any metered model runtime.
- Screenshot review now has an honest deterministic baseline: metadata, hashes,
  dimensions, grouping, and capture-quality heuristics are written separately
  from DOM/text evidence and are not described as model visual judgment.
- The researcher loop records route/action memory and iteration coverage so
  broad exploration, no-op suppression, replay detection, and next-branch
  choices can be audited after a run.
- Dogfood covered both Bulletproof React and the Shopify CSV Preflight target,
  producing browser runtime, screenshot capture, vision review, research memory,
  persona/JTBD, domain-oracle, and iteration coverage artifacts.

Gotchas discovered:

- Previous replay detection only compared adjacent iterations; the fix compares
  against the full prior iteration history so non-adjacent repeats are marked as
  replayed instead of new evidence.
- Browser evidence can be present but hidden if reports only print selected
  evidence summaries. The report writer now prints evidence kinds so
  `browser_research_brain` is visible in customer reports and DoD checks.
- Seeded authentication is a contract plus adapter hook, not a universal
  guarantee. Adapters may still report unsupported storage-state import or app
  login blockers, and artifacts must preserve that distinction.
- Deterministic visual review is useful for proving screenshots exist and
  changed, but it cannot judge visual quality. Reports must keep using
  `metadata_only`/`heuristic` wording unless a future worker performs real visual
  judgment.
- Real-browser smoke depends on the local Windows CDP session on port 18801 and
  should remain an integration command outside ordinary unit tests.

Decisions worth remembering:

- Treat the current customer researcher as beta/internal dogfood, not a finished
  production feature, until seeded post-auth coverage and optional model/vision
  review have more field validation.
- Keep `browser_runtime.yaml`, `screenshot_captures.yaml`,
  `vision_review.yaml`, `research_action_memory.yaml`, and
  `iteration_coverage.yaml` as first-class audit artifacts for browser-capable
  customer-loop runs.
- Preserve the safe default: no metered model calls, deterministic unit tests,
  and `--no-run-teamnot` unless recursive TeamNoT execution is explicitly
  requested.
