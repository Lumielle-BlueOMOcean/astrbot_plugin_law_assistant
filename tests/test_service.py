from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from daily_plans import DailyPlan
from daily_resolver import resolve_daily_constraints
from learning_inventory import LearningContentProvider
from models import CaseItem, SourceDocument
from question_session_repository import QuestionSessionRepository
from scheduler import LawAssistantScheduler
from service import LawAssistantService
from storage import SQLiteStorage
from tests.fakes import FakeAdapter, FakeExtractor, RecordingPublisher, make_event


async def _promoted_synthetic_question(tmp_path):
    from tests.test_structured_ingestion import _prepare_service

    storage, ingestion, prepared, _ = await _prepare_service(tmp_path)
    await ingestion.confirm(prepared)
    item_id = int(
        storage.connection.execute(
            "SELECT id FROM learning_items WHERE identity = 'real_question_candidate'"
        ).fetchone()["id"]
    )
    storage.connection.execute(
        "UPDATE learning_questions SET exam_name = '合成考试' WHERE item_id = ?",
        (item_id,),
    )
    storage.connection.commit()
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        config=SimpleNamespace(operator_ids=["operator-1"]),
    )
    preview = await service.prepare_candidate_promotion(
        [item_id], actor_id="operator-1"
    )
    confirmed = await service.confirm_candidate_promotion(
        preview["token"], actor_id="operator-1"
    )
    assert confirmed["success"] is True
    return (
        storage,
        service,
        item_id,
        LearningContentProvider(storage, library_service=ingestion.library_service),
    )


def document(source_key: str, item_key: str) -> SourceDocument:
    return SourceDocument(
        source_key=source_key,
        source_item_key=item_key,
        url=f"https://example.test/{item_key}",
        title="Fake document",
        content=f"content:{item_key}",
        fetched_at="2026-09-17T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_reveal_history_page_exposes_target_progress_without_snapshot_content(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "reveal-history.sqlite3")
    service = LawAssistantService(storage)
    target = await service.bind_target("aiocqhttp:group:reveal-history", "揭晓记录群")
    session = QuestionSessionRepository(storage.connection).create_session(
        session_key="reveal-history-session",
        scope_origin=target["unified_msg_origin"],
        target_id=target["id"],
        source_kind="generated_question",
        source_item_key="synthetic-question",
        library_item_id=None,
        real_question_id=None,
        question_identity="mock_question",
        snapshot={
            "prompts": [
                {
                    "stem": "合成题干",
                    "answer_blocks": [{"text": "HIDDEN_ANSWER"}],
                    "explanation_blocks": [{"text": "HIDDEN_EXPLANATION"}],
                }
            ]
        },
        created_by="scheduler",
        created_at="2026-09-29T00:00:00+00:00",
    )
    snapshot_hash = storage.connection.execute(
        "SELECT question_snapshot_hash FROM question_sessions WHERE id = ?",
        (session["id"],),
    ).fetchone()[0]
    service.scheduled_reveals.create_for_session(
        session_id=session["id"],
        target_umo=target["unified_msg_origin"],
        snapshot_hash=snapshot_hash,
        question_sent_at="2026-09-29T00:00:00+00:00",
        due_at="2026-09-29T00:20:00+00:00",
        reveal_kind="answer",
        created_at="2026-09-29T00:00:00+00:00",
        prompt_index=0,
    )

    page = await service.dashboard_page("reveals", page=1, page_size=20)

    assert page["total"] == 1
    row = page["items"][0]
    assert row["target_label"] == "揭晓记录群"
    assert row["target_umo"] == "aiocqhttp:group:reveal-history"
    assert row["reveal_kind"] == "answer"
    assert row["status"] == "pending"
    assert row["page_progress"] == []
    serialized = str(row)
    assert "HIDDEN_ANSWER" not in serialized
    assert "HIDDEN_EXPLANATION" not in serialized
    storage.close()


@pytest.mark.asyncio
async def test_zero_source_scan_completes_cleanly(tmp_path) -> None:
    service = LawAssistantService(SQLiteStorage(tmp_path / "runtime.sqlite3"))

    result = await service.scan_events()

    assert result.source_count == 0
    assert result.upserted_count == 0
    assert result.failures == ()


@pytest.mark.asyncio
async def test_scan_runs_fake_source_pipeline_and_upserts_events(tmp_path) -> None:
    adapter = FakeAdapter("fake", [document("fake", "item-1")])
    extractor = FakeExtractor({"item-1": [make_event()]})
    service = LawAssistantService(
        SQLiteStorage(tmp_path / "runtime.sqlite3"),
        sources=[(adapter, extractor)],
    )

    result = await service.scan_events(trigger="test")

    assert result.source_count == 1
    assert result.discovered_count == 1
    assert result.upserted_count == 1
    assert await service.list_events(limit=10)


@pytest.mark.asyncio
async def test_scan_runs_validator_before_storage(tmp_path) -> None:
    adapter = FakeAdapter("fake", [document("fake", "item-1")])
    extractor = FakeExtractor({"item-1": [make_event()]})

    class RejectingValidator:
        async def validate(self, event, source_document):
            assert event.title == "法律硕士模拟竞赛"
            assert source_document.source_key == "fake"
            rejected = None
            return rejected

    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        sources=[(adapter, extractor)],
        validators=[RejectingValidator()],
    )

    result = await service.scan_events()

    assert result.discovered_count == 0
    assert result.upserted_count == 0
    assert storage.count_events() == 0


@pytest.mark.asyncio
async def test_one_source_failure_does_not_abort_other_sources(tmp_path) -> None:
    failed = FakeAdapter("failed", error=RuntimeError("source unavailable"))
    good = FakeAdapter("good", [document("good", "item-1")])
    service = LawAssistantService(
        SQLiteStorage(tmp_path / "runtime.sqlite3"),
        sources=[
            (failed, FakeExtractor()),
            (good, FakeExtractor({"item-1": [make_event(source_key="good")]})),
        ],
    )

    result = await service.scan_events()

    assert len(result.failures) == 1
    assert result.failures[0].source_key == "failed"
    assert result.upserted_count == 1
    assert (await service.list_events(limit=10))[0].source_key == "good"


@pytest.mark.asyncio
async def test_parallel_scan_is_skipped_while_first_scan_is_running(tmp_path) -> None:
    adapter = FakeAdapter("slow", [document("slow", "item-1")])
    adapter.wait_for_release = True
    service = LawAssistantService(
        SQLiteStorage(tmp_path / "runtime.sqlite3"),
        sources=[(adapter, FakeExtractor({"item-1": [make_event(source_key="slow")]}))],
    )

    first = asyncio.create_task(service.scan_events(trigger="first"))
    await adapter.started.wait()
    second = await service.scan_events(trigger="second")
    adapter.release.set()
    first_result = await first

    assert second.skipped is True
    assert first_result.skipped is False


@pytest.mark.asyncio
async def test_service_list_and_get_delegate_to_storage(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(storage)
    event_id = storage.upsert_event(make_event())

    events = await service.list_events(limit=10)
    loaded = await service.get_event(event_id)

    assert len(events) == 1
    assert loaded is not None and loaded.id == event_id


@pytest.mark.asyncio
async def test_data_clear_confirmation_is_operator_bound_one_shot_and_scope_exact(
    tmp_path,
):
    now = datetime(2026, 9, 29, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "clear-confirm.sqlite3")
    event_id = storage.upsert_event(make_event(source_key="clear-radar"))
    storage.import_real_questions(
        [
            {
                "source_name": "synthetic verified fixture",
                "exam_name": "synthetic exam",
                "exam_year": "2024",
                "question_number": "Q1",
                "source_locator": "synthetic page 1",
                "subject": "civil_law",
                "question_type": "single_choice",
                "stem": "synthetic question",
                "options": ["A", "B"],
                "answer": "A",
                "answer_source": "official",
                "verification_status": "verified",
            }
        ]
    )
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(operator_ids=["operator-1"]),
        clock=lambda: now,
    )

    denied = await service.prepare_data_clear("radar", actor_id="ordinary-user")
    prepared = await service.prepare_data_clear("radar", actor_id="operator-1")
    wrong_owner = await service.confirm_data_clear(
        prepared["token"], actor_id="operator-2"
    )
    assert denied["ready"] is False
    assert wrong_owner["success"] is False
    assert storage.count_events() == 1

    cleared = await service.confirm_data_clear(prepared["token"], actor_id="operator-1")
    replay = await service.confirm_data_clear(prepared["token"], actor_id="operator-1")
    assert cleared["success"] is True
    assert storage.count_events() == 0
    assert len(storage.list_real_questions(limit=None)) == 1
    assert replay["success"] is False
    assert replay["reason"] == "清理 token 无效或已使用"
    assert event_id not in {item.id for item in storage.list_events(limit=20)}
    storage.close()


@pytest.mark.asyncio
async def test_data_clear_expiry_and_stale_snapshot_never_delete_rows(tmp_path):
    now = datetime(2026, 9, 29, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "clear-safety.sqlite3")
    storage.upsert_event(make_event(source_key="clear-radar"))
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(operator_ids=["operator-1"]),
        clock=lambda: now,
    )

    stale = await service.prepare_data_clear("radar", actor_id="operator-1")
    storage.upsert_event(make_event(source_key="new-radar"))
    rejected_stale = await service.confirm_data_clear(
        stale["token"], actor_id="operator-1"
    )
    assert rejected_stale["success"] is False
    assert "发生变化" in rejected_stale["reason"]
    assert storage.count_events() == 2

    expired = await service.prepare_data_clear("radar", actor_id="operator-1")
    now += timedelta(minutes=11)
    rejected_expired = await service.confirm_data_clear(
        expired["token"], actor_id="operator-1"
    )
    assert rejected_expired["success"] is False
    assert rejected_expired["reason"] == "清理 token 已过期"
    assert storage.count_events() == 2
    storage.close()


