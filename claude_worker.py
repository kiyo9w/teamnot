"""
TeamNoT — Claude Code CLI Worker
Spawn claude CLI subprocess để thực thi coding tasks.
Dùng cho Architect (design) và Reviewer (review) trong Phase 3.

Claude Code CLI chạy trong thư mục project, có full context từ CLAUDE.md.
Không cần ANTHROPIC_API_KEY — Claude Code tự xác thực.
"""
import os
import subprocess
import logging
import tempfile
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("TeamNoT.ClaudeWorker")
ROOT = Path(os.getenv("TEAMNOT_ROOT",
            r"C:\Users\Jenky - MiniPC\Desktop\Project\TeamNoT"))


def _find_claude_cli() -> str:
    """Tìm claude executable trên Windows."""
    candidates = [
        "claude",
        str(Path.home() / ".local" / "bin" / "claude"),
        str(Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"),
        r"C:\Program Files\Claude\claude.exe",
    ]
    for c in candidates:
        try:
            result = subprocess.run(
                [c, "--version"],
                capture_output=True, text=True, timeout=10,
                shell=(c.endswith(".cmd")),
            )
            if result.returncode == 0:
                logger.info(f"Found claude CLI: {c} ({result.stdout.strip()})")
                return c
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    raise RuntimeError(
        "claude CLI not found. Install: npm install -g @anthropic-ai/claude-code"
    )


CLAUDE_CLI = None  # lazy init


def _get_claude_cli() -> str:
    global CLAUDE_CLI
    if CLAUDE_CLI is None:
        CLAUDE_CLI = _find_claude_cli()
    return CLAUDE_CLI


def run_claude_task(
    prompt: str,
    working_dir: Path = None,
    context_files: list[str] = None,
    model: str = "sonnet",
    max_budget_usd: float = 3.0,
    timeout: int = 300,
    allowed_tools: list[str] = None,
) -> str:
    """
    Chạy một task qua Claude Code CLI.

    Args:
        prompt: Yêu cầu cụ thể cho Claude
        working_dir: Thư mục để Claude làm việc (mặc định: TeamNoT root)
        context_files: Danh sách file cần đọc trước (inject vào prompt)
        model: Model alias (sonnet, opus, haiku)
        max_budget_usd: Budget tối đa cho lần gọi này
        timeout: Timeout tính bằng giây
        allowed_tools: Tools được phép (mặc định: Read, Write, Edit, Bash, Glob, Grep)

    Returns:
        Output text từ Claude
    """
    cli = _get_claude_cli()
    workdir = working_dir or ROOT

    # Build full prompt với context files nếu có
    full_prompt = prompt
    if context_files:
        ctx_parts = []
        for f in context_files:
            p = Path(f) if Path(f).is_absolute() else ROOT / f
            if p.exists():
                content = p.read_text(encoding="utf-8")
                # Truncate nếu quá dài (giữ 4000 chars đầu)
                if len(content) > 4000:
                    content = content[:4000] + "\n\n... (truncated)"
                ctx_parts.append(f"=== {p.name} ===\n{content}")
        if ctx_parts:
            full_prompt = (
                "Context files:\n\n"
                + "\n\n".join(ctx_parts)
                + "\n\n---\n\n"
                + prompt
            )

    if allowed_tools is None:
        allowed_tools = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

    cmd = [
        cli,
        "-p",                                    # non-interactive, print output
        "--model", model,
        "--max-budget-usd", str(max_budget_usd),
        "--no-session-persistence",              # don't clutter session storage
        "--permission-mode", "acceptEdits",      # auto-accept file edits
        "--output-format", "text",
        "--allowedTools", ",".join(allowed_tools),
    ]

    # Claude Code CLI dùng OAuth (claude.ai), không dùng ANTHROPIC_API_KEY.
    # Nếu .env có ANTHROPIC_API_KEY (placeholder hoặc bất kỳ), CLI sẽ ưu tiên
    # dùng nó thay vì OAuth → lỗi auth. Luôn strip để force OAuth.
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    logger.info(
        f"Spawning claude CLI in {workdir} "
        f"(model={model}, budget=${max_budget_usd}, timeout={timeout}s)"
    )

    try:
        result = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(workdir),
            timeout=timeout,
            env=env,
        )

        if result.returncode != 0:
            stderr = result.stderr[:500] if result.stderr else ""
            logger.error(f"Claude CLI error (rc={result.returncode}): {stderr}")
            return (
                f"[ClaudeWorker ERROR rc={result.returncode}]\n{stderr}\n\n"
                f"Partial output:\n{result.stdout[:1000]}"
            )

        output = result.stdout.strip()
        logger.info(f"Claude CLI done, output: {len(output)} chars")
        return output

    except subprocess.TimeoutExpired:
        logger.error(f"Claude CLI timeout after {timeout}s")
        return f"[ClaudeWorker TIMEOUT after {timeout}s]"


