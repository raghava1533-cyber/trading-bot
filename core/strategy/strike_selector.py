"""
strategy/strike_selector.py

HOW OPTIONS SPREAD P&L WORKS (important):
  For a BULL_PUT spread (sell higher strike PE, buy lower strike PE):
    sell_strike = 23750, buy_strike = 23550  -> spread_width = 200 pts
    sell_ltp    = 12.00,  buy_ltp   = 1.75   -> credit_per_share = 10.25 Rs

    max_profit  = credit_per_share * lot_size = 10.25 * 75 = Rs 769
    max_loss    = (spread_width_pts - credit_per_share) * lot_size
                = (200 - 10.25) * 75 = Rs 14,231

  This gives R:R = 1:0.05 which is TERRIBLE.

  WHY: credit_per_share (Rs 10.25) is only 5% of spread_width (200 pts).
  For a good trade we need credit >= 25-33% of spread width.

  SOLUTION: enforce MIN_CREDIT_RATIO. If credit < ratio * spread_width,
  skip this strike and try the next one, or widen the spread.

  GOOD TRADE EXAMPLE:
    sell_ltp = 60, buy_ltp = 10, sw = 200
    credit = 50 Rs/share = 25% of spread -> R:R = 1:1.13  (acceptable)
"""
import logging, math
from scipy.stats import norm
from config import SETTINGS

SPREAD_WIDTH = SETTINGS.spread_width_points

# No minimum credit ratio - always take best available spread
MIN_CREDIT_RATIO = 0.0


def _bs_delta(S, K, T, r, sigma, opt_type):
    if T <= 0 or sigma <= 0:
        return 0.5 if opt_type == "CE" else -0.5
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm.cdf(d1) if opt_type == "CE" else norm.cdf(d1) - 1


def _span_margin(spot, lot_size, psr=0.065, exposure=0.03):
    return round(spot * lot_size * (psr + exposure), 2)


def _margin_for_spread(sell_ltp, buy_ltp, sw_pts, lot_size=75, spot=None):
    """
    Compute spread P&L metrics.

    Parameters
    ----------
    sell_ltp  : float  premium received  (Rs per share)
    buy_ltp   : float  premium paid      (Rs per share)
    sw_pts    : int    spread width in index POINTS
                       For index options 1 point = Rs 1 per share
    lot_size  : int    number of shares per lot
    spot      : float  underlying spot price (for SPAN margin estimate)

    Returns
    -------
    dict with:
      credit_per_share  Rs/share collected
      net_credit        Rs total collected  = credit_per_share * lot_size
      max_profit        Rs = net_credit  (if both legs expire worthless)
      max_loss          Rs = (sw_pts - credit_per_share) * lot_size
                            (if spread fully breached)
      credit_ratio      credit_per_share / sw_pts  (quality metric)
      rr_ratio          max_profit / max_loss
    """
    sell_ltp = sell_ltp or 0.0
    buy_ltp  = buy_ltp  or 0.0
    cps      = round(sell_ltp - buy_ltp, 2)           # credit per share (Rs)
    nc       = round(cps * lot_size, 2)                # net credit (Rs)
    mp       = nc                                      # max profit (Rs)
    ml       = round(max(sw_pts - cps, 0) * lot_size, 2)  # max loss (Rs)
    peak     = _span_margin(spot, lot_size) if spot else ml
    cr       = round(cps / sw_pts, 4) if sw_pts > 0 else 0
    rr       = round(mp / ml, 4) if ml > 0 else 0
    return {
        "credit_per_share": cps,
        "net_credit":       nc,
        "max_profit":       mp,
        "max_loss":         ml,
        "margin_required":  ml,
        "peak_margin_est":  peak,
        "credit_ratio":     cr,    # e.g. 0.25 means credit = 25% of spread
        "rr_ratio":         rr,    # e.g. 0.33 means R:R = 1:3
    }


