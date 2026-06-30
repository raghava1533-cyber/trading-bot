""
train_model.py  -  Train the XGBoost regime model

Usage:
  python core/train_model.py
  python core/train_model.py --years 5 --indices NIFTY BANKNIFTY SENSEX
  python core/train_model.py --years 3 --indices NIFTY
"""
import argparse, logging, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Train XGBoost regime model")
    parser.add_argument("--years",   type=int,  default=5)
    parser.add_argument("--indices", nargs="+", default=None)
    parser.add_argument("--force",   action="store_true")
    args = parser.parse_args()

    from config import SETTINGS, INDEX_CONFIG
    from data.candles import fetch_candles
    from ml.features import compute_features
    from ml.regime_xgb import train, label, FEATURE_COLS, _compute_features_per_index
    import pandas as pd

    indices = [i.upper() for i in args.indices] if args.indices else list(INDEX_CONFIG.keys())
    days    = args.years * 365

    log.info("=" * 60)
    log.info("  XGBoost Regime Model - Training")
    log.info("=" * 60)
    log.info(f"  Indices : {', '.join(indices)}")
    log.info(f"  Years   : {args.years}  ({days} days)")
    log.info(f"  Features: {FEATURE_COLS}")
    log.info(f"  Output  : {SETTINGS.model_path}")
    log.info("-" * 60)

    # -- Connect broker -------------------------------------------------------
    broker = None
    try:
        from broker.upstox import Broker
        broker = Broker()
        log.info("Broker connected - will try Upstox candles first")
    except Exception as exc:
        log.warning(f"Broker unavailable ({exc}) - using yFinance only")

    # -- Fetch raw candles per index ------------------------------------------
    raw_frames = []
    for idx in indices:
        cfg = INDEX_CONFIG.get(idx)
        if not cfg:
            log.warning(f"  {idx}: not in INDEX_CONFIG - skipping")
            continue
        log.info(f"  Fetching {idx} ({cfg['yf_ticker']}, {days}d)...")
        try:
            df = fetch_candles(ticker=cfg["yf_ticker"], interval="1d",
                               days=days, broker=broker)
            if df is None or df.empty:
                log.warning(f"  {idx}: no data - skipping")
                continue
            log.info(f"  {idx}: {len(df)} bars  "
                     f"({df['timestamp'].iloc[0].date()} -> "
                     f"{df['timestamp'].iloc[-1].date()})")
            raw_frames.append(df)
        except Exception as exc:
            log.warning(f"  {idx}: fetch failed - {exc}")

    if not raw_frames:
        log.error("No data fetched. Cannot train.")
        sys.exit(1)

    # -- Compute features per-index (prevents rolling bleed across indices) ---
    log.info("Computing features per index...")
    feat_df = _compute_features_per_index(raw_frames)
    log.info(f"Combined feature dataset: {len(feat_df)} bars")

    # -- Label and show distribution ------------------------------------------
    labelled = label(feat_df)
    if labelled.empty:
        log.error("No labelled samples. Cannot train.")
        sys.exit(1)

    counts = labelled["y"].value_counts().sort_index()
    total  = len(labelled)
    label_names = {0: "SIDE", 1: "BULL", 2: "BEAR"}
    log.info("-" * 60)
    log.info("Regime label distribution:")
    for k, n in counts.items():
        log.info(f"  {label_names.get(k,k):<6} ({k}): {n:>5}  ({n/total*100:.1f}%)")
    log.info(f"  Total        : {total}")
    log.info("-" * 60)

    # -- Train on pre-computed feature df ------------------------------------
    log.info("Training XGBoost model...")
    model = train(feat_df)   # feat_df already has features - train() detects this

    # -- Hold-out accuracy on last 20% ----------------------------------------
    split   = int(len(labelled) * 0.8)
    test_df = labelled.iloc[split:]
    if not test_df.empty:
        X_test = test_df[FEATURE_COLS]
        y_test = test_df["y"]
        preds  = model.predict(X_test)
        acc    = (preds == y_test).mean()
        log.info(f"Hold-out accuracy (last 20%): {acc*100:.1f}%")
        for k in sorted(y_test.unique()):
            mask  = y_test == k
            c_acc = (preds[mask] == y_test[mask]).mean() if mask.sum() > 0 else 0
            log.info(f"  {label_names.get(k,k):<6}: {c_acc*100:.1f}%  ({mask.sum()} samples)")

    log.info("=" * 60)
    log.info(f"Model saved -> {SETTINGS.model_path}")
    log.info("Done! Run the bot:  python core/main_async.py")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
