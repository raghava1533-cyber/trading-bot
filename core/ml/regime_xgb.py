mport os, time, joblib, logging
import numpy as np
from xgboost import XGBClassifier
from config import SETTINGS
from ml.features import compute_features

MODEL_PATH   = SETTINGS.model_path
FEATURE_COLS = ["returns","ema9","ema21","ema_gap","rsi","vol","atr","bb_width"]

_MODEL_MAX_AGE_DAYS  = int(os.getenv("MODEL_MAX_AGE_DAYS", "7"))
_RETRAIN_YEARS       = 5
_SIDE_VOL_PERCENTILE = float(os.getenv("SIDE_VOL_PERCENTILE", "33"))

_LABEL_MAP = {0: "SIDE", 1: "BULL", 2: "BEAR"}


def label(df):
    df = df.copy().dropna(subset=FEATURE_COLS)
    if df.empty:
        return df
    vol_threshold = float(np.percentile(df["vol"], _SIDE_VOL_PERCENTILE))
    conditions = [
        df["vol"] < vol_threshold,
        (df["vol"] >= vol_threshold) & (df["ema9"] > df["ema21"]),
        (df["vol"] >= vol_threshold) & (df["ema9"] <= df["ema21"]),
    ]
    df["y"]             = np.select(conditions, [0, 1, 2], default=0).astype(int)
    df["vol_threshold"] = vol_threshold
    return df


def _compute_features_per_index(frames):
    import pandas as pd
    result = []
    for df in frames:
        feat = compute_features(df.copy())
        if feat is not None and not feat.empty:
            result.append(feat)
    if not result:
        return pd.DataFrame()
    return (pd.concat(result, ignore_index=True)
              .sort_values("timestamp")
              .reset_index(drop=True))


def train(df_or_frames):
    if isinstance(df_or_frames, list):
        combined = _compute_features_per_index(df_or_frames)
    elif all(c in df_or_frames.columns for c in FEATURE_COLS):
        combined = df_or_frames.copy()
    else:
        combined = compute_features(df_or_frames.copy())

    labelled = label(combined)
    if labelled.empty:
        raise RuntimeError("No labelled data for training")

    X       = labelled[FEATURE_COLS]
    y       = labelled["y"].astype(int)
    counts  = y.value_counts().sort_index().to_dict()
    vol_thr = float(labelled["vol_threshold"].iloc[0])

    logging.info(f"  Vol threshold (p{_SIDE_VOL_PERCENTILE:.0f}): {vol_thr:.6f}")
    logging.info(f"  Labels -> SIDE={counts.get(0,0)}  BULL={counts.get(1,0)}  BEAR={counts.get(2,0)}")

    model = XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="multi:softprob", num_class=3,
        eval_metric="mlogloss", random_state=42,
    )
    model.fit(X, y)

    model_dir = os.path.dirname(MODEL_PATH)
    if model_dir:
        os.makedirs(model_dir, exist_ok=True)
    joblib.dump({"model": model, "vol_threshold": vol_thr}, MODEL_PATH)
    logging.info(f"  Model saved -> {MODEL_PATH}  ({len(labelled)} samples)")
    model._vol_threshold = vol_thr
    return model


def _load_model_file(path):
    obj = joblib.load(path)
    if isinstance(obj, dict) and "model" in obj:
        m = obj["model"]
        m._vol_threshold = obj.get("vol_threshold")
        return m
    return obj


def _model_age_days():
    if not os.path.exists(MODEL_PATH):
        return float("inf")
    return (time.time() - os.path.getmtime(MODEL_PATH)) / 86400


def load_model(broker=None, force_retrain=False):
    age = _model_age_days()
    if not force_retrain and age <= _MODEL_MAX_AGE_DAYS:
        logging.info(f"Loading saved model (age {age:.1f}d)")
        return _load_model_file(MODEL_PATH)

    reason = ("no model" if age == float("inf") else
              "forced"   if force_retrain else
              f"{age:.1f}d old - retraining")
    logging.info(f"Model {reason}")

    from data.candles import fetch_candles
    from config import INDEX_CONFIG

    frames = []
    for idx in SETTINGS.active_indices:
        cfg = INDEX_CONFIG.get(idx, {})
        try:
            df = fetch_candles(ticker=cfg.get("yf_ticker","^NSEI"),
                               interval="1d", days=365*_RETRAIN_YEARS, broker=broker)
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as exc:
            logging.warning(f"  {idx}: candle fetch failed - {exc}")

    if not frames:
        if os.path.exists(MODEL_PATH):
            logging.warning("No data for retrain - using existing model")
            return _load_model_file(MODEL_PATH)
        raise RuntimeError("No data and no saved model")

    return train(frames)


def predict_regime(model, df) -> str:
    df = compute_features(df.copy())
    if df.empty:
        return "SIDE"
    row = df[FEATURE_COLS].iloc[[-1]]
    vol = float(row["vol"].values[0])
    vol_threshold = getattr(model, "_vol_threshold", None)
    if vol_threshold is None:
        vol_threshold = float(np.percentile(df["vol"].dropna(), _SIDE_VOL_PERCENTILE))
    if vol < vol_threshold:
        return "SIDE"
    pred = int(model.predict(row)[0])
    return _LABEL_MAP.get(pred, "SIDE")
