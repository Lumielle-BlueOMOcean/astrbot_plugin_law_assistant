from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from question_session import (
    build_question_session_snapshot,
    chunk_text,
    format_session_answer,
    format_session_explanation,
    format_session_prompt,
)
from question_session_repository import QuestionSessionRepository
from service import LawAssistantService
from storage import SQLiteStorage


def _real_question() -> dict[str, object]:
    return {
        "available": True,
        "origin": "real",
        "subject": "criminal_law",
        "question_type": "case_analysis",
        "source_name": "授权题库",
        "exam_name": "法考示例卷",
        "exam_year": "2022",
        "paper": "主观题卷",
        "question_number": "第 1 题",
        "source_locator": "PDF第1页",
        "source_url": "https://example.test/source",
        "answer_source": "official",
        "question_id": 42,
        "content": {
            "question": "请分析甲的行为。",
            "options": [],
            "answer": "【答案秘密】甲构成犯罪。",
            "explanation": "【解析秘密】依法分析构成要件。",
        },
    }


def _structured_candidate() -> dict[str, object]:
    return {
        "item": {
            "id": 91,
            "item_type": "question",
            "identity": "real_question_candidate",
            "title": "合成结构化案例题",
            "subjects": ["criminal_law"],
            "verification_status": "pending_review",
            "metadata": {},
        },
        "question": {
            "question_identity": "real_question_candidate",
            "question_type": "case_analysis",
            "stem": "根据材料回答下列问题。",
            "options": [],
            "answer": None,
            "explanation": "",
            "exam_name": "",
            "exam_year": "",
            "paper": "",
            "question_number": "",
            "answer_source": "not_provided",
        },
        "structured": {
            "review_status": "pending_review",
            "payload": {
                "subject": "criminal_law",
                "question_type": "case_analysis",
                "answer_requirements": [
                    {"order": 1, "text": "说明请求权基础", "locator": "PDF第1页"}
                ],
            },
            "answer_requirements": [
                {
                    "order": 1,
                    "text": "说明请求权基础",
                    "locator": "PDF第1页",
                    "provenance": "source_text",
                }
            ],
        },
        "shared_materials": [
            {
                "id": f"material-{index}",
                "title": f"材料 {index}",
                "blocks": [
                    {
                        "id": f"material-{index}-block",
                        "order": 1,
                        "kind": "paragraph",
                        "text": f"第{index}份案情事实。",
                        "locator": f"PDF第{index}页",
                        "provenance": "source_text",
                    }
                ],
            }
            for index in range(1, 4)
        ],
        "stem_blocks": [
            {
                "id": "stem-1",
                "order": 1,
                "kind": "paragraph",
                "text": "根据材料回答下列问题。",
                "locator": "PDF第1页",
                "provenance": "source_text",
            }
        ],
        "subquestions": [
            {
                "id": f"sub-{index}",
                "source_number": str(index),
                "answer_status": "provided",
                "stem_blocks": [
                    {
                        "id": f"sub-{index}-stem",
                        "order": 1,
                        "kind": "paragraph",
                        "text": f"小问{index}：分析第{index}项请求。",
                        "locator": f"PDF第{index}页",
                        "provenance": "source_text",
                    }
                ],
                "answer": {
                    "identity": "source_text",
                    "blocks": [
                        {
                            "id": f"sub-{index}-answer-{part}",
                            "order": part,
                            "kind": "paragraph",
                            "text": f"小问{index}答案段{part}。",
                            "locator": f"PDF第{index}页",
                            "provenance": "source_text",
                        }
                        for part in (1, 2)
                    ],
                },
                "explanation_blocks": [
                    {
                        "id": f"sub-{index}-explanation",
                        "order": 1,
                        "kind": "paragraph",
                        "text": f"小问{index}独立解析。",
                        "locator": f"PDF第{index}页",
                        "provenance": "source_text",
                    }
                ],
            }
            for index in range(1, 4)
        ],
    }


def test_prompt_answer_and_explanation_are_revealed_separately() -> None:
    snapshot = build_question_session_snapshot(_real_question())

    prompt = format_session_prompt(snapshot, prompt_index=0)
    answer = format_session_answer(snapshot, prompt_index=0)
    explanation = format_session_explanation(snapshot, prompt_index=0)

    assert "【真题｜刑法｜案例分析题】" in prompt
    assert "请分析甲的行为。" in prompt
    assert "题库来源：授权题库" in prompt
    assert "考试：法考示例卷" in prompt
    assert "来源链接：https://example.test/source" in prompt
    assert "【答案秘密】" not in prompt
    assert "【解析秘密】" not in prompt
    assert "PDF第1页" not in prompt
    assert "第 1 题" not in prompt
    assert "【答案秘密】" in answer
    assert "【解析秘密】" not in answer
    assert "【解析秘密】" in explanation
    assert "【答案秘密】" not in explanation


