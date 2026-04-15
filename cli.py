"""
TeamNoT CLI — giao diện terminal cho Phase 2.

Cách dùng:
  python cli.py run "Tạo FastAPI CRUD cho module Product"
  python cli.py queue "Task 1" "Task 2" "Task 3"
  python cli.py queue-p "Task ưu tiên cao" --priority 1
  python cli.py status
  python cli.py cost
  python cli.py loop
  python cli.py pause TASK-20260414-123456
  python cli.py resume TASK-20260414-123456
"""
import sys
import os
import io
import logging
from dotenv import load_dotenv

# Fix Windows console encoding cho tiếng Việt
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def cmd_run(task: str):
    """Chạy ngay một task, block đến khi xong."""
    from teamnot import run_task
    report = run_task(task)
    print("\n=== REPORT ===\n", report)


def cmd_queue(*tasks: str):
    """Thêm nhiều task vào queue (không chạy ngay)."""
    from task_queue import TaskQueue
    q = TaskQueue()
    for t in tasks:
        task = q.add(t)
        print(f"Queued: {task.id} — {t[:60]}")
    print(f"\n{len(tasks)} task(s) added. Run: python cli.py loop")


def cmd_queue_priority(task: str, priority: int = 1):
    """Thêm task vào queue với priority tùy chỉnh."""
    from task_queue import TaskQueue
    q = TaskQueue()
    t = q.add(task, priority=priority)
    print(f"Queued [priority={priority}]: {t.id} — {task[:60]}")


def cmd_status():
    """Hiển thị trạng thái tất cả task trong queue."""
    from task_queue import TaskQueue
    q = TaskQueue()
    print(q.status_summary())
    print()
    for t in q.get_all():
        started = f" | started {t.started_at[:16]}" if t.started_at else ""
        done = f" | done {t.done_at[:16]}" if t.done_at else ""
        error = f" | ERR: {t.error[:40]}" if t.error else ""
        status_str = t.status.value if hasattr(t.status, "value") else str(t.status)
        print(f"  [{status_str}] {t.id}: {t.description[:50]}{started}{done}{error}")


def cmd_cost():
    """Xem tổng resource usage: API cost + session windows."""
    from cost_tracker import full_summary
    print(full_summary())


def cmd_loop():
    """Chạy queue loop song song (blocking). Ctrl+C để dừng."""
    from teamnot import run_queue_loop
    print("TeamNoT queue loop started (parallel). Ctrl+C để dừng.")
    try:
        run_queue_loop()
    except KeyboardInterrupt:
        print("\nQueue loop stopped by user.")


def cmd_pause(task_id: str):
    """Tạm dừng task đang chạy (cần xác nhận trước khi gọi hàm này)."""
    from task_queue import TaskQueue
    if TaskQueue().pause(task_id):
        print(f"Paused: {task_id}")
    else:
        print(f"Cannot pause {task_id} (not RUNNING or not found)")


def cmd_resume(task_id: str):
    """Tiếp tục task đang PAUSED."""
    from task_queue import TaskQueue
    if TaskQueue().resume(task_id):
        print(f"Resumed: {task_id} — will run on next loop cycle")
    else:
        print(f"Cannot resume {task_id} (not PAUSED or not found)")


def cmd_project(requirement: str, project_name: str = None):
    """Phase 3: full specialist team cho project nhiều domain."""
    from teamnot import run_project

    if not project_name:
        project_name = "_".join(requirement.split()[:3]).lower()
        project_name = "".join(
            c if c.isalnum() or c == "_" else "" for c in project_name
        )

    print(f"TeamNoT Phase 3")
    print(f"Project: {project_name}")
    print(f"Team: PM + Claude(Architect/Reviewer) + FE + BE + AI + DevOps + QA")
    print(f"Telegram sẽ nhận báo cáo khi sprint xong.\n")

    report = run_project(requirement, project_name)
    print("\n=== SPRINT REPORT ===\n", report)


