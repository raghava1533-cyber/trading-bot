import math
from scipy.stats import norm

def black_scholes_delta(S, K, T, r, sigma, opt_type):
    if T <= 0 or sigma <= 0:
        return 0.5 if opt_type == "CE" else -0.5
    d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    return norm.cdf(d1) if opt_type == "CE" else norm.cdf(d1) - 1

def greeks_fd(spot, call_strike, put_strike, T=0.1, r=0.06, sigma=0.18):
    """Black-Scholes delta for call and put legs."""
    return {
        "call_delta": black_scholes_delta(spot, call_strike, T, r, sigma, "CE"),
        "put_delta":  black_scholes_delta(spot, put_strike,  T, r, sigma, "PE"),
    }