@pytest.mark.asyncio
async def test_candidate_promotion_is_explicit_authorized_and_keeps_missing_answers_missing(
    tmp_path,
) -> None:
    from tests.test_structured_ingestion import _prepare_service

    storage, ingestion, prepared, _ = await _prepare_service(tmp_path)
    imported = await ingestion.confirm(prepared)
    candidate_id = storage.connection.execute(
        "SELECT id FROM learning_items WHERE identity = 'real_question_candidate'"
    ).fetchone()["id"]
    storage.connection.execute(
        "UPDATE learning_questions SET exam_name = '合成考试', answer_json = 'null', "
        "answer_source = 'not_provided' WHERE item_id = ?",
        (candidate_id,),
    )
    storage.connection.commit()
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        config=SimpleNamespace(operator_ids=["operator-1"]),
    )

    denied = await service.prepare_candidate_promotion(
        [candidate_id], actor_id="ordinary-user"
    )
    prepared_promotion = await service.prepare_candidate_promotion(
        [candidate_id, 999999], actor_id="operator-1"
    )

    assert imported["archived"] >= 1
    assert denied["ready"] is False
    assert prepared_promotion["promotable_count"] == 1
    assert prepared_promotion["selected_count"] == 2
    assert len(prepared_promotion["validation_failures"]) == 1
    assert prepared_promotion["unresolved_answer_count"] == 1
    assert prepared_promotion["items"][0]["normalized"]["answer"] is None
    before = storage.list_real_questions(limit=None)
    assert before == []

    wrong_actor = await service.confirm_candidate_promotion(
        prepared_promotion["token"], actor_id="other-operator"
    )
    confirmed = await service.confirm_candidate_promotion(
        prepared_promotion["token"], actor_id="operator-1"
    )

    assert wrong_actor["success"] is False
    assert confirmed["success"] is False
    assert confirmed["partial_success"] is True
    assert len(confirmed["results"]) == 2
    assert sum(item["success"] for item in confirmed["results"]) == 1
    promoted = storage.list_real_questions(limit=None)
    assert len(promoted) == 1
    assert promoted[0].answer is None
    assert promoted[0].answer_source == "not_provided"
    assert storage.connection.execute(
        "SELECT identity, verification_status FROM learning_items WHERE id = ?",
        (candidate_id,),
    ).fetchone()[:] == ("verified_real_question", "verified")
    assert storage.list_real_questions(limit=None)[0].id == promoted[0].id
    storage.close()


@pytest.mark.asyncio
async def test_candidate_promotion_does_not_overwrite_verified_identity_collision(
    tmp_path,
) -> None:
    from tests.test_structured_ingestion import _prepare_service

    storage, ingestion, prepared, _ = await _prepare_service(tmp_path)
    await ingestion.confirm(prepared)
    candidate_id = storage.connection.execute(
        "SELECT id FROM learning_items WHERE identity = 'real_question_candidate'"
    ).fetchone()["id"]
    storage.connection.execute(
        "UPDATE learning_questions SET exam_name = '合成考试', answer_json = 'null', "
        "answer_source = 'not_provided' WHERE item_id = ?",
        (candidate_id,),
    )
    storage.connection.commit()
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        config=SimpleNamespace(operator_ids=["operator-1"]),
    )
    preview = await service.prepare_candidate_promotion(
        [candidate_id], actor_id="operator-1"
    )
    candidate_mapping = preview["items"][0]["normalized"]
    candidate_mapping["stem"] = "已经存在但内容不同的合成题目"
    candidate_mapping["verification_status"] = "verified"
    storage.import_real_questions([candidate_mapping])
    existing = storage.list_real_questions(limit=None)[0]

    outcome = await service.confirm_candidate_promotion(
        preview["token"], actor_id="operator-1"
    )

    assert outcome["success"] is False
    assert outcome["results"][0]["success"] is False
    assert storage.list_real_questions(limit=None)[0].stem == existing.stem
    assert (
        storage.connection.execute(
            "SELECT identity FROM learning_items WHERE id = ?", (candidate_id,)
        ).fetchone()["identity"]
        == "real_question_candidate"
    )
    storage.close()


@pytest.mark.asyncio
async def test_candidate_promotion_keeps_structured_answer_unverified_not_official(
    tmp_path,
) -> None:
    from tests.test_structured_ingestion import _prepare_service

    storage, ingestion, prepared, _ = await _prepare_service(tmp_path)
    await ingestion.confirm(prepared)
    candidate_id = storage.connection.execute(
        "SELECT id FROM learning_items WHERE identity = 'real_question_candidate'"
    ).fetchone()["id"]
    storage.connection.execute(
        "UPDATE learning_questions SET exam_name = '合成考试' WHERE item_id = ?",
        (candidate_id,),
    )
    storage.connection.commit()
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        config=SimpleNamespace(operator_ids=["operator-1"]),
    )

    preview = await service.prepare_candidate_promotion(
        [candidate_id], actor_id="operator-1"
    )

    assert preview["promotable_count"] == 1
    normalized = preview["items"][0]["normalized"]
    assert normalized["answer"] == "应结合构成要件和证据分析。"
    assert normalized["answer_source"] == "unverified"
    confirmed = await service.confirm_candidate_promotion(
        preview["token"], actor_id="operator-1"
    )
    assert confirmed["success"] is True
    promoted = storage.list_real_questions(limit=None)[0]
    assert promoted.answer == "应结合构成要件和证据分析。"
    assert promoted.answer_source == "unverified"
    storage.close()


@pytest.mark.asyncio
async def test_library_batch_prepare_confirm_is_operator_bound_atomic_and_reversible(
    tmp_path,
) -> None:
    from tests.test_structured_ingestion import _prepare_service

    storage, ingestion, prepared, _ = await _prepare_service(tmp_path)
    await ingestion.confirm(prepared)
    item_id = int(
        storage.connection.execute(
            "SELECT id FROM learning_items WHERE identity='real_question_candidate'"
        ).fetchone()["id"]
    )
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        config=SimpleNamespace(operator_ids=["operator-1"]),
    )

    denied = await service.prepare_library_batch(
        "edit", [item_id], actor_id="ordinary", changes={"title": "不应写入"}
    )
    prepared_edit = await service.prepare_library_batch(
        "edit",
        [item_id],
        actor_id="operator-1",
        changes={"title": "批量编辑后的合成题"},
    )
    assert denied["ready"] is False
    assert prepared_edit["ready"] is True
    before = await service.get_management_learning_item(item_id, actor_id="operator-1")
    assert before["item"]["title"] != "批量编辑后的合成题"
    wrong_actor = await service.confirm_library_batch(
        prepared_edit["token"], actor_id="other"
    )
    assert wrong_actor["success"] is False
    changed = await service.confirm_library_batch(
        prepared_edit["token"], actor_id="operator-1"
    )
    replay = await service.confirm_library_batch(
        prepared_edit["token"], actor_id="operator-1"
    )
    assert changed["success"] is True
    assert replay["success"] is False
    edited = await service.get_management_learning_item(item_id, actor_id="operator-1")
    assert edited["item"]["title"] == "批量编辑后的合成题"

    delete_preview = await service.prepare_library_batch(
        "delete", [item_id], actor_id="operator-1"
    )
    assert (
        await service.confirm_library_batch(
            delete_preview["token"], actor_id="operator-1"
        )
    )["success"] is True
    safe_deleted = await service.get_learning_item(item_id)
    admin_deleted = await service.get_management_learning_item(
        item_id, actor_id="operator-1"
    )
    assert safe_deleted["success"] is False
    assert admin_deleted["item"]["active"] is False
    assert len(admin_deleted["sources"]) == 2

    restore_preview = await service.prepare_library_batch(
        "restore", [item_id], actor_id="operator-1"
    )
    assert (
        await service.confirm_library_batch(
            restore_preview["token"], actor_id="operator-1"
        )
    )["success"] is True
    restored = await service.get_management_learning_item(
        item_id, actor_id="operator-1"
    )
    assert restored["item"]["active"] is True
    assert len(restored["sources"]) == 2

    stale = await service.prepare_library_batch(
        "delete", [item_id], actor_id="operator-1"
    )
    storage.connection.execute(
        "UPDATE learning_items SET item_hash = item_hash || '-changed' WHERE id = ?",
        (item_id,),
    )
    storage.connection.commit()
    rejected = await service.confirm_library_batch(
        stale["token"], actor_id="operator-1"
    )
    assert rejected["success"] is False
    assert (
        storage.connection.execute(
            "SELECT active FROM learning_items WHERE id = ?", (item_id,)
        ).fetchone()["active"]
        == 1
    )
    storage.close()


