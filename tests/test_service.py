from __future__ import annotations

import asyncio

import pytest

from models import SourceDocument
from service import LawAssistantService
from storage import SQLiteStorage
from tests.fakes import FakeAdapter, FakeExtractor, make_event


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
