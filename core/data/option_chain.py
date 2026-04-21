import requests

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

def get_nse_option_chain(index="NIFTY"):
    # Simple in-memory cache for 30 seconds
    if not hasattr(get_nse_option_chain, "_cache"):
        get_nse_option_chain._cache = {"data": None, "spot": 0, "last_fetch": None}
    import time
    now = time.time()
    cache = get_nse_option_chain._cache
    if cache["data"] is not None and cache["last_fetch"] and now - cache["last_fetch"] < 30:
        return cache["data"], cache["spot"]
    try:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={index}"
        session.get("https://www.nseindia.com")  # cookie
        r = session.get(url, timeout=5)
        data = r.json()
        records = data.get("records", {})
        chain = records.get("data", [])
        spot = records.get("underlyingValue", 0)
        # Update cache
        cache["data"] = chain
        cache["spot"] = spot
        cache["last_fetch"] = now
        return chain, spot
    except Exception as e:
        # Optionally log error here
        return [], 0