from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

if __package__ and "." in __package__:
    from .content import (
        normalize_origin,
        normalize_question_type,
        normalize_selection_mode,
        normalize_subject,
    )
else:
    from content import (
        normalize_origin,
        normalize_question_type,
        normalize_selection_mode,
        normalize_subject,
    )

SCHOOL_ROTATION_SUBJECTS = (
    "intellectual_property",
    "civil_commercial",
    "judicial_practice",
    "economic_law",
)


@dataclass(frozen=True, slots=True)
class DailyPlan:
    content_type: str
    enabled: bool = False
    time: str = "08:00"
    selection_mode: str = "random"
    fixed_subject: str | None = None
    rotation_subjects: tuple[str, ...] = field(default_factory=tuple)
    rotation_start_date: str | None = None
    rotation_start_index: int = 0
    question_origin: str = "random"
    question_type: str | None = None

    def __post_init__(self) -> None:
        if self.content_type not in {"daily_case", "daily_question"}:
            raise ValueError(f"unsupported daily content type: {self.content_type}")

    @classmethod
    def from_mapping(
        cls, content_type: str, raw: dict[str, Any] | None = None
    ) -> DailyPlan:
        values = raw or {}
        raw_subjects = values.get("rotation_subjects", [])
        if isinstance(raw_subjects, str) and raw_subjects.strip().lower() in {
            "school",
            "school_four",
            "学校四方向",
            "学校预设",
        }:
            raw_subjects = SCHOOL_ROTATION_SUBJECTS
        if not isinstance(raw_subjects, (list, tuple, set)):
            raw_subjects = ()
        subjects = tuple(
            item
            for item in (normalize_subject(value) for value in raw_subjects)
            if item is not None
        )
        fixed_subject = normalize_subject(
            values.get("fixed_subject", values.get("subject"))
        )
        selection_mode = normalize_selection_mode(
            values.get("selection_mode", "random")
        )
        if selection_mode == "rotation" and not subjects and fixed_subject:
            subjects = (fixed_subject,)
        return cls(
            content_type=content_type,
            enabled=bool(values.get("enabled", False)),
            time=str(values.get("time", "08:00") or "08:00"),
            selection_mode=selection_mode,
            fixed_subject=fixed_subject,
            rotation_subjects=subjects,
            rotation_start_date=(
                str(values["rotation_start_date"]).strip()
                if values.get("rotation_start_date")
                else None
            ),
            rotation_start_index=max(
                0, int(values.get("rotation_start_index", 0) or 0)
            ),
            question_origin=normalize_origin(values.get("question_origin", "random")),
            question_type=normalize_question_type(values.get("question_type")),
        )

    def subject_for(self, current_date: date) -> str | None:
        if self.selection_mode == "random":
            return None
        if self.selection_mode == "fixed":
            return self.fixed_subject
        if not self.rotation_subjects or not self.rotation_start_date:
            return None
        try:
            start = date.fromisoformat(self.rotation_start_date)
        except ValueError:
            return None
        elapsed_days = (current_date - start).days
        if elapsed_days < 0:
            return None
        index = (self.rotation_start_index + elapsed_days) % len(self.rotation_subjects)
        return self.rotation_subjects[index]

    def preview(self, start_date: date, days: int = 7) -> list[dict[str, Any]]:
        count = max(1, min(days, 31))
        return [
            {
                "date": (
                    start_date.fromordinal(start_date.toordinal() + offset)
                ).isoformat(),
                "subject": self.subject_for(
                    start_date.fromordinal(start_date.toordinal() + offset)
                ),
            }
            for offset in range(count)
        ]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "content_type": self.content_type,
            "enabled": self.enabled,
            "time": self.time,
            "selection_mode": self.selection_mode,
            "fixed_subject": self.fixed_subject,
            "rotation_subjects": list(self.rotation_subjects),
            "rotation_start_date": self.rotation_start_date,
            "rotation_start_index": self.rotation_start_index,
            "question_origin": self.question_origin,
            "question_type": self.question_type,
        }


__all__ = ["SCHOOL_ROTATION_SUBJECTS", "DailyPlan"]
