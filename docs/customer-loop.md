# Customer Loop

Customer Loop is a higher-level TeamNoT workflow around the existing brief and
Definition-of-Done engine. It treats the product like a target customer would:
experience the app, capture evidence, write customer-centered findings, choose
the next best move by impact, generate a follow-up TeamNoT brief, and optionally
run that brief.

It is not ordinary QA and it is not another coding-agent role. QA usually asks
whether the implementation satisfies a known spec. Customer Loop asks whether a
realistic customer can trust and use the product for the job that caused them to
look for a solution.

## Proven Workflow

The first proven manual loop used a Shopify CSV Preflight product. TeamNoT built
the product from detailed YAML briefs. A customer-testing workflow then reviewed
the deployed app through a Cloudflare tunnel as a Shopify agency operations lead.
The report included observed browser behavior, screenshots, customer
interpretation, business impact, likely frequency, recommended fix, severity,
and scores for value, usability, trust, task success, domain fit, buying
readiness, retention, confidence, and reliability.

For manual evidence, label blocker fields explicitly when possible:

```markdown
Trust blocker: yes
Core task blocker: no
```

Those labels override loose keyword heuristics and make the overseer's priority
ranking more reliable.

That loop found issues ordinary machine DoD did not naturally catch:

- the product looked too local and developer-only,
- media URLs that were not checked were described as hard failures,
- wrong `.md` files were accepted as CSV input,
- a report preview was blank even though download worked.

The overseer selected the highest-impact next move, generated a new TeamNoT
brief, and TeamNoT executed it.

## Commands

Ingest an existing customer-testing report:

```bash
teamnot customer-test \
  --target https://example-product.test \
  --profile .teamnot/customer-loop/shopify-agency-ops.yaml \
  --evidence .teamnot/customer-testing/report.md \
  --runner manual \
  --out .teamnot/customer-loop/run-001
```

Generate a follow-up brief from the customer report:

```bash
teamnot customer-loop \
  --target https://example-product.test \
  --profile .teamnot/customer-loop/shopify-agency-ops.yaml \
  --seeded-state .teamnot/customer-loop/seeded-state.yaml \
  --domain-oracle .teamnot/customer-loop/domain-oracle.yaml \
  --evidence .teamnot/customer-testing/report.md \
  --runner manual \
  --out .teamnot/customer-loop/run-001 \
  --severity-threshold high \
  --no-run-teamnot
```

`--no-run-teamnot` is the safe default. Passing `--run-teamnot` explicitly lets
the loop invoke `teamnot run --brief generated_brief.yaml`.

Generate a starter flow pack for a project:

```bash
teamnot customer-flow-plan \
  --target https://example-product.test \
  --profile .teamnot/customer-loop/customer.yaml \
  --route / \
  --route /app/projects \
  --route /settings/team \
  --out .teamnot/customer-loop/customer_flow.yaml
```

This creates a universal multi-journey YAML scaffold with TODO selectors and
expected text. It is intentionally project-agnostic: agents can fill in exact
selectors/actions after inspecting the product, then run it through
`--runner openclaw-windows-flow`.

Inspect a running product and generate a richer starter from real DOM controls:

```bash
teamnot customer-flow-inspect \
  --target https://example-product.test \
  --profile .teamnot/customer-loop/customer.yaml \
  --out .teamnot/customer-loop/customer_flow.yaml
```

This optional OpenClaw adapter uses `scripts/winbrowser` to navigate each route,
read visible buttons, links, forms, labels, and customer-facing trust/recovery
copy, then writes a flow pack with concrete selectors and action text where the
browser can infer them. It still leaves TODOs where only product intent can
decide the right customer input or success criterion. If no `--route` is
provided, TeamNoT first explores visible internal links/actions from the target
page, prioritizes main-content product/workflow routes over nav/footer links,
and inspects the highest-value routes. Pass explicit `--route` values when a
private app needs a known dashboard, settings, billing, or record screen.

Map the product before generating flows:

```bash
teamnot customer-explore \
  --target https://example-product.test \
  --profile .teamnot/customer-loop/customer.yaml \
  --out .teamnot/customer-loop/product_exploration.yaml
```

This planner reads visible internal routes/actions and produces a product map:
route kinds, journey priorities, persona lenses, and coverage gaps. It is the
generalization layer before flow execution. It marks auth/account/state,
multi-persona buyer review, and domain-output correctness as explicit gaps
unless the project provides a seeded account, domain fixture/oracle, or
human-approved flow.

Run a full inspected customer session:

