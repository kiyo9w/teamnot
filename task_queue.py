"""
TeamNoT Task Queue — chạy nhiều task song song với thread isolation.
Mỗi task chạy trong thread riêng, không block nhau.
"""
import json
import os
import threading
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum

logger = logging.getLogger("TeamNoT.Queue")

ROOT = Path(os.getenv("TEAMNOT_ROOT",
            r"C:\Users\Jenky - MiniPC\Desktop\Project\TeamNoT"))
QUEUE_FILE   = ROOT / "task_queue.json"
COST_LIMIT   = float(os.getenv("TEAMNOT_COST_LIMIT", "5.0"))
MAX_PARALLEL = int(os.getenv("TEAMNOT_MAX_PARALLEL", "3"))


class TaskStatus(str, Enum):
    QUEUED  = "QUEUED"
    RUNNING = "RUNNING"
    DONE    = "DONE"
    BLOCKED = "BLOCKED"
    PAUSED  = "PAUSED"
    FAILED  = "FAILED"


@dataclass
class QueuedTask:
    """Đại diện cho một task trong queue."""
    id: str
    description: str
    status: TaskStatus    = TaskStatus.QUEUED
    priority: int         = 5          # 1=cao nhất, 10=thấp nhất
    created_at: str       = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str]  = None
    done_at: Optional[str]     = None
    report_path: Optional[str] = None
    cost_usd: float            = 0.0
    error: Optional[str]       = None
    thread_id: Optional[str]   = None


class TaskQueue:
    """Thread-safe queue. Nhiều task chạy song song theo MAX_PARALLEL."""

    def __init__(self):
        self._lock   = threading.Lock()
        self._tasks: list = []
        self._active_threads: dict = {}
        self._load()

    # ── persistence ──────────────────────────────────────────────
    def _load(self):
        """Load queue từ file JSON, reset RUNNING tasks sau crash."""
        if QUEUE_FILE.exists():
            try:
                raw = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
                self._tasks = []
                for t in raw:
                    # Chuyển status string sang enum
                    t["status"] = TaskStatus(t["status"])
                    self._tasks.append(QueuedTask(**t))
                # Reset RUNNING → QUEUED khi khởi động lại (crash recovery)
                for t in self._tasks:
                    if t.status == TaskStatus.RUNNING:
                        t.status = TaskStatus.QUEUED
                        logger.warning(f"Reset crashed task: {t.id}")
                self._save()
            except Exception as e:
                logger.error(f"Failed to load queue: {e} — starting fresh")
                self._tasks = []

    def _save(self):
        """Persist queue sang JSON."""
        try:
            data = [asdict(t) for t in self._tasks]
            QUEUE_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Failed to save queue: {e}")

    # ── public API ───────────────────────────────────────────────
    def add(self, description: str, priority: int = 5) -> QueuedTask:
        """Thêm task mới vào queue, sắp xếp theo priority."""
        with self._lock:
            task_id = f"TASK-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            task = QueuedTask(id=task_id, description=description,
                              priority=priority)
            self._tasks.append(task)
            self._tasks.sort(key=lambda t: t.priority)
            self._save()
            logger.info(f"Queued [priority={priority}]: {task_id} — {description[:60]}")
            return task

    def next_pending(self) -> Optional[QueuedTask]:
        """Lấy task tiếp theo nếu còn slot song song."""
        with self._lock:
            running_count = sum(
                1 for t in self._tasks if t.status == TaskStatus.RUNNING
            )
            if running_count >= MAX_PARALLEL:
                return None
            for t in self._tasks:
                if t.status == TaskStatus.QUEUED:
                    return t
            return None

    def update(self, task_id: str, **kwargs):
        """Cập nhật field của task theo task_id."""
        with self._lock:
            for t in self._tasks:
                if t.id == task_id:
                    for k, v in kwargs.items():
                        setattr(t, k, v)
            self._save()

    def pause(self, task_id: str) -> bool:
        """Yêu cầu pause task đang RUNNING — hiệu lực sau subtask hiện tại xong."""
        with self._lock:
            for t in self._tasks:
                if t.id == task_id and t.status == TaskStatus.RUNNING:
                    t.status = TaskStatus.PAUSED
                    self._save()
                    logger.info(f"Paused: {task_id}")
                    return True
            return False

    def resume(self, task_id: str) -> bool:
        """Tiếp tục task đang PAUSED."""
        with self._lock:
            for t in self._tasks:
                if t.id == task_id and t.status == TaskStatus.PAUSED:
                    t.status = TaskStatus.QUEUED
                    self._save()
                    logger.info(f"Resumed: {task_id}")
                    return True
            return False

    def get_all(self) -> list:
        """Trả về copy toàn bộ task list."""
        with self._lock:
            return list(self._tasks)

    def running_tasks(self) -> list:
        """Trả về các task đang RUNNING."""
        with self._lock:
            return [t for t in self._tasks if t.status == TaskStatus.RUNNING]

    def status_summary(self) -> str:
        """Tóm tắt trạng thái queue dạng string."""
        with self._lock:
            counts: dict = {}
            for t in self._tasks:
                counts[t.status] = counts.get(t.status, 0) + 1
            total_cost = sum(t.cost_usd for t in self._tasks)
            lines = [f"TeamNoT Queue — {len(self._tasks)} tasks total"]
            for status, cnt in counts.items():
                key = status.value if hasattr(status, "value") else str(status)
                lines.append(f"  {key}: {cnt}")
            lines.append(f"  Total cost: ${total_cost:.3f} USD")
            return "\n".join(lines)

    # ── parallel runner ──────────────────────────────────────────
    def run_parallel_loop(self, runner_fn):
        """
        Background loop: liên tục pick task từ queue và chạy song song.

        Args:
            runner_fn: callable(task: QueuedTask) -> str (report)
        """
        import time
        logger.info(f"Queue loop started (max parallel: {MAX_PARALLEL})")
        while True:
            task = self.next_pending()
            if task:
                self.update(task.id,
                            status=TaskStatus.RUNNING,
                            started_at=datetime.now().isoformat())

                def _run(t=task):
                    """Worker thread cho một task."""
                    try:
                        report = runner_fn(t)
                        self.update(t.id,
                                    status=TaskStatus.DONE,
                                    done_at=datetime.now().isoformat(),
                                    report_path=f"REPORTS/{t.id}.md")
                        logger.info(f"[{t.id}] Done")
                    except Exception as exc:
                        self.update(t.id,
                                    status=TaskStatus.FAILED,
                                    error=str(exc))
                        logger.error(f"[{t.id}] Failed: {exc}")

                thread = threading.Thread(target=_run, daemon=True,
                                          name=f"TeamNoT-{task.id}")
                self._active_threads[task.id] = thread
                thread.start()
                logger.info(f"[{task.id}] Thread started")
            else:
                time.sleep(5)
