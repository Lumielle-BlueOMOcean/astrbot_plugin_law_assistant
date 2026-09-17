from __future__ import annotations

import json
import sqlite3
from datetime import datetime as DateTime
from pathlib import Path
from typing import Any

if __package__ and "." in __package__:
    from .models import EventDate, LegalEvent
else:
    from models import EventDate, LegalEvent

SCHEMA_VERSION = 1


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

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
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
            );
            CREATE TABLE IF NOT EXISTS event_dates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                datetime TEXT,
                timezone TEXT NOT NULL,
                label TEXT NOT NULL,
                evidence_text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                success INTEGER NOT NULL,
                discovered_count INTEGER NOT NULL,
                error_summary TEXT
            );
            INSERT INTO schema_meta(key, value)
            VALUES ('version', '1')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """,
        )
        self._connection.commit()

    def upsert_event(self, event: LegalEvent) -> int:
        existing = self._connection.execute(
            "SELECT id FROM events WHERE source_key = ? AND source_item_key = ?",
            (event.source_key, event.source_item_key),
        ).fetchone()
        values = (
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
        )
        if existing:
            event_id = int(existing["id"])
            self._connection.execute(
                """
                UPDATE events SET title = ?, source_url = ?, organizer = ?,
                    event_type = ?, eligibility = ?, status = ?,
                    raw_content_hash = ?, discovered_at = ?, updated_at = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                values[2:] + (event_id,),
            )
        else:
            cursor = self._connection.execute(
                """
                INSERT INTO events(
                    source_key, source_item_key, title, source_url, organizer,
                    event_type, eligibility, status, raw_content_hash,
                    discovered_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            event_id = int(cursor.lastrowid)

        self._connection.execute(
            "DELETE FROM event_dates WHERE event_id = ?", (event_id,)
        )
        self._connection.executemany(
            """
            INSERT INTO event_dates(event_id, kind, datetime, timezone, label, evidence_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    event_id,
                    date.kind,
                    _serialize_datetime(date.datetime),
                    date.timezone,
                    date.label,
                    date.evidence_text,
                )
                for date in event.dates
            ],
        )
        self._connection.commit()
        return event_id

    def list_events(self, limit: int = 20) -> list[LegalEvent]:
        rows = self._connection.execute(
            "SELECT * FROM events ORDER BY updated_at DESC, id DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def get_event(self, event_id: int) -> LegalEvent | None:
        row = self._connection.execute(
            "SELECT * FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        return self._event_from_row(row) if row else None

    def count_events(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM events"
        ).fetchone()
        return int(row["count"])

    def record_source_run(
        self,
        *,
        source_key: str,
        started_at: DateTime,
        finished_at: DateTime,
        success: bool,
        discovered_count: int,
        error_summary: str | None = None,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO source_runs(
                source_key, started_at, finished_at, success,
                discovered_count, error_summary
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source_key,
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
        return [
            {
                "id": int(row["id"]),
                "source_key": row["source_key"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "success": bool(row["success"]),
                "discovered_count": int(row["discovered_count"]),
                "error_summary": row["error_summary"],
            }
            for row in rows
        ]

    def latest_source_run(self) -> dict[str, Any] | None:
        runs = self.list_source_runs(limit=1)
        return runs[0] if runs else None

    def _event_from_row(self, row: sqlite3.Row) -> LegalEvent:
        date_rows = self._connection.execute(
            "SELECT * FROM event_dates WHERE event_id = ? ORDER BY id",
            (row["id"],),
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
                    kind=date_row["kind"],
                    datetime=(
                        DateTime.fromisoformat(date_row["datetime"])
                        if date_row["datetime"]
                        else None
                    ),
                    timezone=date_row["timezone"],
                    label=date_row["label"],
                    evidence_text=date_row["evidence_text"],
                )
                for date_row in date_rows
            ),
        )

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def _serialize_datetime(value: DateTime | str | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, DateTime) else str(value)
