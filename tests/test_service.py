from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from daily_plans import DailyPlan
from daily_resolver import resolve_daily_constraints
from models import CaseItem, SourceDocument
from service import LawAssistantService
from storage import SQLiteStorage
from tests.fakes import FakeAdapter, FakeExtractor, RecordingPublisher, make_event


def document(source_key: str, item_key: str) -> SourceDocument:
    return SourceDocument(
        source_key=source_key,
        source_item_key=item_key,
        url=f"https://example.test/{item_key}",
        title="Fake document",
        content=f"content:{item_key}",
        fetched_at="2026-09-17T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_zero_source_scan_completes_cleanly(tmp_path) -> None:
    service = LawAssistantService(SQLiteStorage(tmp_path / "runtime.sqlite3"))

    result = await service.scan_events()

    assert result.source_count == 0
    assert result.upserted_count == 0
    assert result.failures == ()


@pytest.mark.asyncio
async def test_scan_runs_fake_source_pipeline_and_upserts_events(tmp_path) -> None:
    adapter = FakeAdapter("fake", [document("fake", "item-1")])
    extractor = FakeExtractor({"item-1": [make_event()]})
    service = LawAssistantService(
        SQLiteStorage(tmp_path / "runtime.sqlite3"),
        sources=[(adapter, extractor)],
    )

    result = await service.scan_events(trigger="test")

    assert result.source_count == 1
    assert result.discovered_count == 1
    assert result.upserted_count == 1
    assert await service.list_events(limit=10)


@pytest.mark.asyncio
async def test_scan_runs_validator_before_storage(tmp_path) -> None:
    adapter = FakeAdapter("fake", [document("fake", "item-1")])
    extractor = FakeExtractor({"item-1": [make_event()]})

    class RejectingValidator:
        async def validate(self, event, source_document):
            assert event.title == "法律硕士模拟竞赛"
            assert source_document.source_key == "fake"
            rejected = None
            return rejected

    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        sources=[(adapter, extractor)],
        validators=[RejectingValidator()],
    )

    result = await service.scan_events()

    assert result.discovered_count == 0
    assert result.upserted_count == 0
    assert storage.count_events() == 0


@pytest.mark.asyncio
async def test_one_source_failure_does_not_abort_other_sources(tmp_path) -> None:
    failed = FakeAdapter("failed", error=RuntimeError("source unavailable"))
    good = FakeAdapter("good", [document("good", "item-1")])
    service = LawAssistantService(
        SQLiteStorage(tmp_path / "runtime.sqlite3"),
        sources=[
            (failed, FakeExtractor()),
            (good, FakeExtractor({"item-1": [make_event(source_key="good")]})),
        ],
    )

    result = await service.scan_events()

    assert len(result.failures) == 1
    assert result.failures[0].source_key == "failed"
    assert result.upserted_count == 1
    assert (await service.list_events(limit=10))[0].source_key == "good"


@pytest.mark.asyncio
async def test_parallel_scan_is_skipped_while_first_scan_is_running(tmp_path) -> None:
    adapter = FakeAdapter("slow", [document("slow", "item-1")])
    adapter.wait_for_release = True
    service = LawAssistantService(
        SQLiteStorage(tmp_path / "runtime.sqlite3"),
        sources=[(adapter, FakeExtractor({"item-1": [make_event(source_key="slow")]}))],
    )

    first = asyncio.create_task(service.scan_events(trigger="first"))
    await adapter.started.wait()
    second = await service.scan_events(trigger="second")
    adapter.release.set()
    first_result = await first

    assert second.skipped is True
    assert first_result.skipped is False


