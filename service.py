from __future__ import annotations

import asyncio
import inspect
import logging
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

if __package__ and "." in __package__:
    from .learning_service import LearningService
    from .models import CaseItem, LawUpdate, LegalEvent, SourceDocument
    from .publisher import format_deadline_reminder, format_event
    from .sources.base import Extractor, SourceAdapter, Validator
    from .storage import SQLiteStorage
else:
    from learning_service import LearningService
    from models import CaseItem, LawUpdate, LegalEvent, SourceDocument
    from publisher import format_deadline_reminder, format_event
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
    """The single business facade used by commands, tools and scheduler."""

    def __init__(
        self,
        storage: SQLiteStorage,
        sources: list[tuple[SourceAdapter, Extractor]] | None = None,
        validators: list[Validator] | None = None,
        logger: Any | None = None,
        *,
        case_sources: list[tuple[Any, Any]] | None = None,
        law_sources: list[tuple[Any, Any]] | None = None,
        publisher: Any | None = None,
        learning_service: LearningService | None = None,
        config: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self.storage = storage
        self.sources = list(sources or [])
        self.validators = list(validators or [])
        self.case_sources = list(case_sources or [])
        self.law_sources = list(law_sources or [])
        self.logger = logger or logging.getLogger(__name__)
        self.publisher = publisher
        self.config = config
        self.learning_service = learning_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._scan_lock = asyncio.Lock()
        self._publish_confirmations: dict[str, tuple[int, datetime]] = {}

    async def status(self) -> dict[str, Any]:
        return {
            "service": "law_assistant",
            "schema_version": self.storage.schema_version,
            "source_count": len(self.sources),
            "case_source_count": len(self.case_sources),
            "law_source_count": len(self.law_sources),
            "event_count": self.storage.count_events(),
            "case_count": len(self.storage.list_case_items(limit=100000)),
            "target_count": len(self.storage.list_targets(enabled_only=True)),
            "scan_in_progress": self._scan_lock.locked(),
            "last_source_run": self.storage.latest_source_run(),
            "sources": await self.list_sources(),
        }

    async def scan_events(
        self,
        trigger: str = "manual",
        *,
        session_origin: str | None = None,
    ) -> ScanResult:
        started = self._now_utc()
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
            new_event_ids: list[int] = []
            for adapter, extractor in self.sources:
                source_key = str(getattr(adapter, "key", adapter.__class__.__name__))
                source_started = self._now_utc()
                source_discovered = 0
                try:
                    documents = await _maybe_await(adapter.fetch())
                    for document in documents:
                        prior = self.storage.get_source_document(
                            document.source_key, document.source_item_key
                        )
                        existing = self.storage.get_event_by_key(
                            document.source_key, document.source_item_key
                        )
                        self.storage.upsert_source_document(document)
                        if (
                            prior is not None
                            and prior.content_hash == document.content_hash
                            and existing is not None
                        ):
                            self.storage.touch_event_seen(
                                document.source_key,
                                document.source_item_key,
                                self._now_utc(),
                            )
                            continue
                        candidates = await _extract(
                            extractor, document, session_origin=session_origin
                        )
                        for event in candidates:
                            if not isinstance(event, LegalEvent):
                                raise TypeError(
                                    "extractor returned a non-LegalEvent value"
                                )
                            accepted_event: LegalEvent | None = event
                            for validator in self.validators:
                                accepted_event = await _maybe_await(
                                    validator.validate(accepted_event, document)
                                )
                                if accepted_event is None:
                                    break
                            if accepted_event is None:
                                continue
                            seen_at = self._now_utc()
                            if accepted_event.last_seen_at is None:
                                accepted_event = replace(
                                    accepted_event, last_seen_at=seen_at
                                )
                            result = self.storage.upsert_event_detailed(accepted_event)
                            if result.is_new:
                                new_event_ids.append(result.event_id)
                                self.logger.info(
                                    "Law Assistant discovered new event %s",
                                    result.event_id,
                                )
                            elif result.changed_fields:
                                self.logger.info(
                                    "Law Assistant updated event %s: %s",
                                    result.event_id,
                                    ", ".join(result.changed_fields),
                                )
                            upserted_count += 1
                            source_discovered += 1
                    discovered_count += source_discovered
                    self.storage.record_source_run(
                        source_key=source_key,
                        started_at=source_started,
                        finished_at=self._now_utc(),
                        success=True,
                        discovered_count=source_discovered,
                        source_type="event",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    message = str(exc)
                    failures.append(ScanFailure(source_key=source_key, error=message))
                    self.storage.record_source_run(
                        source_key=source_key,
                        started_at=source_started,
                        finished_at=self._now_utc(),
                        success=False,
                        discovered_count=source_discovered,
                        error_summary=message[:500],
                        source_type="event",
                    )
                    self.logger.exception("Law Assistant source %s failed", source_key)

            if self._auto_publish_enabled():
                for event_id in new_event_ids:
                    await self._publish_event_to_targets(event_id, kind="automatic")

            duration = (self._now_utc() - started).total_seconds()
            return ScanResult(
                trigger=trigger,
                source_count=len(self.sources),
                discovered_count=discovered_count,
                upserted_count=upserted_count,
                failures=tuple(failures),
                duration_seconds=duration,
            )

    async def scan_cases(self, trigger: str = "manual") -> dict[str, Any]:
        return await self._scan_secondary(
            self.case_sources,
            trigger=trigger,
            source_type="case",
            handler=self._store_case,
        )

    async def scan_law_updates(self, trigger: str = "manual") -> dict[str, Any]:
        return await self._scan_secondary(
            self.law_sources,
            trigger=trigger,
            source_type="law_update",
            handler=self._store_law_update,
        )

    async def _scan_secondary(self, sources, *, trigger, source_type, handler):
        if self._scan_lock.locked():
            return {"trigger": trigger, "source_count": len(sources), "skipped": True}
        async with self._scan_lock:
            total = 0
            failures: list[ScanFailure] = []
            for adapter, extractor in sources:
                key = str(getattr(adapter, "key", adapter.__class__.__name__))
                started = self._now_utc()
                count = 0
                try:
                    for document in await _maybe_await(adapter.fetch()):
                        previous = self.storage.get_source_document(
                            document.source_key, document.source_item_key
                        )
                        self.storage.upsert_source_document(document)
                        if previous and previous.content_hash == document.content_hash:
                            continue
                        item = await _maybe_await(extractor.extract(document))
                        if item is not None:
                            handler(item)
                            count += 1
                    total += count
                    self.storage.record_source_run(
                        source_key=key,
                        started_at=started,
                        finished_at=self._now_utc(),
                        success=True,
                        discovered_count=count,
                        source_type=source_type,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    message = str(exc)
                    failures.append(ScanFailure(key, message))
                    self.storage.record_source_run(
                        source_key=key,
                        started_at=started,
                        finished_at=self._now_utc(),
                        success=False,
                        discovered_count=count,
                        error_summary=message[:500],
                        source_type=source_type,
                    )
                    self.logger.exception(
                        "Law Assistant %s source %s failed", source_type, key
                    )
            return {
                "trigger": trigger,
                "source_count": len(sources),
                "discovered_count": total,
                "failures": [
                    {"source_key": failure.source_key, "error": failure.error}
                    for failure in failures
                ],
                "skipped": False,
            }

    def _store_case(self, item: CaseItem) -> None:
        self.storage.upsert_case_item(item)

    def _store_law_update(self, item: LawUpdate) -> None:
        self.storage.upsert_law_update(item)

    async def list_events(self, limit: int = 20) -> list[LegalEvent]:
        return self.storage.list_events(limit=max(1, min(limit, 100)))

    async def get_event(self, event_id: int) -> LegalEvent | None:
        return self.storage.get_event(event_id)

    async def list_deadlines(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.storage.list_deadlines(now=self._now_utc(), limit=limit)

    async def list_sources(self) -> list[dict[str, Any]]:
        result = []
        for adapter, _ in self.sources:
            key = str(getattr(adapter, "key", adapter.__class__.__name__))
            result.append(
                {
                    "key": key,
                    "type": "event",
                    "last_run": self.storage.latest_source_run(key),
                    "health": self.storage.source_health(key),
                }
            )
        for adapter, _ in self.case_sources:
            key = str(getattr(adapter, "key", adapter.__class__.__name__))
            result.append(
                {
                    "key": key,
                    "type": "case",
                    "last_run": self.storage.latest_source_run(key),
                    "health": self.storage.source_health(key),
                }
            )
        for adapter, _ in self.law_sources:
            key = str(getattr(adapter, "key", adapter.__class__.__name__))
            result.append(
                {
                    "key": key,
                    "type": "law_update",
                    "last_run": self.storage.latest_source_run(key),
                    "health": self.storage.source_health(key),
                }
            )
        return result

    async def bind_target(
        self, unified_msg_origin: str, label: str = ""
    ) -> dict[str, Any]:
        return self.storage.bind_target(unified_msg_origin, label)

    async def unbind_target(self, unified_msg_origin: str) -> bool:
        return self.storage.unbind_target(unified_msg_origin)

    async def list_targets(self) -> list[dict[str, Any]]:
        return self.storage.list_targets()

    async def prepare_publish_event(self, event_id: int) -> dict[str, Any]:
        event = self.storage.get_event(event_id)
        if event is None:
            return {"ready": False, "reason": "event not found"}
        if not event.source_url or any(
            date.datetime is None or not date.confirmed for date in event.dates
        ):
            return {"ready": False, "reason": "事件缺少可验证的来源或时间证据"}
        targets = self.storage.list_targets(enabled_only=True)
        if not targets:
            return {"ready": False, "reason": "尚未绑定发布目标"}
        token = secrets.token_urlsafe(12)
        self._publish_confirmations[token] = (
            event_id,
            self._now_utc() + timedelta(minutes=10),
        )
        return {
            "ready": True,
            "token": token,
            "event_id": event_id,
            "revision": event.revision,
            "targets": targets,
            "preview": format_event(event),
        }

    async def confirm_publish(self, token: str) -> dict[str, Any]:
        claimed = self._publish_confirmations.pop(token.strip(), None)
        if claimed is None:
            return {"success": False, "reason": "确认 token 无效或已使用"}
        event_id, expires_at = claimed
        if self._now_utc() > expires_at:
            return {"success": False, "reason": "确认 token 已过期"}
        count = await self._publish_event_to_targets(event_id, kind="manual")
        return {"success": count > 0, "published_count": count}

    async def _publish_event_to_targets(self, event_id: int, *, kind: str) -> int:
        event = self.storage.get_event(event_id)
        if event is None or self.publisher is None:
            return 0
        if kind == "automatic" and (
            event.status.upper() in {"CLOSED", "UNKNOWN"}
            or not event.dates
            or any(not date.confirmed for date in event.dates)
        ):
            return 0
        count = 0
        for target in self.storage.list_targets(enabled_only=True):
            publication_id = self.storage.claim_publication(
                event.id, event.revision, target["id"], kind
            )
            if publication_id is None:
                continue
            success = await self.publisher.publish_text(
                target["unified_msg_origin"], format_event(event)
            )
            self.storage.finish_publication(publication_id, success=success)
            if success:
                count += 1
                self.logger.info(
                    "Law Assistant published event %s to %s",
                    event_id,
                    target["unified_msg_origin"],
                )
            else:
                self.logger.warning(
                    "Law Assistant failed to publish event %s to %s",
                    event_id,
                    target["unified_msg_origin"],
                )
        return count

    async def check_deadline_reminders(self, *, now: datetime | None = None) -> int:
        if self.publisher is None:
            return 0
        current = _as_utc(now or self.clock())
        offsets = set(getattr(self.config, "deadline_reminder_days", (7, 3, 1)))
        if getattr(self.config, "deadline_same_day_enabled", True):
            offsets.add(0)
        sent = 0
        for item in self.storage.list_deadlines(now=None, limit=1000):
            event, date = item["event"], item["date"]
            if date.id is None or date.datetime is None:
                continue
            remaining = (
                date.datetime.astimezone(current.tzinfo).date() - current.date()
            ).days
            if remaining not in offsets:
                continue
            for target in self.storage.list_targets(enabled_only=True):
                reminder_id = self.storage.claim_reminder(
                    event_id=event.id,
                    event_date_id=date.id,
                    deadline_value=date.datetime.isoformat(),
                    target_id=target["id"],
                    reminder_offset=remaining,
                )
                if reminder_id is None:
                    continue
                success = await self.publisher.publish_text(
                    target["unified_msg_origin"],
                    format_deadline_reminder(event, date, remaining),
                )
                self.storage.finish_reminder(reminder_id, success=success)
                sent += int(success)
                if success:
                    self.logger.info(
                        "Law Assistant sent deadline reminder for event %s", event.id
                    )
        return sent

    async def get_daily_case(
        self, *, session_origin: str | None = None
    ) -> dict[str, Any]:
        if self.learning_service is None:
            return {"available": False, "reason": "daily case service unavailable"}
        return await self.learning_service.daily_case(session_origin=session_origin)

    async def generate_question(
        self,
        subject: str = "",
        *,
        question_type: str = "single",
        session_origin: str | None = None,
    ) -> dict[str, Any]:
        if self.learning_service is None:
            return {"available": False, "reason": "question service unavailable"}
        return await self.learning_service.generate_question(
            subject=subject,
            question_type=question_type,
            session_origin=session_origin,
        )

    async def list_law_updates(self, limit: int = 50) -> list[LawUpdate]:
        return self.storage.list_law_updates(limit=limit)

    async def run_scheduled_jobs(
        self, *, now: datetime | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"events": await self.scan_events(trigger="scheduler")}
        if self.case_sources:
            result["cases"] = await self.scan_cases(trigger="scheduler")
        if self.law_sources:
            result["law_updates"] = await self.scan_law_updates(trigger="scheduler")
        current = now or self.clock()
        result["reminders_sent"] = await self.check_deadline_reminders(now=current)
        result["daily_case_sent"] = await self._run_daily_content(
            content_type="daily_case",
            enabled=bool(getattr(self.config, "daily_case_enabled", False)),
            configured_time=str(getattr(self.config, "daily_case_time", "08:00")),
            now=current,
        )
        result["daily_question_sent"] = await self._run_daily_content(
            content_type="daily_question",
            enabled=bool(getattr(self.config, "daily_question_enabled", False)),
            configured_time=str(getattr(self.config, "daily_question_time", "08:00")),
            now=current,
        )
        return result

    async def _run_daily_content(
        self,
        *,
        content_type: str,
        enabled: bool,
        configured_time: str,
        now: datetime | str,
    ) -> int:
        if (
            not enabled
            or self.publisher is None
            or not _time_is_due(now, configured_time, self.config)
        ):
            return 0
        local_now = _local_datetime(
            now, getattr(self.config, "timezone", "Asia/Shanghai")
        )
        if content_type == "daily_case":
            content = await self.get_daily_case()
        else:
            content = await self.generate_question()
        if not content.get("available"):
            return 0
        raw_body = content.get("content", content)
        body = raw_body if isinstance(raw_body, dict) else {"body": raw_body}
        sent = 0
        for target in self.storage.list_targets(enabled_only=True):
            claim_id = self.storage.claim_daily_content(
                content_date=local_now.date().isoformat(),
                target_id=target["id"],
                content_type=content_type,
                body=body,
                source_item_id=content.get("case_id"),
            )
            if claim_id is None:
                continue
            success = await self.publisher.publish_text(
                target["unified_msg_origin"],
                _format_daily_content(content_type, content),
            )
            self.storage.finish_daily_content(claim_id, success=success)
            sent += int(success)
            if success:
                self.logger.info(
                    "Law Assistant sent %s to %s",
                    content_type,
                    target["unified_msg_origin"],
                )
        return sent

    def _auto_publish_enabled(self) -> bool:
        return bool(getattr(self.config, "auto_publish_events", False))

    def _now_utc(self) -> datetime:
        return _as_utc(self.clock())


async def _extract(
    extractor: Any, document: SourceDocument, *, session_origin: str | None
):
    if session_origin is not None:
        try:
            return await _maybe_await(
                extractor.extract(document, session_origin=session_origin)
            )
        except TypeError as exc:
            if "session_origin" not in str(exc):
                raise
    return await _maybe_await(extractor.extract(document))


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local_datetime(value: datetime | str, timezone_name: str) -> datetime:
    from zoneinfo import ZoneInfo

    return _as_utc(value).astimezone(ZoneInfo(timezone_name))


def _time_is_due(value: datetime | str, configured_time: str, config: Any) -> bool:
    try:
        hour, minute = (int(part) for part in configured_time.split(":", 1))
        local = _local_datetime(value, getattr(config, "timezone", "Asia/Shanghai"))
        return (local.hour, local.minute) >= (hour, minute)
    except (TypeError, ValueError):
        return False


def _format_daily_content(content_type: str, content: dict[str, Any]) -> str:
    title = (
        "【每日一案】" if content_type == "daily_case" else "【每日一题｜原创练习题】"
    )
    body = content.get("content", content)
    if not isinstance(body, dict):
        return f"{title}\n{body}"
    labels = {
        "case_summary": "核心事实",
        "issues": "争议焦点",
        "reasoning": "裁判/检察要旨",
        "practice_notes": "涉及知识点",
        "question": "题干",
        "options": "选项",
        "answer": "答案",
        "explanation": "解析",
        "source_note": "参考来源/说明",
    }
    lines = [title]
    for key, value in body.items():
        lines.append(f"{labels.get(key, key)}：{value}")
    if content.get("source_url"):
        lines.append(f"官方来源：{content['source_url']}")
    return "\n".join(lines)


__all__ = ["LawAssistantService", "ScanFailure", "ScanResult"]
