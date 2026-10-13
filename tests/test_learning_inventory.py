from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from learning_inventory import LearningContentProvider
from learning_service import LearningService
from library_models import LibrarySource
from library_repository import LibraryRepository
from library_service import LibraryService
from service import LawAssistantService
from storage import SQLiteStorage
from tests.test_structured_ingestion import (
    _objective_group_payload,
    _prepare_service,
    _text_pdf_bytes,
)


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


class FirstChoiceRandom:
    def choice(self, values):
        return values[0]


async def _import_and_promote_synthetic_objective_group(
    tmp_path,
    *,
    promote_count: int = 3,
    mismatched_subject: bool = False,
    singleton: bool = False,
):
    from content import RealQuestion

    pdf_bytes = _text_pdf_bytes()
    payload = _objective_group_payload(pdf_bytes)
    if singleton:
        payload["questions"] = payload["questions"][:1]
        payload["objective_question_groups"] = []
    if mismatched_subject:
        payload["questions"][2]["subjects"] = ["criminal_law"]
    storage, ingestion, prepared, _ = await _prepare_service(
        tmp_path, payload_override=payload
    )
    confirmed = await ingestion.confirm(prepared)
    repository = LibraryRepository(storage.connection)
    for member in confirmed["items"][:promote_count]:
        source = next(
            question
            for question in payload["questions"]
            if question["id"] == member["external_id"]
        )
        question = RealQuestion.from_mapping(
            {
                "source_name": "合成核验题库",
                "exam_name": "合成考试",
                "exam_year": "2099",
                "source_locator": f"合成题号 {source['source_number']}",
                "subject": source["subjects"][0],
                "question_type": source["question_type"],
                "stem": source["stem_blocks"][0]["text"],
                "options": [
                    f"{option['key']}. {option['text']}" for option in source["options"]
                ],
                "answer": source["answer"]["keys"],
                "explanation": source["explanation_blocks"][0]["text"],
                "answer_source": "third_party",
                "verification_status": "verified",
            }
        )
        storage.promote_candidate_real_question(
            question,
            item_id=member["item_id"],
            actor_id="synthetic-reviewer",
            at="2026-10-13T00:00:00+00:00",
        )
    return storage, ingestion, confirmed, payload, repository


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
                "answer_source": "official",
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
                "answer_source": "third_party",
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
    assert selected["answer_source"] == "official"
    storage.close()


@pytest.mark.asyncio
async def test_structured_candidates_never_enter_verified_real_inventory(tmp_path):
    storage, _, _, _, repository = await _import_and_promote_synthetic_objective_group(
        tmp_path, promote_count=0
    )
    provider = LearningContentProvider(
        storage,
        library_service=LibraryService(repository),
        rng=FirstChoiceRandom(),
    )

    result = await provider.select_question(
        origin="real", subject="civil_commercial", question_type="multiple_choice"
    )

    assert storage.count_real_questions() == 0
    assert result["available"] is False
    assert "已核验真题" in result["reason"]
    storage.close()


@pytest.mark.asyncio
async def test_partially_promoted_objective_group_is_not_split_into_single_real_questions(
    tmp_path,
):
    storage, _, _, _, repository = await _import_and_promote_synthetic_objective_group(
        tmp_path, promote_count=1
    )
    provider = LearningContentProvider(
        storage,
        library_service=LibraryService(repository),
        rng=FirstChoiceRandom(),
    )

    result = await provider.select_question(
        origin="real", subject="civil_commercial", question_type="multiple_choice"
    )

    assert storage.count_real_questions() == 1
    assert result["available"] is False
    assert "已核验真题" in result["reason"]
    storage.close()


