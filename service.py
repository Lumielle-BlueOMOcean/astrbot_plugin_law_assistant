from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

if __package__ and "." in __package__:
    from .activity_radar import (
        canonical_event_key,
        derive_radar_status,
        radar_policy_for,
    )
    from .content import (
        RealQuestion,
        normalize_subject,
        parse_origin,
        parse_question_type,
        parse_subject,
        real_question_identity_key,
    )
    from .daily_plans import DailyPlan
    from .daily_resolver import resolve_daily_constraints
    from .daily_timing import local_due_datetime, next_daily_due
    from .date_parser import parse_datetime_value
    from .document_extractors import DocumentParseError, DocumentSegment, ParsedDocument
    from .learning_segmentation import (
        CASE_SEGMENTATION_VERSION,
        segment_official_cases,
    )
    from .learning_service import LearningService
    from .library_models import LibrarySource
    from .library_service import LibraryService
    from .models import CaseItem, LawUpdate, LegalEvent, SourceDocument
    from .pagination import page_payload, page_request
    from .publisher import format_deadline_reminder, format_event
    from .question_session import (
        build_question_session_snapshot,
        display_session_page_index,
        encode_session_page_index,
        format_session_answer,
        format_session_explanation,
        format_session_prompt,
        format_session_status,
        prepare_question_session_snapshot,
    )
    from .question_session_repository import QuestionSessionRepository
    from .scheduled_reveals import ScheduledRevealRepository
    from .sources.base import Extractor, SourceAdapter, Validator
    from .storage import SQLiteStorage
    from .structured_ingestion import (
        PreparedStructuredImport,
        StructuredImportError,
        StructuredMaterialIngestionService,
    )
else:
    from activity_radar import (
        canonical_event_key,
        derive_radar_status,
        radar_policy_for,
    )
    from content import (
        RealQuestion,
        normalize_subject,
        parse_origin,
        parse_question_type,
        parse_subject,
        real_question_identity_key,
    )
    from daily_plans import DailyPlan
    from daily_resolver import resolve_daily_constraints
    from daily_timing import local_due_datetime, next_daily_due
    from date_parser import parse_datetime_value
    from document_extractors import DocumentParseError, DocumentSegment, ParsedDocument
    from learning_segmentation import CASE_SEGMENTATION_VERSION, segment_official_cases
    from learning_service import LearningService
    from library_models import LibrarySource
    from library_service import LibraryService
    from models import CaseItem, LawUpdate, LegalEvent, SourceDocument
    from pagination import page_payload, page_request
    from publisher import format_deadline_reminder, format_event
    from question_session import (
        build_question_session_snapshot,
        display_session_page_index,
        encode_session_page_index,
        format_session_answer,
        format_session_explanation,
        format_session_prompt,
        format_session_status,
        prepare_question_session_snapshot,
    )
    from question_session_repository import QuestionSessionRepository
    from scheduled_reveals import ScheduledRevealRepository
    from sources.base import Extractor, SourceAdapter, Validator
    from storage import SQLiteStorage
    from structured_ingestion import (
        PreparedStructuredImport,
        StructuredImportError,
        StructuredMaterialIngestionService,
    )


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
    disabled_sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PendingPublication:
    content_type: str
    body: str
    target_ids: tuple[int, ...]
    created_at: datetime
    expires_at: datetime
    event_id: int | None = None
    owner_id: str | None = None
    event_revision: int | None = None
    event_content_hash: str | None = None
    question_snapshot: dict[str, Any] | None = None
    question_identity: str | None = None
    source_kind: str | None = None
    source_item_key: str | None = None
    library_item_id: int | None = None
    real_question_id: int | None = None


@dataclass(frozen=True, slots=True)
class PendingPlanUpdate:
    target_id: int | None
    content_types: tuple[str, ...]
    plans: tuple[DailyPlan, ...]
    created_at: datetime
    expires_at: datetime
    owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class PendingPlanReset:
    target_id: int | None
    content_types: tuple[str, ...]
    global_plans: tuple[DailyPlan, ...]
    created_at: datetime
    expires_at: datetime
    owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class PendingImport:
    token: str
    prepared: Any
    staged_path: str
    file_hash: str
    created_at: datetime
    expires_at: datetime
    owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class PendingStructuredImport:
    token: str
    prepared: PreparedStructuredImport
    created_at: datetime
    expires_at: datetime
    owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class PendingCandidatePromotion:
    owner_id: str
    items: tuple[dict[str, Any], ...]
    prevalidation_failures: tuple[dict[str, Any], ...]
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PendingEventStatusOverride:
    owner_id: str
    event_id: int
    source_hash: str
    status: str
    reason: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PendingEventDateReview:
    owner_id: str
    event_id: int
    event_date_id: int
    source_hash: str
    evidence_hash: str
    evidence_text: str
    old_value: dict[str, Any]
    proposed_value: dict[str, Any]
    decision: str
    reason: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PendingManagementBatch:
    owner_id: str
    action: str
    item_ids: tuple[int, ...]
    changes: dict[str, Any]
    expected: dict[int, dict[str, Any]]
    preview: tuple[dict[str, Any], ...]
    created_at: datetime
    expires_at: datetime
    promotion_token: str | None = None


@dataclass(frozen=True, slots=True)
class PendingModerationBatch:
    owner_id: str
    domain: str
    status: str
    reason: str
    item_ids: tuple[int, ...]
    expected: dict[int, dict[str, Any]]
    preview: tuple[dict[str, Any], ...]
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PendingDataClear:
    owner_id: str
    scope: str
    snapshot: dict[str, list[tuple[Any, ...]]]
    preview: dict[str, Any]
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ContentReference:
    content_type: str
    content: dict[str, Any]
    owner_id: str
    session_origin: str
    created_at: datetime
    expires_at: datetime


def _delivery_status(outcome: Any) -> tuple[str, str | None]:
    """Normalize legacy bool publishers and explicit transport outcomes."""
    if isinstance(outcome, bool):
        return ("sent" if outcome else "failed"), None
    status = str(getattr(outcome, "status", "failed"))
    if status not in {"sent", "failed", "unknown"}:
        status = "failed"
    return status, getattr(outcome, "error", None)


_DAILY_HISTORY_SAFE_FIELDS = (
    "id",
    "content_date",
    "target_id",
    "content_type",
    "status",
    "attempted_at",
    "intended_local_at",
    "target_label",
    "finished_at",
    "error_summary",
    "source_kind",
    "source_item_key",
    "resolved_subject",
    "resolved_question_type",
    "resolved_origin",
)


