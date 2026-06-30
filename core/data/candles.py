import logging
import time
import pandas as pd

_cache: dict = {}

def fetch_candles(days: int = 365, ticker: str = "^NSEI",
                  interval: str = "1d", broker=None) -> "pd.DataFrame | None":
    """Fetch OHLCV candles. Uses Upstox broker first, yFinance as fallback."""
    # ── Try Upstox broker ────────────────────────────────────────────────
    if broker is not None:
        try:
            from config import INDEX_CONFIG
            symbol = next(
                (k for k, v in INDEX_CONFIG.items() if v.get("yf_ticker") == ticker),
                None
            )
            upstox_interval = {"1d":"day","1wk":"week","1mo":"month",
                               "15m":"30minute","30m":"30minute"}.get(interval, "day")
            if symbol:
                df = broker.get_candles(symbol=symbol, interval=upstox_interval, days=days)
                if df is not None and not df.empty:
                    logging.info(f"fetch_candles: {symbol} via Upstox ({len(df)} bars)")
                    return df
        except Exception as exc:
            logging.warning(f"fetch_candles: Upstox failed ({exc}), falling back to yFinance")

    # ── yFinance fallback ────────────────────────────────────────────────
    cache_key = f"{ticker}_{interval}_{days}"
    now   = time.time()
    entry = _cache.get(cache_key)
    if entry and entry["df"] is not None and now - entry["last_fetch"] < 300:
        return entry["df"].copy()

    last_error = None
    for attempt in range(3):
        try:
            import yfinance as yf
            period = f"{min(days, 729)}d"
            df = yf.download(ticker, interval=interval, period=period,
                             progress=False, auto_adjust=True)
            if df is None or df.empty:
                raise RuntimeError(f"Empty response from yfinance")
            df = df.reset_index()
            new_cols = []
            for col in df.columns:
                c = col[0] if isinstance(col, tuple) else col
                new_cols.append(str(c).lower())
            df.columns = new_cols
            for cand in ("datetime","date","index"):
                if cand in df.columns:
                    df.rename(columns={cand:"timestamp"}, inplace=True)
                    break
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            df = df.dropna(subset=["timestamp","close"])
            df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
            if "volume" not in df.columns or df["volume"].isna().all():
                df["volume"] = 1
            df["volume"] = df["volume"].fillna(1)
            if "high" not in df.columns: df["high"] = df["close"]
            if "low"  not in df.columns: df["low"]  = df["close"]
            _cache[cache_key] = {"df": df.copy(), "last_fetch": now}
            logging.info(f"fetch_candles: {ticker} via yFinance ({len(df)} bars)")
            return df
        except Exception as exc:
            last_error = exc
            logging.warning(f"yFinance attempt {attempt+1} failed: {exc}")
            time.sleep(1)

    raise RuntimeError(f"No data from yfinance for {ticker}; last_error={last_error}")
