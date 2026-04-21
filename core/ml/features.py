import pandas as pd
import numpy as np

def compute_features(df):
    df = df.copy()

    df["returns"] = df["close"].pct_change()
    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema21"] = df["close"].ewm(span=21).mean()
    df["ema_gap"] = (df["ema9"] - df["ema21"]) / df["ema21"]

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()

    df["rsi"] = 100 - (100 / (1 + gain / (loss + 1e-9)))
    df["vol"] = df["returns"].rolling(20).std()

    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()

    ma = df["close"].rolling(20).mean()
    sd = df["close"].rolling(20).std()

    df["bb_width"] = (2 * sd) / ma

    df = df.dropna()

    return df


def label_regime(df):
    df["future_ret"] = df["close"].shift(-5) / df["close"] - 1

    df["regime"] = np.where(
        df["future_ret"] > 0.005, "BULL",
        np.where(df["future_ret"] < -0.005, "BEAR", "SIDE")
    )

    return df.dropna()