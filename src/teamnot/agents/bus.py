"""Agent message bus — structured inter-agent communication.

The legacy crew piped string outputs from one task to the next. That works for
simple pipelines but breaks down as soon as the implementer needs to ask the
architect a question, or the tester wants the implementer to redo something.

The bus models messages explicitly:

    msg = bus.send(
        sender="implementer",
        recipient="architect",
        intent=MessageIntent.request_info,
        subject="Need clarification on auth flow",
        payload={"question": "Where does JWT expiry live?"},
    )
    reply = bus.wait_for_reply(msg.id, timeout_s=120)

Replies are correlated by ``reply_to``. The bus also persists every message
to ``.teamnot/logs/messages.jsonl`` so the engine can replay the conversation
when the user inspects a finished run.

Thread-safe — the pipeline may dispatch agents in parallel.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger("teamnot.agents.bus")


class MessageIntent(str, Enum):
    """Why one agent is messaging another."""
    inform = "inform"                  # FYI, no reply expected
    request_info = "request_info"      # please answer
    request_work = "request_work"      # please do
    reply = "reply"                    # answer to an earlier request
    review = "review"                  # please review this artifact
    approve = "approve"                # I approve <artifact>
    reject = "reject"                  # I reject <artifact>; here is why
    handoff = "handoff"                # I am done; next agent takes over
    blocker = "blocker"                # I cannot proceed; explain to coordinator


@dataclass
class AgentMessage:
    """A single message on the bus.

    `payload` is freeform JSON — usually an artifact path, a question, a
    review verdict, etc. Keep it small enough to fit in a single line of the
    JSONL log.
    """
    id: str
    sender: str
    recipient: str
    intent: MessageIntent
    subject: str
    payload: dict
    reply_to: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    consumed: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sender": self.sender,
            "recipient": self.recipient,
            "intent": self.intent.value,
            "subject": self.subject,
            "payload": self.payload,
            "reply_to": self.reply_to,
            "created_at": self.created_at,
            "consumed": self.consumed,
        }

    def to_md(self) -> str:
        rt = f" ↳ reply to `{self.reply_to}`" if self.reply_to else ""
        body = json.dumps(self.payload, ensure_ascii=False)
        if len(body) > 220:
            body = body[:220] + "…"
        return (
            f"- `{self.created_at}` **{self.sender}** → **{self.recipient}** "
            f"`{self.intent.value}`{rt}\n  - {self.subject}\n  - `{body}`"
        )


class AgentMessageBus:
    """Thread-safe routing + persistence for inter-agent messages."""

    def __init__(self, log_path: Path | None = None):
        self._lock = threading.RLock()
        self._messages: list[AgentMessage] = []
        self._by_id: dict[str, AgentMessage] = {}
        self._inboxes: dict[str, deque[AgentMessage]] = defaultdict(deque)
        self._replies: dict[str, list[AgentMessage]] = defaultdict(list)
        self._reply_events: dict[str, threading.Event] = defaultdict(threading.Event)
        self.log_path = log_path

    # ── Send ──────────────────────────────────────────────────────────────

    def send(
        self,
        *,
        sender: str,
        recipient: str,
        intent: MessageIntent,
        subject: str,
        payload: dict | None = None,
        reply_to: str | None = None,
    ) -> AgentMessage:
        msg = AgentMessage(
            id=str(uuid.uuid4()),
            sender=sender,
            recipient=recipient,
            intent=intent,
            subject=subject,
            payload=payload or {},
            reply_to=reply_to,
        )
        with self._lock:
            self._messages.append(msg)
            self._by_id[msg.id] = msg
            self._inboxes[recipient].append(msg)
            if reply_to:
                self._replies[reply_to].append(msg)
                self._reply_events[reply_to].set()
        self._persist(msg)
        return msg

    # ── Receive ───────────────────────────────────────────────────────────

    def inbox(self, agent: str, peek: bool = False) -> list[AgentMessage]:
        """All unconsumed messages addressed to ``agent`` (order preserved)."""
        with self._lock:
            queue = self._inboxes[agent]
            unconsumed = [m for m in queue if not m.consumed]
            if not peek:
                for m in unconsumed:
                    m.consumed = True
            return list(unconsumed)

    def pop(self, agent: str) -> AgentMessage | None:
        """Pop the next unconsumed message from this agent's inbox, or None."""
        with self._lock:
            queue = self._inboxes[agent]
            while queue:
                m = queue.popleft()
                if not m.consumed:
                    m.consumed = True
                    return m
        return None

    def wait_for_reply(self, message_id: str, timeout_s: float = 60.0) -> AgentMessage | None:
        """Block until a reply to ``message_id`` arrives, or timeout."""
        if message_id not in self._by_id:
            raise KeyError(f"No such message: {message_id}")

        deadline = time.monotonic() + timeout_s
        event = self._reply_events[message_id]
        while True:
            with self._lock:
                replies = self._replies.get(message_id, [])
                if replies:
                    return replies[0]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            event.wait(timeout=min(remaining, 1.0))

    # ── Helpers ───────────────────────────────────────────────────────────

    def all_messages(self) -> list[AgentMessage]:
        with self._lock:
            return list(self._messages)

    def transcript(self) -> Iterator[AgentMessage]:
        with self._lock:
            yield from list(self._messages)

    def to_md(self) -> str:
        lines = ["# Agent transcript", ""]
        for m in self.transcript():
            lines.append(m.to_md())
        return "\n".join(lines) + "\n"

    # ── Persistence ───────────────────────────────────────────────────────

    def _persist(self, msg: AgentMessage) -> None:
        if not self.log_path:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("bus persist failed: %s", e)
