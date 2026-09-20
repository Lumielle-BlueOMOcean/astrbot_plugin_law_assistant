from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from activity_radar import (
    canonical_event_key,
    derive_radar_status,
    radar_policy_for,
)
from models import EventDate, LegalEvent

ZONE = ZoneInfo("Asia/Shanghai")


def _event(
    *, deadline: datetime | None = None, published: datetime | None = None, **metadata
):
    dates = ()
    if deadline is not None:
        dates = (
            EventDate(
                kind="registration_deadline",
                datetime=deadline,
                timezone="Asia/Shanghai",
                label="报名截止",
                evidence_text="报名截止：2026年9月30日",
                confirmed=True,
            ),
        )
    now = datetime(2026, 9, 20, 9, 0, tzinfo=ZONE)
    return LegalEvent(
        source_key="china_jm",
        source_item_key="event-1",
        title="法律硕士案例竞赛",
        source_url="https://example.test/event-1",
        organizer="某大学法学院",
        event_type="competition",
        eligibility="法律硕士研究生",
        status="OPEN",
        raw_content_hash="hash",
        discovered_at=now,
        updated_at=now,
        source_published_at=published,
        summary="现接受报名，报名方式见公告。",
        registration_method="在线报名",
        dates=dates,
        metadata={
            "participation_evidence": True,
            "historical_signals": [],
            **metadata,
        },
    )


def test_confirmed_future_deadline_is_current_and_expiry_is_historical():
    event = _event(deadline=datetime(2026, 9, 30, 18, tzinfo=ZONE))

    assert derive_radar_status(event, datetime(2026, 9, 20, tzinfo=ZONE)) == "current"
    assert (
        derive_radar_status(event, datetime(2026, 10, 1, tzinfo=ZONE)) == "historical"
    )


def test_old_notice_first_discovered_today_is_historical():
    event = _event(
        published=datetime(2019, 5, 1, tzinfo=ZONE),
        historical_signals=["往届赛事回顾"],
        participation_evidence=False,
    )

    assert (
        derive_radar_status(event, datetime(2026, 9, 20, tzinfo=ZONE)) == "historical"
    )


def test_missing_confirmed_evidence_is_needs_review():
    event = _event(participation_evidence=False)

    assert (
        derive_radar_status(event, datetime(2026, 9, 20, tzinfo=ZONE)) == "needs_review"
    )


def test_canonical_key_requires_strong_matching_fields():
    first = _event(deadline=datetime(2026, 9, 30, 18, tzinfo=ZONE))
    second = _event(deadline=datetime(2026, 9, 30, 18, tzinfo=ZONE))
    second.source_item_key = "other-source-item"
    second.source_key = "extra:1"

    assert canonical_event_key(first) == canonical_event_key(second)
    assert canonical_event_key(_event()) is None


def test_source_policy_disables_extra_source_auto_publish_by_default():
    config = SimpleNamespace(
        radar_auto_publish_sources=(),
        radar_source_policies=(),
    )

    china = radar_policy_for("china_jm", config)
    extra = radar_policy_for("extra:1", config)

    assert china.discover_enabled is True
    assert china.auto_publish_enabled is True
    assert extra.discover_enabled is True
    assert extra.auto_publish_enabled is False


def test_configured_source_can_be_allowed_to_auto_publish():
    config = SimpleNamespace(
        radar_auto_publish_sources=("extra:1",),
        radar_source_policies=(),
    )

    assert radar_policy_for("extra:1", config).auto_publish_enabled is True
