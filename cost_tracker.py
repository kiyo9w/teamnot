"""
TeamNoT Cost Tracker — hybrid tracking.
- MiniMax: API cost (token-based, USD)
- Claude/Qwen CLI: call count only (OAuth subscription, no token cost)
Ghi vào LOGS/cost_tracking.json.
"""
import json
import os
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("TeamNoT.Cost")

ROOT = Path(os.getenv("TEAMNOT_ROOT",
            r"C:\Users\Jenky - MiniPC\Desktop\Project\TeamNoT"))
COST_FILE = ROOT / "LOGS" / "cost_tracking.json"

# USD per 1M tokens — cập nhật khi giá thay đổi
PRICES = {
    "MiniMax-M2.7":           {"in": 0.30,  "out": 1.20},
    "MiniMax-M2.7-highspeed": {"in": 0.60,  "out": 2.40},
    "claude-sonnet-4-6":      {"in": 3.00,  "out": 15.00},
    "qwen-coder-plus":        {"in": 0.35,  "out": 1.40},
    "default":                {"in": 1.00,  "out": 3.00},
}


def _load() -> dict:
    """Load cost data từ file JSON."""
    if COST_FILE.exists():
        try:
            return json.loads(COST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tasks": [], "total_cost_usd": 0.0, "last_updated": ""}


def _save(data: dict):
    """Persist cost data sang JSON."""
    COST_FILE.parent.mkdir(parents=True, exist_ok=True)
    COST_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def log_usage(task_id: str, model: str,
              tokens_in: int, tokens_out: int) -> float:
    """
    Ghi token usage cho một API call.

    Returns:
        float: cost của call này (USD)
    """
    price = PRICES.get(model, PRICES["default"])
    cost  = (tokens_in  / 1_000_000 * price["in"]) + \
            (tokens_out / 1_000_000 * price["out"])
    cost  = round(cost, 5)

    data  = _load()
    data["tasks"].append({
        "task_id":    task_id,
        "model":      model,
        "tokens_in":  tokens_in,
        "tokens_out": tokens_out,
        "cost_usd":   cost,
        "timestamp":  datetime.now().isoformat(),
    })
    data["total_cost_usd"] = round(
        sum(t["cost_usd"] for t in data["tasks"]), 4)
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    _save(data)
    return cost


def task_total(task_id: str) -> float:
    """Tổng cost của một task cụ thể."""
    data = _load()
    return round(sum(t["cost_usd"]
                     for t in data["tasks"]
                     if t["task_id"] == task_id), 4)


def summary() -> str:
    """Tóm tắt cost toàn bộ, nhóm theo task."""
    data  = _load()
    tasks = data.get("tasks", [])
    if not tasks:
        return "No cost data yet."
    by_task: dict = {}
    for t in tasks:
        by_task[t["task_id"]] = by_task.get(t["task_id"], 0.0) + t["cost_usd"]
    lines = [f"Total: ${data['total_cost_usd']:.3f} USD"]
    for tid, cost in sorted(by_task.items(), key=lambda x: -x[1]):
        lines.append(f"  {tid}: ${cost:.3f}")
    return "\n".join(lines)


# ── Session-based tracking cho CLI models ─────────────────────────

def log_cli_call(task_id: str, provider: str, duration_seconds: float = 0):
    """
    Ghi nhận CLI call (claude / qwen) — không có token cost.
    Chỉ track số lần gọi và thời gian để phân tích.
    """
    data = _load()
    if "cli_calls" not in data:
        data["cli_calls"] = []
    data["cli_calls"].append({
        "task_id": task_id,
        "provider": provider,
        "duration_seconds": round(duration_seconds, 1),
        "timestamp": datetime.now().isoformat(),
        "note": "OAuth subscription — no token cost",
    })
    _save(data)


def full_summary() -> str:
    """Summary tổng hợp: API cost + CLI session usage."""
    from session_manager import get_manager
    mgr = get_manager()
    data = _load()

    lines = ["=== TeamNoT Resource Usage ===", ""]

    # API cost (MiniMax)
    api_cost = data.get("total_cost_usd", 0)
    api_tasks = data.get("tasks", [])
    lines.append(f"MiniMax API cost: ${api_cost:.4f} USD ({len(api_tasks)} calls)")

    # CLI calls
    cli_calls = data.get("cli_calls", [])
    if cli_calls:
        claude_calls = sum(1 for c in cli_calls if c["provider"] == "claude")
        qwen_calls = sum(1 for c in cli_calls if c["provider"] == "qwen")
        lines.append(f"Claude CLI calls: {claude_calls} (OAuth — no cost)")
        lines.append(f"Qwen CLI calls:   {qwen_calls} (subscription — no cost)")
    else:
        lines.append("Claude/Qwen CLI: no calls yet")

    lines.append("")
    lines.append(mgr.status_all())

    # Per-task breakdown (API only)
    if api_tasks:
        by_task: dict = {}
        for t in api_tasks:
            by_task[t["task_id"]] = by_task.get(t["task_id"], 0.0) + t["cost_usd"]
        lines.append("")
        lines.append("API cost by task:")
        for tid, cost in sorted(by_task.items(), key=lambda x: -x[1]):
            lines.append(f"  {tid}: ${cost:.4f}")

    return "\n".join(lines)
