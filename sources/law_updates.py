from __future__ import annotations

import hashlib
import re
from datetime import datetime

if __package__ and "." in __package__:
    from ..date_parser import parse_datetime_value
    from ..models import LawUpdate, SourceDocument
    from .html import html_to_text
else:
    from date_parser import parse_datetime_value
    from models import LawUpdate, SourceDocument
    from sources.html import html_to_text


class LawUpdateExtractor:
    """Deterministically extract dates and category from an official law notice."""

    def __init__(self, *, timezone_name: str = "Asia/Shanghai"):
        self.timezone_name = timezone_name

    def extract(self, document: SourceDocument) -> LawUpdate:
        text = html_to_text(document.content) or document.content
        promulgation = _date_after(text, ("公布日期", "发布日期", "颁布日期"))
        effective = _date_after(text, ("施行日期", "生效日期", "自", "起施行"))
        category = _category(document.title, text)
        return LawUpdate(
            source_key=document.source_key,
            source_item_key=document.source_item_key,
            title=document.title,
            category=category,
            source_url=document.url,
            status="current",
            content_hash=document.content_hash
            or hashlib.sha256(text.encode()).hexdigest(),
            promulgation_date=promulgation,
            effective_date=effective,
            raw_text=text,
            metadata={"content_type": document.content_type},
        )


def _date_after(text: str, labels: tuple[str, ...]) -> datetime | None:
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*[:：]?\s*"
            r"(?P<value>20\d{2}(?:年\d{1,2}月\d{1,2}日|[-/]\d{1,2}[-/]\d{1,2}))",
            text,
        )
        if match:
            return parse_datetime_value(match.group("value"))
    return None


def _category(title: str, text: str) -> str:
    value = f"{title} {text}"
    for keyword, category in (
        ("司法解释", "司法解释"),
        ("行政法规", "行政法规"),
        ("部门规章", "部门规章"),
        ("法律", "法律"),
        ("条例", "行政法规"),
    ):
        if keyword in value:
            return category
    return "法律规范性文件"


__all__ = ["LawUpdateExtractor"]
