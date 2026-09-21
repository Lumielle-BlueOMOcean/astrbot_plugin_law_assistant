from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

if __package__ and "." in __package__:
    from .date_parser import date_is_on_or_after
    from .models import LegalEvent
else:
    from date_parser import date_is_on_or_after
    from models import LegalEvent


DEFAULT_HISTORICAL_KEYWORDS = (
    "已结束",
    "获奖名单",
    "赛事回顾",
    "圆满举办",
    "往届",
    "决赛结果",
    "颁奖典礼",
)


@dataclass(frozen=True, slots=True)
class RadarSourcePolicy:
    source_key: str
    display_name: str
    discover_enabled: bool = True
    auto_publish_enabled: bool = False


def derive_radar_status(
    event: LegalEvent,
    now: datetime,
    timezone_name: str = "Asia/Shanghai",
    historical_keywords: tuple[str, ...] | list[str] = (),
) -> str:
    """Derive current participation value without mutating the stored event."""
    zone = ZoneInfo(timezone_name)
    local_now = _as_local(now, zone)
    confirmed = [
        item for item in event.dates if item.confirmed and item.datetime is not None
    ]
    deadlines = [
        item
        for item in confirmed
        if item.datetime is not None and item.kind.endswith("deadline")
    ]
    future_deadlines = [
        value
        for value in deadlines
        if date_is_on_or_after(
            value.datetime,
            precision=value.precision,
            now=local_now,
            timezone_name=zone.key,
        )
    ]
    if future_deadlines:
        return "current"
    if deadlines and not future_deadlines:
        return "historical"

    metadata = event.metadata or {}
    signals = tuple(
        str(value).strip()
        for value in metadata.get("historical_signals", ())
        if str(value).strip()
    )
    configured_historical = tuple(
        str(value).strip() for value in historical_keywords if str(value).strip()
    )
    combined = " ".join((event.title, event.summary, *signals))
    if any(
        word in combined
        for word in (*DEFAULT_HISTORICAL_KEYWORDS, *configured_historical)
    ):
        return "historical"

    future_activity = any(
        date_is_on_or_after(
            item.datetime,
            precision=item.precision,
            now=local_now,
            timezone_name=zone.key,
        )
        for item in confirmed
        if item.datetime is not None and not item.kind.endswith("deadline")
    )
    participation_evidence = bool(metadata.get("participation_evidence"))
    published_at = event.source_published_at
    if published_at is not None:
        published_local = _as_local(published_at, zone)
        if (
            published_local.date() < local_now.date() - timedelta(days=365)
            and not future_activity
        ):
            return "historical"
        if (
            published_local.date() >= local_now.date() - timedelta(days=180)
            and participation_evidence
        ):
            return "current"
    if future_activity and participation_evidence:
        return "current"
    return "needs_review"


def canonical_event_key(event: LegalEvent) -> str | None:
    """Return a conservative cross-source identity only with strong evidence."""
    deadline = next(
        (
            item.datetime
            for item in event.dates
            if item.confirmed
            and item.datetime is not None
            and item.kind.endswith("deadline")
        ),
        None,
    )
    title = _normalize(event.title)
    organizer = _normalize(event.organizer)
    if not title or not organizer or deadline is None or not event.event_type:
        return None
    raw = "|".join(
        (title, organizer, event.event_type.strip().lower(), deadline.isoformat())
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def radar_policy_for(source_key: str, config: Any | None = None) -> RadarSourcePolicy:
    """Resolve source discovery/automatic-publication policy conservatively."""
    key = str(source_key or "").strip()
    policies = getattr(config, "radar_source_policies", ()) if config else ()
    for raw in policies or ():
        if (
            isinstance(raw, dict)
            and str(raw.get("key") or raw.get("source_key")) == key
        ):
            return RadarSourcePolicy(
                source_key=key,
                display_name=str(raw.get("display_name") or key),
                discover_enabled=bool(raw.get("discover_enabled", True)),
                auto_publish_enabled=bool(raw.get("auto_publish_enabled", False)),
            )
        if isinstance(raw, str) and raw.strip() == key:
            return RadarSourcePolicy(key, key, True, False)
    allowed = {
        str(value).strip()
        for value in getattr(config, "radar_auto_publish_sources", ()) or ()
        if str(value).strip()
    }
    return RadarSourcePolicy(
        source_key=key,
        display_name=key,
        discover_enabled=True,
        auto_publish_enabled=key == "china_jm" or key in allowed,
    )


def _normalize(value: Any) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "").casefold())


def _as_local(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


__all__ = [
    "DEFAULT_HISTORICAL_KEYWORDS",
    "RadarSourcePolicy",
    "canonical_event_key",
    "derive_radar_status",
    "radar_policy_for",
]