@pytest.mark.asyncio
async def test_moderation_batches_lock_review_and_radar_ids_and_keep_truth_separate(
    tmp_path,
):
    from datetime import timezone

    from library_models import LibrarySource
    from library_repository import LibraryRepository
    from library_service import LibraryService

    now = datetime(2026, 9, 29, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "moderation-batch.sqlite3")
    first_event = make_event(source_key="radar-1")
    second_event = make_event(source_key="radar-2")
    first_id = storage.upsert_event(first_event)
    second_id = storage.upsert_event(second_event)
    repository = LibraryRepository(storage.connection)
    source_id = repository.ensure_source(
        LibrarySource(
            source_kind="synthetic_fixture",
            title="虚构复核来源",
            raw_text="仅供测试的虚构 evidence",
            source_url="https://example.test/review",
            content_hash="synthetic-review-hash",
            created_at=now.astimezone(timezone.utc),
            created_by="operator-1",
            session_origin="private:operator-1",
        )
    )
    review_id = repository.record_review_item(
        source_id=source_id,
        candidate_key="synthetic-review-1",
        material_type="note",
        locator="fixture:1",
        raw_fragment="虚构片段",
        proposed_structure={},
        review_reason="合成复核项",
        now=now,
    )
    service = LawAssistantService(
        storage,
        library_service=LibraryService(repository),
        config=SimpleNamespace(operator_ids=["operator-1"], timezone="Asia/Shanghai"),
        clock=lambda: now,
    )

    review_preview = await service.prepare_moderation_batch(
        "review", [review_id], {"status": "resolved"}, actor_id="operator-1"
    )
    assert review_preview["ready"] is True
    assert repository.get_review_item(review_id).status == "pending"
    denied = await service.confirm_moderation_batch(
        review_preview["token"], actor_id="other"
    )
    reviewed = await service.confirm_moderation_batch(
        review_preview["token"], actor_id="operator-1"
    )
    assert denied["success"] is False
    assert reviewed["success"] is True
    assert repository.get_review_item(review_id).status == "resolved"

    radar_preview = await service.prepare_moderation_batch(
        "radar",
        [first_id],
        {"status": "ignored", "reason": "合成批量忽略"},
        actor_id="operator-1",
    )
    assert radar_preview["ready"] is True
    assert storage.get_event_status_override(first_id) is None
    assert storage.get_event_status_override(second_id) is None
    changed = await service.confirm_moderation_batch(
        radar_preview["token"], actor_id="operator-1"
    )
    assert changed["success"] is True
    assert storage.get_event_status_override(first_id)["override_status"] == "ignored"
    assert storage.get_event_status_override(second_id) is None
    replay = await service.confirm_moderation_batch(
        radar_preview["token"], actor_id="operator-1"
    )
    assert replay["success"] is False

    clear_preview = await service.prepare_moderation_batch(
        "radar",
        [first_id],
        {"status": "auto", "reason": "恢复自动状态"},
        actor_id="operator-1",
    )
    cleared = await service.confirm_moderation_batch(
        clear_preview["token"], actor_id="operator-1"
    )
    assert cleared["success"] is True, cleared
    assert storage.get_event_status_override(first_id) is None
    assert storage.get_event(first_id).raw_content_hash == first_event.raw_content_hash
    assert (
        storage.get_event(second_id).raw_content_hash == second_event.raw_content_hash
    )
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()


@pytest.mark.asyncio
async def test_radar_batch_stale_second_row_rolls_back_first_row(tmp_path):

    now = datetime(2026, 9, 29, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "moderation-batch-stale.sqlite3")
    first_id = storage.upsert_event(make_event(source_key="batch-stale-1"))
    second_id = storage.upsert_event(make_event(source_key="batch-stale-2"))
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(operator_ids=["operator-1"], timezone="Asia/Shanghai"),
        clock=lambda: now,
    )
    preview = await service.prepare_moderation_batch(
        "radar",
        [first_id, second_id],
        {"status": "historical", "reason": "合成批量复核"},
        actor_id="operator-1",
    )
    assert preview["ready"] is True
    storage.set_event_status_override(
        second_id,
        "ignored",
        actor_id="operator-1",
        reason="并发变更",
        at=now.isoformat(),
    )

    result = await service.confirm_moderation_batch(
        preview["token"], actor_id="operator-1"
    )

    assert result["success"] is False
    assert storage.get_event_status_override(first_id) is None
    assert storage.get_event_status_override(second_id)["override_status"] == "ignored"
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()


@pytest.mark.asyncio
async def test_unchanged_source_document_skips_duplicate_extraction(tmp_path) -> None:
    adapter = FakeAdapter("fake", [document("fake", "item-1")])

    class CountingExtractor(FakeExtractor):
        def __init__(self):
            super().__init__({"item-1": [make_event()]})
            self.calls = 0

        async def extract(self, source_document):
            self.calls += 1
            return await super().extract(source_document)

    extractor = CountingExtractor()
    service = LawAssistantService(
        SQLiteStorage(tmp_path / "runtime.sqlite3"),
        sources=[(adapter, extractor)],
    )

    await service.scan_events()
    await service.scan_events()

    assert extractor.calls == 1
    assert service.storage.count_events() == 1
    assert service.storage.get_event_by_key("fake", "item-1").last_seen_at is not None


@pytest.mark.asyncio
async def test_case_clear_allows_unchanged_official_source_to_rebuild_case(tmp_path):
    from library_repository import LibraryRepository
    from library_service import LibraryService

    storage = SQLiteStorage(tmp_path / "case-clear-rescan.sqlite3")
    library = LibraryService(LibraryRepository(storage.connection))
    source_document = SourceDocument(
        source_key="court_cases",
        source_item_key="case-clear-1",
        url="https://court.example/case-clear-1",
        title="张三商标侵权案",
        content=(
            "张三商标侵权案\n"
            "基本案情：甲公司主张商标侵权，双方发生争议。\n"
            "裁判要旨：法院结合证据作出裁判。"
        ),
        fetched_at="2026-09-30T00:00:00+00:00",
    )

    class Extractor:
        calls = 0

        async def extract(self, document):
            self.calls += 1
            return CaseItem(
                source_key=document.source_key,
                source_item_key=document.source_item_key,
                title=document.title,
                source_url=document.url,
                authority="最高人民法院",
                raw_text=document.content,
                content_hash=document.content_hash,
                discovered_at=document.fetched_at,
                last_seen_at=document.fetched_at,
            )

    extractor = Extractor()
    service = LawAssistantService(
        storage,
        case_sources=[
            (FakeAdapter("court_cases", [source_document]), extractor),
        ],
        library_service=library,
        config=SimpleNamespace(operator_ids=["operator-1"]),
    )

    first = await service.scan_cases()
    assert first["failures"] == []
    assert (await library.list_official_cases())["count"] == 1
    assert extractor.calls == 1

    preview = await service.prepare_data_clear("cases", actor_id="operator-1")
    cleared = await service.confirm_data_clear(preview["token"], actor_id="operator-1")
    assert cleared["success"] is True
    assert (await library.list_official_cases())["count"] == 0

    second = await service.scan_cases()

    assert second["failures"] == []
    assert extractor.calls == 2
    assert (await library.list_official_cases())["count"] == 1
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()


