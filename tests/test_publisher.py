from __future__ import annotations

import sys
import types

import pytest

from publisher import AstrBotPublisher


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
