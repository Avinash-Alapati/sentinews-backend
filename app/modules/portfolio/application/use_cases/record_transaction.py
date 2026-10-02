"""
Record Transaction Use Case.
"""

from datetime import datetime, timezone
from typing import Optional
from app.modules.portfolio.application.ports import (
    MarketDataProvider,
    NewsIntelligenceRelevancePort,
    PortfolioRepository,
)
from app.modules.portfolio.domain.entities import Holding, Transaction, TransactionType
from app.modules.portfolio.domain.services.cost_basis import calculate_fifo_cost_basis_for_symbol
from app.modules.portfolio.domain.services.performance import calculate_portfolio_weights


class RecordTransactionUseCase:
    def __init__(
        self,
        portfolio_repo: PortfolioRepository,
        market_data_provider: Optional[MarketDataProvider] = None,
        relevance_port: Optional[NewsIntelligenceRelevancePort] = None,
    ):
        self.portfolio_repo = portfolio_repo
        self.market_provider = market_data_provider
        self.relevance_port = relevance_port

    async def execute(
        self,
        portfolio_id: int,
        symbol: str,
        transaction_type: TransactionType,
        quantity: float,
        price: float,
        timestamp: Optional[datetime] = None,
        name: str = "",
        sector: str = "General",
    ) -> Transaction:
        clean_symbol = symbol.strip().upper()
        tx = Transaction(
            portfolio_id=portfolio_id,
            symbol=clean_symbol,
            transaction_type=transaction_type,
            quantity=float(quantity),
            price=float(price),
            timestamp=timestamp or datetime.now(timezone.utc),
        )

        saved_tx = await self.portfolio_repo.add_transaction(portfolio_id, tx)

        # Reload symbol-specific transactions to calculate FIFO cost basis
        if hasattr(self.portfolio_repo, "get_transactions_for_symbol"):
            sym_txs = await self.portfolio_repo.get_transactions_for_symbol(portfolio_id, clean_symbol)
        else:
            portfolio = await self.portfolio_repo.get_portfolio(portfolio_id)
            sym_txs = [t for t in (portfolio.transactions if portfolio else []) if t.symbol == clean_symbol]

        cost_basis = calculate_fifo_cost_basis_for_symbol(sym_txs)

        # Find or create holding
        if hasattr(self.portfolio_repo, "get_holding_by_symbol"):
            existing_holding = await self.portfolio_repo.get_holding_by_symbol(portfolio_id, clean_symbol)
        else:
            existing_holding = None

        is_new_holding = existing_holding is None

        target_holding = Holding(
            id=existing_holding.id if existing_holding else None,
            portfolio_id=portfolio_id,
            symbol=clean_symbol,
            name=name or (existing_holding.name if existing_holding else clean_symbol),
            sector=sector or (existing_holding.sector if existing_holding else "General"),
            quantity=cost_basis.remaining_quantity,
            avg_buy_price=cost_basis.avg_buy_price,
        )
        await self.portfolio_repo.add_holding(portfolio_id, target_holding)

        if is_new_holding and self.relevance_port and target_holding.quantity > 0:
            try:
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.relevance_port.notify_holding_added(target_holding))
                except RuntimeError:
                    pass
            except Exception:
                pass

        return saved_tx
