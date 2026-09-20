from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

if __package__ and "." in __package__:
    from .content import parse_question_type, parse_subject
    from .library_models import (
        CaseDetail,
        LearningItem,
        LibrarySource,
        QuestionDetail,
    )
    from .library_repository import LibraryRepository
else:
    from content import parse_question_type, parse_subject
    from library_models import (
        CaseDetail,
        LearningItem,
        LibrarySource,
        QuestionDetail,
    )
    from library_repository import LibraryRepository


_MATERIAL_TYPES = {
    "case": ("case", "user_case", "unverified"),
    "real_question_candidate": ("question", "real_question_candidate", "unverified"),
    "mock_question": ("question", "mock_question", "not_applicable"),
    "note": ("note", "note", "not_applicable"),
}
_SEARCH_TYPES = {
    "case": "case",
    "official_case": "case",
    "question": "question",
    "real_question_candidate": "question",
    "mock_question": "question",
    "note": "note",
}
_SEARCH_IDENTITIES = {
    "official_case": "official_case",
    "real_question_candidate": "real_question_candidate",
    "mock_question": "mock_question",
}
_SPLIT_RE = re.compile(r"[,，、;；/|]+")


def _error(error: str, message: str) -> dict[str, Any]:
    return {"success": False, "error": error, "message": message}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_text_list(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        values = _SPLIT_RE.split(value)
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        raise TypeError("字段必须是字符串或列表")
    return tuple(str(item).strip() for item in values if str(item).strip())


def _subjects(value: Any) -> tuple[str, ...]:
    try:
        values = _as_text_list(value)
        normalized = []
        for item in values:
            parsed = parse_subject(item)
            if parsed and parsed not in normalized:
                normalized.append(parsed)
        return tuple(normalized)
    except ValueError as exc:
        raise ValueError(f"不支持的方向：{value}") from exc


def _title(raw_text: str, explicit: Any) -> str:
    value = _as_text(explicit)
    if value:
        return value[:200]
    first_line = next(
        (line.strip() for line in raw_text.splitlines() if line.strip()), ""
    )
    return (first_line or "学习资料")[:200]


def _structured_json(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("structured_json 必须是合法 JSON 对象") from exc
    if not isinstance(parsed, dict):
        raise TypeError("structured_json 必须是 JSON 对象")
    return parsed


class LibraryService:
    """Validate and orchestrate evidence-preserving learning-library operations."""

    def __init__(
        self, repository: LibraryRepository, *, clock: Any | None = None
    ) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def record_source(self, source: LibrarySource) -> int:
        """Persist an original source even when extraction cannot proceed."""
        with self.repository.connection:
            return self.repository.ensure_source(source)

    async def archive_learning_material(
        self,
        *,
        raw_text: str,
        material_type: str,
        title: str = "",
        subjects: str | list[str] | tuple[str, ...] = "",
        source_url: str = "",
        structured_json: str | dict[str, Any] = "{}",
        note: str = "",
        created_by: str,
        session_origin: str,
    ) -> dict[str, Any]:
        raw_text = str(raw_text or "")
        if not raw_text.strip():
            return _error("invalid_raw_text", "原始资料不能为空")
        normalized_type = str(material_type or "").strip().lower()
        mapped = _MATERIAL_TYPES.get(normalized_type)
        if mapped is None:
            return _error(
                "invalid_material_type", f"不支持的资料类型：{normalized_type}"
            )
        try:
            structured = _structured_json(structured_json)
            now = self.clock()
            source_hash = _hash(raw_text)
            source = LibrarySource(
                source_kind="user_text",
                title=_title(raw_text, title),
                raw_text=raw_text,
                source_url=_as_text(source_url),
                content_hash=source_hash,
                created_at=now,
                created_by=str(created_by),
                session_origin=str(session_origin),
                metadata={},
            )
            item, case_detail, question_detail = self._build_candidate(
                raw_text=raw_text,
                material_type=normalized_type,
                title=title,
                subjects=subjects,
                structured=structured,
                created_by=str(created_by),
                now=now,
                note=note,
            )
            archived = self.repository.archive(
                source, item, case=case_detail, question=question_detail
            )
        except (TypeError, ValueError) as exc:
            return _error("invalid_structured_content", str(exc))
        return {
            "success": True,
            "item_id": archived.item_id,
            "source_id": archived.source_id,
            "duplicate": archived.duplicate,
            "item_type": item.item_type,
            "identity": item.identity,
            "verification_status": item.verification_status,
            "message": "资料已收藏" if not archived.duplicate else "已找到相同收藏资料",
        }

    def _build_candidate(
        self,
        *,
        raw_text: str,
        material_type: str,
        title: Any,
        subjects: Any,
        structured: dict[str, Any],
        created_by: str,
        now: datetime,
        note: Any = "",
        identity_override: str | None = None,
        verification_override: str | None = None,
        item_hash_seed: Any | None = None,
    ) -> tuple[LearningItem, CaseDetail | None, QuestionDetail | None]:
        normalized_type = str(material_type or "").strip().lower()
        mapped = _MATERIAL_TYPES.get(normalized_type)
        if mapped is None and identity_override != "official_case":
            raise ValueError(f"不支持的资料类型：{normalized_type}")
        normalized_subjects = _subjects(subjects)
        title_value = _title(raw_text, title)
        if identity_override == "official_case":
            item_type, identity, verification_status = (
                "case",
                "official_case",
                verification_override or "verified_official",
            )
        else:
            assert mapped is not None
            item_type, identity, verification_status = mapped
        case_detail: CaseDetail | None = None
        question_detail: QuestionDetail | None = None
        if normalized_type == "case" or identity == "official_case":
            case_summary = _as_text(structured.get("case_summary"))
            issues = _as_text_list(structured.get("issues"))
            practice_notes = _as_text_list(structured.get("practice_notes"))
            case_detail = CaseDetail(
                case_number=_as_text(structured.get("case_number")),
                authority=_as_text(structured.get("authority")),
                case_summary=case_summary,
                issues=issues,
                reasoning=_as_text(structured.get("reasoning")),
                result_text=_as_text(structured.get("result")),
                practice_notes=practice_notes,
            )
            source_summary = case_summary or raw_text.strip()[:300]
        elif normalized_type in {"real_question_candidate", "mock_question"}:
            stem = _as_text(structured.get("stem") or structured.get("question"))
            question_type = parse_question_type(structured.get("question_type"))
            if not stem or not question_type:
                raise ValueError("题目必须包含题干和受支持的题型")
            options = _as_text_list(structured.get("options"))
            question_detail = QuestionDetail(
                question_identity=identity,
                question_type=question_type,
                stem=stem,
                options=options,
                answer=structured.get("answer"),
                explanation=_as_text(structured.get("explanation")),
                exam_name=_as_text(structured.get("exam_name")),
                exam_year=_as_text(structured.get("exam_year")),
                paper=_as_text(structured.get("paper")),
                question_number=_as_text(structured.get("question_number")),
                answer_source=_as_text(structured.get("answer_source")),
            )
            source_summary = question_detail.explanation or stem[:300]
        else:
            body = _as_text(structured.get("body")) or raw_text.strip()
            source_summary = body[:300]

        metadata: dict[str, Any] = {}
        if normalized_type == "note":
            metadata["body"] = body
        if _as_text(note):
            metadata["note"] = _as_text(note)
        for key in (
            "evidence_text",
            "adapter_key",
            "original_title",
            "answer_locator",
            "case_segmentation_version",
            "source_item_key",
        ):
            if _as_text(structured.get(key)):
                metadata[key] = _as_text(structured[key])
        if normalized_type in {"real_question_candidate", "mock_question"}:
            for key in ("questions", "issues", "source_note"):
                if structured.get(key) not in (None, "", [], {}):
                    metadata[key] = structured[key]
        item_payload = (
            item_hash_seed
            if item_hash_seed is not None
            else {
                "source_hash": _hash(raw_text),
                "material_type": normalized_type,
                "title": title_value,
                "subjects": normalized_subjects,
                "structured": structured,
                "note": _as_text(note),
            }
        )
        item = LearningItem(
            item_type=item_type,
            identity=identity,
            item_hash=_hash(_canonical(item_payload)),
            title=title_value,
            subjects=normalized_subjects,
            verification_status=verification_status,
            source_summary=source_summary,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            metadata=metadata,
        )
        return item, case_detail, question_detail

    async def archive_material_batch(
        self,
        *,
        source: LibrarySource,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not source.raw_text.strip():
            return _error("invalid_raw_text", "原始资料不能为空")
        items: list[dict[str, Any]] = []
        failed = 0
        duplicate = 0
        archived = 0
        needs_review = 0
        review_items: list[dict[str, Any]] = []
        with self.repository.connection:
            source_id = self.repository.ensure_source(source)
            for index, candidate in enumerate(candidates):
                if not isinstance(candidate, dict):
                    failed += 1
                    items.append(
                        {
                            "index": index,
                            "status": "failed",
                            "reason": "候选条目必须是对象",
                        }
                    )
                    continue
                if str(candidate.get("status") or "").strip().lower() in {
                    "needs_review",
                    "review",
                }:
                    needs_review += 1
                    review_reason = _as_text(candidate.get("review_reason")) or (
                        "边界需要人工确认"
                    )
                    review_raw_text = (
                        _as_text(candidate.get("raw_text")) or source.raw_text
                    )
                    review_material_type = (
                        _as_text(candidate.get("material_type")) or "unknown"
                    )
                    locator = _as_text(candidate.get("locator"))
                    candidate_key = _hash(
                        _canonical(
                            {
                                "source_hash": source.content_hash,
                                "source_url": source.source_url,
                                "locator": locator,
                                "material_type": review_material_type,
                                "raw_text": review_raw_text,
                            }
                        )
                    )
                    review_id = self.repository.record_review_item(
                        source_id=source_id,
                        candidate_key=candidate_key,
                        material_type=review_material_type,
                        locator=locator,
                        raw_fragment=review_raw_text,
                        proposed_structure=_structured_json(
                            candidate.get("structured", {})
                        ),
                        review_reason=review_reason,
                        now=self.clock(),
                    )
                    items.append(
                        {
                            "index": index,
                            "status": "needs_review",
                            "reason": review_reason,
                            "review_id": review_id,
                            "source_id": source_id,
                            "locator": locator,
                        }
                    )
                    review_items.append(
                        {
                            "id": review_id,
                            "source_id": source_id,
                            "locator": locator,
                            "reason": review_reason,
                        }
                    )
                    continue
                material_type = (
                    str(candidate.get("material_type") or "").strip().lower()
                )
                locator = _as_text(candidate.get("locator"))
                raw_text = _as_text(candidate.get("raw_text")) or source.raw_text
                try:
                    structured = _structured_json(candidate.get("structured", {}))
                    item, case_detail, question_detail = self._build_candidate(
                        raw_text=raw_text,
                        material_type=material_type,
                        title=candidate.get("title", ""),
                        subjects=candidate.get("subjects", ""),
                        structured=structured,
                        created_by=source.created_by,
                        now=self.clock(),
                        note=candidate.get("note", ""),
                        identity_override=(
                            "official_case"
                            if candidate.get("trusted_official") is True
                            else None
                        ),
                        verification_override=(
                            "verified_official"
                            if candidate.get("trusted_official") is True
                            else None
                        ),
                        item_hash_seed={
                            "source_hash": source.content_hash,
                            "source_url": source.source_url,
                            "locator": locator or _hash(_canonical(candidate)),
                            "material_type": material_type,
                            "identity": (
                                "official_case"
                                if candidate.get("trusted_official") is True
                                else material_type
                            ),
                            "segmentation_version": structured.get(
                                "case_segmentation_version", ""
                            ),
                        },
                    )
                    result = self.repository.archive(
                        source,
                        item,
                        case=case_detail,
                        question=question_detail,
                        locator=locator,
                        relationship=_as_text(candidate.get("relationship"))
                        or "primary_evidence",
                    )
                except (TypeError, ValueError, KeyError) as exc:
                    failed += 1
                    items.append(
                        {"index": index, "status": "failed", "reason": str(exc)}
                    )
                    continue
                status = "duplicate" if result.duplicate else "archived"
                if result.duplicate:
                    duplicate += 1
                else:
                    archived += 1
                items.append(
                    {
                        "index": index,
                        "status": status,
                        "item_id": result.item_id,
                        "source_id": source_id,
                        "item_type": item.item_type,
                        "identity": item.identity,
                        "locator": locator,
                    }
                )
        return {
            "success": True,
            "source_id": source_id,
            "total_candidates": len(candidates),
            "archived": archived,
            "duplicate": duplicate,
            "failed": failed,
            "needs_review": needs_review,
            "review_items": review_items,
            "items": items,
        }

    async def archive_official_cases(
        self,
        *,
        source: LibrarySource,
        candidates: list[dict[str, Any]],
        adapter_key: str,
        source_item_key: str = "",
        segmentation_version: str = "",
    ) -> dict[str, Any]:
        if adapter_key not in {"court_cases", "spp_cases"}:
            return _error(
                "untrusted_source", "只有最高法或最高检受信来源可以创建官方案例"
            )
        preserved_subjects: tuple[str, ...] = ()
        if source_item_key:
            with self.repository.connection:
                prior_source_ids = self.repository.source_ids_by_metadata(
                    created_by=source.created_by,
                    key="source_item_key",
                    value=source_item_key,
                )
                preserved_subjects = self.repository.manual_subjects_for_sources(
                    prior_source_ids
                )
                self.repository.deactivate_items_for_sources(
                    prior_source_ids, identity="official_case"
                )
                self.repository.supersede_review_items(prior_source_ids)
        normalized = []
        for candidate in candidates:
            item = dict(candidate)
            item["material_type"] = "case"
            item["trusted_official"] = True
            structured = _structured_json(item.get("structured", {}))
            structured["adapter_key"] = adapter_key
            if segmentation_version:
                structured["case_segmentation_version"] = segmentation_version
            if source_item_key:
                structured["source_item_key"] = source_item_key
            item["structured"] = structured
            candidate_subjects = _as_text_list(item.get("subjects", ""))
            item["subjects"] = list(
                dict.fromkeys((*candidate_subjects, *preserved_subjects))
            )
            normalized.append(item)
        return await self.archive_material_batch(source=source, candidates=normalized)

    async def list_official_cases(
        self, *, subject: str = "", limit: int = 100
    ) -> dict[str, Any]:
        try:
            normalized_subject = parse_subject(subject) if str(subject).strip() else ""
        except ValueError as exc:
            return _error("invalid_subject", str(exc))
        items = self.repository.search(
            item_type="case",
            identity="official_case",
            subject=normalized_subject or "",
            limit=limit,
        )
        return {
            "success": True,
            "count": len(items),
            "items": [self._item_summary(item) for item in items],
        }

    def official_case_bundles(
        self, *, subject: str = "", limit: int = 100
    ) -> list[Any]:
        normalized_subject = parse_subject(subject) if str(subject).strip() else ""
        items = self.repository.search(
            item_type="case",
            identity="official_case",
            subject=normalized_subject or "",
            limit=limit,
        )
        bundles = []
        for item in items:
            if item.id is not None:
                bundle = self.repository.get(item.id)
                if bundle is not None:
                    bundles.append(bundle)
        return bundles

    async def list_review_items(
        self,
        *,
        source_id: int | None = None,
        status: str = "pending",
        limit: int = 100,
    ) -> dict[str, Any]:
        if status not in {"pending", "resolved", "superseded"}:
            return _error("invalid_review_status", "不支持的待复核状态")
        try:
            items = self.repository.list_review_items(
                source_id=source_id, status=status, limit=limit
            )
        except (TypeError, ValueError):
            return _error("invalid_limit", "limit 必须是整数")
        return {
            "success": True,
            "count": len(items),
            "items": [self._review_dict(item) for item in items],
        }

    async def get_review_item(self, review_id: int) -> dict[str, Any]:
        try:
            item = self.repository.get_review_item(int(review_id))
        except (TypeError, ValueError):
            return _error("invalid_review_id", "review_id 必须是整数")
        if item is None:
            return _error("not_found", f"未找到待复核条目：{review_id}")
        return {"success": True, "item": self._review_dict(item)}

    async def update_review_status(self, review_id: int, status: str) -> dict[str, Any]:
        try:
            changed = self.repository.update_review_status(int(review_id), str(status))
        except (TypeError, ValueError) as exc:
            return _error("invalid_review_status", str(exc))
        if not changed:
            return _error("not_found", f"未找到待复核条目：{review_id}")
        return await self.get_review_item(int(review_id))

    def set_official_case_subjects(
        self,
        *,
        source_key: str,
        source_item_key: str,
        subjects: tuple[str, ...],
    ) -> int:
        """Propagate an operator classification to matching official segments."""
        updated = 0
        for item in self.repository.search(
            item_type="case", identity="official_case", limit=50
        ):
            if item.id is None:
                continue
            bundle = self.repository.get(item.id)
            if bundle is None:
                continue
            if (
                any(
                    source.metadata.get("adapter_key") == source_key
                    and source.metadata.get("source_item_key") == source_item_key
                    for source in bundle.sources
                )
                and self.repository.update(item.id, {"subjects": subjects}) is not None
            ):
                updated += 1
        return updated

    async def search_learning_library(
        self,
        *,
        query: str = "",
        material_type: str = "",
        subject: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        normalized_type = str(material_type or "").strip().lower()
        if normalized_type and normalized_type not in _SEARCH_TYPES:
            return _error(
                "invalid_material_type", f"不支持的资料类型：{normalized_type}"
            )
        try:
            normalized_subject = (
                parse_subject(subject) if str(subject).strip() else None
            )
        except ValueError as exc:
            return _error("invalid_subject", str(exc))
        try:
            safe_limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            return _error("invalid_limit", "limit 必须是整数")
        items = self.repository.search(
            query=str(query or "").strip(),
            item_type=_SEARCH_TYPES.get(normalized_type, ""),
            identity=_SEARCH_IDENTITIES.get(normalized_type, ""),
            subject=normalized_subject or "",
            limit=safe_limit,
        )
        return {
            "success": True,
            "count": len(items),
            "limit": safe_limit,
            "items": [self._item_summary(item) for item in items],
        }

    def dashboard_summary(self) -> dict[str, int]:
        """Return bounded inventory counters for the dashboard overview."""
        return {
            "official_case": self.repository.count_items(identity="official_case"),
            "persistent_mock": self.repository.count_items(identity="mock_question"),
            "verified_real": self.repository.count_items(
                identity="verified_real_question"
            ),
            "real_question_candidate": self.repository.count_items(
                identity="real_question_candidate"
            ),
            "pending_review": self.repository.count_review_items(status="pending"),
        }

    async def get_learning_item(self, item_id: int) -> dict[str, Any]:
        try:
            normalized_id = int(item_id)
        except (TypeError, ValueError):
            return _error("invalid_item_id", "item_id 必须是整数")
        bundle = self.repository.get(normalized_id)
        if bundle is None:
            return _error("not_found", f"未找到学习条目：{normalized_id}")
        result = {"success": True}
        result.update(self._bundle_dict(bundle))
        return result

    async def update_learning_item(
        self, item_id: int, changes: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(changes, dict):
            return _error("invalid_update", "changes 必须是 JSON 对象")
        protected = set(changes) & {
            "identity",
            "item_type",
            "verification_status",
            "created_by",
            "source_hash",
            "content_hash",
        }
        if protected:
            return _error(
                "invalid_update_field",
                f"不允许修改字段：{sorted(protected)}",
            )
        allowed = {"title", "subjects", "note", "practice_notes", "explanation"}
        unknown = set(changes) - allowed
        if unknown:
            return _error("invalid_update_field", f"不允许修改字段：{sorted(unknown)}")
        normalized: dict[str, Any] = {}
        try:
            if "title" in changes:
                title = _as_text(changes["title"])
                if not title:
                    raise ValueError("标题不能为空")
                normalized["title"] = title[:200]
            if "subjects" in changes:
                normalized["subjects"] = _subjects(changes["subjects"])
            if "note" in changes:
                normalized["note"] = _as_text(changes["note"])
            if "practice_notes" in changes:
                normalized["practice_notes"] = _as_text_list(changes["practice_notes"])
            if "explanation" in changes:
                normalized["explanation"] = _as_text(changes["explanation"])
            normalized_id = int(item_id)
            current = self.repository.get(normalized_id)
            if current is None:
                return _error("not_found", f"未找到学习条目：{item_id}")
            if "practice_notes" in normalized and current.case is None:
                return _error("invalid_update", "practice_notes 仅适用于案例条目")
            if "explanation" in normalized and current.question is None:
                return _error("invalid_update", "explanation 仅适用于题目条目")
            updated = self.repository.update(normalized_id, normalized)
        except (TypeError, ValueError) as exc:
            return _error("invalid_update", str(exc))
        if updated is None:
            return _error("not_found", f"未找到学习条目：{item_id}")
        result = {"success": True, "message": "学习条目已更新"}
        result.update(self._bundle_dict(updated))
        return result

    @staticmethod
    def _item_summary(item: LearningItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "item_type": item.item_type,
            "identity": item.identity,
            "title": item.title,
            "subjects": list(item.subjects),
            "summary": item.source_summary,
            "verification_status": item.verification_status,
            "created_by": item.created_by,
            "updated_at": item.updated_at.isoformat(),
            "active": item.active,
        }

    @staticmethod
    def _review_dict(item: Any) -> dict[str, Any]:
        return {
            "id": item.id,
            "source_id": item.source_id,
            "candidate_key": item.candidate_key,
            "material_type": item.material_type,
            "locator": item.locator,
            "raw_fragment": item.raw_fragment,
            "proposed_structure": item.proposed_structure,
            "review_reason": item.review_reason,
            "status": item.status,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    @classmethod
    def _bundle_dict(cls, bundle: Any) -> dict[str, Any]:
        item = bundle.item
        sources = [
            {
                "id": source.id,
                "source_kind": source.source_kind,
                "title": source.title,
                "raw_text": source.raw_text,
                "source_url": source.source_url,
                "content_hash": source.content_hash,
                "created_at": source.created_at.isoformat(),
                "created_by": source.created_by,
                "session_origin": source.session_origin,
                "metadata": source.metadata,
            }
            for source in bundle.sources
        ]
        result: dict[str, Any] = {
            "item": {
                "id": item.id,
                "item_type": item.item_type,
                "identity": item.identity,
                "item_hash": item.item_hash,
                "title": item.title,
                "subjects": list(item.subjects),
                "verification_status": item.verification_status,
                "source_summary": item.source_summary,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
                "created_by": item.created_by,
                "metadata": item.metadata,
                "active": item.active,
            },
            "sources": sources,
            "source": sources[0] if sources else None,
            "source_links": [
                {
                    "source_id": link.source.id,
                    "source_url": link.source.source_url,
                    "locator": link.locator,
                    "relationship": link.relationship,
                }
                for link in getattr(bundle, "source_links", ())
            ],
        }
        if bundle.case is not None:
            result["case"] = {
                "case_number": bundle.case.case_number,
                "authority": bundle.case.authority,
                "case_summary": bundle.case.case_summary,
                "issues": list(bundle.case.issues),
                "reasoning": bundle.case.reasoning,
                "result": bundle.case.result_text,
                "practice_notes": list(bundle.case.practice_notes),
            }
        if bundle.question is not None:
            result["question"] = {
                "question_identity": bundle.question.question_identity,
                "question_type": bundle.question.question_type,
                "stem": bundle.question.stem,
                "options": list(bundle.question.options),
                "answer": bundle.question.answer,
                "explanation": bundle.question.explanation,
                "exam_name": bundle.question.exam_name,
                "exam_year": bundle.question.exam_year,
                "paper": bundle.question.paper,
                "question_number": bundle.question.question_number,
                "answer_source": bundle.question.answer_source,
            }
        return result


__all__ = ["LibraryService"]
