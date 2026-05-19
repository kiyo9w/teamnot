"""Telegram gateway — receive briefs by chat, deliver reports back.

Optional dependency: ``aiogram>=3``. Install with ``pip install teamnot[telegram]``.

Wire-up:

    teamnot telegram --token $TELEGRAM_BOT_TOKEN --workspaces ./workspaces

A workspace directory contains one sub-directory per project, each holding a
``.teamnot/brief.yaml``. Users address them by sending::

    /run <project_slug>

The bot dispatches a Worker on the matching brief and replies with the report
when done.

The brief's ``deliverable.report_to`` should be ``telegram`` for the report to
be sent back through this chat.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger("teamnot.gateways.telegram")


def _require_aiogram():
    try:
        import aiogram  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "aiogram not installed. Install with: pip install teamnot[telegram]"
        ) from e


async def run_bot(
    token: str,
    workspaces_root: Path,
    allowed_chat_ids: list[int] | None = None,
) -> None:
    """Run the Telegram bot loop. Blocks until cancelled."""
    _require_aiogram()
    from aiogram import Bot, Dispatcher
    from aiogram.filters import Command
    from aiogram.types import Message

    bot = Bot(token=token)
    dp = Dispatcher()

    workspaces_root = workspaces_root.expanduser().resolve()
    if not workspaces_root.exists():
        raise FileNotFoundError(f"workspaces root not found: {workspaces_root}")

    def _check_chat(message: Message) -> bool:
        if not allowed_chat_ids:
            return True
        return message.chat.id in allowed_chat_ids

    def _list_projects() -> list[str]:
        out: list[str] = []
        for sub in workspaces_root.iterdir():
            if (sub / ".teamnot" / "brief.yaml").exists():
                out.append(sub.name)
        return sorted(out)

    @dp.message(Command("start"))
    async def on_start(message: Message) -> None:
        if not _check_chat(message):
            return
        projects = _list_projects()
        body = (
            "TeamNoT online.\n\n"
            "Commands:\n"
            "  /projects — list available projects\n"
            "  /run <project> — start a run\n"
            "  /status <project> — last result\n\n"
            f"Workspaces: `{workspaces_root}`\n"
            f"Projects: {', '.join(projects) or '(none — drop a .teamnot/brief.yaml inside one)'}"
        )
        await message.reply(body)

    @dp.message(Command("projects"))
    async def on_projects(message: Message) -> None:
        if not _check_chat(message):
            return
        projects = _list_projects()
        if not projects:
            await message.reply("No projects with .teamnot/brief.yaml yet.")
            return
        await message.reply("Projects:\n" + "\n".join(f" • {p}" for p in projects))

    @dp.message(Command("status"))
    async def on_status(message: Message) -> None:
        if not _check_chat(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("Usage: /status <project>")
            return
        slug = parts[1].strip()
        reports_dir = workspaces_root / slug / ".teamnot" / "reports"
        if not reports_dir.exists():
            await message.reply(f"No reports for {slug}")
            return
        reports = sorted(reports_dir.glob("*.md"))
        if not reports:
            await message.reply(f"No reports yet for {slug}")
            return
        latest = reports[-1]
        body = latest.read_text(encoding="utf-8")
        if len(body) > 3500:
            body = body[:3500] + "\n…(truncated)"
        await message.reply(f"```\n{body}\n```", parse_mode="Markdown")

    @dp.message(Command("run"))
    async def on_run(message: Message) -> None:
        if not _check_chat(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("Usage: /run <project>")
            return
        slug = parts[1].strip()
        brief_path = workspaces_root / slug / ".teamnot" / "brief.yaml"
        if not brief_path.exists():
            await message.reply(f"No brief at `{brief_path}`")
            return

        await message.reply(f"Starting TeamNoT run for `{slug}`…")
        try:
            # Run synchronously in a thread so we don't block the bot.
            from teamnot import Worker, load_brief
            brief = load_brief(brief_path)
            # Deep-copy before mutating so concurrent /run calls on the same
            # brief don't trample each other's deliverable.telegram_chat_id.
            brief = brief.model_copy(deep=True)
            if not brief.deliverable.telegram_chat_id:
                brief.deliverable.telegram_chat_id = str(message.chat.id)
            worker = Worker(brief)
            result = await asyncio.to_thread(worker.run_until_done)
        except Exception as e:
            await message.reply(f"Run crashed: {type(e).__name__}: {e}")
            return

        summary = (
            f"Status: `{result.status.value}`\n"
            f"{result.summary}\n\n"
            f"Report: `{result.report_path or '—'}`"
        )
        await message.reply(summary)
        if result.report_path and Path(result.report_path).exists():
            body = Path(result.report_path).read_text(encoding="utf-8")
            if len(body) > 3500:
                body = body[:3500] + "\n…(truncated)"
            await message.reply(f"```\n{body}\n```", parse_mode="Markdown")

    logger.info("Telegram gateway online. Allowed chats: %s",
                allowed_chat_ids or "(all)")
    await dp.start_polling(bot)


def run_blocking(
    token: str | None = None,
    workspaces_root: str | Path = ".",
    allowed_chat_ids: list[int] | None = None,
) -> None:
    """Sync entry point — used by the CLI."""
    tok = token or os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TEAMNOT_TELEGRAM_TOKEN")
    if not tok:
        raise RuntimeError(
            "No Telegram bot token. Pass --token, set TELEGRAM_BOT_TOKEN, or "
            "set TEAMNOT_TELEGRAM_TOKEN."
        )
    asyncio.run(
        run_bot(
            token=tok,
            workspaces_root=Path(workspaces_root),
            allowed_chat_ids=allowed_chat_ids,
        )
    )
