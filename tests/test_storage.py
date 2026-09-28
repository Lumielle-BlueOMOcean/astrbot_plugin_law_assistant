from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

import storage as storage_module
from daily_plans import DailyPlan
from library_repository import LibraryRepository
from library_service import LibraryService
from question_session_repository import QuestionSessionRepository
from scheduled_reveals import ScheduledRevealRepository
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


def _insert_v11_real_question(
    connection,
    *,
    question_id,
    identity_key,
    exam_year="2023",
    paper="A卷",
    question_number="第1题",
    source_locator="第一部分第1题",
    answer_json='"A"',
    answer_source="not_provided",
):
    connection.execute(
        """
        INSERT INTO real_questions(
            id, identity_key, source_name, exam_name, exam_year, exam_date,
            paper, question_number, source_url, source_locator, subject,
            question_type, stem, options_json, answer_json, explanation,
            answer_source, verification_status, content_hash, metadata_json,
            created_at, updated_at
        ) VALUES (?, ?, '合成题库', '合成考试', ?, '', ?, ?, '', ?,
            'criminal_law', 'single_choice', '合成题干', '["A", "B"]', ?,
            '合成解析', ?, 'verified', ?, '{}', 'created', 'updated')
        """,
        (
            question_id,
            identity_key,
            exam_year,
            paper,
            question_number,
            source_locator,
            answer_json,
            answer_source,
            f"legacy-content-{question_id}",
        ),
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


def test_data_clear_preview_is_stale_safe_and_full_reset_keeps_schema(tmp_path):
    storage = SQLiteStorage(tmp_path / "clear-runtime.sqlite3")
    storage.upsert_event(make_event())
    preview = storage.prepare_data_clear("radar")
    assert preview["counts"]["events"] == 1
    storage.upsert_event(replace(make_event(), source_key="concurrent-source"))
    with pytest.raises(ValueError, match="发生变化"):
        storage.confirm_data_clear("radar", preview["snapshot"])
    assert storage.count_events() == 2

    full = storage.prepare_data_clear("all_runtime")
    with pytest.raises(ValueError, match="清空全部数据"):
        storage.confirm_data_clear("all_runtime", full["snapshot"])
    result = storage.confirm_data_clear(
        "all_runtime", full["snapshot"], typed_confirmation="清空全部数据"
    )
    assert result["success"] is True
    assert storage.schema_version == 13
    assert storage.count_events() == 0
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()

    reopened = SQLiteStorage(tmp_path / "clear-runtime.sqlite3")
    assert reopened.schema_version == 13
    assert reopened.count_events() == 0
    reopened.close()


def test_data_clear_failure_rolls_back_prior_child_deletions(tmp_path):
    storage = SQLiteStorage(tmp_path / "clear-rollback.sqlite3")
    event_id = storage.upsert_event(make_event())
    before_dates = storage.connection.execute(
        "SELECT COUNT(*) FROM event_dates WHERE event_id = ?", (event_id,)
    ).fetchone()[0]
    preview = storage.prepare_data_clear("radar")
    storage.connection.execute(
        "CREATE TRIGGER reject_event_clear BEFORE DELETE ON events "
        "BEGIN SELECT RAISE(ABORT, 'injected clear failure'); END"
    )
    storage.connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected clear failure"):
        storage.confirm_data_clear("radar", preview["snapshot"])

    assert storage.get_event(event_id) is not None
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM event_dates WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        == before_dates
    )
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.connection.execute("DROP TRIGGER reject_event_clear")
    storage.connection.commit()
    storage.close()


