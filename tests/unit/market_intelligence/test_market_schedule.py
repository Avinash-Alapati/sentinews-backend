"""
Unit tests for Market Schedule and Dynamic Cadence Calculation.
"""

from datetime import datetime
import pytz
import pytest

from app.core.config import settings
from app.modules.market_intelligence.domain.market_schedule import (
    is_indian_market_open,
    get_current_market_cadence,
)

KOLKATA_TZ = pytz.timezone("Asia/Kolkata")


def test_market_schedule_in_hours():
    # Monday at 11:30 AM IST (Trading day, in hours)
    in_hours_dt = KOLKATA_TZ.localize(datetime(2026, 9, 28, 11, 30, 0))
    assert is_indian_market_open(in_hours_dt) is True


def test_market_schedule_off_hours_weekday():
    # Monday at 08:00 AM IST (Trading day, pre-market)
    pre_market_dt = KOLKATA_TZ.localize(datetime(2026, 9, 28, 8, 0, 0))
    assert is_indian_market_open(pre_market_dt) is False

    # Monday at 16:30 IST (Trading day, post-market)
    post_market_dt = KOLKATA_TZ.localize(datetime(2026, 9, 28, 16, 30, 0))
    assert is_indian_market_open(post_market_dt) is False


def test_market_schedule_weekend():
    # Saturday at 11:30 AM IST
    saturday_dt = KOLKATA_TZ.localize(datetime(2026, 9, 26, 11, 30, 0))
    assert is_indian_market_open(saturday_dt) is False


def test_market_schedule_declared_holiday():
    # Republic Day: Jan 26, 2026 at 11:30 AM IST
    holiday_dt = KOLKATA_TZ.localize(datetime(2026, 1, 26, 11, 30, 0))
    assert is_indian_market_open(holiday_dt) is False


def test_dynamic_cadence_calculation():
    cadence = get_current_market_cadence()
    assert cadence in (
        settings.MARKET_REFRESH_INTERVAL_IN_HOURS,
        settings.MARKET_REFRESH_INTERVAL_OFF_HOURS,
        settings.MARKET_REFRESH_INTERVAL_WEEKEND,
    )
