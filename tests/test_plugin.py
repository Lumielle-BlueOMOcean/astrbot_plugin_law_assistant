from __future__ import annotations

import json
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
    plugin = plugin_module.LawAssistant(
        None, {"auto_scan_enabled": False, "daily_case_enabled": True}
    )

    await plugin.initialize()
    await plugin.terminate()

    assert plugin.scheduler.enabled is True
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
                message="/law bind 法硕一群",
            )
        )
    ]

    assert "aiocqhttp:group:100" in response[0]
    assert "法硕一群" in response[0]
    assert plugin.storage.list_targets()[0]["unified_msg_origin"] == (
        "aiocqhttp:group:100"
    )
    assert plugin.storage.list_targets()[0]["label"] == "法硕一群"
    renamed = [
        item
        async for item in plugin.law(
            FakeEvent(
                private=True,
                sender_id="42",
                message="/law rename 法硕一群 法硕学习群",
            )
        )
    ]
    assert "法硕学习群" in renamed[0]
    assert plugin.storage.list_targets()[0]["label"] == "法硕学习群"
    await plugin.terminate()


@pytest.mark.asyncio
async def test_question_import_command_accepts_quoted_path_and_reports_inventory(
    plugin_module, tmp_path
):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    question_path = tmp_path / "verified questions with spaces.json"
    question_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "source_name": "合法取得题库",
                        "exam_name": "示例考试",
                        "exam_year": "2024",
                        "source_locator": "第1题",
                        "source_url": "https://example.test/q/1",
                        "subject": "刑法",
                        "question_type": "单选",
                        "stem": "下列说法正确的是？",
                        "options": ["A", "B"],
                        "answer": "A",
                        "answer_source": "official",
                        "verification_status": "verified",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = [
        item
        async for item in plugin.law(
            FakeEvent(
                private=True,
                sender_id="42",
                message=f'/law question-import "{question_path}"',
            )
        )
    ]

    assert '"success": true' in response[0]
    assert '"imported_count": 1' in response[0]
    assert '"count": 1' in response[0]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_confirming_daily_plan_wakes_initially_disabled_plugin_scheduler(
    plugin_module,
):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    await plugin.service.bind_target("aiocqhttp:group:100", "一群")
    assert plugin.scheduler.enabled is False

    event = FakeEvent(private=True, sender_id="42")
    preview = json.loads(
        await plugin.law_prepare_daily_plan_update(
            event,
            target="一群",
            content_type="daily_question",
            enabled="true",
            question_origin="mock",
        )
    )
    assert preview["ready"] is True
    confirmed = json.loads(
        await plugin.law_confirm_daily_plan_update(event, preview["token"])
    )
    assert confirmed["success"] is True
    assert plugin.scheduler.enabled is True
    assert plugin.scheduler.task is not None
    await plugin.terminate()