def test_target_clear_keeps_session_content_but_nulls_target_and_removes_target_data(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "clear-targets.sqlite3")
    target = storage.bind_target("aiocqhttp:group:clear", "待清理群")
    storage.upsert_daily_plan(
        DailyPlan("daily_question", enabled=True, question_origin="mock"),
        target["id"],
    )
    storage.claim_daily_content(
        content_date="2026-09-29",
        target_id=target["id"],
        content_type="daily_question",
        body={"question": "合成题"},
    )
    session = QuestionSessionRepository(storage.connection).create_session(
        session_key="clear-target-session",
        scope_origin=target["unified_msg_origin"],
        target_id=target["id"],
        source_kind="generated_question",
        source_item_key="synthetic",
        library_item_id=None,
        real_question_id=None,
        question_identity="mock_question",
        snapshot={"prompts": [{"stem": "保留快照"}]},
        created_by="scheduler",
        created_at="2026-09-29T00:00:00+00:00",
    )
    preview = storage.prepare_data_clear("targets")
    assert preview["counts"]["publish_targets"] == 1
    assert "target_id 将置空" in preview["retained"][-1]
    storage.confirm_data_clear("targets", preview["snapshot"])
    assert storage.get_target(target["id"]) is None
    assert storage.list_daily_plans() == []
    assert storage.list_daily_contents() == []
    retained_session = QuestionSessionRepository(storage.connection).get(session["id"])
    assert retained_session["target_id"] is None
    assert retained_session["snapshot"]["prompts"][0]["stem"] == "保留快照"
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "scope",
        "expected",
    ),
    [
        ("radar", (0, 1, 2, 1, 1, 1, 1, 1)),
        ("library", (1, 1, 0, 1, 1, 1, 1, 1)),
        ("verified_questions", (1, 0, 2, 1, 1, 1, 1, 1)),
        ("cases", (1, 1, 1, 1, 1, 1, 1, 1)),
        ("delivery_history", (1, 1, 2, 1, 1, 0, 1, 0)),
        ("question_sessions", (1, 1, 2, 1, 1, 1, 0, 0)),
        ("daily_plans", (1, 1, 2, 1, 0, 1, 1, 0)),
        ("targets", (1, 1, 2, 0, 0, 0, 1, 1)),
        ("all_runtime", (0, 0, 0, 0, 0, 0, 0, 0)),
    ],
)
async def test_data_clear_scopes_preserve_unrelated_seeded_domains_and_reopen(
    scope, expected, tmp_path
):
    storage = SQLiteStorage(tmp_path / f"clear-integration-{scope}.sqlite3")
    storage.upsert_event(make_event())
    library = LibraryService(LibraryRepository(storage.connection))
    note = await library.archive_learning_material(
        raw_text="用于跨范围保留断言的合成笔记",
        material_type="note",
        title="合成笔记",
        created_by="operator-1",
        session_origin="private:operator-1",
    )
    case = await library.archive_learning_material(
        raw_text="用于案例范围清理的合成案情",
        material_type="case",
        title="合成用户案例",
        subjects="民法",
        structured_json={"case_summary": "合成摘要"},
        created_by="operator-1",
        session_origin="private:operator-1",
    )
    assert note["success"] and case["success"]
    storage.import_real_questions(
        [
            {
                "source_name": "合成核验题库",
                "exam_name": "合成考试",
                "exam_year": "2025",
                "question_number": "Q1",
                "source_locator": "合成定位",
                "subject": "刑法",
                "question_type": "single_choice",
                "stem": "合成真题题干",
                "options": ["A", "B"],
                "answer": "A",
                "answer_source": "official",
                "verification_status": "verified",
            }
        ]
    )
    target = storage.bind_target("aiocqhttp:group:clear-integration", "清理测试群")
    storage.upsert_daily_plan(
        DailyPlan("daily_question", enabled=True, question_origin="mock"),
        target["id"],
    )
    storage.claim_daily_content(
        content_date="2026-09-29",
        target_id=target["id"],
        content_type="daily_question",
        body={"prompt": "合成题面"},
    )
    session = QuestionSessionRepository(storage.connection).create_session(
        session_key=f"clear-session-{scope}",
        scope_origin=target["unified_msg_origin"],
        target_id=target["id"],
        source_kind="generated_question",
        source_item_key="synthetic-question",
        library_item_id=None,
        real_question_id=None,
        question_identity="mock_question",
        snapshot={"prompts": [{"stem": "只用于清理测试"}]},
        created_by="scheduler",
        created_at="2026-09-29T00:00:00+00:00",
    )
    session_hash = storage.connection.execute(
        "SELECT question_snapshot_hash FROM question_sessions WHERE id = ?",
        (session["id"],),
    ).fetchone()[0]
    ScheduledRevealRepository(storage.connection).create_for_session(
        session_id=session["id"],
        target_umo=target["unified_msg_origin"],
        snapshot_hash=session_hash,
        question_sent_at="2026-09-29T00:00:00+00:00",
        due_at="2026-09-29T01:00:00+00:00",
        reveal_kind="answer",
        created_at="2026-09-29T00:00:00+00:00",
    )

    preview = storage.prepare_data_clear(scope)
    storage.confirm_data_clear(
        scope,
        preview["snapshot"],
        typed_confirmation="清空全部数据" if scope == "all_runtime" else None,
    )

    row = storage.connection.execute(
        "SELECT "
        "(SELECT COUNT(*) FROM events), "
        "(SELECT COUNT(*) FROM real_questions), "
        "(SELECT COUNT(*) FROM learning_items), "
        "(SELECT COUNT(*) FROM publish_targets), "
        "(SELECT COUNT(*) FROM daily_plans), "
        "(SELECT COUNT(*) FROM daily_contents), "
        "(SELECT COUNT(*) FROM question_sessions), "
        "(SELECT COUNT(*) FROM scheduled_reveal_jobs)"
    ).fetchone()
    assert tuple(row) == expected
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    if scope == "targets":
        assert (
            storage.connection.execute(
                "SELECT target_id FROM question_sessions WHERE id = ?", (session["id"],)
            ).fetchone()[0]
            is None
        )
        assert (
            storage.connection.execute(
                "SELECT target_id FROM scheduled_reveal_jobs WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0]
            is None
        )
    if scope == "all_runtime":
        assert storage.count_events() == 0
    storage.close()

    reopened = SQLiteStorage(tmp_path / f"clear-integration-{scope}.sqlite3")
    assert reopened.schema_version == 13
    assert reopened.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    reopened.upsert_event(replace(make_event(), source_key=f"post-clear-{scope}"))
    assert reopened.get_event_by_key(f"post-clear-{scope}", "item-1") is not None
    reopened.close()


@pytest.mark.parametrize(
    "scope",
    [
        "radar",
        "library",
        "verified_questions",
        "cases",
        "delivery_history",
        "question_sessions",
        "daily_plans",
        "targets",
        "all_runtime",
    ],
)
def test_data_clear_scope_names_are_fixed_allowlist(scope, tmp_path):
    storage = SQLiteStorage(tmp_path / f"scope-{scope}.sqlite3")
    preview = storage.prepare_data_clear(scope)
    assert preview["scope"] == scope
    assert preview["scope_label"]
    assert preview["delete_description"]
    assert preview["counts"]
    assert "SQLite schema 和 schema_meta" in preview["retained"]
    if scope == "daily_plans":
        assert "scheduled_reveal_jobs" in preview["snapshot"]
    if scope == "delivery_history":
        assert "source_runs" in preview["snapshot"]
    with pytest.raises(ValueError, match="不支持"):
        storage.prepare_data_clear("events; DROP TABLE events")
    storage.confirm_data_clear(
        scope,
        preview["snapshot"],
        typed_confirmation="清空全部数据" if scope == "all_runtime" else None,
    )
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()
    reopened = SQLiteStorage(tmp_path / f"scope-{scope}.sqlite3")
    assert reopened.schema_version == 13
    assert reopened.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    if scope == "all_runtime":
        assert reopened.count_events() == 0
    reopened.close()


def test_fresh_database_initializes_schema_13_reveal_and_review_defaults(tmp_path):
    storage = SQLiteStorage(tmp_path / "schema-13-fresh.sqlite3")

    assert storage.schema_version == 13
    plan_columns = {
        row["name"]
        for row in storage.connection.execute("PRAGMA table_info(daily_plans)")
    }
    assert {
        "question_reveal_mode",
        "answer_reveal_delay_minutes",
        "explanation_reveal_delay_minutes",
    } <= plan_columns
    reveal_columns = {
        row["name"]
        for row in storage.connection.execute(
            "PRAGMA table_info(scheduled_reveal_jobs)"
        )
    }
    assert {
        "session_id",
        "target_umo",
        "snapshot_hash",
        "due_at",
        "reveal_kind",
        "status",
        "page_progress_json",
    } <= reveal_columns
    tables = {
        row["name"]
        for row in storage.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "event_status_overrides",
        "event_date_reviews",
        "operator_action_audits",
    } <= tables
    binding_columns = {
        row["name"]
        for row in storage.connection.execute(
            "PRAGMA table_info(structured_item_bindings)"
        )
    }
    assert {
        "verified_real_question_id",
        "promoted_by",
        "promoted_at",
    } <= binding_columns
    daily_columns = {
        row["name"]
        for row in storage.connection.execute("PRAGMA table_info(daily_contents)")
    }
    assert {"intended_local_at", "target_label"} <= daily_columns
    case_columns = {
        row["name"]
        for row in storage.connection.execute("PRAGMA table_info(case_items)")
    }
    assert {"active", "deleted_at", "deleted_by"} <= case_columns
    learning_columns = {
        row["name"]
        for row in storage.connection.execute("PRAGMA table_info(learning_items)")
    }
    assert {"active", "deleted_at", "deleted_by"} <= learning_columns
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()


def test_v12_to_v13_migration_preserves_existing_data_and_reopens_idempotently(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "schema-12-upgrade.sqlite3"
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 12)
    original = SQLiteStorage(db_path)
    event_id = original.upsert_event(make_event())
    original.connection.execute(
        "INSERT INTO daily_plans(target_id, content_type, enabled, time, "
        "selection_mode, rotation_subjects_json, rotation_start_index, "
        "question_origin, updated_at) VALUES (NULL, 'daily_question', 1, "
        "'08:00', 'random', '[]', 0, 'random', 'before-v13')"
    )
    original.connection.commit()
    original.close()

    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 13)
    migrated = SQLiteStorage(db_path)
    assert migrated.schema_version == 13
    assert migrated.get_event(event_id).title == make_event().title
    plan = migrated.get_daily_plan(None, "daily_question")
    assert plan is not None and plan.enabled
    assert plan.question_reveal_mode == "manual"
    assert plan.answer_reveal_delay_minutes == 0
    assert (
        migrated.connection.execute(
            "SELECT intended_local_at, target_label FROM daily_contents LIMIT 1"
        ).fetchone()
        is None
    )
    migrated.close()

    reopened = SQLiteStorage(db_path)
    assert reopened.schema_version == 13
    assert reopened.get_event(event_id) is not None
    assert (
        reopened.get_daily_plan(None, "daily_question").question_reveal_mode == "manual"
    )
    reopened.close()


