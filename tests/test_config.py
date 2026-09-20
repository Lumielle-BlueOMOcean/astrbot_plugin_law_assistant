from __future__ import annotations

from config import DEFAULT_TIMEZONE, PluginConfig


def test_config_defaults_are_safe() -> None:
    config = PluginConfig.from_mapping({})

    assert config.operator_ids == ()
    assert config.timezone == DEFAULT_TIMEZONE
    assert config.auto_scan_enabled is False
    assert config.scan_interval_minutes == 60
    assert config.auto_publish_events is False
    assert config.deadline_reminder_days == (7, 3, 1)
    assert config.daily_case_enabled is False
    assert config.daily_question_enabled is False
    assert config.daily_case_selection_mode == "random"
    assert config.daily_question_origin == "random"
    assert config.daily_question_type is None
    assert config.daily_question_type_selection_mode == "random"
    assert config.daily_question_fixed_type is None
    assert config.daily_question_rotation_types == ()
    assert config.case_source_court_enabled is True
    assert config.case_source_spp_enabled is True


def test_config_normalizes_operator_ids_without_duplicates() -> None:
    config = PluginConfig.from_mapping(
        {"operator_ids": [" 123 ", 456, "123", "", None]},
    )

    assert config.operator_ids == ("123", "456")


def test_config_clamps_interval_and_falls_back_for_invalid_values() -> None:
    assert (
        PluginConfig.from_mapping({"scan_interval_minutes": 1}).scan_interval_minutes
        == 5
    )
    assert (
        PluginConfig.from_mapping({"scan_interval_minutes": 5000}).scan_interval_minutes
        == 1440
    )
    assert (
        PluginConfig.from_mapping(
            {"scan_interval_minutes": "never"}
        ).scan_interval_minutes
        == 60
    )


def test_config_uses_default_timezone_for_unknown_timezone() -> None:
    config = PluginConfig.from_mapping({"timezone": "Not/AZone"})

    assert config.timezone == DEFAULT_TIMEZONE


def test_config_normalizes_extra_sources_and_reminder_days() -> None:
    config = PluginConfig.from_mapping(
        {
            "extra_event_source_urls": [
                " https://example.test/ ",
                "https://example.test/",
            ],
            "deadline_reminder_days": [7, "3", 0, -1, "bad"],
            "daily_case_time": " 09:30 ",
        }
    )

    assert config.extra_event_source_urls == ("https://example.test/",)
    assert config.deadline_reminder_days == (7, 3)
    assert config.daily_case_time == "09:30"


def test_config_normalizes_daily_plan_subjects_and_dates() -> None:
    config = PluginConfig.from_mapping(
        {
            "daily_case_selection_mode": "rotation",
            "daily_case_rotation_subjects": ["知产", "民商法", "unknown"],
            "daily_case_rotation_start_date": "2026-09-21",
            "daily_question_origin": "real",
            "daily_question_type": "多选",
            "daily_question_subject": "经济法",
        }
    )

    assert config.daily_case_rotation_subjects == (
        "intellectual_property",
        "civil_commercial",
    )
    assert config.daily_case_rotation_start_date == "2026-09-21"
    assert config.daily_question_origin == "real"
    assert config.daily_question_type == "multiple_choice"
    assert config.daily_question_subject == "economic_law"


def test_config_keeps_missing_rotation_anchor_unset_for_persistence_layer() -> None:
    config = PluginConfig.from_mapping(
        {
            "daily_question_enabled": True,
            "daily_question_selection_mode": "rotation",
            "daily_question_rotation_subjects": ["刑法", "民商法"],
        }
    )

    assert config.daily_question_rotation_start_date is None


def test_config_normalizes_independent_question_type_rotation() -> None:
    config = PluginConfig.from_mapping(
        {
            "daily_question_type_selection_mode": "rotation",
            "daily_question_rotation_types": ["单选", "多选", "unknown"],
            "daily_question_type_rotation_start_date": "2026-09-21",
            "daily_question_type_rotation_start_index": 2,
        }
    )

    assert config.daily_question_type_selection_mode == "rotation"
    assert config.daily_question_rotation_types == (
        "single_choice",
        "multiple_choice",
    )
    assert config.daily_question_type_rotation_start_date == "2026-09-21"
    assert config.daily_question_type_rotation_start_index == 2
