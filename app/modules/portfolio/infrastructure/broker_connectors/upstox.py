"""
Upstox Broker Connector.
"""

from typing import Any, Dict, List
import logging

logger = logging.getLogger("sentinews.portfolio.upstox")


class UpstoxConnector:
    """
    Connector for Upstox API v2 (portfolio sync and trade ingestion).
    """

    def __init__(self, api_key: str = "", access_token: str = ""):
        self.api_key = api_key
        self.access_token = access_token

    async def get_holdings(self) -> List[Dict[str, Any]]:
        """Fetches user holdings from Upstox."""
        return []
