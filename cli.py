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
    """Hiển thị tóm tắt chi phí theo task."""
    from cost_tracker import summary
    print(summary())


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


COMMANDS = {
    "run":      (cmd_run,            "run <task>"),
    "queue":    (cmd_queue,          "queue <task1> [task2] ..."),
    "queue-p":  (cmd_queue_priority, "queue-p <task> [--priority 1-10]"),
    "status":   (cmd_status,         "status"),
    "cost":     (cmd_cost,           "cost"),
    "loop":     (cmd_loop,           "loop"),
    "pause":    (cmd_pause,          "pause <task-id>"),
    "resume":   (cmd_resume,         "resume <task-id>"),
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("TeamNoT CLI — Phase 2\n")
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
    else:
        fn(*args)
