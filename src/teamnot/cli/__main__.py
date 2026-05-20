"""TeamNoT v2 CLI entry point.

    teamnot doctor                                # check environment
    teamnot init                                  # scaffold .teamnot/brief.yaml
    teamnot validate --brief .teamnot/brief.yaml  # parse + validate, no execution
    teamnot review --brief .teamnot/brief.yaml    # knowledge gap audit
    teamnot dod --brief .teamnot/brief.yaml       # run DoD checks only
    teamnot run --brief .teamnot/brief.yaml       # autonomous run
    teamnot resume --brief .teamnot/brief.yaml    # resume from last checkpoint
    teamnot status --brief .teamnot/brief.yaml    # cost-guard snapshot
    teamnot logs --brief .teamnot/brief.yaml      # tail the agent transcript
    teamnot skills list                           # list registered skills
    teamnot skills show ARCHITECT                 # view a skill body
    teamnot workers                               # list workers + billing
    teamnot telegram --workspaces DIR             # Telegram gateway bot
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# Force UTF-8 on Windows consoles so rich can emit arrows/box characters.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in {"utf-8", "utf8"}:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, io.UnsupportedOperation):
        pass

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from teamnot import __version__
from teamnot.agents.spec import default_skills_dir, load_skills_from_dir
from teamnot.brief import (
    Brief,
    BriefValidationError,
    example_brief,
    load_brief,
    save_brief,
)
from teamnot.customer_loop import (
    CustomerLoopConfig,
    CustomerLoopError,
    CustomerLoopOrchestrator,
    CustomerLoopRunnerError,
    CustomerLoopRunnerName,
    CustomerProfile,
    CustomerSeverity,
    ExperienceTarget,
    ManualEvidenceRunner,
    OpenClawWindowsCDPRunner,
    default_customer_test_plan,
    load_model,
    write_report_artifacts,
)
from teamnot.dod import DoDEvaluator
from teamnot.engine import Worker
from teamnot.memory.knowledge_review import review_workspace
from teamnot.safety import CostGuard, all_workers
from teamnot.workspace import Workspace

console = Console(force_terminal=False, legacy_windows=False)


@click.group(help="TeamNoT — autonomous AI development workforce.")
@click.version_option(version=__version__, prog_name="teamnot")
def main() -> None:
    """Top-level group."""


# ── init ─────────────────────────────────────────────────────────────────────

@main.command(help="Scaffold a .teamnot/ directory in the target project.")
@click.option("--project", "project_path", type=click.Path(file_okay=False, path_type=Path),
              default=Path.cwd(), help="Target project root (defaults to cwd).")
@click.option("--force", is_flag=True, help="Overwrite existing brief.yaml.")
def init(project_path: Path, force: bool) -> None:
    project_path = project_path.expanduser().resolve()
    if not project_path.exists():
        console.print(f"[red]Project path does not exist:[/red] {project_path}")
        sys.exit(2)

    teamnot_dir = project_path / ".teamnot"
    teamnot_dir.mkdir(parents=True, exist_ok=True)

    brief_path = teamnot_dir / "brief.yaml"
    if brief_path.exists() and not force:
        console.print(f"[yellow]Brief already exists:[/yellow] {brief_path}")
        console.print("Use --force to overwrite.")
        sys.exit(1)

    brief = example_brief(project_path)
    save_brief(brief, brief_path)

    # Seed memory + conventions stubs if missing
    mem = teamnot_dir / "memory.md"
    if not mem.exists():
        mem.write_text(
            "# Project memory (managed by TeamNoT)\n\n"
            "> Patterns, gotchas, decisions accumulated across tasks. Read at the "
            "start of every task, append after every task.\n",
            encoding="utf-8",
        )
    conv = teamnot_dir / "conventions.md"
    if not conv.exists():
        conv.write_text(
            "# Project conventions\n\n"
            "> Fill in: language style, naming, framework patterns, test layout, "
            "branching model, security rules. Agents read this before coding.\n",
            encoding="utf-8",
        )

    console.print(Panel.fit(
        f"[green]Initialized[/green] {teamnot_dir}\n\n"
        f"  brief.yaml       — task contract\n"
        f"  memory.md        — auto-grown project memory\n"
        f"  conventions.md   — fill this in next\n\n"
        f"Next:\n"
        f"  1. Edit {brief_path} (task + DoD + allowed_metered_workers)\n"
        f"  2. teamnot validate --brief {brief_path}\n"
        f"  3. teamnot run --brief {brief_path}",
        title="TeamNoT init", border_style="green",
    ))


# ── validate ─────────────────────────────────────────────────────────────────

@main.command(help="Parse and validate a brief without executing it.")
@click.option("--brief", "brief_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def validate(brief_path: Path, as_json: bool) -> None:
    try:
        brief = load_brief(brief_path)
    except BriefValidationError as e:
        if as_json:
            click.echo(json.dumps({"ok": False, "error": str(e)}))
        else:
            console.print(f"[red]Brief invalid:[/red] {e}")
        sys.exit(1)

    if as_json:
        click.echo(json.dumps({
            "ok": True,
            "project": brief.project.name,
            "path": str(brief.project.path),
            "task": brief.task.id,
            "dod_checks": len(brief.definition_of_done.checks),
            "deliverable": brief.deliverable.type.value,
            "budget_usd": brief.budget.max_usd,
            "allowed_metered_workers": brief.budget.allowed_metered_workers,
        }))
        return

    _print_brief_summary(brief)


# ── dod ──────────────────────────────────────────────────────────────────────

@main.command(help="Run DoD checks against the current state of the project.")
@click.option("--brief", "brief_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True)
@click.option("--skip-judge", is_flag=True, help="Skip LLM judge checks (no API calls).")
def dod(brief_path: Path, skip_judge: bool) -> None:
    brief = load_brief(brief_path)
    if skip_judge:
        # Filter out judge checks from the DoD for this run
        brief.definition_of_done.checks = [
            c for c in brief.definition_of_done.checks if c.kind != "llm_judge"
        ]
        brief.definition_of_done.llm_judge_required = False

    evaluator = DoDEvaluator(brief)
    result = evaluator.evaluate()
    console.print(Panel.fit(result.to_md(), title="DoD Result",
                            border_style="green" if result.all_passed else "red"))
    sys.exit(0 if result.all_passed else 1)


# ── status / workers ─────────────────────────────────────────────────────────

@main.command(help="Show registered workers and their billing model.")
def workers() -> None:
    tbl = Table(title="Registered workers")
    tbl.add_column("Name")
    tbl.add_column("Billing")
    tbl.add_column("Notes")
    for w in sorted(all_workers(), key=lambda t: (t.billing.value, t.name)):
        color = {
            "metered": "yellow",
            "subscription": "green",
            "local": "cyan",
        }.get(w.billing.value, "white")
        tbl.add_row(w.name, f"[{color}]{w.billing.value}[/{color}]", w.notes)
    console.print(tbl)


@main.command(help="Show cost-guard snapshot for a brief.")
@click.option("--brief", "brief_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True)
def status(brief_path: Path) -> None:
    brief = load_brief(brief_path)
    guard = CostGuard.from_brief(brief)
    console.print(json.dumps(guard.status(), indent=2))


# ── review ───────────────────────────────────────────────────────────────────

@main.command(help="Audit the project context for missing knowledge before a run.")
@click.option("--brief", "brief_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def review(brief_path: Path, as_json: bool) -> None:
    brief = load_brief(brief_path)
    ws = Workspace(brief)
    ws.ensure()
    rev = review_workspace(brief, ws)
    if as_json:
        click.echo(json.dumps({
            "can_proceed": rev.can_proceed,
            "summary": rev.summary(),
            "gaps": [
                {"code": g.code, "severity": g.severity.value, "title": g.title,
                 "detail": g.detail, "suggestion": g.suggestion}
                for g in rev.gaps
            ],
        }, indent=2))
    else:
        color = "green" if rev.can_proceed else "red"
        console.print(Panel.fit(rev.to_md(), title=rev.summary(), border_style=color))
    sys.exit(0 if rev.can_proceed else 1)


# ── run ──────────────────────────────────────────────────────────────────────

@main.command(help="Autonomous run — knowledge review -> plan -> pipeline -> DoD -> handover.")
@click.option("--brief", "brief_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True)
@click.option("--skip-review", is_flag=True, help="Bypass the knowledge gap blocker check.")
@click.option("--no-plan", is_flag=True, help="Skip dual planning (just run DoD).")
@click.option("--no-pipeline", is_flag=True, help="Skip the multi-agent pipeline (just plan + DoD).")
@click.option("--no-deliver", is_flag=True, help="Skip the deliverable handover (no git branch / push).")
@click.option("--skills", "skills_dir", type=click.Path(exists=False, file_okay=False, path_type=Path),
              default=None, help="Override the skills directory.")
@click.option("--wait-lock", type=float, default=0.0,
              help="Seconds to wait if another worker holds the workspace lock.")
def run(brief_path: Path, skip_review: bool, no_plan: bool, no_pipeline: bool,
        no_deliver: bool, skills_dir: Path | None, wait_lock: float) -> None:
    brief = load_brief(brief_path)
    _print_brief_summary(brief)
    worker = Worker(brief)
    result = worker.run_until_done(
        skip_review=skip_review,
        plan=not no_plan,
        run_pipeline=not no_pipeline,
        skills_dir=skills_dir,
        deliver=not no_deliver,
        wait_for_lock_s=wait_lock,
    )
    style = {
        "dod_passed": "green",
        "blocked_knowledge": "red",
        "blocked_budget": "red",
        "blocked_retries": "red",
        "plan_failed": "red",
        "error": "red",
        "pipeline_skipped": "yellow",
        "dod_failed": "yellow",
    }.get(result.status.value, "white")
    body_lines = [
        f"[bold]status:[/bold] [{style}]{result.status.value}[/{style}]",
        f"[bold]summary:[/bold] {result.summary}",
    ]
    if result.report_path:
        body_lines.append(f"[bold]report:[/bold] {result.report_path}")
    if result.knowledge:
        body_lines.append(f"[bold]knowledge:[/bold] {result.knowledge.summary()}")
    if result.plan:
        body_lines.append(f"[bold]plan:[/bold] {result.plan.short_summary()}")
    if result.pipeline:
        body_lines.append(
            f"[bold]pipeline:[/bold] {result.pipeline.outcome.value} "
            f"({result.pipeline.iterations} iter, {len(result.pipeline.turns)} turns)"
        )
    if result.handover:
        h = result.handover
        body_lines.append(
            f"[bold]handover:[/bold] {h.deliverable_type} — "
            f"{'OK' if h.ok else 'FAIL'} — {h.summary}"
        )
    if result.cost:
        body_lines.append(
            f"[bold]cost:[/bold] ${result.cost['spent_usd']:.4f} / "
            f"${result.cost['budget']['max_usd']} "
            f"({result.cost['metered_calls']} metered calls)"
        )
    if result.error:
        body_lines.append(f"[bold red]error:[/bold red] {result.error}")
    console.print(Panel.fit("\n".join(body_lines), title="Worker result", border_style=style))
    sys.exit(0 if result.status.value == "dod_passed" else 1)


# ── helpers ──────────────────────────────────────────────────────────────────

def _print_brief_summary(brief: Brief) -> None:
    body = (
        f"[bold]Project[/bold]   {brief.project.name}   ({brief.project.path})\n"
        f"[bold]Stack[/bold]     {', '.join(brief.project.stack) or '—'}   "
        f"langs: {', '.join(brief.project.language) or '—'}\n"
        f"[bold]Task[/bold]      {brief.task.id} — {brief.task.title}\n"
        f"[bold]DoD[/bold]       {len(brief.definition_of_done.checks)} checks "
        f"(machine={len(brief.definition_of_done.machine_checks())}, "
        f"judge={len(brief.definition_of_done.judge_checks())})\n"
        f"[bold]Deliverable[/bold] {brief.deliverable.type.value} -> "
        f"{brief.deliverable.report_to.value}\n"
        f"[bold]Budget[/bold]   ${brief.budget.max_usd} / {brief.budget.max_minutes} min / "
        f"retries={brief.budget.max_retries}\n"
        f"[bold]Metered allow-list[/bold] {brief.budget.allowed_metered_workers or '[red]NONE[/red] (subscription/local only)'}"
    )
    console.print(Panel.fit(body, title="Brief OK", border_style="green"))


# ── customer loop ────────────────────────────────────────────────────────────

RUNNER_CHOICES = [runner.value for runner in CustomerLoopRunnerName]
SEVERITY_CHOICES = ["critical", "high", "medium"]


@main.command("customer-test", help="Run or ingest a customer test into customer-loop artifacts.")
@click.option("--target", required=True, help="Target product URL.")
@click.option("--profile", "profile_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True, help="Customer profile YAML.")
@click.option("--out", "out_dir", type=click.Path(file_okay=False, path_type=Path),
              required=True, help="Output artifact directory.")
@click.option("--runner", type=click.Choice(RUNNER_CHOICES), default=CustomerLoopRunnerName.manual.value,
              show_default=True)
@click.option("--evidence", "evidence_path", type=click.Path(exists=False, dir_okay=False, path_type=Path),
              default=None, help="Existing evidence file for manual mode.")
def customer_test(
    target: str,
    profile_path: Path,
    out_dir: Path,
    runner: str,
    evidence_path: Path | None,
) -> None:
    try:
        profile = load_model(profile_path, CustomerProfile)
        target_model = ExperienceTarget(url=target)
        config = CustomerLoopConfig(
            target=target_model,
            profile=profile,
            out_dir=out_dir,
            runner=CustomerLoopRunnerName(runner),
            evidence_path=evidence_path,
        )
        plan = default_customer_test_plan(config)
        if CustomerLoopRunnerName(runner) == CustomerLoopRunnerName.manual and evidence_path is None:
            raise CustomerLoopRunnerError("--evidence is required for manual customer-test mode")
        experience_runner = (
            ManualEvidenceRunner(evidence_path)
            if CustomerLoopRunnerName(runner) == CustomerLoopRunnerName.manual
            else OpenClawWindowsCDPRunner()
        )
        report = experience_runner.run(target_model, profile, plan, out_dir)
        write_report_artifacts(out_dir, profile, plan, report)
    except CustomerLoopError as e:
        console.print(f"[red]Customer test failed:[/red] {e}")
        sys.exit(1)
    console.print(f"[green]Customer test artifacts written:[/green] {out_dir}")


@main.command("customer-loop", help="Generate a customer-centered report and next TeamNoT brief.")
@click.option("--target", required=True, help="Target product URL.")
@click.option("--profile", "profile_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True, help="Customer profile YAML.")
@click.option("--out", "out_dir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="Output artifact directory.")
@click.option("--max-iterations", type=int, default=1, show_default=True)
@click.option("--severity-threshold", type=click.Choice(SEVERITY_CHOICES),
              default=CustomerSeverity.high.value, show_default=True)
@click.option("--run-teamnot/--no-run-teamnot", default=False, show_default=True)
@click.option("--runner", type=click.Choice(RUNNER_CHOICES), default=CustomerLoopRunnerName.manual.value,
              show_default=True)
@click.option("--evidence", "evidence_path", type=click.Path(exists=False, dir_okay=False, path_type=Path),
              default=None, help="Existing evidence file for manual mode.")
@click.option("--previous-brief", "previous_brief_path",
              type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="Optional previous TeamNoT brief for project metadata.")
def customer_loop(
    target: str,
    profile_path: Path,
    out_dir: Path | None,
    max_iterations: int,
    severity_threshold: str,
    run_teamnot: bool,
    runner: str,
    evidence_path: Path | None,
    previous_brief_path: Path | None,
) -> None:
    out = out_dir or Path(".teamnot") / "customer-loop" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    try:
        profile = load_model(profile_path, CustomerProfile)
        config = CustomerLoopConfig(
            target=ExperienceTarget(url=target),
            profile=profile,
            out_dir=out,
            max_iterations=max_iterations,
            severity_threshold=CustomerSeverity(severity_threshold),
            run_teamnot=run_teamnot,
            runner=CustomerLoopRunnerName(runner),
            evidence_path=evidence_path,
            previous_brief_path=previous_brief_path,
        )
        orchestrator = CustomerLoopOrchestrator(
            run_teamnot_hook=_invoke_teamnot_run if run_teamnot else None
        )
        result = orchestrator.run(config)
    except CustomerLoopError as e:
        console.print(f"[red]Customer loop failed:[/red] {e}")
        sys.exit(1)
    if result.generated_brief:
        console.print(f"[green]Generated brief:[/green] {out / 'generated_brief.yaml'}")
    else:
        console.print("[yellow]No follow-up brief generated; no blocker met the threshold.[/yellow]")
    console.print(f"[green]Customer loop artifacts written:[/green] {out}")


def _invoke_teamnot_run(brief_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "teamnot.cli", "run", "--brief", str(brief_path)],
        check=True,
    )


# ── doctor ───────────────────────────────────────────────────────────────────

@main.command(help="Check the local environment for everything TeamNoT needs.")
def doctor() -> None:
    import platform
    import shutil
    import subprocess

    checks: list[tuple[str, bool, str]] = []

    # Python
    py_ok = sys.version_info >= (3, 11)
    checks.append((
        "python >= 3.11",
        py_ok,
        f"{platform.python_version()} on {platform.system()} {platform.machine()}",
    ))

    # Dependencies (importable?)
    for mod in ("pydantic", "yaml", "click", "rich", "httpx", "litellm", "crewai"):
        try:
            __import__(mod)
            checks.append((f"import {mod}", True, "ok"))
        except ImportError as e:
            checks.append((f"import {mod}", False, str(e)))

    # Optional deps
    for mod, extra in [("aiogram", "[telegram]"), ("fastapi", "[http]")]:
        try:
            __import__(mod)
            checks.append((f"{mod} (optional)", True, f"installed — extra {extra}"))
        except ImportError:
            checks.append((f"{mod} (optional)", True, f"not installed (pip install teamnot{extra})"))

    # External tools
    git = shutil.which("git")
    checks.append(("git in PATH", git is not None, git or "not found"))

    try:
        rc = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
        if rc.returncode == 0:
            checks.append(("git --version", True, rc.stdout.strip()))
    except Exception:
        pass

    try:
        from teamnot.workers.claude_cli import find_claude_cli
        path = find_claude_cli()
        checks.append(("claude CLI", True, path))
    except Exception:
        checks.append((
            "claude CLI",
            True,
            "not found (only required for skills using worker: claude_cli)",
        ))

    try:
        from teamnot.workers.codex_cli import find_codex_cli
        path = find_codex_cli()
        checks.append(("codex CLI", True, path))
    except Exception:
        checks.append((
            "codex CLI",
            False,
            "not found — install/login with Codex CLI, then run `codex doctor`",
        ))

    gh = shutil.which("gh")
    checks.append((
        "gh CLI (optional, for PR handover)",
        True,  # optional — always OK; status text describes
        gh or "not installed (PR handover disabled)",
    ))

    # Env vars
    import os
    for var in ("MINIMAX_API_KEY", "TELEGRAM_BOT_TOKEN", "TEAMNOT_SKILLS_DIR"):
        v = os.environ.get(var)
        if v:
            masked = v[:4] + "…" + v[-4:] if len(v) > 12 else "***"
            checks.append((f"${var}", True, f"set ({masked})"))
        else:
            checks.append((f"${var}", True, "(not set — optional)"))

    # Skills bundled
    try:
        sd = default_skills_dir()
        reg = load_skills_from_dir(sd)
        checks.append((
            "bundled skills",
            bool(reg),
            f"{len(reg)} skills at {sd}" if reg else f"no SKILL.md found under {sd}",
        ))
    except Exception as e:
        checks.append(("bundled skills", False, str(e)))

    # Render
    tbl = Table(title="teamnot doctor", show_header=True)
    tbl.add_column("Check")
    tbl.add_column("Status", justify="center")
    tbl.add_column("Detail")
    any_fail = False
    for name, ok, detail in checks:
        sym = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
        if not ok:
            any_fail = True
        tbl.add_row(name, sym, detail)
    console.print(tbl)
    if any_fail:
        console.print("[yellow]Fix the failing checks before running `teamnot run`.[/yellow]")
        sys.exit(1)
    console.print("[green]All required checks passed.[/green]")


# ── skills ───────────────────────────────────────────────────────────────────

@main.group(help="Inspect and manage skill files (the .md role definitions).")
def skills() -> None:
    """Skills command group."""


@skills.command("list", help="List every registered skill from the active skills dir.")
@click.option("--dir", "skills_dir", type=click.Path(exists=False, file_okay=False, path_type=Path),
              default=None, help="Override the skills directory.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def skills_list(skills_dir: Path | None, as_json: bool) -> None:
    sd = skills_dir or default_skills_dir()
    reg = load_skills_from_dir(sd) if sd.exists() else None
    if not reg:
        console.print(f"[yellow]No skills found at {sd}.[/yellow]")
        sys.exit(1)
    if as_json:
        click.echo(json.dumps({
            "source": str(sd),
            "skills": [
                {"name": s.name, "role": s.role, "worker": s.worker,
                 "talks_to": s.talks_to, "handoff_to": s.handoff_to,
                 "metered_ok": s.metered_ok, "path": s.source_path}
                for s in reg.specs.values()
            ],
        }, indent=2))
        return
    tbl = Table(title=f"Skills @ {sd}")
    tbl.add_column("name")
    tbl.add_column("role")
    tbl.add_column("worker")
    tbl.add_column("handoff →")
    tbl.add_column("metered ok")
    for s in reg.specs.values():
        tbl.add_row(s.name, s.role, s.worker, s.handoff_to or "—",
                    "yes" if s.metered_ok else "no")
    console.print(tbl)


@skills.command("show", help="Print a skill's SKILL.md body (the system prompt).")
@click.argument("name")
@click.option("--dir", "skills_dir", type=click.Path(exists=False, file_okay=False, path_type=Path),
              default=None)
def skills_show(name: str, skills_dir: Path | None) -> None:
    sd = skills_dir or default_skills_dir()
    reg = load_skills_from_dir(sd)
    try:
        spec = reg.get(name.lower())
    except KeyError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)
    header = (
        f"[bold]name:[/bold] {spec.name}   "
        f"[bold]role:[/bold] {spec.role}   "
        f"[bold]worker:[/bold] {spec.worker}\n"
        f"[bold]description:[/bold] {spec.description}\n"
        f"[bold]talks_to:[/bold] {spec.talks_to}   "
        f"[bold]handoff_to:[/bold] {spec.handoff_to or '—'}   "
        f"[bold]metered_ok:[/bold] {spec.metered_ok}\n"
        f"[bold]source:[/bold] {spec.source_path}"
    )
    console.print(Panel.fit(header, title=f"skill: {spec.name}", border_style="cyan"))
    console.print(spec.system_prompt)


@skills.command("path", help="Print the active skills directory.")
def skills_path() -> None:
    click.echo(str(default_skills_dir()))


# ── resume ───────────────────────────────────────────────────────────────────

@main.command(help="Inspect the latest checkpoint for a brief (resumability stub).")
@click.option("--brief", "brief_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True)
def resume(brief_path: Path) -> None:
    brief = load_brief(brief_path)
    ws = Workspace(brief)
    rec = ws.latest_checkpoint(brief.task.id)
    if not rec:
        console.print(f"[yellow]No checkpoints for {brief.task.id}. Start with `teamnot run`.[/yellow]")
        sys.exit(1)
    body = (
        f"[bold]task:[/bold] {rec.task_id}\n"
        f"[bold]phase:[/bold] {rec.phase}\n"
        f"[bold]status:[/bold] {rec.status}\n"
        f"[bold]saved_at:[/bold] {rec.saved_at}\n\n"
        f"[bold]payload:[/bold]\n{json.dumps(rec.payload, indent=2, ensure_ascii=False)}"
    )
    console.print(Panel.fit(body, title="Latest checkpoint", border_style="cyan"))
    console.print(
        "[dim]Full resume-from-checkpoint logic will land in v2.1. For now, "
        "`teamnot run` always re-runs the pipeline; existing artifacts in "
        "`.teamnot/` are preserved.[/dim]"
    )


# ── logs ─────────────────────────────────────────────────────────────────────

@main.command(help="Tail the latest agent transcript and cost-ledger for a brief.")
@click.option("--brief", "brief_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True)
@click.option("--transcript", is_flag=True, help="Show the multi-agent message transcript.")
@click.option("--ledger", is_flag=True, help="Show the cost ledger.")
@click.option("--lines", "n_lines", type=int, default=40, help="Tail this many lines.")
def logs(brief_path: Path, transcript: bool, ledger: bool, n_lines: int) -> None:
    brief = load_brief(brief_path)
    ws = Workspace(brief)
    if not (transcript or ledger):
        transcript = ledger = True

    if transcript:
        files = sorted(ws.logs_dir.glob(f"{brief.task.id}__transcript.md"))
        if files:
            body = files[-1].read_text(encoding="utf-8")
            tail = "\n".join(body.splitlines()[-n_lines:])
            console.print(Panel.fit(tail, title=files[-1].name, border_style="cyan"))
        msg_log = ws.logs_dir / "messages.jsonl"
        if msg_log.exists():
            with msg_log.open(encoding="utf-8") as f:
                rows = f.readlines()
            tail = "".join(rows[-n_lines:])
            console.print(Panel.fit(tail or "(empty)", title="messages.jsonl",
                                    border_style="cyan"))

    if ledger:
        ledger_path = ws.logs_dir / "cost_ledger.jsonl"
        if not ledger_path.exists():
            console.print("[dim]No cost ledger yet.[/dim]")
        else:
            with ledger_path.open(encoding="utf-8") as f:
                rows = f.readlines()
            tail = "".join(rows[-n_lines:])
            console.print(Panel.fit(tail or "(empty)", title="cost_ledger.jsonl",
                                    border_style="cyan"))


# ── telegram ─────────────────────────────────────────────────────────────────

@main.command(help="Run the Telegram gateway bot (blocks). Requires `pip install teamnot[telegram]`.")
@click.option("--token", default=None, help="Bot token; falls back to TELEGRAM_BOT_TOKEN env var.")
@click.option("--workspaces", "workspaces_root",
              type=click.Path(exists=True, file_okay=False, path_type=Path), required=True,
              help="Root directory containing one sub-dir per project (each with .teamnot/brief.yaml).")
@click.option("--allow-chat", "allowed", multiple=True, type=int,
              help="Restrict to these chat IDs (may be passed multiple times).")
def telegram(token: str | None, workspaces_root: Path, allowed: tuple[int, ...]) -> None:
    from teamnot.gateways.telegram import run_blocking
    console.print(f"[green]Telegram gateway starting — workspaces: {workspaces_root}[/green]")
    try:
        run_blocking(
            token=token,
            workspaces_root=workspaces_root,
            allowed_chat_ids=list(allowed) or None,
        )
    except KeyboardInterrupt:
        console.print("[yellow]Gateway stopped.[/yellow]")


if __name__ == "__main__":
    main()
