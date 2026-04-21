import yfinance as yf
import pandas as pd

def fetch_candles(days=30):
    # Simple in-memory cache for last candles
    if not hasattr(fetch_candles, "_cache"):
        fetch_candles._cache = {"df": None, "last_fetch": None}

    import time
    now = time.time()
    cache = fetch_candles._cache
    # Cache for 60 seconds
    if cache["df"] is not None and cache["last_fetch"] and now - cache["last_fetch"] < 60:
        return cache["df"].copy()

    df = yf.download("^NSEI", interval="15m", period=f"{days}d", progress=False)
    if df is None or df.empty:
        raise RuntimeError("No data from yfinance")

    df = df.reset_index()
    new_cols = []
    for col in df.columns:
        if isinstance(col, tuple):
            clean = col[0] if col[0] else col[1]
        else:
            clean = col
        new_cols.append(str(clean).lower())
    df.columns = new_cols

    # Timestamp fix
    if "datetime" in df.columns:
        df.rename(columns={"datetime": "timestamp"}, inplace=True)
    elif "date" in df.columns:
        df.rename(columns={"date": "timestamp"}, inplace=True)
    elif "index" in df.columns:
        df.rename(columns={"index": "timestamp"}, inplace=True)
    else:
        raise RuntimeError(f"No time column found AFTER FIX. Columns: {df.columns}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp", "close"])
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp")
    if "volume" not in df.columns or df["volume"].sum() == 0:
        df["volume"] = 1

    # Update cache
    cache["df"] = df.copy()
    cache["last_fetch"] = now
    return df