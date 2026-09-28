from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def local_due_datetime(day: date, wall_time: str, timezone_name: str) -> datetime:
    """Resolve HH:MM in an IANA zone; advance DST gaps and choose fold zero."""
    try:
        parsed_time = time.fromisoformat(str(wall_time))
    except ValueError as exc:
        raise ValueError("daily plan time must be HH:MM") from exc
    if parsed_time.second or parsed_time.microsecond:
        raise ValueError("daily plan time must be HH:MM")
    zone = ZoneInfo(timezone_name)
    candidate = datetime.combine(day, parsed_time)
    for _ in range(181):
        local = candidate.replace(tzinfo=zone, fold=0)
        round_trip = local.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == candidate:
            return local
        candidate += timedelta(minutes=1)
    raise ValueError("could not resolve daily plan time in configured timezone")


def next_daily_due(now: datetime | str, wall_time: str, timezone_name: str) -> datetime:
    """Return the nearest future daily HH:MM occurrence in local time."""
    current = datetime.fromisoformat(now) if isinstance(now, str) else now
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    zone = ZoneInfo(timezone_name)
    local_now = current.astimezone(zone)
    candidate = local_due_datetime(local_now.date(), wall_time, timezone_name)
    if candidate <= local_now:
        candidate = local_due_datetime(
            local_now.date() + timedelta(days=1), wall_time, timezone_name
        )
    return candidate


__all__ = ["local_due_datetime", "next_daily_due"]
