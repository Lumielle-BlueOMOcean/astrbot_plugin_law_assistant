from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from tests.fakes import FakeEvent, RecordingPublisher


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
    assert plugin.scheduler.enabled is True
    assert plugin.scheduler.scan_task is not None
    await plugin.terminate()

    assert plugin.scheduler.enabled is False
    assert plugin.scheduler.scan_task is None
    assert plugin_module.LawAssistant.law._fake_command == "law"
    assert plugin_module.LawAssistant.law_status._fake_llm_tool == "law_status"
    assert (
        plugin_module.LawAssistant.law_scan_events._fake_llm_tool == "law_scan_events"
    )
    assert (
        plugin_module.LawAssistant.law_list_events._fake_llm_tool == "law_list_events"
    )


def test_question_command_parser_accepts_indefinite_choice_alias(plugin_module):
    assert plugin_module._parse_question_args(["real", "刑法", "不定项选择"]) == (
        "real",
        "刑法",
        "indefinite_choice",
    )


def test_help_text_does_not_show_slash_prefixed_law_commands(plugin_module):
    assert "/law" not in plugin_module.LawAssistant._help_text()


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
async def test_chinese_command_root_uses_same_authorization_and_service_handler(
    plugin_module,
):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    recording = RecordingService()
    plugin.service = recording

    denied = [
        item
        async for item in plugin.law_chinese(
            FakeEvent(private=True, sender_id="7", message="/法务 status")
        )
    ]
    allowed = [
        item
        async for item in plugin.law_chinese(
            FakeEvent(private=True, sender_id="42", message="法务 状态")
        )
    ]
    english = [
        item
        async for item in plugin.law(
            FakeEvent(private=True, sender_id="42", message="/law status")
        )
    ]

    assert "没有" in denied[0]
    assert allowed == english
    assert recording.status_calls == 2
    assert plugin_module.LawAssistant.law_chinese._fake_command == "法务"


def test_chinese_command_aliases_normalize_question_origins_and_session_actions(
    plugin_module,
):
    normalize = plugin_module._normalize_law_command_parts
    assert normalize("法务 真题 刑法 多选") == [
        "/law",
        "question",
        "real",
        "刑法",
        "多选",
    ]
    assert normalize("法务 模拟题 知识产权 案例分析") == [
        "/law",
        "question",
        "mock",
        "知识产权",
        "案例分析",
    ]
    assert normalize("法务 下一页") == ["/law", "next"]
    assert normalize("法务 答案续页") == ["/law", "next-answer"]
    assert normalize("法务 群计划") == ["/law", "plans"]


