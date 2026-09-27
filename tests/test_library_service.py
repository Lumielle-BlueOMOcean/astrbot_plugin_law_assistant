from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from library_models import LibrarySource
from library_repository import LibraryRepository
from library_service import LibraryService
from storage import SQLiteStorage


def _service(tmp_path) -> tuple[SQLiteStorage, LibraryService]:
    storage = SQLiteStorage(tmp_path / "library.sqlite3")
    return storage, LibraryService(LibraryRepository(storage.connection))


@pytest.mark.asyncio
async def test_archive_case_preserves_raw_text_and_structured_learning_fields(tmp_path):
    storage, service = _service(tmp_path)
    raw_text = "原始案件正文：某公司未经许可传播摄影作品。"

    result = await service.archive_learning_material(
        raw_text=raw_text,
        material_type="case",
        title="摄影作品传播案",
        subjects="知产",
        structured_json=json.dumps(
            {
                "case_summary": "围绕著作权许可的侵权争议",
                "issues": ["是否构成侵权"],
                "reasoning": "应结合许可范围判断",
                "practice_notes": ["注意传播行为的证据"],
            },
            ensure_ascii=False,
        ),
        created_by="42",
        session_origin="aiocqhttp:private:42",
    )

    assert result["success"] is True
    assert result["identity"] == "user_case"
    loaded = await service.get_learning_item(result["item_id"])
    assert loaded["source"]["raw_text"] == raw_text
    assert loaded["item"]["subjects"] == ["intellectual_property"]
    assert loaded["case"]["case_summary"] == "围绕著作权许可的侵权争议"
    assert loaded["case"]["practice_notes"] == ["注意传播行为的证据"]
    storage.close()


@pytest.mark.asyncio
async def test_archive_cannot_escalate_verification_identity(tmp_path):
    storage, service = _service(tmp_path)

    result = await service.archive_learning_material(
        raw_text="疑似来自某考试的题目正文",
        material_type="real_question_candidate",
        structured_json=json.dumps(
            {
                "identity": "verified_real_question",
                "verification_status": "official",
                "question_type": "多选",
                "stem": "下列说法正确的是？",
                "options": ["A", "B"],
                "answer": ["A"],
            }
        ),
        created_by="42",
        session_origin="aiocqhttp:private:42",
    )

    assert result["success"] is True
    assert result["identity"] == "real_question_candidate"
    assert result["verification_status"] == "unverified"
    loaded = await service.get_learning_item(result["item_id"])
    assert loaded["item"]["identity"] == "real_question_candidate"
    assert loaded["item"]["verification_status"] == "unverified"
    storage.close()


@pytest.mark.asyncio
async def test_mock_question_external_detail_is_safe_and_session_view_is_complete(
    tmp_path,
):
    storage, service = _service(tmp_path)

    archived = await service.archive_learning_material(
        raw_text="知识产权练习题原始资料",
        material_type="mock_question",
        title="著作权多选模拟题",
        subjects="知识产权",
        structured_json=json.dumps(
            {
                "question_type": "multiple_choice",
                "stem": "未经许可传播摄影作品可能承担什么责任？",
                "options": ["A. 民事责任", "B. 一定没有责任"],
                "answer": ["A"],
                "explanation": "根据题干事实判断。",
                "answer_source": "AI generated",
            },
            ensure_ascii=False,
        ),
        created_by="42",
        session_origin="aiocqhttp:private:42",
    )

    search = await service.search_learning_library(query="摄影作品")
    assert search["success"] is True
    assert search["items"][0]["identity"] == "mock_question"
    detail = await service.get_learning_item(archived["item_id"])
    assert detail["question"]["stem"].startswith("未经许可")
    assert detail["question"]["options"] == ["A. 民事责任", "B. 一定没有责任"]
    assert "answer" not in detail["question"]
    assert "explanation" not in detail["question"]
    internal = await service.get_learning_item_for_session(archived["item_id"])
    assert internal["question"]["answer"] == ["A"]
    assert internal["question"]["explanation"] == "根据题干事实判断。"
    storage.close()


