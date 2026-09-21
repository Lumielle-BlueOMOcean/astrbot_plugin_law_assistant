from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import storage as storage_module
from content import validate_generated_question
from extraction import EventExtractor
from learning_inventory import LearningContentProvider
from library_models import CaseDetail, LearningItem, LibrarySource, QuestionDetail
from library_repository import LibraryRepository
from library_service import LibraryService
from models import SourceDocument
from service import LawAssistantService
from sources.generic import GenericEventSourceAdapter
from storage import SQLiteStorage
from tests.fakes import RecordingPublisher, make_event


def _source(index: int, *, created_by: str = "source:court_cases") -> LibrarySource:
    return LibrarySource(
        source_kind="official_article",
        title=f"官方文章 {index}",
        raw_text=f"官方文章正文 {index}",
        source_url=f"https://court.example/article/{index}",
        content_hash=f"source-hash-{index}",
        created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        created_by=created_by,
        session_origin="source:court_cases",
        metadata={"adapter_key": "court_cases", "source_item_key": "article-1"},
    )


def _official_item(index: int) -> LearningItem:
    return LearningItem(
        item_type="case",
        identity="official_case",
        item_hash=f"official-item-{index}",
        title=f"案例 {index}",
        subjects=("civil_law",),
        verification_status="verified_official",
        source_summary=f"案例摘要 {index}",
        created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        created_by="source:court_cases",
        metadata={},
    )


def _case_detail(index: int) -> CaseDetail:
    return CaseDetail(
        case_number="",
        authority="最高人民法院",
        case_summary=f"案例摘要 {index}",
        issues=("争议",),
        reasoning="官方要旨",
        result_text="结果",
        practice_notes=("学习要点",),
    )


def _mock_question(
    index: int, subject: str
) -> tuple[LibrarySource, LearningItem, QuestionDetail]:
    source = LibrarySource(
        source_kind="file",
        title=f"题目资料 {index}",
        raw_text=f"题目原文 {index}",
        source_url=f"https://example.test/questions/{index}",
        content_hash=f"question-source-{index}",
        created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        created_by="42",
        session_origin="private:42",
    )
    item = LearningItem(
        item_type="question",
        identity="mock_question",
        item_hash=f"mock-item-{index}",
        title=f"模拟题 {index}",
        subjects=(subject,),
        verification_status="not_applicable",
        source_summary="模拟题摘要",
        created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        created_by="42",
        metadata={},
    )
    question = QuestionDetail(
        question_identity="mock_question",
        question_type="single_choice",
        stem=f"题干 {index}",
        options=("A. 选项一", "B. 选项二"),
        answer="A",
        explanation="解析",
        exam_name="",
        exam_year="",
        paper="",
        question_number="",
        answer_source="AI generated",
    )
    return source, item, question


def test_update_merges_manual_subjects_and_note_without_losing_metadata(tmp_path):
    storage = SQLiteStorage(tmp_path / "library.sqlite3")
    repository = LibraryRepository(storage.connection)
    result = repository.archive(
        _source(1, created_by="42"),
        _official_item(1),
        case=_case_detail(1),
    )

    updated = repository.update(
        result.item_id,
        {"subjects": ("intellectual_property",), "note": "人工备注"},
    )

    assert updated is not None
    assert updated.item.subjects == ("intellectual_property",)
    assert updated.item.metadata == {"manual_subjects": True, "note": "人工备注"}
    storage.close()


@pytest.mark.asyncio
async def test_inventory_filters_beyond_old_query_windows(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))
    repository = library.repository
    for index in range(501):
        source, item, question = _mock_question(index, "criminal_law")
        repository.archive(source, item, question=question)
    source, item, question = _mock_question(501, "economic_law")
    repository.archive(source, item, question=question)

    real_records = [
        {
            "source_name": "合法题库",
            "exam_name": "测试考试",
            "exam_year": "2025",
            "source_url": f"https://example.test/real/{index}",
            "source_locator": f"第{index}题",
            "subject": "刑法",
            "question_type": "单选",
            "stem": f"真题 {index}",
            "options": ["A", "B"],
            "answer": "A",
            "answer_source": "官方答案",
            "verification_status": "verified",
        }
        for index in range(1001)
    ]
    real_records.append(
        {
            **real_records[-1],
            "source_url": "https://example.test/real/target",
            "source_locator": "目标题",
            "subject": "知识产权",
            "stem": "窗口之外的知识产权真题",
        }
    )
    storage.import_real_questions(real_records)
    provider = LearningContentProvider(storage, library_service=library)

    mock_result = await provider.select_question(
        origin="mock", subject="economic_law", question_type="single_choice"
    )
    real_result = await provider.select_question(
        origin="real", subject="intellectual_property", question_type="single_choice"
    )

    assert mock_result["available"] is True
    assert mock_result["subject"] == "economic_law"
    assert real_result["available"] is True
    assert real_result["subject"] == "intellectual_property"
    storage.close()


def test_official_case_subject_update_covers_all_source_segments(tmp_path):
    storage = SQLiteStorage(tmp_path / "library.sqlite3")
    repository = LibraryRepository(storage.connection)
    service = LibraryService(repository)
    for index in range(51):
        repository.archive(
            _source(index), _official_item(index), case=_case_detail(index)
        )

    updated = service.set_official_case_subjects(
        source_key="court_cases",
        source_item_key="article-1",
        subjects=("intellectual_property",),
    )

    assert updated == 51
    assert repository.search(
        identity="official_case", subject="intellectual_property", limit=None
    )
    storage.close()


