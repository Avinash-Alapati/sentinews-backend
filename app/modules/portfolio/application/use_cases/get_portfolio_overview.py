"""
Get Portfolio Overview Use Case.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from app.modules.portfolio.application.ports import MarketDataProvider, PortfolioRepository
from app.modules.portfolio.domain.entities import Holding, Portfolio
from app.modules.portfolio.domain.services.cost_basis import calculate_portfolio_cost_basis
from app.modules.portfolio.domain.services.performance import calculate_portfolio_weights


from decimal import Decimal, ROUND_HALF_UP

@dataclass
class HoldingOverviewItem:
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
    day_change: float = 0.0
    day_change_percent: float = 0.0


@dataclass
class PortfolioOverview:
    portfolio_id: int
    name: str
    total_invested_value: float
    total_current_value: float
    total_unrealized_pnl: float
    total_unrealized_pnl_pct: float
    total_realized_pnl: float
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    day_pnl: float = 0.0
    day_pnl_percent: float = 0.0
    total_holdings_count: int = 0
    holdings: List[HoldingOverviewItem] = field(default_factory=list)


class GetPortfolioOverviewUseCase:
    def __init__(
        self,
        portfolio_repo: PortfolioRepository,
        market_data_provider: Optional[MarketDataProvider] = None,
    ):
        self.portfolio_repo = portfolio_repo
        self.market_provider = market_data_provider

    async def execute(self, portfolio_id: int) -> Optional[PortfolioOverview]:
        portfolio = await self.portfolio_repo.get_portfolio(portfolio_id)
        if not portfolio:
            return None

        # Fetch latest quotes from market provider if available
        symbols = [h.symbol for h in portfolio.holdings if h.quantity > 0]
        prices: Dict[str, float] = {}
        quotes_data: Dict[str, dict] = {}
        if self.market_provider and symbols:
            try:
                quotes_batch = await self.market_provider.get_quotes_batch(symbols)
                if isinstance(quotes_batch, dict):
                    for s, q in quotes_batch.items():
                        if isinstance(q, (int, float)):
                            prices[s] = float(q)
                        elif isinstance(q, dict):
                            prices[s] = float(q.get("price", 0.0))
                            quotes_data[s] = q
            except Exception:
                pass

        # Recalculate weights with live prices (offloaded to threadpool)
        weighted_holdings = await asyncio.to_thread(
            calculate_portfolio_weights, portfolio.holdings, current_prices=prices
        )

        # Realized PnL from transactions (offloaded to threadpool)
        cost_bases = await asyncio.to_thread(calculate_portfolio_cost_basis, portfolio.transactions)
        total_realized_pnl = sum((Decimal(str(cb.realized_pnl)) for cb in cost_bases.values()), Decimal("0.0"))

        holding_items: List[HoldingOverviewItem] = []
        for h in weighted_holdings:
            if h.quantity <= 0:
                continue
            cur_price_val = prices.get(h.symbol, h.current_price or h.avg_buy_price)
            cur_price_dec = Decimal(str(cur_price_val))
            qty_dec = Decimal(str(h.quantity))
            cost_dec = Decimal(str(h.total_cost))
            val_dec = qty_dec * cur_price_dec
            pnl_dec = val_dec - cost_dec
            pnl_pct_dec = (pnl_dec / cost_dec * Decimal("100")) if cost_dec > Decimal("0") else Decimal("0")

            q_meta = quotes_data.get(h.symbol, {})
            day_chg = float(q_meta.get("change", 0.0))
            day_chg_pct = float(q_meta.get("change_percent", 0.0))

            holding_items.append(
                HoldingOverviewItem(
                    symbol=h.symbol,
                    name=h.name,
                    sector=h.sector,
                    quantity=float(qty_dec),
                    avg_buy_price=float(Decimal(str(h.avg_buy_price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    current_price=float(cur_price_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    total_cost=float(cost_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    current_value=float(val_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    unrealized_pnl=float(pnl_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    unrealized_pnl_pct=float(pnl_pct_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    weight_pct=round(h.weight_pct, 4),
                    day_change=round(day_chg, 2),
                    day_change_percent=round(day_chg_pct, 2),
                )
            )

        total_cost_dec = sum((Decimal(str(item.total_cost)) for item in holding_items), Decimal("0.0"))
        total_val_dec = sum((Decimal(str(item.current_value)) for item in holding_items), Decimal("0.0"))
        total_unrealized_dec = total_val_dec - total_cost_dec
        total_unrealized_pct_dec = (total_unrealized_dec / total_cost_dec * Decimal("100")) if total_cost_dec > Decimal("0") else Decimal("0")
        total_pnl_dec = total_unrealized_dec + total_realized_pnl
        total_pnl_pct_dec = (total_pnl_dec / total_cost_dec * Decimal("100")) if total_cost_dec > Decimal("0") else Decimal("0")

        day_pnl_dec = sum((Decimal(str(item.day_change)) * Decimal(str(item.quantity)) for item in holding_items), Decimal("0.0"))
        prev_val_dec = total_val_dec - day_pnl_dec
        day_pnl_pct_dec = (day_pnl_dec / prev_val_dec * Decimal("100")) if prev_val_dec > Decimal("0") else Decimal("0")

        return PortfolioOverview(
            portfolio_id=portfolio_id,
            name=portfolio.name,
            total_invested_value=float(total_cost_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            total_current_value=float(total_val_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            total_unrealized_pnl=float(total_unrealized_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            total_unrealized_pnl_pct=float(total_unrealized_pct_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            total_realized_pnl=float(total_realized_pnl.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            total_pnl=float(total_pnl_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            total_pnl_pct=float(total_pnl_pct_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            day_pnl=float(day_pnl_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            day_pnl_percent=float(day_pnl_pct_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            total_holdings_count=len(holding_items),
            holdings=holding_items,
        )
