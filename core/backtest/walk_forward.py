""Walk-forward validation for the regime XGBoost model."""
import logging
import pandas as pd
from ml.regime_xgb import train as train_model, FEATURE_COLS, label as lbl

def walk_forward(df: pd.DataFrame, step: int = 100) -> float:
    results = []
    for i in range(200, len(df)-step, step):
        try:
            model = train_model(df.iloc[:i].copy())
        except Exception as exc:
            logging.warning(f"walk_forward bar {i}: {exc}")
            continue
        test = lbl(df.iloc[i:i+step].copy())
        if test.empty: continue
        preds = model.predict(test[FEATURE_COLS])
        acc   = (preds == test["y"]).mean()
        results.append(acc)
        logging.info(f"  bar {i}: acc={acc:.3f}")
    if not results:
        return 0.0
    mean = sum(results)/len(results)
    logging.info(f"walk_forward mean acc: {mean:.3f} over {len(results)} windows")
    return mean