def test_daily_question_reveal_settings_persist_through_plan_storage(tmp_path):
    db_path = tmp_path / "daily-reveal-plan.sqlite3"
    storage = SQLiteStorage(db_path)
    plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "question_reveal_mode": "delayed",
            "answer_reveal_delay_minutes": 10,
            "explanation_reveal_delay_minutes": 25,
        },
    )
    storage.upsert_daily_plan(plan)
    storage.close()

    reopened = SQLiteStorage(db_path)
    persisted = reopened.get_daily_plan(None, "daily_question")
    assert persisted is not None
    assert persisted.question_reveal_mode == "delayed"
    assert persisted.answer_reveal_delay_minutes == 10
    assert persisted.explanation_reveal_delay_minutes == 25
    reopened.close()


def test_v12_to_v13_migration_failure_rolls_back_schema_and_version(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "schema-13-rollback.sqlite3"
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 12)
    initial = SQLiteStorage(db_path)
    initial.close()
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 13)

    def fail_after_ddl(connection):
        connection.execute("CREATE TABLE migration_probe(id INTEGER PRIMARY KEY)")
        raise RuntimeError("injected migration failure")

    monkeypatch.setitem(storage_module._MIGRATIONS, 12, fail_after_ddl)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        SQLiteStorage(db_path)

    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()[0]
            == "12"
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='migration_probe'"
            ).fetchone()
            is None
        )


