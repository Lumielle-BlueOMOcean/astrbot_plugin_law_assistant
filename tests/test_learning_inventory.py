from __future__ import annotations

import json

import pytest

from learning_inventory import LearningContentProvider
from library_models import LibrarySource
from library_repository import LibraryRepository
from library_service import LibraryService
from storage import SQLiteStorage


class ValidLLM:
    def __init__(self, answer=None):
        self.answer = answer or {
            "question": "下列哪项属于知识产权法保护对象？",
            "options": ["A. 作品", "B. 天气"],
            "answer": "A",
            "explanation": "作品符合题干要求。",
        }
        self.calls = 0

    async def generate_json(self, prompt, *, session_origin=None):
        self.calls += 1
        return self.answer


class InvalidLLM:
    async def generate_json(self, prompt, *, session_origin=None):
        return {"question": "题目", "options": ["A", "B"], "answer": []}


def _services(tmp_path, llm=None):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))
    provider = LearningContentProvider(
        storage,
        library_service=library,
        llm_service=llm,
    )
    return storage, library, provider


def _source(*, source_kind="file", title="测试资料"):
    from datetime import datetime, timezone

    return LibrarySource(
        source_kind=source_kind,
        title=title,
        raw_text="测试资料原文",
        source_url="https://example.test/source",
        content_hash="source-hash",
        created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        created_by="42",
        session_origin="private:42",
    )


@pytest.mark.asyncio
async def test_provider_selects_active_official_case_bundle(tmp_path):
    storage, library, provider = _services(tmp_path)
    result = await library.archive_official_cases(
        source=_source(source_kind="official", title="最高法案例"),
        candidates=[
            {
                "title": "知识产权案例",
                "subjects": ["知识产权"],
                "structured": {
                    "case_summary": "官方案例摘要",
                    "authority": "最高人民法院",
                },
            }
        ],
        adapter_key="court_cases",
        source_item_key="case-1",
    )

    selected = await provider.select_case(
        subject="intellectual_property", date="2026-09-21"
    )

    assert selected["available"] is True
    assert selected["origin"] == "official_case"
    assert selected["case_id"] == result["items"][0]["item_id"]
    assert selected["source_url"] == "https://example.test/source"
    storage.close()


@pytest.mark.asyncio
async def test_provider_real_question_filter_is_exact(tmp_path):
    storage, _, provider = _services(tmp_path)
    storage.import_real_questions(
        [
            {
                "source_name": "合法题库",
                "exam_name": "测试考试",
                "exam_year": "2025",
                "source_url": "https://example.test/q1",
                "source_locator": "第1题",
                "subject": "刑法",
                "question_type": "单选",
                "stem": "刑法题",
                "options": ["A", "B"],
                "answer": "A",
                "verification_status": "verified",
            },
            {
                "source_name": "合法题库",
                "exam_name": "测试考试",
                "exam_year": "2025",
                "source_url": "https://example.test/q2",
                "source_locator": "第2题",
                "subject": "民法",
                "question_type": "多选",
                "stem": "民法题",
                "options": ["A", "B"],
                "answer": ["A"],
                "verification_status": "verified",
            },
        ]
    )

    selected = await provider.select_question(
        origin="real", subject="criminal_law", question_type="single_choice"
    )

    assert selected["available"] is True
    assert selected["origin"] == "real"
    assert selected["subject"] == "criminal_law"
    assert selected["question_type"] == "single_choice"
    assert selected["question_id"] is not None
    storage.close()


@pytest.mark.asyncio
async def test_provider_reuses_persistent_mock_and_excludes_candidates(tmp_path):
    storage, library, provider = _services(tmp_path)
    archived = await library.archive_learning_material(
        raw_text="模拟题正文",
        material_type="mock_question",
        title="模拟题",
        subjects="知识产权",
        structured_json=json.dumps(
            {
                "question_type": "多选",
                "stem": "模拟题题干",
                "options": ["A", "B"],
                "answer": ["A"],
                "explanation": "解析",
            },
            ensure_ascii=False,
        ),
        created_by="system:daily_question",
        session_origin="system:daily_question",
    )
    await library.archive_learning_material(
        raw_text="待核验题正文",
        material_type="real_question_candidate",
        title="待核验题",
        subjects="知识产权",
        structured_json=json.dumps(
            {
                "question_type": "多选",
                "stem": "候选题题干",
                "options": ["A", "B"],
                "answer": ["A"],
            },
            ensure_ascii=False,
        ),
        created_by="42",
        session_origin="private:42",
    )

    selected = await provider.select_question(
        origin="mock", subject="intellectual_property", question_type="multiple_choice"
    )

    assert selected["available"] is True
    assert selected["origin"] == "mock"
    assert selected["question_id"] == archived["item_id"]
    assert selected["label"] == "模拟题"
    storage.close()


@pytest.mark.asyncio
async def test_provider_generates_valid_mock_once_then_reuses_it(tmp_path):
    llm = ValidLLM()
    storage, library, provider = _services(tmp_path, llm)

    first = await provider.select_question(
        origin="mock", subject="intellectual_property", question_type="single_choice"
    )
    second_provider = LearningContentProvider(storage, library_service=library)
    second = await second_provider.select_question(
        origin="mock", subject="intellectual_property", question_type="single_choice"
    )

    assert first["available"] is True
    assert first["origin"] == "mock"
    assert first["question_id"] == second["question_id"]
    assert llm.calls == 1
    assert (await library.search_learning_library(material_type="mock_question"))[
        "count"
    ] == 1
    storage.close()


