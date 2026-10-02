"""
Domain service for Indian Equity Market (NSE/BSE) trading calendar awareness.

Determines trading days and declared exchange holidays to prevent generating
market reports on weekends and exchange holidays.
"""

from datetime import date, timedelta
from typing import Dict, Optional, Set


# Official NSE / BSE Trading Holidays (2024–2027)
# Maps date(YYYY, MM, DD) -> Holiday Name
NSE_HOLIDAYS: Dict[date, str] = {
    # 2024
    date(2024, 1, 22): "Special Holiday (Ayodhya Pran Pratishtha)",
    date(2024, 1, 26): "Republic Day",
    date(2024, 3, 8): "Mahashivratri",
    date(2024, 3, 25): "Holi",
    date(2024, 3, 29): "Good Friday",
    date(2024, 4, 11): "Id-Ul-Fitr (Ramzan Id)",
    date(2024, 4, 17): "Ram Navami",
    date(2024, 5, 1): "Maharashtra Day",
    date(2024, 5, 20): "Lok Sabha Elections (Mumbai)",
    date(2024, 6, 17): "Bakri Id / Eid ul-Adha",
    date(2024, 7, 17): "Muharram",
    date(2024, 8, 15): "Independence Day",
    date(2024, 10, 2): "Mahatma Gandhi Jayanti",
    date(2024, 11, 1): "Diwali Laxmi Pujan (Muhurat Trading only)",
    date(2024, 11, 15): "Gurunanak Jayanti",
    date(2024, 12, 25): "Christmas",

    # 2025
    date(2025, 2, 26): "Mahashivratri",
    date(2025, 3, 14): "Holi",
    date(2025, 3, 31): "Id-Ul-Fitr",
    date(2025, 4, 10): "Mahavir Jayanti",
    date(2025, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
    date(2025, 4, 18): "Good Friday",
    date(2025, 5, 1): "Maharashtra Day",
    date(2025, 6, 7): "Bakri Id",
    date(2025, 8, 15): "Independence Day",
    date(2025, 8, 27): "Ganesh Chaturthi",
    date(2025, 10, 2): "Mahatma Gandhi Jayanti / Dussehra",
    date(2025, 10, 21): "Diwali Laxmi Pujan",
    date(2025, 10, 22): "Diwali Balipratipada",
    date(2025, 11, 5): "Prakash Gurpurb Sri Guru Nanak Dev",
    date(2025, 12, 25): "Christmas",

    # 2026
    date(2026, 1, 26): "Republic Day",
    date(2026, 2, 16): "Mahashivratri",
    date(2026, 3, 4): "Holi",
    date(2026, 3, 20): "Id-Ul-Fitr",
    date(2026, 4, 3): "Good Friday",
    date(2026, 4, 14): "Dr. Ambedkar Jayanti",
    date(2026, 5, 1): "Maharashtra Day",
    date(2026, 5, 27): "Bakri Id",
    date(2026, 6, 26): "Muharram",
    date(2026, 8, 15): "Independence Day",
    date(2026, 10, 2): "Mahatma Gandhi Jayanti",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 8): "Diwali Laxmi Pujan",
    date(2026, 11, 24): "Gurunanak Jayanti",
    date(2026, 12, 25): "Christmas",

    # 2027
    date(2027, 1, 26): "Republic Day",
    date(2027, 3, 8): "Mahashivratri",
    date(2027, 3, 23): "Holi",
    date(2027, 3, 26): "Good Friday",
    date(2027, 4, 14): "Dr. Ambedkar Jayanti",
    date(2027, 5, 1): "Maharashtra Day",
    date(2027, 8, 15): "Independence Day",
    date(2027, 10, 2): "Mahatma Gandhi Jayanti",
    date(2027, 10, 28): "Diwali Laxmi Pujan",
    date(2027, 12, 25): "Christmas",
}


class NSETradingCalendar:
    """
    Utility for checking whether an Indian equity market date is a trading day.
    """

    @staticmethod
    def is_weekend(check_date: date) -> bool:
        """Returns True if the date falls on Saturday (5) or Sunday (6)."""
        return check_date.weekday() >= 5

    @classmethod
    def get_holiday_name(cls, check_date: date) -> Optional[str]:
        """Returns holiday name if declared, else None."""
        return NSE_HOLIDAYS.get(check_date)

    @classmethod
    def is_trading_day(cls, check_date: date) -> bool:
        """
        Evaluates whether standard equity trading is conducted on the given date.
        Returns False on weekends and declared NSE holidays.
        """
        if cls.is_weekend(check_date):
            return False
        if check_date in NSE_HOLIDAYS:
            return False
        return True

    @classmethod
    def get_next_trading_day(cls, start_date: date) -> date:
        """Returns the immediately following active trading day."""
        candidate = start_date + timedelta(days=1)
        while not cls.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    @classmethod
    def get_previous_trading_day(cls, start_date: date) -> date:
        """Returns the immediately preceding active trading day."""
        candidate = start_date - timedelta(days=1)
        while not cls.is_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate
