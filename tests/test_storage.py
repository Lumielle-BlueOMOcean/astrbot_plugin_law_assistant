from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from daily_plans import DailyPlan
from storage import SCHEMA_VERSION, SQLiteStorage
from tests.fakes import make_event


def _create_v1_database(db_path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES ('version', '1');
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                source_item_key TEXT NOT NULL,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                organizer TEXT NOT NULL,
                event_type TEXT NOT NULL,
                eligibility TEXT NOT NULL,
                status TEXT NOT NULL,
                raw_content_hash TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                UNIQUE(source_key, source_item_key)
            );
            CREATE TABLE event_dates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                datetime TEXT,
                timezone TEXT NOT NULL,
                label TEXT NOT NULL,
                evidence_text TEXT NOT NULL
            );
            CREATE TABLE source_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                success INTEGER NOT NULL,
                discovered_count INTEGER NOT NULL,
                error_summary TEXT
            );
            """
        )


def test_storage_initializes_version_one_and_persists_events(tmp_path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    event = make_event()

    storage = SQLiteStorage(db_path)
    assert storage.schema_version == SCHEMA_VERSION
    event_id = storage.upsert_event(event)
    storage.close()

    reopened = SQLiteStorage(db_path)
    assert reopened.schema_version == SCHEMA_VERSION
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

    assert storage.schema_version == SCHEMA_VERSION == 6
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
        match=r"schema version 99 is newer than supported version 6",
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


def test_storage_migrates_existing_version_one_data_without_loss(tmp_path) -> None:
    db_path = tmp_path / "existing-v1.sqlite3"
    _create_v1_database(db_path)
    event = make_event(title="Persisted v1 event")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO events(
                source_key, source_item_key, title, source_url, organizer,
                event_type, eligibility, status, raw_content_hash,
                discovered_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.source_key,
                event.source_item_key,
                event.title,
                event.source_url,
                event.organizer,
                event.event_type,
                event.eligibility,
                event.status,
                event.raw_content_hash,
                event.discovered_at.isoformat(),
                event.updated_at.isoformat(),
                "{}",
            ),
        )

    storage = SQLiteStorage(db_path)
    loaded = storage.get_event(1)

    assert storage.schema_version == SCHEMA_VERSION == 6
    assert loaded is not None and loaded.title == "Persisted v1 event"
    storage.close()


def test_storage_migrates_v2_reminders_to_logical_identity_without_losing_history(
    tmp_path,
) -> None:
    db_path = tmp_path / "existing-v2.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES ('version', '2');
            CREATE TABLE events (id INTEGER PRIMARY KEY, source_key TEXT NOT NULL,
                source_item_key TEXT NOT NULL, UNIQUE(source_key, source_item_key));
            CREATE TABLE event_dates (id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL,
                kind TEXT NOT NULL, datetime TEXT, timezone TEXT NOT NULL,
                label TEXT NOT NULL, evidence_text TEXT NOT NULL, confirmed INTEGER NOT NULL);
            CREATE TABLE publish_targets (id INTEGER PRIMARY KEY, unified_msg_origin TEXT NOT NULL);
            CREATE TABLE reminders (
                id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL,
                event_date_id INTEGER NOT NULL, deadline_value TEXT NOT NULL,
                target_id INTEGER NOT NULL, reminder_offset INTEGER NOT NULL,
                status TEXT NOT NULL, attempted_at TEXT NOT NULL,
                finished_at TEXT, error_summary TEXT
            );
            INSERT INTO events(id, source_key, source_item_key)
                VALUES (1, 'fake', 'item-1');
            INSERT INTO event_dates(id, event_id, kind, datetime, timezone, label,
                evidence_text, confirmed)
                VALUES (10, 1, 'submission_deadline', '2026-10-08T10:00:00+00:00',
                    'UTC', '投稿截止', '官方原文', 1);
            INSERT INTO publish_targets(id, unified_msg_origin)
                VALUES (20, 'aiocqhttp:group:100');
            INSERT INTO reminders(id, event_id, event_date_id, deadline_value,
                target_id, reminder_offset, status, attempted_at, finished_at)
                VALUES (30, 1, 10, '2026-10-08T10:00:00+00:00', 20, 7, 'sent',
                    '2026-10-01T10:00:00+00:00', '2026-10-01T10:00:01+00:00');
            """
        )

    storage = SQLiteStorage(db_path)

    reminder = storage.list_reminders()[0]
    assert storage.schema_version == SCHEMA_VERSION == 6
    assert reminder["date_kind"] == "submission_deadline"
    assert reminder["status"] == "sent"
    assert "event_date_id" not in reminder
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


def test_storage_imports_and_retrieves_verified_real_questions(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    record = {
        "source_name": "合法取得的题库",
        "exam_name": "法硕测试卷",
        "exam_year": "2025",
        "source_locator": "第一卷第 1 题",
        "source_url": "https://example.test/question/1",
        "subject": "刑法",
        "question_type": "多选",
        "stem": "下列哪些说法正确？",
        "options": ["A", "B", "C", "D"],
        "answer": ["A", "C"],
        "answer_source": "official",
        "verification_status": "verified",
    }

    assert storage.import_real_questions([record]) == 1
    questions = storage.list_real_questions(
        subject="刑事法", question_type="multiple_choice"
    )

    assert len(questions) == 1
    assert questions[0].exam_name == "法硕测试卷"
    assert storage.real_question_inventory()["count"] == 1
    storage.close()


def test_storage_persists_independent_daily_plans_and_target_override(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    target = storage.bind_target("aiocqhttp:group:100", "一群")
    global_plan = DailyPlan(
        content_type="daily_case",
        enabled=True,
        selection_mode="rotation",
        rotation_subjects=("intellectual_property", "civil_commercial"),
        rotation_start_date="2026-09-21",
    )
    target_plan = DailyPlan(
        content_type="daily_question",
        enabled=True,
        selection_mode="fixed",
        fixed_subject="economic_law",
        question_origin="real",
        question_type="multiple_choice",
    )
    storage.upsert_daily_plan(global_plan)
    storage.upsert_daily_plan(target_plan, target["id"])
    storage.close()

    reopened = SQLiteStorage(tmp_path / "runtime.sqlite3")
    assert reopened.get_daily_plan(None, "daily_case").rotation_subjects == (
        "intellectual_property",
        "civil_commercial",
    )
    assert (
        reopened.get_daily_plan(target["id"], "daily_question").question_origin
        == "real"
    )
    assert reopened.schema_version == 6
    reopened.close()


def test_storage_runs_the_v3_to_v6_migration_path(tmp_path) -> None:
    db_path = tmp_path / "v3.sqlite3"
    storage = SQLiteStorage(db_path)
    storage.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE schema_meta SET value = '3' WHERE key = 'version'")
        connection.execute("DROP TABLE real_questions")
        connection.execute("DROP TABLE daily_plans")
        connection.commit()

    migrated = SQLiteStorage(db_path)
    assert migrated.schema_version == SCHEMA_VERSION == 6
    assert migrated.real_question_inventory()["count"] == 0
    assert migrated.list_daily_plans() == []
    migrated.close()
