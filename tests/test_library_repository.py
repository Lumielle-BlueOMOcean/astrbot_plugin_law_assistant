from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from library_models import CaseDetail, LearningItem, LibrarySource, QuestionDetail
from library_repository import LibraryRepository
from storage import SCHEMA_VERSION, SQLiteStorage


def _source(*, created_by: str = "42", text: str = "原始案例正文") -> LibrarySource:
    return LibrarySource(
        source_kind="user_text",
        title="用户资料",
        raw_text=text,
        source_url="",
        content_hash=f"hash:{text}",
        created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        created_by=created_by,
        session_origin="aiocqhttp:private:42",
        metadata={},
    )


def _item(
    *,
    item_hash: str,
    identity: str = "user_case",
    title: str = "短视频著作权案",
    subjects: tuple[str, ...] = ("intellectual_property",),
) -> LearningItem:
    return LearningItem(
        item_type="case" if identity == "user_case" else "question",
        identity=identity,
        item_hash=item_hash,
        title=title,
        subjects=subjects,
        verification_status="unverified",
        source_summary="用户整理摘要",
        created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        created_by="42",
        metadata={"note": "复习重点"},
    )


def test_new_database_migrates_to_v8_and_creates_library_tables(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "library.sqlite3")

    assert storage.schema_version == SCHEMA_VERSION == 8
    tables = {
        row[0]
        for row in storage.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "library_sources",
        "learning_items",
        "learning_item_sources",
        "learning_cases",
        "learning_questions",
    } <= tables
    assert storage.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    storage.close()


def test_archive_reuses_source_but_keeps_distinct_items(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "library.sqlite3")
    repository = LibraryRepository(storage.connection)
    source = _source()
    first = _item(item_hash="item-1")
    second = _item(
        item_hash="item-2",
        title="短视频搬运侵权学习笔记",
        subjects=("civil_commercial",),
    )

    first_result = repository.archive(
        source,
        first,
        case=CaseDetail(
            case_number="",
            authority="",
            case_summary="摘要一",
            issues=("是否侵权",),
            reasoning="理由一",
            result_text="结果一",
            practice_notes=("要点一",),
        ),
    )
    second_result = repository.archive(
        source,
        second,
        case=CaseDetail(
            case_number="",
            authority="",
            case_summary="摘要二",
            issues=("是否承担责任",),
            reasoning="理由二",
            result_text="结果二",
            practice_notes=("要点二",),
        ),
    )

    assert first_result.source_id == second_result.source_id
    assert first_result.item_id != second_result.item_id
    assert repository.get(first_result.item_id).case.case_summary == "摘要一"
    assert repository.get(second_result.item_id).case.case_summary == "摘要二"
    storage.close()


def test_duplicate_archive_returns_existing_item_without_overwriting(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "library.sqlite3")
    repository = LibraryRepository(storage.connection)
    source = _source()
    item = _item(item_hash="same-item")
    original_case = CaseDetail(
        case_number="",
        authority="",
        case_summary="原始摘要",
        issues=("原始争议",),
        reasoning="原始理由",
        result_text="原始结果",
        practice_notes=("原始要点",),
    )
    repository.archive(source, item, case=original_case)

    repository.archive(
        source,
        item,
        case=CaseDetail(
            case_number="",
            authority="",
            case_summary="不应覆盖",
            issues=(),
            reasoning="",
            result_text="",
            practice_notes=(),
        ),
    )

    loaded = repository.get(1)
    assert loaded.item.source_summary == "用户整理摘要"
    assert loaded.case.case_summary == "原始摘要"
    storage.close()


def test_repository_round_trips_question_and_searches_detail_fields(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "library.sqlite3")
    repository = LibraryRepository(storage.connection)
    question = _item(
        item_hash="question-1",
        identity="mock_question",
        title="著作权多选练习",
    )
    question = replace(question, verification_status="not_applicable")
    result = repository.archive(
        _source(text="原始题目资料"),
        question,
        question=QuestionDetail(
            question_identity="mock_question",
            question_type="multiple_choice",
            stem="短视频传播是否需要授权？",
            options=("A. 需要", "B. 不需要"),
            answer=("A",),
            explanation="练习解析",
            exam_name="",
            exam_year="",
            paper="",
            question_number="",
            answer_source="AI generated",
        ),
    )

    assert repository.get(result.item_id).question.stem == "短视频传播是否需要授权？"
    assert repository.search(query="短视频传播", limit=10)[0].id == result.item_id
    assert (
        repository.search(subject="intellectual_property", limit=10)[0].id
        == result.item_id
    )
    storage.close()
