from __future__ import annotations

import hashlib
import io
import json

import pytest

from library_repository import LibraryRepository
from library_service import LibraryService
from service import LawAssistantService
from storage import SQLiteStorage
from structured_ingestion import StructuredMaterialIngestionService
from structured_material import validate_structured_material


def _text_pdf_bytes(text: str = "Verified PDF source") -> bytes:
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=240, height=240)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _block(block_id: str, order: int, text: str, locator: str) -> dict[str, object]:
    return {
        "id": block_id,
        "order": order,
        "kind": "paragraph",
        "text": text,
        "locator": locator,
        "provenance": "source_text",
    }


def _payload(pdf_bytes: bytes) -> dict[str, object]:
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
    return {
        "schema_version": "1.0",
        "document": {
            "title": "结构化法考资料",
            "source_type": "exam_pdf",
            "original_filename": "verified.pdf",
            "original_file_sha256": pdf_hash,
            "preparation_method": "human_transcribed",
            "verification_status": "pending_review",
            "source_url": "https://example.test/verified.pdf",
        },
        "materials": [
            {
                "id": "material-1",
                "title": "案例材料一",
                "blocks": [_block("material-1-block-1", 1, "甲乙合同材料", "第1页")],
            }
        ],
        "questions": [
            {
                "id": "question-1",
                "title": "知识产权简答题",
                "source_number": "2022-一",
                "question_type": "short_answer",
                "subjects": ["intellectual_property"],
                "material_refs": ["material-1"],
                "stem_blocks": [
                    _block("question-1-stem-1", 1, "请说明商标权保护要件。", "第1页")
                ],
                "answer": {
                    "value": "应结合构成要件和证据分析。",
                    "provenance": "source_text",
                    "blocks": [
                        _block("question-1-answer-1", 1, "参考答案要点。", "第1页")
                    ],
                },
                "explanation_blocks": [
                    _block("question-1-explain-1", 1, "依据题库答案整理。", "第1页")
                ],
                "answer_requirements": [
                    {
                        "order": 1,
                        "text": "虚构测试要求：回答时说明理由。",
                        "locator": "PDF第1页",
                        "provenance": "source_text",
                    }
                ],
                "locators": ["第1页"],
                "verification_status": "pending_review",
                "subquestions": [
                    {
                        "id": "question-1-sub-1",
                        "source_number": "（1）",
                        "stem_blocks": [
                            _block(
                                "question-1-sub-1-stem", 1, "列举一个要件。", "第1页"
                            )
                        ],
                        "answer": None,
                        "answer_reason": "原始 PDF 未提供该小问的独立答案",
                        "locators": ["第1页"],
                        "answer_requirements": [
                            {
                                "order": 1,
                                "text": "虚构小问要求：简要作答。",
                                "locator": "PDF第1页",
                                "provenance": "source_text",
                            }
                        ],
                    }
                ],
            }
        ],
        "cases": [
            {
                "id": "case-1",
                "title": "合同纠纷案例",
                "source_number": "案例一",
                "subjects": ["civil_commercial"],
                "basic_facts_blocks": [
                    _block("case-1-facts-1", 1, "甲乙发生合同争议。", "第1页")
                ],
                "locators": ["第1页"],
                "verification_status": "pending_review",
                "authority": "测试来源",
            }
        ],
    }


async def _prepare_service(tmp_path):
    pdf_bytes = _text_pdf_bytes()
    payload = _payload(pdf_bytes)
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))
    ingestion = StructuredMaterialIngestionService(tmp_path, library)
    original_path = ingestion.import_dir / "verified.pdf"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(pdf_bytes)
    staged = await ingestion.stage_json_upload(
        "verified material.json", json.dumps(payload, ensure_ascii=False).encode()
    )
    prepared = await ingestion.prepare(
        "verified.pdf",
        staged["staged_path"],
        created_by="operator-1",
        session_origin="private:operator-1",
        original_filename="verified.pdf",
        structured_filename="verified material.json",
    )
    return storage, ingestion, prepared, payload


