from __future__ import annotations

import json

import pytest

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
async def test_mock_question_is_searchable_and_complete_after_archive(tmp_path):
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
    assert detail["question"]["answer"] == ["A"]
    assert detail["question"]["explanation"] == "根据题干事实判断。"
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
        created_by="42",
        session_origin="private:42",
    )

    assert (await service.search_learning_library(query="争议焦点"))["count"] == 1
    assert (await service.search_learning_library(subject="知产"))["count"] == 1
    assert (await service.search_learning_library(query="不存在"))["items"] == []
    assert (await service.search_learning_library(limit=0))["limit"] == 1
    protected = await service.update_learning_item(
        archived["item_id"],
        {"identity": "verified_real_question"},
    )
    assert protected["error"] == "invalid_update_field"
    storage.close()
