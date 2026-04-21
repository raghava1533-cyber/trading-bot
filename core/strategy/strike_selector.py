# strategy/strike_selector.py
import numpy as np

def select_strikes(chain, spot, target_delta, iv_fn):
    candidates = []

    for row in chain:
        K = row["strikePrice"]

        ce = row.get("CE")
        pe = row.get("PE")

        if not ce or not pe:
            continue

        iv = iv_fn(K)

        oi = ce.get("openInterest", 0) + pe.get("openInterest", 0)

        skew = ce.get("impliedVolatility",0) - pe.get("impliedVolatility",0)

        score = oi * (1 - abs(skew))

        candidates.append((K, score))

    candidates.sort(key=lambda x: x[1], reverse=True)

    # choose strikes around ATM
    atm = min(candidates, key=lambda x: abs(x[0]-spot))[0]

    return atm+100, atm-100