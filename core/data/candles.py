import yfinance as yf
import pandas as pd

def fetch_candles(days=30):
    import yfinance as yf
    import pandas as pd

    df = yf.download("^NSEI", interval="15m", period=f"{days}d", progress=False)

    if df is None or df.empty:
        raise RuntimeError("No data from yfinance")

    # ✅ Flatten columns safely
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() for col in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]

    # ✅ Reset index
    df = df.reset_index()

    # 🔥 UNIVERSAL timestamp fix
    possible_cols = ["datetime", "date", "index"]

    found = None
    for col in possible_cols:
        if col in df.columns:
            found = col
            break

    if found is None:
        raise RuntimeError(f"No time column found. Columns: {df.columns}")

    df.rename(columns={found: "timestamp"}, inplace=True)

    # ✅ Ensure datetime format
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    # ✅ Clean data
    df = df.dropna(subset=["timestamp", "close"])
    df = df.drop_duplicates(subset=["timestamp"])

    # Optional sort
    df = df.sort_values("timestamp")

    return df