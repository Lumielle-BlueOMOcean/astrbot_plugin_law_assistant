from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from date_parser import parse_chinese_dates


def test_date_parser_assigns_kinds_and_publication_year() -> None:
    dates = parse_chinese_dates(
        "初赛投稿截止时间为3月26日18:00，复赛拟定于4月19日下午，决赛拟定于5月23日上午举行。",
        publication_year=2026,
        timezone_name="Asia/Shanghai",
    )

    assert [date.kind for date in dates] == [
        "submission_deadline",
        "semifinal",
        "final",
    ]
    assert dates[0].datetime == datetime(
        2026, 3, 26, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    assert dates[0].confirmed is True
    assert dates[0].evidence_text == "投稿截止时间为3月26日18:00"


def test_date_parser_handles_explicit_year_and_morning_afternoon() -> None:
    dates = parse_chinese_dates(
        "报名截止：2026年10月1日上午；活动时间 2026-10-15 14:30。",
        publication_year=2025,
        timezone_name="Asia/Shanghai",
    )

    assert dates[0].datetime == datetime(
        2026, 10, 1, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    assert dates[1].datetime == datetime(
        2026, 10, 15, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai")
    )