@pytest.mark.asyncio
async def test_question_search_detail_and_update_do_not_expose_hidden_evidence(
    tmp_path,
):
    storage, service = _service(tmp_path)
    archived = await service.archive_learning_material(
        raw_text=("SECRET_RAW_SOURCE contains SECRET_ANSWER and SECRET_EXPLANATION"),
        material_type="mock_question",
        title="安全边界合成题",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "question_type": "single_choice",
                "stem": "SECRET_STEM 可公开展示的题干",
                "options": ["A. 选项甲", "B. 选项乙"],
                "answer": "SECRET_ANSWER",
                "explanation": "SECRET_EXPLANATION",
                "questions": [{"answer": "SECRET_NESTED_METADATA"}],
            },
            ensure_ascii=False,
        ),
        created_by="42",
        session_origin="private:42",
    )

    search = await service.search_learning_library(query="SECRET_STEM")
    assert search["count"] == 1
    assert "SECRET_STEM" in search["items"][0]["summary"]
    bundle = service.repository.get(archived["item_id"])
    assert bundle.item.source_summary == "SECRET_STEM 可公开展示的题干"
    storage.connection.execute(
        "UPDATE learning_items SET source_summary = ? WHERE id = ?",
        ("SECRET_LEGACY_SUMMARY", archived["item_id"]),
    )
    storage.connection.commit()
    search_payload = json.dumps(search, ensure_ascii=False)
    detail = await service.get_learning_item(archived["item_id"])
    detail_payload = json.dumps(detail, ensure_ascii=False)
    for secret in (
        "SECRET_ANSWER",
        "SECRET_EXPLANATION",
        "SECRET_RAW_SOURCE",
        "SECRET_NESTED_METADATA",
        "SECRET_LEGACY_SUMMARY",
    ):
        assert secret not in search_payload
        assert secret not in detail_payload
        assert (await service.search_learning_library(query=secret))["items"] == []

    assert "answer" not in detail["question"]
    assert "explanation" not in detail["question"]
    assert "metadata" not in detail["item"]
    assert "structured" not in detail
    assert "raw_text" not in detail["source"]

    updated = await service.update_learning_item(
        archived["item_id"], {"explanation": "SECRET_EXPLANATION_2"}
    )
    assert updated["success"] is True
    assert "SECRET_EXPLANATION_2" not in json.dumps(updated, ensure_ascii=False)
    storage.close()


@pytest.mark.asyncio
async def test_structured_question_blocks_are_excluded_from_safe_reads_and_search(
    tmp_path,
):
    storage, service = _service(tmp_path)
    archived = await service.archive_learning_material(
        raw_text="结构化题目来源原文",
        material_type="real_question_candidate",
        title="结构化合成题",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "question_type": "short_answer",
                "stem": "结构化公开题干",
                "answer": {"value": "SOURCE_SECRET_ANSWER"},
                "explanation": "SOURCE_SECRET_EXPLANATION",
            },
            ensure_ascii=False,
        ),
        created_by="42",
        session_origin="private:42",
    )
    connection = storage.connection
    now = datetime.now(timezone.utc).isoformat()
    source_id = archived["source_id"]
    cursor = connection.execute(
        """
        INSERT INTO structured_imports(
            original_source_id, structured_source_id, schema_version,
            original_file_sha256, structured_json_sha256,
            structured_payload_sha256, preparation_method, payload_json,
            created_by, created_at
        ) VALUES (?, ?, '1.0', 'pdf-hash', 'json-hash', 'payload-hash',
                  'external_model_assisted', '{}', '42', ?)
        """,
        (source_id, source_id, now),
    )
    import_id = int(cursor.lastrowid)
    cursor = connection.execute(
        """
        INSERT INTO structured_item_bindings(
            import_id, item_id, external_id, source_number, item_kind,
            structure_version, review_status, payload_json, metadata_json
        ) VALUES (?, ?, 'question-safe', '题一', 'question', '1.0',
                  'pending_review', ?, '{}')
        """,
        (
            import_id,
            archived["item_id"],
            json.dumps(
                {
                    "answer": {"value": "PAYLOAD_SECRET_ANSWER"},
                    "explanation_blocks": [{"text": "PAYLOAD_SECRET_EXPLANATION"}],
                },
            ),
        ),
    )
    for index, (section, text) in enumerate(
        (
            ("stem", "STRUCTURED_PUBLIC_STEM"),
            ("answer", "BLOCK_SECRET_ANSWER"),
            ("explanation", "BLOCK_SECRET_EXPLANATION"),
        ),
        1,
    ):
        connection.execute(
            """
            INSERT INTO structured_blocks(
                import_id, item_id, material_id, subquestion_id, section,
                external_id, order_index, kind, text, locator, provenance,
                metadata_json
            ) VALUES (?, ?, NULL, NULL, ?, ?, ?, 'paragraph', ?, 'PDF第1页',
                      'source_text', '{}')
            """,
            (import_id, archived["item_id"], section, f"block-{index}", index, text),
        )
    connection.commit()

    detail = await service.get_learning_item(archived["item_id"])
    safe_payload = json.dumps(detail, ensure_ascii=False)
    for secret in (
        "PAYLOAD_SECRET_ANSWER",
        "PAYLOAD_SECRET_EXPLANATION",
        "BLOCK_SECRET_ANSWER",
        "BLOCK_SECRET_EXPLANATION",
    ):
        assert secret not in safe_payload
        assert (await service.search_learning_library(query=secret))["items"] == []
    assert "STRUCTURED_PUBLIC_STEM" not in safe_payload
    search = await service.search_learning_library(query="STRUCTURED_PUBLIC_STEM")
    assert search["count"] == 1
    assert "STRUCTURED_PUBLIC_STEM" in search["items"][0]["summary"]
    storage.close()


