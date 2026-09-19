from __future__ import annotations

from datetime import date

import pytest

from content import (
    QuestionRequest,
    normalize_question_type,
    normalize_subject,
    subject_matches,
)
from daily_plans import SCHOOL_ROTATION_SUBJECTS, DailyPlan


def test_subject_aliases_and_question_types_are_canonical() -> None:
    assert normalize_subject("知产") == "intellectual_property"
    assert normalize_subject("民商法") == "civil_commercial"
    assert normalize_question_type("多选") == "multiple_choice"
    assert normalize_question_type("案例分析题") == "case_analysis"
    assert subject_matches(("civil_law",), "民商法") is True


def test_rotation_uses_calendar_date_and_returns_to_first_subject() -> None:
    plan = DailyPlan(
        content_type="daily_case",
        enabled=True,
        time="08:00",
        selection_mode="rotation",
        rotation_subjects=SCHOOL_ROTATION_SUBJECTS,
        rotation_start_date="2026-09-21",
    )

    assert plan.subject_for(date(2026, 9, 21)) == "intellectual_property"
    assert plan.subject_for(date(2026, 9, 24)) == "economic_law"
    assert plan.subject_for(date(2026, 9, 25)) == "intellectual_property"


@pytest.mark.parametrize(
    ("subjects", "expected"),
    [
        (("criminal_law", "civil_commercial"), "civil_commercial"),
        (
            ("intellectual_property", "economic_law", "judicial_practice"),
            "judicial_practice",
        ),
        (
            (
                "criminal_law",
                "civil_law",
                "commercial_law",
                "economic_law",
                "judicial_practice",
            ),
            "judicial_practice",
        ),
    ],
)
def test_rotation_supports_arbitrary_list_lengths(subjects, expected) -> None:
    plan = DailyPlan(
        content_type="daily_question",
        enabled=True,
        time="08:00",
        selection_mode="rotation",
        rotation_subjects=subjects,
        rotation_start_date="2026-01-01",
    )
    assert plan.subject_for(date(2026, 1, len(subjects))) == expected


def test_rotation_before_start_date_is_not_ready() -> None:
    plan = DailyPlan(
        content_type="daily_case",
        enabled=True,
        time="08:00",
        selection_mode="rotation",
        rotation_subjects=("criminal_law",),
        rotation_start_date="2026-09-21",
    )
    assert plan.subject_for(date(2026, 9, 20)) is None


def test_school_preset_can_be_selected_by_name() -> None:
    plan = DailyPlan.from_mapping(
        "daily_case",
        {
            "selection_mode": "rotation",
            "rotation_subjects": "学校四方向",
            "rotation_start_date": "2026-09-21",
        },
    )
    assert plan.rotation_subjects == SCHOOL_ROTATION_SUBJECTS


def test_question_request_keeps_real_constraints() -> None:
    request = QuestionRequest.from_values(
        origin="real", subject="刑事法", question_type="多选"
    )
    assert request.origin == "real"
    assert request.subject == "criminal_law"
    assert request.question_type == "multiple_choice"
