from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

if __package__ and "." in __package__:
    from .content import (
        parse_origin,
        parse_question_type,
        parse_selection_mode,
        parse_subject,
    )
else:
    from content import (
        parse_origin,
        parse_question_type,
        parse_selection_mode,
        parse_subject,
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
    question_type_selection_mode: str = "random"
    fixed_question_type: str | None = None
    rotation_question_types: tuple[str, ...] = field(default_factory=tuple)
    question_type_rotation_start_date: str | None = None
    question_type_rotation_start_index: int = 0

    def __post_init__(self) -> None:
        if self.content_type not in {"daily_case", "daily_question"}:
            raise ValueError(f"unsupported daily content type: {self.content_type}")

    @classmethod
    def from_mapping(
        cls,
        content_type: str,
        raw: dict[str, Any] | None = None,
        *,
        allow_unanchored_rotation: bool = False,
    ) -> DailyPlan:
        if content_type not in {"daily_case", "daily_question"}:
            raise ValueError(f"unsupported daily content type: {content_type}")
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
            raise TypeError("rotation_subjects 必须是至少包含一个方向的列表")
        subjects: list[str] = []
        for value in raw_subjects:
            subject = parse_subject(value)
            if subject and subject not in subjects:
                subjects.append(subject)
        fixed_subject = parse_subject(
            values.get("fixed_subject", values.get("subject"))
        )
        selection_mode = parse_selection_mode(values.get("selection_mode", "random"))
        if selection_mode == "rotation" and not subjects and fixed_subject:
            subjects = [fixed_subject]
        if selection_mode == "fixed" and fixed_subject is None:
            raise ValueError("fixed 选择模式必须提供 fixed_subject")
        if selection_mode == "rotation" and not subjects:
            raise ValueError("rotation 选择模式必须提供 rotation_subjects")
        start_date = (
            str(values["rotation_start_date"]).strip()
            if values.get("rotation_start_date")
            else None
        )
        if start_date is not None:
            try:
                start_date = date.fromisoformat(start_date).isoformat()
            except ValueError as exc:
                raise ValueError("rotation_start_date 必须是 YYYY-MM-DD") from exc
        if (
            selection_mode == "rotation"
            and start_date is None
            and not allow_unanchored_rotation
        ):
            raise ValueError("rotation 选择模式必须提供 rotation_start_date")
        try:
            start_index = int(values.get("rotation_start_index", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("rotation_start_index 必须是非负整数") from exc
        if start_index < 0:
            raise ValueError("rotation_start_index 必须是非负整数")
        question_type = parse_question_type(values.get("question_type"))
        raw_question_types = values.get(
            "rotation_question_types", values.get("question_types", [])
        )
        if not isinstance(raw_question_types, (list, tuple, set)):
            raise TypeError("rotation_question_types 必须是至少包含一个题型的列表")
        question_types: list[str] = []
        for value in raw_question_types:
            parsed_type = parse_question_type(value)
            if parsed_type and parsed_type not in question_types:
                question_types.append(parsed_type)
        raw_type_mode = values.get("question_type_selection_mode")
        if raw_type_mode is None:
            type_mode = "fixed" if question_type else "random"
        else:
            type_mode = parse_selection_mode(raw_type_mode)
        raw_fixed_question_type = values.get("fixed_question_type")
        if (
            type_mode == "fixed"
            and raw_fixed_question_type in (None, "")
            and question_type
        ):
            raw_fixed_question_type = question_type
        fixed_question_type = parse_question_type(raw_fixed_question_type)
        if type_mode == "fixed" and fixed_question_type is None:
            raise ValueError("fixed 题型模式必须提供 fixed_question_type")
        if type_mode == "rotation" and not question_types:
            raise ValueError("rotation 题型模式必须提供 rotation_question_types")
        type_start_date = (
            str(values["question_type_rotation_start_date"]).strip()
            if values.get("question_type_rotation_start_date")
            else None
        )
        if type_start_date is not None:
            try:
                type_start_date = date.fromisoformat(type_start_date).isoformat()
            except ValueError as exc:
                raise ValueError(
                    "question_type_rotation_start_date 必须是 YYYY-MM-DD"
                ) from exc
        if (
            type_mode == "rotation"
            and type_start_date is None
            and not allow_unanchored_rotation
        ):
            raise ValueError(
                "rotation 题型模式必须提供 question_type_rotation_start_date"
            )
        try:
            type_start_index = int(
                values.get("question_type_rotation_start_index", 0) or 0
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "question_type_rotation_start_index 必须是非负整数"
            ) from exc
        if type_start_index < 0:
            raise ValueError("question_type_rotation_start_index 必须是非负整数")
        return cls(
            content_type=content_type,
            enabled=bool(values.get("enabled", False)),
            time=str(values.get("time", "08:00") or "08:00"),
            selection_mode=selection_mode,
            fixed_subject=fixed_subject,
            rotation_subjects=tuple(subjects),
            rotation_start_date=start_date,
            rotation_start_index=start_index,
            question_origin=parse_origin(values.get("question_origin", "random")),
            question_type=question_type,
            question_type_selection_mode=type_mode,
            fixed_question_type=fixed_question_type,
            rotation_question_types=tuple(question_types),
            question_type_rotation_start_date=type_start_date,
            question_type_rotation_start_index=type_start_index,
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

    def preview(
        self, start_date: date, days: int = 7, *, target_id: int | None = None
    ) -> list[dict[str, Any]]:
        try:
            from .daily_resolver import resolve_daily_constraints
        except ImportError:
            from daily_resolver import resolve_daily_constraints

        count = max(1, min(days, 31))
        return [
            {
                "date": (
                    start_date.fromordinal(start_date.toordinal() + offset)
                ).isoformat(),
                **resolve_daily_constraints(
                    self,
                    start_date.fromordinal(start_date.toordinal() + offset),
                    target_id=target_id,
                    content_type=self.content_type,
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
            "question_type_selection_mode": self.question_type_selection_mode,
            "fixed_question_type": self.fixed_question_type,
            "rotation_question_types": list(self.rotation_question_types),
            "question_type_rotation_start_date": self.question_type_rotation_start_date,
            "question_type_rotation_start_index": self.question_type_rotation_start_index,
        }


__all__ = ["SCHOOL_ROTATION_SUBJECTS", "DailyPlan"]
