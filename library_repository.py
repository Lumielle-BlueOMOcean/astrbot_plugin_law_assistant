from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

if __package__ and "." in __package__:
    from .library_models import (
        CaseDetail,
        LearningItem,
        LibraryArchiveResult,
        LibraryItemBundle,
        LibrarySource,
        QuestionDetail,
    )
else:
    from library_models import (
        CaseDetail,
        LearningItem,
        LibraryArchiveResult,
        LibraryItemBundle,
        LibrarySource,
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
    )


class LibraryRepository:
    """CRUD for library rows using SQLiteStorage's connection and lifecycle."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def archive(
        self,
        source: LibrarySource,
        item: LearningItem,
        *,
        case: CaseDetail | None = None,
        question: QuestionDetail | None = None,
    ) -> LibraryArchiveResult:
        if source.id is not None or item.id is not None:
            raise ValueError("archive expects unsaved source and item models")
        with self.connection:
            source_row = self.connection.execute(
                """
                SELECT * FROM library_sources
                WHERE created_by = ? AND content_hash = ? AND source_url = ?
                """,
                (source.created_by, source.content_hash, source.source_url),
            ).fetchone()
            if source_row is None:
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
                source_id = int(cursor.lastrowid)
            else:
                source_id = int(source_row["id"])

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
                    (int(existing["id"]), source_id, "", "primary_evidence"),
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
                (item_id, source_id, "", "primary_evidence"),
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
            case=case,
            question=question,
        )

    def search(
        self,
        *,
        query: str = "",
        item_type: str = "",
        identity: str = "",
        subject: str = "",
        limit: int = 10,
    ) -> list[LearningItem]:
        clauses = ["1 = 1"]
        params: list[Any] = []
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
