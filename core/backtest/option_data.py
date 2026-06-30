"""Stub for historical option chain provider (CSV/Parquet)."""
import logging
import pandas as pd

class HistoricalOptionChainProvider:
    def __init__(self, path="", fmt="auto", tolerance_minutes=30):
        self.path = path
        self.fmt  = fmt
        self.tol  = tolerance_minutes
        self._data = None

    def load(self):
        if not self.path:
            raise RuntimeError("No option_data_path configured")
        logging.info(f"Loading historical option data from {self.path}")
        if self.fmt == "parquet" or self.path.endswith(".parquet"):
            self._data = pd.read_parquet(self.path)
        else:
            self._data = pd.read_csv(self.path)
        logging.info(f"Loaded {len(self._data)} rows")

    def get_chain(self, timestamp, index):
        return None  # stub — returns None so synthetic fallback is used
