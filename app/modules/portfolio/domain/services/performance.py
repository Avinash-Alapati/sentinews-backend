"""
Portfolio Performance and Allocation Engine.

Computes portfolio allocation weights and annualized money-weighted returns (XIRR)
via the Newton-Raphson numerical method without external library dependencies.
"""

from datetime import datetime, timezone
import math
from typing import Dict, List, Optional, Tuple

from app.infrastructure.observability.decorators import track_compute
from app.modules.portfolio.domain.entities import Holding


@track_compute("portfolio_weights")
def calculate_portfolio_weights(
    holdings: List[Holding],
    current_prices: Optional[Dict[str, float]] = None,
) -> List[Holding]:
    """
    Computes and populates weight_pct for each holding in the portfolio.

    Weight is calculated as a fraction in [0.0, 1.0] relative to the total
    current portfolio market value.

    Args:
        holdings: List of Holding domain entities.
        current_prices: Optional mapping of symbol to current market price.

    Returns:
        List[Holding]: Updated holding entities with weight_pct and current_price assigned.
    """
    if not holdings:
        return []

    # Update prices if provided
    updated_holdings: List[Holding] = []
    for h in holdings:
        price = (
            current_prices.get(h.symbol, h.current_price or h.avg_buy_price)
            if current_prices
            else (h.current_price or h.avg_buy_price)
        )
        updated_holdings.append(
            Holding(
                symbol=h.symbol,
                quantity=h.quantity,
                avg_buy_price=h.avg_buy_price,
                name=h.name,
                sector=h.sector,
                current_price=price,
                weight_pct=0.0,
                id=h.id,
                portfolio_id=h.portfolio_id,
                created_at=h.created_at,
                updated_at=h.updated_at,
            )
        )

    total_value = sum(h.current_value for h in updated_holdings)

    if total_value <= 0:
        # Uniform distribution if total value is zero
        uniform_weight = 1.0 / len(updated_holdings)
        for h in updated_holdings:
            h.weight_pct = uniform_weight
        return updated_holdings

    for h in updated_holdings:
        h.weight_pct = h.current_value / total_value

    return updated_holdings


@track_compute("portfolio_xirr")
def calculate_xirr(
    cash_flows: List[Tuple[datetime, float]],
    guess: float = 0.1,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> Optional[float]:
    """
    Computes the Internal Rate of Return for non-periodic cash flows (XIRR)
    using the Newton-Raphson numerical optimization technique.

    Args:
        cash_flows: List of (timestamp, cash_flow_amount) tuples.
                    Investments are negative amounts, returns/current value positive.
        guess: Initial guess for the discount rate (default 0.1, i.e. 10%).
        max_iter: Maximum iterations before convergence failure.
        tol: Convergence tolerance for residual net present value.

    Returns:
        Optional[float]: Annualized XIRR as a decimal (e.g. 0.15 for 15%),
                         or None if calculation does not converge.
    """
    if len(cash_flows) < 2:
        return None

    # Normalize all datetimes to UTC timezone-aware datetimes before sorting
    normalized_flows = []
    for dt, amount in cash_flows:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        normalized_flows.append((dt, float(amount)))

    # Sort cash flows chronologically
    sorted_flows = sorted(normalized_flows, key=lambda cf: cf[0])
    t0 = sorted_flows[0][0]

    # Normalize timestamps to days from t0
    flows_with_days: List[Tuple[float, float]] = []
    for dt, amount in sorted_flows:
        days = (dt - t0).total_seconds() / 86400.0
        flows_with_days.append((days / 365.0, float(amount)))

    rate = guess

    for _ in range(max_iter):
        npv = 0.0
        d_npv = 0.0

        for years, amount in flows_with_days:
            base = 1.0 + rate
            if base <= 0:
                base = 1e-6

            # NPV term: amount / (1 + r)^t
            discount_factor = math.pow(base, years)
            npv += amount / discount_factor

            # Derivative d(NPV)/dr: -t * amount / (1 + r)^(t + 1)
            d_npv -= (years * amount) / (discount_factor * base)

        if abs(npv) < tol:
            return rate

        if abs(d_npv) < 1e-12:
            return None

        # Newton-Raphson update: r_new = r - f(r) / f'(r)
        new_rate = rate - (npv / d_npv)

        if abs(new_rate - rate) < tol:
            return new_rate

        rate = new_rate

    return None