@pytest.mark.asyncio
async def test_user_case_verification_is_operator_controlled_and_never_official(
    tmp_path,
):
    from tests.test_structured_ingestion import _prepare_service

    storage, ingestion, prepared, _ = await _prepare_service(tmp_path)
    await ingestion.confirm(prepared)
    case_id = int(
        storage.connection.execute(
            "SELECT id FROM learning_items WHERE item_type='case'"
        ).fetchone()[0]
    )
    question_id = int(
        storage.connection.execute(
            "SELECT id FROM learning_items WHERE item_type='question'"
        ).fetchone()[0]
    )
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        config=SimpleNamespace(operator_ids=["operator-1"]),
    )

    denied = await service.prepare_library_batch(
        "verify_case",
        [case_id],
        actor_id="ordinary-user",
        changes={"verification_status": "user_verified"},
    )
    assert denied["ready"] is False
    assert denied["error"] == "forbidden"

    before = await ingestion.library_service.get_management_item(case_id)
    assert before["item"]["identity"] == "user_case"
    assert before["item"]["verification_status"] == "pending_review"
    with pytest.raises(ValueError, match="不允许修改字段"):
        ingestion.library_service.normalize_management_changes(
            case_id, {"identity": "official_case"}
        )
    invalid_type = await service.prepare_library_batch(
        "verify_case",
        [question_id],
        actor_id="operator-1",
        changes={"verification_status": "user_verified"},
    )
    assert invalid_type["ready"] is False
    assert "user_case" in invalid_type["reason"]

    preview = await service.prepare_library_batch(
        "verify_case",
        [case_id],
        actor_id="operator-1",
        changes={"verification_status": "user_verified"},
    )
    assert preview["ready"] is True
    assert preview["items"][0]["verification_after"] == "user_verified"
    assert (await ingestion.library_service.get_management_item(case_id))["item"][
        "verification_status"
    ] == "pending_review"
    confirmed = await service.confirm_library_batch(
        preview["token"], actor_id="operator-1"
    )
    assert confirmed["success"] is True
    after = await ingestion.library_service.get_management_item(case_id)
    assert after["item"]["identity"] == "user_case"
    assert after["item"]["verification_status"] == "user_verified"
    audit = storage.connection.execute(
        "SELECT action, scope, outcome_json FROM operator_action_audits "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert audit["action"] == "library_batch_verify_case"
    assert audit["scope"] == "learning_item"
    revoke = await service.prepare_library_batch(
        "verify_case",
        [case_id],
        actor_id="operator-1",
        changes={"verification_status": "unverified"},
    )
    assert revoke["ready"] is True
    assert (
        await service.confirm_library_batch(revoke["token"], actor_id="operator-1")
    )["success"] is True
    after_revoke = await ingestion.library_service.get_management_item(case_id)
    assert after_revoke["item"]["identity"] == "user_case"
    assert after_revoke["item"]["verification_status"] == "unverified"
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()


@pytest.mark.asyncio
async def test_failed_event_processing_does_not_consume_new_source_hash(
    tmp_path,
) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    old_document = SourceDocument(
        source_key="fake",
        source_item_key="item-1",
        url="https://example.test/item-1",
        title="Old document",
        content="old content",
        fetched_at="2026-09-16T00:00:00+00:00",
    )
    storage.upsert_source_document(old_document)
    storage.upsert_event(make_event())
    new_document = document("fake", "item-1")

    class RetryExtractor:
        calls = 0

        async def extract(self, source_document):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary extraction failure")
            return [
                replace(
                    make_event(title="Recovered event"),
                    raw_content_hash=source_document.content_hash,
                )
            ]

    extractor = RetryExtractor()
    service = LawAssistantService(
        storage,
        sources=[(FakeAdapter("fake", [new_document]), extractor)],
    )

    first = await service.scan_events()
    assert len(first.failures) == 1
    assert storage.get_source_document("fake", "item-1").content_hash == (
        old_document.content_hash
    )

    second = await service.scan_events()

    assert second.failures == ()
    assert extractor.calls == 2
    assert storage.get_source_document("fake", "item-1").content_hash == (
        new_document.content_hash
    )
    assert storage.get_event_by_key("fake", "item-1").title == "Recovered event"


@pytest.mark.asyncio
async def test_failed_secondary_processing_can_retry_same_source_hash(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    old_document = SourceDocument(
        source_key="court_cases",
        source_item_key="case-1",
        url="https://court.example/case-1",
        title="Old case",
        content="old case content",
        fetched_at="2026-09-16T00:00:00+00:00",
    )
    storage.upsert_source_document(old_document)
    new_document = SourceDocument(
        source_key="court_cases",
        source_item_key="case-1",
        url="https://court.example/case-1",
        title="New case",
        content="new case content",
        fetched_at="2026-09-17T00:00:00+00:00",
    )

    class RetryCaseExtractor:
        calls = 0

        async def extract(self, source_document):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary case extraction failure")
            return CaseItem(
                source_key=source_document.source_key,
                source_item_key=source_document.source_item_key,
                title="Recovered case",
                source_url=source_document.url,
                authority="最高人民法院",
                raw_text=source_document.content,
                content_hash=source_document.content_hash,
                discovered_at=source_document.fetched_at,
                last_seen_at=source_document.fetched_at,
            )

    extractor = RetryCaseExtractor()
    service = LawAssistantService(
        storage,
        case_sources=[(FakeAdapter("court_cases", [new_document]), extractor)],
    )

    first = await service.scan_cases()
    assert len(first["failures"]) == 1
    assert storage.get_source_document("court_cases", "case-1").content_hash == (
        old_document.content_hash
    )

    second = await service.scan_cases()

    assert second["failures"] == []
    assert extractor.calls == 2
    assert storage.get_source_document("court_cases", "case-1").content_hash == (
        new_document.content_hash
    )
    assert storage.list_case_items()[0].title == "Recovered case"


@pytest.mark.asyncio
async def test_publish_requires_confirmation_and_is_idempotent(tmp_path) -> None:
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    publisher = RecordingPublisher()
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    event_id = storage.upsert_event(
        replace(
            make_event(),
            discovered_at=now,
            updated_at=now,
            dates=(
                replace(
                    make_event().dates[0],
                    datetime=datetime(
                        2026, 10, 8, 18, tzinfo=ZoneInfo("Asia/Shanghai")
                    ),
                ),
                replace(
                    make_event().dates[0],
                    kind="other",
                    label="未确认辅助时间",
                    confirmed=False,
                ),
            ),
        )
    )
    service = LawAssistantService(
        storage,
        publisher=publisher,
        config=SimpleNamespace(
            auto_publish_events=True,
            timezone="Asia/Shanghai",
            radar_auto_publish_sources=("fake",),
        ),
        clock=lambda: now,
    )
    await service.bind_target("aiocqhttp:group:100", "测试群")

    preview = await service.prepare_publish_event(event_id)
    assert preview["ready"] is True
    assert "未确认辅助时间" not in preview["preview"]
    assert publisher.calls == []
    assert (await service.confirm_publish(preview["token"]))["success"] is True
    assert (await service.confirm_publish(preview["token"]))["success"] is False
    assert len(publisher.calls) == 1
    assert len(storage.list_publications()) == 1
    assert await service._publish_event_to_targets(event_id, kind="automatic") == 1
    assert len(publisher.calls) == 2


@pytest.mark.asyncio
async def test_deadline_reminder_is_sent_once_per_day_and_target(tmp_path) -> None:
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    event = replace(
        make_event(),
        discovered_at=now,
        updated_at=now,
        dates=(
            replace(
                make_event().dates[0],
                datetime=now,
            ),
        ),
    )
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    storage.upsert_event(event)
    publisher = RecordingPublisher()
    service = LawAssistantService(storage, publisher=publisher, clock=lambda: now)
    await service.bind_target("aiocqhttp:group:100", "测试群")

    assert await service.check_deadline_reminders(now=now) == 1
    assert await service.check_deadline_reminders(now=now) == 0
    assert len(storage.list_reminders()) == 1


@pytest.mark.asyncio
async def test_deadline_reminder_survives_unrelated_event_revision(tmp_path) -> None:
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    event = replace(
        make_event(),
        discovered_at=now,
        updated_at=now,
        dates=(replace(make_event().dates[0], datetime=now),),
    )
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    event_id = storage.upsert_event(event)
    publisher = RecordingPublisher()
    service = LawAssistantService(storage, publisher=publisher, clock=lambda: now)
    await service.bind_target("aiocqhttp:group:100", "测试群")

    assert await service.check_deadline_reminders(now=now) == 1
    revised = replace(event, title="Updated title", updated_at=now)
    storage.upsert_event(revised)

    assert storage.get_event(event_id).revision == 2
    assert await service.check_deadline_reminders(now=now) == 0
    assert len(storage.list_reminders()) == 1
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_changed_deadline_creates_new_reminder_identity(tmp_path) -> None:
    now = datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    first_date = replace(make_event().dates[0], datetime=now)
    event = replace(
        make_event(), discovered_at=now, updated_at=now, dates=(first_date,)
    )
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    event_id = storage.upsert_event(event)
    publisher = RecordingPublisher()
    service = LawAssistantService(storage, publisher=publisher, clock=lambda: now)
    await service.bind_target("aiocqhttp:group:100", "测试群")

    assert await service.check_deadline_reminders(now=now) == 1
    changed = replace(
        event,
        dates=(replace(first_date, datetime=now.replace(day=2)),),
        updated_at=now,
    )
    storage.upsert_event(changed)

    assert storage.get_event(event_id).revision == 2
    assert await service.check_deadline_reminders(now=now) == 1
    reminders = storage.list_reminders()
    assert len(reminders) == 2
    assert {item["deadline_value"] for item in reminders} == {
        now.isoformat(),
        now.replace(day=2).isoformat(),
    }


@pytest.mark.asyncio
async def test_daily_content_is_idempotent_per_local_day_and_target(tmp_path) -> None:
    now = datetime(2026, 10, 1, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    storage.upsert_case_item(
        CaseItem(
            source_key="court_cases",
            source_item_key="1",
            title="典型案例",
            source_url="https://court.example/1",
            authority="最高人民法院",
            raw_text="官方案例正文",
            content_hash="case-hash",
        )
    )

    class Learning:
        async def daily_case(self, **kwargs):
            return {"available": True, "content": {"case_summary": "摘要"}}

        async def generate_question(self, **kwargs):
            return {"available": False}

    config = SimpleNamespace(
        timezone="Asia/Shanghai",
        auto_scan_enabled=False,
        daily_case_enabled=True,
        daily_case_time="08:00",
        daily_question_enabled=False,
    )
    publisher = RecordingPublisher()
    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=config,
        clock=lambda: now,
    )

    async def unexpected_event_scan(*args, **kwargs):
        raise AssertionError("daily case must not force an event scan")

    service.scan_events = unexpected_event_scan
    await service.bind_target("aiocqhttp:group:100", "测试群")

    first = await service.run_scheduled_jobs(now=now)
    second = await service.run_scheduled_jobs(now=now)

    assert first["daily_case_sent"] == 1
    assert second["daily_case_sent"] == 0
    assert "events" not in first
    assert len(storage.list_daily_contents()) == 1


@pytest.mark.asyncio
async def test_daily_question_uses_independent_subject_and_type_resolver_and_history(
    tmp_path,
) -> None:
    now = datetime(2026, 10, 2, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    calls: list[dict[str, object]] = []

    class Learning:
        async def generate_question(self, **kwargs):
            calls.append(kwargs)
            return {
                "available": True,
                "origin": "mock",
                "subject": kwargs["subject"],
                "question_type": kwargs["question_type"],
                "question_id": 42,
                "source_kind": "library_mock",
                "source_item_key": "42",
                "content": {
                    "question": "每日题目",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析",
                },
            }

    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: now,
    )
    target = await service.bind_target("aiocqhttp:group:100", "测试群")
    storage.upsert_daily_plan(
        DailyPlan(
            content_type="daily_question",
            enabled=True,
            time="08:00",
            selection_mode="rotation",
            rotation_subjects=("intellectual_property", "economic_law"),
            rotation_start_date="2026-10-01",
            question_origin="mock",
            question_type_selection_mode="rotation",
            rotation_question_types=("single_choice", "multiple_choice", "true_false"),
            question_type_rotation_start_date="2026-10-02",
        ),
        target["id"],
    )

    result = await service.run_scheduled_jobs(now=now)

    assert result["daily_question_sent"] == 1
    assert len(calls) == 1
    assert {key: calls[0][key] for key in ("subject", "origin", "question_type")} == {
        "subject": "economic_law",
        "origin": "mock",
        "question_type": "single_choice",
    }
    history = storage.list_daily_contents()[0]
    assert history["source_kind"] == "library_mock"
    assert history["source_item_key"] == "42"
    assert history["resolved_subject"] == "economic_law"
    assert history["resolved_question_type"] == "single_choice"
    assert history["resolved_origin"] == "mock"


@pytest.mark.asyncio
async def test_daily_question_schedules_reveals_only_after_successful_prompt(tmp_path):
    from publisher import PublishOutcome

    now = datetime(2026, 10, 2, 8, tzinfo=ZoneInfo("Asia/Shanghai"))

    class Learning:
        async def generate_question(self, **kwargs):
            return {
                "available": True,
                "origin": "mock",
                "subject": "criminal_law",
                "question_type": "short_answer",
                "question_id": None,
                "content": {
                    "question": "合成题干：甲的行为如何评价？",
                    "answer": "答案段落。" * 180,
                    "explanation": "解析段落。" * 180,
                },
            }

    class OutcomePublisher:
        def __init__(self, first):
            self.outcomes = [first]
            self.calls = []

        async def publish_text(self, destination, text):
            self.calls.append((destination, text))
            if self.outcomes:
                return self.outcomes.pop(0)
            return PublishOutcome("sent")

    async def build_service(path, first_outcome):
        storage = SQLiteStorage(path)
        publisher = OutcomePublisher(first_outcome)
        service = LawAssistantService(
            storage,
            publisher=publisher,
            learning_service=Learning(),
            config=SimpleNamespace(
                timezone="Asia/Shanghai", question_message_max_chars=300
            ),
            clock=lambda: now,
        )
        target = await service.bind_target("aiocqhttp:group:601", "一群")
        storage.upsert_daily_plan(
            DailyPlan.from_mapping(
                "daily_question",
                {
                    "enabled": True,
                    "time": "08:00",
                    "question_origin": "mock",
                    "question_reveal_mode": "delayed",
                    "answer_reveal_delay_minutes": 20,
                    "explanation_reveal_delay_minutes": 35,
                },
            ),
            target["id"],
        )
        return storage, service, publisher

    failed_storage, failed_service, _failed_publisher = await build_service(
        tmp_path / "failed-prompt.sqlite3", PublishOutcome("failed", "offline")
    )
    failed_result = await failed_service.process_due_work(now=now)
    assert failed_result["daily_question_sent"] == 0
    assert (
        failed_storage.connection.execute(
            "SELECT COUNT(*) FROM question_sessions"
        ).fetchone()[0]
        == 0
    )
    assert (
        failed_storage.connection.execute(
            "SELECT COUNT(*) FROM scheduled_reveal_jobs"
        ).fetchone()[0]
        == 0
    )
    failed_storage.close()

    storage, service, publisher = await build_service(
        tmp_path / "successful-prompt.sqlite3", PublishOutcome("sent")
    )
    sent = await service.process_due_work(now=now)
    assert sent["daily_question_sent"] == 1
    jobs = storage.connection.execute(
        "SELECT * FROM scheduled_reveal_jobs ORDER BY reveal_kind"
    ).fetchall()
    assert {row["reveal_kind"] for row in jobs} == {"answer", "explanation"}
    by_kind = {row["reveal_kind"]: row for row in jobs}
    session = storage.connection.execute("SELECT * FROM question_sessions").fetchone()
    assert by_kind["answer"]["session_id"] == session["id"]
    assert by_kind["answer"]["snapshot_hash"] == session["question_snapshot_hash"]
    assert by_kind["answer"]["target_umo"] == "aiocqhttp:group:601"
    assert datetime.fromisoformat(
        by_kind["answer"]["due_at"]
    ) == datetime.fromisoformat(by_kind["answer"]["question_sent_at"]) + timedelta(
        minutes=20
    )
    assert datetime.fromisoformat(
        by_kind["explanation"]["due_at"]
    ) == datetime.fromisoformat(by_kind["explanation"]["question_sent_at"]) + timedelta(
        minutes=35
    )

    answer_due = datetime.fromisoformat(by_kind["answer"]["due_at"])
    reveal_result = await service.process_due_reveals(now=answer_due)
    answer_job = service.scheduled_reveals.get(by_kind["answer"]["id"])
    assert reveal_result["sent"] == 1, (
        reveal_result,
        service.scheduled_reveals.get(by_kind["answer"]["id"]),
        publisher.calls,
    )
    assert answer_job["status"] == "sent"
    answer_messages = [text for _, text in publisher.calls[1:]]
    assert len(answer_messages) > 1
    assert all(len(text) <= 300 for text in answer_messages)
    assert all("解析段落。" not in text for text in answer_messages)
    assert all("答案段落。" in text for text in answer_messages)
    assert answer_job["page_progress"] == list(range(len(answer_messages)))
    assert await service.process_due_reveals(now=answer_due) == {
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "needs_review": 0,
    }
    storage.close()


@pytest.mark.asyncio
async def test_manual_reveal_skips_pending_job_and_closed_session_is_not_sent(tmp_path):

    now = datetime(2026, 10, 2, 8, tzinfo=ZoneInfo("Asia/Shanghai"))

    class Learning:
        async def generate_question(self, **kwargs):
            return {
                "available": True,
                "origin": "mock",
                "subject": "criminal_law",
                "question_type": "single_choice",
                "content": {
                    "question": "合成选择题？",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析。",
                },
            }

    storage = SQLiteStorage(tmp_path / "reveal-skip.sqlite3")
    publisher = RecordingPublisher()
    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: now,
    )
    target = await service.bind_target("aiocqhttp:group:602", "二群")
    storage.upsert_daily_plan(
        DailyPlan.from_mapping(
            "daily_question",
            {
                "enabled": True,
                "time": "08:00",
                "question_origin": "mock",
                "question_reveal_mode": "delayed",
                "answer_reveal_delay_minutes": 20,
                "explanation_reveal_delay_minutes": 35,
            },
        ),
        target["id"],
    )
    await service.process_due_work(now=now)
    answer_job = storage.connection.execute(
        "SELECT * FROM scheduled_reveal_jobs WHERE reveal_kind='answer'"
    ).fetchone()
    explanation_job = storage.connection.execute(
        "SELECT * FROM scheduled_reveal_jobs WHERE reveal_kind='explanation'"
    ).fetchone()
    assert answer_job is not None and explanation_job is not None
    assert answer_job["status"] == explanation_job["status"] == "pending"

    manual = await service.question_session_action(
        "answer", session_origin="aiocqhttp:group:602", actor_id="42"
    )
    assert "A" in manual["text"]
    assert service.scheduled_reveals.get(answer_job["id"])["status"] == "skipped"
    assert service.scheduled_reveals.get(explanation_job["id"])["status"] == "pending"

    await service.question_session_action(
        "close", session_origin="aiocqhttp:group:602", actor_id="42"
    )
    before = len(publisher.calls)
    result = await service.process_due_reveals(
        now=datetime.fromisoformat(explanation_job["due_at"])
    )
    assert result["skipped"] == 1
    assert len(publisher.calls) == before
    storage.close()


def test_due_scheduler_resolves_earliest_local_plan_and_skips_completed_occurrence(
    tmp_path,
):
    now = datetime(2026, 9, 29, 7, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "due-plan.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: now,
    )
    target = storage.bind_target("aiocqhttp:group:100", "一群")
    storage.upsert_daily_plan(
        DailyPlan.from_mapping("daily_case", {"enabled": True, "time": "08:00"}),
        target["id"],
    )
    storage.upsert_daily_plan(
        DailyPlan.from_mapping("daily_question", {"enabled": True, "time": "08:30"}),
        target["id"],
    )

    assert service.has_due_scheduler_work() is True
    assert service.next_due_at().isoformat() == "2026-09-29T08:00:00+08:00"
    storage.record_daily_skip(
        content_date="2026-09-29",
        target_id=target["id"],
        content_type="daily_case",
        reason="synthetic no matching case",
    )
    assert service.next_due_at().isoformat() == "2026-09-29T08:30:00+08:00"


@pytest.mark.asyncio
async def test_confirmed_plan_change_wakes_scheduler_only_after_persistence(tmp_path):
    storage = SQLiteStorage(tmp_path / "wake-plan.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: datetime(2026, 9, 29, 0, tzinfo=ZoneInfo("UTC")),
    )
    target = await service.bind_target("aiocqhttp:group:100", "一群")
    wake_calls = []

    async def wake():
        wake_calls.append(True)

    service.set_scheduler_wakeup(wake)
    prepared = await service.prepare_daily_plan_update(
        target_selectors=["一群"],
        content_type="daily_question",
        changes={"enabled": True, "time": "08:00"},
        actor_id="operator-1",
    )
    assert prepared["ready"] is True
    assert wake_calls == []
    assert storage.get_daily_plan(target["id"], "daily_question") is None

    confirmed = await service.confirm_daily_plan_update(
        prepared["token"], actor_id="operator-1"
    )

    assert confirmed["success"] is True
    assert storage.get_daily_plan(target["id"], "daily_question").enabled is True
    assert wake_calls == [True]


@pytest.mark.asyncio
async def test_binding_target_wakes_due_scheduler_for_global_daily_question(tmp_path):
    now = datetime(2026, 9, 30, 8, tzinfo=ZoneInfo("Asia/Shanghai"))

    class Learning:
        async def generate_question(self, **kwargs):
            return {
                "available": True,
                "origin": "mock",
                "subject": "criminal_law",
                "question_type": "single_choice",
                "content": {
                    "question": "合成测试题：甲的行为如何评价？",
                    "options": ["A. 选项一", "B. 选项二"],
                    "answer": "A",
                    "explanation": "合成解析。",
                },
            }

    class WaitingPublisher:
        def __init__(self):
            self.calls = []
            self.sent = asyncio.Event()

        async def publish_text(self, destination, text):
            self.calls.append((destination, text))
            self.sent.set()
            return True

    async def sleep_forever(_seconds):
        await asyncio.Future()

    storage = SQLiteStorage(tmp_path / "bind-wakes-scheduler.sqlite3")
    publisher = WaitingPublisher()
    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=SimpleNamespace(
            timezone="Asia/Shanghai",
            daily_question_enabled=True,
            daily_question_time="08:00",
            daily_question_origin="mock",
            question_message_max_chars=1200,
        ),
        clock=lambda: now,
    )
    scheduler = LawAssistantScheduler(
        service,
        enabled=False,
        scan_enabled=False,
        due_enabled=False,
        interval_minutes=5,
        sleep=sleep_forever,
    )
    await scheduler.start()
    service.set_scheduler_wakeup(scheduler.wake)

    assert service.has_due_scheduler_work() is False
    assert scheduler.due_task is None

    target = await service.bind_target("aiocqhttp:group:bind-wake", "一群")

    await asyncio.wait_for(publisher.sent.wait(), timeout=1)
    for _ in range(20):
        history = storage.list_daily_contents()
        if history and history[0]["status"] == "sent":
            break
        await asyncio.sleep(0)

    assert scheduler.due_task is not None
    assert publisher.calls and publisher.calls[0][0] == target["unified_msg_origin"]
    assert len(publisher.calls) == 1
    assert history[0]["status"] == "sent"
    assert history[0]["intended_local_at"] == (
        "2026-09-30T08:00:00+08:00[Asia/Shanghai]"
    )

    await service.process_due_work(now=now)
    assert len(publisher.calls) == 1

    assert await service.unbind_target(target["unified_msg_origin"]) is True
    for _ in range(20):
        if scheduler.due_task is None:
            break
        await asyncio.sleep(0)
    assert scheduler.due_task is None

    reenabled = await service.bind_target(target["unified_msg_origin"], "一群")
    assert reenabled["enabled"] is True
    assert scheduler.due_task is not None
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(publisher.calls) == 1

    await scheduler.stop()
    storage.close()