@pytest.mark.asyncio
async def test_fully_promoted_objective_group_is_one_strictly_filtered_real_unit(
    tmp_path,
):
    storage, _, _, _, repository = await _import_and_promote_synthetic_objective_group(
        tmp_path, promote_count=3
    )
    provider = LearningContentProvider(
        storage,
        library_service=LibraryService(repository),
        rng=FirstChoiceRandom(),
    )

    selected = await provider.select_question(
        origin="real", subject="civil_commercial", question_type="multiple_choice"
    )
    used_group = await provider.select_question(
        origin="real",
        subject="civil_commercial",
        question_type="multiple_choice",
        used_content_keys={(selected["source_kind"], selected["source_item_key"])},
    )
    strict_subject_miss = await provider.select_question(
        origin="real", subject="intellectual_property", question_type="multiple_choice"
    )

    assert storage.count_real_questions() == 3
    assert selected["available"] is True
    assert selected["origin"] == "real"
    assert selected["objective_group_id"] == "civil-objective-206-208"
    assert [question["id"] for question in selected["objective_questions"]] == [
        "objective-206",
        "objective-207",
        "objective-208",
    ]
    assert selected["source_kind"] == "structured_objective_group"
    assert selected["source_item_key"].startswith(f"{selected['import_id']}:")
    assert (
        selected["item"]["id"]
        == repository.list_structured_delivery_units()[0]["members"][0]["item_id"]
    )
    assert used_group["available"] is False
    assert strict_subject_miss["available"] is False
    storage.close()


@pytest.mark.asyncio
async def test_objective_group_subject_filter_must_match_every_member(tmp_path):
    storage, _, _, _, repository = await _import_and_promote_synthetic_objective_group(
        tmp_path, promote_count=3, mismatched_subject=True
    )
    provider = LearningContentProvider(
        storage,
        library_service=LibraryService(repository),
        rng=FirstChoiceRandom(),
    )

    result = await provider.select_question(
        origin="real", subject="civil_commercial", question_type="multiple_choice"
    )

    assert result["available"] is False
    assert storage.count_real_questions() == 3
    storage.close()


@pytest.mark.asyncio
async def test_material_referenced_objective_singleton_uses_structured_snapshot(
    tmp_path,
):
    storage, _, _, _, repository = await _import_and_promote_synthetic_objective_group(
        tmp_path, promote_count=1, singleton=True
    )
    provider = LearningContentProvider(
        storage,
        library_service=LibraryService(repository),
        rng=FirstChoiceRandom(),
    )

    result = await provider.select_question(
        origin="real", subject="civil_commercial", question_type="multiple_choice"
    )

    assert result["available"] is True
    assert result["objective_group_id"] == ""
    assert len(result["objective_questions"]) == 1
    assert result["source_kind"] == "real_question"
    assert result["shared_materials"][0]["id"] == "material-1"
    storage.close()


