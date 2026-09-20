from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

try:
    from .content import QUESTION_TYPE_LABELS
    from .daily_plans import DailyPlan
except ImportError:
    from content import QUESTION_TYPE_LABELS
    from daily_plans import DailyPlan


DEFAULT_RANDOM_SUBJECTS = (
    "criminal_law",
    "civil_commercial",
    "intellectual_property",
    "economic_law",
    "judicial_practice",
)


def resolve_daily_constraints(
    plan: DailyPlan,
    current_date: date,
    *,
    target_id: int | None,
    content_type: str,
) -> dict[str, Any]:
    """Resolve both independent plan axes for one local calendar date."""
    subject = plan.subject_for(current_date)
    if plan.selection_mode == "random":
        subject = _stable_choice(
            DEFAULT_RANDOM_SUBJECTS,
            current_date.isoformat(),
            str(target_id or 0),
            content_type,
            "subject",
        )
    question_type = None
    question_type_mode = "random"
    if content_type == "daily_question":
        question_type_mode = plan.question_type_selection_mode
        if question_type_mode == "fixed":
            question_type = plan.fixed_question_type or plan.question_type
        elif question_type_mode == "rotation":
            question_type = _rotation_value(
                plan.rotation_question_types,
                plan.question_type_rotation_start_date,
                plan.question_type_rotation_start_index,
                current_date,
            )
        else:
            question_type = _stable_choice(
                tuple(QUESTION_TYPE_LABELS),
                current_date.isoformat(),
                str(target_id or 0),
                content_type,
                "question_type",
            )
    return {
        "subject": subject,
        "subject_mode": plan.selection_mode,
        "question_type": question_type,
        "question_type_mode": question_type_mode,
        "origin": plan.question_origin if content_type == "daily_question" else None,
    }


def _rotation_value(
    values: tuple[str, ...],
    start_date: str | None,
    start_index: int,
    current_date: date,
) -> str | None:
    if not values or not start_date:
        return None
    try:
        anchor = date.fromisoformat(start_date)
    except ValueError:
        return None
    elapsed_days = (current_date - anchor).days
    if elapsed_days < 0:
        return None
    return values[(start_index + elapsed_days) % len(values)]


def _stable_choice(values: tuple[str, ...], *parts: str) -> str | None:
    if not values:
        return None
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return values[int.from_bytes(digest[:8], "big") % len(values)]


__all__ = ["DEFAULT_RANDOM_SUBJECTS", "resolve_daily_constraints"]
