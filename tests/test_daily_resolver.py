from __future__ import annotations

import importlib
import importlib.util
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


def test_indefinite_choice_flows_through_fixed_and_rotation_daily_plans():
    fixed_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "question_type_selection_mode": "fixed",
            "fixed_question_type": "不定项选择题",
        },
    )
    fixed = resolve_daily_constraints(
        fixed_plan, date(2026, 9, 27), target_id=1, content_type="daily_question"
    )
    rotation_plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "question_type_selection_mode": "rotation",
            "rotation_question_types": ["indefinite_choice", "single_choice"],
            "question_type_rotation_start_date": "2026-09-27",
        },
    )
    rotating = resolve_daily_constraints(
        rotation_plan,
        date(2026, 9, 27),
        target_id=1,
        content_type="daily_question",
    )

    assert fixed["question_type"] == "indefinite_choice"
    assert rotating["question_type"] == "indefinite_choice"


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


def test_case_plan_does_not_serialize_question_reveal_controls():
    plan = DailyPlan.from_mapping(
        "daily_case",
        {"question_reveal_mode": "delayed", "answer_reveal_delay_minutes": 30},
    )

    serialized = plan.to_mapping()

    assert plan.question_reveal_mode == "manual"
    assert plan.answer_reveal_delay_minutes == 0
    assert "question_reveal_mode" not in serialized
    assert "answer_reveal_delay_minutes" not in serialized
    assert "explanation_reveal_delay_minutes" not in serialized


def test_daily_question_reveal_policy_is_validated_and_round_trips():
    plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "question_reveal_mode": "delayed",
            "answer_reveal_delay_minutes": 15,
            "explanation_reveal_delay_minutes": 30,
        },
    )

    assert plan.to_mapping()["question_reveal_mode"] == "delayed"
    assert plan.to_mapping()["answer_reveal_delay_minutes"] == 15
    assert plan.to_mapping()["explanation_reveal_delay_minutes"] == 30

    for changes in (
        {"question_reveal_mode": "automatic-ish"},
        {"answer_reveal_delay_minutes": -1},
        {"explanation_reveal_delay_minutes": 10081},
        {
            "question_reveal_mode": "delayed",
            "answer_reveal_delay_minutes": 240,
            "explanation_reveal_delay_minutes": 10,
        },
        {
            "question_reveal_mode": "delayed",
            "answer_reveal_delay_minutes": 0,
            "explanation_reveal_delay_minutes": 10,
        },
    ):
        try:
            DailyPlan.from_mapping("daily_question", changes)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid reveal policy accepted: {changes!r}")


def test_manual_question_reveal_ignores_configured_delays():
    plan = DailyPlan.from_mapping(
        "daily_question",
        {
            "question_reveal_mode": "manual",
            "answer_reveal_delay_minutes": 240,
            "explanation_reveal_delay_minutes": 10,
        },
    )

    assert plan.answer_reveal_delay_minutes == 0
    assert plan.explanation_reveal_delay_minutes == 0


def test_due_time_resolver_uses_local_wall_time_and_handles_dst_gap_and_fold():
    spec = importlib.util.find_spec("daily_timing")
    assert spec is not None, "due-time resolver must be available"
    if spec is None:
        return
    timing = importlib.import_module("daily_timing")

    before_due = timing.next_daily_due(
        "2026-09-29T07:59:00+08:00", "08:00", "Asia/Shanghai"
    )
    at_due = timing.next_daily_due(
        "2026-09-29T08:00:00+08:00", "08:00", "Asia/Shanghai"
    )
    spring_gap = timing.local_due_datetime(
        date(2026, 3, 8), "02:30", "America/New_York"
    )
    fall_fold = timing.local_due_datetime(
        date(2026, 11, 1), "01:30", "America/New_York"
    )

    assert before_due.isoformat() == "2026-09-29T08:00:00+08:00"
    assert at_due.isoformat() == "2026-09-30T08:00:00+08:00"
    assert (spring_gap.hour, spring_gap.minute) == (3, 0)
    assert spring_gap.fold == 0
    assert fall_fold.fold == 0
