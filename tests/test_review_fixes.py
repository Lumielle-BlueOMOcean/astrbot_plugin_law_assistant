from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from learning_service import LearningService
from library_models import LibrarySource
from library_repository import LibraryRepository
from library_service import LibraryService
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
async def test_official_case_collection_is_split_into_independent_library_cases(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))
    document = SourceDocument(
        source_key="court_cases",
        source_item_key="collection-1",
        url="https://court.example/collection-1",
        title="三个典型案例",
        content=(
            "导语：本期发布三个案例。\n"
            "案例一：知识产权纠纷\n甲公司主张商标侵权，法院依法裁判。\n"
            "案例二：合同纠纷\n乙公司与丙公司签订合同，法院认定违约。\n"
            "案例三：司法实务\n检察机关依法审查起诉并提出建议。"
        ),
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
            )

    service = LawAssistantService(
        storage,
        case_sources=[(FakeAdapter("court_cases", [document]), Extractor())],
        library_service=library,
    )
    result = await service.scan_cases()

    assert result["failures"] == []
    official = await library.list_official_cases()
    assert official["count"] == 3
    assert all(item["identity"] == "official_case" for item in official["items"])
    assert all("导语" not in item["title"] for item in official["items"])
    legacy_case = storage.list_case_items()[0]
    tagged = await service.set_case_subjects(legacy_case.id, ["知识产权"])
    assert tagged["official_case_items_updated"] == 3
    filtered = await library.list_official_cases(subject="知识产权")
    assert filtered["count"] == 3
    storage.close()


@pytest.mark.asyncio
async def test_official_case_reprocesses_when_segmentation_version_changes(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))
    document = SourceDocument(
        source_key="court_cases",
        source_item_key="collection-reprocess",
        url="https://court.example/collection-reprocess",
        title="三个典型案例",
        content=(
            "导语\n"
            "案例一：知识产权纠纷\n甲公司主张商标侵权，法院依法裁判。\n"
            "案例二：合同纠纷\n乙公司与丙公司签订合同，法院认定违约。\n"
            "案例三：司法实务\n检察机关依法审查起诉并提出建议。"
        ),
        fetched_at="2026-09-18T00:00:00+00:00",
        metadata={
            "case_segmentation_version": "1",
            "case_processing_status": "success",
        },
    )
    old_source = LibrarySource(
        source_kind="official_article",
        title=document.title,
        raw_text=document.content,
        source_url=document.url,
        content_hash=document.content_hash,
        created_at=datetime(2026, 9, 18, tzinfo=ZoneInfo("Asia/Shanghai")),
        created_by="source:court_cases",
        session_origin="source:court_cases",
        metadata={
            "adapter_key": "court_cases",
            "source_item_key": document.source_item_key,
            "case_segmentation_version": "1",
        },
    )
    old = await library.archive_official_cases(
        source=old_source,
        candidates=[
            {
                "title": "旧规则合并条目",
                "locator": "文章全文",
                "raw_text": document.content,
                "structured": {"case_summary": "旧规则错误合并"},
            }
        ],
        adapter_key="court_cases",
        source_item_key=document.source_item_key,
        segmentation_version="1",
    )
    storage.upsert_source_document(document)

    class Extractor:
        calls = 0

        async def extract(self, source_document):
            self.calls += 1
            return CaseItem(
                source_key=source_document.source_key,
                source_item_key=source_document.source_item_key,
                title=source_document.title,
                source_url=source_document.url,
                authority="最高人民法院",
                raw_text=source_document.content,
                content_hash=source_document.content_hash,
            )

    extractor = Extractor()
    service = LawAssistantService(
        storage,
        case_sources=[(FakeAdapter("court_cases", [document]), extractor)],
        library_service=library,
    )

    first = await service.scan_cases()
    assert first["failures"] == []
    assert extractor.calls == 1
    official = await library.list_official_cases()
    assert official["count"] == 3
    old_bundle = await library.get_learning_item(old["items"][0]["item_id"])
    assert old_bundle["item"]["active"] is False

    await service.scan_cases()
    assert extractor.calls == 1
    assert (await library.list_official_cases())["count"] == 3
    storage.close()


