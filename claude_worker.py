"""
TeamNoT — Claude Code CLI Worker (Phase 3)
Spawn claude CLI subprocess dùng OAuth (không cần ANTHROPIC_API_KEY).
Quản lý session window 5h, tự cảnh báo khi gần hết.
"""
import os
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from session_manager import get_manager

logger = logging.getLogger("TeamNoT.ClaudeWorker")
ROOT = Path(os.getenv("TEAMNOT_ROOT",
            r"C:\Users\Jenky - MiniPC\Desktop\Project\TeamNoT"))
MANAGER = None  # lazy init


def _mgr():
    global MANAGER
    if MANAGER is None:
        MANAGER = get_manager()
    return MANAGER


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
            r = subprocess.run(
                [c, "--version"],
                capture_output=True, text=True, timeout=10,
                shell=(c.endswith(".cmd")),
            )
            if r.returncode == 0:
                logger.info(f"Found claude: {c} — {r.stdout.strip()}")
                return c
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    raise RuntimeError(
        "claude CLI not found.\n"
        "Install: npm install -g @anthropic-ai/claude-code\n"
        "Then run: claude login"
    )


_CLAUDE_CLI = None


def _get_cli() -> str:
    global _CLAUDE_CLI
    if _CLAUDE_CLI is None:
        _CLAUDE_CLI = _find_claude_cli()
    return _CLAUDE_CLI


def _check_session_before_call() -> bool:
    """
    Kiểm tra session window trước khi gọi Claude.
    Returns False nếu nên tạm dừng (còn < 10 phút).
    """
    mgr = _mgr()
    info = mgr.check_window("claude")

    if info["should_pause"]:
        logger.warning(
            f"Claude session chỉ còn {info['remaining_minutes']}m — "
            f"tạm dừng để tránh bị cắt giữa chừng. "
            f"Session mới lúc {info['expires_at']}."
        )
        return False

    if info["warn"]:
        logger.warning(
            f"Claude session còn {info['remaining_minutes']}m "
            f"(hết lúc {info['expires_at']})"
        )
    return True


def run_claude_task(
    prompt: str,
    working_dir: Path = None,
    context_files: list[str] = None,
    model: str = "sonnet",
    max_turns: int = 10,
    timeout: int = 300,
    allowed_tools: list[str] = None,
) -> str:
    """
    Chạy task qua Claude Code CLI.
    Session-based — không track token cost.
    """
    # Kiểm tra session trước khi gọi
    if not _check_session_before_call():
        avail = _mgr().get_next_available_claude()
        wait = avail.get("wait_minutes", 0)
        at = avail.get("available_at", "unknown")
        return (
            f"[SESSION_WINDOW_LOW] Claude session gần hết "
            f"(còn < 10 phút). "
            f"Session mới sẵn sàng lúc {at} (~{wait:.0f} phút nữa). "
            f"Task được lưu vào queue, sẽ tự chạy lại sau khi refresh."
        )

    cli = _get_cli()
    workdir = working_dir or ROOT

    # Inject context files vào prompt
    full_prompt = prompt
    if context_files:
        ctx_parts = []
        for f in context_files:
            p = Path(f) if Path(f).is_absolute() else ROOT / f
            if p.exists():
                content = p.read_text(encoding="utf-8")
                if len(content) > 4000:
                    content = content[:4000] + "\n\n... (truncated)"
                ctx_parts.append(f"=== {p.name} ===\n{content}")
        if ctx_parts:
            full_prompt = (
                "=== CONTEXT (read before acting) ===\n"
                + "\n\n".join(ctx_parts)
                + "\n\n=== TASK ===\n"
                + prompt
            )

    if allowed_tools is None:
        allowed_tools = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

    # Claude Code CLI dùng OAuth (claude.ai), không dùng ANTHROPIC_API_KEY.
    # Luôn strip API key khỏi subprocess env để force OAuth.
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    cmd = [
        cli,
        "-p",                                    # non-interactive print mode
        "--model", model,
        "--no-session-persistence",              # don't clutter session storage
        "--permission-mode", "acceptEdits",      # auto-accept file edits
        "--output-format", "text",
        "--allowedTools", ",".join(allowed_tools),
    ]

    logger.info(f"Claude CLI call | dir={workdir} | timeout={timeout}s")

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
        _mgr().record_call("claude")

        if result.returncode != 0:
            stderr = (result.stderr or "")[:500]
            logger.error(f"Claude CLI rc={result.returncode}: {stderr}")
            return (
                f"[CLAUDE_ERROR rc={result.returncode}]\n"
                f"{stderr}\n\nPartial output:\n{result.stdout[:500]}"
            )

        output = result.stdout.strip()
        logger.info(f"Claude CLI OK — {len(output)} chars output")
        return output

    except subprocess.TimeoutExpired:
        _mgr().record_call("claude")  # still counts as a call
        logger.error(f"Claude CLI timeout {timeout}s")
        return f"[CLAUDE_TIMEOUT after {timeout}s] Task too complex — consider splitting"


def architect_design(
    task_id: str,
    task_description: str,
    project_dir: Path = None,
) -> str:
    """Architect Agent qua Claude Code CLI."""
    logger.info(f"[{task_id}] Architect designing...")

    prompt = f"""You are the Architect Agent for TeamNoT.

Task ID: {task_id}
Task: {task_description}

1. Read AGENT_MEMORY.md for project conventions
2. Design the solution and create ADR at ADRs/{task_id}.md

ADR must include:
- Context, Decision, Alternatives considered (2+), Consequences
- Implementation notes: exact file paths, function signatures
- Dependencies: exact pip/npm package names
- API contracts if creating endpoints

Save ADR to ADRs/{task_id}.md then output: "ADR SAVED: ADRs/{task_id}.md"
Do NOT write implementation code.
"""
    return run_claude_task(
        prompt=prompt,
        working_dir=project_dir or ROOT,
        context_files=["AGENT_MEMORY.md", "PROJECT_CONTEXT.md"],
        max_turns=8,
        timeout=180,
    )


def reviewer_review(
    task_id: str,
    branch_name: str,
    project_dir: Path = None,
) -> dict:
    """Reviewer Agent qua Claude Code CLI."""
    logger.info(f"[{task_id}] Reviewer checking {branch_name}...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    prompt = f"""You are the Reviewer Agent for TeamNoT.
Task ID: {task_id} | Branch: {branch_name}

Read ADRs/{task_id}.md then review all changed files.

Checklist (mark each):
SECURITY: [ ] No hardcoded secrets [ ] Input validation [ ] SQL injection safe [ ] Auth correct
QUALITY:  [ ] Logic clear [ ] No duplication [ ] Error handling [ ] Docstrings
CONVENTIONS: [ ] Matches AGENT_MEMORY.md [ ] Naming consistent [ ] File structure correct
EDGE CASES: [ ] Null/empty [ ] Network timeout

Output EXACTLY this format:

## Review: {task_id}
Date: {now}
### Verdict: APPROVE
[or]
### Verdict: REJECT

### Checklist
[ticked items]

### Issues (if REJECT only)
- [CRITICAL|HIGH|MEDIUM] file:line — issue + fix

### Summary
[2-3 sentences]

Save to PROJECT_DOCS/QA_REPORTS/{task_id}_review.md
"""

    output = run_claude_task(
        prompt=prompt,
        working_dir=project_dir or ROOT,
        context_files=["AGENT_MEMORY.md"],
        max_turns=10,
        timeout=240,
    )

    verdict = "REJECT"
    if "### Verdict: APPROVE" in output:
        verdict = "APPROVE"

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
