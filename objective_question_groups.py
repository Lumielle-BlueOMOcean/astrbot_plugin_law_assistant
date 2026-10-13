"""Small deterministic helpers for explicit structured objective groups."""

from __future__ import annotations

import copy
import unicodedata
from collections.abc import Mapping
from typing import Any


def normalize_source_number(value: Any) -> str:
    """Normalize a displayed source number for exact lookup, never fuzzy match."""
    if value is None:
        return ""
    normalized = "".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()
    return normalized.removeprefix("第").removesuffix("题")


def resolve_objective_question_group(
    payload: Mapping[str, Any], external_id: str
) -> dict[str, Any] | None:
    """Find the one explicitly declared group containing ``external_id``.

    Stored imports are expected to have passed the validator. This second,
    small check fails closed if a persisted payload is malformed or corrupt;
    membership is never inferred from text, material similarity, or adjacency.
    """
    raw_groups = payload.get("objective_question_groups", [])
    if not isinstance(raw_groups, list):
        raise TypeError("objective_question_groups must be a list")

    matches: list[dict[str, Any]] = []
    memberships: set[str] = set()
    for group in raw_groups:
        if not isinstance(group, Mapping):
            raise TypeError("objective group must be an object")
        refs = group.get("question_refs")
        if (
            not isinstance(refs, list)
            or len(refs) < 2
            or any(not isinstance(ref, str) or not ref for ref in refs)
        ):
            raise ValueError("objective group question_refs are malformed")
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate member in objective group")
        if any(ref in memberships for ref in refs):
            raise ValueError("multiple objective groups contain the same member")
        memberships.update(refs)
        if external_id in refs:
            matches.append(dict(group))

    if len(matches) > 1:
        raise ValueError("multiple objective groups contain the same member")
    if not matches:
        return None
    selected = matches[0]
    if selected.get("publish_mode") != "all_at_once":
        raise ValueError("unsupported objective group publish_mode")
    return copy.deepcopy(selected)


def current_real_question_payload(
    source_payload: Mapping[str, Any], question: Any
) -> dict[str, Any]:
    """Keep imported relations while taking verified question content from SQLite.

    The import remains authoritative for group membership, materials, source number,
    and answer requirements. Once a member is verified, its editable question fields
    must come from the current persisted ``RealQuestion`` row instead of the older
    hash-bound import snapshot.
    """
    payload = copy.deepcopy(dict(source_payload))
    payload.update(
        {
            "subjects": [str(question.subject)],
            "question_type": str(question.question_type),
            "stem_blocks": str(question.stem),
            "options": copy.deepcopy(question.options),
            "answer": copy.deepcopy(question.answer),
            "answer_status": "provided"
            if question.answer is not None
            else "unresolved",
            "answer_source": str(question.answer_source),
            "verification_status": str(question.verification_status),
            "content_hash": str(question.content_hash),
            "explanation_blocks": [],
            "explanation": str(question.explanation or ""),
            "source_name": str(question.source_name),
            "exam_name": str(question.exam_name),
            "exam_year": str(question.exam_year),
            "exam_date": str(question.exam_date),
            "paper": str(question.paper),
            "question_number": str(question.question_number),
            "source_url": str(question.source_url),
            "source_locator": str(question.source_locator),
            "metadata": copy.deepcopy(question.metadata),
        }
    )
    return payload
