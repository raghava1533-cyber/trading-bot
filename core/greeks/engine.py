import math

def greeks_fd(spot, ce_strike, pe_strike):
    # simple finite difference placeholder
    return {
        "delta": (spot - ce_strike) * 0.001,
        "gamma": 0.0001,
        "theta": -0.01,
        "vega": 0.05
    }