from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

if __package__ and "." in __package__:
    from .models import LegalEvent
    from .sources.base import Extractor, SourceAdapter, Validator
    from .storage import SQLiteStorage
else:
    from models import LegalEvent
    from sources.base import Extractor, SourceAdapter, Validator
    from storage import SQLiteStorage


@dataclass(frozen=True, slots=True)
class ScanFailure:
    source_key: str
    error: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    trigger: str
    source_count: int
    discovered_count: int
    upserted_count: int
    failures: tuple[ScanFailure, ...]
    duration_seconds: float
    skipped: bool = False


class LawAssistantService:
    def __init__(
        self,
        storage: SQLiteStorage,
        sources: list[tuple[SourceAdapter, Extractor]] | None = None,
        validators: list[Validator] | None = None,
        logger: Any | None = None,
    ) -> None:
        self.storage = storage
        self.sources = list(sources or [])
        self.validators = list(validators or [])
        self.logger = logger or logging.getLogger(__name__)
        self._scan_lock = asyncio.Lock()

    async def status(self) -> dict[str, Any]:
        return {
            "service": "law_assistant",
            "schema_version": self.storage.schema_version,
            "source_count": len(self.sources),
            "event_count": self.storage.count_events(),
            "scan_in_progress": self._scan_lock.locked(),
            "last_source_run": self.storage.latest_source_run(),
        }

    async def scan_events(self, trigger: str = "manual") -> ScanResult:
        started = datetime.now(timezone.utc)
        if self._scan_lock.locked():
            return ScanResult(
                trigger=trigger,
                source_count=len(self.sources),
                discovered_count=0,
                upserted_count=0,
                failures=(),
                duration_seconds=0.0,
                skipped=True,
            )

        async with self._scan_lock:
            discovered_count = 0
            upserted_count = 0
            failures: list[ScanFailure] = []
            for adapter, extractor in self.sources:
                source_key = str(getattr(adapter, "key", adapter.__class__.__name__))
                source_started = datetime.now(timezone.utc)
                try:
                    documents = await _maybe_await(adapter.fetch())
                    source_discovered = 0
                    for document in documents:
                        candidates = await _maybe_await(extractor.extract(document))
                        for event in candidates:
                            if not isinstance(event, LegalEvent):
                                raise TypeError(
                                    "extractor returned a non-LegalEvent value"
                                )
                            accepted_event: LegalEvent | None = event
                            for validator in self.validators:
                                accepted_event = await _maybe_await(
                                    validator.validate(accepted_event, document),
                                )
                                if accepted_event is None:
                                    break
                            if accepted_event is None:
                                continue
                            self.storage.upsert_event(accepted_event)
                            upserted_count += 1
                            source_discovered += 1
                    discovered_count += source_discovered
                    self.storage.record_source_run(
                        source_key=source_key,
                        started_at=source_started,
                        finished_at=datetime.now(timezone.utc),
                        success=True,
                        discovered_count=source_discovered,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    message = str(exc)
                    failures.append(ScanFailure(source_key=source_key, error=message))
                    self.storage.record_source_run(
                        source_key=source_key,
                        started_at=source_started,
                        finished_at=datetime.now(timezone.utc),
                        success=False,
                        discovered_count=0,
                        error_summary=message[:500],
                    )
                    self.logger.exception("Law Assistant source %s failed", source_key)

            duration = (datetime.now(timezone.utc) - started).total_seconds()
            return ScanResult(
                trigger=trigger,
                source_count=len(self.sources),
                discovered_count=discovered_count,
                upserted_count=upserted_count,
                failures=tuple(failures),
                duration_seconds=duration,
            )

    async def list_events(self, limit: int = 20) -> list[LegalEvent]:
        return self.storage.list_events(limit=max(1, min(limit, 100)))

    async def get_event(self, event_id: int) -> LegalEvent | None:
        return self.storage.get_event(event_id)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value
