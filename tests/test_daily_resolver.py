from __future__ import annotations

from datetime import date, timedelta

from daily_plans import DailyPlan
from daily_resolver import resolve_daily_constraints


def test_subject_and_question_type_rotations_use_independent_calendar_axes():
    plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "enabled": True,
            "selection_mode": "rotation",
            "rotation_subjects": [
                "知识产权",
                "民商法",
                "经济法",
                "司法实务",
            ],
            "rotation_start_date": "2026-09-21",
            "rotation_start_index": 1,
            "question_type_selection_mode": "rotation",
            "rotation_question_types": ["单选", "多选", "判断"],
            "question_type_rotation_start_date": "2026-09-22",
            "question_type_rotation_start_index": 2,
            "question_origin": "real",
        },
    )

    resolved = [
        resolve_daily_constraints(
            plan,
            date(2026, 9, 21) + timedelta(days=offset),
            target_id=7,
            content_type="daily_question",
        )
        for offset in range(12)
    ]

    assert [item["subject"] for item in resolved[:4]] == [
        "civil_commercial",
        "economic_law",
        "judicial_practice",
        "intellectual_property",
    ]
    assert [item["question_type"] for item in resolved[:4]] == [
        None,
        "true_false",
        "single_choice",
        "multiple_choice",
    ]
    assert all(item["origin"] == "real" for item in resolved)
    assert resolved[0]["question_type_mode"] == "rotation"
    assert resolved[0]["subject_mode"] == "rotation"


def test_question_type_fixed_and_old_question_type_remains_compatible():
    plan = DailyPlan.from_mapping(
        "daily_question",
        {"question_type": "多选", "question_origin": "mock"},
    )

    resolved = resolve_daily_constraints(
        plan, date(2026, 9, 21), target_id=1, content_type="daily_question"
    )

    assert resolved["question_type"] == "multiple_choice"
    assert resolved["question_type_mode"] == "fixed"
    assert resolved["origin"] == "mock"


def test_pre_start_rotation_is_not_resolved_and_preview_does_not_generate_content():
    plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "selection_mode": "rotation",
            "rotation_subjects": ["刑法"],
            "rotation_start_date": "2026-09-22",
            "question_type_selection_mode": "rotation",
            "rotation_question_types": ["单选"],
            "question_type_rotation_start_date": "2026-09-22",
        },
    )

    resolved = resolve_daily_constraints(
        plan, date(2026, 9, 21), target_id=1, content_type="daily_question"
    )
    preview = plan.preview(date(2026, 9, 21), 2, target_id=1)

    assert resolved["subject"] is None
    assert resolved["question_type"] is None
    assert preview[0]["subject"] is None
    assert preview[0]["question_type"] is None
    assert "generated_content" not in preview[0]