@pytest.mark.asyncio
async def test_question_search_separates_real_candidates_and_mock_questions(tmp_path):
    storage, service = _service(tmp_path)

    real = await service.archive_learning_material(
        raw_text="待核验真题原文",
        material_type="real_question_candidate",
        title="待核验题目",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "question_type": "单选",
                "stem": "待核验题干",
                "options": ["A", "B"],
            },
            ensure_ascii=False,
        ),
        created_by="42",
        session_origin="private:42",
    )
    mock = await service.archive_learning_material(
        raw_text="原创模拟题原文",
        material_type="mock_question",
        title="模拟题目",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "question_type": "单选",
                "stem": "模拟题干",
                "options": ["A", "B"],
            },
            ensure_ascii=False,
        ),
        created_by="42",
        session_origin="private:42",
    )

    all_questions = await service.search_learning_library(material_type="question")
    real_only = await service.search_learning_library(
        material_type="real_question_candidate"
    )
    mock_only = await service.search_learning_library(material_type="mock_question")

    assert {item["id"] for item in all_questions["items"]} == {
        real["item_id"],
        mock["item_id"],
    }
    assert [item["id"] for item in real_only["items"]] == [real["item_id"]]
    assert [item["id"] for item in mock_only["items"]] == [mock["item_id"]]
    storage.close()


@pytest.mark.asyncio
async def test_duplicate_item_keeps_new_source_url_and_deduplicates_exact_source(
    tmp_path,
):
    storage, service = _service(tmp_path)
    raw_text = "官方案例原文，先收藏时尚未补充链接"
    structured = json.dumps({"case_summary": "同一案例摘要"}, ensure_ascii=False)

    first = await service.archive_learning_material(
        raw_text=raw_text,
        material_type="case",
        title="官方案例",
        subjects="知识产权",
        structured_json=structured,
        created_by="42",
        session_origin="private:42",
    )
    second = await service.archive_learning_material(
        raw_text=raw_text,
        material_type="case",
        title="官方案例",
        subjects="知识产权",
        source_url="https://example.test/official-case",
        structured_json=structured,
        created_by="42",
        session_origin="private:42",
    )
    third = await service.archive_learning_material(
        raw_text=raw_text,
        material_type="case",
        title="官方案例",
        subjects="知识产权",
        source_url="https://example.test/official-case",
        structured_json=structured,
        created_by="42",
        session_origin="private:42",
    )

    assert second["item_id"] == first["item_id"]
    assert second["duplicate"] is True
    assert third["source_id"] == second["source_id"]
    detail = await service.get_learning_item(first["item_id"])
    assert {source["source_url"] for source in detail["sources"]} == {
        "",
        "https://example.test/official-case",
    }
    assert len(detail["sources"]) == 2
    storage.close()


