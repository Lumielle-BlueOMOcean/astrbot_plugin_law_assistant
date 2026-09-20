from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

if __package__ and "." in __package__:
    from .library_models import (
        CaseDetail,
        LearningItem,
        LearningReviewItem,
        LibraryArchiveResult,
        LibraryItemBundle,
        LibrarySource,
        LibrarySourceLink,
        QuestionDetail,
    )
else:
    from library_models import (
        CaseDetail,
        LearningItem,
        LearningReviewItem,
        LibraryArchiveResult,
        LibraryItemBundle,
        LibrarySource,
        LibrarySourceLink,
        QuestionDetail,
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _source_from_row(row: sqlite3.Row) -> LibrarySource:
    return LibrarySource(
        id=int(row["id"]),
        source_kind=str(row["source_kind"]),
        title=str(row["title"]),
        raw_text=str(row["raw_text"]),
        source_url=str(row["source_url"]),
        content_hash=str(row["content_hash"]),
        created_at=_parse_datetime(str(row["created_at"])),
        created_by=str(row["created_by"]),
        session_origin=str(row["session_origin"]),
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        storage_path=row["storage_path"],
        metadata=json.loads(row["metadata_json"]),
    )


def _item_from_row(row: sqlite3.Row) -> LearningItem:
    return LearningItem(
        id=int(row["id"]),
        item_type=str(row["item_type"]),
        identity=str(row["identity"]),
        item_hash=str(row["item_hash"]),
        title=str(row["title"]),
        subjects=tuple(json.loads(row["subjects_json"])),
        verification_status=str(row["verification_status"]),
        source_summary=str(row["source_summary"]),
        created_at=_parse_datetime(str(row["created_at"])),
        updated_at=_parse_datetime(str(row["updated_at"])),
        created_by=str(row["created_by"]),
        metadata=json.loads(row["metadata_json"]),
        active=bool(row["active"]),
    )


def _review_from_row(row: sqlite3.Row) -> LearningReviewItem:
    return LearningReviewItem(
        id=int(row["id"]),
        source_id=int(row["source_id"]),
        candidate_key=str(row["candidate_key"]),
        material_type=str(row["material_type"]),
        locator=str(row["locator"]),
        raw_fragment=str(row["raw_fragment"]),
        proposed_structure=json.loads(row["proposed_structure_json"]),
        review_reason=str(row["review_reason"]),
        status=str(row["status"]),
        created_at=_parse_datetime(str(row["created_at"])),
        updated_at=_parse_datetime(str(row["updated_at"])),
    )


class LibraryRepository:
    """CRUD for library rows using SQLiteStorage's connection and lifecycle."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ensure_source(self, source: LibrarySource) -> int:
        """Insert or find one source row without creating a learning item."""
        if source.id is not None:
            raise ValueError("ensure_source expects an unsaved source model")
        source_row = self.connection.execute(
            """
            SELECT * FROM library_sources
            WHERE created_by = ? AND content_hash = ? AND source_url = ?
            """,
            (source.created_by, source.content_hash, source.source_url),
        ).fetchone()
        if source_row is not None:
            existing_metadata = json.loads(source_row["metadata_json"])
            merged_metadata = {**existing_metadata, **source.metadata}
            if (
                merged_metadata != existing_metadata
                or source.title != source_row["title"]
            ):
                self.connection.execute(
                    """
                    UPDATE library_sources
                    SET title = ?, metadata_json = ?
                    WHERE id = ?
                    """,
                    (source.title, _json(merged_metadata), int(source_row["id"])),
                )
            return int(source_row["id"])
        cursor = self.connection.execute(
            """
            INSERT INTO library_sources(
                source_kind, title, raw_text, source_url, content_hash,
                created_at, created_by, session_origin, original_filename,
                mime_type, storage_path, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.source_kind,
                source.title,
                source.raw_text,
                source.source_url,
                source.content_hash,
                source.created_at.isoformat(),
                source.created_by,
                source.session_origin,
                source.original_filename,
                source.mime_type,
                source.storage_path,
                _json(source.metadata),
            ),
        )
        return int(cursor.lastrowid)

    def archive(
        self,
        source: LibrarySource,
        item: LearningItem,
        *,
        case: CaseDetail | None = None,
        question: QuestionDetail | None = None,
        locator: str = "",
        relationship: str = "primary_evidence",
    ) -> LibraryArchiveResult:
        if source.id is not None or item.id is not None:
            raise ValueError("archive expects unsaved source and item models")
        with self.connection:
            source_id = self.ensure_source(source)

            existing = self.connection.execute(
                """
                SELECT * FROM learning_items
                WHERE created_by = ? AND item_hash = ?
                """,
                (item.created_by, item.item_hash),
            ).fetchone()
            if existing is not None:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO learning_item_sources(
                        item_id, source_id, locator, relationship
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        int(existing["id"]),
                        source_id,
                        locator,
                        relationship,
                    ),
                )
                if not bool(existing["active"]):
                    self.connection.execute(
                        "UPDATE learning_items SET active = 1 WHERE id = ?",
                        (int(existing["id"]),),
                    )
                return LibraryArchiveResult(
                    source_id=source_id,
                    item_id=int(existing["id"]),
                    duplicate=True,
                )

            cursor = self.connection.execute(
                """
                INSERT INTO learning_items(
                    item_type, identity, item_hash, title, subjects_json,
                    verification_status, source_summary, created_at, updated_at,
                    created_by, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.item_type,
                    item.identity,
                    item.item_hash,
                    item.title,
                    _json(list(item.subjects)),
                    item.verification_status,
                    item.source_summary,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                    item.created_by,
                    _json(item.metadata),
                ),
            )
            item_id = int(cursor.lastrowid)
            self.connection.execute(
                """
                INSERT INTO learning_item_sources(item_id, source_id, locator, relationship)
                VALUES (?, ?, ?, ?)
                """,
                (item_id, source_id, locator, relationship),
            )
            if case is not None:
                self.connection.execute(
                    """
                    INSERT INTO learning_cases(
                        item_id, case_number, authority, case_summary, issues_json,
                        reasoning, result_text, practice_notes_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        case.case_number,
                        case.authority,
                        case.case_summary,
                        _json(list(case.issues)),
                        case.reasoning,
                        case.result_text,
                        _json(list(case.practice_notes)),
                    ),
                )
            if question is not None:
                self.connection.execute(
                    """
                    INSERT INTO learning_questions(
                        item_id, question_identity, question_type, stem, options_json,
                        answer_json, explanation, exam_name, exam_year, paper,
                        question_number, answer_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        question.question_identity,
                        question.question_type,
                        question.stem,
                        _json(list(question.options)),
                        _json(question.answer),
                        question.explanation,
                        question.exam_name,
                        question.exam_year,
                        question.paper,
                        question.question_number,
                        question.answer_source,
                    ),
                )
        return LibraryArchiveResult(
            source_id=source_id, item_id=item_id, duplicate=False
        )

    def get(self, item_id: int) -> LibraryItemBundle | None:
        row = self.connection.execute(
            "SELECT * FROM learning_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        item = _item_from_row(row)
        source_rows = self.connection.execute(
            """
            SELECT s.* FROM library_sources AS s
            JOIN learning_item_sources AS link ON link.source_id = s.id
            WHERE link.item_id = ? ORDER BY s.id
            """,
            (item_id,),
        ).fetchall()
        link_rows = self.connection.execute(
            """
            SELECT s.*, link.locator, link.relationship
            FROM library_sources AS s
            JOIN learning_item_sources AS link ON link.source_id = s.id
            WHERE link.item_id = ?
            ORDER BY s.id
            """,
            (item_id,),
        ).fetchall()
        case_row = self.connection.execute(
            "SELECT * FROM learning_cases WHERE item_id = ?", (item_id,)
        ).fetchone()
        question_row = self.connection.execute(
            "SELECT * FROM learning_questions WHERE item_id = ?", (item_id,)
        ).fetchone()
        case = None
        if case_row is not None:
            case = CaseDetail(
                case_number=str(case_row["case_number"]),
                authority=str(case_row["authority"]),
                case_summary=str(case_row["case_summary"]),
                issues=tuple(json.loads(case_row["issues_json"])),
                reasoning=str(case_row["reasoning"]),
                result_text=str(case_row["result_text"]),
                practice_notes=tuple(json.loads(case_row["practice_notes_json"])),
            )
        question = None
        if question_row is not None:
            question = QuestionDetail(
                question_identity=str(question_row["question_identity"]),
                question_type=str(question_row["question_type"]),
                stem=str(question_row["stem"]),
                options=tuple(json.loads(question_row["options_json"])),
                answer=json.loads(question_row["answer_json"]),
                explanation=str(question_row["explanation"]),
                exam_name=str(question_row["exam_name"]),
                exam_year=str(question_row["exam_year"]),
                paper=str(question_row["paper"]),
                question_number=str(question_row["question_number"]),
                answer_source=str(question_row["answer_source"]),
            )
        return LibraryItemBundle(
            item=item,
            sources=tuple(_source_from_row(source) for source in source_rows),
            source_links=tuple(
                LibrarySourceLink(
                    source=_source_from_row(source),
                    locator=str(source["locator"]),
                    relationship=str(source["relationship"]),
                )
                for source in link_rows
            ),
            case=case,
            question=question,
        )

    def list_by_source(self, source_id: int) -> list[LearningItem]:
        rows = self.connection.execute(
            """
            SELECT i.* FROM learning_items AS i
            JOIN learning_item_sources AS link ON link.item_id = i.id
            WHERE link.source_id = ?
            ORDER BY i.id
            """,
            (source_id,),
        ).fetchall()
        return [_item_from_row(row) for row in rows]

    def source_ids_by_metadata(
        self, *, created_by: str, key: str, value: str
    ) -> list[int]:
        """Find source rows sharing a stable upstream identity."""
        rows = self.connection.execute(
            "SELECT id, metadata_json FROM library_sources WHERE created_by = ?",
            (created_by,),
        ).fetchall()
        result = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            if str(metadata.get(key, "")) == value:
                result.append(int(row["id"]))
        return result

    def deactivate_items_for_sources(
        self, source_ids: list[int], *, identity: str = "official_case"
    ) -> int:
        if not source_ids:
            return 0
        placeholders = ", ".join("?" for _ in source_ids)
        with self.connection:
            cursor = self.connection.execute(
                f"""
                UPDATE learning_items
                SET active = 0
                WHERE identity = ? AND active = 1 AND id IN (
                    SELECT item_id FROM learning_item_sources
                    WHERE source_id IN ({placeholders})
                )
                """,
                [identity, *source_ids],
            )
        return cursor.rowcount

    def manual_subjects_for_sources(self, source_ids: list[int]) -> tuple[str, ...]:
        if not source_ids:
            return ()
        placeholders = ", ".join("?" for _ in source_ids)
        rows = self.connection.execute(
            f"""
            SELECT i.subjects_json, i.metadata_json
            FROM learning_items AS i
            JOIN learning_item_sources AS link ON link.item_id = i.id
            WHERE i.identity = 'official_case'
              AND link.source_id IN ({placeholders})
            """,
            source_ids,
        ).fetchall()
        subjects: list[str] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            if metadata.get("manual_subjects") is not True:
                continue
            for value in json.loads(row["subjects_json"]):
                if value not in subjects:
                    subjects.append(value)
        return tuple(subjects)

    def supersede_review_items(self, source_ids: list[int]) -> int:
        if not source_ids:
            return 0
        placeholders = ", ".join("?" for _ in source_ids)
        with self.connection:
            cursor = self.connection.execute(
                f"""
                UPDATE learning_review_items
                SET status = 'superseded', updated_at = ?
                WHERE status = 'pending' AND source_id IN ({placeholders})
                """,
                [datetime.now().astimezone().isoformat(), *source_ids],
            )
        return cursor.rowcount

    def record_review_item(
        self,
        *,
        source_id: int,
        candidate_key: str,
        material_type: str,
        locator: str,
        raw_fragment: str,
        proposed_structure: dict[str, Any],
        review_reason: str,
        now: datetime,
    ) -> int:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO learning_review_items(
                    source_id, candidate_key, material_type, locator, raw_fragment,
                    proposed_structure_json, review_reason, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(source_id, candidate_key) DO UPDATE SET
                    material_type = excluded.material_type,
                    locator = excluded.locator,
                    raw_fragment = excluded.raw_fragment,
                    proposed_structure_json = excluded.proposed_structure_json,
                    review_reason = excluded.review_reason,
                    status = CASE
                        WHEN learning_review_items.status = 'resolved'
                        THEN learning_review_items.status
                        ELSE 'pending'
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    source_id,
                    candidate_key,
                    material_type,
                    locator,
                    raw_fragment,
                    _json(proposed_structure),
                    review_reason,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = self.connection.execute(
                """
                SELECT id FROM learning_review_items
                WHERE source_id = ? AND candidate_key = ?
                """,
                (source_id, candidate_key),
            ).fetchone()
        return int(row["id"])

    def list_review_items(
        self,
        *,
        source_id: int | None = None,
        status: str = "pending",
        limit: int = 100,
    ) -> list[LearningReviewItem]:
        clauses = ["status = ?"]
        params: list[Any] = [status]
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        params.append(max(1, min(int(limit), 200)))
        rows = self.connection.execute(
            f"""
            SELECT * FROM learning_review_items
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_review_from_row(row) for row in rows]

    def get_review_item(self, review_id: int) -> LearningReviewItem | None:
        row = self.connection.execute(
            "SELECT * FROM learning_review_items WHERE id = ?", (review_id,)
        ).fetchone()
        return _review_from_row(row) if row is not None else None

    def update_review_status(self, review_id: int, status: str) -> bool:
        if status not in {"pending", "resolved", "superseded"}:
            raise ValueError("不支持的待复核状态")
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE learning_review_items
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, datetime.now().astimezone().isoformat(), review_id),
            )
        return cursor.rowcount > 0

    def search(
        self,
        *,
        query: str = "",
        item_type: str = "",
        identity: str = "",
        subject: str = "",
        limit: int = 10,
        include_inactive: bool = False,
    ) -> list[LearningItem]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if not include_inactive:
            clauses.append("i.active = 1")
        if item_type:
            clauses.append("i.item_type = ?")
            params.append(item_type)
        if identity:
            clauses.append("i.identity = ?")
            params.append(identity)
        if subject:
            clauses.append("i.subjects_json LIKE ?")
            params.append(f"%{subject}%")
        if query:
            term = f"%{query}%"
            clauses.append(
                "(i.title LIKE ? OR i.source_summary LIKE ? OR s.raw_text LIKE ? "
                "OR c.case_summary LIKE ? OR c.practice_notes_json LIKE ? "
                "OR q.stem LIKE ? OR q.explanation LIKE ? OR i.metadata_json LIKE ?)"
            )
            params.extend([term] * 8)
        safe_limit = max(1, min(int(limit), 50))
        params.append(safe_limit)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT i.* FROM learning_items AS i
            LEFT JOIN learning_item_sources AS link ON link.item_id = i.id
            LEFT JOIN library_sources AS s ON s.id = link.source_id
            LEFT JOIN learning_cases AS c ON c.item_id = i.id
            LEFT JOIN learning_questions AS q ON q.item_id = i.id
            WHERE {" AND ".join(clauses)}
            ORDER BY i.updated_at DESC, i.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_item_from_row(row) for row in rows]

    def update(self, item_id: int, changes: dict[str, Any]) -> LibraryItemBundle | None:
        allowed = {"title", "subjects", "note", "practice_notes", "explanation"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported learning item fields: {sorted(unknown)}")
        with self.connection:
            row = self.connection.execute(
                "SELECT * FROM learning_items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                return None
            item_updates: list[str] = []
            params: list[Any] = []
            if "title" in changes:
                item_updates.append("title = ?")
                params.append(str(changes["title"]))
            if "subjects" in changes:
                item_updates.append("subjects_json = ?")
                params.append(_json(list(changes["subjects"])))
                if row["identity"] == "official_case":
                    metadata = json.loads(row["metadata_json"])
                    metadata["manual_subjects"] = True
                    item_updates.append("metadata_json = ?")
                    params.append(_json(metadata))
            if "note" in changes:
                metadata = json.loads(row["metadata_json"])
                metadata["note"] = str(changes["note"])
                item_updates.append("metadata_json = ?")
                params.append(_json(metadata))
            if item_updates or "practice_notes" in changes or "explanation" in changes:
                item_updates.append("updated_at = ?")
                params.append(datetime.now().astimezone().isoformat())
                params.append(item_id)
                self.connection.execute(
                    f"UPDATE learning_items SET {', '.join(item_updates)} WHERE id = ?",
                    params,
                )
            if "practice_notes" in changes:
                self.connection.execute(
                    "UPDATE learning_cases SET practice_notes_json = ? WHERE item_id = ?",
                    (_json(list(changes["practice_notes"])), item_id),
                )
            if "explanation" in changes:
                self.connection.execute(
                    "UPDATE learning_questions SET explanation = ? WHERE item_id = ?",
                    (str(changes["explanation"]), item_id),
                )
        return self.get(item_id)