@pytest.mark.asyncio
async def test_verified_linked_question_management_syncs_selection_and_preserves_sessions(
    tmp_path,
):
    storage, service, item_id, provider = await _promoted_synthetic_question(tmp_path)
    before = await provider.select_question(
        origin="real", subject="intellectual_property"
    )
    assert before["available"] is True
    original_stem = before["content"]["question"]
    direct_disable = await service.set_management_real_question_active(
        before["question_id"], active=False, actor_id="operator-1"
    )
    assert direct_disable["success"] is False
    assert "关联学习资料" in direct_disable["message"]
    session = service.open_question_session(
        before,
        session_origin="aiocqhttp:private:operator-1",
        actor_id="operator-1",
    )
    assert session["success"] is True
    persisted_before = service.question_sessions.get(session["session_id"])
    before_hash = persisted_before["snapshot_hash"]

    denied = await service.update_management_learning_item(
        item_id, {"stem": "越权修改"}, actor_id="ordinary-user"
    )
    assert denied["error"] == "forbidden"
    updated = await service.update_management_learning_item(
        item_id,
        {
            "subjects": ["criminal_law"],
            "stem": "管理员核验后的合成题干。",
            "answer": "修改后的合成答案。",
            "answer_source": "user_verified",
        },
        actor_id="operator-1",
    )
    assert updated["success"] is True

    edited = await provider.select_question(origin="real", subject="criminal_law")
    assert edited["available"] is True
    assert edited["content"]["question"] == "管理员核验后的合成题干。"
    assert edited["content"]["answer"] == "修改后的合成答案。"
    assert (
        await provider.select_question(origin="real", subject="intellectual_property")
    )["available"] is False

    after_edit_session = service.question_sessions.get(session["session_id"])
    assert after_edit_session["snapshot_hash"] == before_hash
    assert original_stem in json.dumps(
        after_edit_session["snapshot"], ensure_ascii=False
    )
    assert "管理员核验后的合成题干。" not in json.dumps(
        after_edit_session["snapshot"], ensure_ascii=False
    )

    deleted = await service.soft_delete_learning_items([item_id], actor_id="operator-1")
    assert deleted["success"] is True
    assert (await provider.select_question(origin="real", subject="criminal_law"))[
        "available"
    ] is False

    restored = await service.restore_learning_items([item_id], actor_id="operator-1")
    assert restored["success"] is True
    reverified = await provider.select_question(origin="real", subject="criminal_law")
    assert reverified["available"] is True
    assert reverified["content"]["question"] == "管理员核验后的合成题干。"
    assert (
        service.question_sessions.get(session["session_id"])["snapshot_hash"]
        == before_hash
    )
    storage.close()