@pytest.mark.parametrize(
    ("question_type", "answer", "expected"),
    [
        ("single_choice", "Z", False),
        ("single_choice", "A", True),
        ("multiple_choice", ["A", "Z"], False),
        ("multiple_choice", ["A"], True),
    ],
)
def test_generated_choice_answers_must_reference_existing_options(
    question_type, answer, expected
):
    assert (
        validate_generated_question(
            {
                "question": "题干",
                "options": ["A. 选项一", "B. 选项二"],
                "answer": answer,
                "explanation": "解析",
            },
            question_type,
        )
        is expected
    )


@pytest.mark.asyncio
async def test_llm_date_evidence_must_match_value_and_kind():
    class FakeLLM:
        async def generate_json(self, prompt, *, session_origin=None):
            return {
                "relevant": True,
                "dates": [
                    {
                        "kind": "registration_deadline",
                        "value": "2027-05-01",
                        "evidence": "报名截止：2025年5月1日",
                    },
                    {
                        "kind": "registration_deadline",
                        "value": "2025-05-02",
                        "evidence": "报名开始：2025年5月2日",
                    },
                ],
            }

    document = SourceDocument(
        source_key="fake",
        source_item_key="notice-1",
        url="https://example.test/notice-1",
        title="法律硕士竞赛报名通知",
        content="法律硕士竞赛报名通知\n报名截止：2025年5月1日\n报名开始：2025年5月2日",
        fetched_at="2025-04-01T00:00:00+08:00",
    )
    event = (await EventExtractor(llm_service=FakeLLM()).extract(document))[0]

    mismatched_value = next(date for date in event.dates if date.datetime.year == 2027)
    mismatched_kind = next(
        date
        for date in event.dates
        if date.datetime.year == 2025
        and date.datetime.month == 5
        and date.datetime.day == 2
        and date.kind == "registration_deadline"
    )
    assert mismatched_value.confirmed is False
    assert mismatched_kind.confirmed is False


@pytest.mark.asyncio
async def test_event_publication_token_rejects_changed_revision(tmp_path):
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    event_id = storage.upsert_event(make_event())
    service = LawAssistantService(
        storage,
        publisher=publisher,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: now,
    )
    await service.bind_target("aiocqhttp:group:1", "一群")
    prepared = await service.prepare_publish_event(event_id, actor_id="42")
    assert prepared["ready"] is True
    updated = make_event(title="更新后的活动")
    updated.raw_content_hash = "new-hash"
    storage.upsert_event(updated)

    result = await service.confirm_publish(prepared["token"], actor_id="42")

    assert result["success"] is False
    assert "revision" in result["reason"]
    assert publisher.calls == []


def test_unknown_publication_result_blocks_automatic_duplicate_retry(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    event_id = storage.upsert_event(make_event())
    target_id = storage.bind_target("aiocqhttp:group:unknown", "不确定群")["id"]
    publication_id = storage.claim_publication(event_id, 1, target_id, "manual")
    assert publication_id is not None
    storage.finish_publication(
        publication_id, status="unknown", error_summary="transport timeout"
    )
    assert storage.list_publications(limit=1)[0]["status"] == "unknown"
    assert storage.claim_publication(event_id, 1, target_id, "manual") is None
    storage.close()


@pytest.mark.asyncio
async def test_generic_source_key_keeps_complete_path_and_query():
    class FakeHttp:
        async def fetch_document(self, url, **kwargs):
            if url.endswith("/index"):
                return SimpleNamespace(
                    url=url,
                    text='<a href="/news/123.html">新闻</a><a href="/events/123.html">活动</a>',
                    content_type="text/html",
                )
            return SimpleNamespace(
                url=url,
                text="法律硕士报名截止：2026年10月1日",
                content_type="text/html",
            )

    documents = await GenericEventSourceAdapter(
        "extra:test", "https://example.test/index", FakeHttp()
    ).fetch()

    assert [document.source_item_key for document in documents] == [
        "news/123.html",
        "events/123.html",
    ]


def test_migration_failure_rolls_back_v8_to_v9_objects(tmp_path, monkeypatch):
    path = tmp_path / "migration.sqlite3"
    storage = SQLiteStorage(path)
    storage.connection.execute(
        "UPDATE schema_meta SET value = '8' WHERE key = 'version'"
    )
    storage.connection.execute("DROP TABLE event_relations")
    storage.connection.execute("DROP TABLE canonical_publications")
    storage.connection.commit()
    storage.close()

    original = storage_module._MIGRATIONS[8]

    def fail_after_migration(connection):
        original(connection)
        raise RuntimeError("injected migration failure")

    monkeypatch.setitem(storage_module._MIGRATIONS, 8, fail_after_migration)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        SQLiteStorage(path)

    check = SQLiteStorage.__new__(SQLiteStorage)
    check._connection = __import__("sqlite3").connect(path)
    assert (
        check._connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()[0]
        == "8"
    )
    assert (
        check._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'event_relations'"
        ).fetchone()
        is None
    )
    check._connection.close()


def test_date_only_event_date_has_date_precision():
    from date_parser import parse_chinese_dates

    date = parse_chinese_dates(
        "报名截止：2026年10月1日", timezone_name="Asia/Shanghai"
    )[0]

    assert date.precision == "date"
    assert date.datetime == datetime(2026, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
