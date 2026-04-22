from scipy.stats import norm
import math
def black_scholes_delta(S, K, T, r, sigma, opt_type):
    d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    return norm.cdf(d1) if opt_type == "CE" else norm.cdf(d1) - 1

# Placeholder greeks_fd function for use in main_async.py
def greeks_fd(spot, call_strike, put_strike, T=0.1, r=0.05, sigma=0.2):
    """
    Calculate delta for a call and a put as a simple example.
    Returns a dict with call_delta and put_delta.
    """
    call_delta = black_scholes_delta(spot, call_strike, T, r, sigma, "CE")
    put_delta = black_scholes_delta(spot, put_strike, T, r, sigma, "PE")
    return {"call_delta": call_delta, "put_delta": put_delta}