@pytest.mark.asyncio
async def test_service_list_and_get_delegate_to_storage(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(storage)
    event_id = storage.upsert_event(make_event())

    events = await service.list_events(limit=10)
    loaded = await service.get_event(event_id)

    assert len(events) == 1
    assert loaded is not None and loaded.id == event_id


@pytest.mark.asyncio
async def test_unchanged_source_document_skips_duplicate_extraction(tmp_path) -> None:
    adapter = FakeAdapter("fake", [document("fake", "item-1")])

    class CountingExtractor(FakeExtractor):
        def __init__(self):
            super().__init__({"item-1": [make_event()]})
            self.calls = 0

        async def extract(self, source_document):
            self.calls += 1
            return await super().extract(source_document)

    extractor = CountingExtractor()
    service = LawAssistantService(
        SQLiteStorage(tmp_path / "runtime.sqlite3"),
        sources=[(adapter, extractor)],
    )

    await service.scan_events()
    await service.scan_events()

    assert extractor.calls == 1
    assert service.storage.count_events() == 1
    assert service.storage.get_event_by_key("fake", "item-1").last_seen_at is not None


@pytest.mark.asyncio
async def test_failed_event_processing_does_not_consume_new_source_hash(
    tmp_path,
) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    old_document = SourceDocument(
        source_key="fake",
        source_item_key="item-1",
        url="https://example.test/item-1",
        title="Old document",
        content="old content",
        fetched_at="2026-09-16T00:00:00+00:00",
    )
    storage.upsert_source_document(old_document)
    storage.upsert_event(make_event())
    new_document = document("fake", "item-1")

    class RetryExtractor:
        calls = 0

        async def extract(self, source_document):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary extraction failure")
            return [
                replace(
                    make_event(title="Recovered event"),
                    raw_content_hash=source_document.content_hash,
                )
            ]

    extractor = RetryExtractor()
    service = LawAssistantService(
        storage,
        sources=[(FakeAdapter("fake", [new_document]), extractor)],
    )

    first = await service.scan_events()
    assert len(first.failures) == 1
    assert storage.get_source_document("fake", "item-1").content_hash == (
        old_document.content_hash
    )

    second = await service.scan_events()

    assert second.failures == ()
    assert extractor.calls == 2
    assert storage.get_source_document("fake", "item-1").content_hash == (
        new_document.content_hash
    )
    assert storage.get_event_by_key("fake", "item-1").title == "Recovered event"


@pytest.mark.asyncio
async def test_failed_secondary_processing_can_retry_same_source_hash(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    old_document = SourceDocument(
        source_key="court_cases",
        source_item_key="case-1",
        url="https://court.example/case-1",
        title="Old case",
        content="old case content",
        fetched_at="2026-09-16T00:00:00+00:00",
    )
    storage.upsert_source_document(old_document)
    new_document = SourceDocument(
        source_key="court_cases",
        source_item_key="case-1",
        url="https://court.example/case-1",
        title="New case",
        content="new case content",
        fetched_at="2026-09-17T00:00:00+00:00",
    )

    class RetryCaseExtractor:
        calls = 0

        async def extract(self, source_document):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary case extraction failure")
            return CaseItem(
                source_key=source_document.source_key,
                source_item_key=source_document.source_item_key,
                title="Recovered case",
                source_url=source_document.url,
                authority="最高人民法院",
                raw_text=source_document.content,
                content_hash=source_document.content_hash,
                discovered_at=source_document.fetched_at,
                last_seen_at=source_document.fetched_at,
            )

    extractor = RetryCaseExtractor()
    service = LawAssistantService(
        storage,
        case_sources=[(FakeAdapter("court_cases", [new_document]), extractor)],
    )

    first = await service.scan_cases()
    assert len(first["failures"]) == 1
    assert storage.get_source_document("court_cases", "case-1").content_hash == (
        old_document.content_hash
    )

    second = await service.scan_cases()

    assert second["failures"] == []
    assert extractor.calls == 2
    assert storage.get_source_document("court_cases", "case-1").content_hash == (
        new_document.content_hash
    )
    assert storage.list_case_items()[0].title == "Recovered case"


@pytest.mark.asyncio
async def test_publish_requires_confirmation_and_is_idempotent(tmp_path) -> None:
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    publisher = RecordingPublisher()
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    event_id = storage.upsert_event(
        replace(
            make_event(),
            discovered_at=now,
            updated_at=now,
            dates=(
                replace(
                    make_event().dates[0],
                    datetime=datetime(
                        2026, 10, 8, 18, tzinfo=ZoneInfo("Asia/Shanghai")
                    ),
                ),
                replace(
                    make_event().dates[0],
                    kind="other",
                    label="未确认辅助时间",
                    confirmed=False,
                ),
            ),
        )
    )
    service = LawAssistantService(
        storage,
        publisher=publisher,
        config=SimpleNamespace(
            auto_publish_events=True,
            timezone="Asia/Shanghai",
            radar_auto_publish_sources=("fake",),
        ),
        clock=lambda: now,
    )
    await service.bind_target("aiocqhttp:group:100", "测试群")

    preview = await service.prepare_publish_event(event_id)
    assert preview["ready"] is True
    assert "未确认辅助时间" not in preview["preview"]
    assert publisher.calls == []
    assert (await service.confirm_publish(preview["token"]))["success"] is True
    assert (await service.confirm_publish(preview["token"]))["success"] is False
    assert len(publisher.calls) == 1
    assert len(storage.list_publications()) == 1
    assert await service._publish_event_to_targets(event_id, kind="automatic") == 1
    assert len(publisher.calls) == 2


@pytest.mark.asyncio
async def test_deadline_reminder_is_sent_once_per_day_and_target(tmp_path) -> None:
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    event = replace(
        make_event(),
        discovered_at=now,
        updated_at=now,
        dates=(
            replace(
                make_event().dates[0],
                datetime=now,
            ),
        ),
    )
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    storage.upsert_event(event)
    publisher = RecordingPublisher()
    service = LawAssistantService(storage, publisher=publisher, clock=lambda: now)
    await service.bind_target("aiocqhttp:group:100", "测试群")

    assert await service.check_deadline_reminders(now=now) == 1
    assert await service.check_deadline_reminders(now=now) == 0
    assert len(storage.list_reminders()) == 1


@pytest.mark.asyncio
async def test_deadline_reminder_survives_unrelated_event_revision(tmp_path) -> None:
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    event = replace(
        make_event(),
        discovered_at=now,
        updated_at=now,
        dates=(replace(make_event().dates[0], datetime=now),),
    )
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    event_id = storage.upsert_event(event)
    publisher = RecordingPublisher()
    service = LawAssistantService(storage, publisher=publisher, clock=lambda: now)
    await service.bind_target("aiocqhttp:group:100", "测试群")

    assert await service.check_deadline_reminders(now=now) == 1
    revised = replace(event, title="Updated title", updated_at=now)
    storage.upsert_event(revised)

    assert storage.get_event(event_id).revision == 2
    assert await service.check_deadline_reminders(now=now) == 0
    assert len(storage.list_reminders()) == 1
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_changed_deadline_creates_new_reminder_identity(tmp_path) -> None:
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    first_date = replace(make_event().dates[0], datetime=now)
    event = replace(
        make_event(), discovered_at=now, updated_at=now, dates=(first_date,)
    )
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    event_id = storage.upsert_event(event)
    publisher = RecordingPublisher()
    service = LawAssistantService(storage, publisher=publisher, clock=lambda: now)
    await service.bind_target("aiocqhttp:group:100", "测试群")

    assert await service.check_deadline_reminders(now=now) == 1
    changed = replace(
        event,
        dates=(replace(first_date, datetime=now.replace(day=2)),),
        updated_at=now,
    )
    storage.upsert_event(changed)

    assert storage.get_event(event_id).revision == 2
    assert await service.check_deadline_reminders(now=now) == 1
    reminders = storage.list_reminders()
    assert len(reminders) == 2
    assert {item["deadline_value"] for item in reminders} == {
        now.isoformat(),
        now.replace(day=2).isoformat(),
    }


@pytest.mark.asyncio
async def test_daily_content_is_idempotent_per_local_day_and_target(tmp_path) -> None:
    now = datetime(2026, 10, 1, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    storage.upsert_case_item(
        CaseItem(
            source_key="court_cases",
            source_item_key="1",
            title="典型案例",
            source_url="https://court.example/1",
            authority="最高人民法院",
            raw_text="官方案例正文",
            content_hash="case-hash",
        )
    )

    class Learning:
        async def daily_case(self, **kwargs):
            return {"available": True, "content": {"case_summary": "摘要"}}

        async def generate_question(self, **kwargs):
            return {"available": False}

    config = SimpleNamespace(
        timezone="Asia/Shanghai",
        auto_scan_enabled=False,
        daily_case_enabled=True,
        daily_case_time="08:00",
        daily_question_enabled=False,
    )
    publisher = RecordingPublisher()
    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=config,
        clock=lambda: now,
    )

    async def unexpected_event_scan(*args, **kwargs):
        raise AssertionError("daily case must not force an event scan")

    service.scan_events = unexpected_event_scan
    await service.bind_target("aiocqhttp:group:100", "测试群")

    first = await service.run_scheduled_jobs(now=now)
    second = await service.run_scheduled_jobs(now=now)

    assert first["daily_case_sent"] == 1
    assert second["daily_case_sent"] == 0
    assert "events" not in first
    assert len(storage.list_daily_contents()) == 1


@pytest.mark.asyncio
async def test_daily_question_uses_independent_subject_and_type_resolver_and_history(
    tmp_path,
) -> None:
    now = datetime(2026, 10, 2, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    calls: list[dict[str, object]] = []

    class Learning:
        async def generate_question(self, **kwargs):
            calls.append(kwargs)
            return {
                "available": True,
                "origin": "mock",
                "subject": kwargs["subject"],
                "question_type": kwargs["question_type"],
                "question_id": 42,
                "source_kind": "library_mock",
                "source_item_key": "42",
                "content": {
                    "question": "每日题目",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析",
                },
            }

    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: now,
    )
    target = await service.bind_target("aiocqhttp:group:100", "测试群")
    storage.upsert_daily_plan(
        DailyPlan(
            content_type="daily_question",
            enabled=True,
            time="08:00",
            selection_mode="rotation",
            rotation_subjects=("intellectual_property", "economic_law"),
            rotation_start_date="2026-10-01",
            question_origin="mock",
            question_type_selection_mode="rotation",
            rotation_question_types=("single_choice", "multiple_choice", "true_false"),
            question_type_rotation_start_date="2026-10-02",
        ),
        target["id"],
    )

    result = await service.run_scheduled_jobs(now=now)

    assert result["daily_question_sent"] == 1
    assert len(calls) == 1
    assert {key: calls[0][key] for key in ("subject", "origin", "question_type")} == {
        "subject": "economic_law",
        "origin": "mock",
        "question_type": "single_choice",
    }
    history = storage.list_daily_contents()[0]
    assert history["source_kind"] == "library_mock"
    assert history["source_item_key"] == "42"
    assert history["resolved_subject"] == "economic_law"
    assert history["resolved_question_type"] == "single_choice"
    assert history["resolved_origin"] == "mock"


@pytest.mark.asyncio
async def test_daily_question_skip_does_not_advance_calendar_rotation(tmp_path) -> None:
    current = [datetime(2026, 10, 1, 9, tzinfo=ZoneInfo("Asia/Shanghai"))]
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    calls: list[str] = []

    class Learning:
        async def generate_question(self, **kwargs):
            calls.append(kwargs["subject"])
            if len(calls) == 1:
                return {"available": False, "reason": "暂无匹配的模拟题"}
            return {
                "available": True,
                "origin": "mock",
                "subject": kwargs["subject"],
                "question_type": kwargs["question_type"],
                "content": {
                    "question": "第二天题目",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析",
                },
            }

    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: current[0],
    )
    target = await service.bind_target("aiocqhttp:group:101", "测试群")
    storage.upsert_daily_plan(
        DailyPlan(
            content_type="daily_question",
            enabled=True,
            time="08:00",
            selection_mode="rotation",
            rotation_subjects=("intellectual_property", "economic_law"),
            rotation_start_date="2026-10-01",
            question_origin="mock",
            question_type="single_choice",
        ),
        target["id"],
    )

    first = await service.run_scheduled_jobs(now=current[0])
    current[0] = datetime(2026, 10, 2, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    second = await service.run_scheduled_jobs(now=current[0])

    assert first["daily_question_sent"] == 0
    assert second["daily_question_sent"] == 1
    assert calls == ["intellectual_property", "economic_law"]
    assert len(storage.list_daily_contents()) == 2


@pytest.mark.asyncio
async def test_daily_question_history_is_target_scoped_and_passed_to_provider(tmp_path):
    now = datetime(2026, 10, 3, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    target = storage.bind_target("aiocqhttp:group:100", "测试群")
    prior = storage.claim_daily_content(
        content_date="2026-10-02",
        target_id=target["id"],
        content_type="daily_question",
        body={"question": "A"},
        source_kind="real_question",
        source_item_key="A",
    )
    assert prior is not None
    storage.finish_daily_content(prior, success=True)

    calls: list[dict[str, object]] = []

    class Learning:
        async def generate_question(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["used_content_keys"] == {("real_question", "A")}
            return {
                "available": True,
                "origin": "real",
                "subject": kwargs["subject"],
                "question_type": kwargs["question_type"],
                "question_id": 2,
                "source_kind": "real_question",
                "source_item_key": "B",
                "content": {
                    "question": "未发送真题 B",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析",
                },
            }

    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: now,
    )
    storage.upsert_daily_plan(
        DailyPlan(
            content_type="daily_question",
            enabled=True,
            time="08:00",
            selection_mode="fixed",
            fixed_subject="criminal_law",
            question_origin="real",
            question_type_selection_mode="fixed",
            fixed_question_type="single_choice",
        ),
        target["id"],
    )

    result = await service.run_scheduled_jobs(now=now)

    assert result["daily_question_sent"] == 1
    assert len(calls) == 1
    sent = storage.list_daily_contents()[0]
    assert sent["source_item_key"] == "B"


@pytest.mark.asyncio
async def test_daily_plan_fixed_to_random_survives_preview_confirmation_and_reload(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    target = await service.bind_target("aiocqhttp:group:100", "测试群")
    storage.upsert_daily_plan(
        DailyPlan.from_mapping(
            "daily_question",
            {"question_type": "multiple_choice"},
        ),
        target["id"],
    )

    preview = await service.prepare_daily_plan_update(
        target_selectors=["测试群"],
        content_type="daily_question",
        changes={"question_type_selection_mode": "random"},
    )

    assert preview["ready"] is True
    assert preview["plans"][0]["plan"]["question_type_selection_mode"] == "random"
    assert (await service.confirm_daily_plan_update(preview["token"]))[
        "success"
    ] is True
    reloaded = SQLiteStorage(tmp_path / "runtime.sqlite3")
    loaded = reloaded.get_daily_plan(target["id"], "daily_question")
    assert loaded is not None
    assert loaded.question_type_selection_mode == "random"


@pytest.mark.asyncio
async def test_daily_plan_reset_preview_uses_global_plan_and_rejects_stale_token(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: datetime(2026, 9, 21, 1, 0, tzinfo=ZoneInfo("UTC")),
    )
    target = await service.bind_target("aiocqhttp:group:100", "法硕一群")
    global_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "selection_mode": "rotation",
            "rotation_subjects": ["intellectual_property", "civil_commercial"],
            "rotation_start_date": "2026-09-21",
        },
    )
    target_plan = DailyPlan.from_mapping(
        "daily_question",
        {"enabled": True, "selection_mode": "fixed", "fixed_subject": "criminal_law"},
    )
    storage.upsert_daily_plan(global_plan)
    storage.upsert_daily_plan(target_plan, target["id"])

    prepared = await service.prepare_daily_plan_override_removal(
        target_selectors=["法硕一群"],
        content_type="daily_question",
        actor_id="operator-1",
    )

    assert prepared["ready"] is True
    assert prepared["target"]["id"] == target["id"]
    assert prepared["target"]["label"] == "法硕一群"
    assert prepared["content_types"] == ["daily_question"]
    assert prepared["current_plans"][0]["plan"]["fixed_subject"] == "criminal_law"
    assert prepared["restored_plans"][0]["plan"]["selection_mode"] == "rotation"
    assert prepared["restored_plans"][0]["preview"][0]["subject"] == (
        "intellectual_property"
    )
    assert len(prepared["restored_plans"][0]["preview"]) == 14
    assert storage.get_daily_plan(target["id"], "daily_question") is not None

    storage.upsert_daily_plan(
        DailyPlan.from_mapping(
            "daily_question",
            {
                "enabled": True,
                "selection_mode": "rotation",
                "rotation_subjects": ["economic_law", "civil_commercial"],
                "rotation_start_date": "2026-09-21",
            },
        )
    )
    stale = await service.confirm_daily_plan_override_removal(
        prepared["token"], actor_id="operator-1"
    )
    assert stale == {
        "success": False,
        "reason": "全局计划在准备后已变化，请重新预览",
    }
    assert storage.get_daily_plan(target["id"], "daily_question") is not None

    refreshed = await service.prepare_daily_plan_override_removal(
        target_selectors=["法硕一群"],
        content_type="daily_question",
        actor_id="operator-1",
    )
    confirmed = await service.confirm_daily_plan_override_removal(
        refreshed["token"], actor_id="operator-1"
    )
    assert confirmed == {"success": True, "content_types": ["daily_question"]}
    assert storage.get_daily_plan(target["id"], "daily_question") is None
    restored = service.effective_daily_plan(target["id"], "daily_question")
    assert restored.rotation_subjects == ("economic_law", "civil_commercial")
    replay = await service.confirm_daily_plan_override_removal(
        refreshed["token"], actor_id="operator-1"
    )
    assert replay["success"] is False


@pytest.mark.asyncio
async def test_daily_plan_reset_preview_uses_target_id_for_random_global_resolution(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: datetime(2026, 9, 21, 1, 0, tzinfo=ZoneInfo("UTC")),
    )
    target = await service.bind_target("aiocqhttp:group:100", "法硕一群")
    other_target = await service.bind_target("aiocqhttp:group:200", "法硕二群")
    global_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "selection_mode": "random",
            "question_origin": "random",
            "question_type_selection_mode": "random",
        },
    )
    target_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "selection_mode": "fixed",
            "fixed_subject": "criminal_law",
            "question_type_selection_mode": "fixed",
            "fixed_question_type": "single_choice",
        },
    )
    other_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "selection_mode": "fixed",
            "fixed_subject": "economic_law",
            "question_type_selection_mode": "fixed",
            "fixed_question_type": "true_false",
        },
    )
    storage.upsert_daily_plan(global_plan)
    storage.upsert_daily_plan(target_plan, target["id"])
    storage.upsert_daily_plan(other_plan, other_target["id"])
    local_today = date(2026, 9, 21)

    global_preview_before = global_plan.preview(local_today, 14, target_id=None)
    other_preview_before = other_plan.preview(
        local_today, 14, target_id=other_target["id"]
    )
    prepared = await service.prepare_daily_plan_override_removal(
        target_selectors=["法硕一群"],
        content_type="daily_question",
        actor_id="operator-1",
    )

    restored_preview = prepared["restored_plans"][0]["preview"]
    expected_preview = [
        {
            "date": (local_today + timedelta(days=offset)).isoformat(),
            **resolve_daily_constraints(
                global_plan,
                local_today + timedelta(days=offset),
                target_id=target["id"],
                content_type="daily_question",
            ),
        }
        for offset in range(14)
    ]
    assert restored_preview == expected_preview
    assert restored_preview != global_preview_before
    assert global_plan.preview(local_today, 14, target_id=None) == global_preview_before
    assert other_plan.preview(local_today, 14, target_id=other_target["id"]) == (
        other_preview_before
    )

    confirmed = await service.confirm_daily_plan_override_removal(
        prepared["token"], actor_id="operator-1"
    )
    assert confirmed["success"] is True
    effective = service.effective_daily_plan(target["id"], "daily_question")
    assert effective.to_mapping() == global_plan.to_mapping()
    assert [
        {
            "date": (local_today + timedelta(days=offset)).isoformat(),
            **resolve_daily_constraints(
                effective,
                local_today + timedelta(days=offset),
                target_id=target["id"],
                content_type="daily_question",
            ),
        }
        for offset in range(14)
    ] == restored_preview
    assert (
        service.effective_daily_plan(other_target["id"], "daily_question").to_mapping()
        == other_plan.to_mapping()
    )