@pytest.mark.asyncio
async def test_same_source_does_not_overwrite_distinct_items(tmp_path):
    storage, service = _service(tmp_path)
    raw_text = "同一份原始案例正文"

    first = await service.archive_learning_material(
        raw_text=raw_text,
        material_type="case",
        title="第一种整理",
        subjects="民商法",
        structured_json='{"case_summary":"第一份摘要"}',
        created_by="42",
        session_origin="private:42",
    )
    second = await service.archive_learning_material(
        raw_text=raw_text,
        material_type="case",
        title="第二种整理",
        subjects="知识产权",
        structured_json='{"case_summary":"第二份摘要"}',
        created_by="42",
        session_origin="private:42",
    )

    assert first["source_id"] == second["source_id"]
    assert first["item_id"] != second["item_id"]
    first_loaded = await service.get_learning_item(first["item_id"])
    second_loaded = await service.get_learning_item(second["item_id"])
    assert first_loaded["case"]["case_summary"] == "第一份摘要"
    assert second_loaded["case"]["case_summary"] == "第二份摘要"
    assert first_loaded["item"]["subjects"] == ["civil_commercial"]
    assert second_loaded["item"]["subjects"] == ["intellectual_property"]

    updated = await service.update_learning_item(
        first["item_id"],
        {"title": "第一条人工修订", "subjects": "经济法", "note": "人工备注"},
    )
    assert updated["success"] is True
    assert (await service.get_learning_item(second["item_id"]))["item"]["title"] == (
        "第二种整理"
    )
    storage.close()


@pytest.mark.asyncio
async def test_invalid_archive_input_returns_stable_errors(tmp_path):
    storage, service = _service(tmp_path)

    invalid_type = await service.archive_learning_material(
        raw_text="正文",
        material_type="official_case",
        created_by="42",
        session_origin="private:42",
    )
    invalid_json = await service.archive_learning_material(
        raw_text="正文",
        material_type="case",
        structured_json="not json",
        created_by="42",
        session_origin="private:42",
    )
    invalid_question = await service.archive_learning_material(
        raw_text="题目正文",
        material_type="mock_question",
        structured_json='{"question_type":"多选"}',
        created_by="42",
        session_origin="private:42",
    )

    assert invalid_type == {
        "success": False,
        "error": "invalid_material_type",
        "message": "不支持的资料类型：official_case",
    }
    assert invalid_json["error"] == "invalid_structured_content"
    assert invalid_question["error"] == "invalid_structured_content"
    storage.close()


@pytest.mark.asyncio
async def test_search_fields_limit_no_result_and_protected_update(tmp_path):
    storage, service = _service(tmp_path)
    archived = await service.archive_learning_material(
        raw_text="原文包含短视频和著作权关键词",
        material_type="case",
        title="人工标题",
        subjects="知识产权",
        structured_json='{"case_summary":"摘要中的争议焦点"}',
        note="人工备注",
        created_by="42",
        session_origin="private:42",
    )

    assert (await service.search_learning_library(query="争议焦点"))["count"] == 1
    assert (await service.search_learning_library(query="人工备注"))["count"] == 1
    assert (await service.search_learning_library(subject="知产"))["count"] == 1
    assert (await service.search_learning_library(query="不存在"))["items"] == []
    assert (await service.search_learning_library(limit=0))["limit"] == 1
    protected = await service.update_learning_item(
        archived["item_id"],
        {"identity": "verified_real_question"},
    )
    assert protected["error"] == "invalid_update_field"
    storage.close()


@pytest.mark.asyncio
async def test_note_body_is_preserved_as_structured_learning_content(tmp_path):
    storage, service = _service(tmp_path)
    archived = await service.archive_learning_material(
        raw_text="原始笔记证据",
        material_type="note",
        structured_json=json.dumps({"body": "可复用的学习笔记"}),
        created_by="42",
        session_origin="private:42",
    )

    detail = await service.get_learning_item(archived["item_id"])
    assert detail["item"]["metadata"]["body"] == "可复用的学习笔记"
    storage.close()


