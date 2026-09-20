from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

if __package__ and "." in __package__:
    from .content import (
        QUESTION_TYPE_LABELS,
        SUBJECT_LABELS,
        RealQuestion,
    )
    from .learning_inventory import LearningContentProvider
    from .models import CaseItem
    from .storage import SQLiteStorage
else:
    from content import (
        QUESTION_TYPE_LABELS,
        SUBJECT_LABELS,
        RealQuestion,
    )
    from learning_inventory import LearningContentProvider
    from models import CaseItem
    from storage import SQLiteStorage


class LearningService:
    """Generate optional study material from stored official evidence."""

    def __init__(
        self,
        storage: SQLiteStorage,
        llm_service: Any | None,
        *,
        timezone_name: str = "Asia/Shanghai",
        logger: Any | None = None,
        rng: random.Random | None = None,
        library_service: Any | None = None,
        daily_case_card_max_chars: int = 1800,
    ) -> None:
        self.storage = storage
        self.llm_service = llm_service
        self.timezone_name = timezone_name
        self.logger = logger or logging.getLogger(__name__)
        self.rng = rng or random.Random()
        self.library_service = library_service
        self.daily_case_card_max_chars = daily_case_card_max_chars
        self.content_provider = LearningContentProvider(
            storage,
            library_service=library_service,
            llm_service=llm_service,
            rng=self.rng,
            daily_case_card_max_chars=daily_case_card_max_chars,
        )

    def _provider(self) -> LearningContentProvider:
        self.content_provider.library_service = self.library_service
        self.content_provider.llm_service = self.llm_service
        self.content_provider.daily_case_card_max_chars = self.daily_case_card_max_chars
        return self.content_provider

    async def daily_case(
        self,
        *,
        date: str | None = None,
        subject: str | None = None,
        session_origin: str | None = None,
    ) -> dict[str, Any]:
        requested_date = (
            date or datetime.now(ZoneInfo(self.timezone_name)).date().isoformat()
        )
        return await self._provider().select_case(
            subject=subject,
            date=requested_date,
            session_origin=session_origin,
        )

    async def generate_question(
        self,
        *,
        subject: str = "",
        question_type: str | None = None,
        origin: str = "random",
        source_name: str | None = None,
        exam_year: str | None = None,
        session_origin: str | None = None,
    ) -> dict[str, Any]:
        return await self._provider().select_question(
            origin=origin,
            subject=subject,
            question_type=question_type,
            source_name=source_name,
            exam_year=exam_year,
            session_origin=session_origin,
        )

    def import_real_questions(
        self, records: list[RealQuestion | dict[str, Any]]
    ) -> int:
        return self.storage.import_real_questions(records)

    def import_real_questions_file(self, path: str) -> int:
        try:
            from .content import load_real_questions
        except ImportError:
            from content import load_real_questions

        return self.import_real_questions(load_real_questions(path))

    def real_question_inventory(self) -> dict[str, Any]:
        return self.storage.real_question_inventory()


def _select_for_day(items: list[Any], date: str) -> Any:
    digest = hashlib.sha256(date.encode()).digest()
    return items[int.from_bytes(digest[:4], "big") % len(items)]


def _case_prompt(item: CaseItem) -> str:
    return (
        "请根据下面最高司法机关官方案例原文生成 JSON，字段包括："
        "case_summary、issues、reasoning、practice_notes。不得加入原文无法支持的事实。\n"
        f"来源机关：{item.authority}\n标题：{item.title}\n原文：{item.raw_text[:16000]}"
    )


def _official_case_evidence(bundle: Any) -> str:
    metadata = bundle.item.metadata if bundle is not None else {}
    evidence = str(metadata.get("evidence_text") or "").strip()
    if evidence:
        return evidence[:16000]
    if bundle.case is not None and bundle.case.case_summary:
        return bundle.case.case_summary[:16000]
    return bundle.item.source_summary[:16000]


def _official_case_prompt(bundle: Any, evidence: str) -> str:
    authority = bundle.case.authority if bundle.case else "官方来源"
    return (
        "请根据下面一条已经从官方合集独立拆分并定位的案例原文生成 JSON，字段包括："
        "case_summary、issues、reasoning、practice_notes。不得加入原文无法支持的事实。\n"
        f"来源机关：{authority}\n标题：{bundle.item.title}\n独立案例证据：{evidence}"
    )


def _question_prompt(subject: str, question_type: str, evidence: str = "") -> str:
    structure = {
        "single_choice": "options 至少两个且 answer 只能是一个正确选项",
        "multiple_choice": "options 至少两个且 answer 必须包含一个或多个正确选项",
        "true_false": "answer 必须明确为正确或错误",
        "short_answer": "answer 必须是参考答案和要点",
        "case_analysis": "必须提供 questions 或 issues，以及 answer 和 explanation",
    }.get(question_type, "严格匹配题型")
    return (
        "请生成一道法律学习练习题，严格返回 JSON，字段包括：question、options（"
        "可选数组）、answer、explanation、source_note。题目只用于学习，不构成法律意见。"
        f"不要声称题目来自任何真实考试；{structure}。"
        f"题型：{QUESTION_TYPE_LABELS.get(question_type, question_type)}\n主题：{SUBJECT_LABELS.get(subject, subject or '法律基础')}\n"
        f"可参考的已抓取官方材料（不得超出其事实）：{evidence}"
    )


def _real_question_result(question: RealQuestion) -> dict[str, Any]:
    answer = question.answer
    content = {
        "question": question.stem,
        "options": question.options,
        "answer": answer if answer is not None else "未提供（请以来源核验）",
        "explanation": question.explanation or "来源未提供可靠解析，未由 AI 补写。",
    }
    return {
        "available": True,
        "origin": "real",
        "label": "真题",
        "subject": question.subject,
        "question_type": question.question_type,
        "source_name": question.source_name,
        "exam_name": question.exam_name,
        "exam_year": question.exam_year,
        "exam_date": question.exam_date,
        "paper": question.paper,
        "question_number": question.question_number,
        "source_url": question.source_url,
        "source_locator": question.source_locator,
        "answer_source": question.answer_source,
        "verification_status": question.verification_status,
        "content_hash": question.content_hash,
        "question_id": question.id,
        "content": content,
    }


__all__ = ["LearningService"]
