from __future__ import annotations

from config import DEFAULT_TIMEZONE, PluginConfig


def test_config_defaults_are_safe() -> None:
    config = PluginConfig.from_mapping({})

    assert config.operator_ids == ()
    assert config.timezone == DEFAULT_TIMEZONE
    assert config.auto_scan_enabled is False
    assert config.scan_interval_minutes == 60


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