@pytest.mark.asyncio
async def test_update_rejects_detail_fields_for_wrong_item_type(tmp_path):
    storage, service = _service(tmp_path)
    archived = await service.archive_learning_material(
        raw_text="普通学习笔记",
        material_type="note",
        created_by="42",
        session_origin="private:42",
    )

    result = await service.update_learning_item(
        archived["item_id"], {"practice_notes": "不应静默丢弃"}
    )
    assert result["error"] == "invalid_update"
    storage.close()


def _batch_source() -> LibrarySource:
    return LibrarySource(
        source_kind="file",
        title="测试试卷.docx",
        raw_text="第1题\n题干一\n第2题\n题干二",
        source_url="",
        content_hash="document-text-hash",
        created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        created_by="42",
        session_origin="private:42",
        original_filename="测试试卷.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        storage_path="assets/document-hash.docx",
        metadata={
            "file_hash": "document-file-hash",
            "extracted_text_hash": "document-text-hash",
        },
    )


@pytest.mark.asyncio
async def test_batch_archive_preserves_locators_and_is_idempotent(tmp_path):
    storage, service = _service(tmp_path)
    source = _batch_source()
    candidates = [
        {
            "material_type": "real_question_candidate",
            "title": "第一题",
            "subjects": "刑法",
            "locator": "第1页/第1题",
            "structured": {
                "question_type": "单选",
                "stem": "题干一",
                "options": ["A", "B"],
            },
        },
        {
            "material_type": "real_question_candidate",
            "title": "无法归档题目",
            "locator": "第1页/第2题",
            "structured": {"question_type": "不支持的题型", "stem": "题干二"},
        },
    ]

    first = await service.archive_material_batch(source=source, candidates=candidates)
    second = await service.archive_material_batch(
        source=source, candidates=candidates[:1]
    )

    assert first["success"] is True
    assert first["archived"] == 1
    assert first["failed"] == 1
    assert first["items"][0]["locator"] == "第1页/第1题"
    assert second["duplicate"] == 1
    assert second["archived"] == 0
    detail = await service.get_learning_item(first["items"][0]["item_id"])
    assert detail["source_links"][0]["locator"] == "第1页/第1题"
    storage.close()


@pytest.mark.asyncio
async def test_review_candidates_persist_across_restart_and_are_idempotent(tmp_path):
    database = tmp_path / "library.sqlite3"
    source = _batch_source()
    candidates = [
        {
            "material_type": "real_question_candidate",
            "title": "可归档题目",
            "locator": "第1页/第1题",
            "structured": {
                "question_type": "单选",
                "stem": "题干一",
                "options": ["A", "B"],
            },
        },
        {
            "status": "needs_review",
            "review_reason": "答案题号无法确认",
            "material_type": "real_question_candidate",
            "locator": "第1页/答案区",
            "raw_text": "答案区原文 1 A 2 ?",
            "structured": {"question_type": "single_choice"},
        },
    ]

    storage = SQLiteStorage(database)
    service = LibraryService(LibraryRepository(storage.connection))
    first = await service.archive_material_batch(source=source, candidates=candidates)
    assert first["archived"] == 1
    assert first["needs_review"] == 1
    review_id = first["review_items"][0]["id"]
    storage.close()

    reopened = SQLiteStorage(database)
    reopened_service = LibraryService(LibraryRepository(reopened.connection))
    listed = await reopened_service.list_review_items(source_id=first["source_id"])
    assert listed["count"] == 1
    assert listed["items"][0]["id"] == review_id
    assert "raw_fragment" not in listed["items"][0]
    persisted_review = reopened_service.repository.get_review_item(review_id)
    assert persisted_review.raw_fragment == "答案区原文 1 A 2 ?"
    assert listed["items"][0]["locator"] == "第1页/答案区"
    assert listed["items"][0]["review_reason"] == "答案题号无法确认"

    second = await reopened_service.archive_material_batch(
        source=source, candidates=candidates
    )
    assert second["review_items"][0]["id"] == review_id
    assert (
        reopened.connection.execute(
            "SELECT COUNT(*) FROM learning_review_items"
        ).fetchone()[0]
        == 1
    )
    assert (await reopened_service.update_review_status(review_id, "resolved"))[
        "success"
    ]
    reopened.close()


