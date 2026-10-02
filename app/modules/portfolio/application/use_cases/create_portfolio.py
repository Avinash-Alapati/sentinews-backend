"""
Create Portfolio Use Case.
"""

from app.modules.portfolio.application.ports import PortfolioRepository
from app.modules.portfolio.domain.entities import Portfolio


class CreatePortfolioUseCase:
    def __init__(self, portfolio_repo: PortfolioRepository):
        self.portfolio_repo = portfolio_repo

    async def execute(self, user_id: int, name: str = "My Portfolio") -> Portfolio:
        new_portfolio = Portfolio(
            user_id=user_id,
            name=name,
            cash_balance=0.0,
        )
        return await self.portfolio_repo.save_portfolio(new_portfolio)
