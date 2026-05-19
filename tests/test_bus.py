"""Tests for the AgentMessageBus."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from teamnot.agents.bus import AgentMessageBus, MessageIntent


def test_send_and_inbox(tmp_path: Path):
    bus = AgentMessageBus(log_path=tmp_path / "msg.jsonl")
    m = bus.send(
        sender="architect",
        recipient="implementer",
        intent=MessageIntent.request_work,
        subject="implement T1",
        payload={"adr": "ADRs/T1.md"},
    )
    inbox = bus.inbox("implementer")
    assert len(inbox) == 1
    assert inbox[0].id == m.id
    # Second inbox call is empty (consumed)
    assert bus.inbox("implementer") == []


def test_peek_does_not_consume(tmp_path: Path):
    bus = AgentMessageBus()
    bus.send(sender="a", recipient="b", intent=MessageIntent.inform, subject="x", payload={})
    assert len(bus.inbox("b", peek=True)) == 1
    assert len(bus.inbox("b", peek=True)) == 1  # still there
    assert len(bus.inbox("b")) == 1
    assert bus.inbox("b") == []  # consumed now


def test_reply_correlation(tmp_path: Path):
    bus = AgentMessageBus()
    q = bus.send(
        sender="implementer",
        recipient="architect",
        intent=MessageIntent.request_info,
        subject="where does JWT expiry live?",
        payload={},
    )
    r = bus.send(
        sender="architect",
        recipient="implementer",
        intent=MessageIntent.reply,
        subject="re: jwt expiry",
        payload={"answer": "src/auth/jwt.py:42"},
        reply_to=q.id,
    )
    reply = bus.wait_for_reply(q.id, timeout_s=0.5)
    assert reply is not None
    assert reply.id == r.id
    assert reply.payload["answer"].startswith("src/auth")


def test_wait_for_reply_times_out_when_no_reply(tmp_path: Path):
    bus = AgentMessageBus()
    q = bus.send(sender="a", recipient="b", intent=MessageIntent.request_info,
                 subject="?", payload={})
    started = time.monotonic()
    reply = bus.wait_for_reply(q.id, timeout_s=0.4)
    elapsed = time.monotonic() - started
    assert reply is None
    assert 0.35 < elapsed < 1.0


def test_wait_for_reply_unblocks_on_reply(tmp_path: Path):
    bus = AgentMessageBus()
    q = bus.send(sender="a", recipient="b", intent=MessageIntent.request_info,
                 subject="?", payload={})

    def sender():
        time.sleep(0.1)
        bus.send(sender="b", recipient="a", intent=MessageIntent.reply,
                 subject="re", payload={"v": 1}, reply_to=q.id)

    t = threading.Thread(target=sender)
    t.start()
    reply = bus.wait_for_reply(q.id, timeout_s=2.0)
    t.join()
    assert reply is not None


def test_persist_writes_jsonl(tmp_path: Path):
    log = tmp_path / "messages.jsonl"
    bus = AgentMessageBus(log_path=log)
    bus.send(sender="a", recipient="b", intent=MessageIntent.inform,
             subject="hi", payload={"k": "v"})
    bus.send(sender="b", recipient="a", intent=MessageIntent.reply,
             subject="bye", payload={})
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["sender"] == "a"
    assert rec["recipient"] == "b"
    assert rec["intent"] == "inform"


def test_pop_returns_next_and_marks_consumed(tmp_path: Path):
    bus = AgentMessageBus()
    bus.send(sender="a", recipient="b", intent=MessageIntent.inform, subject="1", payload={})
    bus.send(sender="a", recipient="b", intent=MessageIntent.inform, subject="2", payload={})
    m1 = bus.pop("b")
    m2 = bus.pop("b")
    m3 = bus.pop("b")
    assert m1 and m1.subject == "1"
    assert m2 and m2.subject == "2"
    assert m3 is None


def test_transcript_preserves_order(tmp_path: Path):
    bus = AgentMessageBus()
    for i in range(5):
        bus.send(sender="a", recipient="b", intent=MessageIntent.inform,
                 subject=f"msg-{i}", payload={})
    subs = [m.subject for m in bus.transcript()]
    assert subs == ["msg-0", "msg-1", "msg-2", "msg-3", "msg-4"]


def test_unknown_message_id_raises_for_wait_for_reply():
    bus = AgentMessageBus()
    with pytest.raises(KeyError):
        bus.wait_for_reply("not-a-real-id", timeout_s=0.1)
