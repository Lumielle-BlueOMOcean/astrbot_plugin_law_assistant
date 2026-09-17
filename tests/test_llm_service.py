from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_service import LLMService, parse_json_response


def test_parse_json_response_accepts_fenced_json() -> None:
    assert parse_json_response('```json\n{"relevant": true}\n```') == {"relevant": True}


@pytest.mark.asyncio
async def test_llm_service_resolves_interactive_provider_and_calls_context() -> None:
    class Provider:
        def meta(self):
            return SimpleNamespace(id="default-provider")

    class Context:
        def __init__(self):
            self.calls = []

        async def get_current_chat_provider_id(self, umo):
            self.calls.append(("current", umo))
            return "session-provider"

        def get_using_provider(self, umo=None):
            return Provider()

        async def llm_generate(self, **kwargs):
            self.calls.append(("generate", kwargs))
            return SimpleNamespace(completion_text='{"ok": true}')

    context = Context()
    service = LLMService(context)

    result = await service.generate_json(
        "return JSON", session_origin="aiocqhttp:private:1"
    )

    assert result == {"ok": True}
    assert context.calls[0] == ("current", "aiocqhttp:private:1")
    assert context.calls[1][1]["chat_provider_id"] == "session-provider"


@pytest.mark.asyncio
async def test_llm_service_returns_none_without_provider() -> None:
    class Context:
        async def get_current_chat_provider_id(self, umo):
            raise RuntimeError("provider unavailable")

        def get_using_provider(self, umo=None):
            return None

    assert await LLMService(Context()).generate_json("return JSON") is None
