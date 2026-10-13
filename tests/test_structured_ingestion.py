from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace

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


def _objective_group_payload(pdf_bytes: bytes) -> dict[str, object]:
    payload = _payload(pdf_bytes)
    payload["schema_version"] = "1.1"
    payload["questions"] = [
        {
            "id": f"objective-{source_number}",
            "title": f"合成客观题 {source_number}",
            "source_number": str(source_number),
            "question_type": "multiple_choice",
            "subjects": ["civil_commercial"],
            "material_refs": ["material-1"],
            "stem_blocks": [
                _block(
                    f"objective-{source_number}-stem",
                    1,
                    f"合成问题 {source_number}？",
                    "第1页",
                )
            ],
            "options": [
                {"key": "A", "text": "合成选项 A", "locator": "第1页"},
                {"key": "B", "text": "合成选项 B", "locator": "第1页"},
            ],
            "subquestions": [],
            "answer": {"keys": ["A"], "provenance": "source_text"},
            "answer_status": "provided",
            "answer_reason": "",
            "explanation_blocks": [
                _block(
                    f"objective-{source_number}-explanation",
                    1,
                    "合成解析。",
                    "第1页",
                )
            ],
            "locators": ["第1页"],
            "verification_status": "pending_review",
        }
        for source_number in (206, 207, 208)
    ]
    payload["cases"] = []
    payload["objective_question_groups"] = [
        {
            "id": "civil-objective-206-208",
            "material_refs": ["material-1"],
            "question_refs": ["objective-206", "objective-207", "objective-208"],
            "publish_mode": "all_at_once",
        }
    ]
    return payload


async def _prepare_service(tmp_path, payload_override=None):
    pdf_bytes = _text_pdf_bytes()
    payload = payload_override or _payload(pdf_bytes)
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
async def test_confirmed_group_import_resolves_any_member_to_ordered_delivery_unit(
    tmp_path,
):
    pdf_bytes = _text_pdf_bytes()
    payload = _objective_group_payload(pdf_bytes)
    storage, ingestion, prepared, _ = await _prepare_service(
        tmp_path, payload_override=payload
    )
    confirmed = await ingestion.confirm(prepared)
    repository = LibraryRepository(storage.connection)
    item_ids = {item["external_id"]: item["item_id"] for item in confirmed["items"]}

    unit = repository.get_structured_delivery_unit(item_ids["objective-207"])
    matches = repository.list_structured_source_number_matches("第２０７题")
    stored_import = storage.connection.execute(
        "SELECT payload_json FROM structured_imports WHERE id = ?",
        (confirmed["import_id"],),
    ).fetchone()

    assert unit is not None
    assert unit["group_id"] == "civil-objective-206-208"
    assert unit["import_id"] == confirmed["import_id"]
    assert [member["external_id"] for member in unit["members"]] == [
        "objective-206",
        "objective-207",
        "objective-208",
    ]
    assert [member["source_number"] for member in unit["members"]] == [
        "206",
        "207",
        "208",
    ]
    assert unit["shared_materials"][0]["id"] == "material-1"
    assert len(matches) == 1
    assert matches[0]["group_id"] == unit["group_id"]
    assert (
        json.loads(stored_import["payload_json"])["objective_question_groups"]
        == (payload["objective_question_groups"])
    )
    assert all(
        member["bundle"].item.verification_status == "pending_review"
        for member in unit["members"]
    )
    storage.close()


@pytest.mark.asyncio
async def test_source_number_starts_the_entire_pending_group_without_answer_leak(
    tmp_path,
):
    pdf_bytes = _text_pdf_bytes()
    storage, ingestion, prepared, _ = await _prepare_service(
        tmp_path, payload_override=_objective_group_payload(pdf_bytes)
    )
    await ingestion.confirm(prepared)
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        config=SimpleNamespace(question_message_max_chars=600),
    )

    opened = await service.start_question_by_number(
        "第２０７题",
        source_selector=None,
        session_origin="aiocqhttp:FriendMessage:operator-1",
        actor_id="operator-1",
    )

    assert opened["success"] is True
    assert "待人工核验" in opened["identity"]
    assert "来源资料参考答案" not in opened["text"]
    assert "合成解析" not in opened["text"]
    session = service.question_sessions.get_active("aiocqhttp:FriendMessage:operator-1")
    assert session is not None
    assert session["source_kind"] == "structured_objective_group"
    assert session["source_item_key"].endswith(":civil-objective-206-208")
    assert [prompt["source_number"] for prompt in session["snapshot"]["prompts"]] == [
        "206",
        "207",
        "208",
    ]
    revealed = await service.question_session_action(
        "answer",
        question_number="207",
        session_origin="aiocqhttp:FriendMessage:operator-1",
        actor_id="operator-1",
    )
    assert revealed["success"] is True
    assert "来源资料参考答案" in revealed["text"]
    assert (
        service.question_sessions.get_active("aiocqhttp:FriendMessage:operator-1")[
            "current_prompt_index"
        ]
        == 1
    )
    storage.close()