def test_real_question_identity_v2_separates_year_and_paper_and_updates_answer(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "real-identity-v2.sqlite3")
    base = {
        "source_name": "合成题库",
        "exam_name": "合成考试",
        "exam_year": "2023",
        "exam_date": "2023-09-01",
        "paper": "A卷",
        "question_number": "第1题",
        "source_locator": "第一部分第1题",
        "source_url": "https://example.test/question",
        "subject": "刑法",
        "question_type": "单选",
        "stem": "相同定位的合成题干",
        "options": ["A. 甲", "B. 乙"],
        "answer": "A",
        "answer_source": "official",
        "verification_status": "verified",
    }
    records = [
        base,
        {**base, "exam_year": "2022"},
        {**base, "paper": "B卷"},
    ]

    assert storage.import_real_questions(records) == 3
    questions = storage.list_real_questions(limit=None)
    assert len(questions) == 3
    by_exam = {(question.exam_year, question.paper): question for question in questions}
    original_id = by_exam[("2023", "A卷")].id
    revised = {
        **base,
        "answer": "B",
        "answer_source": "third_party",
        "explanation": "修订后的合成解析",
    }

    assert storage.import_real_questions([revised]) == 1
    updated = storage.list_real_questions(limit=None)
    assert len(updated) == 3
    updated_original = next(
        question for question in updated if question.id == original_id
    )
    assert updated_original.answer == "B"
    assert updated_original.explanation == "修订后的合成解析"
    assert updated_original.answer_source == "third_party"
    storage.close()