@pytest.mark.asyncio
async def test_question_review_external_view_is_allowlisted_and_preserves_storage(
    tmp_path,
):
    storage, service = _service(tmp_path)
    archived = await service.archive_learning_material(
        raw_text="review source evidence",
        material_type="real_question_candidate",
        title="复核题源",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "question_type": "case_analysis",
                "stem": "REVIEW_SAFE_STEM",
            }
        ),
        created_by="42",
        session_origin="private:42",
    )
    review_id = service.repository.record_review_item(
        source_id=archived["source_id"],
        candidate_key="review-secret-fixture",
        material_type="real_question_candidate",
        locator="PDF第1页",
        raw_fragment=(
            "REVIEW_RAW_SOURCE SECRET_REVIEW_ANSWER SECRET_REVIEW_EXPLANATION"
        ),
        proposed_structure={
            "question_type": "case_analysis",
            "stem": "REVIEW_SAFE_STEM",
            "stem_blocks": [
                {"id": "stem-1", "text": "REVIEW_SAFE_BLOCK", "locator": "PDF第1页"}
            ],
            "answer": {"value": "SECRET_REVIEW_ANSWER"},
            "explanation_blocks": [{"text": "SECRET_REVIEW_EXPLANATION"}],
            "metadata": {"answer": "SECRET_NESTED_REVIEW_ANSWER"},
            "subquestions": [
                {
                    "source_number": "（1）",
                    "stem": "REVIEW_SAFE_SUBQUESTION",
                    "answer": {"value": "SECRET_SUBQUESTION_ANSWER"},
                    "explanation": "SECRET_SUBQUESTION_EXPLANATION",
                }
            ],
        },
        review_reason="需要确认题目边界",
        now=datetime.now(timezone.utc),
    )

    listed = await service.list_review_items()
    detail = await service.get_review_item(review_id)
    for payload in (listed, detail):
        serialized = json.dumps(payload, ensure_ascii=False)
        for secret in (
            "REVIEW_RAW_SOURCE",
            "SECRET_REVIEW_ANSWER",
            "SECRET_REVIEW_EXPLANATION",
            "SECRET_NESTED_REVIEW_ANSWER",
            "SECRET_SUBQUESTION_ANSWER",
            "SECRET_SUBQUESTION_EXPLANATION",
        ):
            assert secret not in serialized
    item = detail["item"]
    assert item["answer_safe"] is True
    assert "REVIEW_SAFE_STEM" in item["safe_preview"]
    assert "REVIEW_SAFE_SUBQUESTION" in json.dumps(
        item["safe_structure"], ensure_ascii=False
    )
    assert "raw_fragment" not in item
    assert "proposed_structure" not in item
    persisted = service.repository.get_review_item(review_id)
    assert "SECRET_REVIEW_ANSWER" in persisted.raw_fragment
    assert persisted.proposed_structure["answer"]["value"] == "SECRET_REVIEW_ANSWER"
    storage.close()


@pytest.mark.asyncio
async def test_structured_choice_review_projects_dict_options_without_leaking_answers(
    tmp_path,
):
    storage, service = _service(tmp_path)
    source_id = service.record_source(_batch_source())
    proposed_structure = {
        "question_type": "single_choice",
        "stem": "SAFE_REVIEW_STEM",
        "options": [
            {"key": "A", "text": "SAFE_OPTION_ALPHA", "locator": "PDF第1页"},
            {"key": "B", "text": "SAFE_OPTION_BETA", "locator": "PDF第1页"},
        ],
        "answer": {"keys": ["B"], "provenance": "source_text"},
        "explanation_blocks": [{"text": "SECRET_REVIEW_EXPLANATION"}],
    }
    review_id = service.repository.record_review_item(
        source_id=source_id,
        candidate_key="synthetic-objective-dict-options",
        material_type="real_question_candidate",
        locator="PDF第1页",
        raw_fragment="SECRET_REVIEW_RAW",
        proposed_structure=proposed_structure,
        review_reason="合成选择题复核",
        now=datetime.now(timezone.utc),
    )

    listed = await service.list_review_items()
    detail = await service.get_review_item(review_id)

    assert listed["success"] is True
    assert detail["success"] is True
    item = detail["item"]
    assert item["answer_safe"] is True
    assert item["safe_preview"] == (
        "SAFE_REVIEW_STEM\nA. SAFE_OPTION_ALPHA\nB. SAFE_OPTION_BETA"
    )
    assert item["safe_structure"]["options"] == proposed_structure["options"]
    serialized = json.dumps({"listed": listed, "detail": detail}, ensure_ascii=False)
    for secret in (
        "SECRET_REVIEW_RAW",
        '"keys": ["B"]',
        "SECRET_REVIEW_EXPLANATION",
        '"answer"',
        '"explanation_blocks"',
    ):
        assert secret not in serialized
    assert "answer" not in item["safe_structure"]
    assert "explanation_blocks" not in item["safe_structure"]
    storage.close()