@pytest.mark.asyncio
async def test_verified_group_daily_and_numbered_selection_use_current_real_question(
    tmp_path,
):
    (
        storage,
        _,
        confirmed,
        imported_payload,
        repository,
    ) = await _import_and_promote_synthetic_objective_group(tmp_path)
    item_id = next(
        int(item["item_id"])
        for item in confirmed["items"]
        if item["external_id"] == "objective-206"
    )
    library = LibraryService(repository)

    class RecordingPublisher:
        def __init__(self):
            self.calls = []

        async def publish_text(self, destination, text):
            self.calls.append((destination, text))
            return True

    publisher = RecordingPublisher()
    service = LawAssistantService(
        storage,
        library_service=library,
        publisher=publisher,
        config=SimpleNamespace(operator_ids=["synthetic-reviewer"]),
    )
    edited = await service.update_management_learning_item(
        item_id,
        {
            "stem": "人工修订后的权威题干？",
            "options": ["A. 旧选项", "B. 人工修订后的正确选项"],
            "answer": ["B"],
            "answer_source": "user_verified",
            "explanation": "人工修订后的权威解析。",
        },
        actor_id="synthetic-reviewer",
    )
    assert edited["success"] is True

    provider = LearningContentProvider(
        storage,
        library_service=library,
        rng=FirstChoiceRandom(),
    )
    daily = await provider.select_question(
        origin="real",
        subject="civil_commercial",
        question_type="multiple_choice",
    )

    first_daily_member = daily["objective_questions"][0]
    assert first_daily_member["answer"] == ["B"]
    assert first_daily_member["stem_blocks"] == "人工修订后的权威题干？"
    assert first_daily_member["options"] == [
        "A. 旧选项",
        "B. 人工修订后的正确选项",
    ]
    assert first_daily_member["explanation"] == "人工修订后的权威解析。"
    assert first_daily_member["answer_source"] == "user_verified"

    stored_import = storage.connection.execute(
        "SELECT payload_json FROM structured_imports"
    ).fetchone()
    persisted_import_payload = json.loads(stored_import["payload_json"])
    assert (
        persisted_import_payload["questions"][0]["answer"]["keys"]
        == imported_payload["questions"][0]["answer"]["keys"]
        == ["A"]
    )

    opened = await service.start_question_by_number(
        "206",
        source_selector=None,
        session_origin="aiocqhttp:FriendMessage:synthetic-reviewer",
        actor_id="synthetic-reviewer",
    )
    assert opened["success"] is True
    assert "人工修订后的权威题干？" not in opened["text"]
    page = opened
    for _ in range(10):
        if "人工修订后的权威题干？" in page["text"]:
            break
        page = await service.question_session_action(
            "next",
            session_origin="aiocqhttp:FriendMessage:synthetic-reviewer",
            actor_id="synthetic-reviewer",
        )
        assert page["success"] is True
    assert "人工修订后的权威题干？" in page["text"]

    revealed = await service.question_session_action(
        "answer",
        question_number="206",
        session_origin="aiocqhttp:FriendMessage:synthetic-reviewer",
        actor_id="synthetic-reviewer",
    )
    assert revealed["success"] is True
    assert "真题参考答案 · 人工核验答案来源" in revealed["text"]
    assert revealed["text"].splitlines()[-1] == "B"
    explanation = await service.question_session_action(
        "explanation",
        question_number="206",
        session_origin="aiocqhttp:FriendMessage:synthetic-reviewer",
        actor_id="synthetic-reviewer",
    )
    assert explanation["success"] is True
    assert "人工修订后的权威解析。" in explanation["text"]

    target = await service.bind_target("aiocqhttp:group:verified-group", "核验题群")
    preview = await service.prepare_publish_question(
        source_number="206",
        target_selectors=["核验题群"],
        actor_id="synthetic-reviewer",
    )
    assert preview["ready"] is True
    preview_text = "\n".join(preview["preview_pages"])
    assert "人工修订后的权威题干？" in preview_text
    assert "人工修订后的正确选项" in preview_text
    assert "人工修订后的权威解析" not in preview_text
    assert "真题参考答案" not in preview_text

    published = await service.confirm_publish(
        preview["token"], actor_id="synthetic-reviewer"
    )
    assert published == {"success": True, "published_count": 1, "failed_count": 0}
    assert publisher.calls == [
        (target["unified_msg_origin"], text) for text in preview["preview_pages"]
    ]
    published_answer = await service.question_session_action(
        "answer",
        question_number="206",
        session_origin=target["unified_msg_origin"],
        actor_id="synthetic-reviewer",
    )
    assert published_answer["success"] is True
    assert published_answer["text"].splitlines()[-1] == "B"
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
                "question_type": "不定项选择题",
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
        origin="mock",
        subject="intellectual_property",
        question_type="indefinite_choice",
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
async def test_provider_generates_indefinite_choice_mock(tmp_path):
    llm = ValidLLM()
    storage, _, provider = _services(tmp_path, llm)

    result = await provider.select_question(
        origin="mock",
        subject="intellectual_property",
        question_type="indefinite_choice",
    )

    assert result["available"] is True
    assert result["origin"] == "mock"
    assert result["question_type"] == "indefinite_choice"
    assert llm.calls == 1
    storage.close()


