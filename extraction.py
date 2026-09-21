from __future__ import annotations

import inspect
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

if __package__ and "." in __package__:
    from .date_parser import (
        date_is_on_or_after,
        extract_publication_datetime,
        extract_publication_year,
        parse_chinese_dates,
        parse_datetime_value,
    )
    from .models import EventDate, LegalEvent, SourceDocument
    from .sources.html import html_to_text
else:
    from date_parser import (
        date_is_on_or_after,
        extract_publication_datetime,
        extract_publication_year,
        parse_chinese_dates,
        parse_datetime_value,
    )
    from models import EventDate, LegalEvent, SourceDocument
    from sources.html import html_to_text


EVENT_KEYWORDS = (
    "法律硕士",
    "法律专业学位研究生",
    "法律文书",
    "模拟法庭",
    "案例大赛",
    "竞赛",
    "大赛",
    "征文",
    "征集",
    "论坛",
    "研讨会",
    "培训",
    "实习",
    "招募",
    "涉外法治",
    "仲裁",
    "谈判",
    "知识产权",
)
ACTION_KEYWORDS = (
    "报名",
    "参赛",
    "投稿",
    "征集",
    "培训",
    "论坛",
    "研讨",
    "截止",
    "学生",
    "研究生",
    "招募",
)
HISTORICAL_KEYWORDS = (
    "已结束",
    "获奖名单",
    "赛事回顾",
    "圆满举办",
    "往届",
    "决赛结果",
    "颁奖典礼",
)
IRRELEVANT_TITLE_KEYWORDS = ("绿化维护", "设备维护", "停电通知", "食堂安排")


class EventExtractor:
    """Rules-first extraction with optional evidence-constrained LLM enrichment."""

    def __init__(
        self,
        *,
        timezone_name: str = "Asia/Shanghai",
        now: Any | None = None,
        llm_service: Any | None = None,
        activity_keywords: tuple[str, ...] = (),
        action_keywords: tuple[str, ...] = (),
        historical_keywords: tuple[str, ...] = (),
    ) -> None:
        self.timezone_name = timezone_name
        self.zone = ZoneInfo(timezone_name)
        self.now = now or (lambda: datetime.now(self.zone))
        self.llm_service = llm_service
        self.activity_keywords = tuple(
            dict.fromkeys((*EVENT_KEYWORDS, *activity_keywords))
        )
        self.action_keywords = tuple(
            dict.fromkeys((*ACTION_KEYWORDS, *action_keywords))
        )
        self.historical_keywords = tuple(
            dict.fromkeys((*HISTORICAL_KEYWORDS, *historical_keywords))
        )

    async def extract(
        self,
        document: SourceDocument,
        *,
        session_origin: str | None = None,
    ) -> list[LegalEvent]:
        text = html_to_text(document.content) or document.content
        if not _is_relevant(
            document.title,
            text,
            activity_keywords=self.activity_keywords,
            action_keywords=self.action_keywords,
        ):
            return []

        published_at = extract_publication_datetime(
            text, timezone_name=self.timezone_name
        )
        publication_year = extract_publication_year(
            text, fallback=_as_datetime(document.fetched_at, self.zone).year
        )
        dates = [
            date
            for date in parse_chinese_dates(
                text,
                publication_year=publication_year,
                timezone_name=self.timezone_name,
            )
            if (published_at is None or date.datetime != published_at)
            and not (date.kind == "event" and _looks_like_publication(date))
        ]
        now = _as_datetime(self.now(), self.zone)
        event = LegalEvent(
            source_key=document.source_key,
            source_item_key=document.source_item_key,
            title=document.title.strip() or "未命名法律活动",
            source_url=document.url,
            organizer=_first_match(
                text,
                r"(?:主办单位|主办方|由)(?:为|是)?\s*([^。；\n]{2,80})",
            ),
            event_type=_event_type(document.title, text),
            eligibility=_first_match(
                text,
                r"(?:参赛对象|参会对象|面向对象|报名对象|适用对象)\s*[:：]?\s*([^。；\n]{2,160})",
            ),
            status=_status_for_dates(dates, now),
            raw_content_hash=document.content_hash,
            discovered_at=now.astimezone(timezone.utc),
            updated_at=now.astimezone(timezone.utc),
            metadata={
                "source_type": "html",
                "extraction": "rules_first",
                "attachments": list(document.attachments),
                "published_year": publication_year,
                "participation_evidence": any(
                    word in text for word in self.action_keywords
                ),
                "historical_signals": [
                    word for word in self.historical_keywords if word in text
                ],
            },
            dates=tuple(dates),
            registration_method=_registration_method(text),
            summary=_summary(text),
            source_published_at=published_at,
            last_seen_at=now.astimezone(timezone.utc),
        )

        if self.llm_service is not None:
            llm_result = await _maybe_await(
                self.llm_service.generate_json(
                    _llm_prompt(document, text), session_origin=session_origin
                )
            )
            if isinstance(llm_result, dict):
                if llm_result.get("relevant") is False:
                    return []
                event = _merge_llm_fields(event, llm_result, text, self.zone)
        return [event]


