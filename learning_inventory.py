from __future__ import annotations

import hashlib
import random
from typing import Any

try:
    from .content import (
        QUESTION_TYPE_LABELS,
        SUBJECT_LABELS,
        QuestionRequest,
        normalize_subject,
        subject_matches,
        validate_generated_question,
    )
    from .learning_card import ContentTooLongError, format_case_card
except ImportError:
    from content import (
        QUESTION_TYPE_LABELS,
        SUBJECT_LABELS,
        QuestionRequest,
        normalize_subject,
        subject_matches,
        validate_generated_question,
    )
    from learning_card import ContentTooLongError, format_case_card


class LearningContentProvider:
    """Select and validate persistent learning content for all daily callers."""

    def __init__(
        self,
        storage: Any,
        *,
        library_service: Any | None = None,
        llm_service: Any | None = None,
        rng: random.Random | None = None,
        daily_case_card_max_chars: int = 1800,
    ) -> None:
        self.storage = storage
        self.library_service = library_service
        self.llm_service = llm_service
        self.rng = rng or random.Random()
        self.daily_case_card_max_chars = daily_case_card_max_chars

    async def select_case(
        self,
        subject: str | None = None,
        date: str | None = None,
        content_type: str = "daily_case",
        session_origin: str | None = None,
        used_content_keys: set[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        requested_subject = _parse_subject(subject)
        date_value = date or "1970-01-01"
        if self.library_service is not None:
            bundles = self.library_service.official_case_bundles(
                subject=requested_subject or "", limit=500
            )
            bundles = [bundle for bundle in bundles if bundle.item.active]
            had_matching = bool(bundles)
            bundles = [
                bundle
                for bundle in bundles
                if not _is_used(used_content_keys, "official_case", bundle.item.id)
            ]
            if not bundles:
                return {
                    "available": False,
                    "reason": (
                        "匹配方向的独立官方案例已全部发送过"
                        if used_content_keys and had_matching and requested_subject
                        else "暂无匹配方向的独立官方案例"
                        if requested_subject
                        else (
                            "已存储的独立官方案例已全部发送过"
                            if used_content_keys and had_matching
                            else "暂无已存储的独立官方案例"
                        )
                    ),
                }
            selected = _select_for_day(bundles, date_value)
            result = self._case_bundle_result(selected, requested_subject)
            return self.validate_daily_item(
                result, content_type=content_type, subject=requested_subject
            )

        items = self.storage.list_case_items(limit=500)
        if requested_subject:
            items = [
                item
                for item in items
                if subject_matches(tuple(item.subjects), requested_subject)
            ]
        had_matching = bool(items)
        items = [
            item
            for item in items
            if not _is_used(used_content_keys, "official_case", item.source_item_key)
        ]
        if not items:
            return {
                "available": False,
                "reason": (
                    "匹配方向的官方案例已全部发送过"
                    if used_content_keys and had_matching and requested_subject
                    else "暂无匹配方向的官方案例"
                    if requested_subject
                    else (
                        "已存储的官方案例已全部发送过"
                        if used_content_keys and had_matching
                        else "暂无已存储的官方案例"
                    )
                ),
            }
        selected = _select_for_day(items, date_value)
        if self.llm_service is None:
            return {"available": False, "reason": "LLM provider unavailable"}
        result = await self.llm_service.generate_json(
            _legacy_case_prompt(selected), session_origin=session_origin
        )
        if not isinstance(result, dict):
            return {"available": False, "reason": "LLM provider unavailable"}
        response = {
            "available": True,
            "origin": "official_case",
            "case_id": selected.id,
            "source_kind": "official_case",
            "source_item_key": selected.source_item_key,
            "title": selected.title,
            "source_url": selected.source_url,
            "authority": selected.authority,
            "subject": requested_subject or _first_subject(selected.subjects),
            "content": result,
        }
        try:
            response["card_body"] = format_case_card(
                response, max_chars=self.daily_case_card_max_chars
            )
        except ContentTooLongError:
            return {"available": False, "reason": "案例学习卡片超过长度预算"}
        return self.validate_daily_item(
            response, content_type=content_type, subject=requested_subject
        )

    async def select_question(
        self,
        origin: str = "random",
        subject: str | None = None,
        question_type: str | None = None,
        session_origin: str | None = None,
        source_name: str | None = None,
        exam_year: str | None = None,
        used_content_keys: set[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        request = QuestionRequest.from_values(
            origin=origin,
            subject=subject,
            question_type=question_type,
            source_name=source_name,
            exam_year=exam_year,
        )
        all_real_candidates = self.storage.list_real_questions(
            subject=request.subject,
            question_type=request.question_type,
            source_name=request.source_name,
            exam_year=request.exam_year,
        )
        real_candidates = [
            question
            for question in all_real_candidates
            if not _is_used(used_content_keys, "real_question", question.id)
        ]
        all_mock_candidates = self._mock_question_bundles(
            request.subject, request.question_type
        )
        mock_candidates = [
            bundle
            for bundle in all_mock_candidates
            if not _is_used(used_content_keys, "library_mock", bundle.item.id)
        ]
        if request.origin == "real":
            if not real_candidates:
                return {
                    "available": False,
                    "reason": (
                        "匹配的已核验真题已全部发送过"
                        if used_content_keys and all_real_candidates
                        else "暂无匹配的已核验真题"
                    ),
                }
            return _real_question_result(self.rng.choice(real_candidates))

        selected_origin = request.origin
        if request.origin == "random":
            available_origins = []
            if real_candidates:
                available_origins.append("real")
            if mock_candidates or self.llm_service is not None:
                available_origins.append("mock")
            if not available_origins:
                return {
                    "available": False,
                    "reason": (
                        "匹配的题目库存已全部发送过，且没有可用 LLM Provider"
                        if used_content_keys
                        and (all_real_candidates or all_mock_candidates)
                        and self.llm_service is None
                        else "LLM provider unavailable"
                        if self.llm_service is None
                        else "暂无可用题目"
                    ),
                }
            selected_origin = self.rng.choice(available_origins)

        if selected_origin == "mock" and mock_candidates:
            result = self.validate_daily_item(
                self._mock_question_result(
                    self.rng.choice(mock_candidates), request.subject
                ),
                content_type="daily_question",
                subject=request.subject,
                question_type=request.question_type,
                origin="mock",
            )
            return _with_random_note(result, request.origin, real_candidates)
        if selected_origin == "real":
            if not real_candidates:
                return {
                    "available": False,
                    "reason": (
                        "匹配的已核验真题已全部发送过"
                        if used_content_keys and all_real_candidates
                        else "暂无匹配的已核验真题"
                    ),
                }
            return _real_question_result(self.rng.choice(real_candidates))
        if self.llm_service is None:
            return {
                "available": False,
                "reason": (
                    "匹配的模拟题已全部发送过，且没有可用 LLM Provider"
                    if used_content_keys and all_mock_candidates
                    else "暂无可用的持久化模拟题"
                ),
            }

        selected_subject = request.subject or self.rng.choice(
            (
                "criminal_law",
                "civil_commercial",
                "intellectual_property",
                "economic_law",
                "judicial_practice",
            )
        )
        selected_type = request.question_type or self.rng.choice(
            tuple(QUESTION_TYPE_LABELS)
        )
        result = await self.llm_service.generate_json(
            _question_prompt(selected_subject, selected_type),
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
        if self.library_service is None:
            return _with_random_note(
                self._generated_question_result(
                    result, selected_subject, selected_type
                ),
                request.origin,
                real_candidates,
            )
        archived = await self.library_service.archive_learning_material(
            raw_text=str(result.get("question") or ""),
            material_type="mock_question",
            title=f"{SUBJECT_LABELS.get(selected_subject, selected_subject)}模拟题",
            subjects=[selected_subject],
            structured_json={
                **result,
                "question_type": selected_type,
            },
            created_by="system:daily_question",
            session_origin=session_origin or "system:daily_question",
        )
        if not archived.get("success"):
            return {
                "available": False,
                "reason": archived.get("message") or "模拟题无法持久化",
            }
        bundle = self.library_service.repository.get(archived["item_id"])
        if bundle is None:
            return {"available": False, "reason": "模拟题持久化后无法读取"}
        return _with_random_note(
            self._mock_question_result(bundle, selected_subject),
            request.origin,
            real_candidates,
        )

    def validate_daily_item(
        self,
        content: dict[str, Any],
        *,
        content_type: str,
        subject: str | None = None,
        question_type: str | None = None,
        origin: str | None = None,
    ) -> dict[str, Any]:
        if not content.get("available"):
            return content
        if content_type == "daily_case":
            if content.get("origin") != "official_case":
                return {"available": False, "reason": "每日案例必须来自官方案例"}
            if subject and not subject_matches((content.get("subject", ""),), subject):
                return {"available": False, "reason": "案例方向不符合计划约束"}
            return content
        if content.get("origin") not in {"real", "mock"}:
            return {"available": False, "reason": "题目来源身份无效"}
        if origin and content.get("origin") != origin:
            return {"available": False, "reason": "题目来源不符合计划约束"}
        if subject and not subject_matches((content.get("subject", ""),), subject):
            return {"available": False, "reason": "题目方向不符合计划约束"}
        if question_type and content.get("question_type") != question_type:
            return {"available": False, "reason": "题型不符合计划约束"}
        return content

    def _mock_question_bundles(
        self, subject: str | None, question_type: str | None
    ) -> list[Any]:
        if self.library_service is None:
            return []
        normalized = self.library_service.repository.search(
            item_type="question",
            identity="mock_question",
            subject=subject or "",
            limit=500,
        )
        result = []
        for item in normalized:
            if item.id is None:
                continue
            bundle = self.library_service.repository.get(item.id)
            if bundle is None or not bundle.item.active or bundle.question is None:
                continue
            question = bundle.question
            if question_type and question.question_type != question_type:
                continue
            if not subject_matches(bundle.item.subjects, subject):
                continue
            if not validate_generated_question(
                {
                    "question": question.stem,
                    "options": list(question.options),
                    "answer": question.answer,
                    "explanation": question.explanation,
                    "questions": bundle.item.metadata.get("questions"),
                    "issues": bundle.item.metadata.get("issues"),
                },
                question.question_type,
            ):
                continue
            result.append(bundle)
        return result

    def _mock_question_result(
        self, bundle: Any, requested_subject: str | None
    ) -> dict[str, Any]:
        question = bundle.question
        assert question is not None
        subject = requested_subject or _first_subject(bundle.item.subjects)
        return {
            "available": True,
            "origin": "mock",
            "label": "模拟题",
            "subject": subject,
            "question_type": question.question_type,
            "question_id": bundle.item.id,
            "source_kind": "library_mock",
            "source_item_key": str(bundle.item.id),
            "content": {
                "question": question.stem,
                "options": list(question.options),
                "answer": question.answer,
                "explanation": question.explanation,
                **{
                    key: bundle.item.metadata[key]
                    for key in ("questions", "issues")
                    if key in bundle.item.metadata
                },
                "source_note": "持久化模拟题，非官方考试真题",
            },
            "source_note": "持久化模拟题，非官方考试真题",
        }

    def _case_bundle_result(
        self, bundle: Any, requested_subject: str | None
    ) -> dict[str, Any]:
        source = bundle.sources[0] if bundle.sources else None
        case = bundle.case
        subject = requested_subject or _first_subject(bundle.item.subjects)
        result: dict[str, Any] = {
            "available": True,
            "origin": "official_case",
            "case_id": bundle.item.id,
            "source_kind": "official_case",
            "source_item_key": str(bundle.item.id),
            "title": bundle.item.title,
            "source_url": source.source_url if source else "",
            "authority": case.authority if case else "",
            "subject": subject,
            "source_locator": (
                bundle.source_links[0].locator if bundle.source_links else ""
            ),
            "content": {
                "case_summary": case.case_summary
                if case
                else bundle.item.source_summary,
                "issues": list(case.issues) if case else [],
                "reasoning": case.reasoning if case else "",
                "practice_notes": list(case.practice_notes) if case else [],
            },
        }
        if source is not None:
            result["evidence_text"] = str(
                source.metadata.get("evidence_text") or bundle.item.source_summary
            )
        try:
            result["card_body"] = format_case_card(
                result, max_chars=self.daily_case_card_max_chars
            )
        except ContentTooLongError:
            result["available"] = False
            result["reason"] = "案例学习卡片超过长度预算"
        return result

    @staticmethod
    def _generated_question_result(
        content: dict[str, Any], subject: str, question_type: str
    ) -> dict[str, Any]:
        return {
            "available": True,
            "origin": "mock",
            "label": "模拟题",
            "subject": subject,
            "question_type": question_type,
            "content": content,
            "source_note": "AI 生成的原创练习题，非官方考试真题",
        }


def _parse_subject(value: Any) -> str | None:
    return QuestionRequest.from_values(subject=value).subject


def _select_for_day(items: list[Any], date_value: str) -> Any:
    digest = hashlib.sha256(str(date_value).encode("utf-8")).digest()
    return items[int.from_bytes(digest[:8], "big") % len(items)]


def _is_used(
    used_content_keys: set[tuple[str, str]] | None,
    source_kind: str,
    source_item_key: Any,
) -> bool:
    if used_content_keys is None:
        return False
    return (source_kind, str(source_item_key)) in used_content_keys


def _first_subject(subjects: Any) -> str | None:
    for value in subjects or ():
        normalized = normalize_subject(value)
        if normalized:
            return normalized
    return None


def _real_question_result(question: Any) -> dict[str, Any]:
    answer = question.answer
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
        "source_kind": "real_question",
        "source_item_key": str(question.id),
        "content": {
            "question": question.stem,
            "options": question.options,
            "answer": answer if answer is not None else "未提供（请以来源核验）",
            "explanation": question.explanation or "来源未提供可靠解析，未由 AI 补写。",
        },
    }


def _question_prompt(subject: str, question_type: str) -> str:
    return (
        "请生成一道法律学习练习题，严格返回 JSON，字段包括 question、options、answer、"
        "explanation。不要声称题目来自任何真实考试。"
        f"题型：{QUESTION_TYPE_LABELS.get(question_type, question_type)}；"
        f"方向：{SUBJECT_LABELS.get(subject, subject)}。"
    )


def _legacy_case_prompt(item: Any) -> str:
    return (
        "请根据下面最高司法机关官方案例原文生成 JSON，字段包括 case_summary、issues、reasoning、"
        f"practice_notes。不得加入原文无法支持的事实。标题：{item.title}；原文：{item.raw_text[:16000]}"
    )


def _with_random_note(
    result: dict[str, Any], origin: str, real_candidates: list[Any]
) -> dict[str, Any]:
    if origin == "random" and not real_candidates and result.get("available"):
        result["selection_note"] = "当前真题库没有可用记录，本次随机选择仅使用模拟题"
    return result


__all__ = ["LearningContentProvider"]
