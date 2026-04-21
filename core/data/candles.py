import yfinance as yf
import pandas as pd

def fetch_candles(days=30):
    import yfinance as yf
    import pandas as pd

    df = yf.download("^NSEI", interval="15m", period=f"{days}d", progress=False)

    if df is None or df.empty:
        raise RuntimeError("No data from yfinance")

    # 🔥 FORCE reset index FIRST
    df = df.reset_index()

    # 🔥 FIX MultiIndex properly
    new_cols = []
    for col in df.columns:
        if isinstance(col, tuple):
            # take first non-empty part
            clean = col[0] if col[0] else col[1]
        else:
            clean = col
        new_cols.append(str(clean).lower())

    df.columns = new_cols

    # 🔍 DEBUG (remove later)
    print("Fixed columns:", df.columns)

    # 🔥 TIMESTAMP FIX
    if "datetime" in df.columns:
        df.rename(columns={"datetime": "timestamp"}, inplace=True)
    elif "date" in df.columns:
        df.rename(columns={"date": "timestamp"}, inplace=True)
    elif "index" in df.columns:
        df.rename(columns={"index": "timestamp"}, inplace=True)
    else:
        raise RuntimeError(f"No time column found AFTER FIX. Columns: {df.columns}")

    # ✅ Convert timestamp
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    # ✅ Clean
    df = df.dropna(subset=["timestamp", "close"])
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp")

    # 🔥 volume fallback (important for your ML)
    if "volume" not in df.columns or df["volume"].sum() == 0:
        df["volume"] = 1

    return df