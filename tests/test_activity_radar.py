from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from activity_radar import (
    canonical_event_key,
    derive_radar_status,
    radar_policy_for,
)
from models import EventDate, LegalEvent, SourceDocument
from service import LawAssistantService
from storage import SQLiteStorage
from tests.fakes import FakeAdapter, FakeExtractor, RecordingPublisher, make_event

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


@pytest.mark.asyncio
async def test_radar_override_and_date_review_are_confirmed_audited_and_evidence_safe(
    tmp_path,
):
    from dataclasses import replace

    from models import EventDate

    now = datetime(2026, 9, 20, 9, 0, tzinfo=ZONE)
    storage = SQLiteStorage(tmp_path / "radar-review.sqlite3")
    event = _event()
    event.dates = (
        EventDate(
            kind="registration_deadline",
            datetime=datetime(2026, 9, 30, tzinfo=ZONE),
            timezone="Asia/Shanghai",
            label="报名截止",
            evidence_text="报名截止：9月30日",
            confirmed=False,
            precision="date",
        ),
    )
    event_id = storage.upsert_event(event)
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai", operator_ids=["operator-1"]),
        clock=lambda: now,
    )
    stored = storage.get_event(event_id)
    date_id = stored.dates[0].id

    status_preview = await service.prepare_event_status_override(
        event_id, "current", "人工复核发现活动仍在报名", actor_id="operator-1"
    )
    assert status_preview["ready"] is True
    assert storage.get_event_status_override(event_id) is None
    denied_status = await service.confirm_event_status_override(
        status_preview["token"], actor_id="ordinary-user"
    )
    applied_status = await service.confirm_event_status_override(
        status_preview["token"], actor_id="operator-1"
    )
    assert denied_status["success"] is False
    assert applied_status["success"] is True
    assert (await service.get_event(event_id)).metadata["radar_status"] == "current"

    date_preview = await service.prepare_event_date_review(
        event_id,
        date_id,
        "2026-10-01",
        "仅修正人工录入日期，原文仍未核实年份",
        actor_id="operator-1",
    )
    assert date_preview["ready"] is True
    before_confirm = storage.get_event(event_id)
    assert before_confirm.dates[0].datetime.date().isoformat() == "2026-09-30"
    assert before_confirm.dates[0].confirmed is False

    stale_event = replace(event, raw_content_hash="changed-source-hash")
    storage.upsert_event(stale_event)
    rejected_stale = await service.confirm_event_date_review(
        date_preview["token"], actor_id="operator-1"
    )
    assert rejected_stale["success"] is False
    assert storage.list_event_date_reviews() == []

    fresh_event = storage.get_event(event_id)
    fresh_preview = await service.prepare_event_date_review(
        event_id,
        fresh_event.dates[0].id,
        "2026-10-01",
        "复核记录保留原始证据且不自动确认日期",
        actor_id="operator-1",
    )
    applied_review = await service.confirm_event_date_review(
        fresh_preview["token"], actor_id="operator-1"
    )
    final_event = storage.get_event(event_id)
    review_history = storage.list_event_date_reviews()

    assert applied_review["success"] is True
    assert final_event.raw_content_hash == "changed-source-hash"
    assert final_event.dates[0].datetime.date().isoformat() == "2026-10-01"
    assert final_event.dates[0].confirmed is False
    assert review_history[0]["review_status"] == "accepted"
    assert review_history[0]["actor_id"] == "operator-1"
    assert review_history[0]["evidence_text_snapshot"] == "报名截止：9月30日"
    assert review_history[0]["evidence_hash"] != ""
    storage.close()


@pytest.mark.asyncio
async def test_status_override_does_not_confirm_deadline_but_explicit_date_review_does(
    tmp_path,
):
    from dataclasses import replace

    now = datetime(2026, 9, 20, 9, 0, tzinfo=ZONE)
    storage = SQLiteStorage(tmp_path / "radar-explicit-date-confirmation.sqlite3")
    inferred = replace(
        _event(deadline=datetime(2026, 9, 30, tzinfo=ZONE)),
        dates=(
            EventDate(
                kind="registration_deadline",
                datetime=datetime(2026, 9, 30, tzinfo=ZONE),
                timezone="Asia/Shanghai",
                label="报名截止",
                evidence_text="报名截止：9月30日",
                confirmed=False,
                precision="date",
            ),
        ),
    )
    event_id = storage.upsert_event(inferred)
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai", operator_ids=["operator-1"]),
        clock=lambda: now,
    )
    event = storage.get_event(event_id)

    status = await service.prepare_event_status_override(
        event_id, "current", "只调整展示状态", actor_id="operator-1"
    )
    assert status["ready"] is True
    assert (
        await service.confirm_event_status_override(
            status["token"], actor_id="operator-1"
        )
    )["success"] is True
    assert storage.list_deadlines(now=now) == []

    accepted = await service.prepare_event_date_review(
        event_id,
        event.dates[0].id,
        "2026-10-01",
        "人工核实并确认截止日期证据",
        actor_id="operator-1",
        proposed_confirmed=True,
    )
    assert accepted["ready"] is True
    assert accepted["old_value"]["confirmed"] is False
    assert accepted["proposed_value"]["confirmed"] is True
    assert storage.list_deadlines(now=now) == []
    confirmed = await service.confirm_event_date_review(
        accepted["token"], actor_id="operator-1"
    )
    assert confirmed["success"] is True
    assert storage.get_event(event_id).dates[0].confirmed is True
    assert len(storage.list_deadlines(now=now)) == 1
    audit = storage.list_event_date_reviews()[0]
    assert audit["proposed_value"]["confirmed"] is True
    assert audit["actor_id"] == "operator-1"
    assert audit["reason"] == "人工核实并确认截止日期证据"

    rejected_event = replace(
        inferred,
        source_key="synthetic-rejected",
        source_item_key="event-rejected",
    )
    rejected_id = storage.upsert_event(rejected_event)
    rejected = await service.prepare_event_date_review(
        rejected_id,
        storage.get_event(rejected_id).dates[0].id,
        "2026-10-02",
        "拒绝推断日期",
        actor_id="operator-1",
        decision="rejected",
        proposed_confirmed=True,
    )
    assert rejected["ready"] is True
    assert rejected["proposed_value"]["confirmed"] is False
    rejection = await service.confirm_event_date_review(
        rejected["token"], actor_id="operator-1"
    )
    assert rejection["success"] is True
    assert storage.get_event(rejected_id).dates[0].confirmed is False
    assert len(storage.list_deadlines(now=now)) == 1
    storage.close()


