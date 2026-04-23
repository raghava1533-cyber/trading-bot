import yfinance as yf
import pandas as pd
import time

# Cache per ticker
_cache = {}

def fetch_candles(days=30, ticker="^NSEI"):
    """
    Fetch 15-min candles for the given yfinance ticker.
    Cached for 60 seconds per ticker.

    Tickers:
        NIFTY     -> ^NSEI
        BANKNIFTY -> ^NSEBANK
        SENSEX    -> ^BSESN
    """
    global _cache

    now   = time.time()
    entry = _cache.get(ticker)

    if entry and entry["df"] is not None and now - entry["last_fetch"] < 60:
        return entry["df"].copy()

    df = yf.download(ticker, interval="15m", period=f"{days}d", progress=False)

    if df is None or df.empty:
        raise RuntimeError(f"No data from yfinance for {ticker}")

    df = df.reset_index()

    # Flatten multi-level columns
    new_cols = []
    for col in df.columns:
        if isinstance(col, tuple):
            clean = col[0] if col[0] else col[1]
        else:
            clean = col
        new_cols.append(str(clean).lower())
    df.columns = new_cols

    # Standardise timestamp column name
    for candidate in ("datetime", "date", "index"):
        if candidate in df.columns:
            df.rename(columns={candidate: "timestamp"}, inplace=True)
            break
    else:
        raise RuntimeError(f"No time column found. Columns: {list(df.columns)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp", "close"])
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp")

    if "volume" not in df.columns or df["volume"].sum() == 0:
        df["volume"] = 1

    _cache[ticker] = {"df": df.copy(), "last_fetch": now}
    return df