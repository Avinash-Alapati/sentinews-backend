"""
Get Portfolio Performance & Returns Use Case.

Computes annualized money-weighted returns (XIRR), realized/unrealized profit & loss,
and top gainers/losers across portfolio holdings.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from app.modules.portfolio.application.ports import MarketDataProvider, PortfolioRepository
from app.modules.portfolio.domain.entities import TransactionType
from app.modules.portfolio.domain.services.cost_basis import calculate_portfolio_cost_basis
from app.modules.portfolio.domain.services.performance import calculate_portfolio_weights, calculate_xirr


@dataclass
class HoldingPerformanceSummary:
    symbol: str
    name: str
    sector: str
    quantity: float
    avg_buy_price: float
    current_price: float
    total_cost: float
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    weight_pct: float


@dataclass
class PortfolioPerformance:
    portfolio_id: int
    name: str
    total_invested_value: float
    total_current_value: float
    total_unrealized_pnl: float
    total_unrealized_pnl_pct: float
    total_realized_pnl: float
    total_pnl: float
    xirr_pct: Optional[float]
    top_gainer: Optional[HoldingPerformanceSummary]
    top_loser: Optional[HoldingPerformanceSummary]
    holdings: List[HoldingPerformanceSummary] = field(default_factory=list)


class GetPortfolioPerformanceUseCase:
    """
    Computes comprehensive portfolio return metrics including XIRR and top performers.
    """

    def __init__(
        self,
        portfolio_repo: PortfolioRepository,
        market_data_provider: Optional[MarketDataProvider] = None,
    ):
        self.portfolio_repo = portfolio_repo
        self.market_provider = market_data_provider

    async def execute(self, portfolio_id: int) -> Optional[PortfolioPerformance]:
        portfolio = await self.portfolio_repo.get_portfolio(portfolio_id)
        if not portfolio:
            return None

        # Fetch latest quotes from market provider
        symbols = [h.symbol for h in portfolio.holdings if h.quantity > 0]
        prices: Dict[str, float] = {}
        if self.market_provider and symbols:
            try:
                prices = await self.market_provider.get_quotes_batch(symbols)
            except Exception:
                pass

        # Recalculate weights with live prices (offloaded to threadpool)
        weighted_holdings = await asyncio.to_thread(
            calculate_portfolio_weights, portfolio.holdings, current_prices=prices
        )

        # Realized PnL from transactions (offloaded to threadpool)
        cost_bases = await asyncio.to_thread(calculate_portfolio_cost_basis, portfolio.transactions)
        total_realized_pnl = sum(cb.realized_pnl for cb in cost_bases.values())

        holding_summaries: List[HoldingPerformanceSummary] = []
        for h in weighted_holdings:
            if h.quantity <= 0:
                continue
            cur_price = prices.get(h.symbol, h.current_price or h.avg_buy_price)
            cost = h.total_cost
            val = h.quantity * cur_price
            pnl = val - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0

            holding_summaries.append(
                HoldingPerformanceSummary(
                    symbol=h.symbol,
                    name=h.name,
                    sector=h.sector,
                    quantity=h.quantity,
                    avg_buy_price=round(h.avg_buy_price, 2),
                    current_price=round(cur_price, 2),
                    total_cost=round(cost, 2),
                    current_value=round(val, 2),
                    unrealized_pnl=round(pnl, 2),
                    unrealized_pnl_pct=round(pnl_pct, 2),
                    weight_pct=round(h.weight_pct, 4),
                )
            )

        total_cost = sum(item.total_cost for item in holding_summaries)
        total_value = sum(item.current_value for item in holding_summaries)
        total_unrealized = total_value - total_cost
        total_unrealized_pct = (total_unrealized / total_cost * 100) if total_cost > 0 else 0.0
        total_pnl = total_realized_pnl + total_unrealized

        # Build cash flows for XIRR calculation:
        # BUYs are negative cash flows, SELLs are positive cash flows,
        # and current portfolio value is a terminal positive cash flow at now.
        cash_flows = []
        for tx in portfolio.transactions:
            amount = tx.quantity * tx.price
            if tx.transaction_type == TransactionType.BUY:
                cash_flows.append((tx.timestamp, -amount))
            elif tx.transaction_type == TransactionType.SELL:
                cash_flows.append((tx.timestamp, amount))

        now = datetime.now(timezone.utc)
        if total_value > 0:
            cash_flows.append((now, total_value))

        xirr_val = None
        if len(cash_flows) >= 2:
            computed_xirr = await asyncio.to_thread(calculate_xirr, cash_flows)
            if computed_xirr is not None and not (computed_xirr != computed_xirr):  # check not NaN
                xirr_val = round(computed_xirr * 100, 2)

        # Top gainer and loser
        sorted_by_pct = sorted(holding_summaries, key=lambda h: h.unrealized_pnl_pct, reverse=True)
        top_gainer = sorted_by_pct[0] if sorted_by_pct else None
        top_loser = sorted_by_pct[-1] if sorted_by_pct and len(sorted_by_pct) > 1 else None

        return PortfolioPerformance(
            portfolio_id=portfolio_id,
            name=portfolio.name,
            total_invested_value=round(total_cost, 2),
            total_current_value=round(total_value, 2),
            total_unrealized_pnl=round(total_unrealized, 2),
            total_unrealized_pnl_pct=round(total_unrealized_pct, 2),
            total_realized_pnl=round(total_realized_pnl, 2),
            total_pnl=round(total_pnl, 2),
            xirr_pct=xirr_val,
            top_gainer=top_gainer,
            top_loser=top_loser,
            holdings=holding_summaries,
        )
