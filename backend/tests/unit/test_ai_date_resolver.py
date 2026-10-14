from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.ai.parsing import dates
from app.schemas.ai import AIChatDateResult


@pytest.mark.parametrize("message,expected,status", [
    ("Ngày mai", "2026-10-14", "RESOLVED"),
    ("Thứ hai tuần sau", "2026-10-19", "RESOLVED"),
    ("Ngày 10 tháng tới", "2026-11-10", "RESOLVED"),
    ("Ngày thứ hai của tuần đầu tiên trong tháng tới", "2026-11-02", "RESOLVED"),
    ("Thứ sáu cuối cùng của tháng này", "2026-10-30", "RESOLVED"),
    ("Ngày 31 tháng sau", None, "INVALID"),
    ("Ngày 15/11/2026", "2026-11-15", "RESOLVED"),
    ("Tuan dau tien thang sau", None, "AMBIGUOUS"),
    ("2028-02-29", "2028-02-29", "RESOLVED"),
    ("2027-02-29", None, "INVALID"),
])
def test_fixed_hcm_date_cases(message, expected, status):
    result = dates.resolve_rule_date(message, date(2026, 10, 13))
    assert result.status == status and result.value == expected


@pytest.mark.parametrize("today,message,expected", [
    (date(2026, 12, 29), "ngày 10 tháng sau", "2027-01-10"),
    (date(2027, 1, 2), "thứ hai tuần trước", "2026-12-21"),
    (date(2028, 1, 31), "ngày 29 tháng sau", "2028-02-29"),
])
def test_year_boundary_and_leap_year(today, message, expected):
    assert dates.resolve_rule_date(message, today).value == expected


@pytest.mark.asyncio
async def test_ambiguous_week_asks_without_inventing_day(monkeypatch):
    provider = AsyncMock(return_value=AIChatDateResult(date=None))
    monkeypatch.setattr(dates, "generate_json", provider)
    result = await dates.resolve_date("Một ngày nào đó tuần tới", date(2026, 10, 13), dates.DateContext("leave_date"))
    assert result.status == "AMBIGUOUS" and result.value is None
    provider.assert_not_awaited()


@pytest.mark.parametrize("message", [
    "Nghỉ từ 05/11 đến 07/11",
    "Nghỉ 05/11 hoặc 07/11",
    "Ngày 05/11/2026 và ngày 07/11/2026",
    "Từ ngày 5 đến ngày 7 tháng 11",
])
def test_single_date_parser_never_selects_one_endpoint(message):
    result = dates.resolve_rule_date(message, date(2026, 10, 13))
    assert result.status == "AMBIGUOUS"
    assert result.value is None
    assert result.reason_code == "MULTIPLE_DATES"


@pytest.mark.asyncio
async def test_ambiguous_range_does_not_call_provider(monkeypatch):
    provider = AsyncMock(side_effect=AssertionError("Range must not become a single date"))
    monkeypatch.setattr(dates, "generate_json", provider)
    result = await dates.resolve_date(
        "Nghỉ 05/11 hoặc 07/11", date(2026, 10, 13), dates.DateContext("leave_date")
    )
    assert result.status == "AMBIGUOUS" and result.value is None
    provider.assert_not_awaited()


def test_month_missing_is_not_invented():
    result = dates.resolve_rule_date("Mùng 5", date(2026, 10, 13))
    assert result.status == "AMBIGUOUS" and result.reason_code == "MONTH_REQUIRED"


@pytest.mark.parametrize("message", [
    "Nghỉ từ 5 đến 7/11",
    "Nghỉ từ ngày 5 tới ngày 7 tháng 11",
    "Nghỉ mùng 5 hoặc mùng 7 tháng 11",
    "Ngày 5 tháng sau hoặc 7",
    "Nghỉ thứ hai hoặc thứ ba tuần sau",
    "Nghỉ hôm nay hoặc ngày mai",
])
def test_more_natural_date_alternatives_cannot_select_one_day(message):
    result = dates.resolve_rule_date(message, date(2026, 10, 13))
    assert result.status == "AMBIGUOUS"
    assert result.reason_code == "MULTIPLE_DATES"
    assert result.value is None


@pytest.mark.parametrize("message", [
    "Nghỉ ngày 15/11/2026",
    "Nghỉ ngày 5 tháng sau",
    "Ngày thứ hai của tuần đầu tiên trong tháng tới",
])
def test_single_date_not_misclassified_as_range(message):
    assert dates.resolve_rule_date(message, date(2026, 10, 13)).status == "RESOLVED"
