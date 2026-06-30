""
strategy/gamma_scalper.py
Gamma scalping helper — detects large intraday moves that warrant re-hedging.
"""


def gamma_scalp(last_price: float, current_price: float,
                threshold: float = 0.002) -> bool:
    """
    Return True if the price has moved more than `threshold` (default 0.2%)
    since the last hedge, indicating a gamma scalp opportunity.

    Usage in main loop:
        if gamma_scalp(last_spot, current_spot):
            hedge_delta(broker, portfolio_delta, ...)
            last_spot = current_spot
    """
    if last_price <= 0:
        return False
    move = abs(current_price - last_price) / last_price
    return move > threshold
