from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from models import EventDate, LegalEvent


def make_event(
    *,
    source_key: str = "fake",
    source_item_key: str = "item-1",
    title: str = "法律硕士模拟竞赛",
    url: str = "https://example.test/events/item-1",
) -> LegalEvent:
    now = datetime.now(timezone.utc)
    return LegalEvent(
        source_key=source_key,
        source_item_key=source_item_key,
        title=title,
        source_url=url,
        organizer="Fake Law School",
        event_type="competition",
        eligibility="law students",
        status="open",
        raw_content_hash="hash-1",
        discovered_at=now,
        updated_at=now,
        metadata={"kind": "fake"},
        dates=(
            EventDate(
                kind="registration_deadline",
                datetime=now + timedelta(days=7),
                timezone="Asia/Shanghai",
                label="报名截止",
                evidence_text="报名截止：2026-10-01",
            ),
        ),
    )


class FakeAdapter:
    def __init__(
        self,
        key: str,
        documents: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.key = key
        self.documents = documents or []
        self.error = error
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.wait_for_release = False

    async def fetch(self) -> list[Any]:
        self.started.set()
        if self.wait_for_release:
            await self.release.wait()
        if self.error:
            raise self.error
        return self.documents


class FakeExtractor:
    def __init__(
        self, events_by_item: dict[str, list[LegalEvent]] | None = None
    ) -> None:
        self.events_by_item = events_by_item or {}

    async def extract(self, document: Any) -> list[LegalEvent]:
        return self.events_by_item.get(document.source_item_key, [])


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def publish_text(self, destination: str, text: str) -> bool:
        self.calls.append((destination, text))
        return True


@dataclass
class FakeEvent:
    private: bool = True
    admin: bool = False
    sender_id: str = ""
    message: str = "/law status"
    platform_name: str = "aiocqhttp"
    unified_msg_origin: str = "aiocqhttp:private:1"

    def is_private_chat(self) -> bool:
        return self.private

    def is_admin(self) -> bool:
        return self.admin

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_message_str(self) -> str:
        return self.message

    def get_platform_name(self) -> str:
        return self.platform_name

    def plain_result(self, text: str) -> str:
        return text