@pytest.mark.asyncio
async def test_question_command_returns_bounded_text_and_explicit_answer_continuation(
    plugin_module,
):
    plugin = plugin_module.LawAssistant(
        None, {"operator_ids": ["42"], "question_message_max_chars": 300}
    )
    scope = "aiocqhttp:FriendMessage:42"
    opened = plugin.service.open_question_session(
        {
            "available": True,
            "origin": "real",
            "subject": "criminal_law",
            "question_type": "case_analysis",
            "content": {
                "question": "分析甲的行为。",
                "answer": "答案开头：" + "核验答案。" * 180 + "答案结束。",
                "explanation": "解析开头：" + "核验解析。" * 180 + "解析结束。",
            },
        },
        session_origin=scope,
        actor_id="42",
    )
    assert len(opened["text"]) <= 300

    answer = await anext(
        plugin.law(
            FakeEvent(
                sender_id="42",
                unified_msg_origin=scope,
                message="/law answer",
            )
        )
    )
    assert len(answer) <= 300
    assert "【真题" in answer
    assert '"success"' not in answer
    assert "法务 答案续页" in answer

    ambiguous_next = await anext(
        plugin.law(
            FakeEvent(
                sender_id="42",
                unified_msg_origin=scope,
                message="/law next",
            )
        )
    )
    assert "继续查看答案" in ambiguous_next
    assert "不会跳转题面" in ambiguous_next

    next_answer = await anext(
        plugin.law(
            FakeEvent(
                sender_id="42",
                unified_msg_origin=scope,
                message="/law next-answer",
            )
        )
    )
    assert len(next_answer) <= 300
    assert "核验答案。" in next_answer
    repeated_answer = await anext(
        plugin.law(
            FakeEvent(
                sender_id="42",
                unified_msg_origin=scope,
                message="/law answer",
            )
        )
    )
    assert repeated_answer == next_answer
    current_answer = await anext(
        plugin.law(
            FakeEvent(
                sender_id="42",
                unified_msg_origin=scope,
                message="/law current",
            )
        )
    )
    assert "答案页：2/" in current_answer

    explanation = await anext(
        plugin.law(
            FakeEvent(
                sender_id="42",
                unified_msg_origin=scope,
                message="/law explanation",
            )
        )
    )
    assert len(explanation) <= 300
    assert "法务 解析续页" in explanation
    next_explanation = await anext(
        plugin.law(
            FakeEvent(
                sender_id="42",
                unified_msg_origin=scope,
                message="/law next-explanation",
            )
        )
    )
    assert len(next_explanation) <= 300
    assert "核验解析。" in next_explanation
    await plugin.terminate()


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
async def test_question_command_and_llm_tools_use_answer_gated_sessions(plugin_module):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})

    async def fixed_question(*args, **kwargs):
        return {
            "available": True,
            "origin": "mock",
            "subject": "criminal_law",
            "question_type": "single_choice",
            "content": {
                "question": "题干公开部分",
                "options": ["A. 甲", "B. 乙"],
                "answer": "A",
                "explanation": "解析仅主动请求后显示",
            },
        }

    plugin.service.generate_question = fixed_question
    event = FakeEvent(
        private=True,
        sender_id="42",
        unified_msg_origin="aiocqhttp:FriendMessage:42",
        message="/law question mock 刑法 单选",
    )
    opened = [item async for item in plugin.law(event)]
    assert "题干公开部分" in opened[0]
    assert "A. 甲" in opened[0]
    assert "解析仅主动请求后显示" not in opened[0]
    assert '"session_id"' not in opened[0]

    denied_reveal = await plugin.law_question_session(
        FakeEvent(
            private=True,
            sender_id="7",
            unified_msg_origin=event.unified_msg_origin,
        ),
        action="answer",
    )
    assert "没有" in denied_reveal
    assert "解析仅主动请求后显示" not in denied_reveal

    answer = await plugin.law_question_session(event, action="answer")
    assert "参考答案" in answer and "A" in answer
    assert "解析仅主动请求后显示" not in answer
    explanation = await plugin.law_question_session(event, action="explanation")
    assert "解析仅主动请求后显示" in explanation
    assert (
        "law_question_session"
        == plugin_module.LawAssistant.law_question_session._fake_llm_tool
    )

    tool_result = json.loads(
        await plugin.law_generate_question(
            event,
            subject="刑法",
            origin="mock",
            question_type="single_choice",
        )
    )
    assert tool_result["success"] is True
    assert "题干公开部分" in tool_result["text"]
    assert "解析仅主动请求后显示" not in json.dumps(tool_result, ensure_ascii=False)
    tool_answer = json.loads(await plugin.law_question_session(event, action="answer"))
    assert "参考答案" in tool_answer["text"]
    assert "解析仅主动请求后显示" not in tool_answer["text"]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_study_tool_keeps_candidate_identity_pending_and_answer_gated(
    plugin_module,
):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    event = FakeEvent(
        private=True,
        sender_id="42",
        unified_msg_origin="aiocqhttp:FriendMessage:42",
    )
    archived = await plugin.service.archive_learning_material(
        raw_text="合成候选题原文",
        material_type="real_question_candidate",
        title="候选题 fixture",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "stem": "根据合成案情分析甲的责任。",
                "question_type": "case_analysis",
                "answer": "仅手动揭晓的参考答案",
                "explanation": "仅手动揭晓的解析",
            },
            ensure_ascii=False,
        ),
        created_by="42",
        session_origin=event.unified_msg_origin,
    )
    real_selection = await plugin.service.generate_question(
        origin="real", subject="刑法", session_origin=event.unified_msg_origin
    )
    assert real_selection["available"] is False
    await plugin.service.bind_target("aiocqhttp:group:study", "学习群")

    opened = json.loads(await plugin.law_get_learning_item(event, archived["item_id"]))
    assert opened["success"] is True
    assert "待人工核验" in opened["identity"]
    assert "【模拟题" not in opened["text"]
    assert "仅手动揭晓" not in opened["text"]
    assert "仅手动揭晓的参考答案" not in json.dumps(opened, ensure_ascii=False)
    assert "仅手动揭晓的解析" not in json.dumps(opened, ensure_ascii=False)
    assert opened["content_ref"]
    preview = await plugin.service.prepare_publish_question(
        content_ref=opened["content_ref"],
        target_selectors=["学习群"],
        session_origin=event.unified_msg_origin,
        actor_id="42",
    )
    assert preview["ready"] is True
    assert preview["preview"] == opened["text"]
    revealed = json.loads(await plugin.law_question_session(event, action="answer"))
    assert "仅手动揭晓的参考答案" in revealed["text"]
    updated = json.loads(
        await plugin.law_update_learning_item(
            event,
            archived["item_id"],
            explanation="SECRET_UPDATED_EXPLANATION",
        )
    )
    assert updated["success"] is True
    assert "SECRET_UPDATED_EXPLANATION" not in json.dumps(updated, ensure_ascii=False)
    assert (
        "law_study_question"
        == plugin_module.LawAssistant.law_study_question._fake_llm_tool
    )
    await plugin.terminate()


