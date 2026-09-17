from __future__ import annotations

import logging
from typing import Protocol


class Publisher(Protocol):
    async def publish_text(self, destination: str, text: str) -> bool:
        """Publish ordinary text through a host-provided transport."""


class NoopPublisher:
    """Publisher used when a host transport is not available."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def publish_text(self, destination: str, text: str) -> bool:
        self.logger.info("Publisher is not enabled: %s", destination)
        return False


class AstrBotPublisher:
    """Publish ordinary text with AstrBot's unified public Context API."""

    def __init__(self, context, *, logger: logging.Logger | None = None) -> None:
        self.context = context
        self.logger = logger or logging.getLogger(__name__)

    async def publish_text(self, destination: str, text: str) -> bool:
        try:
            from astrbot.api.event import MessageChain

            chain = MessageChain().message(text)
            result = await self.context.send_message(destination, chain)
            return bool(result)
        except Exception as exc:  # noqa: BLE001 - transport errors are fail-soft
            self.logger.warning("Law Assistant publication failed: %s", exc)
            return False


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
        when = date.datetime.isoformat() if date.datetime else date.label
        lines.append(f"{date.label}：{when}")
    lines.append(f"来源：{event.source_url}")
    return "\n".join(lines)


def format_deadline_reminder(event, date, remaining_days: int) -> str:
    when = date.datetime.isoformat() if date.datetime else date.label
    return f"【DDL提醒】{event.title}\n{date.label}：{when}\n剩余约 {remaining_days} 天\n{event.source_url}"


__all__ = [
    "AstrBotPublisher",
    "NoopPublisher",
    "Publisher",
    "format_deadline_reminder",
    "format_event",
]
