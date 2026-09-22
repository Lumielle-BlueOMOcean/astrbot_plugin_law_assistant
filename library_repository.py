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
    from .content import subject_filter
except ImportError:
    from content import subject_filter


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
            if existing is not None:
                return {
                    "duplicate": True,
                    "import_id": int(existing["id"]),
                    "original_source_id": int(existing["original_source_id"]),
                    "structured_source_id": int(existing["structured_source_id"]),
                    "archived": 0,
                    "failed": 0,
                    "needs_review": 0,
                    "items": [],
                }

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
            material_ids: dict[str, int] = {}
            material_count = 0
            for material in materials:
                external_id = str(material.get("id") or "").strip()
                if not external_id:
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
            for review in review_records:
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
            "duplicate": False,
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
        limit: int | None = 10,
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
            subjects = subject_filter(subject)
            if subjects is None:
                raise ValueError(f"不支持的资料方向：{subject}")
            subject_clauses = []
            for candidate in sorted(subjects):
                subject_clauses.append("i.subjects_json LIKE ?")
                params.append(f'%"{candidate}"%')
            clauses.append(f"({' OR '.join(subject_clauses)})")
        if query:
            term = f"%{query}%"
            clauses.append(
                "(i.title LIKE ? OR i.source_summary LIKE ? OR "
                "(COALESCE(s.source_kind, '') NOT IN "
                "('structured_original', 'structured_json') AND s.raw_text LIKE ?) "
                "OR c.case_summary LIKE ? OR c.practice_notes_json LIKE ? "
                "OR q.stem LIKE ? OR q.explanation LIKE ? OR i.metadata_json LIKE ? "
                "OR EXISTS (SELECT 1 FROM structured_blocks AS sb "
                "WHERE sb.item_id = i.id AND sb.text LIKE ?) "
                "OR EXISTS (SELECT 1 FROM structured_material_relations AS smr "
                "JOIN structured_item_bindings AS sib ON sib.id = smr.binding_id "
                "JOIN structured_blocks AS smb ON smb.material_id = smr.material_id "
                "WHERE sib.item_id = i.id AND smb.text LIKE ?))"
            )
            params.extend([term] * 10)
        limit_clause = ""
        if limit is not None:
            safe_limit = max(1, min(int(limit), 5000))
            params.append(safe_limit)
            limit_clause = " LIMIT ?"
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