@pytest.mark.asyncio
async def test_review_preview_projects_mixed_option_shapes_as_strings_only(tmp_path):
    storage, service = _service(tmp_path)
    source_id = service.record_source(_batch_source())
    options = [
        "A. STRING_OPTION",
        {"key": "B", "text": "DICT_OPTION", "answer": "HIDDEN"},
        {"key": "C"},
        {"text": "TEXT_ONLY"},
        {},
        None,
        7,
    ]
    review_id = service.repository.record_review_item(
        source_id=source_id,
        candidate_key="synthetic-mixed-options",
        material_type="real_question_candidate",
        locator="PDF第2页",
        raw_fragment="HIDDEN_RAW",
        proposed_structure={"stem": "MIXED_STEM", "options": options},
        review_reason="混合选项格式",
        now=datetime.now(timezone.utc),
    )

    detail = await service.get_review_item(review_id)
    item = detail["item"]

    assert item["safe_preview"] == (
        "MIXED_STEM\nA. STRING_OPTION\nB. DICT_OPTION\nC\nTEXT_ONLY"
    )
    assert all(isinstance(part, str) for part in item["safe_preview"].splitlines())
    assert "{}" not in item["safe_preview"]
    assert "None" not in item["safe_preview"]
    assert "HIDDEN" not in json.dumps(item, ensure_ascii=False)
    assert item["safe_structure"]["options"] == [
        "A. STRING_OPTION",
        {"key": "B", "text": "DICT_OPTION"},
        {"key": "C"},
        {"text": "TEXT_ONLY"},
    ]
    storage.close()


@pytest.mark.asyncio
async def test_subjective_review_with_empty_options_keeps_safe_preview(tmp_path):
    storage, service = _service(tmp_path)
    source_id = service.record_source(_batch_source())
    review_id = service.repository.record_review_item(
        source_id=source_id,
        candidate_key="synthetic-empty-options",
        material_type="real_question_candidate",
        locator="PDF第3页",
        raw_fragment="HIDDEN_RAW",
        proposed_structure={
            "question_type": "short_answer",
            "stem": "SUBJECTIVE_SAFE_STEM",
            "options": [],
        },
        review_reason="简答题复核",
        now=datetime.now(timezone.utc),
    )

    detail = await service.get_review_item(review_id)

    assert detail["success"] is True
    assert detail["item"]["safe_preview"] == "SUBJECTIVE_SAFE_STEM"
    assert detail["item"]["safe_structure"]["options"] == []
    storage.close()


@pytest.mark.asyncio
async def test_only_trusted_official_archive_can_create_official_case(tmp_path):
    storage, service = _service(tmp_path)
    source = _batch_source()
    candidate = {
        "title": "官方独立案例",
        "subjects": ["知识产权"],
        "locator": "文章/案例一",
        "structured": {
            "case_summary": "官方原文支持的案件事实",
            "issues": ["是否侵权"],
            "evidence_text": "案例一：官方原文支持的案件事实",
        },
    }

    ordinary = await service.archive_learning_material(
        raw_text="不应直接创建官方案例",
        material_type="official_case",
        created_by="42",
        session_origin="private:42",
    )
    trusted = await service.archive_official_cases(
        source=source, candidates=[candidate], adapter_key="court_cases"
    )

    assert ordinary["error"] == "invalid_material_type"
    assert trusted["archived"] == 1
    detail = await service.get_learning_item(trusted["items"][0]["item_id"])
    assert detail["item"]["identity"] == "official_case"
    assert detail["item"]["verification_status"] == "verified_official"
    assert detail["source_links"][0]["locator"] == "文章/案例一"
    storage.close()
