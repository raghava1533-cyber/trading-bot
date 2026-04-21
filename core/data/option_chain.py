import requests

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

def get_nse_option_chain(index="NIFTY"):
    try:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={index}"
        session.get("https://www.nseindia.com")  # cookie

        r = session.get(url, timeout=5)
        data = r.json()

        records = data.get("records", {})
        return records.get("data", []), records.get("underlyingValue", 0)

    except Exception as e:
        print("Option chain error:", e)
        return [], 0