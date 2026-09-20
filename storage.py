from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime as DateTime
from datetime import timezone
from pathlib import Path
from typing import Any

if __package__ and "." in __package__:
    from .content import RealQuestion, subject_filter
    from .daily_plans import DailyPlan
    from .models import CaseItem, EventDate, LawUpdate, LegalEvent, SourceDocument
else:
    from content import RealQuestion, subject_filter
    from daily_plans import DailyPlan
    from models import CaseItem, EventDate, LawUpdate, LegalEvent, SourceDocument

SCHEMA_VERSION = 9


class UnsupportedSchemaVersionError(RuntimeError):
    """Raised when a database requires a schema newer than this plugin supports."""


def _migrate_0_to_1(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
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
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS event_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            datetime TEXT,
            timezone TEXT NOT NULL,
            label TEXT NOT NULL,
            evidence_text TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS source_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            success INTEGER NOT NULL,
            discovered_count INTEGER NOT NULL,
            error_summary TEXT
        )
        """
    )


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _migrate_1_to_2(connection: sqlite3.Connection) -> None:
    _add_column_if_missing(
        connection, "events", "registration_method", "TEXT NOT NULL DEFAULT ''"
    )
    _add_column_if_missing(connection, "events", "summary", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(connection, "events", "source_published_at", "TEXT")
    _add_column_if_missing(connection, "events", "last_seen_at", "TEXT")
    _add_column_if_missing(
        connection, "events", "revision", "INTEGER NOT NULL DEFAULT 1"
    )
    _add_column_if_missing(
        connection, "event_dates", "confirmed", "INTEGER NOT NULL DEFAULT 1"
    )
    _add_column_if_missing(
        connection,
        "source_runs",
        "source_type",
        "TEXT NOT NULL DEFAULT 'event'",
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS source_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            source_item_key TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content_type TEXT NOT NULL,
            attachments_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            UNIQUE(source_key, source_item_key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS event_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,
            changed_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            changed_fields_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            UNIQUE(event_id, revision)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS publish_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unified_msg_origin TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,
            target_id INTEGER NOT NULL REFERENCES publish_targets(id),
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            finished_at TEXT,
            error_summary TEXT,
            UNIQUE(event_id, revision, target_id, kind)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            event_date_id INTEGER NOT NULL REFERENCES event_dates(id) ON DELETE CASCADE,
            deadline_value TEXT NOT NULL,
            target_id INTEGER NOT NULL REFERENCES publish_targets(id),
            reminder_offset INTEGER NOT NULL,
            status TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            finished_at TEXT,
            error_summary TEXT,
            UNIQUE(event_id, event_date_id, deadline_value, target_id, reminder_offset)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS case_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            source_item_key TEXT NOT NULL,
            title TEXT NOT NULL,
            published_at TEXT,
            source_url TEXT NOT NULL,
            authority TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            UNIQUE(source_key, source_item_key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_date TEXT NOT NULL,
            target_id INTEGER NOT NULL REFERENCES publish_targets(id),
            content_type TEXT NOT NULL,
            source_item_id INTEGER,
            body_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            finished_at TEXT,
            error_summary TEXT,
            UNIQUE(content_date, target_id, content_type)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS law_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            source_item_key TEXT NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            promulgation_date TEXT,
            effective_date TEXT,
            source_url TEXT NOT NULL,
            status TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            UNIQUE(source_key, source_item_key)
        )
        """
    )


def _migrate_2_to_3(connection: sqlite3.Connection) -> None:
    """Move reminder identity from replaceable event-date rows to logical values."""
    connection.execute("ALTER TABLE reminders RENAME TO reminders_v2")
    connection.execute(
        """
        CREATE TABLE reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            date_kind TEXT NOT NULL,
            deadline_value TEXT NOT NULL,
            target_id INTEGER NOT NULL REFERENCES publish_targets(id),
            reminder_offset INTEGER NOT NULL,
            status TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            finished_at TEXT,
            error_summary TEXT,
            UNIQUE(event_id, date_kind, deadline_value, target_id, reminder_offset)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO reminders(
            event_id, date_kind, deadline_value, target_id, reminder_offset,
            status, attempted_at, finished_at, error_summary
        )
        SELECT old.event_id, date.kind, old.deadline_value, old.target_id,
            old.reminder_offset, old.status, old.attempted_at, old.finished_at,
            old.error_summary
        FROM reminders_v2 AS old
        JOIN event_dates AS date ON date.id = old.event_date_id
        ON CONFLICT(event_id, date_kind, deadline_value, target_id, reminder_offset)
        DO UPDATE SET
            status = CASE
                WHEN reminders.status = 'sent' OR excluded.status = 'sent'
                THEN 'sent'
                ELSE excluded.status
            END,
            attempted_at = CASE
                WHEN excluded.attempted_at > reminders.attempted_at
                THEN excluded.attempted_at
                ELSE reminders.attempted_at
            END,
            finished_at = CASE
                WHEN reminders.status = 'sent' THEN reminders.finished_at
                ELSE excluded.finished_at
            END,
            error_summary = CASE
                WHEN reminders.status = 'sent' THEN reminders.error_summary
                ELSE excluded.error_summary
            END
        """
    )
    connection.execute("DROP TABLE reminders_v2")


