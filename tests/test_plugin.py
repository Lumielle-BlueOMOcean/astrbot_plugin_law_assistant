from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tests.fakes import FakeEvent


def install_fake_astrbot(monkeypatch, data_dir: Path) -> None:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")

    class FakeFilter:
        @staticmethod
        def command(name):
            def decorator(function):
                function._fake_command = name
                return function

            return decorator

        @staticmethod
        def llm_tool(name=None):
            def decorator(function):
                function._fake_llm_tool = name or function.__name__
                return function

            return decorator

    class FakeStar:
        def __init__(self, context):
            self.context = context

    class FakeStarTools:
        @classmethod
        def get_data_dir(cls, _plugin_name):
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir

    api.logger = types.SimpleNamespace(
        error=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None
    )
    event.AstrMessageEvent = object
    event.filter = FakeFilter
    star.Context = object
    star.Star = FakeStar
    star.StarTools = FakeStarTools
    monkeypatch.setitem(sys.modules, "astrbot", astrbot)
    monkeypatch.setitem(sys.modules, "astrbot.api", api)
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event)
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star)


class RecordingService:
    def __init__(self):
        self.status_calls = 0
        self.scan_calls = 0
        self.list_calls = 0

    async def status(self):
        self.status_calls += 1
        return {"source_count": 0, "event_count": 0, "schema_version": 1}

    async def scan_events(self, trigger="manual"):
        self.scan_calls += 1
        return types.SimpleNamespace(
            trigger=trigger,
            source_count=0,
            discovered_count=0,
            upserted_count=0,
            failures=(),
            skipped=False,
        )

    async def list_events(self, limit=20):
        self.list_calls += 1
        return []


@pytest.fixture
def plugin_module(monkeypatch, tmp_path):
    install_fake_astrbot(monkeypatch, tmp_path / "plugin-data")
    sys.modules.pop("main", None)
    import main

    return main


@pytest.mark.asyncio
async def test_plugin_import_lifecycle_and_entrypoint_registration(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"auto_scan_enabled": False})

    await plugin.initialize()
    await plugin.terminate()

    assert plugin_module.LawAssistant.law._fake_command == "law"
    assert plugin_module.LawAssistant.law_status._fake_llm_tool == "law_status"
    assert (
        plugin_module.LawAssistant.law_scan_events._fake_llm_tool == "law_scan_events"
    )
    assert (
        plugin_module.LawAssistant.law_list_events._fake_llm_tool == "law_list_events"
    )


@pytest.mark.asyncio
async def test_command_requires_private_admin_or_operator_and_delegates(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    recording = RecordingService()
    plugin.service = recording

    denied = [item async for item in plugin.law(FakeEvent(private=True, sender_id="7"))]
    group_denied = [
        item async for item in plugin.law(FakeEvent(private=False, sender_id="42"))
    ]
    allowed = [
        item async for item in plugin.law(FakeEvent(private=True, sender_id="42"))
    ]

    assert "没有" in denied[0]
    assert "私聊" in group_denied[0]
    assert "法务助手状态" in allowed[0]
    assert recording.status_calls == 1


@pytest.mark.asyncio
async def test_llm_tools_use_same_service_and_authorization(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    recording = RecordingService()
    plugin.service = recording

    denied = await plugin.law_status(FakeEvent(private=True, sender_id="7"))
    status = await plugin.law_status(FakeEvent(private=True, sender_id="42"))
    scan = await plugin.law_scan_events(FakeEvent(private=True, sender_id="42"))
    events = await plugin.law_list_events(
        FakeEvent(private=True, sender_id="42"), limit=5
    )

    assert "没有" in denied
    assert "source_count" in status
    assert "source_count" in scan
    assert events == "[]"
    assert recording.status_calls == 1
    assert recording.scan_calls == 1
    assert recording.list_calls == 1


@pytest.mark.asyncio
async def test_group_operator_can_bind_current_unified_message_origin(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    response = [
        item
        async for item in plugin.law(
            FakeEvent(
                private=False,
                sender_id="42",
                unified_msg_origin="aiocqhttp:group:100",
                message="/law bind",
            )
        )
    ]

    assert "aiocqhttp:group:100" in response[0]
    assert plugin.storage.list_targets()[0]["unified_msg_origin"] == (
        "aiocqhttp:group:100"
    )
    await plugin.terminate()
