"""
Portfolio Holdings Reader Adapter.

Implements the PortfolioHoldingsReader application port in the News Intelligence module.
Interfaces strictly with Portfolio module's public application layer
(GetPortfolioOverviewUseCase or PortfolioRepository) without importing any ORM models.
"""

import logging
from typing import Any, List, Optional, Union
from uuid import UUID

from app.modules.news_intelligence.application.ports import (
    HoldingWeightView,
    PortfolioHoldingsReader,
)
from app.modules.portfolio.application.ports import PortfolioRepository
from app.modules.portfolio.application.use_cases.get_portfolio_overview import (
    GetPortfolioOverviewUseCase,
)

logger = logging.getLogger("sentinews.news_intelligence.portfolio_reader")


class PortfolioHoldingsReaderAdapter(PortfolioHoldingsReader):
    """
    Adapter implementing PortfolioHoldingsReader to fetch portfolio holdings and allocation weights
    via the Portfolio module's public application interfaces.
    """

    def __init__(
        self,
        portfolio_overview_use_case: Optional[GetPortfolioOverviewUseCase] = None,
        portfolio_repository: Optional[PortfolioRepository] = None,
    ):
        self.portfolio_overview_use_case = portfolio_overview_use_case
        self.portfolio_repo = portfolio_repository

    async def get_holdings_with_weights(
        self, portfolio_id: Union[UUID, int, str]
    ) -> List[HoldingWeightView]:
        """
        Retrieves active holdings and current weights for a portfolio, converting
        them to HoldingWeightView DTOs.
        """
        pid: int
        try:
            pid = int(portfolio_id)
        except (ValueError, TypeError):
            # Fallback if UUID or hash representation
            pid = abs(hash(str(portfolio_id))) % 1000000

        # Try using GetPortfolioOverviewUseCase first (includes live price re-weighting)
        if self.portfolio_overview_use_case is not None:
            try:
                overview = await self.portfolio_overview_use_case.execute(portfolio_id=pid)
                if overview and overview.holdings:
                    return [
                        HoldingWeightView(
                            symbol=h.symbol,
                            name=h.name,
                            sector=h.sector,
                            weight_pct=h.weight_pct,
                            quantity=h.quantity,
                            current_price=h.current_price,
                        )
                        for h in overview.holdings
                        if h.quantity > 0
                    ]
            except Exception as exc:
                logger.warning(
                    "Failed fetching overview for portfolio %s, falling back to repository: %s",
                    portfolio_id,
                    exc,
                )

        # Fallback to PortfolioRepository application port
        if self.portfolio_repo is not None:
            try:
                holdings = await self.portfolio_repo.get_holdings_with_weights(portfolio_id=pid)
                if holdings:
                    return [
                        HoldingWeightView(
                            symbol=h.symbol,
                            name=h.name,
                            sector=h.sector,
                            weight_pct=h.weight_pct,
                            quantity=h.quantity,
                            current_price=h.current_price,
                        )
                        for h in holdings
                        if h.quantity > 0
                    ]
            except Exception as exc:
                logger.error("Failed fetching holdings from portfolio repo for %s: %s", portfolio_id, exc)

        return []
