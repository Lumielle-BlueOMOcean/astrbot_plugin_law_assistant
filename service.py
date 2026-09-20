from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ and "." in __package__:
    from .content import (
        format_question_content,
        normalize_subject,
        parse_origin,
        parse_question_type,
        parse_subject,
    )
    from .daily_plans import DailyPlan
    from .document_extractors import DocumentSegment, ParsedDocument
    from .learning_segmentation import (
        CASE_SEGMENTATION_VERSION,
        segment_official_cases,
    )
    from .learning_service import LearningService
    from .library_models import LibrarySource
    from .library_service import LibraryService
    from .models import CaseItem, LawUpdate, LegalEvent, SourceDocument
    from .publisher import format_deadline_reminder, format_event
    from .sources.base import Extractor, SourceAdapter, Validator
    from .storage import SQLiteStorage
else:
    from content import (
        format_question_content,
        normalize_subject,
        parse_origin,
        parse_question_type,
        parse_subject,
    )
    from daily_plans import DailyPlan
    from document_extractors import DocumentSegment, ParsedDocument
    from learning_segmentation import CASE_SEGMENTATION_VERSION, segment_official_cases
    from learning_service import LearningService
    from library_models import LibrarySource
    from library_service import LibraryService
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


@dataclass(frozen=True, slots=True)
class PendingPublication:
    content_type: str
    body: str
    target_ids: tuple[int, ...]
    created_at: datetime
    expires_at: datetime
    event_id: int | None = None
    owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class PendingPlanUpdate:
    target_id: int | None
    content_types: tuple[str, ...]
    plans: tuple[DailyPlan, ...]
    created_at: datetime
    expires_at: datetime
    owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContentReference:
    content_type: str
    content: dict[str, Any]
    owner_id: str
    session_origin: str
    created_at: datetime
    expires_at: datetime