def _is_relevant(
    title: str,
    text: str,
    *,
    activity_keywords: tuple[str, ...] = EVENT_KEYWORDS,
    action_keywords: tuple[str, ...] = ACTION_KEYWORDS,
) -> bool:
    if any(word in title for word in IRRELEVANT_TITLE_KEYWORDS):
        return False
    combined = f"{title}\n{text}"
    return any(word in combined for word in activity_keywords) and any(
        word in combined for word in action_keywords
    )


def _looks_like_publication(date: EventDate) -> bool:
    return bool(re.fullmatch(r"\d{4}[/\-]\d{1,2}[/\-]\d{1,2}", date.evidence_text))


def _event_type(title: str, text: str) -> str:
    value = f"{title} {text}"
    if any(word in value for word in ("竞赛", "大赛", "比赛", "模拟法庭")):
        return "competition"
    if "培训" in value:
        return "training"
    if any(word in value for word in ("论坛", "研讨会")):
        return "forum"
    if any(word in value for word in ("征文", "征集")):
        return "call_for_submissions"
    if "实习" in value:
        return "internship"
    return "activity"


def _status_for_dates(dates: list[EventDate], now: datetime) -> str:
    valid = [date for date in dates if date.confirmed and date.datetime]
    if not valid:
        return "UNKNOWN"
    deadlines = [
        date
        for date in dates
        if date.confirmed and date.datetime and date.kind.endswith("deadline")
    ]
    if deadlines and all(
        not date_is_on_or_after(
            date.datetime,
            precision=date.precision,
            now=now,
            timezone_name=date.timezone,
        )
        for date in deadlines
    ):
        return "CLOSED"
    for date in deadlines:
        local_now = now.astimezone(ZoneInfo(date.timezone))
        local_value = date.datetime.astimezone(ZoneInfo(date.timezone))
        if date.precision == "date":
            remaining_days = (local_value.date() - local_now.date()).days
            if 0 <= remaining_days <= 7:
                return "DEADLINE_SOON"
        elif 0 <= (local_value - local_now).total_seconds() <= 7 * 86400:
            return "DEADLINE_SOON"
    return "OPEN" if deadlines else "UPCOMING"


def _value_precision(value: str) -> str:
    return "minute" if re.search(r"(?:T|\s)\d{1,2}:\d{2}", value) else "date"


def _llm_date_is_supported(
    source_text: str,
    evidence: str,
    raw: dict[str, Any],
    value: datetime,
    kind: str,
    zone: ZoneInfo,
) -> bool:
    """Accept an LLM date only when source text proves value and semantic kind."""
    if not _evidence_matches(source_text, evidence):
        return False
    evidence_dates = parse_chinese_dates(
        evidence, publication_year=value.year, timezone_name=zone.key
    )
    if not evidence_dates:
        return False
    precision = _value_precision(str(raw.get("value") or ""))
    for evidence_date in evidence_dates:
        if evidence_date.datetime is None:
            continue
        if evidence_date.datetime.date() != value.astimezone(zone).date():
            continue
        if evidence_date.kind != kind:
            continue
        if precision == "minute":
            if evidence_date.precision != "minute":
                continue
            if evidence_date.datetime.astimezone(zone).replace(
                tzinfo=None
            ) != value.astimezone(zone).replace(tzinfo=None):
                continue
        return True
    return False


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return " ".join(match.group(1).split()) if match else ""


