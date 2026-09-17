from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

if __package__ and "." in __package__:
    from .models import EventDate
else:
    from models import EventDate

_DATE_RE = re.compile(
    r"(?:"
    r"(?P<year_date>\d{4}年\d{1,2}月\d{1,2}日)"
    r"|(?P<iso_date>\d{4}[-/]\d{1,2}[-/]\d{1,2})"
    r"|(?P<month_date>\d{1,2}月\d{1,2}日)"
    r")"
    r"(?P<meridiem>上午|下午|中午|晚上)?"
    r"(?:\s*(?P<hour>\d{1,2})[:：](?P<minute>\d{2}))?"
)
_PUBLICATION_RE = re.compile(
    r"(?:发布时间|发布日期|发布于|公布日期|公布于|发布)\s*[:：]?\s*"
    r"(?P<value>\d{4}(?:年|[-/])\d{1,2}(?:月|[-/])\d{1,2}日?)"
)


def parse_chinese_dates(
    text: str,
    *,
    publication_year: int | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> list[EventDate]:
    """Parse common Chinese notice dates and infer a timeline kind from nearby words."""
    zone = ZoneInfo(timezone_name)
    reference_year = publication_year or datetime.now(zone).year
    result: list[EventDate] = []
    seen: set[tuple[str, str | None]] = set()

    for match in _DATE_RE.finditer(text):
        value = _parse_match(match, reference_year, zone)
        if value is None:
            continue
        kind = _infer_kind(text, match.start())
        evidence = _evidence_text(text, match.start(), match.end(), kind)
        key = (kind, value.isoformat())
        if key in seen:
            continue
        seen.add(key)
        result.append(
            EventDate(
                kind=kind,
                datetime=value,
                timezone=timezone_name,
                label=_label_for_kind(kind),
                evidence_text=evidence,
                confirmed=True,
            )
        )
    return result


def parse_datetime_value(
    value: str,
    *,
    reference_year: int | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> datetime | None:
    """Parse an LLM date value or a single notice date into an aware datetime."""
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        matches = list(
            parse_chinese_dates(
                candidate,
                publication_year=reference_year,
                timezone_name=timezone_name,
            )
        )
        return matches[0].datetime if matches else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed


def extract_publication_year(text: str, fallback: int | None = None) -> int:
    match = _PUBLICATION_RE.search(text)
    if match:
        found = re.search(r"\d{4}", match.group("value"))
        if found:
            return int(found.group())
    found = re.search(r"20\d{2}", text)
    return (
        int(found.group()) if found else (fallback or datetime.now(timezone.utc).year)
    )


def extract_publication_datetime(
    text: str,
    *,
    timezone_name: str = "Asia/Shanghai",
) -> datetime | None:
    match = _PUBLICATION_RE.search(text)
    if match:
        return parse_datetime_value(
            match.group("value"),
            timezone_name=timezone_name,
        )
    for line in text.splitlines()[:4]:
        candidate = line.strip()
        if re.fullmatch(r"20\d{2}[/\-]\d{1,2}[/\-]\d{1,2}", candidate):
            return parse_datetime_value(candidate, timezone_name=timezone_name)
    return None


def _parse_match(match: re.Match[str], reference_year: int, zone: ZoneInfo):
    raw = match.group(0)
    try:
        if match.group("year_date"):
            year, month, day = map(int, re.findall(r"\d+", match.group("year_date")))
        elif match.group("iso_date"):
            year, month, day = map(int, re.split(r"[-/]", match.group("iso_date")))
        else:
            year = reference_year
            month, day = map(int, re.findall(r"\d+", match.group("month_date")))
        hour_text = match.group("hour")
        minute = int(match.group("minute") or 0)
        if hour_text is None:
            meridiem = match.group("meridiem")
            hour = {
                "上午": 9,
                "下午": 15,
                "中午": 12,
                "晚上": 20,
            }.get(meridiem, 23 if _looks_like_deadline(raw) else 0)
        else:
            hour = int(hour_text)
            meridiem = match.group("meridiem")
            if meridiem in {"下午", "晚上"} and hour < 12:
                hour += 12
            if meridiem == "中午" and hour < 11:
                hour += 12
        return datetime(year, month, day, hour, minute, tzinfo=zone)
    except ValueError:
        return None


def _looks_like_deadline(value: str) -> bool:
    return any(word in value for word in ("截止", "截至"))


def _infer_kind(text: str, position: int) -> str:
    boundary = max(
        text.rfind(mark, 0, position) for mark in ("。", "；", "，", "\n", "！", "？")
    )
    context = text[boundary + 1 : position + 10]
    if any(
        word in context for word in ("投稿截止", "交稿截止", "提交截止", "作品截止")
    ):
        return "submission_deadline"
    if "报名" in context and any(word in context for word in ("截止", "截至")):
        return "registration_deadline"
    if "报名" in context:
        return "registration_open"
    if "初赛" in context:
        return "preliminary"
    if "复赛" in context:
        return "semifinal"
    if "决赛" in context:
        return "final"
    if any(word in context for word in ("结果", "公布名单", "获奖名单")):
        return "result"
    if any(word in context for word in ("截止", "截至")):
        return "registration_deadline"
    return "event"


def _evidence_text(text: str, start: int, end: int, kind: str) -> str:
    boundary = max(
        text.rfind(mark, 0, start) for mark in ("。", "；", "\n", "！", "？")
    )
    evidence = " ".join(text[boundary + 1 : end].split())
    if kind in {"submission_deadline", "registration_deadline"}:
        evidence = re.sub(r"^(?:初赛|复赛|决赛)\s*", "", evidence)
    return evidence or text[start:end]


def _label_for_kind(kind: str) -> str:
    return {
        "registration_open": "报名开始",
        "registration_deadline": "报名截止",
        "submission_deadline": "投稿截止",
        "preliminary": "初赛",
        "semifinal": "复赛",
        "final": "决赛",
        "result": "结果公布",
        "event": "活动时间",
    }.get(kind, "其他时间")
