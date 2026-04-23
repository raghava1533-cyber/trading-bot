# strategy/strike_selector.py
import math
from scipy.stats import norm

SPREAD_WIDTH = 100  # minimum pts between sell and buy leg


def _bs_delta(S, K, T, r, sigma, opt_type):
    """Black-Scholes delta."""
    if T <= 0 or sigma <= 0:
        return 0.5 if opt_type == "CE" else -0.5
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return norm.cdf(d1) if opt_type == "CE" else norm.cdf(d1) - 1


def _margin_for_spread(sell_ltp, buy_ltp, spread_width, lot_size=75):
    """
    Estimate margin needed for a vertical spread.

    SEBI rule: margin = max_loss = (spread_width - net_credit) * lot_size
    Net credit = sell_ltp - buy_ltp
    Max loss   = spread_width - net_credit
    """
    net_credit = (sell_ltp or 0) - (buy_ltp or 0)
    max_loss   = spread_width - net_credit
    margin     = max(max_loss, 0) * lot_size
    return {
        "net_credit": round(net_credit, 2),
        "max_profit": round(net_credit * lot_size, 2),
        "max_loss":   round(max_loss * lot_size, 2),
        "margin_required": round(margin, 2),
    }


def select_strikes(chain, spot, strategy, T=0.1, r=0.05, target_delta=0.25, lot_size=75):
    """
    Smart strike selector using delta targeting + 100pt enforced spread.

    strategy : "BEAR_CALL" | "BULL_PUT" | "IRON_CONDOR"
    Returns  : list of (side, strike, option_type, tradingsymbol) tuples
               + margin_info dict attached as last element if strategy != IRON_CONDOR
    """

    candidates = []

    for row in chain:
        K  = row["strikePrice"]
        ce = row.get("CE", {})
        pe = row.get("PE", {})

        if not ce or not pe:
            continue

        ce_iv = ce.get("iv", 0.15) or 0.15
        pe_iv = pe.get("iv", 0.15) or 0.15

        ce_delta = _bs_delta(spot, K, T, r, ce_iv, "CE")
        pe_delta = _bs_delta(spot, K, T, r, pe_iv, "PE")

        candidates.append({
            "strike":   K,
            "ce_delta": ce_delta,
            "pe_delta": pe_delta,
            "ce_iv":    ce_iv,
            "pe_iv":    pe_iv,
            "ce_ltp":   ce.get("ltp") or 0,
            "pe_ltp":   pe.get("ltp") or 0,
            "ce_sym":   ce.get("tradingsymbol") or f"NIFTY_CE_{int(K)}",
            "pe_sym":   pe.get("tradingsymbol") or f"NIFTY_PE_{int(K)}",
            "ce_key":   ce.get("instrument_key", ""),
            "pe_key":   pe.get("instrument_key", ""),
        })

    if not candidates:
        return []

    # ------------------------------------------------------------------
    if strategy == "BEAR_CALL":
        otm_calls = [c for c in candidates if c["strike"] > spot]
        otm_calls.sort(key=lambda x: abs(x["ce_delta"] - target_delta))

        if not otm_calls:
            return []

        sell = otm_calls[0]

        # Enforce 100pt minimum spread
        buys = [c for c in candidates if c["strike"] >= sell["strike"] + SPREAD_WIDTH]
        if not buys:
            return []
        buys.sort(key=lambda x: x["strike"])
        buy = buys[0]

        margin = _margin_for_spread(sell["ce_ltp"], buy["ce_ltp"], SPREAD_WIDTH, lot_size)

        return [
            ("SELL", sell["strike"], "CE", sell["ce_sym"], sell["ce_ltp"], margin),
            ("BUY",  buy["strike"],  "CE", buy["ce_sym"],  buy["ce_ltp"],  margin),
        ]

    # ------------------------------------------------------------------
    elif strategy == "BULL_PUT":
        otm_puts = [c for c in candidates if c["strike"] < spot]
        otm_puts.sort(key=lambda x: abs(abs(x["pe_delta"]) - target_delta))

        if not otm_puts:
            return []

        sell = otm_puts[0]

        # Enforce 100pt minimum spread
        buys = [c for c in candidates if c["strike"] <= sell["strike"] - SPREAD_WIDTH]
        if not buys:
            return []
        buys.sort(key=lambda x: x["strike"], reverse=True)
        buy = buys[0]

        margin = _margin_for_spread(sell["pe_ltp"], buy["pe_ltp"], SPREAD_WIDTH, lot_size)

        return [
            ("SELL", sell["strike"], "PE", sell["pe_sym"], sell["pe_ltp"], margin),
            ("BUY",  buy["strike"],  "PE", buy["pe_sym"],  buy["pe_ltp"],  margin),
        ]

    # ------------------------------------------------------------------
    elif strategy == "IRON_CONDOR":
        bear_legs = select_strikes(chain, spot, "BEAR_CALL", T, r, target_delta, lot_size)
        bull_legs  = select_strikes(chain, spot, "BULL_PUT",  T, r, target_delta, lot_size)

        if not bear_legs or not bull_legs:
            return []

        # Combined margin = max of the two spreads (SEBI netting for defined-risk)
        bear_margin = bear_legs[0][5]
        bull_margin = bull_legs[0][5]

        combined_margin = {
            "net_credit":      round(bear_margin["net_credit"] + bull_margin["net_credit"], 2),
            "max_profit":      round(bear_margin["max_profit"] + bull_margin["max_profit"], 2),
            "max_loss":        round(max(bear_margin["max_loss"], bull_margin["max_loss"]), 2),
            "margin_required": round(max(bear_margin["margin_required"], bull_margin["margin_required"]), 2),
        }

        # Re-attach combined margin to all legs
        all_legs = []
        for leg in bear_legs + bull_legs:
            all_legs.append((leg[0], leg[1], leg[2], leg[3], leg[4], combined_margin))

        return all_legs

    return []