def test_chunk_text_keeps_unicode_text_exactly_without_empty_chunks() -> None:
    source = "材料甲。\n第二段包含中文与 emoji 🏛️；最后一段结束。"

    chunks = chunk_text(source, max_chars=12)

    assert chunks
    assert all(chunks)
    assert "".join(chunks) == source


def test_structured_candidate_snapshot_keeps_sections_and_pending_identity() -> None:
    snapshot = build_question_session_snapshot(_structured_candidate(), max_chars=8)

    assert snapshot["identity_label"] == "来源资料候选题 · 待人工核验"
    assert len(snapshot["materials"]) == 3
    assert len(snapshot["prompts"]) == 3
    assert len(snapshot["answer_requirements"]) == 1
    assert all(len(prompt["answer_blocks"]) == 2 for prompt in snapshot["prompts"])
    assert "structured_import" not in json.dumps(snapshot, ensure_ascii=False)
    assert "PDF第1页" not in format_session_prompt(snapshot, material_index=0)
    assert "来源资料候选题" in format_session_prompt(snapshot, material_index=0)
    assert "第1份案情事实。" in format_session_prompt(snapshot, material_index=0)


def test_structured_block_boundaries_are_kept_when_the_block_fits_budget() -> None:
    candidate = _structured_candidate()
    candidate["shared_materials"] = [
        {
            "id": "m-boundary",
            "title": "分段材料",
            "blocks": [
                {"id": "b1", "order": 1, "text": "甲" * 15},
                {"id": "b2", "order": 2, "text": "乙" * 10},
            ],
        }
    ]
    snapshot = build_question_session_snapshot(candidate, max_chars=20)

    assert snapshot["materials"][0]["pages"] == ["甲" * 15, "乙" * 10]


def test_question_sessions_are_scope_isolated_and_new_session_supersedes_only_same_scope(
    tmp_path,
) -> None:
    storage = SQLiteStorage(tmp_path / "sessions.sqlite3")
    repository = QuestionSessionRepository(storage.connection)
    created_at = datetime(2026, 9, 23, tzinfo=timezone.utc).isoformat()

    first = repository.create_session(
        session_key="group-a-1",
        scope_origin="aiocqhttp:GroupMessage:group-a",
        target_id=None,
        source_kind="library_candidate",
        source_item_key="91",
        library_item_id=None,
        real_question_id=None,
        question_identity="real_question_candidate",
        snapshot={"title": "原始快照"},
        created_by="42",
        created_at=created_at,
    )
    other = repository.create_session(
        session_key="group-b-1",
        scope_origin="aiocqhttp:GroupMessage:group-b",
        target_id=None,
        source_kind="real_question",
        source_item_key="42",
        library_item_id=None,
        real_question_id=None,
        question_identity="verified_real_question",
        snapshot={"title": "另一群"},
        created_by="42",
        created_at=created_at,
    )
    replacement = repository.create_session(
        session_key="group-a-2",
        scope_origin="aiocqhttp:GroupMessage:group-a",
        target_id=None,
        source_kind="library_candidate",
        source_item_key="91",
        library_item_id=None,
        real_question_id=None,
        question_identity="real_question_candidate",
        snapshot={"title": "新题快照"},
        created_by="42",
        created_at=created_at,
    )

    assert (
        repository.get_active("aiocqhttp:GroupMessage:group-a")["id"]
        == replacement["id"]
    )
    assert repository.get_active("aiocqhttp:GroupMessage:group-b")["id"] == other["id"]
    assert repository.get(first["id"])["status"] == "superseded"
    assert repository.get_active("aiocqhttp:GroupMessage:group-a")["snapshot"] == {
        "title": "新题快照"
    }
    assert [event["event_kind"] for event in repository.list_events(first["id"])] == [
        "opened",
        "superseded",
    ]
    storage.close()


