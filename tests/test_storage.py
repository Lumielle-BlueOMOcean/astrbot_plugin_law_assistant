from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from storage import SCHEMA_VERSION, SQLiteStorage
from tests.fakes import make_event


def test_storage_initializes_version_one_and_persists_events(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    event = make_event()

    storage = SQLiteStorage(db_path)
    assert storage.schema_version == 1
    event_id = storage.upsert_event(event)
    storage.close()

    reopened = SQLiteStorage(db_path)
    assert reopened.schema_version == 1
    loaded = reopened.get_event(event_id)
    assert loaded is not None
    assert loaded.title == event.title
    assert loaded.dates[0].kind == "registration_deadline"
    reopened.close()


def test_storage_migrates_version_zero_database_to_current_schema(tmp_path) -> None:
    db_path = tmp_path / "version-zero.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '0')"
        )

    storage = SQLiteStorage(db_path)

    assert storage.schema_version == SCHEMA_VERSION == 1
    assert storage.count_events() == 0
    storage.close()


def test_storage_rejects_schema_version_newer_than_supported_without_downgrade(
    tmp_path,
) -> None:
    db_path = tmp_path / "future-version.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '99')"
        )

    with pytest.raises(
        RuntimeError,
        match=r"schema version 99 is newer than supported version 1",
    ):
        SQLiteStorage(db_path)

    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()[0]
    assert version == "99"


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


def test_storage_preserves_first_discovered_at_on_upsert(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    first_discovered = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
    second_discovered = datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc)
    first = replace(
        make_event(title="Initial competition"),
        discovered_at=first_discovered,
        updated_at=first_discovered,
    )
    second = replace(
        first,
        title="Updated competition",
        discovered_at=second_discovered,
        updated_at=second_discovered,
    )

    first_id = storage.upsert_event(first)
    second_id = storage.upsert_event(second)
    loaded = storage.get_event(first_id)

    assert second_id == first_id
    assert loaded is not None
    assert loaded.title == "Updated competition"
    assert loaded.discovered_at == first_discovered
    assert loaded.updated_at == second_discovered
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
