"""
strategy/delta_hedger.py
Portfolio delta calculation and delta-hedging via index futures/ETF.
"""
import logging

log = logging.getLogger(__name__)


def compute_portfolio_delta(positions: list, greeks_map: dict) -> float:
    """
    Sum net delta across all open positions.

    greeks_map: { instrument_key_or_symbol: {"delta": float, ...} }
    Each leg in a position has: side, strike, type, symbol, price, qty
    """
    total_delta = 0.0
    for pos in positions:
        if not pos.get("open", True):
            continue
        for leg in pos.get("legs", []):
            symbol = leg.get("symbol") or leg.get("instrument_key", "")
            qty    = int(leg.get("qty", 75))
            delta  = greeks_map.get(symbol, {}).get("delta", 0.0)
            # SELL legs flip the delta sign
            sign   = 1 if leg.get("side", "BUY") == "BUY" else -1
            total_delta += sign * delta * qty
    return round(total_delta, 4)


def hedge_delta(broker, current_delta: float, threshold: float,
                index_symbol: str, lot_size: int) -> None:
    """
    Place a hedge order if |delta| exceeds threshold.
    Sells futures/ETF to reduce positive delta, buys to reduce negative.
    """
    if abs(current_delta) < threshold:
        return

    hedge_qty = int(abs(current_delta) / lot_size) * lot_size
    if hedge_qty == 0:
        return

    side = "SELL" if current_delta > 0 else "BUY"
    log.info(f"Delta hedge: {side} {hedge_qty} {index_symbol}  "
             f"(delta={current_delta:+.4f}  threshold={threshold})")
    broker.place_order(index_symbol, side, hedge_qty)