def test_real_question_without_locator_updates_same_row_when_answer_changes(tmp_path):
    storage = SQLiteStorage(tmp_path / "real-question-content-fallback.sqlite3")
    base = {
        "source_name": "合成题库",
        "exam_name": "合成考试",
        "exam_year": "2023",
        "source_url": "https://example.test/without-locator",
        "subject": "刑法",
        "question_type": "单选",
        "stem": "无题号定位的合成题干",
        "options": ["A. 甲", "B. 乙"],
        "answer": "A",
        "answer_source": "official",
        "verification_status": "verified",
    }

    assert storage.import_real_questions([base]) == 1
    first = storage.list_real_questions(limit=None)[0]
    assert (
        storage.import_real_questions(
            [{**base, "answer": "B", "explanation": "修订后的解析"}]
        )
        == 1
    )
    rows = storage.list_real_questions(limit=None)

    assert len(rows) == 1
    assert rows[0].id == first.id
    assert rows[0].answer == "B"
    assert rows[0].explanation == "修订后的解析"
    storage.close()


def test_v11_to_v12_migration_preserves_real_question_ids_and_session_references(
    tmp_path,
):
    db_path = tmp_path / "v11-real-questions.sqlite3"
    storage = SQLiteStorage(db_path)
    storage.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("UPDATE schema_meta SET value = '11' WHERE key = 'version'")
        _insert_v11_real_question(
            connection,
            question_id=101,
            identity_key="legacy-2023",
            exam_year="2023",
            answer_json='"A"',
            answer_source="not_provided",
        )
        _insert_v11_real_question(
            connection,
            question_id=102,
            identity_key="legacy-2022",
            exam_year="2022",
            answer_json=None,
            answer_source="official",
        )
        connection.execute(
            """
            INSERT INTO question_sessions(
                session_key, scope_origin, source_kind, source_item_key,
                real_question_id, question_identity, question_snapshot_hash,
                question_snapshot_json, status, current_stage, created_by,
                created_at, updated_at
            ) VALUES ('session-101', 'private:42', 'real_question', '101', 101,
                'verified_real_question', 'snapshot-hash', '{}', 'open', 'prompt',
                '42', 'created', 'updated')
            """
        )
        connection.commit()

    migrated = SQLiteStorage(db_path)

    assert migrated.schema_version == SCHEMA_VERSION == 13
    rows = migrated.connection.execute(
        "SELECT id, identity_key, exam_year, answer_json, answer_source "
        "FROM real_questions ORDER BY id"
    ).fetchall()
    assert [row["id"] for row in rows] == [101, 102]
    assert rows[0]["identity_key"].startswith("rq:v2:")
    assert rows[1]["identity_key"].startswith("rq:v2:")
    assert rows[0]["identity_key"] != rows[1]["identity_key"]
    assert rows[0]["answer_source"] == "unverified"
    assert rows[1]["answer_source"] == "not_provided"
    assert (
        migrated.connection.execute(
            "SELECT real_question_id FROM question_sessions WHERE session_key = 'session-101'"
        ).fetchone()[0]
        == 101
    )
    assert migrated.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    migrated.close()


def test_v11_to_v12_identity_collision_rolls_back_without_merging_rows(tmp_path):
    db_path = tmp_path / "v11-identity-collision.sqlite3"
    storage = SQLiteStorage(db_path)
    storage.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE schema_meta SET value = '11' WHERE key = 'version'")
        _insert_v11_real_question(connection, question_id=201, identity_key="legacy-a")
        _insert_v11_real_question(connection, question_id=202, identity_key="legacy-b")
        connection.commit()

    with pytest.raises(RuntimeError, match="identity v2 collision.*201.*202"):
        SQLiteStorage(db_path)

    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT id, identity_key, answer_source FROM real_questions ORDER BY id"
        ).fetchall()
    assert version == "11"
    assert rows == [
        (201, "legacy-a", "not_provided"),
        (202, "legacy-b", "not_provided"),
    ]


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

    assert storage.schema_version == SCHEMA_VERSION == 13
    assert storage.count_events() == 0
    storage.close()