def cmd_sprint_status():
    """Xem trạng thái sprint hiện tại."""
    from pathlib import Path
    root = Path(os.getenv("TEAMNOT_ROOT", "."))
    sprint = root / "SPRINTS" / "SPRINT_CURRENT.md"
    if sprint.exists():
        print(sprint.read_text(encoding="utf-8"))
    else:
        print("No active sprint.")
        print("Run: python cli.py project '<requirement>'")


def cmd_test_claude():
    """Kiểm tra Claude Code CLI hoạt động không."""
    from claude_worker import run_claude_task
    print("Testing Claude Code CLI...")
    result = run_claude_task(
        "Reply with exactly 3 words: CLAUDE CLI OK",
        timeout=30,
    )
    print("Result:", result[:200])
    if "CLAUDE" in result.upper() and len(result) > 5:
        print("Claude Code CLI OK")
    else:
        print("Unexpected output — check manually")


def cmd_sessions():
    """Xem trạng thái session windows của Claude và Qwen CLI."""
    from session_manager import get_manager
    mgr = get_manager()
    print(mgr.status_all())
    print()
    info = mgr.check_window("claude")
    if info["should_pause"]:
        avail = mgr.get_next_available_claude()
        print(f"Claude session còn {info['remaining_minutes']}m — "
              f"không nên bắt task mới cần Architect/Reviewer.")
        print(f"   Session mới lúc {avail.get('available_at', '?')}")
    elif info["warn"]:
        print(f"Claude session còn {info['remaining_minutes']}m — "
              f"hãy hoàn thành task hiện tại trước khi bắt task mới.")


def cmd_reset_session(provider: str = "claude"):
    """Reset session window thủ công (dùng khi đã login lại CLI)."""
    from session_manager import get_manager, SessionWindow, SESSION_WINDOWS
    mgr = get_manager()
    cfg = SESSION_WINDOWS.get(provider, {"window_hours": 5})
    from datetime import datetime as dt
    mgr._sessions[provider] = SessionWindow(
        provider=provider,
        started_at=dt.now().isoformat(),
        window_hours=cfg["window_hours"],
    )
    mgr._save()
    print(f"Session reset: {provider} — {cfg['window_hours']}h window from now")


COMMANDS = {
    "run":         (cmd_run,            "run <task>"),
    "queue":       (cmd_queue,          "queue <task1> [task2] ..."),
    "queue-p":     (cmd_queue_priority, "queue-p <task> [--priority 1-10]"),
    "status":      (cmd_status,         "status"),
    "cost":        (cmd_cost,           "cost"),
    "loop":        (cmd_loop,           "loop"),
    "pause":       (cmd_pause,          "pause <task-id>"),
    "resume":      (cmd_resume,         "resume <task-id>"),
    "project":       (cmd_project,        "project '<requirement>' [project_name]"),
    "sprint":        (cmd_sprint_status,  "sprint"),
    "test-claude":   (cmd_test_claude,    "test-claude"),
    "sessions":      (cmd_sessions,       "sessions"),
    "reset-session": (cmd_reset_session,  "reset-session [claude|qwen|minimax]"),
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("TeamNoT CLI — Phase 3\n")
        print("Commands:")
        for name, (_, usage) in COMMANDS.items():
            print(f"  python cli.py {usage}")
        sys.exit(0)

    cmd  = sys.argv[1]
    args = sys.argv[2:]

    # Xử lý --priority flag cho queue-p
    priority = 5
    if "--priority" in args:
        idx      = args.index("--priority")
        priority = int(args[idx + 1])
        args     = [a for i, a in enumerate(args)
                    if i != idx and i != idx + 1]

    fn = COMMANDS[cmd][0]
    if cmd == "queue-p":
        fn(*args, priority=priority)
    elif cmd == "project":
        requirement = args[0] if args else ""
        pname = args[1] if len(args) > 1 else None
        fn(requirement, pname)
    else:
        fn(*args)
