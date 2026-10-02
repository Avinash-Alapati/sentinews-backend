"""
Unit tests for Indian Equity Market (NSE) Trading Calendar & Holiday Service.
"""

from datetime import date, datetime, timezone
import pytest

from app.modules.market_reports.domain.services.trading_calendar import (
    NSETradingCalendar,
)
from app.modules.market_reports.infrastructure.scheduler import (
    should_generate_report_today,
)


def test_weekend_detection():
    """Identifies Saturdays and Sundays as non-trading days."""
    saturday = date(2026, 9, 26)
    sunday = date(2026, 9, 27)
    monday = date(2026, 9, 28)

    assert NSETradingCalendar.is_weekend(saturday) is True
    assert NSETradingCalendar.is_weekend(sunday) is True
    assert NSETradingCalendar.is_weekend(monday) is False

    assert NSETradingCalendar.is_trading_day(saturday) is False
    assert NSETradingCalendar.is_trading_day(sunday) is False
    assert NSETradingCalendar.is_trading_day(monday) is True


def test_declared_nse_holidays():
    """Correctly identifies official NSE declared holidays."""
    republic_day = date(2026, 1, 26)
    independence_day = date(2026, 8, 15)
    gandhi_jayanti = date(2026, 10, 2)
    christmas = date(2026, 12, 25)

    assert NSETradingCalendar.is_trading_day(republic_day) is False
    assert NSETradingCalendar.get_holiday_name(republic_day) == "Republic Day"

    assert NSETradingCalendar.is_trading_day(gandhi_jayanti) is False
    assert NSETradingCalendar.get_holiday_name(gandhi_jayanti) == "Mahatma Gandhi Jayanti"

    assert NSETradingCalendar.is_trading_day(christmas) is False
    assert NSETradingCalendar.get_holiday_name(christmas) == "Christmas"


def test_regular_trading_day():
    """Confirms active status on regular weekdays."""
    regular_wednesday = date(2026, 9, 23)
    assert NSETradingCalendar.is_trading_day(regular_wednesday) is True
    assert NSETradingCalendar.get_holiday_name(regular_wednesday) is None


def test_get_next_trading_day_skips_weekends_and_holidays():
    """Advances past weekends and holidays to the next valid trading day."""
    friday = date(2026, 9, 25)
    next_day = NSETradingCalendar.get_next_trading_day(friday)
    # Friday -> Monday (skipping Sat 26, Sun 27)
    assert next_day == date(2026, 9, 28)

    # Next trading day after Thursday 2026-10-01 (Friday 10-02 is Gandhi Jayanti, 10-03 Sat, 10-04 Sun)
    thursday_oct1 = date(2026, 10, 1)
    next_day_oct = NSETradingCalendar.get_next_trading_day(thursday_oct1)
    assert next_day_oct == date(2026, 10, 5)  # Monday Oct 5


def test_should_generate_report_today():
    """Evaluates scheduler skip logic on trading vs non-trading days."""
    trading_dt = datetime(2026, 9, 23, 7, 45, tzinfo=timezone.utc)
    assert should_generate_report_today(trading_dt) is True

    weekend_dt = datetime(2026, 9, 27, 7, 45, tzinfo=timezone.utc)
    assert should_generate_report_today(weekend_dt) is False

    holiday_dt = datetime(2026, 1, 26, 7, 45, tzinfo=timezone.utc)
    assert should_generate_report_today(holiday_dt) is False