def _daily_history_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    """Expose bounded daily-run metadata without stored content bodies."""
    return {key: record[key] for key in _DAILY_HISTORY_SAFE_FIELDS if key in record}


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
        structured_ingestion: StructuredMaterialIngestionService | None = None,
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
        self.structured_ingestion = structured_ingestion
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._scan_lock = asyncio.Lock()
        self._publish_confirmations: dict[str, PendingPublication] = {}
        self._plan_confirmations: dict[str, PendingPlanUpdate] = {}
        self._plan_reset_confirmations: dict[str, PendingPlanReset] = {}
        self._import_confirmations: dict[str, PendingImport] = {}
        self._structured_import_confirmations: dict[str, PendingStructuredImport] = {}
        self._candidate_promotion_confirmations: dict[
            str, PendingCandidatePromotion
        ] = {}
        self._event_status_confirmations: dict[str, PendingEventStatusOverride] = {}
        self._event_date_review_confirmations: dict[str, PendingEventDateReview] = {}
        self._management_batch_confirmations: dict[str, PendingManagementBatch] = {}
        self._moderation_batch_confirmations: dict[str, PendingModerationBatch] = {}
        self._data_clear_confirmations: dict[str, PendingDataClear] = {}
        self._unbind_confirmations: dict[
            str, tuple[int, datetime, datetime, str | None]
        ] = {}
        self._content_references: dict[str, ContentReference] = {}
        self.question_sessions = QuestionSessionRepository(storage.connection)
        self.scheduled_reveals = ScheduledRevealRepository(storage.connection)
        self._scheduled_reveal_lock = asyncio.Lock()
        self._scheduler_wakeup: Any | None = None

    def set_scheduler_wakeup(self, callback: Any | None) -> None:
        """Register the host scheduler's idempotent wake-up callback."""
        self._scheduler_wakeup = callback

    def open_question_session(
        self,
        content: dict[str, Any],
        *,
        session_origin: str,
        actor_id: str,
        target_id: int | None = None,
        source_kind: str | None = None,
    ) -> dict[str, Any]:
        """Persist an immutable question snapshot and return its answer-free first page."""
        if not content.get("available", True):
            return {
                "success": False,
                "reason": str(content.get("reason") or "题目不可用"),
            }
        scope = str(session_origin or "").strip()
        if not scope:
            return {"success": False, "reason": "缺少题目会话来源，无法安全保存进度"}
        limit = getattr(self.config, "question_message_max_chars", 1600)
        try:
            limit = max(300, min(4000, int(limit)))
        except (TypeError, ValueError):
            limit = 1600
        snapshot = build_question_session_snapshot(content, max_chars=limit)
        item = content.get("item") if isinstance(content.get("item"), dict) else {}
        origin = str(content.get("origin") or "")
        identity = str(
            snapshot.get("identity")
            or item.get("identity")
            or ("verified_real_question" if origin == "real" else "mock_question")
        )
        library_id = item.get("id")
        try:
            library_id = int(library_id) if library_id is not None else None
        except (TypeError, ValueError):
            library_id = None
        question_id = content.get("question_id") if origin == "real" else None
        try:
            question_id = int(question_id) if question_id is not None else None
        except (TypeError, ValueError):
            question_id = None
        if (
            question_id is not None
            and self.storage.connection.execute(
                "SELECT 1 FROM real_questions WHERE id = ?", (question_id,)
            ).fetchone()
            is None
        ):
            question_id = None
        if (
            library_id is not None
            and self.storage.connection.execute(
                "SELECT 1 FROM learning_items WHERE id = ?", (library_id,)
            ).fetchone()
            is None
        ):
            library_id = None
        item_key = str(
            content.get("question_id")
            or item.get("id")
            or snapshot.get("snapshot_hash")
        )
        return self._persist_question_snapshot(
            snapshot,
            session_origin=scope,
            actor_id=actor_id,
            target_id=target_id,
            source_kind=(
                source_kind
                or (
                    str(content.get("source_kind") or "")
                    or (
                        "library_question"
                        if item
                        else (
                            "real_question"
                            if origin == "real"
                            else "generated_question"
                        )
                    )
                )
            ),
            source_item_key=item_key,
            question_identity=identity,
            library_item_id=library_id,
            real_question_id=question_id,
        )

    def _persist_question_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        session_origin: str,
        actor_id: str,
        target_id: int | None = None,
        source_kind: str,
        source_item_key: str,
        question_identity: str,
        library_item_id: int | None = None,
        real_question_id: int | None = None,
    ) -> dict[str, Any]:
        if (
            library_item_id is not None
            and self.storage.connection.execute(
                "SELECT 1 FROM learning_items WHERE id = ?", (library_item_id,)
            ).fetchone()
            is None
        ):
            library_item_id = None
        if (
            real_question_id is not None
            and self.storage.connection.execute(
                "SELECT 1 FROM real_questions WHERE id = ?", (real_question_id,)
            ).fetchone()
            is None
        ):
            real_question_id = None
        session = self.question_sessions.create_session(
            session_key=secrets.token_urlsafe(18),
            scope_origin=session_origin,
            target_id=target_id,
            source_kind=source_kind,
            source_item_key=source_item_key,
            library_item_id=library_item_id,
            real_question_id=real_question_id,
            question_identity=question_identity,
            snapshot=snapshot,
            created_by=str(actor_id),
            created_at=self._now_utc().isoformat(),
        )
        text = self._question_session_prompt(session)
        return {
            "success": True,
            "session_id": session["id"],
            "identity": snapshot["identity_label"],
            "text": text,
            "snapshot_hash": session["snapshot_hash"],
        }

    async def start_question_session(
        self,
        *,
        subject: str = "",
        origin: str = "random",
        question_type: str | None = None,
        session_origin: str,
        actor_id: str,
    ) -> dict[str, Any]:
        content = await self.generate_question(
            subject,
            origin=origin,
            question_type=question_type,
            session_origin=session_origin,
            actor_id=actor_id,
        )
        if not content.get("available"):
            return {"success": False, **content}
        result = self.open_question_session(
            content, session_origin=session_origin, actor_id=actor_id
        )
        if result.get("success") and content.get("content_ref"):
            result["content_ref"] = content["content_ref"]
        return result

    async def start_library_question_session(
        self, item_id: int, *, session_origin: str, actor_id: str
    ) -> dict[str, Any]:
        detail = await self.get_learning_item_for_session(item_id)
        if not detail.get("success") or not isinstance(detail.get("question"), dict):
            return {
                "success": False,
                "reason": detail.get("message", "该条目不是可学习题目"),
            }
        item = detail.get("item") or {}
        identity = str(item.get("identity") or "")
        if identity not in {"real_question_candidate", "mock_question"}:
            return {
                "success": False,
                "reason": "仅支持人工学习来源候选题或模拟题；候选题不会升级为核验真题",
            }
        content = dict(detail)
        content["subject"] = (item.get("subjects") or [""])[0]
        content["question_type"] = detail["question"].get("question_type")
        content["origin"] = (
            "candidate" if identity == "real_question_candidate" else "mock"
        )
        content["available"] = True
        remembered = self._remember_content(
            "question",
            content,
            actor_id=actor_id,
            session_origin=session_origin,
        )
        opened = self.open_question_session(
            remembered,
            session_origin=session_origin,
            actor_id=actor_id,
            source_kind="library_question",
        )
        if opened.get("success") and remembered.get("content_ref"):
            opened["content_ref"] = remembered["content_ref"]
        return opened

    async def question_session_action(
        self, action: str, *, session_origin: str, actor_id: str
    ) -> dict[str, Any]:
        """Apply one explicit command to the active session in exactly this chat scope."""
        session = self.question_sessions.get_active(str(session_origin or ""))
        if session is None:
            return {
                "success": False,
                "reason": "当前会话没有进行中的题目；可以请我出一道题，或打开资料库中的题目开始学习。",
            }
        action = str(action).strip().lower().replace("_", "-")
        if action == "close":
            self.question_sessions.close(
                session["id"], actor_id=str(actor_id), at=self._now_utc().isoformat()
            )
            return {
                "success": True,
                "session_id": session["id"],
                "text": "已结束当前题目会话。",
            }
        snapshot = prepare_question_session_snapshot(session["snapshot"])
        session["snapshot"] = snapshot
        if action == "current":
            return {
                "success": True,
                "session_id": session["id"],
                "text": format_session_status(session),
            }
        if action == "next":
            if session.get("current_stage") in {"answer", "explanation"}:
                continuation = (
                    "继续查看答案"
                    if session["current_stage"] == "answer"
                    else "继续查看解析"
                )
                return {
                    "success": False,
                    "reason": f"当前正在阅读已揭晓内容；请使用 {continuation} 继续，不会跳转题面。",
                }
            materials = snapshot.get("materials", [])
            material_index = session["current_material_index"]
            material_page = session["current_material_page"]
            prompt_index = session["current_prompt_index"]
            prompt_page = session["current_prompt_page"]
            if material_index < len(materials):
                pages = materials[material_index].get("pages", [])
                display_page = display_session_page_index(
                    snapshot, "materials", material_index, material_page
                )
                if display_page + 1 < len(pages):
                    material_page = encode_session_page_index(
                        snapshot, display_page + 1
                    )
                else:
                    material_index += 1
                    material_page = 0
            else:
                prompts = snapshot.get("prompts", [])
                pages = prompts[prompt_index].get("stem_pages", []) if prompts else []
                display_page = display_session_page_index(
                    snapshot, "prompts", prompt_index, prompt_page
                )
                if display_page + 1 >= len(pages):
                    return {
                        "success": True,
                        "session_id": session["id"],
                        "text": "本题内容已展示完毕。你可以继续讨论、明确要求查看答案，或在有下一小问时进入下一小问。",
                    }
                prompt_page = encode_session_page_index(snapshot, display_page + 1)
            session = (
                self.question_sessions.advance(
                    session["id"],
                    actor_id=str(actor_id),
                    at=self._now_utc().isoformat(),
                    material_index=material_index,
                    material_page=material_page,
                    prompt_index=prompt_index,
                    prompt_page=prompt_page,
                )
                or session
            )
            session["snapshot"] = snapshot
        elif action in {"next-answer", "next-explanation"}:
            kind = "answer" if action == "next-answer" else "explanation"
            if session.get("current_stage") != kind:
                reveal_command = "查看答案" if kind == "answer" else "查看解析"
                return {
                    "success": False,
                    "reason": f"当前尚未进入{('答案' if kind == 'answer' else '解析')}分页；请先使用 {reveal_command}。",
                }
            prompt_index = session["current_prompt_index"]
            page_index = session["current_prompt_page"] + 1
            prompts = snapshot.get("prompts", [])
            page_key = "answer_pages" if kind == "answer" else "explanation_pages"
            pages = (
                prompts[prompt_index].get(page_key, [])
                if prompt_index < len(prompts)
                else []
            )
            if page_index >= len(pages):
                noun = "答案" if kind == "answer" else "解析"
                return {
                    "success": True,
                    "session_id": session["id"],
                    "text": f"当前小问的{noun}已展示完毕。",
                }
            session = (
                self.question_sessions.advance(
                    session["id"],
                    actor_id=str(actor_id),
                    at=self._now_utc().isoformat(),
                    material_index=session["current_material_index"],
                    material_page=session["current_material_page"],
                    prompt_index=prompt_index,
                    prompt_page=page_index,
                    stage=kind,
                )
                or session
            )
            session["snapshot"] = snapshot
            text = (
                format_session_answer(
                    snapshot, prompt_index=prompt_index, page_index=page_index
                )
                if kind == "answer"
                else format_session_explanation(
                    snapshot, prompt_index=prompt_index, page_index=page_index
                )
            )
            return {"success": True, "session_id": session["id"], "text": text}
        elif action == "next-question":
            prompts = snapshot.get("prompts", [])
            next_index = session["current_prompt_index"] + 1
            if next_index >= len(prompts):
                return {"success": False, "reason": "没有更多小问了。"}
            session = (
                self.question_sessions.advance(
                    session["id"],
                    actor_id=str(actor_id),
                    at=self._now_utc().isoformat(),
                    material_index=len(snapshot.get("materials", [])),
                    material_page=0,
                    prompt_index=next_index,
                    prompt_page=0,
                )
                or session
            )
            session["snapshot"] = snapshot
        elif action in {"answer", "explanation"}:
            unread_notice = (
                action == "answer"
                and self._question_session_has_unread_content(session)
            )
            async with self._scheduled_reveal_lock:
                session = (
                    self.question_sessions.reveal(
                        session["id"],
                        action,
                        actor_id=str(actor_id),
                        at=self._now_utc().isoformat(),
                        prompt_index=session["current_prompt_index"],
                    )
                    or session
                )
                self.scheduled_reveals.skip_for_manual_reveal(
                    session["id"],
                    action,
                    prompt_index=session["current_prompt_index"],
                    at=self._now_utc().isoformat(),
                )
            prompt_index = session["current_prompt_index"]
            page_index = session["current_prompt_page"]
            text = (
                format_session_answer(
                    snapshot, prompt_index=prompt_index, page_index=page_index
                )
                if action == "answer"
                else format_session_explanation(
                    snapshot, prompt_index=prompt_index, page_index=page_index
                )
            )
            if unread_notice:
                notice = "提示：题目材料或题干尚未全部展开。"
                if len(notice) + 1 + len(text) <= int(
                    snapshot.get("message_max_chars", 1600)
                ):
                    text = f"{notice}\n{text}"
            session["snapshot"] = snapshot
            return {"success": True, "session_id": session["id"], "text": text}
        else:
            return {"success": False, "reason": f"不支持的题目会话操作：{action}"}
        return {
            "success": True,
            "session_id": session["id"],
            "text": self._question_session_prompt(session),
        }

    @staticmethod
    def _question_session_prompt(session: dict[str, Any]) -> str:
        snapshot = prepare_question_session_snapshot(session["snapshot"])
        material_index = session["current_material_index"]
        if material_index < len(snapshot.get("materials", [])):
            return format_session_prompt(
                snapshot,
                material_index=material_index,
                material_page=session["current_material_page"],
                prompt_index=session["current_prompt_index"],
            )
        return format_session_prompt(
            snapshot,
            prompt_index=session["current_prompt_index"],
            prompt_page=session["current_prompt_page"],
        )

    @staticmethod
    def _question_session_has_unread_content(session: dict[str, Any]) -> bool:
        snapshot = session["snapshot"]
        materials = snapshot.get("materials", [])
        if session["current_material_index"] < len(materials):
            return True
        prompts = snapshot.get("prompts", [])
        prompt_index = session["current_prompt_index"]
        if prompt_index >= len(prompts):
            return False
        pages = prompts[prompt_index].get("stem_pages", [])
        display_page = display_session_page_index(
            snapshot,
            "prompts",
            prompt_index,
            session["current_prompt_page"],
        )
        return display_page + 1 < len(pages)

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

    async def dashboard_overview(self) -> dict[str, Any]:
        """Build the bounded read model used by the embedded management page."""
        events = await self.list_events(limit=1000, radar_status="all")
        radar_counts = {
            key: 0 for key in ("current", "needs_review", "historical", "ignored")
        }
        for event in events:
            radar_counts[self._radar_status(event)] += 1
        learning = (
            self.library_service.dashboard_summary()
            if self.library_service is not None
            else {}
        )
        learning["verified_real"] = self.storage.count_real_questions()
        plans = self.storage.list_daily_plans()
        targets_by_id = {
            int(target["id"]): target
            for target in self.storage.list_targets(enabled_only=False)
        }
        question_sessions = []
        for session in self.question_sessions.list_active(limit=100):
            snapshot = session["snapshot"]
            target = targets_by_id.get(session["target_id"])
            prompts = snapshot.get("prompts", [])
            question_sessions.append(
                {
                    "target_label": target["label"] if target else "私聊会话",
                    "identity_label": snapshot.get("identity_label", "题目"),
                    "subject": snapshot.get("subject_label", "其他"),
                    "question_type": snapshot.get("question_type_label", ""),
                    "prompt_index": session["current_prompt_index"] + 1,
                    "prompt_count": len(prompts),
                    "material_index": min(
                        session["current_material_index"],
                        len(snapshot.get("materials", [])),
                    ),
                    "material_count": len(snapshot.get("materials", [])),
                    "answer_revealed": session["current_prompt_answer_revealed"],
                    "explanation_revealed": session[
                        "current_prompt_explanation_revealed"
                    ],
                    "updated_at": session["updated_at"],
                }
            )
        status = await self.status()
        return {
            "schema_version": self.storage.schema_version,
            "plugin": {
                "version": "0.6.0",
                "schema_version": self.storage.schema_version,
                "scheduler_enabled": bool(
                    any(
                        bool(getattr(self.config, name, False))
                        for name in (
                            "auto_scan_enabled",
                            "daily_case_enabled",
                            "daily_question_enabled",
                            "law_update_enabled",
                        )
                    )
                    or any(bool(item.get("enabled")) for item in plans)
                ),
                "scan_in_progress": self._scan_lock.locked(),
            },
            "radar": {
                **radar_counts,
                "last_scan": status.get("last_source_run"),
                "sources": await self.list_sources(),
            },
            "learning": learning,
            "question_sessions": question_sessions,
            "targets": {
                "count": len(self.storage.list_targets(enabled_only=True)),
                "enabled_plans": sum(1 for item in plans if item.get("enabled")),
            },
            "recent": {
                "daily": [
                    _daily_history_safe_record(record)
                    for record in self.storage.list_daily_contents(limit=10)
                ],
                "source_runs": self.storage.list_source_runs(limit=10),
            },
        }

    async def dashboard_radar_events(
        self,
        *,
        limit: int = 50,
        radar_status: str = "current",
        event_type: str | None = None,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        events = await self.list_events(
            limit=limit,
            radar_status=radar_status,
            event_type=event_type,
            keyword=keyword,
        )
        return [_event_dashboard_dict(event) for event in events]

    async def dashboard_radar_events_page(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        radar_status: str = "current",
        event_type: str | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        if radar_status not in {"current", "needs_review", "historical", "all"}:
            raise ValueError("不支持的活动状态")
        normalized_page, normalized_size, offset = page_request(page, page_size)
        selected: list[LegalEvent] = []
        total = 0
        normalized_keyword = str(keyword or "").casefold()
        for event in self.storage.iter_events():
            status = self._radar_status(event)
            if radar_status != "all" and status != radar_status:
                continue
            if event_type and event.event_type.casefold() != str(event_type).casefold():
                continue
            if (
                normalized_keyword
                and normalized_keyword
                not in (
                    f"{event.title} {event.organizer} {event.summary} {event.eligibility}"
                ).casefold()
            ):
                continue
            if offset <= total < offset + normalized_size:
                selected.append(
                    replace(
                        event,
                        metadata={
                            **event.metadata,
                            "radar_status": status,
                            "status_override": self.storage.get_event_status_override(
                                int(event.id)
                            )
                            if event.id is not None
                            else None,
                        },
                    )
                )
            total += 1
        return page_payload(
            [_event_dashboard_dict(event) for event in selected],
            page=normalized_page,
            page_size=normalized_size,
            total=total,
        )

    async def dashboard_history(
        self, kind: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        if kind == "daily":
            return [
                _daily_history_safe_record(record)
                for record in self.storage.list_daily_contents(limit=safe_limit)
            ]
        if kind == "publications":
            return self.storage.list_publications(limit=safe_limit)
        if kind == "reminders":
            return self.storage.list_reminders(limit=safe_limit)
        if kind == "sources":
            return self.storage.list_source_runs(limit=safe_limit)
        if kind == "reveals":
            return self.scheduled_reveals.list_history(limit=safe_limit)
        raise ValueError(
            "history kind 必须是 daily、publications、reminders、sources 或 reveals"
        )

    async def dashboard_page(
        self, kind: str, *, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        normalized_page, normalized_size, _ = page_request(page, page_size)
        if kind == "cases":
            if self.library_service is None:
                return page_payload(
                    [], page=normalized_page, page_size=normalized_size, total=0
                )
            repository = self.library_service.repository
            items = repository.search(
                item_type="case",
                identity="official_case",
                limit=normalized_size,
                offset=(normalized_page - 1) * normalized_size,
            )
            total = repository.count_search(item_type="case", identity="official_case")
            return page_payload(
                [self.library_service._item_summary(item) for item in items],
                page=normalized_page,
                page_size=normalized_size,
                total=total,
            )
        result = self.storage.dashboard_list_page(
            kind, page=normalized_page, page_size=normalized_size
        )
        items = result["items"]
        if kind == "daily":
            items = [_daily_history_safe_record(item) for item in items]
        elif kind == "targets":
            items = [{**item, "enabled": bool(item.get("enabled"))} for item in items]
        elif kind == "reveals":
            for item in items:
                item["page_progress"] = json.loads(
                    item.pop("page_progress_json", "[]") or "[]"
                )
                target = (
                    self.storage.get_target(int(item["target_id"]))
                    if item.get("target_id") is not None
                    else None
                )
                item["target_label"] = target["label"] if target else "已解绑目标"
        elif kind == "sources":
            items = [{**item, "success": bool(item.get("success"))} for item in items]
        result["items"] = items
        return result

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
            disabled_sources: list[str] = []
            new_event_ids: list[int] = []
            for adapter, extractor in self.sources:
                source_key = str(getattr(adapter, "key", adapter.__class__.__name__))
                policy = radar_policy_for(source_key, self.config)
                if not policy.discover_enabled:
                    disabled_sources.append(source_key)
                    self.logger.info(
                        "Law Assistant source %s discovery is disabled", source_key
                    )
                    continue
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
                            canonical_key = canonical_event_key(accepted_event)
                            prior_canonical_events = []
                            if canonical_key:
                                prior_canonical_events = [
                                    prior_event
                                    for prior_event in self.storage.list_events(
                                        limit=10000
                                    )
                                    if prior_event.source_key
                                    != accepted_event.source_key
                                    and prior_event.source_item_key
                                    != accepted_event.source_item_key
                                    and canonical_event_key(prior_event)
                                    == canonical_key
                                ]
                            result = self.storage.upsert_event_detailed(accepted_event)
                            if canonical_key:
                                for prior_event in prior_canonical_events:
                                    self.storage.record_event_relation(
                                        result.event_id,
                                        int(prior_event.id),
                                        canonical_key,
                                    )
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
                disabled_sources=tuple(disabled_sources),
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

    async def list_events(
        self,
        limit: int = 20,
        *,
        radar_status: str = "current",
        event_type: str | None = None,
        keyword: str | None = None,
    ) -> list[LegalEvent]:
        if radar_status not in {
            "current",
            "needs_review",
            "historical",
            "ignored",
            "all",
        }:
            raise ValueError(
                "radar_status 必须是 current、needs_review、historical、ignored 或 all"
            )
        candidates = self.storage.list_events(limit=1000)
        result: list[LegalEvent] = []
        for event in candidates:
            derived_status = self._radar_status(event)
            if radar_status != "all" and derived_status != radar_status:
                continue
            if event_type and event.event_type.casefold() != str(event_type).casefold():
                continue
            if keyword:
                haystack = (
                    f"{event.title} {event.organizer} {event.summary} {event.eligibility}"
                ).casefold()
                if str(keyword).casefold() not in haystack:
                    continue
            result.append(
                replace(
                    event,
                    metadata={
                        **event.metadata,
                        "radar_status": derived_status,
                        "status_override": self.storage.get_event_status_override(
                            int(event.id)
                        )
                        if event.id is not None
                        else None,
                    },
                )
            )
        result.sort(key=self._event_sort_key)
        return result[: max(1, min(limit, 100))]

    async def get_event(self, event_id: int) -> LegalEvent | None:
        event = self.storage.get_event(event_id)
        if event is None:
            return None
        return replace(
            event,
            metadata={
                **event.metadata,
                "radar_status": self._radar_status(event),
                "status_override": self.storage.get_event_status_override(int(event.id))
                if event.id is not None
                else None,
            },
        )

    def _radar_status(self, event: LegalEvent) -> str:
        override = (
            self.storage.get_event_status_override(event.id)
            if event.id is not None
            else None
        )
        if override is not None:
            return str(override["override_status"])
        return derive_radar_status(
            event,
            self._now_utc(),
            getattr(self.config, "timezone", "Asia/Shanghai"),
            tuple(getattr(self.config, "radar_historical_keywords", ()) or ()),
        )

    async def prepare_event_status_override(
        self,
        event_id: int,
        status: str,
        reason: str,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "ready": False,
                "error": "forbidden",
                "reason": "需要 operator/Admin 权限",
            }
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {
            "current",
            "needs_review",
            "historical",
            "ignored",
            "derived",
        }:
            return {"ready": False, "reason": "不支持的雷达展示状态"}
        normalized_reason = str(reason or "").strip()
        if not normalized_reason or len(normalized_reason) > 500:
            return {"ready": False, "reason": "请填写不超过 500 字的处理原因"}
        event = self.storage.get_event(int(event_id))
        if event is None:
            return {"ready": False, "reason": "活动不存在"}
        now = self._now_utc()
        token = secrets.token_urlsafe(18)
        self._event_status_confirmations[token] = PendingEventStatusOverride(
            owner_id=str(actor_id),
            event_id=int(event_id),
            source_hash=event.raw_content_hash,
            status=normalized_status,
            reason=normalized_reason,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        current_override = self.storage.get_event_status_override(int(event_id))
        return {
            "ready": True,
            "token": token,
            "event_id": int(event_id),
            "title": event.title,
            "derived_status": derive_radar_status(
                event,
                now,
                getattr(self.config, "timezone", "Asia/Shanghai"),
                tuple(getattr(self.config, "radar_historical_keywords", ()) or ()),
            ),
            "current_override": current_override,
            "status_after": normalized_status,
            "reason": normalized_reason,
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }

    async def confirm_event_status_override(
        self, token: str, *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        pending = self._event_status_confirmations.get(str(token))
        if pending is None:
            return {"success": False, "reason": "确认 token 无效或已使用"}
        if pending.owner_id != str(actor_id):
            return {"success": False, "reason": "确认 token 不属于当前操作者"}
        self._event_status_confirmations.pop(str(token), None)
        now = self._now_utc()
        if now > pending.expires_at:
            return {"success": False, "reason": "确认 token 已过期"}
        event = self.storage.get_event(pending.event_id)
        if event is None or event.raw_content_hash != pending.source_hash:
            return {"success": False, "reason": "活动来源已变化，请重新预览"}
        try:
            self.storage.set_event_status_override(
                pending.event_id,
                pending.status,
                actor_id=str(actor_id),
                reason=pending.reason,
                at=now.isoformat(),
            )
        except ValueError as exc:
            return {"success": False, "reason": str(exc)}
        return {
            "success": True,
            "event_id": pending.event_id,
            "radar_status": self._radar_status(event),
            "status_override": self.storage.get_event_status_override(pending.event_id),
        }

    async def prepare_event_date_review(
        self,
        event_id: int,
        event_date_id: int,
        proposed_datetime: str,
        reason: str,
        *,
        actor_id: str,
        decision: str = "accepted",
        proposed_confirmed: bool | None = None,
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "ready": False,
                "error": "forbidden",
                "reason": "需要 operator/Admin 权限",
            }
        normalized_decision = str(decision or "accepted").strip().lower()
        if normalized_decision not in {"accepted", "rejected"}:
            return {"ready": False, "reason": "复核决定必须是 accepted 或 rejected"}
        if proposed_confirmed is not None and not isinstance(proposed_confirmed, bool):
            return {"ready": False, "reason": "日期确认状态必须是布尔值"}
        normalized_reason = str(reason or "").strip()
        if not normalized_reason or len(normalized_reason) > 500:
            return {"ready": False, "reason": "请填写不超过 500 字的复核原因"}
        event = self.storage.get_event(int(event_id))
        if event is None:
            return {"ready": False, "reason": "活动不存在"}
        event_date = next(
            (item for item in event.dates if item.id == int(event_date_id)), None
        )
        if event_date is None:
            return {"ready": False, "reason": "日期节点不属于该活动"}
        raw_value = str(proposed_datetime or "").strip()
        parsed = parse_datetime_value(raw_value, timezone_name=event_date.timezone)
        if parsed is None:
            return {"ready": False, "reason": "建议日期必须是有效 ISO 日期或时间"}
        supplied_precision = "minute" if ":" in raw_value else "date"
        if supplied_precision != event_date.precision:
            return {"ready": False, "reason": "建议日期精度必须与原证据一致"}
        parsed = parsed.astimezone(ZoneInfo(event_date.timezone))
        old_value = {
            "kind": event_date.kind,
            "datetime": event_date.datetime.isoformat()
            if event_date.datetime
            else None,
            "timezone": event_date.timezone,
            "label": event_date.label,
            "confirmed": event_date.confirmed,
            "precision": event_date.precision,
        }
        proposed_value = {
            "kind": event_date.kind,
            "datetime": parsed.isoformat(),
            "timezone": event_date.timezone,
            "label": event_date.label,
            "confirmed": (
                bool(proposed_confirmed)
                if normalized_decision == "accepted" and proposed_confirmed is not None
                else event_date.confirmed
            ),
        }
        evidence_hash = hashlib.sha256(
            "\0".join(
                (
                    event.raw_content_hash,
                    str(event_date.id),
                    event_date.kind,
                    event_date.evidence_text,
                )
            ).encode("utf-8")
        ).hexdigest()
        now = self._now_utc()
        token = secrets.token_urlsafe(18)
        self._event_date_review_confirmations[token] = PendingEventDateReview(
            owner_id=str(actor_id),
            event_id=int(event_id),
            event_date_id=int(event_date_id),
            source_hash=event.raw_content_hash,
            evidence_hash=evidence_hash,
            evidence_text=event_date.evidence_text,
            old_value=old_value,
            proposed_value=proposed_value,
            decision=normalized_decision,
            reason=normalized_reason,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        return {
            "ready": True,
            "token": token,
            "event_id": int(event_id),
            "event_date_id": int(event_date_id),
            "old_value": old_value,
            "proposed_value": proposed_value,
            "decision": normalized_decision,
            "reason": normalized_reason,
            "evidence_hash": evidence_hash,
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }

    async def confirm_event_date_review(
        self, token: str, *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        pending = self._event_date_review_confirmations.get(str(token))
        if pending is None:
            return {"success": False, "reason": "复核 token 无效或已使用"}
        if pending.owner_id != str(actor_id):
            return {"success": False, "reason": "复核 token 不属于当前操作者"}
        self._event_date_review_confirmations.pop(str(token), None)
        now = self._now_utc()
        if now > pending.expires_at:
            return {"success": False, "reason": "复核 token 已过期"}
        try:
            review_id = self.storage.record_event_date_review(
                event_id=pending.event_id,
                event_date_id=pending.event_date_id,
                source_hash=pending.source_hash,
                evidence_hash=pending.evidence_hash,
                evidence_text=pending.evidence_text,
                old_value=pending.old_value,
                proposed_value=pending.proposed_value,
                decision=pending.decision,
                actor_id=str(actor_id),
                reason=pending.reason,
                at=now.isoformat(),
            )
        except (TypeError, ValueError) as exc:
            return {"success": False, "reason": str(exc)}
        event = self.storage.get_event(pending.event_id)
        reviewed_date = (
            next(
                (item for item in event.dates if item.id == pending.event_date_id), None
            )
            if event
            else None
        )
        return {
            "success": True,
            "review_id": review_id,
            "decision": pending.decision,
            "confirmed": reviewed_date.confirmed if reviewed_date else False,
            "event_date": pending.proposed_value
            if pending.decision == "accepted"
            else pending.old_value,
        }

    async def list_event_date_reviews(
        self, *, actor_id: str, limit: int = 50
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        return {
            "success": True,
            "items": self.storage.list_event_date_reviews(limit=limit),
        }

    def _event_sort_key(self, event: LegalEvent) -> tuple[datetime, float, int]:
        deadlines = [
            item.datetime
            for item in event.dates
            if item.confirmed
            and item.datetime is not None
            and item.kind.endswith("deadline")
            and item.datetime >= self._now_utc()
        ]
        deadline = (
            min(deadlines) if deadlines else datetime.max.replace(tzinfo=timezone.utc)
        )
        published = event.source_published_at or event.updated_at
        return (deadline, -published.timestamp(), -(event.id or 0))

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
        target = self.storage.bind_target(unified_msg_origin, label)
        if target.get("enabled"):
            await self._wake_scheduler()
        return target

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
        unbound = self.storage.unbind_target(unified_msg_origin)
        if unbound:
            await self._wake_scheduler()
        return unbound

    async def prepare_unbind_target(
        self, selector: str, *, actor_id: str | None = None
    ) -> dict[str, Any]:
        needle = str(selector or "").strip()
        matches = [
            target
            for target in self.storage.list_targets(enabled_only=False)
            if needle == str(target["id"])
            or needle == target["unified_msg_origin"]
            or needle.casefold() == str(target["label"]).casefold()
        ]
        if len(matches) > 1:
            return {"ready": False, "reason": f"群名称有歧义：{needle}"}
        if not matches:
            return {"ready": False, "reason": f"未找到发布目标：{needle}"}
        token = secrets.token_urlsafe(12)
        now = self._now_utc()
        self._unbind_confirmations[token] = (
            int(matches[0]["id"]),
            now,
            now + timedelta(minutes=10),
            actor_id,
        )
        return {
            "ready": True,
            "token": token,
            "target": matches[0],
            "notice": "解绑会停止该群的后续自动任务，必须明确确认。",
        }

    async def confirm_unbind_target(
        self, token: str, *, actor_id: str | None = None
    ) -> dict[str, Any]:
        pending = self._unbind_confirmations.pop(str(token).strip(), None)
        if pending is None:
            return {"success": False, "reason": "确认 token 无效或已使用"}
        target_id, _, expires_at, owner_id = pending
        if owner_id and owner_id != str(actor_id or ""):
            self._unbind_confirmations[str(token).strip()] = pending
            return {"success": False, "reason": "确认 token 不属于当前操作者"}
        if self._now_utc() > expires_at:
            return {"success": False, "reason": "确认 token 已过期"}
        target = self.storage.get_target(target_id)
        if target is None:
            return {"success": False, "reason": "目标群不存在"}
        success = await self.unbind_target(target["unified_msg_origin"])
        return {"success": success, "target_id": target_id}

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
                "question_type_selection_mode": getattr(
                    self.config, "daily_question_type_selection_mode", "random"
                ),
                "fixed_question_type": getattr(
                    self.config,
                    "daily_question_fixed_type",
                    getattr(self.config, "daily_question_type", None),
                ),
                "rotation_question_types": tuple(
                    getattr(self.config, "daily_question_rotation_types", ())
                ),
                "question_type_rotation_start_date": getattr(
                    self.config, "daily_question_type_rotation_start_date", None
                ),
                "question_type_rotation_start_index": int(
                    getattr(self.config, "daily_question_type_rotation_start_index", 0)
                ),
            }
        if str(
            values.get("selection_mode", "random")
        ).strip().lower() == "rotation" and not values.get("rotation_start_date"):
            values["rotation_start_date"] = self.storage.get_rotation_anchor(
                content_type
            )
        if str(
            values.get("question_type_selection_mode", "random")
        ).strip().lower() == "rotation" and not values.get(
            "question_type_rotation_start_date"
        ):
            values["question_type_rotation_start_date"] = (
                self.storage.get_rotation_anchor("daily_question_type")
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
            if (
                content_type == "daily_question"
                and plan.enabled
                and plan.question_type_selection_mode == "rotation"
                and plan.rotation_question_types
                and plan.question_type_rotation_start_date is None
            ):
                self.storage.set_rotation_anchor("daily_question_type", local_today)

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
                        local_today, days, target_id=None
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
                            ).preview(local_today, days, target_id=target["id"]),
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
            if (
                item == "daily_question"
                and str(merged.get("question_type_selection_mode", "random"))
                .strip()
                .lower()
                == "rotation"
                and not merged.get("question_type_rotation_start_date")
            ):
                merged["question_type_rotation_start_date"] = local_today.isoformat()
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
                {
                    "plan": plan.to_mapping(),
                    "preview": plan.preview(local_today, 14, target_id=target_id),
                }
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
        await self._wake_scheduler()
        return {"success": True, "content_types": list(pending.content_types)}

    async def prepare_daily_plan_override_removal(
        self,
        *,
        target_selectors: list[str] | None,
        content_type: str = "both",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        targets = self._resolve_targets(target_selectors)
        if len(targets) != 1:
            return {"ready": False, "reason": "一次只能恢复一个群的全局计划"}
        content_types = (
            ("daily_case", "daily_question")
            if content_type == "both"
            else (content_type,)
        )
        if any(item not in {"daily_case", "daily_question"} for item in content_types):
            return {"ready": False, "reason": "content_type 参数无效"}
        local_today = _local_datetime(
            self._now_utc(), getattr(self.config, "timezone", "Asia/Shanghai")
        ).date()
        current_plans = []
        restored_plans = []
        global_plans: list[DailyPlan] = []
        for item in content_types:
            current_plan = self.effective_daily_plan(int(targets[0]["id"]), item)
            global_plan = self.effective_daily_plan(None, item)
            current_plans.append(
                {
                    "content_type": item,
                    "plan": current_plan.to_mapping(),
                    "preview": current_plan.preview(
                        local_today, 14, target_id=int(targets[0]["id"])
                    ),
                }
            )
            restored_plans.append(
                {
                    "content_type": item,
                    "plan": global_plan.to_mapping(),
                    "preview": global_plan.preview(
                        local_today, 14, target_id=int(targets[0]["id"])
                    ),
                }
            )
            global_plans.append(global_plan)
        token = secrets.token_urlsafe(12)
        now = self._now_utc()
        self._plan_reset_confirmations[token] = PendingPlanReset(
            target_id=int(targets[0]["id"]),
            content_types=content_types,
            global_plans=tuple(global_plans),
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            owner_id=actor_id,
        )
        return {
            "ready": True,
            "token": token,
            "target": targets[0],
            "content_types": list(content_types),
            "current_plans": current_plans,
            "restored_plans": restored_plans,
            "notice": "恢复全局计划只会在明确确认后执行。",
        }

    async def confirm_daily_plan_override_removal(
        self, token: str, *, actor_id: str | None = None
    ) -> dict[str, Any]:
        pending = self._plan_reset_confirmations.pop(str(token).strip(), None)
        if pending is None:
            return {"success": False, "reason": "确认 token 无效或已使用"}
        if pending.owner_id and pending.owner_id != str(actor_id or ""):
            self._plan_reset_confirmations[str(token).strip()] = pending
            return {"success": False, "reason": "确认 token 不属于当前操作者"}
        if self._now_utc() > pending.expires_at:
            return {"success": False, "reason": "确认 token 已过期"}
        target = self.storage.get_target(pending.target_id)
        if target is None:
            return {"success": False, "reason": "目标群不存在"}
        for content_type, expected_plan in zip(
            pending.content_types, pending.global_plans, strict=True
        ):
            current_global = self.effective_daily_plan(None, content_type)
            if current_global.to_mapping() != expected_plan.to_mapping():
                return {
                    "success": False,
                    "reason": "全局计划在准备后已变化，请重新预览",
                }
        for content_type in pending.content_types:
            self.storage.delete_daily_plan(pending.target_id, content_type)
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
            event_revision=event.revision,
            event_content_hash=event.raw_content_hash,
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
            current_event = self.storage.get_event(claimed.event_id)
            if current_event is None:
                return {"success": False, "reason": "活动已不存在，发布预览失效"}
            if (
                current_event.revision != claimed.event_revision
                or current_event.raw_content_hash != claimed.event_content_hash
            ):
                return {
                    "success": False,
                    "reason": "活动 revision 或内容已更新，原发布预览失效，请重新预览",
                }
            count = await self._publish_event_to_targets(
                claimed.event_id,
                kind="manual",
                target_ids=claimed.target_ids,
                fixed_body=claimed.body,
            )
        else:
            if (
                claimed.content_type == "question"
                and claimed.question_snapshot is not None
            ):
                outcomes = await self._publish_question_to_targets(
                    claimed,
                    actor_id=str(actor_id or ""),
                )
                count = sum(status == "sent" for status in outcomes)
                return {
                    "success": count > 0,
                    "published_count": count,
                    "failed_count": sum(status != "sent" for status in outcomes),
                }
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
        snapshot = self._build_question_snapshot(content)
        body = self._snapshot_first_prompt(snapshot)
        token = secrets.token_urlsafe(12)
        created = self._now_utc()
        self._publish_confirmations[token] = PendingPublication(
            content_type="question",
            body=body,
            target_ids=tuple(target["id"] for target in targets),
            created_at=created,
            expires_at=created + timedelta(minutes=10),
            owner_id=actor_id,
            question_snapshot=snapshot,
            question_identity=str(snapshot.get("identity") or ""),
            source_kind=(
                "library_question"
                if isinstance(content.get("item"), dict)
                else (
                    "real_question"
                    if content.get("origin") == "real"
                    else "generated_question"
                )
            ),
            source_item_key=str(
                content.get("question_id")
                or (content.get("item") or {}).get("id")
                or snapshot.get("snapshot_hash")
            ),
            library_item_id=(content.get("item") or {}).get("id"),
            real_question_id=(
                content.get("question_id") if content.get("origin") == "real" else None
            ),
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

    def _build_question_snapshot(self, content: dict[str, Any]) -> dict[str, Any]:
        limit = getattr(self.config, "question_message_max_chars", 1600)
        try:
            limit = max(300, min(4000, int(limit)))
        except (TypeError, ValueError):
            limit = 1600
        return build_question_session_snapshot(content, max_chars=limit)

    @staticmethod
    def _snapshot_first_prompt(snapshot: dict[str, Any]) -> str:
        if snapshot.get("materials"):
            return format_session_prompt(snapshot, material_index=0, material_page=0)
        return format_session_prompt(snapshot, prompt_index=0, prompt_page=0)

    async def _publish_question_to_targets(
        self, pending: PendingPublication, *, actor_id: str
    ) -> list[str]:
        if self.publisher is None or pending.question_snapshot is None:
            return []
        targets = {
            int(target["id"]): target
            for target in self.storage.list_targets(enabled_only=True)
        }
        outcomes: list[str] = []
        for target_id in pending.target_ids:
            target = targets.get(target_id)
            if target is None:
                outcomes.append("failed")
                continue
            try:
                outcome = await self.publisher.publish_text(
                    target["unified_msg_origin"], pending.body
                )
                status, error = _delivery_status(outcome)
            except Exception as exc:  # noqa: BLE001 - isolate target transport failure.
                status, error = "failed", str(exc)
            outcomes.append(status)
            if status == "sent":
                self._persist_question_snapshot(
                    pending.question_snapshot,
                    session_origin=target["unified_msg_origin"],
                    target_id=target_id,
                    actor_id=actor_id,
                    source_kind=pending.source_kind or "generated_question",
                    source_item_key=pending.source_item_key or "unknown",
                    question_identity=pending.question_identity or "mock_question",
                    library_item_id=pending.library_item_id,
                    real_question_id=pending.real_question_id,
                )
            else:
                self.logger.warning(
                    "Law Assistant failed to publish question to %s: %s",
                    target["unified_msg_origin"],
                    error,
                )
        return outcomes

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
        if kind == "automatic":
            policy = radar_policy_for(event.source_key, self.config)
            if (
                not policy.auto_publish_enabled
                or self._radar_status(event) != "current"
            ):
                return 0
            if not any(
                date.confirmed
                and date.datetime is not None
                and date.kind in {"registration_deadline", "submission_deadline"}
                for date in event.dates
            ):
                return 0
        count = 0
        allowed = set(target_ids) if target_ids is not None else None
        for target in self.storage.list_targets(enabled_only=True):
            if allowed is not None and target["id"] not in allowed:
                continue
            canonical_key = canonical_event_key(event)
            if (
                kind == "automatic"
                and canonical_key
                and self.storage.has_canonical_publication(canonical_key, target["id"])
            ):
                continue
            publication_id = self.storage.claim_publication(
                event.id, event.revision, target["id"], kind
            )
            if publication_id is None:
                continue
            outcome = await self.publisher.publish_text(
                target["unified_msg_origin"], fixed_body or format_event(event)
            )
            status, error = _delivery_status(outcome)
            self.storage.finish_publication(
                publication_id, status=status, error_summary=error
            )
            if status == "sent":
                count += 1
                if kind == "automatic" and canonical_key:
                    self.storage.record_canonical_publication(
                        canonical_key, target["id"]
                    )
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
            outcome = await self.publisher.publish_text(
                target["unified_msg_origin"], body
            )
            status, _ = _delivery_status(outcome)
            if status == "sent":
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
                outcome = await self.publisher.publish_text(
                    target["unified_msg_origin"],
                    format_deadline_reminder(event, date, remaining),
                )
                status, error = _delivery_status(outcome)
                self.storage.finish_reminder(
                    reminder_id, status=status, error_summary=error
                )
                sent += int(status == "sent")
                if status == "sent":
                    self.logger.info(
                        "Law Assistant sent deadline reminder for event %s", event.id
                    )
        return sent

    async def get_daily_case(
        self,
        *,
        date: str | None = None,
        subject: str | None = None,
        session_origin: str | None = None,
        actor_id: str | None = None,
        used_content_keys: set[tuple[str, str]] | None = None,
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
                date=date,
                subject=subject,
                session_origin=session_origin,
                used_content_keys=used_content_keys,
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

    async def stage_learning_upload(self, filename: str, data: bytes) -> dict[str, Any]:
        if self.document_ingestion is None:
            return {"success": False, "error": "document_ingestion_unavailable"}
        try:
            staged = await self.document_ingestion.stage_upload(filename, data)
        except (OSError, ValueError) as exc:
            return {
                "success": False,
                "error": _document_error_code(str(exc)),
                "message": str(exc),
            }
        return {"success": True, **staged}

    async def prepare_learning_import(
        self,
        staged_path: str,
        *,
        content_kind: str,
        created_by: str,
        session_origin: str,
        owner_id: str | None = None,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        if self.document_ingestion is None:
            return {"success": False, "error": "document_ingestion_unavailable"}
        try:
            prepared = await self.document_ingestion.prepare_import(
                staged_path,
                created_by=created_by,
                session_origin=session_origin,
                content_kind=content_kind,
                original_filename=original_filename,
            )
        except (DocumentParseError, OSError, ValueError) as exc:
            return {
                "success": False,
                "error": getattr(exc, "code", "invalid_import"),
                "message": str(exc),
            }
        token = secrets.token_urlsafe(12)
        now = self._now_utc()
        self._import_confirmations[token] = PendingImport(
            token=token,
            prepared=prepared,
            staged_path=str(staged_path),
            file_hash=prepared.parsed.file_hash,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            owner_id=owner_id,
        )
        candidates = list(prepared.candidates)
        counts = {
            "total_candidates": len(candidates),
            "archivable": sum(
                1
                for item in candidates
                if str(item.get("status") or "").lower()
                not in {"needs_review", "review"}
            ),
            "needs_review": sum(
                1
                for item in candidates
                if str(item.get("status") or "").lower() in {"needs_review", "review"}
            ),
        }
        preview_items = [
            {
                "index": index,
                "status": item.get("status", "ready"),
                "material_type": item.get("material_type", content_kind),
                "title": item.get("title", ""),
                "locator": item.get("locator", ""),
                "review_reason": item.get("review_reason", ""),
            }
            for index, item in enumerate(candidates[:10])
        ]
        return {
            "success": True,
            "token": token,
            "file_hash": prepared.parsed.file_hash,
            "original_filename": prepared.original_filename,
            "content_kind": content_kind,
            "preview": {**counts, "items": preview_items},
            "warnings": list(prepared.warnings),
        }

    async def confirm_learning_import(
        self, token: str, *, owner_id: str | None = None
    ) -> dict[str, Any]:
        key = str(token or "").strip()
        pending = self._import_confirmations.pop(key, None)
        if pending is None:
            return {
                "success": False,
                "error": "invalid_token",
                "message": "导入 token 无效或已使用",
            }
        if pending.owner_id and pending.owner_id != owner_id:
            self._import_confirmations[key] = pending
            return {
                "success": False,
                "error": "forbidden",
                "message": "导入 token 不属于当前会话",
            }
        if self._now_utc() > pending.expires_at:
            return {
                "success": False,
                "error": "expired_token",
                "message": "导入 token 已过期",
            }
        try:
            current_hash = await self.document_ingestion.staged_file_hash(
                pending.staged_path
            )
            if current_hash != pending.file_hash:
                return {
                    "success": False,
                    "error": "staged_file_changed",
                    "message": "staged 文件内容已变化，请重新 prepare",
                }
            result = await self.document_ingestion.archive_prepared(pending.prepared)
        except (DocumentParseError, OSError, ValueError) as exc:
            return {
                "success": False,
                "error": getattr(exc, "code", "invalid_import"),
                "message": str(exc),
            }
        return {"success": True, **result}

    async def stage_structured_upload(
        self, filename: str, data: bytes
    ) -> dict[str, Any]:
        if self.structured_ingestion is None:
            return {"success": False, "error": "structured_ingestion_unavailable"}
        try:
            staged = await self.structured_ingestion.stage_json_upload(filename, data)
        except (OSError, ValueError) as exc:
            return {
                "success": False,
                "error": "invalid_structured_upload",
                "message": str(exc),
            }
        return {"success": True, **staged}

    async def prepare_structured_learning_import(
        self,
        original_staged_path: str,
        structured_staged_path: str,
        *,
        created_by: str,
        session_origin: str,
        owner_id: str | None = None,
        original_filename: str | None = None,
        structured_filename: str | None = None,
    ) -> dict[str, Any]:
        if self.structured_ingestion is None:
            return {"success": False, "error": "structured_ingestion_unavailable"}
        try:
            prepared = await self.structured_ingestion.prepare(
                original_staged_path,
                structured_staged_path,
                created_by=created_by,
                session_origin=session_origin,
                original_filename=original_filename,
                structured_filename=structured_filename,
            )
        except StructuredImportError as exc:
            return {"success": False, "error": exc.code, "message": str(exc)}
        except (OSError, ValueError) as exc:
            return {
                "success": False,
                "error": "invalid_structured_import",
                "message": str(exc),
            }
        token = secrets.token_urlsafe(16)
        now = self._now_utc()
        self._structured_import_confirmations[token] = PendingStructuredImport(
            token=token,
            prepared=prepared,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            owner_id=owner_id,
        )
        return {
            "success": True,
            "token": token,
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "preview": copy.deepcopy(prepared.preview),
        }

    async def confirm_structured_learning_import(
        self, token: str, *, owner_id: str | None = None
    ) -> dict[str, Any]:
        key = str(token or "").strip()
        pending = self._structured_import_confirmations.pop(key, None)
        if pending is None:
            return {
                "success": False,
                "error": "invalid_token",
                "message": "结构化导入 token 无效或已使用",
            }
        if pending.owner_id and pending.owner_id != owner_id:
            self._structured_import_confirmations[key] = pending
            return {
                "success": False,
                "error": "forbidden",
                "message": "结构化导入 token 不属于当前会话",
            }
        if self._now_utc() > pending.expires_at:
            return {
                "success": False,
                "error": "expired_token",
                "message": "结构化导入 token 已过期",
            }
        try:
            result = await self.structured_ingestion.confirm(pending.prepared)
        except StructuredImportError as exc:
            return {"success": False, "error": exc.code, "message": str(exc)}
        except (OSError, ValueError) as exc:
            return {
                "success": False,
                "error": "invalid_structured_import",
                "message": str(exc),
            }
        return result

    async def search_learning_library(self, **kwargs: Any) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.search_learning_library(**kwargs)

    async def search_management_learning_library(
        self, *, actor_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        kwargs["include_inactive"] = bool(kwargs.get("include_inactive", False))
        return await self.library_service.search_learning_library(**kwargs)

    async def list_management_real_questions(
        self, *, actor_id: str, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        try:
            normalized_page, normalized_size, offset = page_request(page, page_size)
        except ValueError as exc:
            return {
                "success": False,
                "error": "invalid_pagination",
                "message": str(exc),
            }
        return {
            "success": True,
            **page_payload(
                self.storage.list_managed_real_questions(
                    limit=normalized_size, offset=offset
                ),
                page=normalized_page,
                page_size=normalized_size,
                total=self.storage.count_managed_real_questions(),
            ),
        }

    async def get_management_real_question(
        self, question_id: int, *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        question = self.storage.get_managed_real_question(int(question_id))
        if question is None:
            return {"success": False, "error": "not_found", "message": "未找到核验真题"}
        return {"success": True, "question": question}

    async def update_management_real_question(
        self, question_id: int, changes: dict[str, Any], *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        try:
            question = self.storage.update_independent_real_question(
                int(question_id), changes
            )
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "error": "invalid_real_question",
                "message": str(exc),
            }
        self._audit_operator_action(
            actor_id, "real_question_updated", [int(question_id)], scope="real_question"
        )
        return {"success": True, "question": question}

    async def set_management_real_question_active(
        self, question_id: int, *, active: bool, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        try:
            question = self.storage.set_independent_real_question_active(
                int(question_id), active=bool(active)
            )
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "error": "invalid_real_question",
                "message": str(exc),
            }
        action = "real_question_restored" if active else "real_question_disabled"
        self._audit_operator_action(
            actor_id, action, [int(question_id)], scope="real_question"
        )
        return {"success": True, "question": question}

    async def get_learning_item(self, item_id: int) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.get_learning_item(item_id)

    async def get_learning_item_for_session(self, item_id: int) -> dict[str, Any]:
        """Internal full-detail path used only when preparing a gated session."""
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.get_learning_item_for_session(item_id)

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

    def _management_actor_allowed(self, actor_id: str | None) -> bool:
        actor = str(actor_id or "").strip()
        if actor == "webui":
            return True
        configured = (
            self.config.get("operator_ids", [])
            if isinstance(self.config, dict)
            else getattr(self.config, "operator_ids", [])
        )
        return actor != "" and actor in {str(value) for value in (configured or [])}

    async def get_management_learning_item(
        self, item_id: int, *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.get_management_item(item_id)

    async def prepare_library_batch(
        self,
        action: str,
        item_ids: list[int],
        *,
        actor_id: str,
        changes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "ready": False,
                "error": "forbidden",
                "reason": "需要 operator/Admin 权限",
            }
        if self.library_service is None:
            return {"ready": False, "reason": "学习资料库服务不可用"}
        normalized_action = str(action or "").strip().lower()
        ids = list(dict.fromkeys(int(value) for value in item_ids))
        if not ids or len(ids) > 100:
            return {"ready": False, "reason": "请选择 1 至 100 条资料"}
        if normalized_action == "promote":
            promotion = await self.prepare_candidate_promotion(ids, actor_id=actor_id)
            if not promotion.get("ready"):
                return promotion
            promotion_token = str(promotion["token"])
            preview_items = tuple(
                {
                    key: item.get(key)
                    for key in (
                        "item_id",
                        "eligible",
                        "already_linked",
                        "will_upsert",
                        "real_question_id",
                        "reason",
                        "normalized",
                    )
                }
                for item in promotion.get("items", [])
            )
            promotion_summary = {
                key: promotion.get(key, 0)
                for key in (
                    "selected_count",
                    "promotable_count",
                    "already_linked_count",
                    "unresolved_answer_count",
                    "upsert_count",
                )
            }
            promotion_summary["validation_failures"] = promotion.get(
                "validation_failures", []
            )
            changes_value: dict[str, Any] = {}
        elif normalized_action in {"edit", "delete", "restore", "verify_case"}:
            changes_value = (
                dict(changes or {})
                if normalized_action in {"edit", "verify_case"}
                else {}
            )
            if normalized_action == "edit" and not changes_value:
                return {"ready": False, "reason": "编辑操作需要至少一个字段"}
            if normalized_action == "verify_case" and (
                set(changes_value) != {"verification_status"}
                or changes_value.get("verification_status")
                not in {"unverified", "user_verified"}
            ):
                return {
                    "ready": False,
                    "reason": "用户案例状态必须选择 unverified 或 user_verified",
                }
            expected: dict[int, dict[str, Any]] = {}
            preview_list: list[dict[str, Any]] = []
            normalized_changes: list[dict[str, Any]] = []
            item_types: set[str] = set()
            for item_id in ids:
                bundle = self.library_service.repository.get(item_id)
                if bundle is None:
                    return {"ready": False, "reason": f"未找到资料 {item_id}"}
                item = bundle.item
                if normalized_action == "verify_case" and (
                    item.item_type != "case" or item.identity != "user_case"
                ):
                    return {
                        "ready": False,
                        "reason": "批量核验只支持 user_case，不能手动设置 official_case",
                    }
                if normalized_action == "verify_case" and not item.active:
                    return {"ready": False, "reason": f"用户案例 {item_id} 已停用"}
                if normalized_action == "delete" and not item.active:
                    return {"ready": False, "reason": f"资料 {item_id} 已停用"}
                if normalized_action == "restore" and item.active:
                    return {"ready": False, "reason": f"资料 {item_id} 已启用"}
                item_types.add(item.item_type)
                if normalized_action == "edit":
                    try:
                        normalized_changes.append(
                            self.library_service.normalize_management_changes(
                                item_id, changes_value
                            )
                        )
                    except (TypeError, ValueError) as exc:
                        return {"ready": False, "reason": f"资料 {item_id}：{exc}"}
                expected[item_id] = {
                    "item_hash": item.item_hash,
                    "updated_at": item.updated_at.isoformat(),
                    "active": int(item.active),
                    "identity": item.identity,
                    "item_type": item.item_type,
                }
                if normalized_action == "verify_case":
                    expected[item_id]["verification_status"] = item.verification_status
                preview_list.append(
                    {
                        "id": item_id,
                        "title": item.title,
                        "identity": item.identity,
                        "active": item.active,
                        **(
                            {
                                "verification_before": item.verification_status,
                                "verification_after": changes_value[
                                    "verification_status"
                                ],
                            }
                            if normalized_action == "verify_case"
                            else {}
                        ),
                    }
                )
            if normalized_action == "edit":
                if len(item_types) != 1:
                    return {"ready": False, "reason": "批量编辑要求所选资料类型一致"}
                if any(
                    item != normalized_changes[0] for item in normalized_changes[1:]
                ):
                    return {
                        "ready": False,
                        "reason": "所选资料无法使用同一规范化编辑内容",
                    }
                changes_value = normalized_changes[0]
            preview_items = tuple(preview_list)
            promotion_token = None
        else:
            return {"ready": False, "reason": "不支持的批量操作"}

        token = secrets.token_urlsafe(18)
        now = self._now_utc()
        self._management_batch_confirmations[token] = PendingManagementBatch(
            owner_id=str(actor_id),
            action=normalized_action,
            item_ids=tuple(ids),
            changes=changes_value,
            expected=locals().get("expected", {}),
            preview=preview_items,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            promotion_token=locals().get("promotion_token"),
        )
        return {
            "ready": True,
            "token": token,
            "action": normalized_action,
            "count": len(ids),
            "items": list(preview_items),
            "changes": changes_value,
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            **locals().get("promotion_summary", {}),
        }

    async def prepare_moderation_batch(
        self,
        domain: str,
        item_ids: list[int],
        changes: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        """Prepare an exact-ID Review or Radar status batch for an operator."""
        if not self._management_actor_allowed(actor_id):
            return {
                "ready": False,
                "error": "forbidden",
                "reason": "需要 operator/Admin 权限",
            }
        normalized_domain = str(domain or "").strip().lower()
        if normalized_domain not in {"review", "radar"}:
            return {"ready": False, "reason": "不支持的批量管理列表"}
        if not isinstance(changes, dict) or set(changes) - {"status", "reason"}:
            return {"ready": False, "reason": "批量状态参数无效"}
        status = str(changes.get("status") or "").strip().lower()
        allowed_statuses = (
            {"pending", "resolved", "superseded"}
            if normalized_domain == "review"
            else {"current", "needs_review", "historical", "ignored", "auto"}
        )
        if status not in allowed_statuses:
            return {"ready": False, "reason": "不支持的批量状态"}
        reason = str(changes.get("reason") or "").strip()
        if (
            normalized_domain == "radar"
            and status != "auto"
            and (not reason or len(reason) > 500)
        ):
            return {
                "ready": False,
                "reason": "人工状态覆盖必须填写不超过 500 字的原因",
            }
        try:
            ids = list(dict.fromkeys(int(value) for value in item_ids))
        except (TypeError, ValueError):
            return {"ready": False, "reason": "所选 ID 必须是整数"}
        if not ids or len(ids) > 100:
            return {"ready": False, "reason": "请选择 1 至 100 条记录"}
        expected: dict[int, dict[str, Any]] = {}
        preview: list[dict[str, Any]] = []
        now = self._now_utc()
        if normalized_domain == "review":
            if self.library_service is None:
                return {"ready": False, "reason": "学习资料库服务不可用"}
            for review_id in ids:
                item = self.library_service.repository.get_review_item(review_id)
                if item is None:
                    return {"ready": False, "reason": f"复核记录 {review_id} 不存在"}
                expected[review_id] = {
                    "source_id": item.source_id,
                    "candidate_key": item.candidate_key,
                    "status": item.status,
                    "updated_at": item.updated_at.isoformat(),
                }
                preview.append(
                    {
                        "id": review_id,
                        "material_type": item.material_type,
                        "status_before": item.status,
                        "status_after": status,
                        "review_reason": item.review_reason,
                    }
                )
        else:
            for event_id in ids:
                event = self.storage.get_event(event_id)
                if event is None:
                    return {"ready": False, "reason": f"活动 {event_id} 不存在"}
                override = self.storage.get_event_status_override(event_id)
                derived_status = derive_radar_status(
                    event,
                    now,
                    getattr(self.config, "timezone", "Asia/Shanghai"),
                    tuple(getattr(self.config, "radar_historical_keywords", ()) or ()),
                )
                expected[event_id] = {
                    "source_hash": event.raw_content_hash,
                    "override": override,
                    "derived_status": derived_status,
                }
                preview.append(
                    {
                        "id": event_id,
                        "title": event.title,
                        "status_before": override["override_status"]
                        if override
                        else derived_status,
                        "status_after": derived_status if status == "auto" else status,
                        "action": "clear_override"
                        if status == "auto"
                        else "set_override",
                    }
                )
        token = secrets.token_urlsafe(18)
        pending = PendingModerationBatch(
            owner_id=str(actor_id),
            domain=normalized_domain,
            status=status,
            reason=reason,
            item_ids=tuple(ids),
            expected=expected,
            preview=tuple(preview),
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        self._moderation_batch_confirmations[token] = pending
        return {
            "ready": True,
            "token": token,
            "domain": normalized_domain,
            "status": status,
            "reason": reason,
            "count": len(ids),
            "item_ids": list(ids),
            "items": list(pending.preview),
            "expires_at": pending.expires_at.isoformat(),
        }

    async def confirm_moderation_batch(
        self, token: str, *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        pending = self._moderation_batch_confirmations.get(str(token))
        if pending is None:
            return {"success": False, "reason": "批量确认 token 无效或已使用"}
        if pending.owner_id != str(actor_id):
            return {"success": False, "reason": "批量确认 token 不属于当前操作者"}
        self._moderation_batch_confirmations.pop(str(token), None)
        now = self._now_utc()
        if now > pending.expires_at:
            return {"success": False, "reason": "批量确认 token 已过期"}
        try:
            if pending.domain == "review":
                changed = self.library_service.repository.apply_review_status_batch(
                    list(pending.item_ids),
                    pending.expected,
                    pending.status,
                    actor_id=str(actor_id),
                    at=now.isoformat(),
                )
            else:
                for event_id in pending.item_ids:
                    event = self.storage.get_event(event_id)
                    if event is None:
                        raise ValueError("活动已删除，请重新预览")
                    current_derived = derive_radar_status(
                        event,
                        now,
                        getattr(self.config, "timezone", "Asia/Shanghai"),
                        tuple(
                            getattr(self.config, "radar_historical_keywords", ()) or ()
                        ),
                    )
                    if current_derived != pending.expected[event_id]["derived_status"]:
                        raise ValueError("活动自动状态已变化，请重新预览")
                changed = self.storage.apply_event_status_batch(
                    list(pending.item_ids),
                    pending.expected,
                    pending.status,
                    actor_id=str(actor_id),
                    reason=pending.reason,
                    at=now.isoformat(),
                )
        except (TypeError, ValueError, sqlite3.Error, RuntimeError) as exc:
            return {"success": False, "reason": str(exc), "changed_ids": []}
        return {
            "success": True,
            "domain": pending.domain,
            "status": pending.status,
            "changed_ids": changed,
            "count": len(changed),
        }

    async def confirm_library_batch(
        self, token: str, *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        pending = self._management_batch_confirmations.get(str(token))
        if pending is None:
            return {"success": False, "reason": "批量确认 token 无效或已使用"}
        if pending.owner_id != str(actor_id):
            return {"success": False, "reason": "批量确认 token 不属于当前操作者"}
        self._management_batch_confirmations.pop(str(token), None)
        if self._now_utc() > pending.expires_at:
            return {"success": False, "reason": "批量确认 token 已过期"}
        if pending.action == "promote":
            return await self.confirm_candidate_promotion(
                str(pending.promotion_token), actor_id=actor_id
            )
        try:
            changed = self.library_service.repository.apply_management_batch(
                action=pending.action,
                item_ids=list(pending.item_ids),
                expected=pending.expected,
                actor_id=str(actor_id),
                at=self._now_utc().isoformat(),
                changes=pending.changes,
            )
        except (TypeError, ValueError, sqlite3.Error) as exc:
            return {"success": False, "reason": str(exc), "changed_ids": []}
        if self._scheduler_wakeup is not None and pending.action in {
            "delete",
            "restore",
        }:
            await self._wake_scheduler()
        return {
            "success": True,
            "action": pending.action,
            "changed_ids": changed,
            "count": len(changed),
        }

    async def prepare_data_clear(self, scope: str, *, actor_id: str) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "ready": False,
                "error": "forbidden",
                "reason": "需要 operator/Admin 权限",
            }
        try:
            preview = self.storage.prepare_data_clear(str(scope))
        except (TypeError, ValueError) as exc:
            return {"ready": False, "error": "invalid_scope", "reason": str(exc)}
        token = secrets.token_urlsafe(18)
        now = self._now_utc()
        self._data_clear_confirmations[token] = PendingDataClear(
            owner_id=str(actor_id),
            scope=str(scope),
            snapshot=preview["snapshot"],
            preview={key: value for key, value in preview.items() if key != "snapshot"},
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        return {
            "ready": True,
            "token": token,
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            **self._data_clear_confirmations[token].preview,
        }

    async def confirm_data_clear(
        self, token: str, *, actor_id: str, typed_confirmation: str | None = None
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        pending = self._data_clear_confirmations.get(str(token))
        if pending is None:
            return {"success": False, "reason": "清理 token 无效或已使用"}
        if pending.owner_id != str(actor_id):
            return {"success": False, "reason": "清理 token 不属于当前操作者"}
        self._data_clear_confirmations.pop(str(token), None)
        if self._now_utc() > pending.expires_at:
            return {"success": False, "reason": "清理 token 已过期"}
        try:
            result = self.storage.confirm_data_clear(
                pending.scope,
                pending.snapshot,
                typed_confirmation=typed_confirmation,
            )
        except (TypeError, ValueError, sqlite3.Error, RuntimeError) as exc:
            return {"success": False, "reason": str(exc)}
        await self._wake_scheduler()
        return result

    async def update_management_learning_item(
        self, item_id: int, changes: dict[str, Any], *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        result = await self.library_service.update_management_item(item_id, changes)
        if result.get("success"):
            with self.storage.connection:
                self.storage.connection.execute(
                    "INSERT INTO operator_action_audits(actor_id, action, scope, "
                    "target_ids_json, outcome_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(actor_id),
                        "learning_item_updated",
                        "learning_item",
                        json.dumps([int(item_id)]),
                        "{}",
                        self._now_utc().isoformat(),
                    ),
                )
        return result

    async def soft_delete_learning_items(
        self, item_ids: list[int], *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        try:
            changed = self.library_service.repository.soft_delete_items(
                item_ids, actor_id=str(actor_id), at=self._now_utc().isoformat()
            )
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": "invalid_item_ids", "message": str(exc)}
        self._audit_operator_action(actor_id, "learning_items_soft_deleted", changed)
        return {"success": True, "changed_ids": changed, "count": len(changed)}

    async def restore_learning_items(
        self, item_ids: list[int], *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        try:
            changed = self.library_service.repository.restore_items(
                item_ids, actor_id=str(actor_id), at=self._now_utc().isoformat()
            )
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": "invalid_item_ids", "message": str(exc)}
        self._audit_operator_action(actor_id, "learning_items_restored", changed)
        return {"success": True, "changed_ids": changed, "count": len(changed)}

    def _audit_operator_action(
        self,
        actor_id: str,
        action: str,
        ids: list[int],
        *,
        scope: str = "learning_item",
    ) -> None:
        with self.storage.connection:
            self.storage.connection.execute(
                "INSERT INTO operator_action_audits(actor_id, action, scope, "
                "target_ids_json, outcome_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(actor_id),
                    action,
                    str(scope),
                    json.dumps(ids),
                    json.dumps({"count": len(ids)}),
                    self._now_utc().isoformat(),
                ),
            )

    @staticmethod
    def _candidate_promotion_question(
        bundle: Any, item_id: int, override: dict[str, Any]
    ) -> RealQuestion:
        """Rebuild the canonical promotion candidate from persisted normalized data."""
        source = bundle.sources[0] if bundle.sources else None
        source_link = bundle.source_links[0] if bundle.source_links else None
        question = bundle.question
        answer_source = question.answer_source or (
            "not_provided" if question.answer is None else "unverified"
        )
        if question.answer is not None and answer_source not in {
            "official",
            "third_party",
            "user_verified",
            "unverified",
        }:
            answer_source = "unverified"
        mapping = {
            "source_name": override.get("source_name")
            or (source.title if source else ""),
            "exam_name": override.get("exam_name") or question.exam_name,
            "subject": override.get("subject")
            or (bundle.item.subjects[0] if bundle.item.subjects else ""),
            "question_type": override.get("question_type") or question.question_type,
            "stem": override.get("stem") or question.stem,
            "options": override.get("options", list(question.options)),
            "answer": override.get("answer", question.answer),
            "explanation": override.get("explanation", question.explanation),
            "answer_source": override.get("answer_source") or answer_source,
            "verification_status": "user_verified",
            "source_url": override.get("source_url")
            or (source.source_url if source else ""),
            "source_locator": override.get("source_locator")
            or (source_link.locator if source_link else ""),
            "exam_year": override.get("exam_year") or question.exam_year,
            "exam_date": override.get("exam_date") or question.exam_date,
            "paper": override.get("paper") or question.paper,
            "question_number": override.get("question_number")
            or question.question_number,
            "metadata": {
                "learning_item_id": item_id,
                "source_content_hash": source.content_hash if source else "",
            },
        }
        if mapping["answer"] is None:
            mapping["answer_source"] = "not_provided"
        return RealQuestion.from_mapping(mapping)

    @staticmethod
    def _promotion_truth_fingerprint(bundle: Any, question: RealQuestion) -> str:
        source = bundle.sources[0] if bundle.sources else None
        source_link = bundle.source_links[0] if bundle.source_links else None
        normalized = bundle.question
        canonical = json.dumps(
            {
                "promotion": question.to_mapping(),
                "candidate": {
                    "subjects": list(bundle.item.subjects),
                    "question_type": normalized.question_type,
                    "stem": normalized.stem,
                    "options": list(normalized.options),
                    "answer": normalized.answer,
                    "explanation": normalized.explanation,
                    "answer_source": normalized.answer_source,
                    "exam_name": normalized.exam_name,
                    "exam_year": normalized.exam_year,
                    "exam_date": normalized.exam_date,
                    "paper": normalized.paper,
                    "question_number": normalized.question_number,
                    "source_content_hash": source.content_hash if source else "",
                    "source_title": source.title if source else "",
                    "source_url": source.source_url if source else "",
                    "source_locator": source_link.locator if source_link else "",
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def prepare_candidate_promotion(
        self,
        item_ids: list[int],
        *,
        actor_id: str,
        overrides_by_id: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "ready": False,
                "error": "forbidden",
                "reason": "需要 operator/Admin 权限",
            }
        if self.library_service is None:
            return {"ready": False, "reason": "学习资料库服务不可用"}
        normalized_ids = list(dict.fromkeys(int(value) for value in item_ids))
        if not normalized_ids or len(normalized_ids) > 100:
            return {"ready": False, "reason": "请选择 1 至 100 个候选题"}
        overrides_by_id = overrides_by_id or {}
        preview_items: list[dict[str, Any]] = []
        pending_items: list[dict[str, Any]] = []
        prevalidation_failures: list[dict[str, Any]] = []
        for item_id in normalized_ids:
            bundle = self.library_service.repository.get(item_id)
            if (
                bundle is None
                or bundle.item.identity != "real_question_candidate"
                or bundle.question is None
            ):
                failure = {
                    "item_id": item_id,
                    "success": False,
                    "reason": "不是有效的真题候选",
                }
                preview_items.append({**failure, "eligible": False})
                prevalidation_failures.append(failure)
                continue
            override = overrides_by_id.get(str(item_id), {})
            override = override if isinstance(override, dict) else {}
            try:
                real_question = self._candidate_promotion_question(
                    bundle, item_id, override
                )
                identity_key = real_question_identity_key(real_question)
                existing = self.storage.connection.execute(
                    "SELECT id FROM real_questions WHERE identity_key = ?",
                    (identity_key,),
                ).fetchone()
                item_preview = {
                    "item_id": item_id,
                    "eligible": True,
                    "already_linked": bool(
                        bundle.structured
                        and bundle.structured.get("verified_real_question_id")
                    ),
                    "will_upsert": existing is not None,
                    "real_question_id": int(existing["id"]) if existing else None,
                    "normalized": real_question.to_mapping(),
                }
                pending_items.append(
                    {
                        "item_id": item_id,
                        "item_hash": bundle.item.item_hash,
                        "updated_at": bundle.item.updated_at.isoformat(),
                        "identity_key": identity_key,
                        "overrides": copy.deepcopy(override),
                        "truth_fingerprint": self._promotion_truth_fingerprint(
                            bundle, real_question
                        ),
                        "question": real_question,
                    }
                )
            except (TypeError, ValueError) as exc:
                item_preview = {
                    "item_id": item_id,
                    "eligible": False,
                    "reason": str(exc),
                }
                prevalidation_failures.append(
                    {"item_id": item_id, "success": False, "reason": str(exc)}
                )
            preview_items.append(item_preview)
        token = secrets.token_urlsafe(18)
        now = self._now_utc()
        self._candidate_promotion_confirmations[token] = PendingCandidatePromotion(
            owner_id=str(actor_id),
            items=tuple(pending_items),
            prevalidation_failures=tuple(prevalidation_failures),
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        return {
            "ready": True,
            "token": token,
            "selected_count": len(normalized_ids),
            "promotable_count": len(pending_items),
            "already_linked_count": sum(
                bool(item.get("already_linked")) for item in preview_items
            ),
            "validation_failures": [
                item for item in preview_items if not item.get("eligible")
            ],
            "unresolved_answer_count": sum(
                bool(item.get("eligible") and item["normalized"].get("answer") is None)
                for item in preview_items
            ),
            "upsert_count": sum(
                bool(item.get("will_upsert")) for item in preview_items
            ),
            "items": preview_items,
        }

    async def confirm_candidate_promotion(
        self, token: str, *, actor_id: str
    ) -> dict[str, Any]:
        if not self._management_actor_allowed(actor_id):
            return {
                "success": False,
                "error": "forbidden",
                "message": "需要 operator/Admin 权限",
            }
        pending = self._candidate_promotion_confirmations.get(str(token))
        if pending is None:
            return {"success": False, "reason": "确认 token 无效或已使用"}
        if pending.owner_id != str(actor_id):
            return {"success": False, "reason": "确认 token 不属于当前操作者"}
        self._candidate_promotion_confirmations.pop(str(token), None)
        if self._now_utc() > pending.expires_at:
            return {"success": False, "reason": "确认 token 已过期"}
        outcomes = [dict(failure) for failure in pending.prevalidation_failures]
        connection = self.storage.connection
        if pending.items:
            connection.execute("BEGIN IMMEDIATE")
        try:
            for index, prepared in enumerate(pending.items):
                savepoint = f"candidate_promotion_{index}"
                connection.execute(f"SAVEPOINT {savepoint}")
                try:
                    bundle = self.library_service.repository.get(
                        int(prepared["item_id"])
                    )
                    if (
                        bundle is None
                        or bundle.item.identity != "real_question_candidate"
                        or bundle.item.item_hash != prepared["item_hash"]
                        or bundle.item.updated_at.isoformat() != prepared["updated_at"]
                    ):
                        raise ValueError("候选题在预览后发生变化")
                    current_question = self._candidate_promotion_question(
                        bundle,
                        int(prepared["item_id"]),
                        prepared.get("overrides", {}),
                    )
                    if (
                        self._promotion_truth_fingerprint(bundle, current_question)
                        != prepared["truth_fingerprint"]
                    ):
                        raise ValueError("候选题的规范化真题内容在预览后发生变化")
                    linked = self.storage.promote_candidate_real_question(
                        current_question,
                        item_id=int(prepared["item_id"]),
                        actor_id=str(actor_id),
                        at=self._now_utc().isoformat(),
                    )
                    connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                    outcomes.append({"success": True, **linked})
                except (TypeError, ValueError, sqlite3.Error) as exc:
                    connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                    outcomes.append(
                        {
                            "item_id": prepared["item_id"],
                            "success": False,
                            "reason": str(exc),
                        }
                    )
            if pending.items:
                connection.commit()
        except Exception:
            if pending.items:
                connection.rollback()
            raise
        return {
            "success": all(item.get("success") for item in outcomes)
            if outcomes
            else False,
            "partial_success": any(item.get("success") for item in outcomes)
            and any(not item.get("success") for item in outcomes),
            "results": outcomes,
        }

    async def list_learning_review_items(
        self,
        *,
        source_id: int | None = None,
        status: str = "pending",
        limit: int = 100,
        page: int | None = None,
        page_size: int = 20,
    ) -> dict[str, Any]:
        if self.library_service is None:
            return {
                "success": False,
                "error": "library_service_unavailable",
                "message": "学习资料库服务不可用",
            }
        return await self.library_service.list_review_items(
            source_id=source_id,
            status=status,
            limit=limit,
            page=page,
            page_size=page_size,
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
        used_content_keys: set[tuple[str, str]] | None = None,
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
            previous_content_key = None
            if (
                used_content_keys is None
                and session_origin
                and origin
                in {
                    "mock",
                    "random",
                }
            ):
                previous_session = self.question_sessions.get_latest_for_scope(
                    str(session_origin)
                )
                if (
                    previous_session
                    and previous_session.get("source_kind") == "library_mock"
                ):
                    previous_content_key = (
                        "library_mock",
                        str(previous_session.get("source_item_key") or ""),
                    )
            result = await self.learning_service.generate_question(
                subject=subject,
                question_type=question_type,
                origin=origin,
                source_name=source_name,
                exam_year=exam_year,
                session_origin=session_origin,
                used_content_keys=used_content_keys,
                previous_content_key=previous_content_key,
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
        result = await self.run_scheduled_scan_jobs(now=now)
        result.update(await self.process_due_work(now=now))
        return result

    async def run_scheduled_scan_jobs(
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
        return result

    async def process_due_work(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or self.clock()
        result: dict[str, Any] = {}
        result["daily_case_sent"] = await self._run_daily_content(
            content_type="daily_case",
            now=current,
        )
        result["daily_question_sent"] = await self._run_daily_content(
            content_type="daily_question",
            now=current,
        )
        result["scheduled_reveals"] = await self.process_due_reveals(now=current)
        return result

    async def process_due_reveals(
        self, *, now: datetime | None = None
    ) -> dict[str, int]:
        """Deliver due pages from the exact persisted session snapshot."""
        if self.publisher is None:
            return {"sent": 0, "skipped": 0, "failed": 0, "needs_review": 0}
        current = _as_utc(now or self.clock())
        current_text = current.isoformat()
        local_today = _local_datetime(
            current, getattr(self.config, "timezone", "Asia/Shanghai")
        ).date()
        totals = {"sent": 0, "skipped": 0, "failed": 0, "needs_review": 0}
        async with self._scheduled_reveal_lock:
            for pending in self.scheduled_reveals.list_pending(
                now=current_text, limit=1000
            ):
                due_local_date = _local_datetime(
                    str(pending["due_at"]),
                    getattr(self.config, "timezone", "Asia/Shanghai"),
                ).date()
                claimed = self.scheduled_reveals.claim_due(
                    int(pending["id"]), now=current_text
                )
                if claimed is None:
                    continue
                if due_local_date < local_today:
                    self.scheduled_reveals.finish(
                        int(claimed["id"]),
                        "skipped",
                        at=current_text,
                        error_summary="超过配置时区的当日发送窗口，未补发旧日期内容",
                    )
                    totals["skipped"] += 1
                    continue
                session = self.question_sessions.get(int(claimed["session_id"]))
                target = (
                    self.storage.get_target(int(claimed["target_id"]))
                    if claimed.get("target_id") is not None
                    else None
                )
                if (
                    session is None
                    or session["status"] != "open"
                    or session["snapshot_hash"] != claimed["snapshot_hash"]
                    or session["scope_origin"] != claimed["target_umo"]
                    or target is None
                    or not target.get("enabled")
                    or target["unified_msg_origin"] != claimed["target_umo"]
                ):
                    self.scheduled_reveals.finish(
                        int(claimed["id"]),
                        "skipped",
                        at=current_text,
                        error_summary="题目会话已关闭、替代或发布目标已停用",
                    )
                    totals["skipped"] += 1
                    continue

                reveal_kind = str(claimed["reveal_kind"])
                prompt_index = int(claimed["prompt_index"])
                if self.scheduled_reveals.was_revealed(
                    session["id"], reveal_kind, prompt_index
                ):
                    self.scheduled_reveals.finish(
                        int(claimed["id"]),
                        "skipped",
                        at=current_text,
                        error_summary="已由人工提前揭晓",
                    )
                    totals["skipped"] += 1
                    continue

                snapshot = prepare_question_session_snapshot(session["snapshot"])
                prompts = snapshot.get("prompts", [])
                if prompt_index >= len(prompts):
                    self.scheduled_reveals.finish(
                        int(claimed["id"]),
                        "needs_review",
                        at=current_text,
                        error_summary="快照中不存在已绑定的小问索引",
                    )
                    totals["needs_review"] += 1
                    continue
                prompt = prompts[prompt_index]
                availability_key = (
                    "answer_available"
                    if reveal_kind == "answer"
                    else "explanation_available"
                )
                page_key = (
                    "answer_pages" if reveal_kind == "answer" else "explanation_pages"
                )
                if not prompt.get(availability_key, False):
                    self.scheduled_reveals.finish(
                        int(claimed["id"]),
                        "skipped",
                        at=current_text,
                        error_summary=(
                            "来源未提供可核验答案"
                            if reveal_kind == "answer"
                            else "来源未提供独立解析"
                        ),
                    )
                    totals["skipped"] += 1
                    continue

                pages = prompt.get(page_key, [])
                if not pages:
                    self.scheduled_reveals.finish(
                        int(claimed["id"]),
                        "skipped",
                        at=current_text,
                        error_summary="没有可发送的揭晓页面",
                    )
                    totals["skipped"] += 1
                    continue
                known_progress = {int(value) for value in claimed["page_progress"]}
                failed = False
                for page_index, text in enumerate(pages):
                    if page_index in known_progress:
                        continue
                    try:
                        outcome = await self.publisher.publish_text(
                            str(claimed["target_umo"]), str(text)
                        )
                    except Exception as exc:  # noqa: BLE001 - delivery may be ambiguous
                        self.scheduled_reveals.finish(
                            int(claimed["id"]),
                            "needs_review",
                            at=self._now_utc().isoformat(),
                            error_summary=str(exc),
                        )
                        totals["needs_review"] += 1
                        failed = True
                        break
                    status, error = _delivery_status(outcome)
                    if status != "sent":
                        final_status = (
                            "failed" if status == "failed" else "needs_review"
                        )
                        self.scheduled_reveals.finish(
                            int(claimed["id"]),
                            final_status,
                            at=self._now_utc().isoformat(),
                            error_summary=error or "揭晓页送达状态未确认",
                        )
                        totals[final_status] += 1
                        failed = True
                        break
                    self.scheduled_reveals.mark_page_sent(
                        int(claimed["id"]),
                        page_index,
                        at=self._now_utc().isoformat(),
                    )
                if failed:
                    continue
                finished_at = self._now_utc().isoformat()
                self.scheduled_reveals.finish(
                    int(claimed["id"]), "sent", at=finished_at
                )
                self.question_sessions.record_reveal_event(
                    session["id"],
                    reveal_kind,
                    actor_id="scheduler",
                    at=finished_at,
                    prompt_index=prompt_index,
                )
                totals["sent"] += 1
        return totals

    def has_due_scheduler_work(self) -> bool:
        if self.scheduled_reveals.list_pending(limit=1):
            return True
        targets = self.storage.list_targets(enabled_only=True)
        return any(
            self.effective_daily_plan(target["id"], content_type).enabled
            for target in targets
            for content_type in ("daily_case", "daily_question")
        )

    def next_due_at(self, now: datetime | None = None) -> datetime | None:
        current = now or self.clock()
        timezone_name = getattr(self.config, "timezone", "Asia/Shanghai")
        local_now = _local_datetime(current, timezone_name)
        local_today = local_now.date()
        candidates: list[datetime] = []
        for target in self.storage.list_targets(enabled_only=True):
            target_id = int(target["id"])
            for content_type in ("daily_case", "daily_question"):
                plan = self.effective_daily_plan(target_id, content_type)
                if not plan.enabled:
                    continue
                due = next_daily_due(current, plan.time, timezone_name)
                if due.astimezone(
                    local_now.tzinfo
                ).date() == local_today and self.storage.has_daily_content(
                    content_date=local_today.isoformat(),
                    target_id=target_id,
                    content_type=content_type,
                ):
                    due = next_daily_due(
                        due + timedelta(seconds=1), plan.time, timezone_name
                    )
                candidates.append(due)

        current_utc = _as_utc(current)
        for job in self.scheduled_reveals.list_pending(limit=1000):
            due = _as_utc(str(job["due_at"]))
            local_due = due.astimezone(local_now.tzinfo)
            if local_due.date() < local_today:
                continue
            candidates.append(max(current_utc, due))
        if not candidates:
            return None
        return min(candidates, key=_as_utc)

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
            timezone_name = getattr(self.config, "timezone", "Asia/Shanghai")
            intended_local = local_due_datetime(
                local_now.date(), plan.time, timezone_name
            )
            intended_local_at = f"{intended_local.isoformat()}[{timezone_name}]"
            resolved = resolve_daily_constraints(
                plan,
                local_now.date(),
                target_id=target["id"],
                content_type=content_type,
            )
            selected_subject = resolved["subject"]
            selected_question_type = resolved["question_type"]
            if (plan.selection_mode == "rotation" and selected_subject is None) or (
                content_type == "daily_question"
                and resolved["question_type_mode"] == "rotation"
                and selected_question_type is None
            ):
                self._record_daily_skip(
                    local_now.date().isoformat(),
                    target["id"],
                    content_type,
                    "轮换计划尚未开始或缺少有效方向/题型",
                    selected_subject,
                    question_type=selected_question_type,
                    origin=resolved["origin"],
                    intended_local_at=intended_local_at,
                    target_label=str(target.get("label") or ""),
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
                used_content_keys = self.storage.list_sent_content_keys(
                    target["id"], content_type
                )
                content = await self.get_daily_case(
                    date=content_date,
                    subject=selected_subject,
                    used_content_keys=used_content_keys,
                )
            else:
                used_content_keys = self.storage.list_sent_content_keys(
                    target["id"], content_type
                )
                content = await self.generate_question(
                    selected_subject or "",
                    origin=resolved["origin"] or "random",
                    question_type=selected_question_type,
                    used_content_keys=used_content_keys,
                )
            if not content.get("available"):
                reason = str(content.get("reason", "没有匹配内容"))
                self._record_daily_skip(
                    content_date,
                    target["id"],
                    content_type,
                    reason,
                    selected_subject,
                    question_type=selected_question_type,
                    origin=resolved["origin"],
                    intended_local_at=intended_local_at,
                    target_label=str(target.get("label") or ""),
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
                source_kind=content.get("source_kind"),
                source_item_key=content.get("source_item_key"),
                resolved_subject=content.get("subject") or selected_subject,
                resolved_question_type=content.get("question_type")
                or selected_question_type,
                resolved_origin=content.get("origin") or resolved["origin"],
                intended_local_at=intended_local_at,
                target_label=str(target.get("label") or ""),
            )
            if claim_id is None:
                continue
            question_snapshot = (
                self._build_question_snapshot(content)
                if content_type == "daily_question"
                else None
            )
            formatted = (
                _format_daily_content(content_type, content)
                if content_type == "daily_case"
                else self._snapshot_first_prompt(question_snapshot or {})
            )
            outcome = await self.publisher.publish_text(
                target["unified_msg_origin"],
                formatted,
            )
            status, error = _delivery_status(outcome)
            self.storage.finish_daily_content(
                claim_id, status=status, error_summary=error
            )
            sent += int(status == "sent")
            if status == "sent":
                if question_snapshot is not None:
                    item = (
                        content.get("item")
                        if isinstance(content.get("item"), dict)
                        else {}
                    )
                    opened = self._persist_question_snapshot(
                        question_snapshot,
                        session_origin=target["unified_msg_origin"],
                        target_id=target["id"],
                        actor_id="scheduler",
                        source_kind=(
                            "library_question"
                            if item
                            else (
                                "real_question"
                                if content.get("origin") == "real"
                                else "generated_question"
                            )
                        ),
                        source_item_key=str(
                            content.get("question_id")
                            or item.get("id")
                            or question_snapshot.get("snapshot_hash")
                        ),
                        question_identity=str(question_snapshot.get("identity") or ""),
                        library_item_id=item.get("id"),
                        real_question_id=(
                            content.get("question_id")
                            if content.get("origin") == "real"
                            else None
                        ),
                    )
                    sent_at = self._now_utc()
                    self._schedule_question_reveals(
                        plan=plan,
                        session_id=int(opened["session_id"]),
                        target_umo=str(target["unified_msg_origin"]),
                        snapshot=question_snapshot,
                        snapshot_hash=str(opened["snapshot_hash"]),
                        sent_at=sent_at,
                    )
                self.logger.info(
                    "Law Assistant sent %s to %s",
                    content_type,
                    target["unified_msg_origin"],
                )
        return sent

    def _schedule_question_reveals(
        self,
        *,
        plan: DailyPlan,
        session_id: int,
        target_umo: str,
        snapshot: dict[str, Any],
        snapshot_hash: str,
        sent_at: datetime,
    ) -> None:
        """Persist independent delayed jobs for each answer/explanation prompt."""
        if plan.question_reveal_mode != "delayed":
            return
        for prompt_index, prompt in enumerate(snapshot.get("prompts", [])):
            for kind, delay in (
                ("answer", plan.answer_reveal_delay_minutes),
                ("explanation", plan.explanation_reveal_delay_minutes),
            ):
                if delay <= 0:
                    continue
                job = self.scheduled_reveals.create_for_session(
                    session_id=session_id,
                    target_umo=target_umo,
                    snapshot_hash=snapshot_hash,
                    question_sent_at=sent_at.isoformat(),
                    due_at=(sent_at + timedelta(minutes=delay)).isoformat(),
                    reveal_kind=kind,
                    prompt_index=prompt_index,
                    created_at=sent_at.isoformat(),
                )
                has_source_content = bool(
                    prompt.get(
                        "answer_available"
                        if kind == "answer"
                        else "explanation_available",
                        False,
                    )
                )
                if not has_source_content:
                    self.scheduled_reveals.skip(
                        int(job["id"]),
                        at=sent_at.isoformat(),
                        reason=(
                            "来源未提供可核验答案"
                            if kind == "answer"
                            else "来源未提供独立解析"
                        ),
                    )

    def _record_daily_skip(
        self,
        content_date: str,
        target_id: int,
        content_type: str,
        reason: str,
        subject: str | None,
        *,
        question_type: str | None = None,
        origin: str | None = None,
        intended_local_at: str | None = None,
        target_label: str = "",
    ) -> None:
        claim_id = self.storage.record_daily_skip(
            content_date=content_date,
            target_id=target_id,
            content_type=content_type,
            reason=reason,
            subject=subject,
            resolved_question_type=question_type,
            resolved_origin=origin,
            intended_local_at=intended_local_at,
            target_label=target_label,
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


def _event_dashboard_dict(event: LegalEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "source_key": event.source_key,
        "source_item_key": event.source_item_key,
        "title": event.title,
        "source_url": event.source_url,
        "organizer": event.organizer,
        "event_type": event.event_type,
        "eligibility": event.eligibility,
        "status": event.status,
        "summary": event.summary,
        "radar_status": event.metadata.get("radar_status"),
        "status_override": event.metadata.get("status_override"),
        "revision": event.revision,
        "source_published_at": (
            event.source_published_at.isoformat()
            if event.source_published_at is not None
            else None
        ),
        "updated_at": event.updated_at.isoformat(),
        "dates": [
            {
                "kind": item.kind,
                "datetime": item.datetime.isoformat() if item.datetime else None,
                "timezone": item.timezone,
                "label": item.label,
                "evidence_text": item.evidence_text,
                "confirmed": item.confirmed,
            }
            for item in event.dates
        ],
    }


def _document_error_code(message: str) -> str:
    lowered = str(message).lower()
    if "格式" in message:
        return "unsupported_format"
    if "20 mb" in lowered:
        return "file_too_large"
    if "为空" in message:
        return "empty_file"
    return "invalid_upload"


__all__ = ["LawAssistantService", "ScanFailure", "ScanResult"]
