from scipy.stats import norm
import math
def black_scholes_delta(S, K, T, r, sigma, opt_type):
    d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    return norm.cdf(d1) if opt_type == "CE" else norm.cdf(d1) - 1