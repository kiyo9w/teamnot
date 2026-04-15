"""
TeamNoT Session Manager
Quản lý session window cho Claude CLI và Qwen CLI.
Không track token cost — track thời gian sử dụng.

Claude Code CLI: OAuth session ~5h, tự refresh
Qwen Code CLI: subscription, ~8h window
MiniMax: API key, không giới hạn session (track cost riêng)
"""
import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger("TeamNoT.Session")
ROOT = Path(os.getenv("TEAMNOT_ROOT",
            r"C:\Users\Jenky - MiniPC\Desktop\Project\TeamNoT"))
STATE_FILE = ROOT / "LOGS" / "session_state.json"

# Session window config (giờ)
SESSION_WINDOWS = {
    "claude": {
        "window_hours": 5,
        "warn_at_remaining_minutes": 30,   # cảnh báo khi còn 30 phút
        "pause_at_remaining_minutes": 10,  # tạm dừng task nếu còn 10 phút
    },
    "qwen": {
        "window_hours": 8,                 # điều chỉnh theo thực tế
        "warn_at_remaining_minutes": 30,
        "pause_at_remaining_minutes": 10,
    },
    "minimax": {
        "window_hours": 24,                # API key — không giới hạn session
        "warn_at_remaining_minutes": 0,
        "pause_at_remaining_minutes": 0,
    },
}


@dataclass
class SessionWindow:
    provider: str
    started_at: str
    window_hours: float
    calls_made: int = 0
    last_call_at: Optional[str] = None
    paused: bool = False

    @property
    def started_dt(self) -> datetime:
        return datetime.fromisoformat(self.started_at)

    @property
    def expires_at(self) -> datetime:
        return self.started_dt + timedelta(hours=self.window_hours)

    @property
    def remaining_minutes(self) -> float:
        delta = self.expires_at - datetime.now()
        return max(0, delta.total_seconds() / 60)

    @property
    def is_expired(self) -> bool:
        return datetime.now() >= self.expires_at

    @property
    def elapsed_minutes(self) -> float:
        return (datetime.now() - self.started_dt).total_seconds() / 60


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, SessionWindow] = {}
        self._load()

    def _load(self):
        if STATE_FILE.exists():
            try:
                raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                for provider, data in raw.items():
                    self._sessions[provider] = SessionWindow(**data)
            except Exception as e:
                logger.error(f"Failed to load session state: {e}")
        # Khởi tạo session mới cho provider chưa có
        for provider in SESSION_WINDOWS:
            if provider not in self._sessions:
                self._start_session(provider)

    def _save(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {p: asdict(s) for p, s in self._sessions.items()}
        STATE_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _start_session(self, provider: str):
        cfg = SESSION_WINDOWS.get(provider, {"window_hours": 8})
        self._sessions[provider] = SessionWindow(
            provider=provider,
            started_at=datetime.now().isoformat(),
            window_hours=cfg["window_hours"],
        )
        self._save()
        logger.info(f"[{provider}] New session started — {cfg['window_hours']}h window")

    def refresh_if_expired(self, provider: str):
        """Gọi trước mỗi task — tự refresh nếu session đã hết."""
        session = self._sessions.get(provider)
        if session is None or session.is_expired:
            logger.info(f"[{provider}] Session expired/missing — auto refresh")
            self._start_session(provider)

    def record_call(self, provider: str):
        """Ghi nhận một lần gọi CLI."""
        self.refresh_if_expired(provider)
        s = self._sessions[provider]
        s.calls_made += 1
        s.last_call_at = datetime.now().isoformat()
        self._save()

    def check_window(self, provider: str) -> dict:
        """
        Kiểm tra trạng thái session.
        Returns: {"ok", "warn", "should_pause", "remaining_minutes", ...}
        """
        self.refresh_if_expired(provider)
        s = self._sessions[provider]
        cfg = SESSION_WINDOWS.get(provider, {})
        remaining = s.remaining_minutes
        warn_at = cfg.get("warn_at_remaining_minutes", 30)
        pause_at = cfg.get("pause_at_remaining_minutes", 10)

        return {
            "ok": remaining > pause_at,
            "warn": 0 < warn_at >= remaining > pause_at,
            "should_pause": remaining <= pause_at and pause_at > 0,
            "remaining_minutes": round(remaining, 1),
            "elapsed_minutes": round(s.elapsed_minutes, 1),
            "calls_made": s.calls_made,
            "expires_at": s.expires_at.strftime("%H:%M"),
        }

    def status_all(self) -> str:
        lines = ["Session Windows:"]
        for provider in SESSION_WINDOWS:
            self.refresh_if_expired(provider)
            info = self.check_window(provider)
            warn_tag = " (!) LOW" if info["should_pause"] else (
                " (!) WARN" if info["warn"] else ""
            )
            lines.append(
                f"  {provider:10} | {info['remaining_minutes']:6.1f}m left "
                f"(expires {info['expires_at']}) "
                f"| {info['calls_made']} calls{warn_tag}"
            )
        return "\n".join(lines)

    def get_next_available_claude(self) -> dict:
        """Trả về thời điểm Claude session tiếp theo sẵn sàng."""
        s = self._sessions.get("claude")
        if s is None or s.is_expired:
            return {"available_now": True, "wait_minutes": 0}
        remaining = s.remaining_minutes
        pause_at = SESSION_WINDOWS["claude"]["pause_at_remaining_minutes"]
        if remaining > pause_at:
            return {"available_now": True, "wait_minutes": 0}
        # Session gần hết — tính thời gian chờ
        wait = s.expires_at - datetime.now()
        return {
            "available_now": False,
            "wait_minutes": round(max(0, wait.total_seconds() / 60), 1),
            "available_at": s.expires_at.strftime("%H:%M"),
        }


# Singleton
_manager: Optional[SessionManager] = None


def get_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
