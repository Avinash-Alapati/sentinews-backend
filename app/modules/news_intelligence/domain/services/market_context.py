"""
Domain service for classifying article publication timestamp into Indian Market Hours context.

NSE Trading Hours:
- Standard Trading Session: 09:15 to 15:30 IST (UTC+05:30), Monday through Friday.
- Pre-Market: Monday-Friday prior to 09:15 IST.
- Market-Hours: Monday-Friday between 09:15 and 15:30 IST (inclusive).
- Post-Market: Monday-Friday after 15:30 IST, and all day Saturday/Sunday.

This is a factual timestamp classification for chronological indexing,
not an investment advice or market prediction signal.
"""

from datetime import datetime, time, timezone, timedelta

# IST is UTC + 5:30
IST_OFFSET = timedelta(hours=5, minutes=30)
IST_TZ = timezone(IST_OFFSET, name="IST")

MARKET_OPEN_TIME = time(9, 15, 0)
MARKET_CLOSE_TIME = time(15, 30, 0)

PRE_MARKET = "pre_market"
MARKET_HOURS = "market_hours"
POST_MARKET = "post_market"


def get_market_context(published_at: datetime) -> str:
    """
    Classifies a publication timestamp into pre_market, market_hours, or post_market.

    Args:
        published_at: Publication datetime (timezone-aware or naive UTC).

    Returns:
        str: 'pre_market', 'market_hours', or 'post_market'
    """
    if published_at.tzinfo is None:
        # Assume UTC if naive
        dt_utc = published_at.replace(tzinfo=timezone.utc)
    else:
        dt_utc = published_at.astimezone(timezone.utc)

    # Convert to IST
    dt_ist = dt_utc.astimezone(IST_TZ)
    weekday = dt_ist.weekday()  # Monday is 0, Sunday is 6

    # Weekends (Saturday=5, Sunday=6) are post_market
    if weekday >= 5:
        return POST_MARKET

    pub_time = dt_ist.time()

    if pub_time < MARKET_OPEN_TIME:
        return PRE_MARKET
    elif pub_time <= MARKET_CLOSE_TIME:
        return MARKET_HOURS
    else:
        return POST_MARKET
