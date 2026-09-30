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
try:
    from .content import RealQuestion, real_question_identity_key, subject_filter
except ImportError:
    from content import RealQuestion, real_question_identity_key, subject_filter


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


_LINKED_REAL_QUESTION_FIELDS = {
    "source_name",
    "source_url",
    "source_locator",
    "exam_name",
    "exam_year",
    "exam_date",
    "paper",
    "question_number",
    "subjects",
    "question_type",
    "stem",
    "options",
    "answer",
    "explanation",
    "answer_source",
}


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
        deleted_at=row["deleted_at"],
        deleted_by=row["deleted_by"],
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
        structured_import_id=(
            int(row["structured_import_id"])
            if row["structured_import_id"] is not None
            else None
        ),
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
            return self._archive_inner(
                source,
                item,
                case=case,
                question=question,
                locator=locator,
                relationship=relationship,
            )

    def _archive_inner(
        self,
        source: LibrarySource,
        item: LearningItem,
        *,
        case: CaseDetail | None = None,
        question: QuestionDetail | None = None,
        locator: str = "",
        relationship: str = "primary_evidence",
    ) -> LibraryArchiveResult:
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
                (int(existing["id"]), source_id, locator, relationship),
            )
            if not bool(existing["active"]):
                self.connection.execute(
                    "UPDATE learning_items SET active = 1, deleted_at = NULL, "
                    "deleted_by = NULL WHERE id = ?",
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
                    question_number, answer_source, exam_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    question.exam_date,
                ),
            )
        return LibraryArchiveResult(
            source_id=source_id, item_id=item_id, duplicate=False
        )

    def archive_structured_import(
        self,
        *,
        original_source: LibrarySource,
        structured_source: LibrarySource,
        schema_version: str,
        original_file_sha256: str,
        structured_json_sha256: str,
        structured_payload_sha256: str,
        preparation_method: str,
        payload_json: str,
        created_by: str,
        created_at: datetime,
        materials: list[dict[str, Any]],
        item_records: list[dict[str, Any]],
        review_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist one validated structured document in one transaction.

        ``learning_items`` remains the compatibility index.  The structured
        tables below it retain the validated payload, ordered blocks and
        relations without asking the legacy segmenter to infer boundaries.
        """

        def add_blocks(
            *,
            import_id: int,
            blocks: Any,
            section: str,
            item_id: int | None = None,
            material_id: int | None = None,
            subquestion_id: int | None = None,
        ) -> int:
            if not isinstance(blocks, list):
                return 0
            count = 0
            for index, block in enumerate(blocks, 1):
                if not isinstance(block, dict):
                    continue
                text = str(block.get("text") or "").strip()
                if not text:
                    continue
                self.connection.execute(
                    """
                    INSERT INTO structured_blocks(
                        import_id, item_id, material_id, subquestion_id, section,
                        external_id, order_index, kind, text, locator, provenance,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        import_id,
                        item_id,
                        material_id,
                        subquestion_id,
                        section,
                        str(block.get("id") or f"{section}-{index}"),
                        int(block.get("order") or index),
                        str(block.get("kind") or "paragraph"),
                        text,
                        str(block.get("locator") or ""),
                        str(block.get("provenance") or "unknown"),
                        _json(
                            {
                                key: value
                                for key, value in block.items()
                                if key
                                not in {
                                    "id",
                                    "order",
                                    "kind",
                                    "text",
                                    "locator",
                                    "provenance",
                                }
                            }
                        ),
                    ),
                )
                count += 1
            return count

        def block_locator(item: dict[str, Any]) -> str:
            locators = item.get("locators")
            if isinstance(locators, list) and locators:
                return str(locators[0])
            for field in ("stem_blocks", "blocks", "basic_facts_blocks"):
                values = item.get(field)
                if isinstance(values, list) and values:
                    return str(values[0].get("locator") or "")
            return ""

        def answer_requirement_blocks(
            item: dict[str, Any], prefix: str
        ) -> list[dict[str, Any]]:
            values = item.get("answer_requirements")
            if not isinstance(values, list):
                return []
            blocks: list[dict[str, Any]] = []
            for index, requirement in enumerate(values, 1):
                if not isinstance(requirement, dict):
                    continue
                blocks.append(
                    {
                        "id": str(requirement.get("id") or f"{prefix}-{index}"),
                        "order": int(requirement.get("order") or index),
                        "kind": "paragraph",
                        "text": str(requirement.get("text") or ""),
                        "locator": str(requirement.get("locator") or ""),
                        "provenance": str(requirement.get("provenance") or "unknown"),
                    }
                )
            return blocks

        with self.connection:
            original_source_id = self.ensure_source(original_source)
            structured_source_id = self.ensure_source(structured_source)
            existing = self.connection.execute(
                """
                SELECT id, original_source_id, structured_source_id
                FROM structured_imports
                WHERE created_by = ? AND original_file_sha256 = ?
                  AND structured_json_sha256 = ?
                  AND structured_payload_sha256 = ?
                """,
                (
                    created_by,
                    original_file_sha256,
                    structured_json_sha256,
                    structured_payload_sha256,
                ),
            ).fetchone()
            duplicate_import = existing is not None
            if existing is None:
                cursor = self.connection.execute(
                    """
                    INSERT INTO structured_imports(
                        original_source_id, structured_source_id, schema_version,
                        original_file_sha256, structured_json_sha256,
                        structured_payload_sha256, preparation_method, payload_json,
                        created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        original_source_id,
                        structured_source_id,
                        schema_version,
                        original_file_sha256,
                        structured_json_sha256,
                        structured_payload_sha256,
                        preparation_method,
                        payload_json,
                        created_by,
                        created_at.isoformat(),
                    ),
                )
                import_id = int(cursor.lastrowid)
            else:
                import_id = int(existing["id"])
                original_source_id = int(existing["original_source_id"])
                structured_source_id = int(existing["structured_source_id"])
            material_ids: dict[str, int] = {}
            material_count = 0
            existing_materials = {
                str(row["external_id"]): int(row["id"])
                for row in self.connection.execute(
                    "SELECT id, external_id FROM structured_materials WHERE import_id=?",
                    (import_id,),
                ).fetchall()
            }
            for material in materials:
                external_id = str(material.get("id") or "").strip()
                if not external_id:
                    continue
                if external_id in existing_materials:
                    material_ids[external_id] = existing_materials[external_id]
                    continue
                material_cursor = self.connection.execute(
                    """
                    INSERT INTO structured_materials(
                        import_id, external_id, title, metadata_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        import_id,
                        external_id,
                        str(material.get("title") or ""),
                        _json(
                            {
                                key: value
                                for key, value in material.items()
                                if key not in {"id", "title", "blocks"}
                            }
                        ),
                    ),
                )
                material_id = int(material_cursor.lastrowid)
                material_ids[external_id] = material_id
                add_blocks(
                    import_id=import_id,
                    blocks=material.get("blocks"),
                    section="material",
                    material_id=material_id,
                )
                material_count += 1

            archived = 0
            duplicates = 0
            item_results: list[dict[str, Any]] = []
            for record in item_records:
                payload = dict(record["payload"])
                item = record["item"]
                external_id = str(payload.get("id") or "")
                existing_binding = self.connection.execute(
                    "SELECT item_id FROM structured_item_bindings "
                    "WHERE import_id=? AND external_id=?",
                    (import_id, external_id),
                ).fetchone()
                if existing_binding is not None:
                    duplicates += 1
                    item_results.append(
                        {
                            "item_id": int(existing_binding["item_id"]),
                            "external_id": payload.get("id"),
                            "source_number": payload.get("source_number", ""),
                            "status": "duplicate",
                            "review_status": record.get(
                                "review_status", "pending_review"
                            ),
                        }
                    )
                    continue
                archive_result = self._archive_inner(
                    original_source,
                    item,
                    case=record.get("case"),
                    question=record.get("question"),
                    locator=block_locator(payload),
                    relationship="structured_evidence",
                )
                if archive_result.duplicate:
                    duplicates += 1
                else:
                    archived += 1
                item_id = archive_result.item_id
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO learning_item_sources(
                        item_id, source_id, locator, relationship
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (item_id, structured_source_id, "", "structured_derivation"),
                )
                binding_cursor = self.connection.execute(
                    """
                    INSERT INTO structured_item_bindings(
                        import_id, item_id, external_id, source_number, item_kind,
                        structure_version, review_status, payload_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        import_id,
                        item_id,
                        str(payload.get("id") or ""),
                        str(payload.get("source_number") or ""),
                        str(record.get("item_kind") or "question"),
                        schema_version,
                        str(record.get("review_status") or "pending_review"),
                        _json(payload),
                        _json(record.get("metadata") or {}),
                    ),
                )
                binding_id = int(binding_cursor.lastrowid)
                for relation_order, material_ref in enumerate(
                    payload.get("material_refs") or [], 1
                ):
                    material_id = material_ids.get(str(material_ref))
                    if material_id is not None:
                        self.connection.execute(
                            """
                            INSERT INTO structured_material_relations(
                                binding_id, material_id, relation_order
                            ) VALUES (?, ?, ?)
                            """,
                            (binding_id, material_id, relation_order),
                        )
                add_blocks(
                    import_id=import_id,
                    blocks=payload.get("stem_blocks"),
                    section="stem",
                    item_id=item_id,
                )
                add_blocks(
                    import_id=import_id,
                    blocks=payload.get("explanation_blocks"),
                    section="explanation",
                    item_id=item_id,
                )
                add_blocks(
                    import_id=import_id,
                    blocks=answer_requirement_blocks(
                        payload, f"{payload.get('id')}-requirement"
                    ),
                    section="answer_requirement",
                    item_id=item_id,
                )
                answer = payload.get("answer")
                if isinstance(answer, dict):
                    add_blocks(
                        import_id=import_id,
                        blocks=answer.get("blocks"),
                        section="answer",
                        item_id=item_id,
                    )
                if isinstance(payload.get("options"), list):
                    option_blocks = [
                        {
                            "id": f"{payload.get('id')}-option-{index}",
                            "order": index,
                            "kind": "paragraph",
                            "text": f"{option.get('key', '')}. {option.get('text', '')}",
                            "locator": str(
                                option.get("locator") or block_locator(payload)
                            ),
                            "provenance": "source_text",
                        }
                        for index, option in enumerate(payload["options"], 1)
                        if isinstance(option, dict)
                    ]
                    add_blocks(
                        import_id=import_id,
                        blocks=option_blocks,
                        section="options",
                        item_id=item_id,
                    )
                for index, subquestion in enumerate(
                    payload.get("subquestions") or [], 1
                ):
                    if not isinstance(subquestion, dict):
                        continue
                    sub_cursor = self.connection.execute(
                        """
                        INSERT INTO structured_subquestions(
                            binding_id, external_id, source_number, order_index,
                            answer_status, answer_reason, locators_json, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            binding_id,
                            str(subquestion.get("id") or f"sub-{index}"),
                            str(subquestion.get("source_number") or ""),
                            index,
                            str(
                                subquestion.get("answer_status")
                                or (
                                    "provided"
                                    if subquestion.get("answer") is not None
                                    else "unresolved"
                                )
                            ),
                            str(subquestion.get("answer_reason") or ""),
                            _json(subquestion.get("locators") or []),
                            _json(subquestion),
                        ),
                    )
                    subquestion_id = int(sub_cursor.lastrowid)
                    add_blocks(
                        import_id=import_id,
                        blocks=subquestion.get("stem_blocks"),
                        section="subquestion_stem",
                        item_id=item_id,
                        subquestion_id=subquestion_id,
                    )
                    add_blocks(
                        import_id=import_id,
                        blocks=subquestion.get("explanation_blocks"),
                        section="subquestion_explanation",
                        item_id=item_id,
                        subquestion_id=subquestion_id,
                    )
                    add_blocks(
                        import_id=import_id,
                        blocks=answer_requirement_blocks(
                            subquestion,
                            f"{payload.get('id')}-sub-{index}-requirement",
                        ),
                        section="subquestion_answer_requirement",
                        item_id=item_id,
                        subquestion_id=subquestion_id,
                    )
                    sub_answer = subquestion.get("answer")
                    if isinstance(sub_answer, dict):
                        add_blocks(
                            import_id=import_id,
                            blocks=sub_answer.get("blocks"),
                            section="subquestion_answer",
                            item_id=item_id,
                            subquestion_id=subquestion_id,
                        )
                item_results.append(
                    {
                        "item_id": item_id,
                        "external_id": payload.get("id"),
                        "source_number": payload.get("source_number", ""),
                        "status": "duplicate"
                        if archive_result.duplicate
                        else "archived",
                        "review_status": record.get("review_status", "pending_review"),
                    }
                )

            review_items: list[dict[str, Any]] = []
            for review in [] if duplicate_import else review_records:
                review_id = self.record_review_item(
                    source_id=original_source_id,
                    candidate_key=str(review["candidate_key"]),
                    material_type=str(review.get("material_type") or "structured"),
                    locator=str(review.get("locator") or ""),
                    raw_fragment=str(review.get("raw_fragment") or ""),
                    proposed_structure=dict(review.get("proposed_structure") or {}),
                    review_reason=str(review.get("review_reason") or "需要人工复核"),
                    now=created_at,
                    structured_import_id=import_id,
                )
                review_items.append({"id": review_id, **review})

        return {
            "duplicate": duplicate_import,
            "import_id": import_id,
            "original_source_id": original_source_id,
            "structured_source_id": structured_source_id,
            "archived": archived,
            "duplicate_items": duplicates,
            "failed": 0,
            "needs_review": len(review_records)
            + sum(
                record.get("review_status") == "pending_review"
                for record in item_records
            ),
            "materials": material_count,
            "items": item_results,
            "review_items": review_items,
        }

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
                exam_date=str(question_row["exam_date"]),
            )
        structured = self._structured_for_item(item_id)
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
            structured=structured["structured"] if structured else None,
            shared_materials=tuple(
                structured["shared_materials"] if structured else ()
            ),
            stem_blocks=tuple(structured["stem_blocks"] if structured else ()),
            subquestions=tuple(structured["subquestions"] if structured else ()),
            explanation_blocks=tuple(
                structured["explanation_blocks"] if structured else ()
            ),
        )

    def _structured_for_item(self, item_id: int) -> dict[str, Any] | None:
        binding = self.connection.execute(
            """
            SELECT b.*, i.schema_version, i.original_file_sha256,
                   i.structured_json_sha256, i.structured_payload_sha256,
                   i.preparation_method, i.created_by AS import_created_by,
                   i.created_at AS import_created_at
            FROM structured_item_bindings AS b
            JOIN structured_imports AS i ON i.id = b.import_id
            WHERE b.item_id = ?
            ORDER BY b.id DESC LIMIT 1
            """,
            (item_id,),
        ).fetchone()
        if binding is None:
            return None

        def block_dict(row: sqlite3.Row) -> dict[str, Any]:
            metadata = json.loads(row["metadata_json"])
            return {
                "id": row["external_id"],
                "order": int(row["order_index"]),
                "kind": row["kind"],
                "text": row["text"],
                "locator": row["locator"],
                "provenance": row["provenance"],
                **metadata,
            }

        item_blocks = self.connection.execute(
            """
            SELECT * FROM structured_blocks
            WHERE item_id = ? AND subquestion_id IS NULL
            ORDER BY section, order_index, id
            """,
            (item_id,),
        ).fetchall()
        stem_blocks = [
            block_dict(row) for row in item_blocks if row["section"] == "stem"
        ]
        explanation_blocks = [
            block_dict(row) for row in item_blocks if row["section"] == "explanation"
        ]
        answer_requirements = [
            block_dict(row)
            for row in item_blocks
            if row["section"] == "answer_requirement"
        ]
        shared_materials: list[dict[str, Any]] = []
        material_rows = self.connection.execute(
            """
            SELECT m.* FROM structured_materials AS m
            JOIN structured_material_relations AS r ON r.material_id = m.id
            WHERE r.binding_id = ? ORDER BY r.relation_order
            """,
            (int(binding["id"]),),
        ).fetchall()
        for material in material_rows:
            blocks = self.connection.execute(
                """
                SELECT * FROM structured_blocks
                WHERE material_id = ? ORDER BY order_index, id
                """,
                (int(material["id"]),),
            ).fetchall()
            shared_materials.append(
                {
                    "id": material["external_id"],
                    "title": material["title"],
                    "metadata": json.loads(material["metadata_json"]),
                    "blocks": [block_dict(row) for row in blocks],
                }
            )

        subquestions: list[dict[str, Any]] = []
        sub_rows = self.connection.execute(
            """
            SELECT * FROM structured_subquestions
            WHERE binding_id = ? ORDER BY order_index, id
            """,
            (int(binding["id"]),),
        ).fetchall()
        for subquestion in sub_rows:
            sub_blocks = self.connection.execute(
                """
                SELECT * FROM structured_blocks
                WHERE subquestion_id = ? ORDER BY section, order_index, id
                """,
                (int(subquestion["id"]),),
            ).fetchall()
            payload = json.loads(subquestion["payload_json"])
            payload["id"] = subquestion["external_id"]
            payload["source_number"] = subquestion["source_number"]
            payload["answer_status"] = subquestion["answer_status"]
            payload["answer_reason"] = subquestion["answer_reason"]
            payload["locators"] = json.loads(subquestion["locators_json"])
            payload["stem_blocks"] = [
                block_dict(row)
                for row in sub_blocks
                if row["section"] == "subquestion_stem"
            ]
            payload["explanation_blocks"] = [
                block_dict(row)
                for row in sub_blocks
                if row["section"] == "subquestion_explanation"
            ]
            payload["answer_requirements"] = [
                block_dict(row)
                for row in sub_blocks
                if row["section"] == "subquestion_answer_requirement"
            ]
            subquestions.append(payload)

        return {
            "structured": {
                "import_id": int(binding["import_id"]),
                "external_id": binding["external_id"],
                "source_number": binding["source_number"],
                "item_kind": binding["item_kind"],
                "schema_version": binding["schema_version"],
                "review_status": binding["review_status"],
                "verified_real_question_id": (
                    int(binding["verified_real_question_id"])
                    if binding["verified_real_question_id"] is not None
                    else None
                ),
                "promoted_by": binding["promoted_by"],
                "promoted_at": binding["promoted_at"],
                "original_file_sha256": binding["original_file_sha256"],
                "structured_json_sha256": binding["structured_json_sha256"],
                "structured_payload_sha256": binding["structured_payload_sha256"],
                "preparation_method": binding["preparation_method"],
                "payload": json.loads(binding["payload_json"]),
                "answer_requirements": answer_requirements,
            },
            "shared_materials": shared_materials,
            "stem_blocks": stem_blocks,
            "subquestions": subquestions,
            "explanation_blocks": explanation_blocks,
        }

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

    def list_by_source_metadata(
        self,
        *,
        created_by: str,
        key: str,
        value: str,
        identity: str = "",
        active_only: bool = False,
    ) -> list[LearningItem]:
        """Return every item linked to sources with a stable upstream identity."""
        source_ids = self.source_ids_by_metadata(
            created_by=created_by, key=key, value=value
        )
        if not source_ids:
            return []
        placeholders = ", ".join("?" for _ in source_ids)
        clauses = [f"link.source_id IN ({placeholders})"]
        params: list[Any] = list(source_ids)
        if identity:
            clauses.append("i.identity = ?")
            params.append(identity)
        if active_only:
            clauses.append("i.active = 1")
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT i.* FROM learning_items AS i
            JOIN learning_item_sources AS link ON link.item_id = i.id
            WHERE {" AND ".join(clauses)}
            ORDER BY i.id
            """,
            params,
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
        structured_import_id: int | None = None,
    ) -> int:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO learning_review_items(
                    source_id, candidate_key, material_type, locator, raw_fragment,
                    proposed_structure_json, review_reason, status, created_at, updated_at,
                    structured_import_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(source_id, candidate_key) DO UPDATE SET
                    material_type = excluded.material_type,
                    locator = excluded.locator,
                    raw_fragment = excluded.raw_fragment,
                    proposed_structure_json = excluded.proposed_structure_json,
                    review_reason = excluded.review_reason,
                    structured_import_id = COALESCE(
                        excluded.structured_import_id,
                        learning_review_items.structured_import_id
                    ),
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
                    structured_import_id,
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
        offset: int = 0,
    ) -> list[LearningReviewItem]:
        clauses = ["status = ?"]
        params: list[Any] = [status]
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        params.extend((max(1, min(int(limit), 200)), max(0, int(offset))))
        rows = self.connection.execute(
            f"""
            SELECT * FROM learning_review_items
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [_review_from_row(row) for row in rows]

    def count_filtered_review_items(
        self, *, source_id: int | None = None, status: str = "pending"
    ) -> int:
        clauses = ["status = ?"]
        params: list[Any] = [status]
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(int(source_id))
        row = self.connection.execute(
            f"SELECT COUNT(*) AS total FROM learning_review_items WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
        return int(row["total"])

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

    def apply_review_status_batch(
        self,
        review_ids: list[int],
        expected: dict[int, dict[str, Any]],
        status: str,
        *,
        actor_id: str,
        at: str,
    ) -> list[int]:
        """Apply a locked review status batch atomically and audit its scope."""
        if status not in {"pending", "resolved", "superseded"}:
            raise ValueError("不支持的待复核状态")
        ids = list(dict.fromkeys(int(value) for value in review_ids))
        if not ids or len(ids) > 100:
            raise ValueError("请选择 1 至 100 条复核记录")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for review_id in ids:
                row = self.connection.execute(
                    "SELECT source_id, candidate_key, status, updated_at "
                    "FROM learning_review_items WHERE id = ?",
                    (review_id,),
                ).fetchone()
                wanted = expected.get(review_id)
                actual = (
                    {
                        "source_id": int(row["source_id"]),
                        "candidate_key": str(row["candidate_key"]),
                        "status": str(row["status"]),
                        "updated_at": str(row["updated_at"]),
                    }
                    if row
                    else None
                )
                if actual is None or actual != wanted:
                    raise ValueError("复核队列在预览后发生变化，请重新预览")
            for review_id in ids:
                self.connection.execute(
                    "UPDATE learning_review_items SET status = ?, updated_at = ? WHERE id = ?",
                    (status, at, review_id),
                )
            self.connection.execute(
                "INSERT INTO operator_action_audits(actor_id, action, scope, "
                "target_ids_json, outcome_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(actor_id),
                    "review_status_batch",
                    "review",
                    _json(ids),
                    _json({"status": status, "count": len(ids)}),
                    at,
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return ids

    def search(
        self,
        *,
        query: str = "",
        item_type: str = "",
        identity: str = "",
        subject: str = "",
        limit: int | None = 10,
        include_inactive: bool = False,
        offset: int = 0,
    ) -> list[LearningItem]:
        clauses, params = self._search_filters(
            query=query,
            item_type=item_type,
            identity=identity,
            subject=subject,
            include_inactive=include_inactive,
        )
        limit_clause = ""
        if limit is not None:
            safe_limit = max(1, min(int(limit), 5000))
            params.extend((safe_limit, max(0, int(offset))))
            limit_clause = " LIMIT ? OFFSET ?"
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT i.* FROM learning_items AS i
            LEFT JOIN learning_item_sources AS link ON link.item_id = i.id
            LEFT JOIN library_sources AS s ON s.id = link.source_id
            LEFT JOIN learning_cases AS c ON c.item_id = i.id
            LEFT JOIN learning_questions AS q ON q.item_id = i.id
            WHERE {" AND ".join(clauses)}
            ORDER BY i.updated_at DESC, i.id DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
        return [_item_from_row(row) for row in rows]

    def count_search(
        self,
        *,
        query: str = "",
        item_type: str = "",
        identity: str = "",
        subject: str = "",
        include_inactive: bool = False,
    ) -> int:
        clauses, params = self._search_filters(
            query=query,
            item_type=item_type,
            identity=identity,
            subject=subject,
            include_inactive=include_inactive,
        )
        row = self.connection.execute(
            "SELECT COUNT(DISTINCT i.id) AS total FROM learning_items AS i "
            "LEFT JOIN learning_item_sources AS link ON link.item_id = i.id "
            "LEFT JOIN library_sources AS s ON s.id = link.source_id "
            "LEFT JOIN learning_cases AS c ON c.item_id = i.id "
            "LEFT JOIN learning_questions AS q ON q.item_id = i.id "
            f"WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
        return int(row["total"])

    def _search_filters(
        self,
        *,
        query: str,
        item_type: str,
        identity: str,
        subject: str,
        include_inactive: bool,
    ) -> tuple[list[str], list[Any]]:
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
            subjects = subject_filter(subject)
            if subjects is None:
                raise ValueError(f"不支持的资料方向：{subject}")
            clauses.append(
                "(" + " OR ".join("i.subjects_json LIKE ?" for _ in subjects) + ")"
            )
            params.extend(f'%"{candidate}"%' for candidate in sorted(subjects))
        if query:
            term = f"%{query}%"
            clauses.append(
                "((i.item_type = 'question' AND (i.title LIKE ? OR q.stem LIKE ? "
                "OR q.options_json LIKE ? OR EXISTS (SELECT 1 FROM structured_blocks qsb "
                "WHERE qsb.item_id = i.id AND qsb.section IN "
                "('stem','options','answer_requirement','subquestion_stem',"
                "'subquestion_answer_requirement') AND qsb.text LIKE ?) OR EXISTS ("
                "SELECT 1 FROM structured_material_relations qsmr "
                "JOIN structured_item_bindings qsib ON qsib.id=qsmr.binding_id "
                "JOIN structured_blocks qsmb ON qsmb.material_id=qsmr.material_id "
                "WHERE qsib.item_id=i.id AND qsmb.text LIKE ?))) OR "
                "(i.item_type <> 'question' AND (i.title LIKE ? OR i.source_summary LIKE ? "
                "OR (COALESCE(s.source_kind,'') NOT IN ('structured_original',"
                "'structured_json') AND s.raw_text LIKE ?) OR c.case_summary LIKE ? "
                "OR c.practice_notes_json LIKE ? OR q.stem LIKE ? OR q.explanation LIKE ? "
                "OR i.metadata_json LIKE ? OR EXISTS (SELECT 1 FROM structured_blocks sb "
                "WHERE sb.item_id=i.id AND sb.text LIKE ?) OR EXISTS (SELECT 1 FROM "
                "structured_material_relations smr JOIN structured_item_bindings sib "
                "ON sib.id=smr.binding_id JOIN structured_blocks smb "
                "ON smb.material_id=smr.material_id WHERE sib.item_id=i.id "
                "AND smb.text LIKE ?))))"
            )
            params.extend([term] * 15)
        return clauses, params

    def safe_question_summary(self, item_id: int | None) -> str:
        """Return answer-free question text for search results, never legacy summary."""
        if item_id is None:
            return ""
        row = self.connection.execute(
            "SELECT stem FROM learning_questions WHERE item_id = ?", (item_id,)
        ).fetchone()
        parts = [str(row["stem"] or "").strip()] if row else []
        block_rows = self.connection.execute(
            """
            SELECT text FROM structured_blocks
            WHERE item_id = ? AND section IN ('stem', 'subquestion_stem')
            ORDER BY CASE section WHEN 'stem' THEN 0 ELSE 1 END, order_index, id
            """,
            (item_id,),
        ).fetchall()
        seen = {part for part in parts if part}
        for block in block_rows:
            text = str(block["text"] or "").strip()
            if text and text not in seen:
                seen.add(text)
                parts.append(text)
        return "\n".join(part for part in parts if part).strip()[:300]

    def count_items(
        self, *, item_type: str = "", identity: str = "", active_only: bool = True
    ) -> int:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if active_only:
            clauses.append("active = 1")
        if item_type:
            clauses.append("item_type = ?")
            params.append(item_type)
        if identity:
            clauses.append("identity = ?")
            params.append(identity)
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM learning_items WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
        return int(row["count"])

    def count_review_items(self, *, status: str | None = None) -> int:
        if status:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM learning_review_items WHERE status = ?",
                (status,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM learning_review_items"
            ).fetchone()
        return int(row["count"])

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
            if _LINKED_REAL_QUESTION_FIELDS.intersection(changes):
                linked = self._linked_real_question_id(item_id)
                if linked is not None:
                    self._synchronize_linked_real_question(linked, changes)
            item_updates: list[str] = []
            params: list[Any] = []
            if "title" in changes:
                item_updates.append("title = ?")
                params.append(str(changes["title"]))
            if "subjects" in changes:
                item_updates.append("subjects_json = ?")
                params.append(_json(list(changes["subjects"])))
            if "subjects" in changes or "note" in changes:
                metadata = json.loads(row["metadata_json"])
                if "subjects" in changes and row["identity"] == "official_case":
                    metadata["manual_subjects"] = True
                if "note" in changes:
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

    def update_management(
        self, item_id: int, changes: dict[str, Any]
    ) -> LibraryItemBundle | None:
        """Update allowlisted normalized fields while leaving source bytes immutable."""
        allowed = {
            "title",
            "subjects",
            "note",
            "body",
            "practice_notes",
            "case_number",
            "authority",
            "case_summary",
            "issues",
            "reasoning",
            "result_text",
            "question_type",
            "stem",
            "options",
            "answer",
            "explanation",
            "answer_source",
            "exam_name",
            "exam_year",
            "exam_date",
            "paper",
            "question_number",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported management fields: {sorted(unknown)}")
        with self.connection:
            bundle = self.get(int(item_id))
            if bundle is None:
                return None
            linked_real_question_id = self._linked_real_question_id(int(item_id))
            if (
                linked_real_question_id is not None
                and _LINKED_REAL_QUESTION_FIELDS.intersection(changes)
            ):
                self._synchronize_linked_real_question(linked_real_question_id, changes)
            updates: list[str] = []
            values: list[Any] = []
            if "title" in changes:
                updates.append("title = ?")
                values.append(str(changes["title"]))
            if "subjects" in changes:
                updates.append("subjects_json = ?")
                values.append(_json(list(changes["subjects"])))
            metadata_changed = "note" in changes or "body" in changes
            if metadata_changed:
                metadata = dict(bundle.item.metadata)
                for field in ("note", "body"):
                    if field in changes:
                        metadata[field] = str(changes[field])
                updates.append("metadata_json = ?")
                values.append(_json(metadata))
            updates.append("updated_at = ?")
            values.append(datetime.now().astimezone().isoformat())
            values.append(int(item_id))
            self.connection.execute(
                f"UPDATE learning_items SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            if bundle.question is not None:
                q_fields = {
                    "question_type": "question_type",
                    "stem": "stem",
                    "options": "options_json",
                    "answer": "answer_json",
                    "explanation": "explanation",
                    "answer_source": "answer_source",
                    "exam_name": "exam_name",
                    "exam_year": "exam_year",
                    "exam_date": "exam_date",
                    "paper": "paper",
                    "question_number": "question_number",
                }
                selected = [
                    (key, column) for key, column in q_fields.items() if key in changes
                ]
                if selected:
                    assignments = []
                    q_values = []
                    for key, column in selected:
                        value = changes[key]
                        if key in {"options", "answer"}:
                            value = _json(value)
                        assignments.append(f"{column} = ?")
                        q_values.append(value)
                    q_values.append(int(item_id))
                    self.connection.execute(
                        f"UPDATE learning_questions SET {', '.join(assignments)} WHERE item_id = ?",
                        q_values,
                    )
            if bundle.case is not None:
                c_fields = {
                    "case_number": "case_number",
                    "authority": "authority",
                    "case_summary": "case_summary",
                    "issues": "issues_json",
                    "reasoning": "reasoning",
                    "result_text": "result_text",
                    "practice_notes": "practice_notes_json",
                }
                selected = [
                    (key, column) for key, column in c_fields.items() if key in changes
                ]
                if selected:
                    assignments = []
                    c_values = []
                    for key, column in selected:
                        value = (
                            _json(list(changes[key]))
                            if key in {"issues", "practice_notes"}
                            else changes[key]
                        )
                        assignments.append(f"{column} = ?")
                        c_values.append(value)
                    c_values.append(int(item_id))
                    self.connection.execute(
                        f"UPDATE learning_cases SET {', '.join(assignments)} WHERE item_id = ?",
                        c_values,
                    )
        return self.get(int(item_id))

    def _linked_real_question_id(self, item_id: int) -> int | None:
        row = self.connection.execute(
            "SELECT verified_real_question_id FROM structured_item_bindings "
            "WHERE item_id = ? AND verified_real_question_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (int(item_id),),
        ).fetchone()
        return int(row["verified_real_question_id"]) if row else None

    def _synchronize_linked_real_question(
        self, real_question_id: int, changes: dict[str, Any]
    ) -> None:
        """Atomically update canonical truth and every normalized linked projection."""
        normalized = self._update_linked_real_question(real_question_id, changes)
        projection_fields = {
            "question_type": normalized.question_type,
            "stem": normalized.stem,
            "options_json": _json(normalized.options),
            "answer_json": _json(normalized.answer)
            if normalized.answer is not None
            else None,
            "explanation": normalized.explanation,
            "answer_source": normalized.answer_source,
            "exam_name": normalized.exam_name,
            "exam_year": normalized.exam_year,
            "exam_date": normalized.exam_date,
            "paper": normalized.paper,
            "question_number": normalized.question_number,
        }
        linked_ids = [
            int(row["item_id"])
            for row in self.connection.execute(
                "SELECT item_id FROM structured_item_bindings "
                "WHERE verified_real_question_id = ? ORDER BY item_id",
                (int(real_question_id),),
            ).fetchall()
        ]
        if not linked_ids:
            raise ValueError("关联核验真题已没有资料投影，未保存任何更改")
        assignments = ", ".join(f"{column} = ?" for column in projection_fields)
        placeholders = ",".join("?" for _ in linked_ids)
        self.connection.execute(
            f"UPDATE learning_questions SET {assignments} WHERE item_id IN ({placeholders})",
            [*projection_fields.values(), *linked_ids],
        )
        self.connection.execute(
            f"UPDATE learning_items SET subjects_json = ?, updated_at = ? "
            f"WHERE id IN ({placeholders})",
            [
                _json([normalized.subject]),
                datetime.now().astimezone().isoformat(),
                *linked_ids,
            ],
        )

    def _update_linked_real_question(
        self, real_question_id: int, changes: dict[str, Any]
    ) -> RealQuestion:
        row = self.connection.execute(
            "SELECT * FROM real_questions WHERE id = ?", (real_question_id,)
        ).fetchone()
        if row is None:
            raise ValueError("关联的核验真题不存在；请先重新核验候选资料")
        mapping = {
            "source_name": row["source_name"],
            "exam_name": row["exam_name"],
            "exam_year": row["exam_year"],
            "exam_date": row["exam_date"],
            "paper": row["paper"],
            "question_number": row["question_number"],
            "source_url": row["source_url"],
            "source_locator": row["source_locator"],
            "subject": row["subject"],
            "question_type": row["question_type"],
            "stem": row["stem"],
            "options": json.loads(row["options_json"]),
            "answer": json.loads(row["answer_json"])
            if row["answer_json"] is not None
            else None,
            "explanation": row["explanation"],
            "answer_source": row["answer_source"],
            "verification_status": row["verification_status"],
            "metadata": json.loads(row["metadata_json"]),
        }
        question_fields = {
            "source_name",
            "exam_name",
            "exam_year",
            "exam_date",
            "paper",
            "question_number",
            "source_url",
            "source_locator",
            "question_type",
            "stem",
            "options",
            "answer",
            "explanation",
            "answer_source",
        }
        for key in question_fields & changes.keys():
            mapping[key] = changes[key]
        if "subjects" in changes:
            subjects = list(changes["subjects"])
            if len(subjects) != 1:
                raise ValueError("已核验真题必须对应且仅对应一个规范方向")
            mapping["subject"] = subjects[0]

        normalized = RealQuestion.from_mapping(mapping)
        identity_key = real_question_identity_key(normalized)
        collision = self.connection.execute(
            "SELECT id FROM real_questions WHERE identity_key = ? AND id <> ?",
            (identity_key, real_question_id),
        ).fetchone()
        if collision is not None:
            raise ValueError("修改后的题目身份与另一条核验真题冲突，未保存任何更改")

        self.connection.execute(
            """
            UPDATE real_questions SET identity_key = ?, source_name = ?, exam_name = ?,
                exam_year = ?, exam_date = ?, paper = ?, question_number = ?,
                source_url = ?, source_locator = ?, subject = ?, question_type = ?,
                stem = ?, options_json = ?, answer_json = ?, explanation = ?,
                answer_source = ?, verification_status = ?, content_hash = ?,
                metadata_json = ?, updated_at = ? WHERE id = ?
            """,
            (
                identity_key,
                normalized.source_name,
                normalized.exam_name,
                normalized.exam_year,
                normalized.exam_date,
                normalized.paper,
                normalized.question_number,
                normalized.source_url,
                normalized.source_locator,
                normalized.subject,
                normalized.question_type,
                normalized.stem,
                _json(normalized.options),
                _json(normalized.answer) if normalized.answer is not None else None,
                normalized.explanation,
                normalized.answer_source,
                normalized.verification_status,
                normalized.content_hash,
                _json(normalized.metadata),
                datetime.now().astimezone().isoformat(),
                real_question_id,
            ),
        )
        return normalized

    def soft_delete_items(
        self, item_ids: list[int], *, actor_id: str, at: str
    ) -> list[int]:
        ids = list(dict.fromkeys(int(value) for value in item_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.connection:
            rows = self.connection.execute(
                f"SELECT id FROM learning_items WHERE id IN ({placeholders}) AND active = 1",
                ids,
            ).fetchall()
            changed = [int(row["id"]) for row in rows]
            if changed:
                changed_placeholders = ",".join("?" for _ in changed)
                self.connection.execute(
                    f"UPDATE learning_items SET active = 0, deleted_at = ?, deleted_by = ? "
                    f"WHERE id IN ({changed_placeholders})",
                    [str(at), str(actor_id), *changed],
                )
        return changed

    def restore_items(
        self, item_ids: list[int], *, actor_id: str, at: str
    ) -> list[int]:
        ids = list(dict.fromkeys(int(value) for value in item_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.connection:
            rows = self.connection.execute(
                f"SELECT id FROM learning_items WHERE id IN ({placeholders}) AND active = 0",
                ids,
            ).fetchall()
            changed = [int(row["id"]) for row in rows]
            if changed:
                changed_placeholders = ",".join("?" for _ in changed)
                self.connection.execute(
                    f"UPDATE learning_items SET active = 1, deleted_at = NULL, deleted_by = NULL, "
                    f"updated_at = ? WHERE id IN ({changed_placeholders})",
                    [str(at), *changed],
                )
        return changed

    def apply_management_batch(
        self,
        *,
        action: str,
        item_ids: list[int],
        expected: dict[int, dict[str, Any]],
        actor_id: str,
        at: str,
        changes: dict[str, Any] | None = None,
    ) -> list[int]:
        """Apply one reviewed homogeneous mutation atomically after fingerprint checks."""
        if action not in {"edit", "delete", "restore", "verify_case"}:
            raise ValueError("unsupported management batch action")
        ids = list(dict.fromkeys(int(value) for value in item_ids))
        if not ids or len(ids) > 100 or set(ids) != set(expected):
            raise ValueError("batch item set changed")
        normalized_changes = changes or {}
        if action == "verify_case" and (
            set(normalized_changes) != {"verification_status"}
            or normalized_changes.get("verification_status")
            not in {"unverified", "user_verified"}
        ):
            raise ValueError("unsupported user-case verification status")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                rows_by_id: dict[int, sqlite3.Row] = {}
                for item_id in ids:
                    row = self.connection.execute(
                        "SELECT item_hash, updated_at, active, identity, item_type, verification_status, metadata_json "
                        "FROM learning_items WHERE id = ?",
                        (item_id,),
                    ).fetchone()
                    fingerprint = expected[item_id]
                    if (
                        row is None
                        or any(
                            str(row[key] if row[key] is not None else "")
                            != str(
                                fingerprint.get(key, "")
                                if fingerprint.get(key) is not None
                                else ""
                            )
                            for key in (
                                "item_hash",
                                "updated_at",
                                "active",
                                "identity",
                                "item_type",
                            )
                        )
                        or (
                            "verification_status" in fingerprint
                            and str(row["verification_status"] or "")
                            != str(fingerprint["verification_status"] or "")
                        )
                    ):
                        raise ValueError(f"资料 {item_id} 在预览后发生变化")
                    rows_by_id[item_id] = row
                for item_id in ids:
                    row = rows_by_id[item_id]
                    if action == "verify_case":
                        if row["item_type"] != "case" or row["identity"] != "user_case":
                            raise ValueError("批量核验只支持 user_case")
                        if not int(row["active"]):
                            raise ValueError("停用的用户案例不能核验")
                        self.connection.execute(
                            "UPDATE learning_items SET verification_status=?, updated_at=? WHERE id=?",
                            (
                                str(normalized_changes["verification_status"]),
                                str(at),
                                item_id,
                            ),
                        )
                    elif action == "delete":
                        if not int(row["active"]):
                            raise ValueError(f"资料 {item_id} 已停用")
                        self.connection.execute(
                            "UPDATE learning_items SET active=0, deleted_at=?, deleted_by=?, updated_at=? WHERE id=?",
                            (str(at), str(actor_id), str(at), item_id),
                        )
                    elif action == "restore":
                        if int(row["active"]):
                            raise ValueError(f"资料 {item_id} 已启用")
                        self.connection.execute(
                            "UPDATE learning_items SET active=1, deleted_at=NULL, deleted_by=NULL, updated_at=? WHERE id=?",
                            (str(at), item_id),
                        )
                    else:
                        linked_real_question_id = self._linked_real_question_id(item_id)
                        if (
                            linked_real_question_id is not None
                            and _LINKED_REAL_QUESTION_FIELDS.intersection(
                                normalized_changes
                            )
                        ):
                            self._synchronize_linked_real_question(
                                linked_real_question_id, normalized_changes
                            )
                        metadata = json.loads(row["metadata_json"] or "{}")
                        for key in ("note", "body"):
                            if key in normalized_changes:
                                metadata[key] = str(normalized_changes[key])
                        item_updates: list[str] = []
                        item_values: list[Any] = []
                        if "title" in normalized_changes:
                            item_updates.append("title=?")
                            item_values.append(str(normalized_changes["title"]))
                        if "subjects" in normalized_changes:
                            item_updates.append("subjects_json=?")
                            item_values.append(
                                _json(list(normalized_changes["subjects"]))
                            )
                        if "note" in normalized_changes or "body" in normalized_changes:
                            item_updates.append("metadata_json=?")
                            item_values.append(_json(metadata))
                        item_updates.append("updated_at=?")
                        item_values.extend((str(at), item_id))
                        self.connection.execute(
                            f"UPDATE learning_items SET {', '.join(item_updates)} WHERE id=?",
                            item_values,
                        )
                        question_fields = {
                            "question_type": "question_type",
                            "stem": "stem",
                            "options": "options_json",
                            "answer": "answer_json",
                            "explanation": "explanation",
                            "answer_source": "answer_source",
                            "exam_name": "exam_name",
                            "exam_year": "exam_year",
                            "exam_date": "exam_date",
                            "paper": "paper",
                            "question_number": "question_number",
                        }
                        q_pairs = [
                            (key, col)
                            for key, col in question_fields.items()
                            if key in normalized_changes
                        ]
                        if q_pairs:
                            self.connection.execute(
                                "UPDATE learning_questions SET "
                                + ", ".join(f"{col}=?" for _, col in q_pairs)
                                + " WHERE item_id=?",
                                [
                                    (
                                        _json(normalized_changes[key])
                                        if key in {"options", "answer"}
                                        else normalized_changes[key]
                                    )
                                    for key, _ in q_pairs
                                ]
                                + [item_id],
                            )
                        case_fields = {
                            "case_number": "case_number",
                            "authority": "authority",
                            "case_summary": "case_summary",
                            "issues": "issues_json",
                            "reasoning": "reasoning",
                            "result_text": "result_text",
                            "practice_notes": "practice_notes_json",
                        }
                        c_pairs = [
                            (key, col)
                            for key, col in case_fields.items()
                            if key in normalized_changes
                        ]
                        if c_pairs:
                            self.connection.execute(
                                "UPDATE learning_cases SET "
                                + ", ".join(f"{col}=?" for _, col in c_pairs)
                                + " WHERE item_id=?",
                                [
                                    (
                                        _json(list(normalized_changes[key]))
                                        if key in {"issues", "practice_notes"}
                                        else normalized_changes[key]
                                    )
                                    for key, col in c_pairs
                                ]
                                + [item_id],
                            )
                self.connection.execute(
                    "INSERT INTO operator_action_audits(actor_id, action, scope, target_ids_json, outcome_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(actor_id),
                        f"library_batch_{action}",
                        "learning_item",
                        _json(ids),
                        _json(
                            {
                                "count": len(ids),
                                **(
                                    {
                                        "verification_status": normalized_changes[
                                            "verification_status"
                                        ]
                                    }
                                    if action == "verify_case"
                                    else {}
                                ),
                            }
                        ),
                        str(at),
                    ),
                )
            except Exception:
                self.connection.rollback()
                raise
        return ids
