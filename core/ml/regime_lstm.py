import numpy as np
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense

def prepare_data(df, features):
    X = df[features].values
    y = df["regime"].map({"BULL":0,"SIDE":1,"BEAR":2}).values

    X_seq = []
    y_seq = []

    for i in range(20, len(X)):
        X_seq.append(X[i-20:i])
        y_seq.append(y[i])

    return np.array(X_seq), np.array(y_seq)


def build_lstm(input_shape):
    model = Sequential([
        LSTM(64, return_sequences=True, input_shape=input_shape),
        LSTM(32),
        Dense(3, activation="softmax")
    ])

    model.compile(loss="sparse_categorical_crossentropy", optimizer="adam")
    return model