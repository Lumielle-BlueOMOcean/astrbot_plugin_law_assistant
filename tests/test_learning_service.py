from __future__ import annotations

from datetime import datetime, timezone

import pytest

from learning_service import LearningService
from models import CaseItem
from storage import SQLiteStorage


class FakeLLM:
    async def generate_json(self, prompt, *, session_origin=None):
        if "请根据下面最高司法机关官方案例原文" in prompt:
            return {"case_summary": "summary", "issues": ["issue"]}
        return {
            "question": "问题",
            "options": ["A", "B", "C", "D"],
            "answer": "A",
            "explanation": "解释",
            "source_note": "练习题",
        }


class InvalidShapeLLM:
    def __init__(self, answer):
        self.answer = answer

    async def generate_json(self, prompt, *, session_origin=None):
        return {
            "question": "问题",
            "options": ["A", "B"],
            "answer": self.answer,
            "explanation": "解释",
        }


def case_item() -> CaseItem:
    return CaseItem(
        source_key="court_cases",
        source_item_key="1",
        title="典型案例",
        source_url="https://court.example/1",
        authority="最高人民法院",
        raw_text="案例原文：合同争议。",
        content_hash="hash",
        published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_learning_service_uses_stored_case_and_provider(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    storage.upsert_case_item(case_item())
    service = LearningService(storage, FakeLLM())

    result = await service.daily_case(date="2026-09-17")
    question = await service.generate_question(
        subject="民法", question_type="single_choice"
    )

    assert result["available"] is True
    assert result["source_url"] == "https://court.example/1"
    assert question["content"]["answer"] == "A"


@pytest.mark.asyncio
async def test_learning_service_degrades_without_provider(tmp_path) -> None:
    service = LearningService(SQLiteStorage(tmp_path / "runtime.sqlite3"), None)

    result = await service.generate_question()

    assert result == {"available": False, "reason": "LLM provider unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question_type, answer", [("true_false", "不确定"), ("multiple_choice", [])]
)
async def test_learning_service_rejects_invalid_answer_shape(
    tmp_path, question_type, answer
) -> None:
    service = LearningService(
        SQLiteStorage(tmp_path / f"{question_type}.sqlite3"),
        InvalidShapeLLM(answer),
    )

    result = await service.generate_question(origin="mock", question_type=question_type)

    assert result == {
        "available": False,
        "reason": "LLM response does not match requested question type",
    }