def _migrate_3_to_4(connection: sqlite3.Connection) -> None:
    """Add verified question inventory and independent daily-plan state."""
    if _table_exists(connection, "case_items"):
        _add_column_if_missing(
            connection, "case_items", "subjects_json", "TEXT NOT NULL DEFAULT '[]'"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS real_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_key TEXT NOT NULL UNIQUE,
            source_name TEXT NOT NULL,
            exam_name TEXT NOT NULL,
            exam_year TEXT NOT NULL,
            exam_date TEXT NOT NULL,
            paper TEXT NOT NULL,
            question_number TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_locator TEXT NOT NULL,
            subject TEXT NOT NULL,
            question_type TEXT NOT NULL,
            stem TEXT NOT NULL,
            options_json TEXT NOT NULL,
            answer_json TEXT,
            explanation TEXT NOT NULL,
            answer_source TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER REFERENCES publish_targets(id) ON DELETE CASCADE,
            content_type TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            time TEXT NOT NULL,
            selection_mode TEXT NOT NULL,
            fixed_subject TEXT,
            rotation_subjects_json TEXT NOT NULL,
            rotation_start_date TEXT,
            rotation_start_index INTEGER NOT NULL,
            question_origin TEXT NOT NULL,
            question_type TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS daily_plans_global_unique
        ON daily_plans(content_type) WHERE target_id IS NULL
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS daily_plans_target_unique
        ON daily_plans(target_id, content_type) WHERE target_id IS NOT NULL
        """
    )


def _migrate_4_to_5(connection: sqlite3.Connection) -> None:
    """Separate source-provided case subjects from operator classifications."""
    if not _table_exists(connection, "case_items"):
        return
    _add_column_if_missing(
        connection,
        "case_items",
        "manual_subjects_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    # Before v5 every stored subject could have come from /law case-tag. Keep
    # those labels as manual classifications instead of allowing the next
    # source refresh to erase them.
    connection.execute(
        """
        UPDATE case_items
        SET manual_subjects_json = subjects_json,
            subjects_json = '[]'
        WHERE manual_subjects_json = '[]' AND subjects_json <> '[]'
        """
    )


def _migrate_5_to_6(connection: sqlite3.Connection) -> None:
    """Create the evidence-preserving user learning library."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS library_sources (
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
        CREATE INDEX IF NOT EXISTS idx_library_sources_hash
            ON library_sources(content_hash);
        CREATE TABLE IF NOT EXISTS learning_items (
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
        CREATE INDEX IF NOT EXISTS idx_learning_items_type
            ON learning_items(item_type);
        CREATE INDEX IF NOT EXISTS idx_learning_items_subjects
            ON learning_items(subjects_json);
        CREATE INDEX IF NOT EXISTS idx_learning_items_created_by
            ON learning_items(created_by);
        CREATE TABLE IF NOT EXISTS learning_item_sources (
            item_id INTEGER NOT NULL REFERENCES learning_items(id) ON DELETE CASCADE,
            source_id INTEGER NOT NULL REFERENCES library_sources(id) ON DELETE CASCADE,
            locator TEXT NOT NULL,
            relationship TEXT NOT NULL,
            PRIMARY KEY(item_id, source_id, relationship)
        );
        CREATE TABLE IF NOT EXISTS learning_cases (
            item_id INTEGER PRIMARY KEY REFERENCES learning_items(id) ON DELETE CASCADE,
            case_number TEXT NOT NULL,
            authority TEXT NOT NULL,
            case_summary TEXT NOT NULL,
            issues_json TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            result_text TEXT NOT NULL,
            practice_notes_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learning_questions (
            item_id INTEGER PRIMARY KEY REFERENCES learning_items(id) ON DELETE CASCADE,
            question_identity TEXT NOT NULL,
            question_type TEXT NOT NULL,
            stem TEXT NOT NULL,
            options_json TEXT NOT NULL,
            answer_json TEXT,
            explanation TEXT NOT NULL,
            exam_name TEXT NOT NULL,
            exam_year TEXT NOT NULL,
            paper TEXT NOT NULL,
            question_number TEXT NOT NULL,
            answer_source TEXT NOT NULL
        );
        """
    )


def _migrate_6_to_7(connection: sqlite3.Connection) -> None:
    """Allow one evidence hash to retain one row per distinct source URL."""
    if not _table_exists(connection, "library_sources"):
        return

    source_rows = connection.execute(
        "SELECT * FROM library_sources ORDER BY id"
    ).fetchall()
    links = connection.execute(
        """
        SELECT item_id, source_id, locator, relationship
        FROM learning_item_sources
        ORDER BY item_id, source_id, relationship
        """
    ).fetchall()
    connection.execute(
        """
        CREATE TABLE library_sources_v7 (
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
            UNIQUE(created_by, content_hash, source_url)
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO library_sources_v7(
            id, source_kind, title, raw_text, source_url, content_hash,
            created_at, created_by, session_origin, original_filename,
            mime_type, storage_path, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["id"],
                row["source_kind"],
                row["title"],
                row["raw_text"],
                row["source_url"],
                row["content_hash"],
                row["created_at"],
                row["created_by"],
                row["session_origin"],
                row["original_filename"],
                row["mime_type"],
                row["storage_path"],
                row["metadata_json"],
            )
            for row in source_rows
        ],
    )
    connection.execute("DROP TABLE library_sources")
    connection.execute("ALTER TABLE library_sources_v7 RENAME TO library_sources")
    connection.execute(
        "CREATE INDEX idx_library_sources_hash ON library_sources(content_hash)"
    )
    connection.executemany(
        """
        INSERT INTO learning_item_sources(item_id, source_id, locator, relationship)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                row["item_id"],
                row["source_id"],
                row["locator"],
                row["relationship"],
            )
            for row in links
        ],
    )


def _migrate_7_to_8(connection: sqlite3.Connection) -> None:
    """Track active split results and persist candidates needing review."""
    _add_column_if_missing(
        connection, "learning_items", "active", "INTEGER NOT NULL DEFAULT 1"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS learning_review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES library_sources(id) ON DELETE CASCADE,
            candidate_key TEXT NOT NULL,
            material_type TEXT NOT NULL,
            locator TEXT NOT NULL,
            raw_fragment TEXT NOT NULL,
            proposed_structure_json TEXT NOT NULL,
            review_reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_id, candidate_key)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_learning_review_items_source
            ON learning_review_items(source_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_learning_review_items_status
            ON learning_review_items(status)
        """
    )


def _migrate_8_to_9(connection: sqlite3.Connection) -> None:
    """Add independent question-type plans and stable daily/source identities."""
    if not _table_exists(connection, "daily_plans"):
        connection.execute(
            """
            CREATE TABLE daily_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER,
                content_type TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                time TEXT NOT NULL,
                selection_mode TEXT NOT NULL,
                fixed_subject TEXT,
                rotation_subjects_json TEXT NOT NULL,
                rotation_start_date TEXT,
                rotation_start_index INTEGER NOT NULL,
                question_origin TEXT NOT NULL,
                question_type TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
    if not _table_exists(connection, "daily_contents"):
        connection.execute(
            """
            CREATE TABLE daily_contents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_date TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                content_type TEXT NOT NULL,
                source_item_id INTEGER,
                body_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                finished_at TEXT,
                error_summary TEXT,
                UNIQUE(content_date, target_id, content_type)
            )
            """
        )
    for column, definition in (
        ("question_type_selection_mode", "TEXT NOT NULL DEFAULT 'random'"),
        ("fixed_question_type", "TEXT"),
        ("rotation_question_types", "TEXT NOT NULL DEFAULT '[]'"),
        ("question_type_rotation_start_date", "TEXT"),
        ("question_type_rotation_start_index", "INTEGER NOT NULL DEFAULT 0"),
    ):
        _add_column_if_missing(connection, "daily_plans", column, definition)
    connection.execute(
        """
        UPDATE daily_plans
        SET question_type_selection_mode = CASE
                WHEN question_type IS NULL OR trim(question_type) = '' THEN 'random'
                ELSE 'fixed'
            END,
            fixed_question_type = CASE
                WHEN question_type IS NULL OR trim(question_type) = ''
                THEN fixed_question_type
                ELSE question_type
            END
        WHERE question_type_selection_mode = 'random'
           OR fixed_question_type IS NULL
        """
    )
    for column, definition in (
        ("source_kind", "TEXT"),
        ("source_item_key", "TEXT"),
        ("resolved_subject", "TEXT"),
        ("resolved_question_type", "TEXT"),
        ("resolved_origin", "TEXT"),
    ):
        _add_column_if_missing(connection, "daily_contents", column, definition)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS event_relations (
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            related_event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            canonical_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(event_id, related_event_id),
            CHECK(event_id <> related_event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_event_relations_canonical
            ON event_relations(canonical_key);
        CREATE TABLE IF NOT EXISTS canonical_publications (
            canonical_key TEXT NOT NULL,
            target_id INTEGER,
            first_published_at TEXT NOT NULL,
            PRIMARY KEY(canonical_key, target_id)
        );
        """
    )


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    0: _migrate_0_to_1,
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
    3: _migrate_3_to_4,
    4: _migrate_4_to_5,
    5: _migrate_5_to_6,
    6: _migrate_6_to_7,
    7: _migrate_7_to_8,
    8: _migrate_8_to_9,
}