def _case_needs_reprocessing(document: SourceDocument) -> bool:
    metadata = document.metadata
    return (
        metadata.get("case_segmentation_version") != CASE_SEGMENTATION_VERSION
        or metadata.get("case_processing_status") != "success"
    )


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
        library_service: LibraryService | None = None,
        document_ingestion: Any | None = None,
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
        self.library_service = library_service
        self.document_ingestion = document_ingestion
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._scan_lock = asyncio.Lock()
        self._publish_confirmations: dict[str, PendingPublication] = {}
        self._plan_confirmations: dict[str, PendingPlanUpdate] = {}
        self._content_references: dict[str, ContentReference] = {}
        self._scheduler_wakeup: Any | None = None

    def set_scheduler_wakeup(self, callback: Any | None) -> None:
        """Register the host scheduler's idempotent wake-up callback."""
        self._scheduler_wakeup = callback

    async def status(self) -> dict[str, Any]:
        return {
            "service": "law_assistant",
            "schema_version": self.storage.schema_version,
            "source_count": len(self.sources),
            "case_source_count": len(self.case_sources),
            "law_source_count": len(self.law_sources),
            "event_count": self.storage.count_events(),
            "case_count": len(self.storage.list_case_items(limit=100000)),
            "real_question_inventory": self.storage.real_question_inventory(),
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
                        # A document becomes the processed baseline only after its
                        # full extraction, validation, and event writes succeed.
                        self.storage.upsert_source_document(document)
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
                        if (
                            previous
                            and previous.content_hash == document.content_hash
                            and (
                                source_type != "case"
                                or not _case_needs_reprocessing(previous)
                            )
                        ):
                            continue
                        item = await _maybe_await(extractor.extract(document))
                        processing_status = "success"
                        if item is not None:
                            outcome = await _maybe_await(
                                handler(item, document=document, source_key=key)
                                if source_type == "case"
                                else handler(item)
                            )
                            if source_type == "case" and isinstance(outcome, dict):
                                processing_status = str(
                                    outcome.get("processing_status") or "success"
                                )
                            count += 1
                        # Keep the last successfully processed document so a failed
                        # extraction can be retried on the next scan.
                        if source_type == "case":
                            metadata = dict(document.metadata)
                            metadata.update(
                                {
                                    "case_segmentation_version": CASE_SEGMENTATION_VERSION,
                                    "case_processing_status": processing_status,
                                }
                            )
                            document = replace(document, metadata=metadata)
                        self.storage.upsert_source_document(document)
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

    async def _store_case(
        self,
        item: CaseItem,
        *,
        document: SourceDocument | None = None,
        source_key: str = "",
    ) -> dict[str, Any]:
        self.storage.upsert_case_item(item)
        if self.library_service is None or document is None:
            return {"processing_status": "success"}
        parsed = ParsedDocument(
            path=Path(f"{document.source_item_key}.html"),
            original_filename=document.title or f"{document.source_item_key}.html",
            mime_type=document.content_type,
            file_hash=document.content_hash,
            extracted_text_hash=document.content_hash,
            text=item.raw_text,
            segments=(DocumentSegment(0, item.raw_text, "文章正文"),),
        )
        split = segment_official_cases(parsed)
        candidates = []
        for candidate in split.candidates:
            current = dict(candidate)
            structured = dict(current.get("structured") or {})
            structured["authority"] = item.authority
            current["structured"] = structured
            current["subjects"] = list(item.subjects)
            candidates.append(current)
        source = LibrarySource(
            source_kind="official_article",
            title=item.title,
            raw_text=item.raw_text,
            source_url=item.source_url,
            content_hash=item.content_hash,
            created_at=self._now_utc(),
            created_by=f"source:{source_key or item.source_key}",
            session_origin=f"source:{source_key or item.source_key}",
            metadata={
                "adapter_key": source_key or item.source_key,
                "source_item_key": item.source_item_key,
                "authority": item.authority,
                "case_segmentation_version": CASE_SEGMENTATION_VERSION,
            },
        )
        result = await self.library_service.archive_official_cases(
            source=source,
            candidates=candidates,
            adapter_key=source_key or item.source_key,
            source_item_key=item.source_item_key,
            segmentation_version=CASE_SEGMENTATION_VERSION,
        )
        return {
            "processing_status": (
                "needs_review"
                if split.warnings
                or any(
                    str(candidate.get("status") or "").lower()
                    in {"needs_review", "review"}
                    for candidate in split.candidates
                )
                else "success"
            ),
            "archive_result": result,
        }

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

    async def rename_target(self, selector: str, label: str) -> dict[str, Any]:
        needle = str(selector or "").strip()
        new_label = str(label or "").strip()
        if not needle:
            return {
                "success": False,
                "reason": "必须提供明确的目标群 ID、UMO 或现有别名",
            }
        if not new_label:
            return {"success": False, "reason": "群别名不能为空"}
        matches = [
            target
            for target in self.storage.list_targets(enabled_only=False)
            if needle == str(target["id"])
            or needle == target["unified_msg_origin"]
            or needle.casefold() == str(target["label"]).casefold()
        ]
        if len(matches) > 1:
            return {"success": False, "reason": f"群名称有歧义：{needle}"}
        if not matches:
            return {"success": False, "reason": f"未找到发布目标：{needle}"}
        return self.storage.rename_target(matches[0]["id"], new_label)

    async def unbind_target(self, unified_msg_origin: str) -> bool:
        return self.storage.unbind_target(unified_msg_origin)

    async def list_targets(self) -> list[dict[str, Any]]:
        return self.storage.list_targets()

    async def question_inventory(self) -> dict[str, Any]:
        return self.storage.real_question_inventory()

    def _config_default_plan(self, content_type: str) -> DailyPlan:
        if content_type == "daily_case":
            values = {
                "enabled": bool(getattr(self.config, "daily_case_enabled", False)),
                "time": str(getattr(self.config, "daily_case_time", "08:00")),
                "selection_mode": str(
                    getattr(self.config, "daily_case_selection_mode", "random")
                ),
                "fixed_subject": getattr(self.config, "daily_case_subject", None),
                "rotation_subjects": tuple(
                    getattr(self.config, "daily_case_rotation_subjects", ())
                ),
                "rotation_start_date": getattr(
                    self.config, "daily_case_rotation_start_date", None
                ),
                "rotation_start_index": int(
                    getattr(self.config, "daily_case_rotation_start_index", 0)
                ),
            }
        else:
            values = {
                "enabled": bool(getattr(self.config, "daily_question_enabled", False)),
                "time": str(getattr(self.config, "daily_question_time", "08:00")),
                "selection_mode": str(
                    getattr(self.config, "daily_question_selection_mode", "random")
                ),
                "fixed_subject": getattr(self.config, "daily_question_subject", None),
                "rotation_subjects": tuple(
                    getattr(self.config, "daily_question_rotation_subjects", ())
                ),
                "rotation_start_date": getattr(
                    self.config, "daily_question_rotation_start_date", None
                ),
                "rotation_start_index": int(
                    getattr(self.config, "daily_question_rotation_start_index", 0)
                ),
                "question_origin": str(
                    getattr(self.config, "daily_question_origin", "random")
                ),
                "question_type": getattr(self.config, "daily_question_type", None),
            }
        if str(
            values.get("selection_mode", "random")
        ).strip().lower() == "rotation" and not values.get("rotation_start_date"):
            values["rotation_start_date"] = self.storage.get_rotation_anchor(
                content_type
            )
        return DailyPlan.from_mapping(
            content_type, values, allow_unanchored_rotation=True
        )

    def materialize_config_rotation_anchors(self) -> None:
        """Persist missing global rotation anchors exactly once per plan."""
        local_today = (
            _local_datetime(
                self._now_utc(), getattr(self.config, "timezone", "Asia/Shanghai")
            )
            .date()
            .isoformat()
        )
        for content_type in ("daily_case", "daily_question"):
            if self.storage.get_daily_plan(None, content_type) is not None:
                continue
            plan = self._config_default_plan(content_type)
            if (
                plan.enabled
                and plan.selection_mode == "rotation"
                and plan.rotation_subjects
                and plan.rotation_start_date is None
            ):
                self.storage.set_rotation_anchor(content_type, local_today)

    def effective_daily_plan(
        self, target_id: int | None, content_type: str
    ) -> DailyPlan:
        if target_id is not None:
            override = self.storage.get_daily_plan(target_id, content_type)
            if override is not None:
                return override
        global_plan = self.storage.get_daily_plan(None, content_type)
        return global_plan or self._config_default_plan(content_type)

    async def list_daily_plans(
        self, target_selectors: list[str] | None = None, *, days: int = 7
    ) -> dict[str, Any]:
        if target_selectors:
            targets = self._resolve_targets(target_selectors)
        else:
            targets = self.storage.list_targets(enabled_only=True)
        local_today = _local_datetime(
            self._now_utc(), getattr(self.config, "timezone", "Asia/Shanghai")
        ).date()
        result: dict[str, Any] = {
            "global": {
                content_type: {
                    "plan": self.effective_daily_plan(None, content_type).to_mapping(),
                    "preview": self.effective_daily_plan(None, content_type).preview(
                        local_today, days
                    ),
                }
                for content_type in ("daily_case", "daily_question")
            },
            "targets": [],
        }
        for target in targets:
            result["targets"].append(
                {
                    "target": target,
                    "plans": {
                        content_type: {
                            "plan": self.effective_daily_plan(
                                target["id"], content_type
                            ).to_mapping(),
                            "preview": self.effective_daily_plan(
                                target["id"], content_type
                            ).preview(local_today, days),
                            "override": self.storage.get_daily_plan(
                                target["id"], content_type
                            )
                            is not None,
                        }
                        for content_type in ("daily_case", "daily_question")
                    },
                }
            )
        return result

    async def prepare_daily_plan_update(
        self,
        *,
        target_selectors: list[str] | None = None,
        global_scope: bool = False,
        content_type: str = "both",
        changes: dict[str, Any] | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        if global_scope:
            target_id = None
            target_info: list[dict[str, Any]] = [{"scope": "global"}]
        else:
            targets = self._resolve_targets(target_selectors)
            if len(targets) != 1:
                return {
                    "ready": False,
                    "reason": "一次只能修改一个群级计划；全局修改请使用 global_scope",
                }
            target_id = targets[0]["id"]
            target_info = [targets[0]]
        content_types = (
            ("daily_case", "daily_question")
            if content_type == "both"
            else (content_type,)
        )
        if any(item not in {"daily_case", "daily_question"} for item in content_types):
            return {
                "ready": False,
                "reason": "content_type 必须是 daily_case、daily_question 或 both",
            }
        values = changes or {}
        local_today = _local_datetime(
            self._now_utc(), getattr(self.config, "timezone", "Asia/Shanghai")
        ).date()
        plans: list[DailyPlan] = []
        for item in content_types:
            current = self.effective_daily_plan(target_id, item).to_mapping()
            item_values = values.get(item, values) if isinstance(values, dict) else {}
            merged = {**current, **item_values, "content_type": item}
            if str(
                merged.get("selection_mode", "random")
            ).strip().lower() == "rotation" and not merged.get("rotation_start_date"):
                merged["rotation_start_date"] = local_today.isoformat()
            plans.append(DailyPlan.from_mapping(item, merged))
        token = secrets.token_urlsafe(12)
        created = self._now_utc()
        self._plan_confirmations[token] = PendingPlanUpdate(
            target_id=target_id,
            content_types=tuple(content_types),
            plans=tuple(plans),
            created_at=created,
            expires_at=created + timedelta(minutes=10),
            owner_id=actor_id,
        )
        return {
            "ready": True,
            "token": token,
            "scope": "global" if global_scope else "target",
            "targets": target_info,
            "content_types": list(content_types),
            "plans": [
                {"plan": plan.to_mapping(), "preview": plan.preview(local_today, 7)}
                for plan in plans
            ],
            "notice": "计划修改只会在明确确认后持久化。案例与题目计划独立执行。",
        }

    async def confirm_daily_plan_update(
        self, token: str, *, actor_id: str | None = None
    ) -> dict[str, Any]:
        pending = self._plan_confirmations.pop(token.strip(), None)
        if pending is None:
            return {"success": False, "reason": "确认 token 无效或已使用"}
        if pending.owner_id and pending.owner_id != str(actor_id or ""):
            self._plan_confirmations[token.strip()] = pending
            return {"success": False, "reason": "确认 token 不属于当前操作者"}
        if self._now_utc() > pending.expires_at:
            return {"success": False, "reason": "确认 token 已过期"}
        if pending.target_id is not None:
            target = self.storage.get_target(pending.target_id)
            if target is None or not target["enabled"]:
                return {"success": False, "reason": "目标群已不存在或已停用"}
        for plan in pending.plans:
            self.storage.upsert_daily_plan(plan, pending.target_id)
        if any(plan.enabled for plan in pending.plans) or self._has_enabled_plan():
            await self._wake_scheduler()
        return {"success": True, "content_types": list(pending.content_types)}

    def _has_enabled_plan(self) -> bool:
        if any(
            bool(getattr(self.config, name, False))
            for name in (
                "auto_scan_enabled",
                "daily_case_enabled",
                "daily_question_enabled",
                "law_update_enabled",
            )
        ):
            return True
        return any(
            bool(item.get("enabled")) for item in self.storage.list_daily_plans()
        )

    async def _wake_scheduler(self) -> None:
        if self._scheduler_wakeup is None:
            return
        result = self._scheduler_wakeup()
        if inspect.isawaitable(result):
            await result

    def _resolve_targets(self, selectors: list[str] | None) -> list[dict[str, Any]]:
        targets = self.storage.list_targets(enabled_only=True)
        if not selectors:
            if len(targets) == 1:
                return targets
            if not targets:
                raise ValueError("尚未绑定发布目标")
            raise ValueError("已绑定多个群，请明确提供群名、群 ID 或目标列表")
        if any(
            str(item).strip().lower() in {"all", "所有群", "所有学习群"}
            for item in selectors
        ):
            return targets
        result: list[dict[str, Any]] = []
        for selector in selectors:
            needle = str(selector).strip()
            matches = [
                target
                for target in targets
                if needle == str(target["id"])
                or needle == target["unified_msg_origin"]
                or needle.casefold() == str(target["label"]).casefold()
            ]
            if len(matches) > 1:
                raise ValueError(f"群名称有歧义：{needle}")
            if not matches:
                raise ValueError(f"未找到启用的发布目标：{needle}")
            if matches[0] not in result:
                result.append(matches[0])
        return result

    async def prepare_publish_event(
        self,
        event_id: int,
        target_selectors: list[str] | None = None,
        *,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        event = self.storage.get_event(event_id)
        if event is None:
            return {"ready": False, "reason": "event not found"}
        if not event.source_url:
            return {"ready": False, "reason": "事件缺少可验证的来源"}
        try:
            targets = self._resolve_targets(target_selectors)
        except ValueError as exc:
            return {"ready": False, "reason": str(exc)}
        token = secrets.token_urlsafe(12)
        created = self._now_utc()
        self._publish_confirmations[token] = PendingPublication(
            content_type="event",
            body=format_event(event),
            target_ids=tuple(target["id"] for target in targets),
            created_at=created,
            expires_at=created + timedelta(minutes=10),
            event_id=event_id,
            owner_id=actor_id,
        )
        return {
            "ready": True,
            "token": token,
            "event_id": event_id,
            "revision": event.revision,
            "targets": targets,
            "preview": format_event(event),
        }

    async def confirm_publish(
        self, token: str, *, actor_id: str | None = None
    ) -> dict[str, Any]:
        claimed = self._publish_confirmations.pop(token.strip(), None)
        if claimed is None:
            return {"success": False, "reason": "确认 token 无效或已使用"}
        if claimed.owner_id and claimed.owner_id != str(actor_id or ""):
            self._publish_confirmations[token.strip()] = claimed
            return {"success": False, "reason": "确认 token 不属于当前操作者"}
        if self._now_utc() > claimed.expires_at:
            return {"success": False, "reason": "确认 token 已过期"}
        if claimed.event_id is not None:
            count = await self._publish_event_to_targets(
                claimed.event_id,
                kind="manual",
                target_ids=claimed.target_ids,
                fixed_body=claimed.body,
            )
        else:
            count = await self._publish_fixed_to_targets(
                claimed.body, claimed.target_ids, claimed.content_type
            )
        return {"success": count > 0, "published_count": count}

    async def prepare_publish_question(
        self,
        *,
        origin: str = "random",
        subject: str | None = None,
        question_type: str | None = None,
        target_selectors: list[str] | None = None,
        session_origin: str | None = None,
        actor_id: str | None = None,
        content_ref: str | None = None,
    ) -> dict[str, Any]:
        try:
            targets = self._resolve_targets(target_selectors)
        except ValueError as exc:
            return {"ready": False, "reason": str(exc)}
        if content_ref:
            content, reference_error = self._take_content_reference(
                content_ref,
                content_type="question",
                actor_id=actor_id,
                session_origin=session_origin,
            )
            if content is None:
                return {"ready": False, "reason": reference_error}
        else:
            content = await self.generate_question(
                subject or "",
                origin=origin,
                question_type=question_type,
                session_origin=session_origin,
                actor_id=actor_id,
            )
        if not content.get("available"):
            return {"ready": False, **content}
        body = format_question_content(content)
        token = secrets.token_urlsafe(12)
        created = self._now_utc()
        self._publish_confirmations[token] = PendingPublication(
            content_type="question",
            body=body,
            target_ids=tuple(target["id"] for target in targets),
            created_at=created,
            expires_at=created + timedelta(minutes=10),
            owner_id=actor_id,
        )
        return {
            "ready": True,
            "token": token,
            "content_type": "question",
            "targets": targets,
            "preview": body,
            "content_ref": content.get("content_ref"),
            "selection": {
                key: content.get(key)
                for key in ("origin", "subject", "question_type", "source_url")
                if content.get(key) is not None
            },
        }

    async def prepare_publish_case(
        self,
        *,
        subject: str | None = None,
        target_selectors: list[str] | None = None,
        session_origin: str | None = None,
        actor_id: str | None = None,
        content_ref: str | None = None,
    ) -> dict[str, Any]:
        try:
            targets = self._resolve_targets(target_selectors)
        except ValueError as exc:
            return {"ready": False, "reason": str(exc)}
        if content_ref:
            content, reference_error = self._take_content_reference(
                content_ref,
                content_type="case",
                actor_id=actor_id,
                session_origin=session_origin,
            )
            if content is None:
                return {"ready": False, "reason": reference_error}
        else:
            content = await self.get_daily_case(
                subject=subject,
                session_origin=session_origin,
                actor_id=actor_id,
            )
        if not content.get("available"):
            return {"ready": False, **content}
        body = _format_daily_content("daily_case", content)
        token = secrets.token_urlsafe(12)
        created = self._now_utc()
        self._publish_confirmations[token] = PendingPublication(
            content_type="case",
            body=body,
            target_ids=tuple(target["id"] for target in targets),
            created_at=created,
            expires_at=created + timedelta(minutes=10),
            owner_id=actor_id,
        )
        return {
            "ready": True,
            "token": token,
            "content_type": "case",
            "targets": targets,
            "preview": body,
            "content_ref": content.get("content_ref"),
        }

    def _remember_content(
        self,
        content_type: str,
        content: dict[str, Any],
        *,
        actor_id: str | None,
        session_origin: str | None,
    ) -> dict[str, Any]:
        if not actor_id or not session_origin or not content.get("available"):
            return content
        token = secrets.token_urlsafe(12)
        now = self._now_utc()
        self._content_references[token] = ContentReference(
            content_type=content_type,
            content=copy.deepcopy(content),
            owner_id=str(actor_id),
            session_origin=str(session_origin),
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        result = dict(content)
        result["content_ref"] = token
        return result

    def _take_content_reference(
        self,
        token: str,
        *,
        content_type: str,
        actor_id: str | None,
        session_origin: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        key = str(token or "").strip()
        pending = self._content_references.get(key)
        if pending is None:
            return None, "内容引用无效、已使用或已过期"
        if self._now_utc() > pending.expires_at:
            self._content_references.pop(key, None)
            return None, "内容引用无效、已使用或已过期"
        if pending.content_type != content_type:
            return None, "内容引用类型不匹配"
        if pending.owner_id != str(actor_id or ""):
            return None, "内容引用不属于当前操作者"
        if pending.session_origin != str(session_origin or ""):
            return None, "内容引用不属于当前会话"
        self._content_references.pop(key, None)
        result = copy.deepcopy(pending.content)
        result["content_ref"] = key
        return result, ""

    async def set_case_subjects(
        self, case_id: int, subjects: list[str]
    ) -> dict[str, Any]:
        normalized = tuple(
            item for item in (normalize_subject(value) for value in subjects) if item
        )
        if not normalized:
            return {"success": False, "reason": "至少需要一个有效方向"}
        success = self.storage.set_case_subjects(case_id, normalized)
        legacy = self.storage.get_case_item(case_id)
        official_updated = 0
        if success and legacy is not None and self.library_service is not None:
            official_updated = self.library_service.set_official_case_subjects(
                source_key=legacy.source_key,
                source_item_key=legacy.source_item_key,
                subjects=normalized,
            )
        return {
            "success": success,
            "case_id": case_id,
            "subjects": list(normalized),
            "official_case_items_updated": official_updated,
        }

    def import_real_questions_file(self, path: str) -> dict[str, Any]:
        try:
            from .content import load_real_questions
        except ImportError:
            from content import load_real_questions
        try:
            questions = load_real_questions(path)
            count = self.storage.import_real_questions(questions)
        except (OSError, ValueError, TypeError) as exc:
            return {"success": False, "reason": str(exc)}
        return {
            "success": True,
            "imported_count": count,
            "inventory": self.storage.real_question_inventory(),
        }

    async def _publish_event_to_targets(
        self,
        event_id: int,
        *,
        kind: str,
        target_ids: tuple[int, ...] | None = None,
        fixed_body: str | None = None,
    ) -> int:
        event = self.storage.get_event(event_id)
        if event is None or self.publisher is None:
            return 0
        if kind == "automatic" and (
            event.status.upper() in {"CLOSED", "UNKNOWN"}
            or not any(
                date.confirmed
                and date.datetime is not None
                and date.kind in {"registration_deadline", "submission_deadline"}
                for date in event.dates
            )
        ):
            return 0
        count = 0
        allowed = set(target_ids) if target_ids is not None else None
        for target in self.storage.list_targets(enabled_only=True):
            if allowed is not None and target["id"] not in allowed:
                continue
            publication_id = self.storage.claim_publication(
                event.id, event.revision, target["id"], kind
            )
            if publication_id is None:
                continue
            success = await self.publisher.publish_text(
                target["unified_msg_origin"], fixed_body or format_event(event)
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

    async def _publish_fixed_to_targets(
        self, body: str, target_ids: tuple[int, ...], content_type: str
    ) -> int:
        if self.publisher is None:
            return 0
        count = 0
        enabled_targets = {
            target["id"]: target
            for target in self.storage.list_targets(enabled_only=True)
        }
        for target_id in target_ids:
            target = enabled_targets.get(target_id)
            if target is None:
                continue
            success = await self.publisher.publish_text(
                target["unified_msg_origin"], body
            )
            if success:
                count += 1
            else:
                self.logger.warning(
                    "Law Assistant failed to publish %s to %s",
                    content_type,
                    target["unified_msg_origin"],
                )
        return count

    async def check_deadline_reminders(self, *, now: datetime | None = None) -> int:
        if self.publisher is None:
            return 0
        timezone_name = getattr(self.config, "timezone", "Asia/Shanghai")
        local_current = _local_datetime(now or self.clock(), timezone_name)
        offsets = set(getattr(self.config, "deadline_reminder_days", (7, 3, 1)))
        if getattr(self.config, "deadline_same_day_enabled", True):
            offsets.add(0)
        sent = 0
        for item in self.storage.list_deadlines(now=None, limit=1000):
            event, date = item["event"], item["date"]
            if date.id is None or date.datetime is None:
                continue
            deadline_local = date.datetime.astimezone(local_current.tzinfo)
            remaining = (deadline_local.date() - local_current.date()).days
            if remaining not in offsets:
                continue
            for target in self.storage.list_targets(enabled_only=True):
                reminder_id = self.storage.claim_reminder(
                    event_id=event.id,
                    date_kind=date.kind,
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
        self,
        *,
        subject: str | None = None,
        session_origin: str | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            parse_subject(subject)
        except ValueError as exc:
            return {
                "available": False,
                "error": "invalid_parameter",
                "reason": str(exc),
            }
        if self.learning_service is None:
            return {"available": False, "reason": "daily case service unavailable"}
        try:
            result = await self.learning_service.daily_case(
                subject=subject, session_origin=session_origin
            )
        except ValueError as exc:
            return {
                "available": False,
                "error": "invalid_parameter",
                "reason": str(exc),
            }
        return self._remember_content(
            "case",
            result,
            actor_id=actor_id,
            session_origin=session_origin,
        )

    async def archive_learning_material(self, **kwargs: Any) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.archive_learning_material(**kwargs)

    async def import_learning_document(
        self,
        relative_path: str,
        *,
        created_by: str,
        session_origin: str,
        content_kind: str = "auto",
    ) -> dict[str, Any]:
        if self.document_ingestion is None:
            return {
                "success": False,
                "error": "document_ingestion_unavailable",
                "reason": "文档导入服务不可用",
            }
        return await self.document_ingestion.import_document(
            relative_path,
            created_by=created_by,
            session_origin=session_origin,
            content_kind=content_kind,
        )

    async def search_learning_library(self, **kwargs: Any) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.search_learning_library(**kwargs)

    async def get_learning_item(self, item_id: int) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.get_learning_item(item_id)

    async def update_learning_item(
        self, item_id: int, changes: dict[str, Any]
    ) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.update_learning_item(item_id, changes)

    async def list_learning_review_items(
        self,
        *,
        source_id: int | None = None,
        status: str = "pending",
        limit: int = 100,
    ) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.list_review_items(
            source_id=source_id, status=status, limit=limit
        )

    async def get_learning_review_item(self, review_id: int) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.get_review_item(review_id)

    async def update_learning_review_status(
        self, review_id: int, status: str
    ) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.update_review_status(review_id, status)

    async def generate_question(
        self,
        subject: str = "",
        *,
        question_type: str | None = None,
        origin: str = "random",
        source_name: str | None = None,
        exam_year: str | None = None,
        session_origin: str | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            parse_origin(origin)
            parse_subject(subject)
            parse_question_type(question_type)
        except ValueError as exc:
            return {
                "available": False,
                "error": "invalid_parameter",
                "reason": str(exc),
            }
        if self.learning_service is None:
            return {"available": False, "reason": "question service unavailable"}
        try:
            result = await self.learning_service.generate_question(
                subject=subject,
                question_type=question_type,
                origin=origin,
                source_name=source_name,
                exam_year=exam_year,
                session_origin=session_origin,
            )
        except ValueError as exc:
            return {
                "available": False,
                "error": "invalid_parameter",
                "reason": str(exc),
            }
        return self._remember_content(
            "question",
            result,
            actor_id=actor_id,
            session_origin=session_origin,
        )

    async def list_law_updates(self, limit: int = 50) -> list[LawUpdate]:
        return self.storage.list_law_updates(limit=limit)

    async def run_scheduled_jobs(
        self, *, now: datetime | None = None
    ) -> dict[str, Any]:
        auto_scan_enabled = bool(getattr(self.config, "auto_scan_enabled", False))
        stored_plans = self.storage.list_daily_plans()
        daily_case_enabled = bool(
            getattr(self.config, "daily_case_enabled", False)
        ) or any(
            item["content_type"] == "daily_case" and item["enabled"]
            for item in stored_plans
        )
        law_update_enabled = bool(getattr(self.config, "law_update_enabled", False))
        result: dict[str, Any] = {}
        if auto_scan_enabled:
            result["events"] = await self.scan_events(trigger="scheduler")
        if self.case_sources and (auto_scan_enabled or daily_case_enabled):
            result["cases"] = await self.scan_cases(trigger="scheduler")
        if self.law_sources and law_update_enabled:
            result["law_updates"] = await self.scan_law_updates(trigger="scheduler")
        current = now or self.clock()
        result["reminders_sent"] = (
            await self.check_deadline_reminders(now=current) if auto_scan_enabled else 0
        )
        result["daily_case_sent"] = await self._run_daily_content(
            content_type="daily_case",
            now=current,
        )
        result["daily_question_sent"] = await self._run_daily_content(
            content_type="daily_question",
            now=current,
        )
        return result

    async def _run_daily_content(
        self,
        *,
        content_type: str,
        now: datetime | str,
    ) -> int:
        if self.publisher is None:
            return 0
        local_now = _local_datetime(
            now, getattr(self.config, "timezone", "Asia/Shanghai")
        )
        sent = 0
        for target in self.storage.list_targets(enabled_only=True):
            plan = self.effective_daily_plan(target["id"], content_type)
            if not plan.enabled or not _time_is_due(now, plan.time, self.config):
                continue
            selected_subject = plan.subject_for(local_now.date())
            if plan.selection_mode == "rotation" and selected_subject is None:
                self._record_daily_skip(
                    local_now.date().isoformat(),
                    target["id"],
                    content_type,
                    "轮换计划尚未开始或缺少有效方向",
                    None,
                )
                continue
            content_date = local_now.date().isoformat()
            if self.storage.has_daily_content(
                content_date=content_date,
                target_id=target["id"],
                content_type=content_type,
            ):
                continue
            if content_type == "daily_case":
                content = await self.get_daily_case(subject=selected_subject)
            else:
                content = await self.generate_question(
                    selected_subject or "",
                    origin=plan.question_origin,
                    question_type=plan.question_type,
                )
            if not content.get("available"):
                reason = str(content.get("reason", "没有匹配内容"))
                if "匹配" in reason or "暂无" in reason:
                    self._record_daily_skip(
                        content_date,
                        target["id"],
                        content_type,
                        reason,
                        selected_subject,
                    )
                continue
            raw_body = content.get("content", content)
            body = raw_body if isinstance(raw_body, dict) else {"body": raw_body}
            claim_id = self.storage.claim_daily_content(
                content_date=content_date,
                target_id=target["id"],
                content_type=content_type,
                body=body,
                source_item_id=content.get("case_id") or content.get("question_id"),
            )
            if claim_id is None:
                continue
            formatted = (
                _format_daily_content(content_type, content)
                if content_type == "daily_case"
                else format_question_content(content)
            )
            success = await self.publisher.publish_text(
                target["unified_msg_origin"],
                formatted,
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

    def _record_daily_skip(
        self,
        content_date: str,
        target_id: int,
        content_type: str,
        reason: str,
        subject: str | None,
    ) -> None:
        claim_id = self.storage.record_daily_skip(
            content_date=content_date,
            target_id=target_id,
            content_type=content_type,
            reason=reason,
            subject=subject,
        )
        if claim_id is not None:
            self.storage.mark_daily_skipped(claim_id, reason)

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
    if content_type == "daily_case":
        if isinstance(content.get("card_body"), str):
            return content["card_body"]
        title = "【每日一案】"
        if content.get("subject"):
            title += f"｜{content['subject']}"
    else:
        title = "【每日一题｜模拟题】"
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
    if content_type == "daily_case":
        if content.get("title"):
            lines.append(f"案例：{content['title']}")
        if content.get("authority"):
            lines.append(f"来源机关：{content['authority']}")
    for key, value in body.items():
        lines.append(f"{labels.get(key, key)}：{value}")
    if content.get("source_url"):
        lines.append(f"官方来源：{content['source_url']}")
    return "\n".join(lines)


__all__ = ["LawAssistantService", "ScanFailure", "ScanResult"]
