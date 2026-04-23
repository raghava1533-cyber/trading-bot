# strategy/delta_hedger.py

def compute_portfolio_delta(positions, greeks_map):
    total_delta = 0

    for pos in positions:
        # Fixed: was pos["symbol"], paper_engine stores pos["strategy"]
        sym = pos["strategy"]
        qty = pos.get("qty", 1)

        delta = greeks_map.get(sym, {}).get("delta", 0)

        total_delta += delta * qty

    return total_delta


def hedge_delta(broker, current_delta, threshold, index_symbol, lot_size):
    if abs(current_delta) < threshold:
        return

    hedge_qty = int(current_delta / lot_size)

    if hedge_qty > 0:
        broker.place_order(index_symbol, "SELL", hedge_qty * lot_size)
    else:
        broker.place_order(index_symbol, "BUY", abs(hedge_qty) * lot_size)