from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

if __package__ and "." in __package__:
    from ..models import LegalEvent, SourceDocument
else:
    from models import LegalEvent, SourceDocument


class SourceAdapter(Protocol):
    key: str

    async def fetch(self) -> list[SourceDocument]:
        """Fetch source documents without interpreting their contents."""


class Extractor(Protocol):
    async def extract(self, document: SourceDocument) -> Iterable[LegalEvent]:
        """Convert one source document into candidate events."""


class Validator(Protocol):
    async def validate(
        self,
        event: LegalEvent,
        document: SourceDocument,
    ) -> LegalEvent | None:
        """Validate evidence and return the accepted event, or None to reject it."""
