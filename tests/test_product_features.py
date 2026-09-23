from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from content import format_question_content
from learning_service import LearningService
from models import CaseItem
from service import LawAssistantService
from storage import SQLiteStorage
from tests.fakes import RecordingPublisher


class FakeLearningLLM:
    async def generate_json(self, prompt, *, session_origin=None):
        if "最高司法机关" in prompt:
            return {
                "case_summary": "官方案例摘要",
                "issues": ["争议焦点"],
                "reasoning": "官方要旨",
            }
        return {
            "question": "下列说法正确的是？",
            "options": ["A", "B", "C", "D"],
            "answer": "A",
            "explanation": "学习解析",
        }


def _real_question() -> dict[str, object]:
    return {
        "source_name": "用户合法取得题库",
        "exam_name": "法硕真题示例卷",
        "exam_year": "2024",
        "source_locator": "第一卷第 1 题",
        "source_url": "https://example.test/real/1",
        "subject": "知识产权",
        "question_type": "多选",
        "stem": "下列哪些属于作品？",
        "options": ["A", "B", "C", "D"],
        "answer": ["A"],
        "answer_source": "official",
        "verification_status": "verified",
    }


@pytest.mark.asyncio
async def test_question_selection_preserves_real_mock_and_constraints(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    learning = LearningService(storage, FakeLearningLLM())
    learning.import_real_questions([_real_question()])

    real = await learning.generate_question(
        origin="real", subject="知识产权", question_type="multiple_choice"
    )
    assert real["available"] is True
    assert real["origin"] == "real"
    assert real["source_url"] == "https://example.test/real/1"
    assert "【真题｜知识产权｜多项选择题】" in format_question_content(real)

    missing = await learning.generate_question(
        origin="real", subject="刑法", question_type="multiple_choice"
    )
    assert missing["available"] is False

    mock = await learning.generate_question(
        origin="mock", subject="刑法", question_type="multiple_choice"
    )
    assert mock["origin"] == "mock"
    assert mock["label"] == "模拟题"
    assert "非官方考试真题" in mock["source_note"]
    assert "【模拟题｜刑法｜多项选择题】" in format_question_content(mock)


@pytest.mark.asyncio
async def test_case_direction_filter_and_random_question_fallback_are_explicit(
    tmp_path,
) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    storage.upsert_case_item(
        CaseItem(
            source_key="court",
            source_item_key="1",
            title="知产案例",
            source_url="https://example.test/case/1",
            authority="最高人民法院",
            raw_text="官方事实",
            content_hash="case-1",
            subjects=("intellectual_property",),
        )
    )
    learning = LearningService(storage, FakeLearningLLM())
    case = await learning.daily_case(subject="知产")
    assert case["available"] is True
    assert (await learning.daily_case(subject="刑法"))["available"] is False

    fallback = await learning.generate_question(
        origin="random", subject="刑法", question_type="multiple_choice"
    )
    assert fallback["origin"] == "mock"
    assert "真题库没有" in fallback["selection_note"]


@pytest.mark.asyncio
async def test_publish_preview_locks_body_and_explicit_targets(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    learning = LearningService(storage, FakeLearningLLM())
    service = LawAssistantService(
        storage,
        learning_service=learning,
        publisher=publisher,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    first = await service.bind_target("aiocqhttp:group:1", "一群")
    await service.bind_target("aiocqhttp:group:2", "二群")

    preview = await service.prepare_publish_question(
        origin="mock",
        subject="刑法",
        question_type="single_choice",
        target_selectors=["一群"],
    )
    assert preview["ready"] is True
    body = preview["preview"]
    assert "下列说法正确的是？" in body
    assert "参考答案" not in body
    assert "学习解析" not in body
    result = await service.confirm_publish(preview["token"])
    assert result["published_count"] == 1
    assert publisher.calls == [(first["unified_msg_origin"], body)]
    active = service.question_sessions.get_active(first["unified_msg_origin"])
    assert active is not None
    assert "参考答案" not in publisher.calls[0][1]
    assert (await service.confirm_publish(preview["token"]))["success"] is False


@pytest.mark.asyncio
async def test_publish_token_is_bound_to_preparing_operator(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    service = LawAssistantService(
        storage,
        learning_service=LearningService(storage, FakeLearningLLM()),
        publisher=publisher,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    await service.bind_target("aiocqhttp:group:1", "一群")
    preview = await service.prepare_publish_question(
        origin="mock",
        subject="刑法",
        question_type="single_choice",
        target_selectors=["一群"],
        actor_id="42",
    )
    assert (
        "当前操作者"
        in (await service.confirm_publish(preview["token"], actor_id="7"))["reason"]
    )
    assert (await service.confirm_publish(preview["token"], actor_id="42"))["success"]


@pytest.mark.asyncio
async def test_failed_manual_question_delivery_does_not_open_target_session(tmp_path):
    class FailedPublisher:
        async def publish_text(self, destination: str, text: str) -> bool:
            return False

    storage = SQLiteStorage(tmp_path / "failed-question-publish.sqlite3")
    learning = LearningService(storage, FakeLearningLLM())
    service = LawAssistantService(
        storage,
        learning_service=learning,
        publisher=FailedPublisher(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    target = await service.bind_target("aiocqhttp:group:failure", "失败测试群")
    preview = await service.prepare_publish_question(
        origin="mock",
        subject="刑法",
        question_type="single_choice",
        target_selectors=["失败测试群"],
        actor_id="42",
    )
    result = await service.confirm_publish(preview["token"], actor_id="42")
    assert result == {"success": False, "published_count": 0, "failed_count": 1}
    assert service.question_sessions.get_active(target["unified_msg_origin"]) is None


@pytest.mark.asyncio
async def test_partial_manual_question_delivery_opens_only_successful_target_sessions(
    tmp_path,
):
    class OneTargetPublisher:
        async def publish_text(self, destination: str, text: str) -> bool:
            return destination.endswith(":success")

    storage = SQLiteStorage(tmp_path / "partial-question-publish.sqlite3")
    service = LawAssistantService(
        storage,
        learning_service=LearningService(storage, FakeLearningLLM()),
        publisher=OneTargetPublisher(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    success_target = await service.bind_target("aiocqhttp:group:success", "成功群")
    failed_target = await service.bind_target("aiocqhttp:group:failed", "失败群")
    preview = await service.prepare_publish_question(
        origin="mock",
        subject="刑法",
        question_type="single_choice",
        target_selectors=["成功群", "失败群"],
        actor_id="42",
    )
    result = await service.confirm_publish(preview["token"], actor_id="42")
    assert result["published_count"] == 1
    assert result["failed_count"] == 1
    assert service.question_sessions.get_active(success_target["unified_msg_origin"])
    assert (
        service.question_sessions.get_active(failed_target["unified_msg_origin"])
        is None
    )


@pytest.mark.asyncio
async def test_daily_plans_are_independent_and_idempotent(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()

    class Learning:
        async def daily_case(self, *, subject=None, **kwargs):
            return {
                "available": True,
                "subject": subject,
                "content": {"case_summary": subject or "随机案例"},
            }

        async def generate_question(self, *, subject="", origin="random", **kwargs):
            return {
                "available": True,
                "origin": "mock",
                "subject": subject,
                "question_type": "single_choice",
                "content": {
                    "question": "题干",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析",
                },
            }

    config = SimpleNamespace(
        timezone="Asia/Shanghai",
        auto_scan_enabled=False,
        law_update_enabled=False,
        daily_case_enabled=False,
        daily_question_enabled=False,
    )
    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=config,
        clock=lambda: datetime(2026, 9, 21, 1, 0, tzinfo=ZoneInfo("UTC")),
    )
    target = await service.bind_target("aiocqhttp:group:1", "一群")
    from daily_plans import DailyPlan

    storage.upsert_daily_plan(
        DailyPlan(
            "daily_case",
            enabled=True,
            selection_mode="rotation",
            rotation_subjects=("intellectual_property", "economic_law"),
            rotation_start_date="2026-09-21",
        )
    )
    storage.upsert_daily_plan(
        DailyPlan("daily_question", enabled=True, question_origin="mock"),
    )

    first = await service.run_scheduled_jobs(
        now=datetime(2026, 9, 21, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    second = await service.run_scheduled_jobs(
        now=datetime(2026, 9, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert first["daily_case_sent"] == 1
    assert first["daily_question_sent"] == 1
    assert second["daily_case_sent"] == 0
    assert second["daily_question_sent"] == 0
    assert len(storage.list_daily_contents()) == 2
    assert target["id"] == 1
    question_message = next(text for _, text in publisher.calls if "题干：题干" in text)
    assert "参考答案：A" not in question_message
    assert "解析：解析" not in question_message
    assert (
        service.question_sessions.get_active(target["unified_msg_origin"]) is not None
    )


@pytest.mark.asyncio
async def test_daily_plan_change_requires_confirmation_and_preserves_other_task(
    tmp_path,
) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: datetime(2026, 9, 19, 0, 0, tzinfo=ZoneInfo("UTC")),
    )
    await service.bind_target("aiocqhttp:group:1", "一群")
    from daily_plans import DailyPlan

    storage.upsert_daily_plan(
        DailyPlan(
            "daily_case",
            enabled=True,
            fixed_subject="judicial_practice",
            selection_mode="fixed",
        ),
        1,
    )
    preview = await service.prepare_daily_plan_update(
        target_selectors=["一群"],
        content_type="daily_question",
        changes={
            "enabled": True,
            "selection_mode": "rotation",
            "rotation_subjects": ["economic_law", "civil_commercial"],
            "rotation_start_date": "2026-09-21",
        },
        actor_id="42",
    )
    assert preview["ready"] is True
    assert storage.get_daily_plan(1, "daily_question") is None
    assert "economic_law" in str(preview["plans"])

    assert (
        "当前操作者"
        in (await service.confirm_daily_plan_update(preview["token"], actor_id="7"))[
            "reason"
        ]
    )
    assert (await service.confirm_daily_plan_update(preview["token"], actor_id="42"))[
        "success"
    ]
    assert storage.get_daily_plan(1, "daily_case").fixed_subject == "judicial_practice"
    assert storage.get_daily_plan(1, "daily_question").rotation_subjects == (
        "economic_law",
        "civil_commercial",
    )


@pytest.mark.asyncio
async def test_missing_case_subject_does_not_block_daily_question(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()

    class Learning:
        async def daily_case(self, *, subject=None, **kwargs):
            return {"available": False, "reason": "暂无匹配方向的官方案例"}

        async def generate_question(self, *, subject="", **kwargs):
            return {
                "available": True,
                "origin": "mock",
                "subject": subject,
                "question_type": "true_false",
                "content": {
                    "question": "题干",
                    "answer": "正确",
                    "explanation": "解析",
                },
            }

    from daily_plans import DailyPlan

    storage.upsert_daily_plan(
        DailyPlan(
            "daily_case",
            enabled=True,
            selection_mode="fixed",
            fixed_subject="intellectual_property",
        )
    )
    storage.upsert_daily_plan(
        DailyPlan(
            "daily_question",
            enabled=True,
            selection_mode="fixed",
            fixed_subject="criminal_law",
            question_origin="mock",
            question_type="true_false",
        )
    )
    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=SimpleNamespace(
            timezone="Asia/Shanghai",
            auto_scan_enabled=False,
            daily_case_enabled=False,
            daily_question_enabled=False,
            law_update_enabled=False,
        ),
    )
    await service.bind_target("aiocqhttp:group:1", "一群")
    result = await service.run_scheduled_jobs(
        now=datetime(2026, 9, 21, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert result["daily_case_sent"] == 0
    assert result["daily_question_sent"] == 1
    records = storage.list_daily_contents()
    assert any(
        item["content_type"] == "daily_case" and item["status"] == "skipped"
        for item in records
    )
    assert any(
        item["content_type"] == "daily_question" and item["status"] == "sent"
        for item in records
    )