def test_fresh_database_has_v11_question_session_tables(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "question-sessions.sqlite3")

    tables = {
        row[0]
        for row in storage.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    columns = {
        row[1]
        for row in storage.connection.execute("PRAGMA table_info(question_sessions)")
    }

    assert storage.schema_version == SCHEMA_VERSION == 13
    assert {"question_sessions", "question_session_events"} <= tables
    assert {
        "scope_origin",
        "target_id",
        "source_kind",
        "source_item_key",
        "question_identity",
        "question_snapshot_hash",
        "question_snapshot_json",
        "status",
        "current_stage",
        "current_material_index",
        "current_material_page",
        "current_prompt_index",
        "current_prompt_page",
        "answer_revealed",
        "explanation_revealed",
        "created_by",
    } <= columns
    storage.close()


def test_schema_v9_migrates_through_v11_and_preserves_rows(
    tmp_path,
) -> None:
    db_path = tmp_path / "version-nine.sqlite3"
    storage = SQLiteStorage(db_path)
    event_id = storage.upsert_event(make_event(title="迁移前活动"))
    storage.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE schema_meta SET value = '9' WHERE key = 'version'")
        connection.commit()

    migrated = SQLiteStorage(db_path)
    tables = {
        row[0]
        for row in migrated.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert migrated.schema_version == 13
    assert migrated.get_event(event_id).title == "迁移前活动"
    assert {
        "structured_imports",
        "structured_materials",
        "structured_item_bindings",
        "structured_subquestions",
        "structured_blocks",
        "structured_material_relations",
    } <= tables
    migrated.close()


def test_schema_v9_migration_rolls_back_on_failure(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "migration-rollback.sqlite3"
    storage = SQLiteStorage(db_path)
    storage.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE schema_meta SET value = '9' WHERE key = 'version'")
        connection.commit()

    original = storage_module._MIGRATIONS[9]

    def fail_after_write(connection):
        connection.execute("CREATE TABLE migration_probe (id INTEGER PRIMARY KEY)")
        raise RuntimeError("synthetic v10 migration failure")

    monkeypatch.setitem(storage_module._MIGRATIONS, 9, fail_after_write)
    with pytest.raises(RuntimeError, match="synthetic v10 migration failure"):
        SQLiteStorage(db_path)
    monkeypatch.setitem(storage_module._MIGRATIONS, 9, original)

    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()[0]
        probe = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'migration_probe'"
        ).fetchone()
    assert version == "9"
    assert probe is None


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
        match=r"schema version 99 is newer than supported version 13",
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


def test_storage_lists_only_successful_daily_content_by_target_and_identity(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    storage.bind_target("aiocqhttp:group:1", "一群")
    storage.bind_target("aiocqhttp:group:2", "二群")
    first = storage.claim_daily_content(
        content_date="2026-09-21",
        target_id=1,
        content_type="daily_question",
        body={"question": "A"},
        source_kind="real_question",
        source_item_key="1",
    )
    assert first is not None
    storage.finish_daily_content(first, success=True)
    failed = storage.claim_daily_content(
        content_date="2026-09-22",
        target_id=1,
        content_type="daily_question",
        body={"question": "B"},
        source_kind="real_question",
        source_item_key="2",
    )
    assert failed is not None
    storage.finish_daily_content(failed, success=False, error_summary="network")
    other_target = storage.claim_daily_content(
        content_date="2026-09-21",
        target_id=2,
        content_type="daily_question",
        body={"question": "C"},
        source_kind="real_question",
        source_item_key="3",
    )
    assert other_target is not None
    storage.finish_daily_content(other_target, success=True)

    assert storage.list_sent_content_keys(1, "daily_question") == {
        ("real_question", "1")
    }
    assert storage.list_sent_content_keys(2, "daily_question") == {
        ("real_question", "3")
    }
    assert storage.list_sent_content_keys(1, "daily_case") == set()


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


def test_schema_v9_adds_daily_axes_and_stable_content_identity(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    daily_columns = {
        row[1] for row in storage.connection.execute("PRAGMA table_info(daily_plans)")
    }
    history_columns = {
        row[1]
        for row in storage.connection.execute("PRAGMA table_info(daily_contents)")
    }

    assert storage.schema_version == SCHEMA_VERSION == 13
    assert {
        "question_type_selection_mode",
        "fixed_question_type",
        "rotation_question_types",
        "question_type_rotation_start_date",
        "question_type_rotation_start_index",
    } <= daily_columns
    assert {
        "source_kind",
        "source_item_key",
        "resolved_subject",
        "resolved_question_type",
        "resolved_origin",
    } <= history_columns

    target = storage.bind_target("aiocqhttp:group:1", "一群")
    history_id = storage.claim_daily_content(
        content_date="2026-09-21",
        target_id=target["id"],
        content_type="daily_question",
        body={"question": "题干"},
        source_kind="library_mock",
        source_item_key="42",
        resolved_subject="criminal_law",
        resolved_question_type="multiple_choice",
        resolved_origin="mock",
    )
    record = storage.list_daily_contents()[0]
    assert history_id is not None
    assert record["source_kind"] == "library_mock"
    assert record["source_item_key"] == "42"
    assert record["resolved_question_type"] == "multiple_choice"
    storage.close()


def test_event_relation_preserves_both_sources_and_canonical_publication_state(
    tmp_path,
) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    first_id = storage.upsert_event(make_event(source_key="china_jm"))
    second_id = storage.upsert_event(
        replace(make_event(source_key="extra:1"), source_item_key="mirror-1")
    )

    storage.record_event_relation(first_id, second_id, "canonical-hash")

    related = storage.related_events(second_id)
    assert {item.id for item in related} == {first_id, second_id}
    assert storage.has_canonical_publication("canonical-hash") is False
    storage.record_canonical_publication("canonical-hash")
    assert storage.has_canonical_publication("canonical-hash") is True
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

    assert storage.schema_version == SCHEMA_VERSION == 13
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
    assert storage.schema_version == SCHEMA_VERSION == 13
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
    assert questions[0].answer_source == "official"
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
    assert reopened.schema_version == 13
    reopened.close()


def test_storage_round_trips_independent_question_type_plan(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    plan = DailyPlan(
        content_type="daily_question",
        enabled=True,
        selection_mode="rotation",
        rotation_subjects=("intellectual_property", "economic_law"),
        rotation_start_date="2026-09-21",
        question_origin="real",
        question_type_selection_mode="rotation",
        rotation_question_types=("multiple_choice", "case_analysis"),
        question_type_rotation_start_date="2026-09-22",
        question_type_rotation_start_index=1,
    )
    storage.upsert_daily_plan(plan)

    reopened = SQLiteStorage(tmp_path / "runtime.sqlite3")
    loaded = reopened.get_daily_plan(None, "daily_question")
    assert loaded is not None
    assert loaded.question_type_selection_mode == "rotation"
    assert loaded.rotation_question_types == ("multiple_choice", "case_analysis")
    assert loaded.question_type_rotation_start_date == "2026-09-22"
    assert loaded.question_type_rotation_start_index == 1
    reopened.close()


def test_storage_runs_the_v3_to_v8_migration_path(tmp_path) -> None:
    db_path = tmp_path / "v3.sqlite3"
    storage = SQLiteStorage(db_path)
    storage.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE schema_meta SET value = '3' WHERE key = 'version'")
        connection.execute("DROP TABLE real_questions")
        connection.execute("DROP TABLE daily_plans")
        connection.commit()

    migrated = SQLiteStorage(db_path)
    assert migrated.schema_version == SCHEMA_VERSION == 13
    assert migrated.real_question_inventory()["count"] == 0
    assert migrated.list_daily_plans() == []
    migrated.close()


def test_schema_v10_to_v11_preserves_existing_events_targets_and_plans(tmp_path):
    db_path = tmp_path / "version-ten.sqlite3"
    storage = SQLiteStorage(db_path)
    event_id = storage.upsert_event(make_event(title="v10 活动"))
    target = storage.bind_target("aiocqhttp:GroupMessage:session-v10", "v10 群")
    storage.upsert_daily_plan(DailyPlan("daily_question", enabled=True), target["id"])
    storage.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE question_session_events")
        connection.execute("DROP TABLE question_sessions")
        connection.execute("UPDATE schema_meta SET value = '10' WHERE key = 'version'")
        connection.commit()

    upgraded = SQLiteStorage(db_path)
    assert upgraded.schema_version == 13
    assert upgraded.get_event(event_id).title == "v10 活动"
    assert upgraded.get_target(target["id"])["label"] == "v10 群"
    assert upgraded.get_daily_plan(target["id"], "daily_question").enabled is True
    assert (
        upgraded.connection.execute(
            "SELECT COUNT(*) FROM question_sessions"
        ).fetchone()[0]
        == 0
    )
    upgraded.close()


def test_schema_v10_to_v11_migration_rolls_back_on_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "v11-rollback.sqlite3"
    storage = SQLiteStorage(db_path)
    storage.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE question_session_events")
        connection.execute("DROP TABLE question_sessions")
        connection.execute("UPDATE schema_meta SET value = '10' WHERE key = 'version'")
        connection.commit()

    def fail_after_write(connection):
        connection.execute("CREATE TABLE migration_probe (id INTEGER PRIMARY KEY)")
        raise RuntimeError("synthetic v11 migration failure")

    monkeypatch.setitem(storage_module._MIGRATIONS, 10, fail_after_write)
    with pytest.raises(RuntimeError, match="synthetic v11 migration failure"):
        SQLiteStorage(db_path)

    with sqlite3.connect(db_path) as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert version == "10"
    assert "migration_probe" not in tables
    assert "question_sessions" not in tables


def test_storage_migrates_v6_sources_without_losing_item_links(tmp_path) -> None:
    db_path = tmp_path / "v6-library.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES ('version', '6');
            CREATE TABLE library_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_kind TEXT NOT NULL,
                title TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                source_url TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                session_origin TEXT NOT NULL,
                original_filename TEXT,
                mime_type TEXT,
                storage_path TEXT,
                metadata_json TEXT NOT NULL,
                UNIQUE(created_by, content_hash)
            );
            CREATE TABLE learning_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                identity TEXT NOT NULL,
                item_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                subjects_json TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                source_summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                UNIQUE(created_by, item_hash)
            );
            CREATE TABLE learning_item_sources (
                item_id INTEGER NOT NULL REFERENCES learning_items(id) ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES library_sources(id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                relationship TEXT NOT NULL,
                PRIMARY KEY(item_id, source_id, relationship)
            );
            INSERT INTO library_sources(
                id, source_kind, title, raw_text, source_url, content_hash,
                created_at, created_by, session_origin, metadata_json
            ) VALUES (
                7, 'user_text', '旧来源', '旧正文', '', 'same-hash',
                '2026-09-21T00:00:00+00:00', '42', 'private:42', '{}'
            );
            INSERT INTO learning_items(
                id, item_type, identity, item_hash, title, subjects_json,
                verification_status, source_summary, created_at, updated_at,
                created_by, metadata_json
            ) VALUES (
                9, 'case', 'user_case', 'item-hash', '旧条目', '[]',
                'unverified', '旧摘要', '2026-09-21T00:00:00+00:00',
                '2026-09-21T00:00:00+00:00', '42', '{}'
            );
            INSERT INTO learning_item_sources(item_id, source_id, locator, relationship)
            VALUES (9, 7, '', 'primary_evidence');
            """
        )

    storage = SQLiteStorage(db_path)

    assert storage.schema_version == SCHEMA_VERSION == 13
    assert [
        tuple(row)
        for row in storage.connection.execute(
            "SELECT item_id, source_id FROM learning_item_sources"
        ).fetchall()
    ] == [(9, 7)]
    storage.connection.execute(
        """
        INSERT INTO library_sources(
            source_kind, title, raw_text, source_url, content_hash,
            created_at, created_by, session_origin, metadata_json
        ) VALUES ('user_text', '新来源', '旧正文', 'https://example.test/case',
                  'same-hash', '2026-09-21T00:00:00+00:00', '42', 'private:42', '{}')
        """
    )
    storage.connection.commit()
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM library_sources WHERE content_hash = 'same-hash'"
        ).fetchone()[0]
        == 2
    )
    assert (
        storage.connection.execute(
            "SELECT active FROM learning_items WHERE id = 9"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'learning_review_items'"
        ).fetchone()[0]
        == "learning_review_items"
    )
    storage.close()