@pytest.mark.asyncio
async def test_verified_linked_question_identity_collision_rolls_back_both_views(
    tmp_path,
):
    storage, service, item_id, _provider = await _promoted_synthetic_question(tmp_path)
    linked = storage.list_real_questions(limit=None)[0]
    collision = linked.to_mapping()
    collision.update(
        {
            "exam_year": "2099",
            "paper": "碰撞合成卷",
            "question_number": "99",
        }
    )
    storage.import_real_questions([collision])
    current_item = service.library_service.repository.get(item_id)
    current_real = storage.connection.execute(
        "SELECT * FROM real_questions WHERE id = ?", (linked.id,)
    ).fetchone()

    result = await service.update_management_learning_item(
        item_id,
        {
            "exam_year": "2099",
            "paper": "碰撞合成卷",
            "question_number": "99",
        },
        actor_id="operator-1",
    )

    assert result["success"] is False
    assert "冲突" in result["message"] or "已存在" in result["message"]
    after_item = service.library_service.repository.get(item_id)
    after_real = storage.connection.execute(
        "SELECT * FROM real_questions WHERE id = ?", (linked.id,)
    ).fetchone()
    assert after_item.question.question_number == current_item.question.question_number
    assert after_real["identity_key"] == current_real["identity_key"]
    assert after_real["exam_year"] == current_real["exam_year"]
    assert len(storage.list_real_questions(limit=None)) == 2
    storage.close()


@pytest.mark.asyncio
async def test_linked_question_batch_subject_edit_updates_verified_inventory(tmp_path):
    storage, service, item_id, provider = await _promoted_synthetic_question(tmp_path)
    preview = await service.prepare_library_batch(
        "edit",
        [item_id],
        actor_id="operator-1",
        changes={"subjects": ["criminal_law"]},
    )
    assert preview["ready"] is True
    result = await service.confirm_library_batch(
        preview["token"], actor_id="operator-1"
    )
    assert result["success"] is True
    selected = await provider.select_question(origin="real", subject="criminal_law")
    assert selected["available"] is True
    assert (
        storage.list_real_questions(subject="criminal_law", limit=None)[0].subject
        == "criminal_law"
    )
    assert (
        await provider.select_question(origin="real", subject="intellectual_property")
    )["available"] is False
    storage.close()


@pytest.mark.asyncio
async def test_ordinary_linked_question_update_synchronizes_subject_and_explanation(
    tmp_path,
):
    storage, service, item_id, provider = await _promoted_synthetic_question(tmp_path)
    result = await service.update_learning_item(
        item_id,
        {"subjects": ["criminal_law"], "explanation": "统一更新的合成解析。"},
    )
    assert result["success"] is True
    selected = await provider.select_question(origin="real", subject="criminal_law")
    assert selected["available"] is True
    assert selected["content"]["explanation"] == "统一更新的合成解析。"
    assert (
        await provider.select_question(origin="real", subject="intellectual_property")
    )["available"] is False
    storage.close()


@pytest.mark.asyncio
async def test_linked_question_edit_updates_all_normalized_projections_atomically(
    tmp_path,
):
    storage, service, first_id, provider = await _promoted_synthetic_question(tmp_path)
    first_binding = storage.connection.execute(
        "SELECT * FROM structured_item_bindings WHERE item_id = ? ORDER BY id DESC LIMIT 1",
        (first_id,),
    ).fetchone()
    second = await service.library_service.archive_learning_material(
        raw_text="第二份合成来源中的同一真题。",
        material_type="real_question_candidate",
        title="第二投影",
        subjects=["intellectual_property"],
        structured_json={
            "question_type": "single_choice",
            "stem": service.library_service.repository.get(first_id).question.stem,
            "options": list(
                service.library_service.repository.get(first_id).question.options
            ),
            "answer": service.library_service.repository.get(first_id).question.answer,
            "answer_source": "user_verified",
            "exam_name": "合成考试",
            "exam_year": service.library_service.repository.get(
                first_id
            ).question.exam_year,
            "paper": service.library_service.repository.get(first_id).question.paper,
            "question_number": service.library_service.repository.get(
                first_id
            ).question.question_number,
        },
        created_by="operator-1",
        session_origin="private:operator-1",
    )
    second_id = second["item_id"]
    storage.connection.execute(
        "UPDATE learning_items SET identity='verified_real_question', verification_status='verified' WHERE id=?",
        (second_id,),
    )
    storage.connection.execute(
        "UPDATE learning_questions SET exam_name='合成考试', exam_year=?, paper=?, question_number=? WHERE item_id=?",
        (
            service.library_service.repository.get(first_id).question.exam_year,
            service.library_service.repository.get(first_id).question.paper,
            service.library_service.repository.get(first_id).question.question_number,
            second_id,
        ),
    )
    storage.connection.execute(
        "INSERT INTO structured_item_bindings(import_id,item_id,external_id,source_number,item_kind,structure_version,review_status,payload_json,metadata_json,verified_real_question_id,promoted_by,promoted_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            first_binding["import_id"],
            second_id,
            "second-projection",
            "Q2",
            "question",
            first_binding["structure_version"],
            "resolved",
            first_binding["payload_json"],
            first_binding["metadata_json"],
            first_binding["verified_real_question_id"],
            "operator-1",
            "2026-09-30T00:00:00+00:00",
        ),
    )
    storage.connection.commit()
    original_binding_payload = storage.connection.execute(
        "SELECT payload_json FROM structured_item_bindings WHERE item_id=?",
        (first_id,),
    ).fetchone()[0]
    original_source_rows = [
        tuple(row)
        for row in storage.connection.execute(
            "SELECT s.id,s.raw_text,s.source_url,s.content_hash FROM library_sources s "
            "JOIN learning_item_sources x ON x.source_id=s.id WHERE x.item_id=? ORDER BY s.id",
            (first_id,),
        ).fetchall()
    ]

    updated = await service.update_management_learning_item(
        first_id,
        {"subjects": ["criminal_law"], "stem": "两个投影共同更新的题干？"},
        actor_id="operator-1",
    )
    assert updated["success"] is True
    first = service.library_service.repository.get(first_id)
    second = service.library_service.repository.get(second_id)
    assert first.question.stem == second.question.stem == "两个投影共同更新的题干？"
    assert first.item.subjects == second.item.subjects == ("criminal_law",)
    assert (
        storage.connection.execute(
            "SELECT payload_json FROM structured_item_bindings WHERE item_id=? ORDER BY id DESC LIMIT 1",
            (first_id,),
        ).fetchone()[0]
        == original_binding_payload
    )
    assert [
        tuple(row)
        for row in storage.connection.execute(
            "SELECT s.id,s.raw_text,s.source_url,s.content_hash FROM library_sources s "
            "JOIN learning_item_sources x ON x.source_id=s.id WHERE x.item_id=? ORDER BY s.id",
            (first_id,),
        ).fetchall()
    ] == original_source_rows
    selected = await provider.select_question(origin="real", subject="criminal_law")
    assert selected["content"]["question"] == "两个投影共同更新的题干？"

    current_real = storage.list_real_questions(limit=None)[0]
    collision = current_real.to_mapping()
    collision.update(
        {
            "exam_year": "2099",
            "paper": "共享投影冲突卷",
            "question_number": "99",
        }
    )
    storage.import_real_questions([collision])
    before_first = service.library_service.repository.get(first_id)
    before_second = service.library_service.repository.get(second_id)
    rejected = await service.update_management_learning_item(
        first_id,
        {
            "exam_year": "2099",
            "paper": "共享投影冲突卷",
            "question_number": "99",
        },
        actor_id="operator-1",
    )
    assert rejected["success"] is False
    assert "冲突" in rejected["message"] or "已存在" in rejected["message"]
    after_first = service.library_service.repository.get(first_id)
    after_second = service.library_service.repository.get(second_id)
    assert after_first.question == before_first.question
    assert after_second.question == before_second.question
    assert after_first.item.subjects == before_first.item.subjects
    assert after_second.item.subjects == before_second.item.subjects
    unchanged_real = next(
        question
        for question in storage.list_real_questions(limit=None)
        if question.id == current_real.id
    )
    assert unchanged_real.exam_year == current_real.exam_year
    storage.close()