def architect_design(
    task_id: str,
    task_description: str,
    project_dir: Path = None,
) -> str:
    """
    Chạy Architect Agent qua Claude Code CLI.
    Output: ADR document đầy đủ.
    """
    logger.info(f"[{task_id}] Architect designing...")

    prompt = f"""You are the Architect Agent for TeamNoT.

Task ID: {task_id}
Task: {task_description}

Read AGENT_MEMORY.md and PROJECT_CONTEXT.md first, then:

1. Design the solution architecture
2. Create an ADR (Architecture Decision Record) with:
   - Context: what problem we're solving
   - Decision: chosen approach
   - Alternatives considered (at least 2) with reasons rejected
   - Consequences: positive and tradeoffs
   - Implementation notes: exact file structure, key patterns
   - Dependencies: packages to install
   - API contracts (if creating endpoints)

3. Save the ADR to: ADRs/{task_id}.md

Format the ADR following the template in PROJECT_CONTEXT.md.
Be specific about file paths, function names, and interfaces.
Do NOT write implementation code — only the design.

After saving, output: "ADR SAVED: ADRs/{task_id}.md"
"""

    return run_claude_task(
        prompt=prompt,
        working_dir=project_dir or ROOT,
        context_files=["AGENT_MEMORY.md", "PROJECT_CONTEXT.md"],
        model="sonnet",
        max_budget_usd=2.0,
        timeout=180,
    )


def reviewer_review(
    task_id: str,
    branch_name: str,
    project_dir: Path = None,
) -> dict:
    """
    Chạy Reviewer Agent qua Claude Code CLI.
    Returns: {"verdict": "APPROVE"|"REJECT", "report": str, "issues": list}
    """
    logger.info(f"[{task_id}] Reviewer reviewing branch {branch_name}...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    prompt = f"""You are the Reviewer Agent for TeamNoT.

Task ID: {task_id}
Branch to review: {branch_name}

Read the ADR at ADRs/{task_id}.md first, then review all changed files.

Use this EXACT checklist:

SECURITY:
- [ ] No hardcoded secrets
- [ ] Input validation present (Pydantic/Zod)
- [ ] SQL injection protection (ORM only, no raw SQL)
- [ ] Auth on protected routes

CODE QUALITY:
- [ ] Logic is clear and readable
- [ ] No significant code duplication
- [ ] Error handling is complete
- [ ] Docstrings/comments sufficient

CONVENTIONS (from AGENT_MEMORY.md):
- [ ] Correct patterns used
- [ ] Naming is consistent
- [ ] File structure matches project standard

EDGE CASES:
- [ ] Null/empty input handled
- [ ] Network timeout handled

Output your review in this EXACT format:

## Review: {task_id}
Date: {now}
### Verdict: APPROVE
(or)
### Verdict: REJECT

### Checklist
[your checklist with ticks]

### Issues (if REJECT)
- [SEVERITY] file:line — description + how to fix

### Summary
[2-3 sentences]

Save report to PROJECT_DOCS/QA_REPORTS/{task_id}_review.md
"""

    output = run_claude_task(
        prompt=prompt,
        working_dir=project_dir or ROOT,
        context_files=["AGENT_MEMORY.md"],
        model="sonnet",
        max_budget_usd=2.0,
        timeout=240,
    )

    # Parse verdict
    verdict = "REJECT"  # default safe
    if "### Verdict: APPROVE" in output:
        verdict = "APPROVE"

    # Parse issues
    issues = []
    in_issues = False
    for line in output.split("\n"):
        if "### Issues" in line:
            in_issues = True
            continue
        if line.startswith("### ") and in_issues:
            in_issues = False
        if in_issues and line.strip().startswith("- "):
            issues.append(line.strip()[2:])

    return {
        "verdict": verdict,
        "report": output,
        "issues": issues,
        "task_id": task_id,
        "branch": branch_name,
        "reviewed_at": datetime.now().isoformat(),
    }
