from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from learning_service import LearningService
from models import CaseItem, EventDate, SourceDocument
from scheduler import LawAssistantScheduler
from service import LawAssistantService
from storage import SQLiteStorage
from tests.fakes import FakeAdapter, RecordingPublisher


class LearningLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(self, prompt, *, session_origin=None):
        self.calls += 1
        if "最高司法机关" in prompt:
            return {
                "case_summary": f"案例摘要 {self.calls}",
                "issues": ["争议焦点"],
                "reasoning": "官方要旨",
            }
        return {
            "question": f"题干 {self.calls}",
            "options": ["A", "B"],
            "answer": "A",
            "explanation": "解析",
        }


@pytest.mark.asyncio
async def test_scheduler_wakes_after_confirming_first_enabled_daily_plan(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()

    class Learning:
        async def generate_question(self, **kwargs):
            return {
                "available": True,
                "origin": "mock",
                "subject": kwargs.get("subject"),
                "question_type": "true_false",
                "content": {
                    "question": "今天的题目",
                    "answer": "正确",
                    "explanation": "解析",
                },
            }

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
    target = await service.bind_target("aiocqhttp:group:1", "一群")
    sleeping = asyncio.Event()
    scheduler = LawAssistantScheduler(
        service,
        enabled=False,
        interval_minutes=60,
        sleep=lambda _seconds: sleeping.wait(),
    )

    async def wake_scheduler():
        scheduler.enabled = True
        await scheduler.start()

    service.set_scheduler_wakeup(wake_scheduler)
    preview = await service.prepare_daily_plan_update(
        target_selectors=["一群"],
        content_type="daily_question",
        changes={"enabled": True, "question_origin": "mock"},
        actor_id="42",
    )
    assert preview["ready"] is True
    assert (await service.confirm_daily_plan_update(preview["token"], actor_id="42"))[
        "success"
    ]
    task = scheduler.task
    assert task is not None
    assert scheduler.task is task

    result = await service.run_scheduled_jobs(
        now=datetime(2026, 9, 21, 8, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert result["daily_question_sent"] == 1
    assert publisher.calls[0][0] == target["unified_msg_origin"]
    second_target = await service.bind_target("aiocqhttp:group:2", "二群")
    from daily_plans import DailyPlan

    storage.upsert_daily_plan(
        DailyPlan("daily_question", enabled=True, question_origin="mock"),
        second_target["id"],
    )
    running_task = scheduler.task
    disable_preview = await service.prepare_daily_plan_update(
        target_selectors=["一群"],
        content_type="daily_question",
        changes={"enabled": False},
        actor_id="42",
    )
    assert (
        await service.confirm_daily_plan_update(disable_preview["token"], actor_id="42")
    )["success"]
    assert scheduler.task is running_task
    await scheduler.stop()


@pytest.mark.asyncio
async def test_case_manual_subject_survives_updated_official_source(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    first_document = SourceDocument(
        source_key="court_cases",
        source_item_key="case-1",
        url="https://court.example/case-1",
        title="旧标题",
        content="旧正文",
        fetched_at="2026-09-17T00:00:00+00:00",
    )
    second_document = SourceDocument(
        source_key="court_cases",
        source_item_key="case-1",
        url="https://court.example/case-1",
        title="新标题",
        content="更新后的官方正文",
        fetched_at="2026-09-18T00:00:00+00:00",
    )

    class Extractor:
        async def extract(self, document):
            return CaseItem(
                source_key=document.source_key,
                source_item_key=document.source_item_key,
                title=document.title,
                source_url=document.url,
                authority="最高人民法院",
                raw_text=document.content,
                content_hash=document.content_hash,
                discovered_at=document.fetched_at,
                last_seen_at=document.fetched_at,
            )

    adapter = FakeAdapter("court_cases", [first_document])
    service = LawAssistantService(storage, case_sources=[(adapter, Extractor())])
    await service.scan_cases()
    case_id = storage.list_case_items()[0].id
    assert case_id is not None
    assert (await service.set_case_subjects(case_id, ["知识产权"]))["success"] is True

    adapter.documents = [second_document]
    await service.scan_cases()
    updated = storage.get_case_item(case_id)
    assert updated is not None
    assert updated.title == "新标题"
    assert "intellectual_property" in updated.subjects
    filtered = await LearningService(storage, LearningLLM()).daily_case(
        subject="知识产权"
    )
    assert filtered["available"] is True


@pytest.mark.asyncio
async def test_recent_question_reference_reuses_exact_body_and_owner_session(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    llm = LearningLLM()
    service = LawAssistantService(
        storage,
        learning_service=__import__("learning_service").LearningService(storage, llm),
        publisher=publisher,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    await service.bind_target("aiocqhttp:group:1", "一群")
    first = await service.generate_question(
        subject="刑法",
        origin="mock",
        question_type="single_choice",
        actor_id="42",
        session_origin="aiocqhttp:private:42",
    )
    assert first["content_ref"]
    preview = await service.prepare_publish_question(
        content_ref=first["content_ref"],
        target_selectors=["一群"],
        actor_id="42",
        session_origin="aiocqhttp:private:42",
    )
    assert preview["ready"] is True
    assert "题干 1" in preview["preview"]
    assert llm.calls == 1
    denied = await service.prepare_publish_question(
        content_ref=first["content_ref"],
        target_selectors=["一群"],
        actor_id="7",
        session_origin="aiocqhttp:private:7",
    )
    assert denied["ready"] is False
    assert "引用" in denied["reason"] or "操作者" in denied["reason"]


@pytest.mark.asyncio
async def test_recent_case_reference_reuses_exact_body(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    storage.upsert_case_item(
        CaseItem(
            source_key="court",
            source_item_key="case-1",
            title="官方案例",
            source_url="https://court.example/case-1",
            authority="最高人民法院",
            raw_text="官方事实",
            content_hash="case-1",
            subjects=("intellectual_property",),
        )
    )
    llm = LearningLLM()
    service = LawAssistantService(
        storage,
        learning_service=__import__("learning_service").LearningService(storage, llm),
        publisher=RecordingPublisher(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    await service.bind_target("aiocqhttp:group:1", "一群")
    first = await service.get_daily_case(
        subject="知识产权",
        actor_id="42",
        session_origin="aiocqhttp:private:42",
    )
    preview = await service.prepare_publish_case(
        content_ref=first["content_ref"],
        target_selectors=["一群"],
        actor_id="42",
        session_origin="aiocqhttp:private:42",
    )
    assert preview["ready"] is True
    assert "案例摘要 1" in preview["preview"]
    assert llm.calls == 1


def test_explicit_invalid_question_parameters_do_not_fall_back_to_random(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    result = __import__("asyncio").run(
        service.generate_question(
            subject="不存在的方向", origin="unsupported", question_type="unsupported"
        )
    )
    assert result["available"] is False
    assert result["error"] == "invalid_parameter"


@pytest.mark.asyncio
async def test_rotation_without_start_date_defaults_to_local_today_and_validates_subjects(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: datetime(2026, 9, 21, 16, 0, tzinfo=ZoneInfo("UTC")),
    )
    await service.bind_target("aiocqhttp:group:1", "一群")
    preview = await service.prepare_daily_plan_update(
        target_selectors=["一群"],
        content_type="daily_case",
        changes={
            "enabled": True,
            "selection_mode": "rotation",
            "rotation_subjects": ["知识产权", "经济法"],
        },
        actor_id="42",
    )
    assert preview["ready"] is True
    assert preview["plans"][0]["plan"]["rotation_start_date"] == "2026-09-22"
    with pytest.raises(ValueError, match="rotation_subjects"):
        await service.prepare_daily_plan_update(
            target_selectors=["一群"],
            content_type="daily_case",
            changes={
                "enabled": True,
                "selection_mode": "rotation",
                "rotation_subjects": [],
                "rotation_start_date": "2026-09-22",
            },
            actor_id="42",
        )
    with pytest.raises(ValueError, match="选择模式"):
        await service.prepare_daily_plan_update(
            target_selectors=["一群"],
            content_type="daily_case",
            changes={"selection_mode": "not-a-mode"},
            actor_id="42",
        )
    with pytest.raises(ValueError, match="题目来源"):
        await service.prepare_daily_plan_update(
            target_selectors=["一群"],
            content_type="daily_question",
            changes={"question_origin": "not-an-origin"},
            actor_id="42",
        )


@pytest.mark.asyncio
async def test_deadline_reminder_uses_configured_local_calendar_date(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    deadline = datetime(2026, 10, 8, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    from tests.fakes import make_event

    event = make_event()
    event.dates = (
        EventDate(
            kind="registration_deadline",
            datetime=deadline,
            timezone="Asia/Shanghai",
            label="报名截止",
            evidence_text="官方原文",
        ),
    )
    storage.upsert_event(event)
    service = LawAssistantService(
        storage,
        publisher=RecordingPublisher(),
        config=SimpleNamespace(
            timezone="Asia/Shanghai",
            deadline_reminder_days=(7,),
            deadline_same_day_enabled=False,
        ),
    )
    await service.bind_target("aiocqhttp:group:1", "一群")
    assert (
        await service.check_deadline_reminders(
            now=datetime(2026, 10, 1, 15, 30, tzinfo=ZoneInfo("UTC"))
        )
        == 1
    )
