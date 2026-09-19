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
    confirmed: bool = True
    id: int | None = None


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
    registration_method: str = ""
    summary: str = ""
    source_published_at: DateTime | None = None
    last_seen_at: DateTime | None = None
    revision: int = 1


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
    content_type: str = "text/html"
    attachments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.content_hash:
            object.__setattr__(
                self,
                "content_hash",
                hashlib.sha256(self.content.encode("utf-8")).hexdigest(),
            )


@dataclass(frozen=True, slots=True)
class CaseItem:
    source_key: str
    source_item_key: str
    title: str
    source_url: str
    authority: str
    raw_text: str
    content_hash: str
    published_at: DateTime | None = None
    discovered_at: DateTime | str | None = None
    last_seen_at: DateTime | str | None = None
    subjects: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None


@dataclass(frozen=True, slots=True)
class LawUpdate:
    source_key: str
    source_item_key: str
    title: str
    category: str
    source_url: str
    status: str
    content_hash: str
    promulgation_date: DateTime | None = None
    effective_date: DateTime | None = None
    raw_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