```bash
teamnot customer-flow-session \
  --target https://example-product.test \
  --profile .teamnot/customer-loop/customer.yaml \
  --seeded-state .teamnot/customer-loop/seeded-state.yaml \
  --domain-oracle .teamnot/customer-loop/domain-oracle.yaml \
  --out .teamnot/customer-loop/session-001
```

This performs the productized loop for flow discovery: inspect the browser DOM,
run the product exploration planner when routes are not supplied, write
`product_exploration.yaml`, inspect selected routes, write `inspected_flow.yaml`,
convert unresolved TODO/external-risky actions into a safer
`runnable_flow.yaml`, execute that flow with screenshots, write the customer
report, and emit `flow_refinement_report.md`. External downloads, installers,
login, checkout, claim-offer, and account actions are verified as visible
links/text unless a human-approved flow explicitly models the click.

## Seeded State

Authenticated apps need an explicit state contract. Pass `--seeded-state` to
`customer-test`, `customer-flow-session`, or `customer-loop`:

```yaml
storage_state_path: .teamnot/customer-loop/storage-state.json
cookies:
  - name: session
    value: replace-in-local-fixture
    domain: 127.0.0.1
    path: /
local_storage:
  - origin: http://127.0.0.1:3000
    values:
      workspace: demo-workspace
test_account:
  email: customer@example.test
  password: replace-in-local-fixture
  login_url: http://127.0.0.1:3000/auth/login
  workspace_id: demo-workspace
cleanup_notes: Reset the demo workspace after each run.
reset_notes: Delete generated records before reusing the account.
workspace_id: demo-workspace
safety_constraints:
  - Use test data only.
  - Do not touch production billing or irreversible actions.
```

Artifacts write `seeded_state_metadata.yaml` with passwords, cookies, and
localStorage values redacted. Browser-capable adapters report whether state was
`applied`, `metadata_only`, or `unsupported`; unsupported adapter behavior is a
blocker in the artifact, not hidden as authenticated coverage. The Windows CDP
session can apply cookies/localStorage and can attempt a Playwright
`storageState` import. Login hints record account metadata only; TeamNoT does
not type passwords into a product unless a project-specific flow explicitly
models that action.

## Domain Oracles

Use `--domain-oracle` when output correctness matters:

```yaml
oracles:
  - name: CSV preflight report
    expected_output: prioritized import blockers
    golden_file: fixtures/expected-report.md
    api_check: GET /api/reports/latest
    semantic_rubric: Report must separate hard blockers from warnings.
    manual_checkpoint: Confirm the downloaded report matches the on-screen summary.
```

If no oracle is provided, the report records a coverage gap. Generic DOM and UI
evidence can prove interaction and trust cues; it cannot prove that a report,
invoice, recommendation, import result, or generated artifact is domain-correct.

## Screenshot And Vision Review

Browser runs write:

- `browser_runtime.yaml` with CDP URL/port, session id, profile/page details,
  screenshot method, and adapter blockers.
- `screenshot_captures.yaml` with one record per capture attempt, including
  route/action, method, retry/fallback metadata, success, hash, and dimensions
  when available.
- `vision_review.yaml` with deterministic screenshot grouping, hash-change
  heuristics, missing/blank capture blockers, model visual findings/action
  hints when enabled, and a `review_kind`.

The default reviewer is local and deterministic. It collects visual metadata and
hash-level change signals only. Reports distinguish DOM/text evidence, visual
metadata, heuristic screenshot health, and model visual judgment.

Model vision can be enabled with:

```bash
TEAMNOT_VISION_WORKER=codex_cli uv run teamnot customer-test \
  --runner openclaw-windows-researcher \
  --target http://127.0.0.1:3000/ \
  --profile customer_profile.yaml \
  --out .teamnot/customer-loop/example-with-vision
```

When enabled, TeamNoT attaches successful screenshot files to `codex exec
--image`, asks for structured JSON, stores visual findings/action hints in
`vision_review.yaml`, and feeds those findings back into the customer report as
`model_vision` evidence. If the CLI is unavailable, unauthenticated, or returns
malformed output, the run falls back to deterministic screenshot metadata and
records a model-vision blocker instead of overclaiming.

## Artifacts

Customer Loop writes project-local artifacts under the requested `--out`
directory, commonly `.teamnot/customer-loop/<run-id>/`:

