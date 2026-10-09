from __future__ import annotations

import logging
import sys
import types

import pytest

from publisher import AstrBotPublisher, NoopPublisher


@pytest.mark.asyncio
async def test_astrbot_publisher_uses_unified_context_send_message(monkeypatch) -> None:
    class Chain:
        def __init__(self):
            self.text = ""

        def message(self, text):
            self.text = text
            return self

    class Context:
        def __init__(self):
            self.calls = []

        async def send_message(self, destination, chain):
            self.calls.append((destination, chain.text))
            return True

    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = Chain
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)

    context = Context()
    assert await AstrBotPublisher(context).publish_text("aiocqhttp:group:1", "公告")
    assert context.calls == [("aiocqhttp:group:1", "公告")]


@pytest.mark.asyncio
async def test_publisher_failure_does_not_log_or_return_raw_transport_error(
    monkeypatch, caplog
) -> None:
    class Chain:
        def message(self, _text):
            return self

    class Context:
        async def send_message(self, _destination, _chain):
            raise RuntimeError("PRIVATE_SESSION_TOKEN_SHOULD_NOT_ESCAPE")

    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = Chain
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    caplog.set_level(logging.WARNING)

    outcome = await AstrBotPublisher(Context()).publish_text(
        "aiocqhttp:group:secret", "synthetic body"
    )

    assert outcome.status == "unknown"
    assert "PRIVATE_SESSION_TOKEN_SHOULD_NOT_ESCAPE" not in str(outcome.error)
    assert "PRIVATE_SESSION_TOKEN_SHOULD_NOT_ESCAPE" not in caplog.text
    assert "aiocqhttp:group:secret" not in caplog.text


@pytest.mark.asyncio
async def test_noop_publisher_does_not_log_target_identity(caplog) -> None:
    caplog.set_level(logging.INFO)

    await NoopPublisher().publish_text("aiocqhttp:group:private-target", "synthetic")

    assert "aiocqhttp:group:private-target" not in caplog.text
