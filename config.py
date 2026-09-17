from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_SCAN_INTERVAL_MINUTES = 60
MIN_SCAN_INTERVAL_MINUTES = 5
MAX_SCAN_INTERVAL_MINUTES = 24 * 60


@dataclass(frozen=True, slots=True)
class PluginConfig:
    operator_ids: tuple[str, ...]
    timezone: str
    auto_scan_enabled: bool
    scan_interval_minutes: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> PluginConfig:
        values = raw or {}
        return cls(
            operator_ids=_normalize_operator_ids(values.get("operator_ids", [])),
            timezone=_normalize_timezone(values.get("timezone", DEFAULT_TIMEZONE)),
            auto_scan_enabled=bool(values.get("auto_scan_enabled", False)),
            scan_interval_minutes=_normalize_interval(
                values.get("scan_interval_minutes", DEFAULT_SCAN_INTERVAL_MINUTES),
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