@pytest.mark.asyncio
async def test_mock_random_selection_uses_exact_session_origin_no_repeat_key(tmp_path):
    storage, library, provider = _services(tmp_path)
    identifiers = []
    for title in ("合成题甲", "合成题乙"):
        archived = await library.archive_learning_material(
            raw_text=f"{title} 原始资料",
            material_type="mock_question",
            title=title,
            subjects="刑法",
            structured_json={
                "question_type": "single_choice",
                "stem": f"{title} 题干？",
                "options": ["A. 甲", "B. 乙"],
                "answer": "A",
                "explanation": "合成解析。",
            },
            created_by="operator-1",
            session_origin="private:operator-1",
        )
        identifiers.append(str(archived["item_id"]))

    first = await provider.select_question(
        origin="mock",
        subject="criminal_law",
        question_type="single_choice",
        session_origin="private:operator-1",
    )
    remaining = await provider.select_question(
        origin="mock",
        subject="criminal_law",
        question_type="single_choice",
        session_origin="private:operator-1",
        used_content_keys={("library_mock", str(first["question_id"]))},
    )
    same_id_other_scope = await provider.select_question(
        origin="mock",
        subject="criminal_law",
        question_type="single_choice",
        session_origin="private:another-operator",
        used_content_keys={("real_question", str(first["question_id"]))},
    )

    assert first["available"] is True
    assert remaining["available"] is True
    assert remaining["question_id"] != first["question_id"]
    assert str(remaining["question_id"]) in identifiers
    assert same_id_other_scope["available"] is True
    assert same_id_other_scope["question_id"] in {int(value) for value in identifiers}
    storage.close()


@pytest.mark.asyncio
async def test_manual_mock_session_avoids_immediate_repeat_only_in_exact_scope(
    tmp_path,
):
    storage, library, _ = _services(tmp_path)
    for title in ("合成题甲", "合成题乙"):
        await library.archive_learning_material(
            raw_text=f"{title} 原始资料",
            material_type="mock_question",
            title=title,
            subjects="刑法",
            structured_json={
                "question_type": "single_choice",
                "stem": f"{title} 题干？",
                "options": ["A. 甲", "B. 乙"],
                "answer": "A",
                "explanation": "合成解析。",
            },
            created_by="operator-1",
            session_origin="private:operator-1",
        )
    learning = LearningService(
        storage, None, library_service=library, rng=FirstChoiceRandom()
    )
    service = LawAssistantService(
        storage, learning_service=learning, library_service=library
    )

    first = await service.start_question_session(
        origin="mock",
        subject="criminal_law",
        question_type="single_choice",
        session_origin="private:operator-1",
        actor_id="operator-1",
    )
    first_session = service.question_sessions.get_latest_for_scope("private:operator-1")
    second = await service.start_question_session(
        origin="mock",
        subject="criminal_law",
        question_type="single_choice",
        session_origin="private:operator-1",
        actor_id="operator-1",
    )
    second_session = service.question_sessions.get_latest_for_scope(
        "private:operator-1"
    )
    other_scope = await service.start_question_session(
        origin="mock",
        subject="criminal_law",
        question_type="single_choice",
        session_origin="private:other",
        actor_id="other",
    )

    assert first["success"] is second["success"] is other_scope["success"] is True
    assert (
        first_session["source_kind"] == second_session["source_kind"] == "library_mock"
    )
    assert first_session["source_item_key"] != second_session["source_item_key"]
    assert service.question_sessions.get_active("private:other") is not None
    storage.close()


@pytest.mark.asyncio
async def test_provider_selects_indefinite_choice_from_verified_real_inventory(
    tmp_path,
):
    storage, _, provider = _services(tmp_path)
    storage.import_real_questions(
        [
            {
                "source_name": "合成已核验题库",
                "exam_name": "合成考试",
                "exam_year": "2023",
                "source_locator": "合成第1题",
                "subject": "刑法",
                "question_type": "indefinite_choice",
                "stem": "哪些合成选项成立？",
                "options": ["A. 甲", "B. 乙", "C. 丙"],
                "answer": ["A", "C"],
                "answer_source": "official",
                "verification_status": "verified",
            }
        ]
    )

    result = await provider.select_question(
        origin="real", subject="criminal_law", question_type="indefinite_choice"
    )

    assert result["available"] is True
    assert result["origin"] == "real"
    assert result["question_type"] == "indefinite_choice"
    assert result["answer_source"] == "official"
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
                "answer_source": "official",
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
