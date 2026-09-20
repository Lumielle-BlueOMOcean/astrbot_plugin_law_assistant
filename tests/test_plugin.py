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
async def test_controlled_document_import_command_archives_multiple_items(
    plugin_module, tmp_path
):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    import_dir = tmp_path / "plugin-data" / "imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    (import_dir / "练习资料.txt").write_text(
        "第1题 单项选择题\n题干一\nA. 一\nB. 二\n答案\n1 A\n"
        "第2题 判断题\n题干二\n答案\n2 对\n",
        encoding="utf-8",
    )
    response = [
        item
        async for item in plugin.law(
            FakeEvent(
                private=True,
                sender_id="42",
                message="/law import 练习资料.txt real_question_candidate",
            )
        )
    ]

    assert '"success": true' in response[0]
    assert '"archived": 2' in response[0]
    assert '"source_id":' in response[0]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_learning_tools_archive_and_search_through_service(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    event = FakeEvent(private=True, sender_id="42")

    archived = await plugin.law_archive_learning_material(
        event,
        raw_text="可复用的案例资料",
        material_type="case",
        title="可复用案例",
        subjects="知识产权",
        structured_json='{"case_summary":"原始事实"}',
    )
    searched = await plugin.law_search_learning_library(
        event, query="可复用案例", material_type="case"
    )

    assert '"success": true' in archived
    assert '"count": 1' in searched
    await plugin.terminate()


@pytest.mark.asyncio
async def test_review_command_is_operator_only_and_reads_persisted_queue(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    denied = [
        item
        async for item in plugin.law(
            FakeEvent(private=True, sender_id="7", message="/law review")
        )
    ]
    allowed = [
        item
        async for item in plugin.law(
            FakeEvent(private=True, sender_id="42", message="/law review")
        )
    ]

    assert "没有" in denied[0]
    assert '"success": true' in allowed[0]
    assert '"count": 0' in allowed[0]
    await plugin.terminate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "reason_fragment"),
    [
        ("real 不存在的方向 多选", "不支持的方向"),
        ("real 刑法 不存在的题型", "不支持的题型"),
        ("不存在的来源 刑法 单选", "不支持的题目来源"),
    ],
)
async def test_question_command_rejects_unknown_explicit_parameters_at_entrypoint(
    plugin_module, arguments, reason_fragment
):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    response = [
        item
        async for item in plugin.law(
            FakeEvent(
                private=True,
                sender_id="42",
                message=f"/law question {arguments}",
            )
        )
    ]

    assert '"error": "invalid_parameter"' in response[0]
    assert reason_fragment in response[0]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_question_import_command_preserves_quoted_windows_path(
    plugin_module,
):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    captured: list[str] = []

    def capture(path: str) -> dict[str, object]:
        captured.append(path)
        return {"success": True, "path": path, "imported_count": 0}

    plugin.service.import_real_questions_file = capture
    windows_path = r"C:\Users\Alice\verified questions.json"
    response = [
        item
        async for item in plugin.law(
            FakeEvent(
                private=True,
                sender_id="42",
                message=f'/law question-import "{windows_path}"',
            )
        )
    ]

    assert captured == [windows_path]
    assert json.loads(response[0])["path"] == windows_path
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


@pytest.mark.asyncio
async def test_plugin_persists_global_rotation_anchor_across_reinitialization(
    plugin_module,
):
    config = {
        "daily_question_enabled": True,
        "daily_question_selection_mode": "rotation",
        "daily_question_rotation_subjects": ["刑法", "民商法"],
    }
    first = plugin_module.LawAssistant(None, config)
    first_anchor = first.storage.get_rotation_anchor("daily_question")
    assert first_anchor is not None
    await first.terminate()

    second = plugin_module.LawAssistant(None, config)
    assert second.storage.get_rotation_anchor("daily_question") == first_anchor
    await second.terminate()
