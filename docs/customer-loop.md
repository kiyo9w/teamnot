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
  --evidence .teamnot/customer-testing/report.md \
  --runner manual \
  --out .teamnot/customer-loop/run-001 \
  --severity-threshold high \
  --no-run-teamnot
```

`--no-run-teamnot` is the safe default. Passing `--run-teamnot` explicitly lets
the loop invoke `teamnot run --brief generated_brief.yaml`.

## Artifacts

Customer Loop writes project-local artifacts under the requested `--out`
directory, commonly `.teamnot/customer-loop/<run-id>/`:

- `customer_profile.yaml`
- `customer_test_plan.yaml`
- `customer_report.md`
- `customer_report.json`
- `generated_brief.yaml`
- `loop_summary.md`
- `screenshots/`

When evaluating another project, put `--out` under that project so browser
evidence and customer reports do not land in TeamNoT's own source tree.

## Adapter Design

Core TeamNoT imports do not depend on OpenClaw, Windows, Chrome, Playwright, or
CDP. The `ExperienceRunner` protocol isolates evidence collection. The baseline
`ManualEvidenceRunner` consumes an existing report file and normalizes it into
customer-loop artifacts. The optional `OpenClawWindowsCDPRunner` shells out to
`scripts/winbrowser` when present and reports a readable error when absent.
Tests mock command execution and do not require a real browser.

## Generated Brief Shape

The generated brief includes customer context, evidence references, the selected
finding, precise required behavior, non-goals, safety constraints, and
machine-verifiable DoD such as `pytest -q` and `ruff check .`. It keeps budget
defaults safe by disallowing metered workers unless a later brief explicitly
opts in.
