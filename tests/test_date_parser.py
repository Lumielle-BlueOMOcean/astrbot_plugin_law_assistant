from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from date_parser import extract_publication_year, parse_chinese_dates


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


def test_publication_year_uses_labeled_metadata_not_incidental_body_year():
    assert (
        extract_publication_year(
            "发布日期：2024年12月1日。本文引用2020年法规。", fallback=2026
        )
        == 2024
    )
    assert extract_publication_year("本公告提到2020年法规。", fallback=2026) == 2026


def test_month_day_without_trusted_publication_year_is_unconfirmed():
    dates = parse_chinese_dates(
        "报名截止：3月20日。正文提到2020年法规。",
        timezone_name="Asia/Shanghai",
    )

    assert len(dates) == 1
    assert dates[0].confirmed is False
