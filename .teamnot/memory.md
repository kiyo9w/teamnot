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
