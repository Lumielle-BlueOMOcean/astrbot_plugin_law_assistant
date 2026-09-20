from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class LibrarySource:
    source_kind: str
    title: str
    raw_text: str
    source_url: str
    content_hash: str
    created_at: datetime
    created_by: str
    session_origin: str
    original_filename: str | None = None
    mime_type: str | None = None
    storage_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None


@dataclass(frozen=True, slots=True)
class LearningItem:
    item_type: str
    identity: str
    item_hash: str
    title: str
    subjects: tuple[str, ...]
    verification_status: str
    source_summary: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None


@dataclass(frozen=True, slots=True)
class CaseDetail:
    case_number: str
    authority: str
    case_summary: str
    issues: tuple[str, ...]
    reasoning: str
    result_text: str
    practice_notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuestionDetail:
    question_identity: str
    question_type: str
    stem: str
    options: tuple[str, ...]
    answer: Any
    explanation: str
    exam_name: str
    exam_year: str
    paper: str
    question_number: str
    answer_source: str


@dataclass(frozen=True, slots=True)
class LibraryArchiveResult:
    source_id: int
    item_id: int
    duplicate: bool


@dataclass(frozen=True, slots=True)
class LibraryItemBundle:
    item: LearningItem
    sources: tuple[LibrarySource, ...]
    case: CaseDetail | None = None
    question: QuestionDetail | None = None
