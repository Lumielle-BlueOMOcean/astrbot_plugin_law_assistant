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
    for prompt_index, prompt in enumerate(snapshot["prompts"], start=1):
        rendered_answer = "\n".join(prompt["answer_pages"])
        assert rendered_answer.count(f"小问{prompt_index}答案段1。") == 1
        assert rendered_answer.count(f"小问{prompt_index}答案段2。") == 1
    assert "structured_import" not in json.dumps(snapshot, ensure_ascii=False)
    assert "PDF第1页" not in format_session_prompt(snapshot, material_index=0)
    assert "来源资料候选题" in format_session_prompt(snapshot, material_index=0)
    assert "第1份案情事实。" in format_session_prompt(snapshot, material_index=0)


def test_structured_block_boundaries_are_kept_with_message_wrappers_in_budget() -> None:
    candidate = _structured_candidate()
    candidate["shared_materials"] = [
        {
            "id": "m-boundary",
            "title": "分段材料",
            "blocks": [
                {"id": "b1", "order": 1, "text": "甲" * 220},
                {"id": "b2", "order": 2, "text": "乙" * 100},
            ],
        }
    ]
    snapshot = build_question_session_snapshot(candidate, max_chars=300)

    pages = snapshot["materials"][0]["pages"]
    assert len(pages) == 2
    assert all(len(page) <= 300 for page in pages)
    assert "甲" * 220 in pages[0]
    assert "乙" * 100 in pages[1]


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
    assert "材料 1/3（第 1/" in opened["text"]
    assert len(opened["text"]) <= 300
    assert "小问1" not in opened["text"]

    next_page = await service.question_session_action(
        "next", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert "材料 1/3（第 2/" in next_page["text"]
    assert "甲" in next_page["text"]
    last_page = await service.question_session_action(
        "next", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert "材料 1/3（第 3/" in last_page["text"]
    active = service.question_sessions.get_active("aiocqhttp:FriendMessage:42")
    total_material_pages = sum(
        len(material["pages"]) for material in active["snapshot"]["materials"]
    )
    result = last_page
    for _ in range(total_material_pages - 3):
        result = await service.question_session_action(
            "next", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
        )
        assert len(result["text"]) <= 300
    first_prompt = await service.question_session_action(
        "next", session_origin="aiocqhttp:FriendMessage:42", actor_id="42"
    )
    assert len(first_prompt["text"]) <= 300
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


@pytest.mark.asyncio
async def test_final_question_answer_and_explanation_pages_obey_message_budget(
    tmp_path,
):
    limit = 300
    storage = SQLiteStorage(tmp_path / "bounded-session.sqlite3")
    service = LawAssistantService(
        storage, config=SimpleNamespace(question_message_max_chars=limit)
    )
    content = _real_question()
    content["content"]["question"] = "公共题干片段。" * 80
    content["content"]["options"] = [
        f"选项{i}：" + "选项正文。" * 12 for i in range(1, 5)
    ]
    answer_blocks = [f"答案片段{i:03d}：" + "答案正文。" * 8 for i in range(1, 19)]
    explanation_blocks = [f"解析片段{i:03d}：" + "解析正文。" * 8 for i in range(1, 17)]
    content["content"]["answer"] = answer_blocks
    content["content"]["explanation"] = explanation_blocks
    scope = "aiocqhttp:FriendMessage:42"

    opened = service.open_question_session(content, session_origin=scope, actor_id="42")
    prompt_messages = [opened["text"]]
    for _ in range(40):
        result = await service.question_session_action(
            "next", session_origin=scope, actor_id="42"
        )
        prompt_messages.append(result["text"])
        if "本题内容已展示完毕" in result["text"]:
            break

    async def collect_pages(action: str, continuation: str) -> list[str]:
        result = await service.question_session_action(
            action, session_origin=scope, actor_id="42"
        )
        pages = []
        for _ in range(40):
            pages.append(result["text"])
            if continuation not in result["text"]:
                break
            result = await service.question_session_action(
                continuation.removeprefix("/law "),
                session_origin=scope,
                actor_id="42",
            )
        return pages

    answer_pages = await collect_pages("answer", "/law next-answer")
    explanation_pages = await collect_pages("explanation", "/law next-explanation")

    for message in prompt_messages + answer_pages + explanation_pages:
        assert len(message) <= limit
    for index in range(1, 19):
        marker = f"答案片段{index:03d}："
        assert sum(page.count(marker) for page in answer_pages) == 1
    for index in range(1, 17):
        marker = f"解析片段{index:03d}："
        assert sum(page.count(marker) for page in explanation_pages) == 1
    assert len(answer_pages) > 1
    assert len(explanation_pages) > 1
    assert all("/law next-answer" in page for page in answer_pages[:-1])
    assert all("/law next-explanation" in page for page in explanation_pages[:-1])
    storage.close()


@pytest.mark.asyncio
async def test_single_oversized_answer_is_lossless_across_bounded_pages(tmp_path):
    limit = 300
    raw_answer = "答案起始：" + "核验内容。" * 180 + "答案结束。"
    storage = SQLiteStorage(tmp_path / "single-long-answer.sqlite3")
    service = LawAssistantService(
        storage, config=SimpleNamespace(question_message_max_chars=limit)
    )
    content = _real_question()
    content["content"]["answer"] = raw_answer
    scope = "aiocqhttp:FriendMessage:42"
    service.open_question_session(content, session_origin=scope, actor_id="42")

    result = await service.question_session_action(
        "answer", session_origin=scope, actor_id="42"
    )
    pages = []
    for _ in range(40):
        message = result["text"]
        assert len(message) <= limit
        lines = message.split("\n")
        assert "参考答案" in lines[1]
        body_lines = lines[2:]
        if body_lines and body_lines[-1] == "答案未完，继续使用 /law next-answer。":
            body_lines.pop()
        pages.append("\n".join(body_lines))
        if "/law next-answer" not in message:
            break
        result = await service.question_session_action(
            "next-answer", session_origin=scope, actor_id="42"
        )

    assert len(pages) > 1
    assert "".join(pages) == raw_answer
    storage.close()


@pytest.mark.asyncio
async def test_structured_prompt_shows_shared_stem_and_subquestion_requirements_once(
    tmp_path,
):
    storage = SQLiteStorage(tmp_path / "shared-stem-session.sqlite3")
    service = LawAssistantService(storage)
    content = _structured_candidate()
    content["subquestions"][0]["answer_requirements"] = [
        {"order": 1, "text": "第一问要求：比较两个请求权基础。"}
    ]
    content["subquestions"][0]["stem_blocks"].insert(
        0,
        {
            "id": "duplicated-common-stem",
            "order": 0,
            "text": "根据材料回答下列问题。",
        },
    )
    content["subquestions"][1]["answer_requirements"] = [
        {"order": 1, "text": "第二问要求：说明举证责任。"}
    ]
    scope = "aiocqhttp:FriendMessage:42"

    opened = service.open_question_session(content, session_origin=scope, actor_id="42")
    first_prompt = opened["text"]
    for _ in range(3):
        first_prompt = (
            await service.question_session_action(
                "next", session_origin=scope, actor_id="42"
            )
        )["text"]

    second_prompt = await service.question_session_action(
        "next-question", session_origin=scope, actor_id="42"
    )

    assert first_prompt.count("根据材料回答下列问题。") == 1
    assert "小问1：分析第1项请求。" in first_prompt
    assert "第一问要求：比较两个请求权基础。" in first_prompt
    assert "说明请求权基础" in first_prompt
    assert "小问2：分析第2项请求。" in second_prompt["text"]
    assert "第二问要求：说明举证责任。" in second_prompt["text"]
    assert "根据材料回答下列问题。" not in second_prompt["text"]
    storage.close()


@pytest.mark.asyncio
async def test_current_reveal_state_is_per_subquestion_and_survives_reopen(tmp_path):
    db_path = tmp_path / "per-prompt-reveal.sqlite3"
    scope = "aiocqhttp:FriendMessage:42"
    storage = SQLiteStorage(db_path)
    service = LawAssistantService(storage)
    opened = service.open_question_session(
        _structured_candidate(), session_origin=scope, actor_id="42"
    )

    first_reveal = await service.question_session_action(
        "answer", session_origin=scope, actor_id="42"
    )
    first_explanation = await service.question_session_action(
        "explanation", session_origin=scope, actor_id="42"
    )
    repeated_reveal = await service.question_session_action(
        "answer", session_origin=scope, actor_id="42"
    )
    repeated_explanation = await service.question_session_action(
        "explanation", session_origin=scope, actor_id="42"
    )
    await service.question_session_action(
        "next-question", session_origin=scope, actor_id="42"
    )
    second_prompt_status = await service.question_session_action(
        "current", session_origin=scope, actor_id="42"
    )
    events = service.question_sessions.list_events(opened["session_id"])
    assert sum(event["event_kind"] == "answer_revealed" for event in events) == 1
    assert sum(event["event_kind"] == "explanation_revealed" for event in events) == 1
    assert "答案：未揭晓" in second_prompt_status["text"]
    assert "解析：未揭晓" in second_prompt_status["text"]
    assert "小问：2/3" in second_prompt_status["text"]
    dashboard = await service.dashboard_overview()
    assert dashboard["question_sessions"][0]["answer_revealed"] is False
    assert dashboard["question_sessions"][0]["explanation_revealed"] is False
    storage.close()

    reopened = SQLiteStorage(db_path)
    restored_service = LawAssistantService(reopened)
    restored_status = await restored_service.question_session_action(
        "current", session_origin=scope, actor_id="42"
    )
    assert restored_status["text"] == second_prompt_status["text"]
    second_reveal = await restored_service.question_session_action(
        "answer", session_origin=scope, actor_id="42"
    )
    reveal_events = [
        event
        for event in restored_service.question_sessions.list_events(
            opened["session_id"]
        )
        if event["event_kind"] == "answer_revealed"
    ]
    assert "小问2答案段1。" in second_reveal["text"]
    assert [event["metadata"]["prompt_index"] for event in reveal_events] == [0, 1]
    assert (
        "答案：已揭晓"
        in (
            await restored_service.question_session_action(
                "current", session_origin=scope, actor_id="42"
            )
        )["text"]
    )
    second_explanation = await restored_service.question_session_action(
        "explanation", session_origin=scope, actor_id="42"
    )
    assert "小问2独立解析。" in second_explanation["text"]
    current = await restored_service.question_session_action(
        "current", session_origin=scope, actor_id="42"
    )
    assert "解析：已揭晓" in current["text"]
    explanation_events = [
        event
        for event in restored_service.question_sessions.list_events(
            opened["session_id"]
        )
        if event["event_kind"] == "explanation_revealed"
    ]
    assert [event["metadata"]["prompt_index"] for event in explanation_events] == [0, 1]
    assert first_reveal["text"] == repeated_reveal["text"]
    assert first_explanation["text"] == repeated_explanation["text"]
    reopened.close()
