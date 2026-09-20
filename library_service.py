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
    from library_models import CaseDetail, LearningItem, LibrarySource, QuestionDetail
    from library_repository import LibraryRepository


_MATERIAL_TYPES = {
    "case": ("case", "user_case", "unverified"),
    "real_question_candidate": ("question", "real_question_candidate", "unverified"),
    "mock_question": ("question", "mock_question", "not_applicable"),
    "note": ("note", "note", "not_applicable"),
}
_SEARCH_TYPES = {
    "case": "case",
    "question": "question",
    "real_question_candidate": "question",
    "mock_question": "question",
    "note": "note",
}
_SEARCH_IDENTITIES = {
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
            normalized_subjects = _subjects(subjects)
            title_value = _title(raw_text, title)
            now = self.clock()
            source_hash = _hash(raw_text)
            item_type, identity, verification_status = mapped
            case_detail: CaseDetail | None = None
            question_detail: QuestionDetail | None = None
            if normalized_type == "case":
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
                answer = structured.get("answer")
                question_detail = QuestionDetail(
                    question_identity=identity,
                    question_type=question_type,
                    stem=stem,
                    options=options,
                    answer=answer,
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
            item_payload = {
                "source_hash": source_hash,
                "material_type": normalized_type,
                "title": title_value,
                "subjects": normalized_subjects,
                "structured": structured,
                "note": _as_text(note),
            }
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
                created_by=str(created_by),
                metadata=metadata,
            )
            source = LibrarySource(
                source_kind="user_text",
                title=title_value,
                raw_text=raw_text,
                source_url=_as_text(source_url),
                content_hash=source_hash,
                created_at=now,
                created_by=str(created_by),
                session_origin=str(session_origin),
                metadata={},
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
            "item_type": item_type,
            "identity": identity,
            "verification_status": verification_status,
            "message": "资料已收藏" if not archived.duplicate else "已找到相同收藏资料",
        }

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
            },
            "sources": sources,
            "source": sources[0] if sources else None,
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
