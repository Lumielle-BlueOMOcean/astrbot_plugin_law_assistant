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
    from .activity_radar import (
        canonical_event_key,
        derive_radar_status,
        radar_policy_for,
    )
    from .content import (
        normalize_subject,
        parse_origin,
        parse_question_type,
        parse_subject,
    )
    from .daily_plans import DailyPlan
    from .daily_resolver import resolve_daily_constraints
    from .document_extractors import DocumentParseError, DocumentSegment, ParsedDocument
    from .learning_segmentation import (
        CASE_SEGMENTATION_VERSION,
        segment_official_cases,
    )
    from .learning_service import LearningService
    from .library_models import LibrarySource
    from .library_service import LibraryService
    from .models import CaseItem, LawUpdate, LegalEvent, SourceDocument
    from .publisher import format_deadline_reminder, format_event
    from .question_session import (
        build_question_session_snapshot,
        format_session_answer,
        format_session_explanation,
        format_session_prompt,
        format_session_status,
        prepare_question_session_snapshot,
    )
    from .question_session_repository import QuestionSessionRepository
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
        normalize_subject,
        parse_origin,
        parse_question_type,
        parse_subject,
    )
    from daily_plans import DailyPlan
    from daily_resolver import resolve_daily_constraints
    from document_extractors import DocumentParseError, DocumentSegment, ParsedDocument
    from learning_segmentation import CASE_SEGMENTATION_VERSION, segment_official_cases
    from learning_service import LearningService
    from library_models import LibrarySource
    from library_service import LibraryService
    from models import CaseItem, LawUpdate, LegalEvent, SourceDocument
    from publisher import format_deadline_reminder, format_event
    from question_session import (
        build_question_session_snapshot,
        format_session_answer,
        format_session_explanation,
        format_session_prompt,
        format_session_status,
        prepare_question_session_snapshot,
    )
    from question_session_repository import QuestionSessionRepository
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
        self._unbind_confirmations: dict[
            str, tuple[int, datetime, datetime, str | None]
        ] = {}
        self._content_references: dict[str, ContentReference] = {}
        self.question_sessions = QuestionSessionRepository(storage.connection)
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
                    "library_question"
                    if item
                    else ("real_question" if origin == "real" else "generated_question")
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
        detail = await self.get_learning_item(item_id)
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
                "reason": "当前会话没有进行中的题目；可用 /law question 或 /law study <ID> 开始。",
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
                    "/law next-answer"
                    if session["current_stage"] == "answer"
                    else "/law next-explanation"
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
                if material_page + 1 < len(pages):
                    material_page += 1
                else:
                    material_index += 1
                    material_page = 0
            else:
                prompts = snapshot.get("prompts", [])
                pages = prompts[prompt_index].get("stem_pages", []) if prompts else []
                if prompt_page + 1 >= len(pages):
                    return {
                        "success": True,
                        "session_id": session["id"],
                        "text": "本题内容已展示完毕，可继续讨论、使用 /law answer 查看答案，或在有下一小问时使用 /law next-question。",
                    }
                prompt_page += 1
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
                reveal_command = (
                    "/law answer" if kind == "answer" else "/law explanation"
                )
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
        return session["current_prompt_page"] + 1 < len(pages)

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
        radar_counts = {key: 0 for key in ("current", "needs_review", "historical")}
        for event in events:
            radar_counts[self._radar_status(event)] += 1
        learning = (
            self.library_service.dashboard_summary()
            if self.library_service is not None
            else {}
        )
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
                "version": "0.5.0",
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
                "daily": self.storage.list_daily_contents(limit=10),
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

    async def dashboard_history(
        self, kind: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        if kind == "daily":
            return self.storage.list_daily_contents(limit=safe_limit)
        if kind == "publications":
            return self.storage.list_publications(limit=safe_limit)
        if kind == "reminders":
            return self.storage.list_reminders(limit=safe_limit)
        if kind == "sources":
            return self.storage.list_source_runs(limit=safe_limit)
        raise ValueError(
            "history kind 必须是 daily、publications、reminders 或 sources"
        )

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
        if radar_status not in {"current", "needs_review", "historical", "all"}:
            raise ValueError(
                "radar_status 必须是 current、needs_review、historical 或 all"
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
                    metadata={**event.metadata, "radar_status": derived_status},
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
            metadata={**event.metadata, "radar_status": self._radar_status(event)},
        )

    def _radar_status(self, event: LegalEvent) -> str:
        return derive_radar_status(
            event,
            self._now_utc(),
            getattr(self.config, "timezone", "Asia/Shanghai"),
            tuple(getattr(self.config, "radar_historical_keywords", ()) or ()),
        )

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
        if any(plan.enabled for plan in pending.plans) or self._has_enabled_plan():
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
            result = await self.learning_service.generate_question(
                subject=subject,
                question_type=question_type,
                origin=origin,
                source_name=source_name,
                exam_year=exam_year,
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
                    self._persist_question_snapshot(
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
        *,
        question_type: str | None = None,
        origin: str | None = None,
    ) -> None:
        claim_id = self.storage.record_daily_skip(
            content_date=content_date,
            target_id=target_id,
            content_type=content_type,
            reason=reason,
            subject=subject,
            resolved_question_type=question_type,
            resolved_origin=origin,
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
