from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from extraction import EventExtractor
from models import SourceDocument

FIXTURES = Path(__file__).parent / "fixtures"


def source_document(name: str, title: str | None = None) -> SourceDocument:
    return SourceDocument(
        source_key="china_jm",
        source_item_key="771",
        url="https://china-jm.org/article/?id=771",
        title=title or "关于举办第九届（2026）法律专业学位研究生法律文书写作大赛的通知",
        content=(FIXTURES / name).read_text(encoding="utf-8"),
        fetched_at="2026-09-17T00:00:00+08:00",
    )


@pytest.mark.asyncio
async def test_rules_first_extraction_builds_event_and_confirmed_dates() -> None:
    extractor = EventExtractor(
        timezone_name="Asia/Shanghai",
        now=lambda: datetime(2026, 3, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    events = await extractor.extract(source_document("china_jm_notice.html"))

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "competition"
    assert "厦门大学" in event.organizer
    assert "法律硕士" in event.eligibility
    assert event.registration_method
    assert event.dates[0].kind == "submission_deadline"
    assert event.dates[0].confirmed is True


@pytest.mark.asyncio
async def test_irrelevant_notice_is_skipped_without_llm() -> None:
    document = source_document(
        "generic_notice.html", title="关于校园绿化维护安排的通知"
    )
    extractor = EventExtractor(timezone_name="Asia/Shanghai")

    events = await extractor.extract(document)

    assert events == []


@pytest.mark.asyncio
async def test_llm_date_without_matching_evidence_is_unconfirmed() -> None:
    class FakeLLM:
        async def generate_json(
            self, prompt: str, *, session_origin: str | None = None
        ):
            return {
                "relevant": True,
                "event_type": "competition",
                "dates": [
                    {
                        "kind": "registration_deadline",
                        "value": "2026-12-31T18:00:00+08:00",
                        "label": "报名截止",
                        "evidence": "原文没有这一天",
                    }
                ],
            }

    extractor = EventExtractor(llm_service=FakeLLM(), timezone_name="Asia/Shanghai")

    events = await extractor.extract(source_document("china_jm_notice.html"))

    assert len(events) == 1
    llm_date = next(
        date for date in events[0].dates if date.kind == "registration_deadline"
    )
    assert llm_date.confirmed is False
