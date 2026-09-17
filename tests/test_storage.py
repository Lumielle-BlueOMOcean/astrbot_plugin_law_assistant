from __future__ import annotations

from datetime import datetime, timezone

from storage import SQLiteStorage
from tests.fakes import make_event


def test_storage_initializes_version_one_and_persists_events(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    event = make_event()

    storage = SQLiteStorage(db_path)
    assert storage.schema_version == 1
    event_id = storage.upsert_event(event)
    storage.close()

    reopened = SQLiteStorage(db_path)
    loaded = reopened.get_event(event_id)
    assert loaded is not None
    assert loaded.title == event.title
    assert loaded.dates[0].kind == "registration_deadline"
    reopened.close()


def test_storage_deterministically_upserts_same_source_item(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    first = make_event()
    second = make_event(title="Updated competition")

    first_id = storage.upsert_event(first)
    second_id = storage.upsert_event(second)

    assert second_id == first_id
    assert storage.count_events() == 1
    assert storage.list_events(limit=10)[0].title == "Updated competition"
    storage.close()


def test_storage_records_source_run_success_and_failure(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    started = datetime(2026, 9, 17, tzinfo=timezone.utc)
    finished = datetime(2026, 9, 17, 0, 1, tzinfo=timezone.utc)

    success_id = storage.record_source_run(
        source_key="good",
        started_at=started,
        finished_at=finished,
        success=True,
        discovered_count=2,
    )
    failure_id = storage.record_source_run(
        source_key="bad",
        started_at=started,
        finished_at=finished,
        success=False,
        discovered_count=0,
        error_summary="timeout",
    )

    runs = storage.list_source_runs(limit=10)
    assert {run["id"] for run in runs} == {success_id, failure_id}
    assert any(
        run["success"] is False and run["error_summary"] == "timeout" for run in runs
    )
    storage.close()
