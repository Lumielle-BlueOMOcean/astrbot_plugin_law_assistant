from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

if __package__ and "." in __package__:
    from .models import CaseItem
    from .storage import SQLiteStorage
else:
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
    ) -> None:
        self.storage = storage
        self.llm_service = llm_service
        self.timezone_name = timezone_name
        self.logger = logger or logging.getLogger(__name__)

    async def daily_case(
        self,
        *,
        date: str | None = None,
        session_origin: str | None = None,
    ) -> dict[str, Any]:
        items = self.storage.list_case_items(limit=500)
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
        return {
            "available": True,
            "case_id": selected.id,
            "source_url": selected.source_url,
            "authority": selected.authority,
            "content": result,
        }

    async def generate_question(
        self,
        *,
        subject: str = "",
        question_type: str = "single",
        session_origin: str | None = None,
    ) -> dict[str, Any]:
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
            _question_prompt(subject, question_type, evidence),
            session_origin=session_origin,
        )
        if not isinstance(result, dict):
            return {"available": False, "reason": "LLM provider unavailable"}
        required = {"question", "answer", "explanation"}
        if not required.issubset(result):
            return {
                "available": False,
                "reason": "LLM response missing required fields",
            }
        result.setdefault("is_original_practice", True)
        result.setdefault("disclaimer", "原创练习题，仅供学习，不构成法律意见")
        return {"available": True, "content": result}


def _select_for_day(items: list[CaseItem], date: str) -> CaseItem:
    digest = hashlib.sha256(date.encode()).digest()
    return items[int.from_bytes(digest[:4], "big") % len(items)]


def _case_prompt(item: CaseItem) -> str:
    return (
        "请根据下面最高司法机关官方案例原文生成 JSON，字段包括："
        "case_summary、issues、reasoning、practice_notes。不得加入原文无法支持的事实。\n"
        f"来源机关：{item.authority}\n标题：{item.title}\n原文：{item.raw_text[:16000]}"
    )


def _question_prompt(subject: str, question_type: str, evidence: str = "") -> str:
    return (
        "请生成一道法律学习练习题，严格返回 JSON，字段包括：question、options（"
        "可选数组）、answer、explanation、source_note。题目只用于学习，不构成法律意见。"
        f"题型：{question_type}\n主题：{subject or '法律基础'}\n"
        f"可参考的已抓取官方材料（不得超出其事实）：{evidence}"
    )


__all__ = ["LearningService"]