@pytest.mark.asyncio
async def test_published_group_question_session_is_reachable_only_in_exact_scope(
    plugin_module,
):
    plugin = plugin_module.LawAssistant(None, {"operator_ids": ["42"]})
    group_a = "aiocqhttp:GroupMessage:group-a"
    group_b = "aiocqhttp:GroupMessage:group-b"
    private = "aiocqhttp:FriendMessage:7"
    publisher = RecordingPublisher()
    plugin.service.publisher = publisher

    async def fixed_question(*args, **kwargs):
        return {
            "available": True,
            "origin": "mock",
            "subject": "criminal_law",
            "question_type": "single_choice",
            "content": {
                "question": "GROUP_A_PUBLIC_STEM",
                "options": ["A. 甲", "B. 乙"],
                "answer": "GROUP_A_ANSWER_SECRET",
                "explanation": "GROUP_A_EXPLANATION_SECRET",
            },
        }

    plugin.service.generate_question = fixed_question
    target = await plugin.service.bind_target(group_a, "法硕一群")
    preview = await plugin.service.prepare_publish_question(
        origin="mock",
        subject="刑法",
        question_type="single_choice",
        target_selectors=["法硕一群"],
        actor_id="42",
    )
    assert preview["ready"] is True
    assert "GROUP_A_ANSWER_SECRET" not in preview["preview"]
    assert (await plugin.service.confirm_publish(preview["token"], actor_id="42"))[
        "published_count"
    ] == 1
    assert publisher.calls == [(group_a, preview["preview"])]
    assert plugin.service.question_sessions.get_active(group_a) is not None
    assert plugin.service.question_sessions.get_active(group_b) is None

    async def command(text, *, origin, sender="7", admin=False, is_private=False):
        return await anext(
            plugin.law(
                FakeEvent(
                    private=is_private,
                    admin=admin,
                    sender_id=sender,
                    message=text,
                    unified_msg_origin=origin,
                )
            )
        )

    group_current = await command("/law current", origin=group_a)
    assert "当前题目" in group_current
    group_prompt = await command("/law next", origin=group_a)
    assert "本题内容已展示完毕" in group_prompt

    tool_event = FakeEvent(
        private=False,
        sender_id="7",
        unified_msg_origin=group_a,
    )
    for action, expected_secret in (
        ("current", "当前题目"),
        ("next", "本题内容已展示完毕"),
        ("answer", "GROUP_A_ANSWER_SECRET"),
        ("explanation", "GROUP_A_EXPLANATION_SECRET"),
    ):
        result = await plugin.law_question_session(tool_event, action=action)
        assert expected_secret in result

    group_answer = await command("/law answer", origin=group_a)
    assert "GROUP_A_ANSWER_SECRET" in group_answer
    assert "GROUP_A_EXPLANATION_SECRET" not in group_answer
    group_explanation = await command("/law explanation", origin=group_a)
    assert "GROUP_A_EXPLANATION_SECRET" in group_explanation

    for origin, is_private in ((group_b, False), (private, True)):
        for action in ("current", "answer", "explanation"):
            event = FakeEvent(
                private=is_private,
                sender_id="7",
                unified_msg_origin=origin,
            )
            command_result = await command(
                f"/law {action}", origin=origin, is_private=is_private
            )
            tool_result = await plugin.law_question_session(event, action=action)
            for result in (command_result, tool_result):
                assert "GROUP_A_ANSWER_SECRET" not in result
                assert "GROUP_A_EXPLANATION_SECRET" not in result
                if origin == group_b:
                    assert "没有进行中的题目" in result

    for text in ("/law status", "/law question", "/law study 1", "/law close"):
        denied = await command(text, origin=group_a)
        assert "operator 权限" in denied or "私聊" in denied
        assert "GROUP_A_ANSWER_SECRET" not in denied
    denied_close_tool = await plugin.law_question_session(tool_event, action="close")
    assert "operator 权限" in denied_close_tool

    closed = await command("/law close", origin=group_a, sender="42", admin=True)
    assert "已结束当前题目会话" in closed
    assert (
        plugin.service.question_sessions.get_active(target["unified_msg_origin"])
        is None
    )
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
    assert plugin.scheduler.enabled is False
    await plugin.service.bind_target("aiocqhttp:group:100", "一群")
    assert plugin.scheduler.due_task is None
    assert plugin.scheduler.due_enabled is False

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
