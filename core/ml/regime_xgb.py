import os
import joblib
import numpy as np

from xgboost import XGBClassifier

MODEL_PATH = "models/xgb.pkl"


def compute_features(df):
    df["ret"] = df["close"].pct_change()
    df["ema"] = df["close"].ewm(span=10).mean()
    df["vol"] = df["ret"].rolling(10).std()

    return df.dropna()


def label(df):
    df["future"] = df["close"].shift(-3)
    df["y"] = np.where(df["future"] > df["close"], "BULL", "BEAR")
    return df.dropna()


def train(df):
    df = compute_features(df)
    df = label(df)

    X = df[["ret", "ema", "vol"]]
    y = df["y"]

    model = XGBClassifier(n_estimators=50)
    model.fit(X, y)

    os.makedirs("models", exist_ok=True)
    joblib.dump(model, MODEL_PATH)

    return model


def load_model():
    if os.path.exists(MODEL_PATH):
        return joblib.load(MODEL_PATH)

    from data.candles import fetch_candles
    return train(fetch_candles())


def predict_regime(model, df):
    df = compute_features(df)

    if df.empty:
        return "SIDE"

    X = df[["ret", "ema", "vol"]].iloc[[-1]]
    return model.predict(X)[0]