@pytest.mark.asyncio
async def test_candidate_promotion_rejects_truth_edit_after_preview_and_repreview_succeeds(
    tmp_path,
):
    from tests.test_structured_ingestion import _prepare_service

    storage, ingestion, prepared, _ = await _prepare_service(tmp_path)
    await ingestion.confirm(prepared)
    item_id = int(
        storage.connection.execute(
            "SELECT id FROM learning_items WHERE identity='real_question_candidate'"
        ).fetchone()[0]
    )
    storage.connection.execute(
        "UPDATE learning_questions SET exam_name='合成考试' WHERE item_id=?", (item_id,)
    )
    storage.connection.commit()
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        config=SimpleNamespace(operator_ids=["operator-1"]),
    )
    original_updated_at = service.library_service.repository.get(
        item_id
    ).item.updated_at
    stale = await service.prepare_candidate_promotion([item_id], actor_id="operator-1")
    edited = await service.update_management_learning_item(
        item_id,
        {"stem": "复核后修订的新合成题干？", "explanation": "新解释"},
        actor_id="operator-1",
    )
    assert edited["success"] is True
    assert (
        service.library_service.repository.get(item_id).item.updated_at
        > original_updated_at
    )
    rejected = await service.confirm_candidate_promotion(
        stale["token"], actor_id="operator-1"
    )
    assert rejected["success"] is False
    assert storage.list_real_questions(limit=None) == []
    fresh = await service.prepare_candidate_promotion([item_id], actor_id="operator-1")
    accepted = await service.confirm_candidate_promotion(
        fresh["token"], actor_id="operator-1"
    )
    assert accepted["success"] is True
    assert storage.list_real_questions(limit=None)[0].stem == "复核后修订的新合成题干？"
    storage.close()


@pytest.mark.asyncio
async def test_candidate_promotion_truth_fingerprint_catches_normalized_row_change(
    tmp_path,
):
    from tests.test_structured_ingestion import _prepare_service

    storage, ingestion, prepared, _ = await _prepare_service(tmp_path)
    await ingestion.confirm(prepared)
    item_id = int(
        storage.connection.execute(
            "SELECT id FROM learning_items WHERE identity='real_question_candidate'"
        ).fetchone()[0]
    )
    storage.connection.execute(
        "UPDATE learning_questions SET exam_name='合成考试' WHERE item_id=?", (item_id,)
    )
    storage.connection.commit()
    service = LawAssistantService(
        storage,
        library_service=ingestion.library_service,
        config=SimpleNamespace(operator_ids=["operator-1"]),
    )
    preview = await service.prepare_candidate_promotion(
        [item_id],
        actor_id="operator-1",
        overrides_by_id={str(item_id): {"stem": "预览时覆盖的合成题干？"}},
    )
    storage.connection.execute(
        "UPDATE learning_questions SET explanation='直接变更的合成解析' WHERE item_id=?",
        (item_id,),
    )
    storage.connection.commit()
    result = await service.confirm_candidate_promotion(
        preview["token"], actor_id="operator-1"
    )
    assert result["success"] is False
    assert "规范化真题内容" in result["results"][0]["reason"]
    assert storage.list_real_questions(limit=None) == []
    storage.close()


@pytest.mark.asyncio
async def test_independent_real_question_has_authorized_edit_disable_and_restore_path(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "independent-real-question.sqlite3")
    storage.import_real_questions(
        [
            {
                "source_name": "独立合成题库",
                "exam_name": "合成考试",
                "exam_year": "2098",
                "paper": "合成卷",
                "question_number": "第1题",
                "source_locator": "fixture:question-1",
                "subject": "criminal_law",
                "question_type": "single_choice",
                "stem": "独立真题初始题干？",
                "options": ["A. 甲", "B. 乙"],
                "answer": "A",
                "answer_source": "user_verified",
                "verification_status": "user_verified",
                "explanation": "初始合成解析。",
            }
        ]
    )
    question_id = storage.list_real_questions(limit=None)[0].id
    service = LawAssistantService(
        storage, config=SimpleNamespace(operator_ids=["operator-1"])
    )
    provider = LearningContentProvider(storage)

    denied = await service.list_management_real_questions(actor_id="ordinary-user")
    assert denied["error"] == "forbidden"
    listing = await service.list_management_real_questions(actor_id="operator-1")
    assert listing["success"] is True
    assert listing["items"][0]["management_mode"] == "independent"
    assert listing["items"][0]["selectable"] is True
    detail = await service.get_management_real_question(
        question_id, actor_id="operator-1"
    )
    assert detail["question"]["answer"] == "A"

    edited = await service.update_management_real_question(
        question_id,
        {"stem": "独立真题已复核的新题干？", "explanation": "新合成解析。"},
        actor_id="operator-1",
    )
    assert edited["success"] is True
    selected = await provider.select_question(origin="real", subject="criminal_law")
    assert selected["available"] is True
    assert selected["content"]["question"] == "独立真题已复核的新题干？"

    disabled = await service.set_management_real_question_active(
        question_id, active=False, actor_id="operator-1"
    )
    assert disabled["success"] is True
    assert disabled["question"]["selectable"] is False
    assert (await provider.select_question(origin="real", subject="criminal_law"))[
        "available"
    ] is False

    restored = await service.set_management_real_question_active(
        question_id, active=True, actor_id="operator-1"
    )
    assert restored["success"] is True
    assert restored["question"]["verification_status"] == "user_verified"
    selected_again = await provider.select_question(
        origin="real", subject="criminal_law"
    )
    assert selected_again["available"] is True
    assert selected_again["content"]["question"] == "独立真题已复核的新题干？"
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()


@pytest.mark.asyncio
async def test_verified_question_clear_demotes_only_linked_candidate_for_repromotion(
    tmp_path,
):
    storage, service, promoted_item_id, _provider = await _promoted_synthetic_question(
        tmp_path
    )
    unrelated = await service.library_service.archive_learning_material(
        raw_text="未升格的独立候选合成原文",
        material_type="real_question_candidate",
        title="无结构化关联的候选",
        subjects="刑法",
        structured_json={
            "question_type": "short_answer",
            "stem": "未升格的候选题干",
            "answer": "合成答案",
        },
        created_by="operator-1",
        session_origin="private:operator-1",
    )
    prepared = await service.prepare_data_clear(
        "verified_questions", actor_id="operator-1"
    )
    cleared = await service.confirm_data_clear(prepared["token"], actor_id="operator-1")

    assert cleared["success"] is True
    assert storage.count_real_questions() == 0
    candidate = service.library_service.repository.get(promoted_item_id)
    binding = candidate.structured
    assert candidate.item.identity == "real_question_candidate"
    assert candidate.item.verification_status == "unverified"
    assert binding["verified_real_question_id"] is None
    assert binding["promoted_by"] is None
    assert binding["promoted_at"] is None
    assert binding["review_status"] == "pending_review"
    assert candidate.stem_blocks
    assert candidate.sources[0].raw_text

    still_unpromoted = service.library_service.repository.get(unrelated["item_id"])
    assert still_unpromoted.item.identity == "real_question_candidate"
    assert still_unpromoted.structured is None

    second_preview = await service.prepare_candidate_promotion(
        [promoted_item_id], actor_id="operator-1"
    )
    assert second_preview["promotable_count"] == 1
    second_confirm = await service.confirm_candidate_promotion(
        second_preview["token"], actor_id="operator-1"
    )
    assert second_confirm["success"] is True
    assert storage.count_real_questions() == 1
    assert storage.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    storage.close()

    reopened = SQLiteStorage(tmp_path / "runtime.sqlite3")
    assert reopened.schema_version == 13
    assert reopened.count_real_questions() == 1
    assert reopened.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    reopened.close()


