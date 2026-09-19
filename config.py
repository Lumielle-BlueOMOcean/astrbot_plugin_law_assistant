from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

if __package__ and "." in __package__:
    from .content import (
        normalize_origin,
        normalize_question_type,
        normalize_selection_mode,
        normalize_subject,
    )
else:
    from content import (
        normalize_origin,
        normalize_question_type,
        normalize_selection_mode,
        normalize_subject,
    )

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_SCAN_INTERVAL_MINUTES = 60
MIN_SCAN_INTERVAL_MINUTES = 5
MAX_SCAN_INTERVAL_MINUTES = 24 * 60
DEFAULT_DEADLINE_REMINDER_DAYS = (7, 3, 1)
DEFAULT_DAILY_TIME = "08:00"
DEFAULT_HTTP_TIMEOUT_SECONDS = 20


@dataclass(frozen=True, slots=True)
class PluginConfig:
    operator_ids: tuple[str, ...]
    timezone: str
    auto_scan_enabled: bool
    scan_interval_minutes: int
    llm_provider_id: str
    extra_event_source_urls: tuple[str, ...]
    auto_publish_events: bool
    deadline_reminder_days: tuple[int, ...]
    deadline_same_day_enabled: bool
    daily_case_enabled: bool
    daily_case_time: str
    daily_case_selection_mode: str
    daily_case_subject: str | None
    daily_case_rotation_subjects: tuple[str, ...]
    daily_case_rotation_start_date: str | None
    daily_case_rotation_start_index: int
    daily_question_enabled: bool
    daily_question_time: str
    daily_question_selection_mode: str
    daily_question_origin: str
    daily_question_subject: str | None
    daily_question_type: str | None
    daily_question_rotation_subjects: tuple[str, ...]
    daily_question_rotation_start_date: str | None
    daily_question_rotation_start_index: int
    case_source_court_enabled: bool
    case_source_spp_enabled: bool
    law_update_enabled: bool
    http_timeout_seconds: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> PluginConfig:
        values = raw or {}
        timezone_name = _normalize_timezone(values.get("timezone", DEFAULT_TIMEZONE))
        case_selection_mode = normalize_selection_mode(
            values.get("daily_case_selection_mode", "random")
        )
        case_rotation_subjects = _normalize_subjects(
            values.get("daily_case_rotation_subjects", [])
        )
        if case_selection_mode == "rotation" and not case_rotation_subjects:
            case_selection_mode = "random"
        case_rotation_start_date = _normalize_date(
            values.get("daily_case_rotation_start_date")
        )
        if case_selection_mode == "rotation" and case_rotation_start_date is None:
            case_rotation_start_date = _local_today(timezone_name)
        question_selection_mode = normalize_selection_mode(
            values.get("daily_question_selection_mode", "random")
        )
        question_rotation_subjects = _normalize_subjects(
            values.get("daily_question_rotation_subjects", [])
        )
        if question_selection_mode == "rotation" and not question_rotation_subjects:
            question_selection_mode = "random"
        question_rotation_start_date = _normalize_date(
            values.get("daily_question_rotation_start_date")
        )
        if (
            question_selection_mode == "rotation"
            and question_rotation_start_date is None
        ):
            question_rotation_start_date = _local_today(timezone_name)
        return cls(
            operator_ids=_normalize_operator_ids(values.get("operator_ids", [])),
            timezone=timezone_name,
            auto_scan_enabled=_to_bool(values.get("auto_scan_enabled", False)),
            scan_interval_minutes=_normalize_interval(
                values.get("scan_interval_minutes", DEFAULT_SCAN_INTERVAL_MINUTES),
            ),
            llm_provider_id=str(values.get("llm_provider_id", "") or "").strip(),
            extra_event_source_urls=_normalize_urls(
                values.get("extra_event_source_urls", [])
            ),
            auto_publish_events=_to_bool(values.get("auto_publish_events", False)),
            deadline_reminder_days=_normalize_days(
                values.get("deadline_reminder_days", DEFAULT_DEADLINE_REMINDER_DAYS)
            ),
            deadline_same_day_enabled=_to_bool(
                values.get("deadline_same_day_enabled", True)
            ),
            daily_case_enabled=_to_bool(values.get("daily_case_enabled", False)),
            daily_case_time=_normalize_time(
                values.get("daily_case_time", DEFAULT_DAILY_TIME)
            ),
            daily_case_selection_mode=case_selection_mode,
            daily_case_subject=normalize_subject(values.get("daily_case_subject")),
            daily_case_rotation_subjects=case_rotation_subjects,
            daily_case_rotation_start_date=case_rotation_start_date,
            daily_case_rotation_start_index=_normalize_index(
                values.get("daily_case_rotation_start_index", 0)
            ),
            daily_question_enabled=_to_bool(
                values.get("daily_question_enabled", False)
            ),
            daily_question_time=_normalize_time(
                values.get("daily_question_time", DEFAULT_DAILY_TIME)
            ),
            daily_question_selection_mode=question_selection_mode,
            daily_question_origin=normalize_origin(
                values.get("daily_question_origin", "random")
            ),
            daily_question_subject=normalize_subject(
                values.get("daily_question_subject")
            ),
            daily_question_type=normalize_question_type(
                values.get("daily_question_type")
            ),
            daily_question_rotation_subjects=question_rotation_subjects,
            daily_question_rotation_start_date=question_rotation_start_date,
            daily_question_rotation_start_index=_normalize_index(
                values.get("daily_question_rotation_start_index", 0)
            ),
            case_source_court_enabled=_to_bool(
                values.get("case_source_court_enabled", True)
            ),
            case_source_spp_enabled=_to_bool(
                values.get("case_source_spp_enabled", True)
            ),
            law_update_enabled=_to_bool(values.get("law_update_enabled", False)),
            http_timeout_seconds=_normalize_timeout(
                values.get("http_timeout_seconds", DEFAULT_HTTP_TIMEOUT_SECONDS)
            ),
        )


def _normalize_operator_ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
        return ()

    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item is None:
            continue
        normalized = str(item).strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


def _normalize_urls(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        url = str(item or "").strip()
        if url and url.startswith(("http://", "https://")) and url not in seen:
            result.append(url)
            seen.add(url)
    return tuple(result)


def _normalize_days(value: Any) -> tuple[int, ...]:
    if isinstance(value, int):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return DEFAULT_DEADLINE_REMINDER_DAYS
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            day = int(item)
        except (TypeError, ValueError):
            continue
        if day > 0 and day not in seen:
            result.append(day)
            seen.add(day)
    return tuple(result)


def _normalize_timezone(value: Any) -> str:
    candidate = str(value or DEFAULT_TIMEZONE).strip()
    try:
        ZoneInfo(candidate)
    except (KeyError, ValueError):
        return DEFAULT_TIMEZONE
    return candidate


def _normalize_interval(value: Any) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SCAN_INTERVAL_MINUTES
    return max(MIN_SCAN_INTERVAL_MINUTES, min(MAX_SCAN_INTERVAL_MINUTES, interval))


def _normalize_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return DEFAULT_HTTP_TIMEOUT_SECONDS
    return max(5, min(120, timeout))


def _normalize_time(value: Any) -> str:
    candidate = str(value or DEFAULT_DAILY_TIME).strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", candidate):
        return DEFAULT_DAILY_TIME
    return candidate


def _normalize_subjects(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
        return ()
    result: list[str] = []
    for item in value:
        subject = normalize_subject(item)
        if subject and subject not in result:
            result.append(subject)
    return tuple(result)


def _normalize_date(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        from datetime import date

        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def _local_today(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).date().isoformat()


def _normalize_index(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _to_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