@pytest.mark.asyncio
async def test_structured_pdf_json_prepare_confirm_persists_rich_candidate_data(
    tmp_path,
):
    storage, ingestion, prepared, payload = await _prepare_service(tmp_path)
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM structured_imports"
        ).fetchone()[0]
        == 0
    )
    assert prepared.preview["counts"] == {
        "questions": 1,
        "cases": 1,
        "materials": 1,
        "subquestions": 1,
        "answer_requirements": 2,
        "answer_mappings_resolved": 0,
        "answer_mappings_unresolved": 1,
        "duplicate_warnings": 0,
        "contamination_warnings": 0,
        "processable": 2,
        "entry_errors": 0,
        "review_items": 1,
    }

    result = await ingestion.confirm(prepared)
    assert result["archived"] == 2
    assert result["failed"] == 0
    assert result["needs_review"] == 3
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM structured_imports"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM structured_materials"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM structured_subquestions"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM learning_review_items"
        ).fetchone()[0]
        == 1
    )
    review = ingestion.library_service.repository.list_review_items()[0]
    assert review.structured_import_id == 1
    assert (
        storage.connection.execute("SELECT COUNT(*) FROM structured_blocks").fetchone()[
            0
        ]
        >= 5
    )

    rows = storage.connection.execute(
        "SELECT id, identity, verification_status FROM learning_items ORDER BY id"
    ).fetchall()
    assert {(row[1], row[2]) for row in rows} == {
        ("real_question_candidate", "pending_review"),
        ("user_case", "pending_review"),
    }
    question_id = storage.connection.execute(
        "SELECT item_id FROM structured_item_bindings WHERE external_id = 'question-1'"
    ).fetchone()[0]
    bundle = ingestion.library_service.repository.get(question_id)
    assert bundle is not None
    assert bundle.structured["review_status"] == "pending_review"
    assert bundle.question.question_identity == "real_question_candidate"
    assert {source.source_kind for source in bundle.sources} == {
        "structured_original",
        "structured_json",
    }
    assert bundle.shared_materials[0]["blocks"][0]["text"] == "甲乙合同材料"
    assert bundle.stem_blocks[0]["text"] == "请说明商标权保护要件。"
    assert bundle.subquestions[0]["answer_reason"] == "原始 PDF 未提供该小问的独立答案"
    assert bundle.structured["answer_requirements"][0]["text"] == (
        "虚构测试要求：回答时说明理由。"
    )
    assert bundle.subquestions[0]["answer_requirements"][0]["text"] == (
        "虚构小问要求：简要作答。"
    )
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM structured_blocks WHERE section = 'answer_requirement'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM structured_blocks "
            "WHERE section = 'subquestion_answer_requirement'"
        ).fetchone()[0]
        == 1
    )
    assert bundle.explanation_blocks[0]["text"] == "依据题库答案整理。"
    assert (
        ingestion.library_service.repository.search(query="商标权保护", limit=10)[0].id
        == question_id
    )
    assert (
        ingestion.library_service.repository.search(query="虚构测试要求", limit=10)[
            0
        ].id
        == question_id
    )
    assert "虚构测试要求" not in bundle.question.stem
    assert payload["document"]["original_file_sha256"] == prepared.original_file_sha256
    storage.close()


@pytest.mark.asyncio
async def test_structured_import_is_hash_bound_and_duplicate_is_idempotent(tmp_path):
    storage, ingestion, prepared, payload = await _prepare_service(tmp_path)
    first = await ingestion.confirm(prepared)
    assert first["archived"] == 2

    duplicate_prepared = await ingestion.prepare(
        "verified.pdf",
        prepared.structured_path.relative_to(ingestion.import_dir).as_posix(),
        created_by="operator-1",
        session_origin="private:operator-1",
    )
    duplicate = await ingestion.confirm(duplicate_prepared)
    assert duplicate["duplicate"] is True
    assert duplicate["archived"] == 0
    assert (
        storage.connection.execute("SELECT COUNT(*) FROM learning_items").fetchone()[0]
        == 2
    )

    changed = dict(payload)
    changed["document"] = dict(payload["document"])
    changed["document"]["title"] = "被篡改后的标题"
    prepared.structured_path.write_text(
        json.dumps(changed, ensure_ascii=False), encoding="utf-8"
    )
    # The one-time confirmation path must reject modified staged JSON before storage.
    with pytest.raises(ValueError, match="已变化"):
        await ingestion.confirm(prepared)
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM structured_imports"
        ).fetchone()[0]
        == 1
    )
    storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case_only", [False, True], ids=["mixed", "case-only"])
