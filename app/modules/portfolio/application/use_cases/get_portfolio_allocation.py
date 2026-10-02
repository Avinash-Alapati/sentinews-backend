"""
Get Portfolio Allocation Use Case.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from app.modules.portfolio.application.ports import PortfolioRepository
from app.modules.portfolio.domain.services.performance import calculate_portfolio_weights


@dataclass
class HoldingAllocation:
    symbol: str
    name: str
    sector: str
    weight_pct: float
    current_value: float


@dataclass
class SectorAllocation:
    sector: str
    weight_pct: float
    total_value: float


@dataclass
class PortfolioAllocation:
    portfolio_id: int
    holdings: List[HoldingAllocation] = field(default_factory=list)
    sectors: List[SectorAllocation] = field(default_factory=list)


class GetPortfolioAllocationUseCase:
    def __init__(self, portfolio_repo: PortfolioRepository):
        self.portfolio_repo = portfolio_repo

    async def execute(self, portfolio_id: int) -> Optional[PortfolioAllocation]:
        holdings = await self.portfolio_repo.get_holdings_with_weights(portfolio_id)
        if not holdings:
            return PortfolioAllocation(portfolio_id=portfolio_id)

        weighted = calculate_portfolio_weights([h for h in holdings if h.quantity > 0])
        total_val = sum(h.current_value for h in weighted)

        holding_allocs = [
            HoldingAllocation(
                symbol=h.symbol,
                name=h.name,
                sector=h.sector,
                weight_pct=round(h.weight_pct, 4),
                current_value=round(h.current_value, 2),
            )
            for h in weighted
        ]

        sector_totals: Dict[str, float] = {}
        for h in weighted:
            sec = h.sector or "General"
            sector_totals[sec] = sector_totals.get(sec, 0.0) + h.current_value

        sector_allocs = [
            SectorAllocation(
                sector=sec,
                weight_pct=round(val / total_val, 4) if total_val > 0 else 0.0,
                total_value=round(val, 2),
            )
            for sec, val in sector_totals.items()
        ]

        return PortfolioAllocation(
            portfolio_id=portfolio_id,
            holdings=holding_allocs,
            sectors=sector_allocs,
        )
