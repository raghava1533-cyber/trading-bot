import pandas as pd
import numpy as np

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["returns"] = df["close"].pct_change()
    df["ema9"]    = df["close"].ewm(span=9,  adjust=False).mean()
    df["ema21"]   = df["close"].ewm(span=21, adjust=False).mean()
    df["ema_gap"] = (df["ema9"] - df["ema21"]) / (df["ema21"] + 1e-9)
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"]      = 100 - (100 / (1 + gain / (loss + 1e-9)))
    df["vol"]      = df["returns"].rolling(20).std()
    df["atr"]      = (df["high"] - df["low"]).rolling(14).mean()
    ma = df["close"].rolling(20).mean()
    sd = df["close"].rolling(20).std()
    df["bb_width"] = (2 * sd) / (ma + 1e-9)
    return df.dropna().reset_index(drop=True)
