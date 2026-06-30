import logging, math
from scipy.stats import norm
from config import SETTINGS

SPREAD_WIDTH = SETTINGS.spread_width_points

def _bs_delta(S, K, T, r, sigma, opt_type):
    if T <= 0 or sigma <= 0:
        return 0.5 if opt_type == "CE" else -0.5
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm.cdf(d1) if opt_type == "CE" else norm.cdf(d1) - 1

def _span_margin(spot, lot_size, psr=0.065, exposure=0.03):
    return round(spot * lot_size * (psr + exposure), 2)

def _margin_for_spread(sell_ltp, buy_ltp, sw_points, lot_size=75, spot=None):
    """
    sw_points : spread width in index POINTS (e.g. 200)
    sell_ltp  : premium received per share (Rs)
    buy_ltp   : premium paid per share (Rs)

    net_credit  = (sell_ltp - buy_ltp) * lot_size          [Rs]
    max_profit  = net_credit                                [Rs]
    max_loss    = (sw_points - (sell_ltp - buy_ltp)) * lot_size  [Rs]
                  i.e. spread width in Rs minus credit received
    """
    sell_ltp = sell_ltp or 0.0
    buy_ltp  = buy_ltp  or 0.0
    cps      = round(sell_ltp - buy_ltp, 2)          # credit per share (Rs)
    nc       = round(cps * lot_size, 2)               # net credit (Rs)
    mp       = nc                                     # max profit = net credit
    ml       = round(max(sw_points - cps, 0) * lot_size, 2)  # max loss (Rs)
    peak     = _span_margin(spot, lot_size) if spot else ml
    return {
        "credit_per_share": cps,
        "net_credit":       nc,
        "max_profit":       mp,
        "max_loss":         ml,
        "margin_required":  ml,
        "peak_margin_est":  peak,
    }

def select_strikes(chain, spot, strategy, T=0.1, r=0.06, target_delta=0.30,
                   lot_size=75, spread_width=None):
    sw = int(spread_width) if spread_width is not None else SPREAD_WIDTH
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

    if strategy == "BEAR_CALL":
        otm = sorted(
            [c for c in candidates if c["strike"] > spot],
            key=lambda x: abs(x["ce_delta"] - target_delta),
        )
        if not otm:
            return []
        sell = otm[0]
        buys = sorted(
            [c for c in candidates if c["strike"] >= sell["strike"] + sw],
            key=lambda x: x["strike"],
        )
        if not buys:
            logging.warning(f"BEAR_CALL: no buy leg {sw}pts above {sell['strike']}")
            return []
        buy = buys[0]
        m = _margin_for_spread(sell["ce_ltp"], buy["ce_ltp"], sw, lot_size, spot)
        return [
            ("SELL", sell["strike"], "CE", sell["ce_sym"], sell["ce_ltp"], m),
            ("BUY",  buy["strike"],  "CE", buy["ce_sym"],  buy["ce_ltp"],  m),
        ]

    elif strategy == "BULL_PUT":
        otm = sorted(
            [c for c in candidates if c["strike"] < spot],
            key=lambda x: abs(abs(x["pe_delta"]) - target_delta),
        )
        if not otm:
            return []
        sell = otm[0]
        buys = sorted(
            [c for c in candidates if c["strike"] <= sell["strike"] - sw],
            key=lambda x: x["strike"], reverse=True,
        )
        if not buys:
            logging.warning(f"BULL_PUT: no buy leg {sw}pts below {sell['strike']}")
            return []
        buy = buys[0]
        m = _margin_for_spread(sell["pe_ltp"], buy["pe_ltp"], sw, lot_size, spot)
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
        # Iron condor: collect credit from both spreads
        # Max profit = total net credit
        # Max loss   = max of either spread's max loss (only one side can be breached)
        nc = round(bm["net_credit"] + pm["net_credit"], 2)
        ml = round(max(bm["max_loss"], pm["max_loss"]), 2)
        pk = round(bm["peak_margin_est"] + pm["peak_margin_est"], 2)
        cm = {
            "credit_per_share": round(bm["credit_per_share"] + pm["credit_per_share"], 2),
            "net_credit":       nc,
            "max_profit":       nc,
            "max_loss":         ml,
            "margin_required":  ml,
            "peak_margin_est":  pk,
        }
        return [(l[0], l[1], l[2], l[3], l[4], cm) for l in bl + pl]

    return []