@pytest.mark.asyncio
async def test_provider_rejects_invalid_generated_mock_without_archiving(tmp_path):
    storage, library, provider = _services(tmp_path, InvalidLLM())

    result = await provider.select_question(
        origin="mock", subject="criminal_law", question_type="multiple_choice"
    )

    assert result["available"] is False
    assert "题型" in result["reason"] or "match" in result["reason"]
    assert (await library.search_learning_library(material_type="mock_question"))[
        "count"
    ] == 0
    storage.close()


@pytest.mark.asyncio
async def test_provider_strict_no_match_does_not_fallback_to_other_origin(tmp_path):
    storage, _, provider = _services(tmp_path)

    result = await provider.select_question(
        origin="real", subject="economic_law", question_type="case_analysis"
    )

    assert result["available"] is False
    assert "真题" in result["reason"]
    storage.close()


@pytest.mark.asyncio
async def test_provider_preserves_non_choice_mock_structure(tmp_path):
    storage, library, provider = _services(tmp_path)
    await library.archive_learning_material(
        raw_text="案例分析模拟题正文",
        material_type="mock_question",
        title="案例分析模拟题",
        subjects="司法实务",
        structured_json=json.dumps(
            {
                "question_type": "案例分析题",
                "stem": "请分析本案争议。",
                "questions": ["是否构成违约？"],
                "answer": "应结合合同约定判断。",
                "explanation": "先确定合同义务。",
            },
            ensure_ascii=False,
        ),
        created_by="system:daily_question",
        session_origin="system:daily_question",
    )

    result = await provider.select_question(
        origin="mock", subject="judicial_practice", question_type="case_analysis"
    )

    assert result["available"] is True
    assert result["content"]["questions"] == ["是否构成违约？"]
    storage.close()


@pytest.mark.asyncio
async def test_provider_excludes_sent_mock_identity_but_manual_selection_still_works(
    tmp_path,
):
    storage, library, provider = _services(tmp_path)
    first = await library.archive_learning_material(
        raw_text="模拟题一正文",
        material_type="mock_question",
        title="模拟题一",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "question_type": "单选题",
                "stem": "模拟题一题干",
                "options": ["A", "B"],
                "answer": "A",
                "explanation": "解析一",
            },
            ensure_ascii=False,
        ),
        created_by="system:daily_question",
        session_origin="system:daily_question",
    )
    second = await library.archive_learning_material(
        raw_text="模拟题二正文",
        material_type="mock_question",
        title="模拟题二",
        subjects="刑法",
        structured_json=json.dumps(
            {
                "question_type": "单选题",
                "stem": "模拟题二题干",
                "options": ["A", "B"],
                "answer": "B",
                "explanation": "解析二",
            },
            ensure_ascii=False,
        ),
        created_by="system:daily_question",
        session_origin="system:daily_question",
    )

    selected = await provider.select_question(
        origin="mock",
        subject="criminal_law",
        question_type="single_choice",
        used_content_keys={("library_mock", str(first["item_id"]))},
    )
    manual = await provider.select_question(
        origin="mock", subject="criminal_law", question_type="single_choice"
    )

    assert selected["question_id"] == second["item_id"]
    assert manual["question_id"] in {first["item_id"], second["item_id"]}
    storage.close()


@pytest.mark.asyncio
async def test_provider_keeps_case_and_question_identities_independent(tmp_path):
    storage, library, provider = _services(tmp_path)
    case = await library.archive_official_cases(
        source=_source(source_kind="official", title="最高法案例"),
        candidates=[
            {
                "title": "刑法案例",
                "subjects": ["刑法"],
                "structured": {
                    "case_summary": "官方案例摘要",
                    "authority": "最高人民法院",
                },
            }
        ],
        adapter_key="court_cases",
        source_item_key="case-1",
    )
    storage.import_real_questions(
        [
            {
                "source_name": "合法题库",
                "exam_name": "测试考试",
                "exam_year": "2025",
                "source_url": "https://example.test/q1",
                "source_locator": "第1题",
                "subject": "刑法",
                "question_type": "单选",
                "stem": "刑法真题",
                "options": ["A", "B"],
                "answer": "A",
                "verification_status": "verified",
            }
        ]
    )

    selected_case = await provider.select_case(
        subject="criminal_law",
        date="2026-09-21",
        used_content_keys={("real_question", "1")},
    )
    selected_question = await provider.select_question(
        origin="real",
        subject="criminal_law",
        question_type="single_choice",
        used_content_keys={("official_case", str(case["items"][0]["item_id"]))},
    )

    assert selected_case["available"] is True
    assert selected_question["available"] is True
    storage.close()


@pytest.mark.asyncio
async def test_provider_generates_new_mock_after_sent_inventory_is_exhausted(tmp_path):
    llm = ValidLLM()
    storage, library, provider = _services(tmp_path, llm)
    archived = await library.archive_learning_material(
        raw_text="已发送模拟题",
        material_type="mock_question",
        title="已发送模拟题",
        subjects="知识产权",
        structured_json=json.dumps(
            {
                "question_type": "单选题",
                "stem": "已发送题干",
                "options": ["A", "B"],
                "answer": "A",
                "explanation": "解析",
            },
            ensure_ascii=False,
        ),
        created_by="system:daily_question",
        session_origin="system:daily_question",
    )

    result = await provider.select_question(
        origin="mock",
        subject="intellectual_property",
        question_type="single_choice",
        used_content_keys={("library_mock", str(archived["item_id"]))},
    )

    assert result["available"] is True
    assert result["origin"] == "mock"
    assert result["question_id"] != archived["item_id"]
    assert llm.calls == 1
    storage.close()