@pytest.mark.asyncio
async def test_daily_question_skip_does_not_advance_calendar_rotation(tmp_path) -> None:
    current = [datetime(2026, 10, 1, 9, tzinfo=ZoneInfo("Asia/Shanghai"))]
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    calls: list[str] = []

    class Learning:
        async def generate_question(self, **kwargs):
            calls.append(kwargs["subject"])
            if len(calls) == 1:
                return {"available": False, "reason": "暂无匹配的模拟题"}
            return {
                "available": True,
                "origin": "mock",
                "subject": kwargs["subject"],
                "question_type": kwargs["question_type"],
                "content": {
                    "question": "第二天题目",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析",
                },
            }

    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: current[0],
    )
    target = await service.bind_target("aiocqhttp:group:101", "测试群")
    storage.upsert_daily_plan(
        DailyPlan(
            content_type="daily_question",
            enabled=True,
            time="08:00",
            selection_mode="rotation",
            rotation_subjects=("intellectual_property", "economic_law"),
            rotation_start_date="2026-10-01",
            question_origin="mock",
            question_type="single_choice",
        ),
        target["id"],
    )

    first = await service.run_scheduled_jobs(now=current[0])
    current[0] = datetime(2026, 10, 2, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    second = await service.run_scheduled_jobs(now=current[0])

    assert first["daily_question_sent"] == 0
    assert second["daily_question_sent"] == 1
    assert calls == ["intellectual_property", "economic_law"]
    assert len(storage.list_daily_contents()) == 2


@pytest.mark.asyncio
async def test_daily_question_history_is_target_scoped_and_passed_to_provider(tmp_path):
    now = datetime(2026, 10, 3, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    publisher = RecordingPublisher()
    target = storage.bind_target("aiocqhttp:group:100", "测试群")
    prior = storage.claim_daily_content(
        content_date="2026-10-02",
        target_id=target["id"],
        content_type="daily_question",
        body={"question": "A"},
        source_kind="real_question",
        source_item_key="A",
    )
    assert prior is not None
    storage.finish_daily_content(prior, success=True)

    calls: list[dict[str, object]] = []

    class Learning:
        async def generate_question(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["used_content_keys"] == {("real_question", "A")}
            return {
                "available": True,
                "origin": "real",
                "subject": kwargs["subject"],
                "question_type": kwargs["question_type"],
                "question_id": 2,
                "source_kind": "real_question",
                "source_item_key": "B",
                "content": {
                    "question": "未发送真题 B",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "解析",
                },
            }

    service = LawAssistantService(
        storage,
        publisher=publisher,
        learning_service=Learning(),
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: now,
    )
    storage.upsert_daily_plan(
        DailyPlan(
            content_type="daily_question",
            enabled=True,
            time="08:00",
            selection_mode="fixed",
            fixed_subject="criminal_law",
            question_origin="real",
            question_type_selection_mode="fixed",
            fixed_question_type="single_choice",
        ),
        target["id"],
    )

    result = await service.run_scheduled_jobs(now=now)

    assert result["daily_question_sent"] == 1
    assert len(calls) == 1
    sent = storage.list_daily_contents()[0]
    assert sent["source_item_key"] == "B"


@pytest.mark.asyncio
async def test_daily_plan_fixed_to_random_survives_preview_confirmation_and_reload(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    target = await service.bind_target("aiocqhttp:group:100", "测试群")
    storage.upsert_daily_plan(
        DailyPlan.from_mapping(
            "daily_question",
            {"question_type": "multiple_choice"},
        ),
        target["id"],
    )

    preview = await service.prepare_daily_plan_update(
        target_selectors=["测试群"],
        content_type="daily_question",
        changes={"question_type_selection_mode": "random"},
    )

    assert preview["ready"] is True
    assert preview["plans"][0]["plan"]["question_type_selection_mode"] == "random"
    assert (await service.confirm_daily_plan_update(preview["token"]))[
        "success"
    ] is True
    reloaded = SQLiteStorage(tmp_path / "runtime.sqlite3")
    loaded = reloaded.get_daily_plan(target["id"], "daily_question")
    assert loaded is not None
    assert loaded.question_type_selection_mode == "random"


@pytest.mark.asyncio
async def test_daily_plan_reset_preview_uses_global_plan_and_rejects_stale_token(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: datetime(2026, 9, 21, 1, 0, tzinfo=ZoneInfo("UTC")),
    )
    target = await service.bind_target("aiocqhttp:group:100", "法硕一群")
    global_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "selection_mode": "rotation",
            "rotation_subjects": ["intellectual_property", "civil_commercial"],
            "rotation_start_date": "2026-09-21",
        },
    )
    target_plan = DailyPlan.from_mapping(
        "daily_question",
        {"enabled": True, "selection_mode": "fixed", "fixed_subject": "criminal_law"},
    )
    storage.upsert_daily_plan(global_plan)
    storage.upsert_daily_plan(target_plan, target["id"])

    prepared = await service.prepare_daily_plan_override_removal(
        target_selectors=["法硕一群"],
        content_type="daily_question",
        actor_id="operator-1",
    )

    assert prepared["ready"] is True
    assert prepared["target"]["id"] == target["id"]
    assert prepared["target"]["label"] == "法硕一群"
    assert prepared["content_types"] == ["daily_question"]
    assert prepared["current_plans"][0]["plan"]["fixed_subject"] == "criminal_law"
    assert prepared["restored_plans"][0]["plan"]["selection_mode"] == "rotation"
    assert prepared["restored_plans"][0]["preview"][0]["subject"] == (
        "intellectual_property"
    )
    assert len(prepared["restored_plans"][0]["preview"]) == 14
    assert storage.get_daily_plan(target["id"], "daily_question") is not None

    storage.upsert_daily_plan(
        DailyPlan.from_mapping(
            "daily_question",
            {
                "enabled": True,
                "selection_mode": "rotation",
                "rotation_subjects": ["economic_law", "civil_commercial"],
                "rotation_start_date": "2026-09-21",
            },
        )
    )
    stale = await service.confirm_daily_plan_override_removal(
        prepared["token"], actor_id="operator-1"
    )
    assert stale == {
        "success": False,
        "reason": "全局计划在准备后已变化，请重新预览",
    }
    assert storage.get_daily_plan(target["id"], "daily_question") is not None

    refreshed = await service.prepare_daily_plan_override_removal(
        target_selectors=["法硕一群"],
        content_type="daily_question",
        actor_id="operator-1",
    )
    confirmed = await service.confirm_daily_plan_override_removal(
        refreshed["token"], actor_id="operator-1"
    )
    assert confirmed == {"success": True, "content_types": ["daily_question"]}
    assert storage.get_daily_plan(target["id"], "daily_question") is None
    restored = service.effective_daily_plan(target["id"], "daily_question")
    assert restored.rotation_subjects == ("economic_law", "civil_commercial")
    replay = await service.confirm_daily_plan_override_removal(
        refreshed["token"], actor_id="operator-1"
    )
    assert replay["success"] is False


@pytest.mark.asyncio
async def test_daily_plan_reset_preview_uses_target_id_for_random_global_resolution(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai"),
        clock=lambda: datetime(2026, 9, 21, 1, 0, tzinfo=ZoneInfo("UTC")),
    )
    target = await service.bind_target("aiocqhttp:group:100", "法硕一群")
    other_target = await service.bind_target("aiocqhttp:group:200", "法硕二群")
    global_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "selection_mode": "random",
            "question_origin": "random",
            "question_type_selection_mode": "random",
        },
    )
    target_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "selection_mode": "fixed",
            "fixed_subject": "criminal_law",
            "question_type_selection_mode": "fixed",
            "fixed_question_type": "single_choice",
        },
    )
    other_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "selection_mode": "fixed",
            "fixed_subject": "economic_law",
            "question_type_selection_mode": "fixed",
            "fixed_question_type": "true_false",
        },
    )
    storage.upsert_daily_plan(global_plan)
    storage.upsert_daily_plan(target_plan, target["id"])
    storage.upsert_daily_plan(other_plan, other_target["id"])
    local_today = date(2026, 9, 21)

    global_preview_before = global_plan.preview(local_today, 14, target_id=None)
    other_preview_before = other_plan.preview(
        local_today, 14, target_id=other_target["id"]
    )
    prepared = await service.prepare_daily_plan_override_removal(
        target_selectors=["法硕一群"],
        content_type="daily_question",
        actor_id="operator-1",
    )

    restored_preview = prepared["restored_plans"][0]["preview"]
    expected_preview = [
        {
            "date": (local_today + timedelta(days=offset)).isoformat(),
            **resolve_daily_constraints(
                global_plan,
                local_today + timedelta(days=offset),
                target_id=target["id"],
                content_type="daily_question",
            ),
        }
        for offset in range(14)
    ]
    assert restored_preview == expected_preview
    assert restored_preview != global_preview_before
    assert global_plan.preview(local_today, 14, target_id=None) == global_preview_before
    assert other_plan.preview(local_today, 14, target_id=other_target["id"]) == (
        other_preview_before
    )

    confirmed = await service.confirm_daily_plan_override_removal(
        prepared["token"], actor_id="operator-1"
    )
    assert confirmed["success"] is True
    effective = service.effective_daily_plan(target["id"], "daily_question")
    assert effective.to_mapping() == global_plan.to_mapping()
    assert [
        {
            "date": (local_today + timedelta(days=offset)).isoformat(),
            **resolve_daily_constraints(
                effective,
                local_today + timedelta(days=offset),
                target_id=target["id"],
                content_type="daily_question",
            ),
        }
        for offset in range(14)
    ] == restored_preview
    assert (
        service.effective_daily_plan(other_target["id"], "daily_question").to_mapping()
        == other_plan.to_mapping()
    )
