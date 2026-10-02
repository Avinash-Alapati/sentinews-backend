"""
Explicit Allowlist of Source RSS Feeds for SentiNews Ingestion.

SEBI Compliance Requirement:
Only verified, allowlisted Indian and global financial news publishers are permitted.
Arbitrary, unverified, or third-party blog feeds are strictly rejected.
"""

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse


@dataclass(frozen=True)
class RSSFeedSource:
    name: str
    publisher: str
    url: str
    category: str
    enabled: bool = True


ALLOWLISTED_FEEDS: List[RSSFeedSource] = [
    # Yahoo Finance
    RSSFeedSource(
        name="Yahoo Finance Markets",
        publisher="Yahoo Finance",
        url="https://finance.yahoo.com/news/rssindex",
        category="Markets",
        enabled=True,
    ),
    # Moneycontrol Financial Feeds
    RSSFeedSource(
        name="Moneycontrol Latest News",
        publisher="Moneycontrol",
        url="https://www.moneycontrol.com/rss/latestnews.xml",
        category="General",
        enabled=True,
    ),
    RSSFeedSource(
        name="Moneycontrol Markets",
        publisher="Moneycontrol",
        url="https://www.moneycontrol.com/rss/MCmarket.xml",
        category="Markets",
        enabled=True,
    ),
    RSSFeedSource(
        name="Moneycontrol Business",
        publisher="Moneycontrol",
        url="https://www.moneycontrol.com/rss/business.xml",
        category="Business",
        enabled=True,
    ),
    RSSFeedSource(
        name="Moneycontrol Economy",
        publisher="Moneycontrol",
        url="https://www.moneycontrol.com/rss/economy.xml",
        category="Economy",
        enabled=True,
    ),
    RSSFeedSource(
        name="Moneycontrol Corporate Results",
        publisher="Moneycontrol",
        url="https://www.moneycontrol.com/rss/results.xml",
        category="Corporate",
        enabled=True,
    ),
    RSSFeedSource(
        name="Moneycontrol Mutual Funds",
        publisher="Moneycontrol",
        url="https://www.moneycontrol.com/rss/mfnews.xml",
        category="Markets",
        enabled=True,
    ),
    # Economic Times Financial Feeds
    RSSFeedSource(
        name="Economic Times Markets",
        publisher="Economic Times",
        url="https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        category="Markets",
        enabled=True,
    ),
    RSSFeedSource(
        name="Economic Times Economy",
        publisher="Economic Times",
        url="https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
        category="Economy",
        enabled=True,
    ),
    RSSFeedSource(
        name="Economic Times Industry",
        publisher="Economic Times",
        url="https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms",
        category="Industry",
        enabled=True,
    ),
    RSSFeedSource(
        name="Economic Times Stocks and IPOs",
        publisher="Economic Times",
        url="https://economictimes.indiatimes.com/markets/ipos/fpos/rssfeeds/14655708.cms",
        category="Markets",
        enabled=True,
    ),
    # LiveMint Financial Feeds
    RSSFeedSource(
        name="LiveMint Markets",
        publisher="LiveMint",
        url="https://www.livemint.com/rss/markets",
        category="Markets",
        enabled=True,
    ),
    RSSFeedSource(
        name="LiveMint Companies",
        publisher="LiveMint",
        url="https://www.livemint.com/rss/companies",
        category="Corporate",
        enabled=True,
    ),
    RSSFeedSource(
        name="LiveMint Economy and Policy",
        publisher="LiveMint",
        url="https://www.livemint.com/rss/economy",
        category="Economy",
        enabled=True,
    ),
    # Business Standard Financial Feeds
    RSSFeedSource(
        name="Business Standard Markets",
        publisher="Business Standard",
        url="https://www.business-standard.com/rss/markets-106.rss",
        category="Markets",
        enabled=True,
    ),
    RSSFeedSource(
        name="Business Standard Companies",
        publisher="Business Standard",
        url="https://www.business-standard.com/rss/companies-101.rss",
        category="Corporate",
        enabled=True,
    ),
    RSSFeedSource(
        name="Business Standard Economy Policy",
        publisher="Business Standard",
        url="https://www.business-standard.com/rss/economy-policy-102.rss",
        category="Economy",
        enabled=True,
    ),
    RSSFeedSource(
        name="Business Standard Finance",
        publisher="Business Standard",
        url="https://www.business-standard.com/rss/finance-103.rss",
        category="Finance",
        enabled=True,
    ),
    # The Hindu BusinessLine Financial Feeds
    RSSFeedSource(
        name="The Hindu BusinessLine Markets",
        publisher="The Hindu BusinessLine",
        url="https://www.thehindubusinessline.com/markets/feeder/default.rss",
        category="Markets",
        enabled=True,
    ),
    RSSFeedSource(
        name="The Hindu BusinessLine Economy",
        publisher="The Hindu BusinessLine",
        url="https://www.thehindubusinessline.com/economy/feeder/default.rss",
        category="Economy",
        enabled=True,
    ),
    RSSFeedSource(
        name="The Hindu BusinessLine Companies",
        publisher="The Hindu BusinessLine",
        url="https://www.thehindubusinessline.com/companies/feeder/default.rss",
        category="Corporate",
        enabled=True,
    ),
    # Financial Express Feeds
    RSSFeedSource(
        name="Financial Express Market",
        publisher="Financial Express",
        url="https://www.financialexpress.com/market/feed/",
        category="Markets",
        enabled=True,
    ),
    RSSFeedSource(
        name="Financial Express Economy",
        publisher="Financial Express",
        url="https://www.financialexpress.com/about/economy/feed/",
        category="Economy",
        enabled=True,
    ),
    RSSFeedSource(
        name="Financial Express Industry",
        publisher="Financial Express",
        url="https://www.financialexpress.com/industry/feed/",
        category="Industry",
        enabled=True,
    ),
    # NDTV Profit
    RSSFeedSource(
        name="NDTV Profit Markets",
        publisher="NDTV Profit",
        url="https://feeds.feedburner.com/ndtvprofit-latest",
        category="Markets",
        enabled=True,
    ),
]


def get_allowlisted_feeds() -> List[RSSFeedSource]:
    """Returns all active allowlisted RSS feed sources."""
    return [feed for feed in ALLOWLISTED_FEEDS if feed.enabled]


def is_feed_allowlisted(url: str) -> bool:
    """
    Checks if a given feed URL is in the explicit allowlist.
    Normalizes protocols and trailing slashes for comparison.
    """
    if not url:
        return False

    clean_target = url.strip().rstrip("/").lower()
    for feed in ALLOWLISTED_FEEDS:
        if feed.url.strip().rstrip("/").lower() == clean_target:
            return True
        # Also check domain match if matching exact allowed host
        target_domain = urlparse(clean_target).netloc
        feed_domain = urlparse(feed.url.lower()).netloc
        if target_domain and feed_domain and target_domain == feed_domain and feed.url.strip().rstrip("/").lower() in clean_target:
            return True

    return False
