from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime as DateTime
from typing import Any


@dataclass(frozen=True, slots=True)
class EventDate:
    kind: str
    datetime: DateTime | None
    timezone: str
    label: str
    evidence_text: str


@dataclass(slots=True)
class LegalEvent:
    source_key: str
    source_item_key: str
    title: str
    source_url: str
    organizer: str
    event_type: str
    eligibility: str
    status: str
    raw_content_hash: str
    discovered_at: DateTime
    updated_at: DateTime
    metadata: dict[str, Any] = field(default_factory=dict)
    dates: tuple[EventDate, ...] = ()
    id: int | None = None


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_key: str
    source_item_key: str
    url: str
    title: str
    content: str
    fetched_at: DateTime | str
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content_hash:
            object.__setattr__(
                self,
                "content_hash",
                hashlib.sha256(self.content.encode("utf-8")).hexdigest(),
            )
