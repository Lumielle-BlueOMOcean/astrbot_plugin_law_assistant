from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

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
            ),
        )
    )
    service = LawAssistantService(storage, publisher=publisher, clock=lambda: now)
    await service.bind_target("aiocqhttp:group:100", "测试群")

    preview = await service.prepare_publish_event(event_id)
    assert preview["ready"] is True
    assert publisher.calls == []
    assert (await service.confirm_publish(preview["token"]))["success"] is True
    assert (await service.confirm_publish(preview["token"]))["success"] is False
    assert len(publisher.calls) == 1
    assert len(storage.list_publications()) == 1


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
    await service.bind_target("aiocqhttp:group:100", "测试群")

    first = await service.run_scheduled_jobs(now=now)
    second = await service.run_scheduled_jobs(now=now)

    assert first["daily_case_sent"] == 1
    assert second["daily_case_sent"] == 0
    assert len(storage.list_daily_contents()) == 1
