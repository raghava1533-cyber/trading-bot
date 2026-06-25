"""
ml/regime_lstm.py
LSTM-based regime model — stub only.
tensorflow is NOT a project dependency (too heavy).
Use ml/regime_xgb.py for the active regime model.
"""
import logging

log = logging.getLogger(__name__)


def prepare_data(df, features, seq_len: int = 20):
    """Prepare sequential data for LSTM training (requires numpy)."""
    import numpy as np
    X = df[features].values
    y_map = {"BULL": 0, "SIDE": 1, "BEAR": 2}
    y = df["regime"].map(y_map).values
    X_seq, y_seq = [], []
    for i in range(seq_len, len(X)):
        X_seq.append(X[i - seq_len:i])
        y_seq.append(y[i])
    return np.array(X_seq), np.array(y_seq)


def build_lstm(input_shape):
    """
    Build a Keras LSTM model.
    Only call this if tensorflow/keras is installed.
    """
    try:
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout
        model = Sequential([
            LSTM(64, return_sequences=True, input_shape=input_shape),
            Dropout(0.2),
            LSTM(32),
            Dropout(0.2),
            Dense(3, activation="softmax"),
        ])
        model.compile(loss="sparse_categorical_crossentropy",
                      optimizer="adam", metrics=["accuracy"])
        return model
    except ImportError:
        log.error("tensorflow not installed — cannot build LSTM model. "
                  "Use ml/regime_xgb.py instead.")
        return None