- `customer_profile.yaml`
- `customer_test_plan.yaml`
- `customer_report.md`
- `customer_report.json`
- `product_exploration.yaml`
- `inspected_flow.yaml`
- `runnable_flow.yaml`
- `flow_refinement_report.md`
- `generated_brief.yaml`
- `loop_summary.md`
- `seeded_state_metadata.yaml`
- `browser_runtime.yaml`
- `screenshot_captures.yaml`
- `vision_review.yaml`
- `persona_jtbd_panel.yaml`
- `domain_oracles.yaml`
- `research_action_memory.yaml`
- `iteration_coverage.yaml`
- `screenshots/`

When evaluating another project, put `--out` under that project so browser
evidence and customer reports do not land in TeamNoT's own source tree.

## Adapter Design

Core TeamNoT imports do not depend on OpenClaw, Windows, Chrome, Playwright, or
CDP. The `ExperienceRunner` protocol isolates evidence collection. The baseline
`ManualEvidenceRunner` consumes an existing report file and normalizes it into
customer-loop artifacts.

The optional `OpenClawWindowsCDPRunner` shells out to `scripts/winbrowser` when
present and reports a readable error when absent. It is not a title-only smoke
check: it navigates in a real Windows Chrome/CDP session, captures first
impression, full-page, and mobile-review screenshots, probes the DOM, and emits
`STEP_PASS`/`STEP_FAIL`/`STEP_SKIP` markers using the `customer-testing-openclaw`
rubric. Coverage includes first 30-second impression, customer promise, core
workflow cues, output/actionability, error recovery, trust/data handling,
adoption and commercial cues, domain fit, time-to-value, recommendation clarity,
mobile review, accessibility basics, layout overflow, resource health, JTBD
forces, buyer/user mismatch, and emotional confidence. Tests mock command
execution and do not require a real browser.

`OpenClawWindowsResearcherRunner` is the broad autonomous customer-research lane.
It keeps a persistent Windows Chrome/CDP session, explores visible routes,
observes each screen, chooses customer/product actions over navigation/footer
links, records why each branch was chosen, suppresses repeated no-op actions,
captures before/after screenshots, and writes `research_brain.yaml`. Seeded
state lets this runner reach authenticated dashboards, records, settings, team
invites, billing, and saved state when the adapter can apply the fixture.

Full task-specific flows such as uploading a real file, completing checkout,
onboarding, inviting teammates, changing settings, or testing an authenticated
workspace can run through configured flow packs. Manual mode remains useful for
ingesting richer human/agent reports that include business interpretation
outside the browser.

`OpenClawWindowsInteractiveRunner` is the opt-in browser interaction lane. It
runs the baseline probe, then looks for a visible sample/demo action, clicks it,
captures before/after screenshots, and checks whether the click creates visible
result or download cues. It is a quick generic probe; project-specific work
belongs in `OpenClawWindowsFlowRunner` flow packs.

`OpenClawWindowsFlowRunner` is the task-specific lane. Pass
`--runner openclaw-windows-flow --flow path/to/customer_flow.yaml` to execute a
configured customer workflow pack after the baseline probe. The YAML is product
data, not Shopify/CSV code: one project can define a landing-page trial flow,
another can define checkout, onboarding, workspace setup, authenticated admin
tasks, or a 20-screen SaaS operator journey.

Supported flow actions include `navigate`, `fill`, `select`, `check`,
`uncheck`, `click`, `click_text`, `press`, `wait_ms`, `wait_for_text`,
`wait_for_text_absent`, `wait_for_selector`, `wait_for_selector_hidden`,
`wait_for_enabled`, `wait_for_url`, `assert_text`, `assert_no_text`,
`assert_selector`, `checkpoint`, and `upload`. Each step emits a stable
`STEP_PASS` or `STEP_FAIL` marker and captures a screenshot. A single file can
contain multiple flows, so TeamNoT can test happy paths, error/recovery paths,
navigation across screens, collaboration/sharing, checkout, settings, export, or
any other end-user job the project defines.

## Generated Brief Shape

The generated brief includes customer context, evidence references, the selected
finding, precise required behavior, non-goals, safety constraints, and
machine-verifiable DoD such as `pytest -q` and `ruff check .`. It keeps budget
defaults safe by disallowing metered workers unless a later brief explicitly
opts in.

## Readiness Classification

Final customer-loop validation should classify the result honestly:

- `production feature`: deterministic tests pass, browser dogfood succeeds on a
  representative app, seeded/domain/vision artifacts are complete for the target
  claim, and no customer-critical blocker remains.
- `beta/internal dogfood`: the core loop and artifacts work, but browser,
  seeded-state, vision, or multi-target coverage still needs more hardening.
- `blocked`: evidence collection, artifact honesty, or repeat-loop behavior is
  unreliable enough that TeamNoT should not claim autonomous customer research.
