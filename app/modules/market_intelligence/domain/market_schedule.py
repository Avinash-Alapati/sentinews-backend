"""
Market Hours & Trading Schedule Service.

Determines real-time trading status for Indian Equity Exchanges (NSE & BSE)
and computes dynamic background fetch intervals based on market open/close state.
"""

from datetime import datetime, time as dtime
import logging
from typing import Optional
import pytz

from app.core.config import settings
from app.modules.market_reports.domain.services.trading_calendar import NSETradingCalendar

logger = logging.getLogger(__name__)

KOLKATA_TZ = pytz.timezone(settings.MARKET_TIMEZONE)


def get_current_ist_time() -> datetime:
    """Returns the current timezone-aware timestamp in Asia/Kolkata."""
    return datetime.now(KOLKATA_TZ)


def is_indian_market_open(dt: Optional[datetime] = None) -> bool:
    """
    Returns True if Indian Equities (NSE/BSE) are actively trading:
    - Monday through Friday
    - Non-exchange holiday
    - Between 09:15:00 IST and 15:30:00 IST
    """
    now_ist = dt.astimezone(KOLKATA_TZ) if dt else get_current_ist_time()
    today_date = now_ist.date()

    # 1. Weekend check
    if NSETradingCalendar.is_weekend(today_date):
        return False

    # 2. Holiday check
    if not NSETradingCalendar.is_trading_day(today_date):
        return False

    # 3. Market trading hours (09:15 to 15:30 IST)
    current_time = now_ist.time()
    market_open = dtime(9, 15)
    market_close = dtime(15, 30)

    return market_open <= current_time <= market_close


def get_current_market_cadence() -> float:
    """
    Returns the dynamic fetch cadence in seconds:
    - During Market Hours (09:15 - 15:30 IST, Mon-Fri): 30s
    - Off-Hours (Pre/Post market on trading days): 300s (5m)
    - Weekends and Declared Holidays: 900s (15m)
    """
    now_ist = get_current_ist_time()
    today_date = now_ist.date()

    if NSETradingCalendar.is_weekend(today_date) or not NSETradingCalendar.is_trading_day(today_date):
        return float(settings.MARKET_REFRESH_INTERVAL_WEEKEND)

    if is_indian_market_open(now_ist):
        return float(settings.MARKET_REFRESH_INTERVAL_IN_HOURS)

    return float(settings.MARKET_REFRESH_INTERVAL_OFF_HOURS)