@pytest.mark.asyncio
async def test_source_number_collision_requires_explicit_import_selector(tmp_path):
    pdf_bytes = _text_pdf_bytes()
    storage, ingestion, prepared, _ = await _prepare_service(
        tmp_path, payload_override=_objective_group_payload(pdf_bytes)
    )
    await ingestion.confirm(prepared)
    repository = ingestion.library_service.repository
    unit = repository.list_structured_source_number_matches("207")[0]
    second_unit = {**unit, "import_id": unit["import_id"] + 100}
    repository.list_structured_source_number_matches = lambda _number: [
        unit,
        second_unit,
    ]
    service = LawAssistantService(storage, library_service=ingestion.library_service)

    ambiguous = await service.start_question_by_number(
        "207",
        source_selector=None,
        session_origin="private:operator-1",
        actor_id="operator-1",
    )
    selected = await service.start_question_by_number(
        "207",
        source_selector=str(unit["import_id"]),
        session_origin="private:operator-1",
        actor_id="operator-1",
    )

    assert ambiguous["success"] is False
    assert ambiguous["error"] == "ambiguous_source_number"
    assert str(unit["import_id"]) in ambiguous["reason"]
    assert selected["success"] is True
    storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filters", "expected_fragment"),
    [
        ({"subject": "刑法"}, "方向"),
        ({"question_type": "single_choice"}, "题型"),
        ({"origin": "mock"}, "来源"),
    ],
)
async def test_source_number_never_silently_ignores_explicit_filters(
    tmp_path, filters, expected_fragment
):
    pdf_bytes = _text_pdf_bytes()
    storage, ingestion, prepared, _ = await _prepare_service(
        tmp_path, payload_override=_objective_group_payload(pdf_bytes)
    )
    await ingestion.confirm(prepared)
    service = LawAssistantService(storage, library_service=ingestion.library_service)

    result = await service.start_question_by_number(
        "207",
        source_selector=None,
        session_origin="private:operator-1",
        actor_id="operator-1",
        **filters,
    )

    assert result["success"] is False
    assert result["error"] == "source_number_filter_mismatch"
    assert expected_fragment in result["reason"]
    assert service.question_sessions.get_active("private:operator-1") is None
    storage.close()


@pytest.mark.asyncio
async def test_manual_group_publish_previews_and_sends_locked_answer_free_pages(
    tmp_path,
):
    pdf_bytes = _text_pdf_bytes()
    payload = _objective_group_payload(pdf_bytes)
    long_material = "X" * 1750
    payload["materials"][0]["blocks"][0]["text"] = long_material
    storage, ingestion, prepared, _ = await _prepare_service(
        tmp_path, payload_override=payload
    )
    await ingestion.confirm(prepared)

    class PagePublisher:
        def __init__(self):
            self.calls = []

        async def publish_text(self, destination, text):
            self.calls.append((destination, text))
            return True

    publisher = PagePublisher()
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        publisher=publisher,
        config=SimpleNamespace(question_message_max_chars=1700),
    )
    target = await service.bind_target("aiocqhttp:group:group-pages", "组题群")

    preview = await service.prepare_publish_question(
        source_number="207",
        target_selectors=["组题群"],
        session_origin="private:operator-1",
        actor_id="operator-1",
    )

    assert preview["ready"] is True
    assert preview["targets"] == [target]
    assert len(preview["preview_pages"]) > 3
    assert all(len(page) <= 1000 for page in preview["preview_pages"])
    assert sum(page.count("X") for page in preview["preview_pages"]) == 1750
    all_preview = "\n".join(preview["preview_pages"])
    assert all(f"合成问题 {number}？" in all_preview for number in (206, 207, 208))
    assert "合成解析" not in all_preview
    assert "参考答案" not in all_preview
    assert service.question_sessions.get_active(target["unified_msg_origin"]) is None

    import_row = storage.connection.execute(
        "SELECT id, payload_json FROM structured_imports"
    ).fetchone()
    changed_payload = json.loads(import_row["payload_json"])
    changed_payload["questions"][0]["stem_blocks"][0]["text"] = "后来修改的题干"
    storage.connection.execute(
        "UPDATE structured_imports SET payload_json = ? WHERE id = ?",
        (json.dumps(changed_payload, ensure_ascii=False), import_row["id"]),
    )
    storage.connection.commit()

    sent = await service.confirm_publish(preview["token"], actor_id="operator-1")

    assert sent == {"success": True, "published_count": 1, "failed_count": 0}
    assert publisher.calls == [
        (target["unified_msg_origin"], page) for page in preview["preview_pages"]
    ]
    session = service.question_sessions.get_active(target["unified_msg_origin"])
    assert session is not None
    assert session["snapshot"]["objective_group_pages_sent"] is True
    calls_before_navigation = len(publisher.calls)
    continued = await service.question_session_action(
        "next",
        session_origin=target["unified_msg_origin"],
        actor_id="operator-1",
    )
    assert continued["success"] is True
    assert "全部题面已在发布时完整发送" in continued["text"]
    selected = await service.question_session_action(
        "next-question",
        session_origin=target["unified_msg_origin"],
        actor_id="operator-1",
    )
    assert selected["success"] is True
    assert "207" in selected["text"]
    assert "题面已随题组发布" in selected["text"]
    assert len(publisher.calls) == calls_before_navigation
    assert (await service.confirm_publish(preview["token"], actor_id="operator-1"))[
        "success"
    ] is False
    assert len(publisher.calls) == len(preview["preview_pages"])
    storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_status", ["failed", "unknown"])
