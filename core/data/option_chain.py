from nsepython import option_chain
import time

def get_nse_option_chain(symbol="NIFTY", retries=3):
    for attempt in range(retries):
        try:
            data = option_chain(symbol)

            if not data or "records" not in data:
                print("⚠️ NSE empty response, retrying...")
                time.sleep(1)
                continue

            records = data["records"]
            spot = records.get("underlyingValue", 0)

            chain = []

            for row in records.get("data", []):
                strike = row.get("strikePrice")

                ce = row.get("CE", {})
                pe = row.get("PE", {})

                chain.append({
                    "strikePrice": strike,
                    "CE": {
                        "ltp": ce.get("lastPrice"),
                        "oi": ce.get("openInterest"),
                        "iv": ce.get("impliedVolatility"),
                    },
                    "PE": {
                        "ltp": pe.get("lastPrice"),
                        "oi": pe.get("openInterest"),
                        "iv": pe.get("impliedVolatility"),
                    }
                })

            if not chain:
                print("⚠️ Empty chain parsed")
                continue

            return chain, spot

        except Exception as e:
            print(f"⚠️ NSE fetch error: {e}")
            time.sleep(1)

    return None, 0