def test_revealing_answer_is_persisted_once_and_survives_reopen(tmp_path) -> None:
    db_path = tmp_path / "session-reopen.sqlite3"
    storage = SQLiteStorage(db_path)
    repository = QuestionSessionRepository(storage.connection)
    session = repository.create_session(
        session_key="private-1",
        scope_origin="aiocqhttp:FriendMessage:42",
        target_id=None,
        source_kind="real_question",
        source_item_key="7",
        library_item_id=None,
        real_question_id=None,
        question_identity="verified_real_question",
        snapshot={"answer_blocks": ["来自核验来源的答案"]},
        created_by="42",
        created_at="2026-09-23T00:00:00+00:00",
    )

    repository.reveal(
        session["id"], "answer", actor_id="42", at="2026-09-23T00:01:00+00:00"
    )
    repository.reveal(
        session["id"], "answer", actor_id="42", at="2026-09-23T00:02:00+00:00"
    )
    storage.close()

    reopened = SQLiteStorage(db_path)
    restored = QuestionSessionRepository(reopened.connection).get_active(
        "aiocqhttp:FriendMessage:42"
    )
    assert restored["answer_revealed"] is True
    assert restored["explanation_revealed"] is False
    assert restored["snapshot"]["answer_blocks"] == ["来自核验来源的答案"]
    assert (
        sum(
            event["event_kind"] == "answer_revealed"
            for event in QuestionSessionRepository(reopened.connection).list_events(
                session["id"]
            )
        )
        == 1
    )
    reopened.close()


@pytest.mark.asyncio
async def test_service_scopes_question_session_and_reveals_answers_only_on_request(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "service-session.sqlite3")
    service = LawAssistantService(
        storage, config=SimpleNamespace(question_message_max_chars=300)
    )
    scope = "aiocqhttp:FriendMessage:42"
    source_content = _real_question()

    opened = service.open_question_session(
        source_content, session_origin=scope, actor_id="42"
    )
    source_content["content"]["answer"] = "后续来源修改不能改变会话快照"
    assert opened["success"] is True
    assert "【真题｜刑法｜案例分析题】" in opened["text"]
    assert "【答案秘密】" not in opened["text"]
    assert "【解析秘密】" not in opened["text"]
    assert service.question_sessions.get_active("aiocqhttp:FriendMessage:7") is None

    answer = await service.question_session_action(
        "answer", session_origin=scope, actor_id="42"
    )
    assert "【答案秘密】甲构成犯罪。" in answer["text"]
    assert "后续来源修改" not in answer["text"]
    assert "【解析秘密】" not in answer["text"]
    explanation = await service.question_session_action(
        "explanation", session_origin=scope, actor_id="42"
    )
    assert "【解析秘密】依法分析构成要件。" in explanation["text"]
    assert "【答案秘密】" not in explanation["text"]
    storage.close()


@pytest.mark.asyncio
async def test_current_returns_progress_summary_and_early_answer_has_reading_notice(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "current-summary.sqlite3")
    service = LawAssistantService(storage)
    opened = service.open_question_session(
        _structured_candidate(),
        session_origin="aiocqhttp:FriendMessage:42",
        actor_id="42",
    )
    current = await service.question_session_action(
        "current", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert "材料进度：0/3" in current["text"]
    assert "第1份案情事实。" not in current["text"]
    assert "小问：1/3" in current["text"]

    answer = await service.question_session_action(
        "answer", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert "材料或题干尚未全部展开" in answer["text"]
    assert "小问1答案段1。" in answer["text"]
    assert "小问1答案段2。" in answer["text"]
    assert opened["session_id"] == current["session_id"]
    storage.close()


@pytest.mark.asyncio
async def test_service_paginates_shared_material_then_advances_subquestions(tmp_path):
    storage = SQLiteStorage(tmp_path / "paged-session.sqlite3")
    service = LawAssistantService(
        storage, config=SimpleNamespace(question_message_max_chars=300)
    )
    content = _structured_candidate()
    content["shared_materials"][0]["blocks"][0]["text"] = "材料内容。" + "甲" * 700
    opened = service.open_question_session(
        content,
        session_origin="aiocqhttp:FriendMessage:42",
        actor_id="42",
    )
    assert "材料 1/3（第 1/3 页）" in opened["text"]
    assert "小问1" not in opened["text"]

    next_page = await service.question_session_action(
        "next", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert "材料 1/3（第 2/3 页）" in next_page["text"]
    assert "甲" in next_page["text"]
    last_page = await service.question_session_action(
        "next", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert "材料 1/3（第 3/3 页）" in last_page["text"]
    await service.question_session_action(
        "next", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    await service.question_session_action(
        "next", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    first_prompt = await service.question_session_action(
        "next", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert "小问 1/3" in first_prompt["text"]
    assert "小问1：分析第1项请求。" in first_prompt["text"]
    second_prompt = await service.question_session_action(
        "next-question", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert "小问 2/3" in second_prompt["text"]
    answer = await service.question_session_action(
        "answer", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert "小问2答案段1。" in answer["text"]
    assert "小问1答案段1。" not in answer["text"]
    reveal_events = [
        event
        for event in service.question_sessions.list_events(answer["session_id"])
        if event["event_kind"] == "answer_revealed"
    ]
    assert [event["metadata"]["prompt_index"] for event in reveal_events] == [1]
    storage.close()