async def test_duplicate_structured_import_rebuilds_cleared_cases_without_duplicate_questions(
    tmp_path, case_only
):
    storage, ingestion, prepared, payload = await _prepare_service(tmp_path)
    if case_only:
        payload["questions"] = []
        prepared.structured_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        prepared = await ingestion.prepare(
            "verified.pdf",
            prepared.structured_path.relative_to(ingestion.import_dir).as_posix(),
            created_by="operator-1",
            session_origin="private:operator-1",
        )

    first = await ingestion.confirm(prepared)
    assert first["archived"] == (1 if case_only else 2)
    before_question_ids = {
        int(row[0])
        for row in storage.connection.execute(
            "SELECT id FROM learning_items WHERE item_type='question'"
        ).fetchall()
    }
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM learning_items WHERE item_type='case'"
        ).fetchone()[0]
        == 1
    )

    clear = storage.prepare_data_clear("cases")
    storage.confirm_data_clear("cases", clear["snapshot"])
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM learning_items WHERE item_type='case'"
        ).fetchone()[0]
        == 0
    )

    duplicate_prepared = await ingestion.prepare(
        "verified.pdf",
        prepared.structured_path.relative_to(ingestion.import_dir).as_posix(),
        created_by="operator-1",
        session_origin="private:operator-1",
    )
    rebuilt = await ingestion.confirm(duplicate_prepared)

    assert rebuilt["duplicate"] is True
    assert rebuilt["archived"] == 1
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM learning_items WHERE item_type='case'"
        ).fetchone()[0]
        == 1
    )
    after_question_ids = {
        int(row[0])
        for row in storage.connection.execute(
            "SELECT id FROM learning_items WHERE item_type='question'"
        ).fetchall()
    }
    assert after_question_ids == before_question_ids
    assert (
        storage.connection.execute(
            "SELECT COUNT(*) FROM structured_imports"
        ).fetchone()[0]
        == 1
    )
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()

    reopened = SQLiteStorage(tmp_path / "runtime.sqlite3")
    assert (
        reopened.connection.execute(
            "SELECT COUNT(*) FROM learning_items WHERE item_type='case'"
        ).fetchone()[0]
        == 1
    )
    assert reopened.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    reopened.close()


def test_structured_validator_keeps_identity_as_candidate_only():
    pdf_bytes = _text_pdf_bytes()
    validation = validate_structured_material(_payload(pdf_bytes))
    assert not validation.fatal
    assert all(
        item["verification_status"] == "pending_review"
        for item in validation.valid_question_items
    )
    assert all(
        item.get("identity_granted") is False for item in validation.valid_case_items
    )


@pytest.mark.asyncio
async def test_persisted_structured_choice_keys_reveal_after_reopen_without_reimport(
    tmp_path,
):
    db_path = tmp_path / "persisted-choice.sqlite3"
    pdf_bytes = _text_pdf_bytes("Synthetic source PDF for choice answer regression")
    payload = _payload(pdf_bytes)
    question = payload["questions"][0]
    question["question_type"] = "single_choice"
    question["options"] = [
        {"key": "A", "text": "虚构选项甲"},
        {"key": "B", "text": "虚构选项乙"},
    ]
    question["answer"] = {"keys": ["B"], "provenance": "source_text"}
    question["subquestions"] = []

    storage = SQLiteStorage(db_path)
    library = LibraryService(LibraryRepository(storage.connection))
    ingestion = StructuredMaterialIngestionService(tmp_path, library)
    original_path = ingestion.import_dir / "choice-source.pdf"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(pdf_bytes)
    staged = await ingestion.stage_json_upload(
        "choice-material.json", json.dumps(payload, ensure_ascii=False).encode()
    )
    prepared = await ingestion.prepare(
        "choice-source.pdf",
        staged["staged_path"],
        created_by="synthetic-operator",
        session_origin="private:synthetic-operator",
        original_filename="choice-source.pdf",
        structured_filename="choice-material.json",
    )
    confirmed = await ingestion.confirm(prepared)
    assert confirmed["archived"] == 2

    item_id = storage.connection.execute(
        "SELECT item_id FROM structured_item_bindings WHERE external_id = 'question-1'"
    ).fetchone()[0]
    stored_answer = json.loads(
        storage.connection.execute(
            "SELECT answer_json FROM learning_questions WHERE item_id = ?",
            (item_id,),
        ).fetchone()[0]
    )
    assert stored_answer == {"keys": ["B"], "provenance": "source_text"}
    storage.close()

    reopened = SQLiteStorage(db_path)
    reopened_library = LibraryService(LibraryRepository(reopened.connection))
    service = LawAssistantService(reopened, library_service=reopened_library)

    safe_detail = await service.get_learning_item(item_id)
    assert safe_detail["success"] is True
    assert "answer" not in safe_detail["question"]
    assert "explanation" not in safe_detail["question"]
    assert "keys" not in json.dumps(safe_detail, ensure_ascii=False)
    assert (await service.search_learning_library(query="A、C"))["items"] == []

    scope = "aiocqhttp:FriendMessage:synthetic-operator"
    opened = await service.start_library_question_session(
        item_id,
        session_origin=scope,
        actor_id="synthetic-operator",
    )
    assert opened["success"] is True
    assert "来源资料参考答案" not in opened["text"]
    assert "原始资料未提供可核验参考答案" not in opened["text"]
    prompt = await service.question_session_action(
        "next", session_origin=scope, actor_id="synthetic-operator"
    )
    assert "虚构选项乙" in prompt["text"]
    assert "来源资料参考答案" not in prompt["text"]
    assert "原始资料未提供可核验参考答案" not in prompt["text"]

    revealed = await service.question_session_action(
        "answer", session_origin=scope, actor_id="synthetic-operator"
    )
    assert "第 1/1 页\nB" in revealed["text"]
    assert "原始资料未提供可核验参考答案" not in revealed["text"]
    reopened.close()