def _find_best_spread(candidates, spot, strategy, sw, lot_size,
                      target_delta, min_credit_ratio=0.0):
    """
    Find best sell+buy pair by delta proximity.
    No credit ratio filter - always takes best available spread.
    Only skips if sell_ltp <= buy_ltp (zero/negative credit = no point entering).
    """
    if strategy == "BEAR_CALL":
        otm_sells = sorted(
            [c for c in candidates if c["strike"] > spot],
            key=lambda x: abs(x["ce_delta"] - target_delta),
        )
    else:  # BULL_PUT
        otm_sells = sorted(
            [c for c in candidates if c["strike"] < spot],
            key=lambda x: abs(abs(x["pe_delta"]) - target_delta),
        )

    if not otm_sells:
        return None

    # Try up to 10 sell candidates (closest delta first), pick first with positive credit
    for sell in otm_sells[:10]:
        if strategy == "BEAR_CALL":
            buy_candidates = sorted(
                [c for c in candidates if c["strike"] >= sell["strike"] + sw],
                key=lambda x: x["strike"],
            )
            sell_ltp   = sell["ce_ltp"]
            buy_ltp_fn = lambda b: b["ce_ltp"]
        else:
            buy_candidates = sorted(
                [c for c in candidates if c["strike"] <= sell["strike"] - sw],
                key=lambda x: x["strike"], reverse=True,
            )
            sell_ltp   = sell["pe_ltp"]
            buy_ltp_fn = lambda b: b["pe_ltp"]

        if not buy_candidates:
            continue

        buy     = buy_candidates[0]
        buy_ltp = buy_ltp_fn(buy)
        m       = _margin_for_spread(sell_ltp, buy_ltp, sw, lot_size, spot)

        if m["credit_per_share"] > 0:
            logging.info(
                f"{strategy}: sell={sell['strike']:.0f} buy={buy['strike']:.0f} "
                f"credit=Rs{m['credit_per_share']:.2f}/share "
                f"MaxProfit=Rs{m['max_profit']:,.0f} MaxLoss=Rs{m['max_loss']:,.0f}"
            )
            return sell, buy, m

    logging.warning(f"{strategy}: no spread with positive credit found in chain")
    return None


def select_strikes(chain, spot, strategy, T=0.1, r=0.06, target_delta=0.30,
                   lot_size=75, spread_width=None):
    sw  = int(spread_width) if spread_width is not None else SPREAD_WIDTH
    mcr = MIN_CREDIT_RATIO

    candidates = []
    for row in chain:
        K   = row["strikePrice"]
        ce  = row.get("CE", {}) or {}
        pe  = row.get("PE", {}) or {}
        if not ce and not pe:
            continue
        ce_iv = ce.get("iv") or 0.18
        pe_iv = pe.get("iv") or 0.18
        candidates.append({
            "strike":   K,
            "ce_delta": _bs_delta(spot, K, T, r, ce_iv, "CE"),
            "pe_delta": _bs_delta(spot, K, T, r, pe_iv, "PE"),
            "ce_ltp":   ce.get("ltp") or 0,
            "pe_ltp":   pe.get("ltp") or 0,
            "ce_sym":   ce.get("tradingsymbol") or f"CE_{int(K)}",
            "pe_sym":   pe.get("tradingsymbol") or f"PE_{int(K)}",
        })
    if not candidates:
        return []

    if strategy in ("BEAR_CALL", "BULL_PUT"):
        result = _find_best_spread(candidates, spot, strategy, sw,
                                   lot_size, target_delta)
        if result is None:
            return []
        sell, buy, m = result

        if strategy == "BEAR_CALL":
            return [
                ("SELL", sell["strike"], "CE", sell["ce_sym"], sell["ce_ltp"], m),
                ("BUY",  buy["strike"],  "CE", buy["ce_sym"],  buy["ce_ltp"],  m),
            ]
        else:
            return [
                ("SELL", sell["strike"], "PE", sell["pe_sym"], sell["pe_ltp"], m),
                ("BUY",  buy["strike"],  "PE", buy["pe_sym"],  buy["pe_ltp"],  m),
            ]

    elif strategy == "IRON_CONDOR":
        bl = select_strikes(chain, spot, "BEAR_CALL", T, r, target_delta, lot_size, sw)
        pl = select_strikes(chain, spot, "BULL_PUT",  T, r, target_delta, lot_size, sw)
        if not bl or not pl:
            return []
        bm, pm = bl[0][5], pl[0][5]
        # Iron condor:
        #   max_profit = sum of both credits (collect from both sides)
        #   max_loss   = max of either spread's loss (only one side breached at a time)
        nc = round(bm["net_credit"] + pm["net_credit"], 2)
        ml = round(max(bm["max_loss"], pm["max_loss"]), 2)
        pk = round(bm["peak_margin_est"] + pm["peak_margin_est"], 2)
        cr = round(nc / (ml + nc) if (ml + nc) > 0 else 0, 4)
        cm = {
            "credit_per_share": round(bm["credit_per_share"] + pm["credit_per_share"], 2),
            "net_credit":       nc,
            "max_profit":       nc,
            "max_loss":         ml,
            "margin_required":  ml,
            "peak_margin_est":  pk,
            "credit_ratio":     cr,
            "rr_ratio":         round(nc / ml, 4) if ml > 0 else 0,
        }
        return [(l[0], l[1], l[2], l[3], l[4], cm) for l in bl + pl]

    return []