@pytest.mark.asyncio
async def test_official_case_manual_subject_survives_resegmentation(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))
    first_document = SourceDocument(
        source_key="court_cases",
        source_item_key="single-reprocess",
        url="https://court.example/single-reprocess",
        title="张三商标侵权案",
        content="张三商标侵权案\n基本案情：甲公司主张商标侵权。\n裁判要旨：法院依法裁判。",
        fetched_at="2026-09-18T00:00:00+00:00",
    )
    second_document = SourceDocument(
        source_key=first_document.source_key,
        source_item_key=first_document.source_item_key,
        url=first_document.url,
        title=first_document.title,
        content="张三商标侵权案\n基本案情：甲公司主张商标侵权，补充证据。\n裁判要旨：法院依法裁判。",
        fetched_at="2026-09-19T00:00:00+00:00",
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
            )

    adapter = FakeAdapter("court_cases", [first_document])
    service = LawAssistantService(
        storage,
        case_sources=[(adapter, Extractor())],
        library_service=library,
    )
    await service.scan_cases()
    legacy = storage.list_case_items()[0]
    tagged = await service.set_case_subjects(legacy.id, ["知识产权"])
    assert tagged["official_case_items_updated"] == 1

    adapter.documents = [second_document]
    await service.scan_cases()

    official = await library.list_official_cases(subject="知识产权")
    assert official["count"] == 1
    assert official["items"][0]["subjects"] == ["intellectual_property"]
    storage.close()


@pytest.mark.asyncio
async def test_daily_case_uses_one_independent_library_case_and_card_budget(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))
    await library.archive_official_cases(
        source=LibrarySource(
            source_kind="official_article",
            title="官方合集",
            raw_text="案例一正文\n案例二正文",
            source_url="https://court.example/collection",
            content_hash="collection-hash",
            created_at=datetime(2026, 9, 18, tzinfo=ZoneInfo("Asia/Shanghai")),
            created_by="source:court_cases",
            session_origin="source:court_cases",
        ),
        candidates=[
            {
                "title": "案例一",
                "locator": "案例一",
                "subjects": ["知识产权"],
                "structured": {
                    "case_summary": "甲公司商标纠纷",
                    "evidence_text": "案例一原文证据",
                },
            }
        ],
        adapter_key="court_cases",
    )
    learning = LearningService(
        storage,
        LearningLLM(),
        library_service=library,
        daily_case_card_max_chars=1800,
    )

    result = await learning.daily_case(subject="知识产权", date="2026-09-18")

    assert result["available"] is True
    assert result["title"] == "案例一"
    assert "案例二" not in result["card_body"]
    assert "官方来源" in result["card_body"]
    storage.close()


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


def test_global_rotation_anchor_is_persisted_and_reused_after_restart(tmp_path):
    config = SimpleNamespace(
        timezone="Asia/Shanghai",
        daily_case_enabled=True,
        daily_case_selection_mode="rotation",
        daily_case_subject=None,
        daily_case_rotation_subjects=("intellectual_property", "economic_law"),
        daily_case_rotation_start_date=None,
        daily_case_rotation_start_index=0,
        daily_question_enabled=True,
        daily_question_selection_mode="rotation",
        daily_question_subject=None,
        daily_question_rotation_subjects=("criminal_law", "civil_commercial"),
        daily_question_rotation_start_date=None,
        daily_question_rotation_start_index=0,
        daily_question_origin="random",
        daily_question_type=None,
    )
    database = tmp_path / "runtime.sqlite3"
    first = LawAssistantService(
        SQLiteStorage(database),
        config=config,
        clock=lambda: datetime(2026, 9, 21, 16, 0, tzinfo=ZoneInfo("UTC")),
    )
    first.materialize_config_rotation_anchors()
    assert first.storage.get_rotation_anchor("daily_case") == "2026-09-22"
    assert first.storage.get_rotation_anchor("daily_question") == "2026-09-22"
    assert (
        first.effective_daily_plan(None, "daily_case").subject_for(date(2026, 9, 23))
        == "economic_law"
    )
    anchored = first.effective_daily_plan(None, "daily_question")
    assert anchored.rotation_start_date == "2026-09-22"
    assert anchored.subject_for(date(2026, 9, 22)) == "criminal_law"
    first.storage.close()

    second = LawAssistantService(
        SQLiteStorage(database),
        config=config,
        clock=lambda: datetime(2026, 9, 22, 16, 0, tzinfo=ZoneInfo("UTC")),
    )
    second.materialize_config_rotation_anchors()
    assert second.storage.get_rotation_anchor("daily_case") == "2026-09-22"
    assert (
        second.storage.get_rotation_anchor("daily_question")
        == anchored.rotation_start_date
    )
    reopened = second.effective_daily_plan(None, "daily_question")
    assert reopened.rotation_start_date == anchored.rotation_start_date
    assert reopened.subject_for(date(2026, 9, 23)) == "civil_commercial"
    second.storage.close()

    config.daily_case_rotation_start_date = "2026-10-01"
    third = LawAssistantService(
        SQLiteStorage(database),
        config=config,
        clock=lambda: datetime(2026, 9, 23, 16, 0, tzinfo=ZoneInfo("UTC")),
    )
    third.materialize_config_rotation_anchors()
    assert (
        third.effective_daily_plan(None, "daily_case").rotation_start_date
        == "2026-10-01"
    )
    third.storage.close()


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
