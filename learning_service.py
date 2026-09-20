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
        QuestionRequest,
        RealQuestion,
        normalize_subject,
        parse_subject,
        subject_matches,
        validate_generated_question,
    )
    from .learning_card import ContentTooLongError, format_case_card
    from .models import CaseItem
    from .storage import SQLiteStorage
else:
    from content import (
        QUESTION_TYPE_LABELS,
        SUBJECT_LABELS,
        QuestionRequest,
        RealQuestion,
        normalize_subject,
        parse_subject,
        subject_matches,
        validate_generated_question,
    )
    from learning_card import ContentTooLongError, format_case_card
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

    async def daily_case(
        self,
        *,
        date: str | None = None,
        subject: str | None = None,
        session_origin: str | None = None,
    ) -> dict[str, Any]:
        requested_subject = parse_subject(subject)
        if self.library_service is not None:
            bundles = self.library_service.official_case_bundles(
                subject=requested_subject or "", limit=500
            )
            if not bundles:
                return {
                    "available": False,
                    "reason": (
                        "暂无匹配方向的独立官方案例"
                        if requested_subject
                        else "暂无已存储的独立官方案例"
                    ),
                }
            selected = _select_for_day(
                bundles,
                date or datetime.now(ZoneInfo(self.timezone_name)).date().isoformat(),
            )
            if self.llm_service is None:
                return {"available": False, "reason": "LLM provider unavailable"}
            evidence = _official_case_evidence(selected)
            prompt = _official_case_prompt(selected, evidence)
            result = await self.llm_service.generate_json(
                prompt,
                session_origin=session_origin,
            )
            if not isinstance(result, dict):
                return {"available": False, "reason": "LLM provider unavailable"}
            source = selected.sources[0] if selected.sources else None
            subjects = tuple(selected.item.subjects)
            selected_subject = requested_subject or next(
                (
                    normalize_subject(value)
                    for value in subjects
                    if normalize_subject(value)
                ),
                None,
            )
            response = {
                "available": True,
                "case_id": selected.item.id,
                "title": selected.item.title,
                "source_url": source.source_url if source else "",
                "authority": selected.case.authority if selected.case else "",
                "subject": selected_subject,
                "content": result,
                "evidence_text": evidence,
                "source_locator": (
                    selected.source_links[0].locator if selected.source_links else ""
                ),
            }
            try:
                response["card_body"] = format_case_card(
                    response, max_chars=self.daily_case_card_max_chars
                )
            except ContentTooLongError:
                compact = await self.llm_service.generate_json(
                    prompt
                    + f"\n请在 {self.daily_case_card_max_chars} 字符内重新整理，不得省略来源证据支持的关键结论。",
                    session_origin=session_origin,
                )
                if not isinstance(compact, dict):
                    return {
                        "available": False,
                        "error": "content_too_long",
                        "reason": "案例学习卡片超过长度预算且无法压缩",
                    }
                response["content"] = compact
                try:
                    response["card_body"] = format_case_card(
                        response, max_chars=self.daily_case_card_max_chars
                    )
                except ContentTooLongError:
                    return {
                        "available": False,
                        "error": "content_too_long",
                        "reason": "案例学习卡片超过长度预算且无法压缩",
                    }
            return response
        items = self.storage.list_case_items(limit=500)
        if requested_subject:
            items = [
                item
                for item in items
                if subject_matches(
                    tuple(item.subjects)
                    or tuple(item.metadata.get("subjects", ()))
                    or (
                        (item.metadata.get("subject"),)
                        if item.metadata.get("subject")
                        else ()
                    ),
                    requested_subject,
                )
            ]
            if not items:
                return {"available": False, "reason": "暂无匹配方向的官方案例"}
        if not items:
            return {"available": False, "reason": "暂无已存储的官方案例"}
        unpublished = [
            item
            for item in items
            if item.id is not None and item.id not in self.storage.published_case_ids()
        ]
        items = unpublished or items
        selected = _select_for_day(
            items,
            date or datetime.now(ZoneInfo(self.timezone_name)).date().isoformat(),
        )
        if self.llm_service is None:
            return {"available": False, "reason": "LLM provider unavailable"}
        result = await self.llm_service.generate_json(
            _case_prompt(selected), session_origin=session_origin
        )
        if not isinstance(result, dict):
            return {"available": False, "reason": "LLM provider unavailable"}
        response = {
            "available": True,
            "case_id": selected.id,
            "title": selected.title,
            "source_url": selected.source_url,
            "authority": selected.authority,
            "subject": requested_subject
            or next(
                (
                    normalize_subject(value)
                    for value in (
                        tuple(selected.subjects)
                        or tuple(selected.metadata.get("subjects", ()))
                    )
                    if normalize_subject(value)
                ),
                None,
            ),
            "content": result,
        }
        try:
            response["card_body"] = format_case_card(
                response, max_chars=self.daily_case_card_max_chars
            )
        except ContentTooLongError:
            return {
                "available": False,
                "error": "content_too_long",
                "reason": "案例学习卡片超过长度预算且无法压缩",
            }
        return response

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
        request = QuestionRequest.from_values(
            origin=origin,
            subject=subject,
            question_type=question_type,
            source_name=source_name,
            exam_year=exam_year,
        )
        candidates = self.storage.list_real_questions(
            subject=request.subject,
            question_type=request.question_type,
            source_name=request.source_name,
            exam_year=request.exam_year,
            exclude_ids=self.storage.published_question_ids(),
        )
        if not candidates:
            candidates = self.storage.list_real_questions(
                subject=request.subject,
                question_type=request.question_type,
                source_name=request.source_name,
                exam_year=request.exam_year,
            )
        selected_origin = request.origin
        if request.origin == "real":
            if not candidates:
                return {"available": False, "reason": "暂无匹配的已核验真题"}
            return _real_question_result(self.rng.choice(candidates))
        if request.origin == "random":
            if candidates and self.rng.choice((True, False)):
                return _real_question_result(self.rng.choice(candidates))
            selected_origin = "mock"

        selected_subject = request.subject or self.rng.choice(
            (
                "criminal_law",
                "civil_commercial",
                "intellectual_property",
                "economic_law",
            )
        )
        selected_type = request.question_type or self.rng.choice(
            tuple(QUESTION_TYPE_LABELS)
        )
        if self.llm_service is None:
            return {"available": False, "reason": "LLM provider unavailable"}
        evidence = ""
        cases = self.storage.list_case_items(limit=1)
        if cases:
            evidence = cases[0].raw_text[:8000]
        else:
            updates = self.storage.list_law_updates(limit=1)
            if updates:
                evidence = updates[0].raw_text[:8000]
        result = await self.llm_service.generate_json(
            _question_prompt(selected_subject, selected_type, evidence),
            session_origin=session_origin,
        )
        if not validate_generated_question(result, selected_type):
            return {
                "available": False,
                "reason": "LLM response does not match requested question type",
            }
        result = dict(result)
        result["is_original_practice"] = True
        result["disclaimer"] = "原创练习题，仅供学习，不构成法律意见"
        response = {
            "available": True,
            "origin": selected_origin,
            "subject": selected_subject,
            "question_type": selected_type,
            "content": result,
            "label": "模拟题",
            "source_note": "AI 生成的原创练习题，非官方考试真题",
        }
        if request.origin == "random" and not candidates:
            response["selection_note"] = (
                "当前真题库没有可用记录，本次随机选择仅使用模拟题"
            )
        return response

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