def _registration_method(text: str) -> str:
    for pattern in (
        r"(?:报名|提交|投稿)[^。；\n]{0,80}(?:电子邮件|邮箱|邮件|网站|系统|平台)[^。；\n]{0,80}",
        r"(?:报名|提交|投稿)[^。；\n]{0,100}",
    ):
        value = _first_match(text, f"({pattern})")
        if value:
            return value
    return ""


def _summary(text: str) -> str:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    useful = [line for line in paragraphs if len(line) > 12]
    return " ".join(useful[:2])[:300]


def _merge_llm_fields(
    event: LegalEvent,
    result: dict[str, Any],
    source_text: str,
    zone: ZoneInfo,
) -> LegalEvent:
    values: dict[str, Any] = {}
    for field in (
        "title",
        "organizer",
        "event_type",
        "eligibility",
        "summary",
        "registration_method",
    ):
        value = result.get(field)
        if isinstance(value, str) and value.strip():
            values[field] = value.strip()
    raw_dates = result.get("dates")
    if isinstance(raw_dates, list):
        llm_dates: list[EventDate] = []
        for raw in raw_dates:
            if not isinstance(raw, dict):
                continue
            value = parse_datetime_value(
                str(raw.get("value", "")),
                reference_year=_as_datetime(event.discovered_at, zone).year,
                timezone_name=str(raw.get("timezone") or zone.key),
            )
            if value is None:
                continue
            evidence = str(
                raw.get("evidence") or raw.get("evidence_text") or ""
            ).strip()
            llm_dates.append(
                EventDate(
                    kind=str(raw.get("kind") or "event"),
                    datetime=value,
                    timezone=str(raw.get("timezone") or zone.key),
                    label=str(raw.get("label") or raw.get("kind") or "活动时间"),
                    evidence_text=evidence,
                    confirmed=_llm_date_is_supported(
                        source_text,
                        evidence,
                        raw,
                        value,
                        str(raw.get("kind") or "event"),
                        zone,
                    ),
                    precision=_value_precision(str(raw.get("value") or "")),
                )
            )
        if llm_dates:
            values["dates"] = tuple(_merge_dates(event.dates, llm_dates))
    if "status" not in values:
        dates = values.get("dates", event.dates)
        values["status"] = _status_for_dates(list(dates), event.updated_at)
    return _replace_event(event, **values)


def _merge_dates(
    existing: tuple[EventDate, ...], additions: list[EventDate]
) -> list[EventDate]:
    result = list(existing)
    seen = {(date.kind, date.datetime) for date in result}
    for date in additions:
        key = (date.kind, date.datetime)
        if key not in seen:
            result.append(date)
            seen.add(key)
    return result


def _evidence_matches(source_text: str, evidence: str) -> bool:
    if not evidence:
        return False
    normalized_source = _normalize_evidence(source_text)
    normalized_evidence = _normalize_evidence(evidence)
    return normalized_evidence in normalized_source


def _normalize_evidence(value: str) -> str:
    return re.sub(r"[\s，。；：:、,.!?！？（）()\[\]【】]", "", value)


def _llm_prompt(document: SourceDocument, text: str) -> str:
    return (
        "请只根据以下官方通知原文返回 JSON。若不是法律活动，relevant=false；"
        "日期必须给出原文 evidence，不能臆造。字段可含 relevant、event_type、"
        "organizer、eligibility、registration_method、summary、dates。\n"
        f"标题：{document.title}\n原文：{text[:12000]}"
    )


def _replace_event(event: LegalEvent, **values: Any) -> LegalEvent:
    from dataclasses import replace

    return replace(event, **values)


def _as_datetime(value: datetime | str, zone: ZoneInfo) -> datetime:
    if isinstance(value, str):
        parsed = parse_datetime_value(value, timezone_name=zone.key)
        if parsed is not None:
            value = parsed
        else:
            return datetime.now(zone)
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["EventExtractor"]
