from __future__ import annotations

import importlib
import importlib.util

from question_session_repository import QuestionSessionRepository
from storage import SQLiteStorage


def _session(storage: SQLiteStorage) -> dict:
    target = storage.bind_target("aiocqhttp:group:501", "测试群")
    return QuestionSessionRepository(storage.connection).create_session(
        session_key="session-501",
        scope_origin="aiocqhttp:group:501",
        target_id=target["id"],
        source_kind="generated_question",
        source_item_key="synthetic-question",
        library_item_id=None,
        real_question_id=None,
        question_identity="mock_question",
        snapshot={"prompts": [{"stem": "合成题干"}], "answer": ["A"]},
        created_by="scheduler",
        created_at="2026-09-29T00:00:00+00:00",
    )


def _repository_type():
    spec = importlib.util.find_spec("scheduled_reveals")
    assert spec is not None, "scheduled reveal repository must be available"
    if spec is None:
        return None
    return importlib.import_module("scheduled_reveals").ScheduledRevealRepository


def test_reveal_jobs_lock_snapshot_are_idempotent_and_recover_page_progress(tmp_path):
    repository_type = _repository_type()
    if repository_type is None:
        return
    path = tmp_path / "scheduled-reveal.sqlite3"
    storage = SQLiteStorage(path)
    session = _session(storage)
    repository = repository_type(storage.connection)
    fields = {
        "session_id": session["id"],
        "target_umo": session["scope_origin"],
        "snapshot_hash": session["snapshot_hash"],
        "question_sent_at": "2026-09-29T00:00:00+00:00",
        "due_at": "2026-09-29T00:15:00+00:00",
        "reveal_kind": "answer",
        "prompt_index": 0,
        "created_at": "2026-09-29T00:00:00+00:00",
    }

    first = repository.create_for_session(**fields)
    duplicate = repository.create_for_session(**fields)
    assert duplicate["id"] == first["id"]
    assert repository.claim_due(first["id"], now="2026-09-29T00:14:59+00:00") is None
    claimed = repository.claim_due(first["id"], now="2026-09-29T00:15:00+00:00")
    assert claimed["status"] == "sending"
    assert repository.claim_due(first["id"], now="2026-09-29T00:16:00+00:00") is None
    assert (
        repository.mark_page_sent(first["id"], 0, at="2026-09-29T00:15:01+00:00")
        is True
    )
    assert (
        repository.mark_page_sent(first["id"], 0, at="2026-09-29T00:15:02+00:00")
        is True
    )
    storage.close()

    reopened = SQLiteStorage(path)
    recovered_repository = repository_type(reopened.connection)
    recovered = recovered_repository.get(first["id"])
    assert recovered["status"] == "sending"
    assert recovered["page_progress"] == [0]
    assert recovered["snapshot_hash"] == session["snapshot_hash"]
    assert recovered_repository.recover_inflight(at="2026-09-29T00:20:00+00:00") == 1
    assert recovered_repository.get(first["id"])["status"] == "needs_review"
    reopened.close()


def test_manual_reveal_skips_only_matching_pending_job_and_conflicts_are_rejected(
    tmp_path,
):
    repository_type = _repository_type()
    if repository_type is None:
        return
    storage = SQLiteStorage(tmp_path / "manual-reveal.sqlite3")
    session = _session(storage)
    repository = repository_type(storage.connection)
    common = {
        "session_id": session["id"],
        "target_umo": session["scope_origin"],
        "snapshot_hash": session["snapshot_hash"],
        "question_sent_at": "2026-09-29T00:00:00+00:00",
        "due_at": "2026-09-29T00:15:00+00:00",
        "prompt_index": 0,
        "created_at": "2026-09-29T00:00:00+00:00",
    }
    answer = repository.create_for_session(**common, reveal_kind="answer")
    explanation = repository.create_for_session(**common, reveal_kind="explanation")

    assert (
        repository.skip_for_manual_reveal(
            session["id"], "answer", prompt_index=0, at="2026-09-29T00:05:00+00:00"
        )
        == 1
    )
    assert repository.get(answer["id"])["status"] == "skipped"
    assert repository.get(explanation["id"])["status"] == "pending"
    assert (
        repository.skip(
            explanation["id"], at="2026-09-29T00:05:00+00:00", reason="session closed"
        )
        is True
    )
    assert repository.get(explanation["id"])["status"] == "skipped"

    try:
        repository.create_for_session(
            **{**common, "snapshot_hash": "different"}, reveal_kind="answer"
        )
    except ValueError as exc:
        assert "snapshot" in str(exc)
    else:
        raise AssertionError("conflicting snapshot must not overwrite an existing job")
    storage.close()
