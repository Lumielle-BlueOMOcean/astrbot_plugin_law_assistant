from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    status: str
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"sent", "failed", "unknown"}:
            raise ValueError(f"unsupported publish status: {self.status}")


class Publisher(Protocol):
    async def publish_text(self, destination: str, text: str) -> bool | PublishOutcome:
        """Publish ordinary text through a host-provided transport."""


class NoopPublisher:
    """Publisher used when a host transport is not available."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def publish_text(self, destination: str, text: str) -> bool | PublishOutcome:
        self.logger.info("Publisher is not enabled: %s", destination)
        return False


class AstrBotPublisher:
    """Publish ordinary text with AstrBot's unified public Context API."""

    def __init__(self, context, *, logger: logging.Logger | None = None) -> None:
        self.context = context
        self.logger = logger or logging.getLogger(__name__)

    async def publish_text(self, destination: str, text: str) -> bool | PublishOutcome:
        try:
            from astrbot.api.event import MessageChain

            chain = MessageChain().message(text)
            result = await self.context.send_message(destination, chain)
            return PublishOutcome("sent") if result else PublishOutcome("failed")
        except Exception as exc:  # noqa: BLE001 - transport errors are fail-soft
            self.logger.warning("Law Assistant publication failed: %s", exc)
            return PublishOutcome("unknown", str(exc))


def format_event(event) -> str:
    lines = [f"【法律活动】{event.title}"]
    if event.organizer:
        lines.append(f"主办方：{event.organizer}")
    if event.eligibility:
        lines.append(f"对象：{event.eligibility}")
    if event.summary:
        lines.append(event.summary)
    for date in event.dates:
        if not date.confirmed:
            continue
        when = _format_event_date(date)
        lines.append(f"{date.label}：{when}")
    lines.append(f"来源：{event.source_url}")
    return "\n".join(lines)


def format_deadline_reminder(event, date, remaining_days: int) -> str:
    when = _format_event_date(date)
    return f"【DDL提醒】{event.title}\n{date.label}：{when}\n剩余约 {remaining_days} 天\n{event.source_url}"


def _format_event_date(date) -> str:
    if date.datetime is None:
        return date.label
    if getattr(date, "precision", "minute") == "date":
        return date.datetime.astimezone(ZoneInfo(date.timezone)).date().isoformat()
    return date.datetime.isoformat()


__all__ = [
    "AstrBotPublisher",
    "NoopPublisher",
    "PublishOutcome",
    "Publisher",
    "format_deadline_reminder",
    "format_event",
]