async def test_partial_group_publish_stops_failed_target_but_continues_other_target(
    tmp_path, failure_status
):
    pdf_bytes = _text_pdf_bytes()
    storage, ingestion, prepared, _ = await _prepare_service(
        tmp_path, payload_override=_objective_group_payload(pdf_bytes)
    )
    await ingestion.confirm(prepared)

    class IsolatedPublisher:
        def __init__(self):
            self.calls = []
            self.counts = {}

        async def publish_text(self, destination, text):
            self.calls.append((destination, text))
            self.counts[destination] = self.counts.get(destination, 0) + 1
            if destination.endswith(":first") and self.counts[destination] == 2:
                return SimpleNamespace(status=failure_status, error="fixture")
            return True

    publisher = IsolatedPublisher()
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        publisher=publisher,
        config=SimpleNamespace(question_message_max_chars=300),
    )
    first = await service.bind_target("aiocqhttp:group:first", "失败目标")
    second = await service.bind_target("aiocqhttp:group:second", "成功目标")
    preview = await service.prepare_publish_question(
        source_number="207",
        target_selectors=["失败目标", "成功目标"],
        actor_id="operator-1",
    )
    assert preview["ready"] is True
    page_count = len(preview["preview_pages"])
    assert page_count > 2

    result = await service.confirm_publish(preview["token"], actor_id="operator-1")

    assert result == {"success": True, "published_count": 1, "failed_count": 1}
    assert publisher.counts[first["unified_msg_origin"]] == 2
    assert publisher.counts[second["unified_msg_origin"]] == page_count
    assert service.question_sessions.get_active(first["unified_msg_origin"]) is None
    assert (
        service.question_sessions.get_active(second["unified_msg_origin"]) is not None
    )
    assert (await service.confirm_publish(preview["token"], actor_id="operator-1"))[
        "success"
    ] is False
    assert len(publisher.calls) == page_count + 2
    storage.close()


@pytest.mark.asyncio
async def test_source_number_collision_remains_import_scoped(tmp_path):
    pdf_bytes = _text_pdf_bytes()
    payload = _objective_group_payload(pdf_bytes)
    storage, ingestion, prepared, _ = await _prepare_service(
        tmp_path, payload_override=payload
    )
    first = await ingestion.confirm(prepared)

    second_payload = _objective_group_payload(pdf_bytes)
    second_payload["document"]["paper"] = "第二个合成导入"
    for question in second_payload["questions"]:
        question["id"] = f"second-{question['id']}"
        question["title"] = f"第二来源 {question['title']}"
        question["stem_blocks"][0]["id"] = f"second-{question['stem_blocks'][0]['id']}"
        question["explanation_blocks"][0]["id"] = (
            f"second-{question['explanation_blocks'][0]['id']}"
        )
    second_payload["objective_question_groups"][0]["id"] = "second-group-206-208"
    second_payload["objective_question_groups"][0]["question_refs"] = [
        question["id"] for question in second_payload["questions"]
    ]
    staged = await ingestion.stage_json_upload(
        "second synthetic.json",
        json.dumps(second_payload, ensure_ascii=False).encode(),
    )
    second_prepared = await ingestion.prepare(
        "verified.pdf",
        staged["staged_path"],
        created_by="operator-1",
        session_origin="private:operator-1",
        original_filename="verified.pdf",
        structured_filename="second synthetic.json",
    )
    second = await ingestion.confirm(second_prepared)
    matches = LibraryRepository(
        storage.connection
    ).list_structured_source_number_matches("207")

    assert first["import_id"] != second["import_id"]
    assert len(matches) == 2
    assert {unit["import_id"] for unit in matches} == {
        first["import_id"],
        second["import_id"],
    }
    assert {unit["group_id"] for unit in matches} == {
        "civil-objective-206-208",
        "second-group-206-208",
    }
    storage.close()


@pytest.mark.asyncio
async def test_material_referenced_single_question_remains_a_singleton_unit(tmp_path):
    pdf_bytes = _text_pdf_bytes()
    payload = _objective_group_payload(pdf_bytes)
    payload["objective_question_groups"] = []
    payload["questions"] = [payload["questions"][0]]
    storage, ingestion, prepared, _ = await _prepare_service(
        tmp_path, payload_override=payload
    )
    confirmed = await ingestion.confirm(prepared)
    item_id = confirmed["items"][0]["item_id"]
    unit = LibraryRepository(storage.connection).get_structured_delivery_unit(item_id)

    assert unit is not None
    assert unit["group_id"] is None
    assert unit["source_item_key"] == "objective-206"
    assert [member["external_id"] for member in unit["members"]] == ["objective-206"]
    assert unit["shared_materials"][0]["id"] == "material-1"
    storage.close()


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
        "objective_question_groups": 0,
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