@dataclass(frozen=True, slots=True)
class EventUpsertResult:
    event_id: int
    is_new: bool
    revision: int
    changed_fields: tuple[str, ...]


class SQLiteStorage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    @property
    def schema_version(self) -> int:
        row = self._connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'",
        ).fetchone()
        return int(row["value"]) if row else 0

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the owned connection to bounded repositories only."""
        if self._connection is None:
            raise RuntimeError("storage is closed")
        return self._connection

    def get_rotation_anchor(self, content_type: str) -> str | None:
        if content_type not in {
            "daily_case",
            "daily_question",
            "daily_question_type",
        }:
            raise ValueError(f"unsupported daily content type: {content_type}")
        row = self._connection.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (f"rotation_anchor:{content_type}",),
        ).fetchone()
        return str(row["value"]) if row else None

    def set_rotation_anchor(self, content_type: str, value: str) -> None:
        if content_type not in {
            "daily_case",
            "daily_question",
            "daily_question_type",
        }:
            raise ValueError(f"unsupported daily content type: {content_type}")
        self._connection.execute(
            """
            INSERT INTO schema_meta(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (f"rotation_anchor:{content_type}", value),
        )
        self._connection.commit()

    def _initialize_schema(self) -> None:
        connection = self._connection
        connection.execute("BEGIN")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'version'"
            ).fetchone()
            if row is None:
                current_version = 0
                connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('version', '0')"
                )
            else:
                try:
                    current_version = int(row["value"])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"invalid database schema version: {row['value']!r}"
                    ) from exc

            if current_version > SCHEMA_VERSION:
                raise UnsupportedSchemaVersionError(
                    "database schema version "
                    f"{current_version} is newer than supported version "
                    f"{SCHEMA_VERSION}"
                )
            if current_version < 0:
                raise RuntimeError(
                    f"invalid database schema version: {current_version}"
                )

            while current_version < SCHEMA_VERSION:
                migration = _MIGRATIONS.get(current_version)
                if migration is None:
                    raise RuntimeError(
                        "no migration is registered for database schema version "
                        f"{current_version}"
                    )
                migration(connection)
                next_version = current_version + 1
                connection.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'version'",
                    (str(next_version),),
                )
                current_version = next_version
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def upsert_event(self, event: LegalEvent) -> int:
        return self.upsert_event_detailed(event).event_id

    def upsert_event_detailed(self, event: LegalEvent) -> EventUpsertResult:
        now = event.last_seen_at or event.updated_at or DateTime.now(timezone.utc)
        connection = self._connection
        existing = connection.execute(
            "SELECT * FROM events WHERE source_key = ? AND source_item_key = ?",
            (event.source_key, event.source_item_key),
        ).fetchone()
        incoming_dates = _dates_snapshot(event.dates)

        if existing:
            event_id = int(existing["id"])
            old_dates = self._event_dates_snapshot(event_id)
            changed_fields = _changed_event_fields(
                existing, event, old_dates, incoming_dates
            )
            revision = int(existing["revision"])
            if changed_fields:
                revision += 1
            connection.execute(
                """
                UPDATE events SET title = ?, source_url = ?, organizer = ?,
                    event_type = ?, eligibility = ?, status = ?,
                    raw_content_hash = ?, updated_at = ?, metadata_json = ?,
                    registration_method = ?, summary = ?, source_published_at = ?,
                    last_seen_at = ?, revision = ?
                WHERE id = ?
                """,
                (
                    event.title,
                    event.source_url,
                    event.organizer,
                    event.event_type,
                    event.eligibility,
                    event.status,
                    event.raw_content_hash,
                    _serialize_datetime(event.updated_at),
                    json.dumps(event.metadata, ensure_ascii=False, sort_keys=True),
                    event.registration_method,
                    event.summary,
                    _serialize_datetime(event.source_published_at),
                    _serialize_datetime(now),
                    revision,
                    event_id,
                ),
            )
            if changed_fields:
                self._insert_revision(
                    event_id=event_id,
                    revision=revision,
                    event=event,
                    changed_fields=changed_fields,
                )
        else:
            cursor = connection.execute(
                """
                INSERT INTO events(
                    source_key, source_item_key, title, source_url, organizer,
                    event_type, eligibility, status, raw_content_hash,
                    discovered_at, updated_at, metadata_json, registration_method,
                    summary, source_published_at, last_seen_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _serialize_datetime(event.discovered_at),
                    _serialize_datetime(event.updated_at),
                    json.dumps(event.metadata, ensure_ascii=False, sort_keys=True),
                    event.registration_method,
                    event.summary,
                    _serialize_datetime(event.source_published_at),
                    _serialize_datetime(now),
                    max(1, event.revision),
                ),
            )
            event_id = int(cursor.lastrowid)
            revision = max(1, event.revision)
            changed_fields = ()
            self._insert_revision(
                event_id=event_id,
                revision=revision,
                event=event,
                changed_fields=("initial",),
            )

        connection.execute("DELETE FROM event_dates WHERE event_id = ?", (event_id,))
        connection.executemany(
            """
            INSERT INTO event_dates(
                event_id, kind, datetime, timezone, label, evidence_text, confirmed
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    event_id,
                    date.kind,
                    _serialize_datetime(date.datetime),
                    date.timezone,
                    date.label,
                    date.evidence_text,
                    int(date.confirmed),
                )
                for date in event.dates
            ],
        )
        connection.commit()
        return EventUpsertResult(
            event_id=event_id,
            is_new=existing is None,
            revision=revision,
            changed_fields=tuple(changed_fields),
        )

    def _insert_revision(
        self,
        *,
        event_id: int,
        revision: int,
        event: LegalEvent,
        changed_fields: Iterable[str],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO event_revisions(
                event_id, revision, changed_at, content_hash,
                changed_fields_json, snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                revision,
                _serialize_datetime(DateTime.now(timezone.utc)),
                event.raw_content_hash,
                json.dumps(list(changed_fields), ensure_ascii=False),
                json.dumps(_event_snapshot(event, revision), ensure_ascii=False),
            ),
        )

    def _event_dates_snapshot(self, event_id: int) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM event_dates WHERE event_id = ? ORDER BY id", (event_id,)
        ).fetchall()
        return [
            {
                "kind": row["kind"],
                "datetime": row["datetime"],
                "timezone": row["timezone"],
                "label": row["label"],
                "evidence_text": row["evidence_text"],
                "confirmed": bool(row["confirmed"]),
            }
            for row in rows
        ]

    def upsert_source_document(self, document: SourceDocument) -> int:
        self._connection.execute(
            """
            INSERT INTO source_documents(
                source_key, source_item_key, url, title, raw_text, fetched_at,
                content_hash, content_type, attachments_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key, source_item_key) DO UPDATE SET
                url = excluded.url, title = excluded.title, raw_text = excluded.raw_text,
                fetched_at = excluded.fetched_at, content_hash = excluded.content_hash,
                content_type = excluded.content_type,
                attachments_json = excluded.attachments_json,
                metadata_json = excluded.metadata_json
            """,
            (
                document.source_key,
                document.source_item_key,
                document.url,
                document.title,
                document.content,
                _serialize_datetime(document.fetched_at),
                document.content_hash,
                document.content_type,
                json.dumps(list(document.attachments), ensure_ascii=False),
                json.dumps(document.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT id FROM source_documents WHERE source_key = ? AND source_item_key = ?",
            (document.source_key, document.source_item_key),
        ).fetchone()
        return int(row["id"])

    def get_source_document(
        self, source_key: str, source_item_key: str
    ) -> SourceDocument | None:
        row = self._connection.execute(
            "SELECT * FROM source_documents WHERE source_key = ? AND source_item_key = ?",
            (source_key, source_item_key),
        ).fetchone()
        if row is None:
            return None
        return SourceDocument(
            source_key=row["source_key"],
            source_item_key=row["source_item_key"],
            url=row["url"],
            title=row["title"],
            content=row["raw_text"],
            fetched_at=row["fetched_at"],
            content_hash=row["content_hash"],
            metadata=json.loads(row["metadata_json"]),
            content_type=row["content_type"],
            attachments=tuple(json.loads(row["attachments_json"])),
        )

    def touch_event_seen(
        self, source_key: str, source_item_key: str, last_seen_at: DateTime
    ) -> bool:
        cursor = self._connection.execute(
            """
            UPDATE events SET last_seen_at = ?
            WHERE source_key = ? AND source_item_key = ?
            """,
            (_serialize_datetime(last_seen_at), source_key, source_item_key),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def list_events(
        self, limit: int = 20, *, status: str | None = None
    ) -> list[LegalEvent]:
        if status:
            rows = self._connection.execute(
                """
                SELECT * FROM events WHERE status = ?
                ORDER BY updated_at DESC, id DESC LIMIT ?
                """,
                (status, max(1, limit)),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM events ORDER BY updated_at DESC, id DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def get_event(self, event_id: int) -> LegalEvent | None:
        row = self._connection.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._event_from_row(row) if row else None

    def get_event_by_key(
        self, source_key: str, source_item_key: str
    ) -> LegalEvent | None:
        row = self._connection.execute(
            "SELECT * FROM events WHERE source_key = ? AND source_item_key = ?",
            (source_key, source_item_key),
        ).fetchone()
        return self._event_from_row(row) if row else None

    def record_event_relation(
        self, event_id: int, related_event_id: int, canonical_key: str
    ) -> None:
        if event_id == related_event_id:
            return
        now = _serialize_datetime(DateTime.now(timezone.utc))
        self._connection.executemany(
            """
            INSERT OR IGNORE INTO event_relations(
                event_id, related_event_id, canonical_key, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (event_id, related_event_id, canonical_key, now),
                (related_event_id, event_id, canonical_key, now),
            ],
        )
        self._connection.commit()

    def related_events(self, event_id: int) -> list[LegalEvent]:
        rows = self._connection.execute(
            """
            SELECT DISTINCT e.*
            FROM events AS e
            JOIN event_relations AS relation
              ON relation.related_event_id = e.id
            WHERE relation.event_id = ? OR e.id = ?
            ORDER BY e.id
            """,
            (event_id, event_id),
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def record_canonical_publication(
        self, canonical_key: str, target_id: int | None = None
    ) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO canonical_publications(
                canonical_key, target_id, first_published_at
            ) VALUES (?, ?, ?)
            """,
            (
                canonical_key,
                target_id,
                _serialize_datetime(DateTime.now(timezone.utc)),
            ),
        )
        self._connection.commit()

    def has_canonical_publication(
        self, canonical_key: str, target_id: int | None = None
    ) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM canonical_publications
            WHERE canonical_key = ? AND (target_id = ? OR target_id IS NULL)
            """,
            (canonical_key, target_id),
        ).fetchone()
        return row is not None

    def count_events(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM events"
        ).fetchone()
        return int(row["count"])

    def list_deadlines(
        self, *, now: DateTime | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        events = self.list_events(limit=1000)
        result: list[dict[str, Any]] = []
        for event in events:
            for date in event.dates:
                if date.kind not in {"registration_deadline", "submission_deadline"}:
                    continue
                if not date.confirmed or date.datetime is None:
                    continue
                if now is not None and date.datetime < now:
                    continue
                result.append({"event": event, "date": date})
        result.sort(key=lambda item: item["date"].datetime)
        return result[: max(1, limit)]

    def list_event_revisions(self, event_id: int) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM event_revisions WHERE event_id = ? ORDER BY revision",
            (event_id,),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "event_id": int(row["event_id"]),
                "revision": int(row["revision"]),
                "changed_at": row["changed_at"],
                "content_hash": row["content_hash"],
                "changed_fields": json.loads(row["changed_fields_json"]),
                "snapshot": json.loads(row["snapshot_json"]),
            }
            for row in rows
        ]

    def record_source_run(
        self,
        *,
        source_key: str,
        started_at: DateTime,
        finished_at: DateTime,
        success: bool,
        discovered_count: int,
        error_summary: str | None = None,
        source_type: str = "event",
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO source_runs(
                source_key, source_type, started_at, finished_at, success,
                discovered_count, error_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_key,
                source_type,
                _serialize_datetime(started_at),
                _serialize_datetime(finished_at),
                int(success),
                discovered_count,
                error_summary,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def list_source_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM source_runs ORDER BY id DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [_source_run_from_row(row) for row in rows]

    def latest_source_run(self, source_key: str | None = None) -> dict[str, Any] | None:
        if source_key:
            row = self._connection.execute(
                "SELECT * FROM source_runs WHERE source_key = ? ORDER BY id DESC LIMIT 1",
                (source_key,),
            ).fetchone()
            return _source_run_from_row(row) if row else None
        runs = self.list_source_runs(limit=1)
        return runs[0] if runs else None

    def source_health(self, source_key: str) -> dict[str, Any]:
        success = self._connection.execute(
            "SELECT * FROM source_runs WHERE source_key = ? AND success = 1 "
            "ORDER BY id DESC LIMIT 1",
            (source_key,),
        ).fetchone()
        failure = self._connection.execute(
            "SELECT * FROM source_runs WHERE source_key = ? AND success = 0 "
            "ORDER BY id DESC LIMIT 1",
            (source_key,),
        ).fetchone()
        return {
            "last_success": _source_run_from_row(success) if success else None,
            "last_failure": _source_run_from_row(failure) if failure else None,
        }

    def bind_target(self, unified_msg_origin: str, label: str = "") -> dict[str, Any]:
        now = _serialize_datetime(DateTime.now(timezone.utc))
        self._connection.execute(
            """
            INSERT INTO publish_targets(
                unified_msg_origin, label, enabled, created_at, updated_at
            ) VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(unified_msg_origin) DO UPDATE SET
                label = excluded.label, enabled = 1, updated_at = excluded.updated_at
            """,
            (unified_msg_origin, label or unified_msg_origin, now, now),
        )
        self._connection.commit()
        return self._target_by_umo(unified_msg_origin)

    def unbind_target(self, unified_msg_origin: str) -> bool:
        cursor = self._connection.execute(
            "UPDATE publish_targets SET enabled = 0, updated_at = ? WHERE unified_msg_origin = ?",
            (_serialize_datetime(DateTime.now(timezone.utc)), unified_msg_origin),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def rename_target(self, target_id: int, label: str) -> dict[str, Any]:
        now = _serialize_datetime(DateTime.now(timezone.utc))
        cursor = self._connection.execute(
            "UPDATE publish_targets SET label = ?, updated_at = ? WHERE id = ?",
            (str(label).strip(), now, target_id),
        )
        self._connection.commit()
        if cursor.rowcount == 0:
            return {"success": False, "reason": "未找到发布目标"}
        target = self.get_target(target_id)
        return {"success": True, "target": target}

    def list_targets(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM publish_targets"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        rows = self._connection.execute(query).fetchall()
        return [_target_from_row(row) for row in rows]

    def _target_by_umo(self, unified_msg_origin: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM publish_targets WHERE unified_msg_origin = ?",
            (unified_msg_origin,),
        ).fetchone()
        return _target_from_row(row)

    def get_target(self, target_id: int) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM publish_targets WHERE id = ?", (target_id,)
        ).fetchone()
        return _target_from_row(row) if row else None

    def import_real_questions(
        self, records: Iterable[RealQuestion | dict[str, Any]]
    ) -> int:
        imported = 0
        now = _serialize_datetime(DateTime.now(timezone.utc))
        self._connection.execute("BEGIN")
        try:
            for raw in records:
                question = (
                    raw
                    if isinstance(raw, RealQuestion)
                    else RealQuestion.from_mapping(raw)
                )
                identity = "|".join(
                    (
                        question.source_name,
                        question.exam_name,
                        question.source_locator
                        or question.question_number
                        or question.content_hash,
                    )
                )
                self._connection.execute(
                    """
                INSERT INTO real_questions(
                    identity_key, source_name, exam_name, exam_year, exam_date,
                    paper, question_number, source_url, source_locator, subject,
                    question_type, stem, options_json, answer_json, explanation,
                    answer_source, verification_status, content_hash, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    source_name = excluded.source_name, exam_name = excluded.exam_name,
                    exam_year = excluded.exam_year, exam_date = excluded.exam_date,
                    paper = excluded.paper, question_number = excluded.question_number,
                    source_url = excluded.source_url, source_locator = excluded.source_locator,
                    subject = excluded.subject, question_type = excluded.question_type,
                    stem = excluded.stem, options_json = excluded.options_json,
                    answer_json = excluded.answer_json, explanation = excluded.explanation,
                    answer_source = excluded.answer_source,
                    verification_status = excluded.verification_status,
                    content_hash = excluded.content_hash, metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                    """,
                    (
                        identity,
                        question.source_name,
                        question.exam_name,
                        question.exam_year,
                        question.exam_date,
                        question.paper,
                        question.question_number,
                        question.source_url,
                        question.source_locator,
                        question.subject,
                        question.question_type,
                        question.stem,
                        json.dumps(
                            question.options, ensure_ascii=False, sort_keys=True
                        ),
                        json.dumps(question.answer, ensure_ascii=False, sort_keys=True)
                        if question.answer is not None
                        else None,
                        question.explanation,
                        question.answer_source,
                        question.verification_status,
                        question.content_hash,
                        json.dumps(
                            question.metadata, ensure_ascii=False, sort_keys=True
                        ),
                        now,
                        now,
                    ),
                )
                imported += 1
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return imported

    def list_real_questions(
        self,
        *,
        subject: Any = None,
        question_type: str | None = None,
        source_name: str | None = None,
        exam_year: str | None = None,
        exclude_ids: set[int] | None = None,
        limit: int = 100,
    ) -> list[RealQuestion]:
        rows = self._connection.execute(
            "SELECT * FROM real_questions WHERE verification_status IN "
            "('verified', 'official', 'user_verified') ORDER BY id LIMIT ?",
            (max(1, min(limit, 1000)),),
        ).fetchall()
        requested_subjects = subject_filter(subject)
        excluded = exclude_ids or set()
        result: list[RealQuestion] = []
        for row in rows:
            if int(row["id"]) in excluded:
                continue
            if (
                requested_subjects is not None
                and row["subject"] not in requested_subjects
            ):
                continue
            if question_type and row["question_type"] != question_type:
                continue
            if source_name and row["source_name"] != source_name:
                continue
            if exam_year and row["exam_year"] != str(exam_year):
                continue
            result.append(_real_question_from_row(row))
        return result

    def count_real_questions(
        self, *, subject: Any = None, question_type: str | None = None
    ) -> int:
        return len(
            self.list_real_questions(
                subject=subject, question_type=question_type, limit=1000
            )
        )

    def real_question_inventory(self) -> dict[str, Any]:
        questions = self.list_real_questions(limit=1000)
        by_subject: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for question in questions:
            by_subject[question.subject] = by_subject.get(question.subject, 0) + 1
            by_type[question.question_type] = by_type.get(question.question_type, 0) + 1
        return {"count": len(questions), "by_subject": by_subject, "by_type": by_type}

    def get_daily_plan(
        self, target_id: int | None, content_type: str
    ) -> DailyPlan | None:
        if target_id is None:
            row = self._connection.execute(
                "SELECT * FROM daily_plans WHERE target_id IS NULL AND content_type = ?",
                (content_type,),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT * FROM daily_plans WHERE target_id = ? AND content_type = ?",
                (target_id, content_type),
            ).fetchone()
        return _daily_plan_from_row(row) if row else None

    def upsert_daily_plan(self, plan: DailyPlan, target_id: int | None = None) -> None:
        now = _serialize_datetime(DateTime.now(timezone.utc))
        self._connection.execute(
            """
            INSERT INTO daily_plans(
                target_id, content_type, enabled, time, selection_mode, fixed_subject,
                rotation_subjects_json, rotation_start_date, rotation_start_index,
                question_origin, question_type, question_type_selection_mode,
                fixed_question_type, rotation_question_types,
                question_type_rotation_start_date, question_type_rotation_start_index,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET
                enabled = excluded.enabled, time = excluded.time,
                selection_mode = excluded.selection_mode, fixed_subject = excluded.fixed_subject,
                rotation_subjects_json = excluded.rotation_subjects_json,
                rotation_start_date = excluded.rotation_start_date,
                rotation_start_index = excluded.rotation_start_index,
                question_origin = excluded.question_origin, question_type = excluded.question_type,
                question_type_selection_mode = excluded.question_type_selection_mode,
                fixed_question_type = excluded.fixed_question_type,
                rotation_question_types = excluded.rotation_question_types,
                question_type_rotation_start_date = excluded.question_type_rotation_start_date,
                question_type_rotation_start_index = excluded.question_type_rotation_start_index,
                updated_at = excluded.updated_at
            """,
            (
                target_id,
                plan.content_type,
                int(plan.enabled),
                plan.time,
                plan.selection_mode,
                plan.fixed_subject,
                json.dumps(plan.rotation_subjects, ensure_ascii=False),
                plan.rotation_start_date,
                plan.rotation_start_index,
                plan.question_origin,
                plan.question_type,
                plan.question_type_selection_mode,
                plan.fixed_question_type,
                json.dumps(plan.rotation_question_types, ensure_ascii=False),
                plan.question_type_rotation_start_date,
                plan.question_type_rotation_start_index,
                now,
            ),
        )
        self._connection.commit()

    def list_daily_plans(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM daily_plans ORDER BY target_id, content_type"
        ).fetchall()
        return [_daily_plan_record(row) for row in rows]

    def delete_daily_plan(self, target_id: int, content_type: str) -> bool:
        cursor = self._connection.execute(
            "DELETE FROM daily_plans WHERE target_id = ? AND content_type = ?",
            (target_id, content_type),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def claim_publication(
        self, event_id: int, revision: int, target_id: int, kind: str
    ) -> int | None:
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO publications(
                event_id, revision, target_id, kind, status, attempted_at
            ) VALUES (?, ?, ?, ?, 'claimed', ?)
            """,
            (
                event_id,
                revision,
                target_id,
                kind,
                _serialize_datetime(DateTime.now(timezone.utc)),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid) if cursor.rowcount else None

    def finish_publication(
        self, publication_id: int, *, success: bool, error_summary: str | None = None
    ) -> None:
        self._connection.execute(
            """
            UPDATE publications SET status = ?, finished_at = ?, error_summary = ?
            WHERE id = ?
            """,
            (
                "sent" if success else "failed",
                _serialize_datetime(DateTime.now(timezone.utc)),
                error_summary,
                publication_id,
            ),
        )
        self._connection.commit()

    def list_publications(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM publications ORDER BY id DESC LIMIT ?", (max(1, limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    def claim_reminder(
        self,
        *,
        event_id: int,
        date_kind: str,
        deadline_value: str,
        target_id: int,
        reminder_offset: int,
    ) -> int | None:
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO reminders(
                event_id, date_kind, deadline_value, target_id,
                reminder_offset, status, attempted_at
            ) VALUES (?, ?, ?, ?, ?, 'claimed', ?)
            """,
            (
                event_id,
                date_kind,
                deadline_value,
                target_id,
                reminder_offset,
                _serialize_datetime(DateTime.now(timezone.utc)),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid) if cursor.rowcount else None

    def finish_reminder(
        self, reminder_id: int, *, success: bool, error_summary: str | None = None
    ) -> None:
        self._connection.execute(
            """
            UPDATE reminders SET status = ?, finished_at = ?, error_summary = ?
            WHERE id = ?
            """,
            (
                "sent" if success else "failed",
                _serialize_datetime(DateTime.now(timezone.utc)),
                error_summary,
                reminder_id,
            ),
        )
        self._connection.commit()

    def list_reminders(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM reminders ORDER BY id DESC LIMIT ?", (max(1, limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_case_item(self, item: CaseItem) -> int:
        now = item.last_seen_at or DateTime.now(timezone.utc)
        discovered = item.discovered_at or now
        self._connection.execute(
            """
            INSERT INTO case_items(
                source_key, source_item_key, title, published_at, source_url,
                authority, raw_text, content_hash, discovered_at, last_seen_at,
                subjects_json, manual_subjects_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key, source_item_key) DO UPDATE SET
                title = excluded.title, published_at = excluded.published_at,
                source_url = excluded.source_url, authority = excluded.authority,
                raw_text = excluded.raw_text, content_hash = excluded.content_hash,
                last_seen_at = excluded.last_seen_at, subjects_json = excluded.subjects_json,
                metadata_json = excluded.metadata_json
            """,
            (
                item.source_key,
                item.source_item_key,
                item.title,
                _serialize_datetime(item.published_at),
                item.source_url,
                item.authority,
                item.raw_text,
                item.content_hash,
                _serialize_datetime(discovered),
                _serialize_datetime(now),
                json.dumps(item.subjects, ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                json.dumps(item.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT id FROM case_items WHERE source_key = ? AND source_item_key = ?",
            (item.source_key, item.source_item_key),
        ).fetchone()
        return int(row["id"])

    def set_case_subjects(self, case_id: int, subjects: Iterable[str]) -> bool:
        normalized = tuple(
            dict.fromkeys(str(item).strip() for item in subjects if str(item).strip())
        )
        cursor = self._connection.execute(
            "UPDATE case_items SET manual_subjects_json = ? WHERE id = ?",
            (json.dumps(normalized, ensure_ascii=False), case_id),
        )
        self._connection.commit()
        return cursor.rowcount > 0

    def list_case_items(self, limit: int = 50) -> list[CaseItem]:
        rows = self._connection.execute(
            """
            SELECT * FROM case_items
            ORDER BY COALESCE(published_at, discovered_at) DESC, id DESC LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [_case_from_row(row) for row in rows]

    def get_case_item(self, item_id: int) -> CaseItem | None:
        row = self._connection.execute(
            "SELECT * FROM case_items WHERE id = ?", (item_id,)
        ).fetchone()
        return _case_from_row(row) if row else None

    def claim_daily_content(
        self,
        *,
        content_date: str,
        target_id: int,
        content_type: str,
        body: dict[str, Any],
        source_item_id: int | None = None,
        source_kind: str | None = None,
        source_item_key: str | None = None,
        resolved_subject: str | None = None,
        resolved_question_type: str | None = None,
        resolved_origin: str | None = None,
    ) -> int | None:
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO daily_contents(
                content_date, target_id, content_type, source_item_id,
                body_json, status, attempted_at, source_kind, source_item_key,
                resolved_subject, resolved_question_type, resolved_origin
            ) VALUES (?, ?, ?, ?, ?, 'claimed', ?, ?, ?, ?, ?, ?)
            """,
            (
                content_date,
                target_id,
                content_type,
                source_item_id,
                json.dumps(body, ensure_ascii=False, sort_keys=True),
                _serialize_datetime(DateTime.now(timezone.utc)),
                source_kind,
                source_item_key,
                resolved_subject,
                resolved_question_type,
                resolved_origin,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid) if cursor.rowcount else None

    def has_daily_content(
        self, *, content_date: str, target_id: int, content_type: str
    ) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM daily_contents WHERE content_date = ? AND target_id = ? "
            "AND content_type = ?",
            (content_date, target_id, content_type),
        ).fetchone()
        return row is not None

    def record_daily_skip(
        self,
        *,
        content_date: str,
        target_id: int,
        content_type: str,
        reason: str,
        subject: str | None = None,
        resolved_question_type: str | None = None,
        resolved_origin: str | None = None,
    ) -> int | None:
        body = {"skipped": True, "reason": reason, "subject": subject}
        return self.claim_daily_content(
            content_date=content_date,
            target_id=target_id,
            content_type=content_type,
            body=body,
            resolved_subject=subject,
            resolved_question_type=resolved_question_type,
            resolved_origin=resolved_origin,
        )

    def finish_daily_content(
        self, content_id: int, *, success: bool, error_summary: str | None = None
    ) -> None:
        self._connection.execute(
            """
            UPDATE daily_contents SET status = ?, finished_at = ?, error_summary = ?
            WHERE id = ?
            """,
            (
                "sent" if success else "failed",
                _serialize_datetime(DateTime.now(timezone.utc)),
                error_summary,
                content_id,
            ),
        )
        self._connection.commit()

    def mark_daily_skipped(self, content_id: int, reason: str) -> None:
        self._connection.execute(
            "UPDATE daily_contents SET status = ?, finished_at = ?, error_summary = ? WHERE id = ?",
            (
                "skipped",
                _serialize_datetime(DateTime.now(timezone.utc)),
                reason,
                content_id,
            ),
        )
        self._connection.commit()

    def list_daily_contents(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM daily_contents ORDER BY id DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [
            {
                **dict(row),
                "body": json.loads(row["body_json"]),
            }
            for row in rows
        ]

    def list_sent_content_keys(
        self, target_id: int, content_type: str
    ) -> set[tuple[str, str]]:
        """Return successful source identities already sent to one target."""
        rows = self._connection.execute(
            """
            SELECT source_kind, source_item_key
            FROM daily_contents
            WHERE target_id = ? AND content_type = ? AND status = 'sent'
              AND source_kind IS NOT NULL AND source_item_key IS NOT NULL
            """,
            (target_id, content_type),
        ).fetchall()
        return {(str(row["source_kind"]), str(row["source_item_key"])) for row in rows}

    def published_case_ids(self) -> set[int]:
        rows = self._connection.execute(
            "SELECT DISTINCT source_item_id FROM daily_contents "
            "WHERE content_type = 'daily_case' AND status = 'sent' "
            "AND source_item_id IS NOT NULL"
        ).fetchall()
        return {int(row["source_item_id"]) for row in rows}

    def published_question_ids(self) -> set[int]:
        rows = self._connection.execute(
            "SELECT DISTINCT source_item_id FROM daily_contents "
            "WHERE content_type = 'daily_question' AND status = 'sent' "
            "AND source_item_id IS NOT NULL"
        ).fetchall()
        return {int(row["source_item_id"]) for row in rows}

    def upsert_law_update(self, update: LawUpdate) -> int:
        self._connection.execute(
            """
            INSERT INTO law_updates(
                source_key, source_item_key, title, category, promulgation_date,
                effective_date, source_url, status, content_hash, raw_text, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key, source_item_key) DO UPDATE SET
                title = excluded.title, category = excluded.category,
                promulgation_date = excluded.promulgation_date,
                effective_date = excluded.effective_date, source_url = excluded.source_url,
                status = excluded.status, content_hash = excluded.content_hash,
                raw_text = excluded.raw_text, metadata_json = excluded.metadata_json
            """,
            (
                update.source_key,
                update.source_item_key,
                update.title,
                update.category,
                _serialize_datetime(update.promulgation_date),
                _serialize_datetime(update.effective_date),
                update.source_url,
                update.status,
                update.content_hash,
                update.raw_text,
                json.dumps(update.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT id FROM law_updates WHERE source_key = ? AND source_item_key = ?",
            (update.source_key, update.source_item_key),
        ).fetchone()
        return int(row["id"])

    def list_law_updates(self, limit: int = 50) -> list[LawUpdate]:
        rows = self._connection.execute(
            "SELECT * FROM law_updates ORDER BY id DESC LIMIT ?", (max(1, limit),)
        ).fetchall()
        return [_law_update_from_row(row) for row in rows]

    def _event_from_row(self, row: sqlite3.Row) -> LegalEvent:
        date_rows = self._connection.execute(
            "SELECT * FROM event_dates WHERE event_id = ? ORDER BY id", (row["id"],)
        ).fetchall()
        return LegalEvent(
            id=int(row["id"]),
            source_key=row["source_key"],
            source_item_key=row["source_item_key"],
            title=row["title"],
            source_url=row["source_url"],
            organizer=row["organizer"],
            event_type=row["event_type"],
            eligibility=row["eligibility"],
            status=row["status"],
            raw_content_hash=row["raw_content_hash"],
            discovered_at=DateTime.fromisoformat(row["discovered_at"]),
            updated_at=DateTime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["metadata_json"]),
            dates=tuple(
                EventDate(
                    id=int(date_row["id"]),
                    kind=date_row["kind"],
                    datetime=(
                        DateTime.fromisoformat(date_row["datetime"])
                        if date_row["datetime"]
                        else None
                    ),
                    timezone=date_row["timezone"],
                    label=date_row["label"],
                    evidence_text=date_row["evidence_text"],
                    confirmed=bool(date_row["confirmed"]),
                )
                for date_row in date_rows
            ),
            registration_method=row["registration_method"],
            summary=row["summary"],
            source_published_at=(
                DateTime.fromisoformat(row["source_published_at"])
                if row["source_published_at"]
                else None
            ),
            last_seen_at=(
                DateTime.fromisoformat(row["last_seen_at"])
                if row["last_seen_at"]
                else None
            ),
            revision=int(row["revision"]),
        )

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def _changed_event_fields(
    existing: sqlite3.Row,
    incoming: LegalEvent,
    old_dates: list[dict[str, Any]],
    incoming_dates: list[dict[str, Any]],
) -> list[str]:
    fields = [
        "title",
        "source_url",
        "organizer",
        "event_type",
        "eligibility",
        "status",
        "raw_content_hash",
        "registration_method",
        "summary",
        "source_published_at",
        "metadata",
    ]
    changed: list[str] = []
    for field in fields:
        if field == "metadata":
            old_value = json.loads(existing["metadata_json"])
            new_value = incoming.metadata
        elif field == "source_published_at":
            old_value = existing["source_published_at"]
            new_value = _serialize_datetime(incoming.source_published_at)
        else:
            old_value = existing[field]
            new_value = getattr(incoming, field)
        if old_value != new_value:
            changed.append(field)
    if old_dates != incoming_dates:
        changed.append("event_dates")
    if existing["raw_content_hash"] != incoming.raw_content_hash:
        changed.append("source_content")
    return list(dict.fromkeys(changed))


def _dates_snapshot(dates: Iterable[EventDate]) -> list[dict[str, Any]]:
    return [
        {
            "kind": date.kind,
            "datetime": _serialize_datetime(date.datetime),
            "timezone": date.timezone,
            "label": date.label,
            "evidence_text": date.evidence_text,
            "confirmed": bool(date.confirmed),
        }
        for date in dates
    ]


def _event_snapshot(event: LegalEvent, revision: int) -> dict[str, Any]:
    return {
        "revision": revision,
        "source_key": event.source_key,
        "source_item_key": event.source_item_key,
        "title": event.title,
        "source_url": event.source_url,
        "organizer": event.organizer,
        "event_type": event.event_type,
        "eligibility": event.eligibility,
        "status": event.status,
        "raw_content_hash": event.raw_content_hash,
        "registration_method": event.registration_method,
        "summary": event.summary,
        "source_published_at": _serialize_datetime(event.source_published_at),
        "metadata": event.metadata,
        "dates": _dates_snapshot(event.dates),
    }


def _source_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "source_key": row["source_key"],
        "source_type": row["source_type"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "success": bool(row["success"]),
        "discovered_count": int(row["discovered_count"]),
        "error_summary": row["error_summary"],
    }


def _target_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "unified_msg_origin": row["unified_msg_origin"],
        "label": row["label"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _real_question_from_row(row: sqlite3.Row) -> RealQuestion:
    return RealQuestion(
        id=int(row["id"]),
        source_name=row["source_name"],
        exam_name=row["exam_name"],
        exam_year=row["exam_year"],
        exam_date=row["exam_date"],
        paper=row["paper"],
        question_number=row["question_number"],
        source_url=row["source_url"],
        source_locator=row["source_locator"],
        subject=row["subject"],
        question_type=row["question_type"],
        stem=row["stem"],
        options=json.loads(row["options_json"]),
        answer=json.loads(row["answer_json"]) if row["answer_json"] else None,
        explanation=row["explanation"],
        answer_source=row["answer_source"],
        verification_status=row["verification_status"],
        content_hash=row["content_hash"],
        metadata=json.loads(row["metadata_json"]),
    )


def _daily_plan_from_row(row: sqlite3.Row) -> DailyPlan:
    legacy_question_type = row["question_type"]
    question_type_mode = row["question_type_selection_mode"]
    fixed_question_type = row["fixed_question_type"]
    if question_type_mode == "fixed" and not fixed_question_type:
        fixed_question_type = legacy_question_type
    if question_type_mode == "fixed" and fixed_question_type is None:
        question_type_mode = "random"
    return DailyPlan(
        content_type=row["content_type"],
        enabled=bool(row["enabled"]),
        time=row["time"],
        selection_mode=row["selection_mode"],
        fixed_subject=row["fixed_subject"],
        rotation_subjects=tuple(json.loads(row["rotation_subjects_json"])),
        rotation_start_date=row["rotation_start_date"],
        rotation_start_index=int(row["rotation_start_index"]),
        question_origin=row["question_origin"],
        question_type=legacy_question_type,
        question_type_selection_mode=question_type_mode,
        fixed_question_type=fixed_question_type,
        rotation_question_types=tuple(json.loads(row["rotation_question_types"])),
        question_type_rotation_start_date=row["question_type_rotation_start_date"],
        question_type_rotation_start_index=int(
            row["question_type_rotation_start_index"]
        ),
    )


def _daily_plan_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "target_id": int(row["target_id"]) if row["target_id"] is not None else None,
        **_daily_plan_from_row(row).to_mapping(),
        "updated_at": row["updated_at"],
    }


def _case_from_row(row: sqlite3.Row) -> CaseItem:
    source_subjects = tuple(json.loads(row["subjects_json"]))
    manual_subjects = tuple(json.loads(row["manual_subjects_json"]))
    return CaseItem(
        id=int(row["id"]),
        source_key=row["source_key"],
        source_item_key=row["source_item_key"],
        title=row["title"],
        source_url=row["source_url"],
        authority=row["authority"],
        raw_text=row["raw_text"],
        content_hash=row["content_hash"],
        published_at=(
            DateTime.fromisoformat(row["published_at"]) if row["published_at"] else None
        ),
        discovered_at=row["discovered_at"],
        last_seen_at=row["last_seen_at"],
        subjects=tuple(dict.fromkeys((*source_subjects, *manual_subjects))),
        metadata=json.loads(row["metadata_json"]),
    )


def _law_update_from_row(row: sqlite3.Row) -> LawUpdate:
    return LawUpdate(
        id=int(row["id"]),
        source_key=row["source_key"],
        source_item_key=row["source_item_key"],
        title=row["title"],
        category=row["category"],
        source_url=row["source_url"],
        status=row["status"],
        content_hash=row["content_hash"],
        promulgation_date=(
            DateTime.fromisoformat(row["promulgation_date"])
            if row["promulgation_date"]
            else None
        ),
        effective_date=(
            DateTime.fromisoformat(row["effective_date"])
            if row["effective_date"]
            else None
        ),
        raw_text=row["raw_text"],
        metadata=json.loads(row["metadata_json"]),
    )


def _serialize_datetime(value: DateTime | str | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, DateTime) else str(value)
