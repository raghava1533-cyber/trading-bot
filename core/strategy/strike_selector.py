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


def _span_margin_per_sell_leg(spot, lot_size, psr=0.065, exposure=0.03):
    """
    Approximate NSE SPAN + Exposure margin for ONE naked short index option lot.

    NSE uses a Price Scan Range (PSR) of ~6.5% for NIFTY index options.
    Exposure margin = 3% of notional (NSE Master Circular 2023).

    This is the margin blocked by the broker the moment a SELL order is placed,
    BEFORE the hedge (BUY) leg is placed. Once the hedge is in, the broker
    reduces the block to max_loss of the spread (SEBI Jan-2020 circular).

    If legs are placed as a basket/simultaneous order, the broker applies
    spread margin directly and this peak amount is never actually blocked.
    """
    notional = spot * lot_size
    return round(notional * (psr + exposure), 2)


def _margin_for_spread(sell_ltp, buy_ltp, spread_width, lot_size=75, spot=None):
    """
    Margin for a single vertical spread (one sell + one buy leg).

    Two margin figures are returned:
      margin_required  — SEBI defined-risk spread margin = max possible loss.
                         This is what the broker blocks AFTER both legs are live,
                         or immediately if placed as a basket/strategy order.
      peak_margin_est  — Approximate SPAN+Exposure margin for the naked sell leg,
                         i.e. the capital the broker will demand the moment the
                         SELL order is placed before the BUY leg is confirmed.
                         Use this to size how much free cash you need in your account.
    """
    sell_ltp       = sell_ltp or 0
    buy_ltp        = buy_ltp  or 0
    credit_p_share = sell_ltp - buy_ltp
    net_credit_lot = round(credit_p_share * lot_size, 2)
    max_loss_lot   = round(max(spread_width - credit_p_share, 0) * lot_size, 2)

    # Peak margin: SPAN estimate for one sell leg (only meaningful if spot provided)
    peak = _span_margin_per_sell_leg(spot, lot_size) if spot else max_loss_lot

    return {
        "credit_per_share": round(credit_p_share, 2),
        "net_credit":       net_credit_lot,
        "max_profit":       net_credit_lot,
        "max_loss":         max_loss_lot,
        "margin_required":  max_loss_lot,   # SEBI spread benefit (simultaneous/basket)
        "peak_margin_est":  peak,           # broker blocks this when sell leg placed alone
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
            logging.warning("BEAR_CALL: no OTM calls found above spot")
            return []

        sell = otm_calls[0]

        # Enforce 100pt minimum spread
        buys = [c for c in candidates if c["strike"] >= sell["strike"] + SPREAD_WIDTH]
        if not buys:
            logging.warning(
                f"BEAR_CALL: no buy leg found {SPREAD_WIDTH}pts above sell strike "
                f"{sell['strike']} — chain may be too narrow"
            )
            return []
        buys.sort(key=lambda x: x["strike"])
        buy = buys[0]

        margin = _margin_for_spread(sell["ce_ltp"], buy["ce_ltp"], SPREAD_WIDTH, lot_size, spot=spot)

        return [
            ("SELL", sell["strike"], "CE", sell["ce_sym"], sell["ce_ltp"], margin),
            ("BUY",  buy["strike"],  "CE", buy["ce_sym"],  buy["ce_ltp"],  margin),
        ]

    # ------------------------------------------------------------------
    elif strategy == "BULL_PUT":
        otm_puts = [c for c in candidates if c["strike"] < spot]
        otm_puts.sort(key=lambda x: abs(abs(x["pe_delta"]) - target_delta))

        if not otm_puts:
            logging.warning("BULL_PUT: no OTM puts found below spot")
            return []

        sell = otm_puts[0]

        # Enforce 100pt minimum spread
        buys = [c for c in candidates if c["strike"] <= sell["strike"] - SPREAD_WIDTH]
        if not buys:
            logging.warning(
                f"BULL_PUT: no buy leg found {SPREAD_WIDTH}pts below sell strike "
                f"{sell['strike']} — chain may be too narrow"
            )
            return []
        buys.sort(key=lambda x: x["strike"], reverse=True)
        buy = buys[0]

        margin = _margin_for_spread(sell["pe_ltp"], buy["pe_ltp"], SPREAD_WIDTH, lot_size, spot=spot)

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

        # Iron Condor combined margin:
        # - net_credit = sum of both spreads (we receive premium from both sides)
        # - max_profit = same (both expire worthless)
        # - max_loss   = max of the two spread max-losses
        #   (both sides cannot hit max loss simultaneously — price can only go one way)
        # - margin_required = max_loss per NSE defined-risk netting rule
        bear_margin = bear_legs[0][5]
        bull_margin = bull_legs[0][5]

        combined_net_credit = round(bear_margin["net_credit"] + bull_margin["net_credit"], 2)
        combined_max_loss   = round(max(bear_margin["max_loss"], bull_margin["max_loss"]), 2)
        # Peak margin: both sell legs are placed sequentially so broker
        # blocks SPAN on each in turn — sum of both peak estimates.
        combined_peak       = round(bear_margin["peak_margin_est"] + bull_margin["peak_margin_est"], 2)

        combined_margin = {
            "credit_per_share": round(
                bear_margin["credit_per_share"] + bull_margin["credit_per_share"], 2
            ),
            "net_credit":      combined_net_credit,
            "max_profit":      combined_net_credit,
            "max_loss":        combined_max_loss,
            "margin_required": combined_max_loss,   # SEBI: basket order margin
            "peak_margin_est": combined_peak,        # broker blocks this if sequential
        }

        # Re-attach combined margin to all legs
        all_legs = []
        for leg in bear_legs + bull_legs:
            all_legs.append((leg[0], leg[1], leg[2], leg[3], leg[4], combined_margin))

        return all_legs

    return []