@pytest.mark.asyncio
async def test_ignored_radar_override_is_counted_filtered_and_clears_to_derived(
    tmp_path,
):
    now = datetime(2026, 9, 20, 9, 0, tzinfo=ZONE)
    storage = SQLiteStorage(tmp_path / "ignored-radar.sqlite3")
    event_id = storage.upsert_event(
        _event(deadline=datetime(2026, 9, 30, 18, tzinfo=ZONE))
    )
    service = LawAssistantService(
        storage,
        config=SimpleNamespace(timezone="Asia/Shanghai", operator_ids=["operator-1"]),
        clock=lambda: now,
    )

    prepared = await service.prepare_event_status_override(
        event_id, "ignored", "重复归档", actor_id="operator-1"
    )
    applied = await service.confirm_event_status_override(
        prepared["token"], actor_id="operator-1"
    )
    assert applied["success"] is True

    overview = await service.dashboard_overview()
    assert overview["radar"]["current"] == 0
    assert overview["radar"]["ignored"] == 1
    ignored = await service.list_events(radar_status="ignored")
    assert [event.id for event in ignored] == [event_id]

    clear = await service.prepare_event_status_override(
        event_id, "derived", "恢复证据推导", actor_id="operator-1"
    )
    cleared = await service.confirm_event_status_override(
        clear["token"], actor_id="operator-1"
    )
    assert cleared["success"] is True
    assert storage.get_event_status_override(event_id) is None
    assert (await service.get_event(event_id)).metadata["radar_status"] == "current"
    assert [event.id for event in await service.list_events()] == [event_id]
    storage.close()


@pytest.mark.asyncio
async def test_scan_skips_sources_with_discovery_disabled_and_reports_them(tmp_path):
    disabled = FakeAdapter(
        "extra:disabled",
        [
            SourceDocument(
                source_key="extra:disabled",
                source_item_key="event-1",
                url="https://example.test/disabled",
                title="Disabled source",
                content="should not be fetched",
                fetched_at="2026-09-21T00:00:00+00:00",
            )
        ],
    )
    enabled = FakeAdapter(
        "extra:enabled",
        [
            SourceDocument(
                source_key="extra:enabled",
                source_item_key="event-2",
                url="https://example.test/enabled",
                title="Enabled source",
                content="can be fetched",
                fetched_at="2026-09-21T00:00:00+00:00",
            )
        ],
    )
    config = SimpleNamespace(
        radar_auto_publish_sources=(),
        auto_publish_events=True,
        radar_source_policies=(
            {
                "key": "extra:disabled",
                "discover_enabled": False,
                "auto_publish_enabled": True,
            },
            {
                "key": "extra:enabled",
                "discover_enabled": True,
                "auto_publish_enabled": False,
            },
        ),
    )
    service = LawAssistantService(
        SQLiteStorage(tmp_path / "runtime.sqlite3"),
        sources=[
            (
                disabled,
                FakeExtractor(
                    {
                        "event-1": [
                            make_event(
                                source_key="extra:disabled",
                                source_item_key="event-1",
                            )
                        ]
                    }
                ),
            ),
            (
                enabled,
                FakeExtractor(
                    {
                        "event-2": [
                            make_event(
                                source_key="extra:enabled",
                                source_item_key="event-2",
                            )
                        ]
                    }
                ),
            ),
        ],
        config=config,
        publisher=RecordingPublisher(),
    )

    result = await service.scan_events()

    assert disabled.started.is_set() is False
    assert enabled.started.is_set() is True
    assert result.disabled_sources == ("extra:disabled",)
    assert result.discovered_count == 1
    assert service.storage.get_event_by_key("extra:disabled", "event-1") is None
    assert service.storage.get_event_by_key("extra:enabled", "event-2") is not None
    assert service.publisher.calls == []


@pytest.mark.asyncio
async def test_disabled_source_does_not_remove_existing_history(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.sqlite3")
    existing = make_event(source_key="extra:disabled", source_item_key="event-1")
    storage.upsert_event(existing)
    adapter = FakeAdapter("extra:disabled", error=RuntimeError("must not fetch"))
    service = LawAssistantService(
        storage,
        sources=[(adapter, FakeExtractor())],
        config=SimpleNamespace(
            radar_auto_publish_sources=(),
            radar_source_policies=(
                {"key": "extra:disabled", "discover_enabled": False},
            ),
        ),
    )

    result = await service.scan_events()

    assert result.failures == ()
    assert storage.get_event_by_key("extra:disabled", "event-1") is not None
