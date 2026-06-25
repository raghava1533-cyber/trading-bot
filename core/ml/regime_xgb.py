import os, time, joblib, logging
import numpy as np
from xgboost import XGBClassifier
from config import SETTINGS
from ml.features import compute_features

MODEL_PATH   = SETTINGS.model_path
FEATURE_COLS = ["returns","ema9","ema21","ema_gap","rsi","vol","atr","bb_width"]
_MODEL_MAX_AGE_DAYS = int(os.getenv("MODEL_MAX_AGE_DAYS","7"))
_RETRAIN_YEARS = 5
_LOW_VOL_THRESHOLD  = float(os.getenv("REGIME_LOW_VOL",  "0.007"))
_HIGH_VOL_THRESHOLD = float(os.getenv("REGIME_HIGH_VOL", "0.011"))

def label(df):
    df = df.copy()
    df = compute_features(df)
    df = df.dropna(subset=FEATURE_COLS)
    conditions = [
        (df["vol"] > _HIGH_VOL_THRESHOLD) & (df["ema9"] > df["ema21"]),
        (df["vol"] > _HIGH_VOL_THRESHOLD) & (df["ema9"] < df["ema21"]),
    ]
    df["y"] = np.select(conditions, [1, 2], default=0)
    return df.dropna(subset=["y"])

def train(df):
    df = label(df)
    if df.empty:
        raise RuntimeError("No labelled data for training")
    X = df[FEATURE_COLS]
    y = df["y"]
    counts = y.value_counts().to_dict()
    logging.info(f"  Regime labels: SIDE={counts.get(0,0)} BULL={counts.get(1,0)} BEAR={counts.get(2,0)}")
    model = XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05,
                          eval_metric="mlogloss", num_class=3, objective="multi:softprob")
    model.fit(X, y)
    model_dir = os.path.dirname(MODEL_PATH)
    if model_dir:
        os.makedirs(model_dir, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    logging.info(f"Model saved -> {MODEL_PATH} ({len(df)} samples)")
    return model

def _model_age_days():
    if not os.path.exists(MODEL_PATH):
        return float("inf")
    return (time.time() - os.path.getmtime(MODEL_PATH)) / 86400

def load_model(broker=None, force_retrain=False):
    age = _model_age_days()
    if not force_retrain and age <= _MODEL_MAX_AGE_DAYS:
        logging.info(f"Loading saved model (age {age:.1f}d)")
        return joblib.load(MODEL_PATH)
    reason = "no model" if age == float("inf") else ("forced" if force_retrain else f"{age:.1f}d old")
    logging.info(f"Retraining model — {reason}")
    from data.candles import fetch_candles
    from config import INDEX_CONFIG
    frames = []
    for idx in SETTINGS.active_indices:
        cfg = INDEX_CONFIG.get(idx, {})
        try:
            df = fetch_candles(ticker=cfg.get("yf_ticker","^NSEI"), interval="1d",
                               days=365*_RETRAIN_YEARS, broker=broker)
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as exc:
            logging.warning(f"  {idx}: candle fetch failed — {exc}")
    if not frames:
        if os.path.exists(MODEL_PATH):
            logging.warning("No data for retrain — using existing model")
            return joblib.load(MODEL_PATH)
        raise RuntimeError("No data and no saved model")
    import pandas as pd
    combined = pd.concat(frames, ignore_index=True).sort_values("timestamp").reset_index(drop=True)
    return train(combined)

def predict_regime(model, df):
    df = compute_features(df)
    if df.empty:
        return "SIDE"
    row = df[FEATURE_COLS].iloc[[-1]]
    vol = float(row["vol"].values[0])
    if vol < _LOW_VOL_THRESHOLD:
        return "SIDE"
    pred = int(model.predict(row)[0])
    return {0:"SIDE", 1:"BULL", 2:"BEAR"}.get(pred, "SIDE")
