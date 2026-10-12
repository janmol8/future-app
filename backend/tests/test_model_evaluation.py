import numpy as np
import pandas as pd

import model_evaluation
from model_evaluation import evaluate_model_on_candles


def make_candles(count=240):
    candles = []
    price = 100.0
    for i in range(count):
        price += 1 if i % 3 else -0.5
        candles.append({
            "time": 1700000000 + i * 86400,
            "open": price - 0.2,
            "high": price + 1,
            "low": price - 1,
            "close": price,
            "volume": 1_000_000,
        })
    return candles


class DummyModel:
    def predict_proba(self, X):
        probs = np.where(np.arange(len(X)) % 2 == 0, 0.7, 0.3)
        return np.column_stack([1 - probs, probs])


class DummyAIEngine:
    def __init__(self, model_path):
        self.model = DummyModel()

    def engineer_features(self, candles, sr_levels):
        df = pd.DataFrame(candles)
        df["ema20"] = df["close"]
        df["ema50"] = df["close"]
        df["ema200"] = df["close"]
        df["feature"] = np.arange(len(df))
        return df


def test_evaluate_model_returns_metrics(monkeypatch):
    monkeypatch.setattr(model_evaluation, "AIEngine", DummyAIEngine)
    result = evaluate_model_on_candles(make_candles(), model_path="dummy.joblib")
    assert result.samples > 0
    assert result.accuracy is not None
    assert result.win_rate is not None
    assert result.max_drawdown_pct is not None
    assert result.validation_status == "historical_backtest_completed"
    assert "education" in result.disclaimer.lower()


def test_evaluate_model_handles_insufficient_data():
    result = evaluate_model_on_candles(make_candles(30), model_path="missing.joblib")
    assert result.samples == 0
    assert result.accuracy is None
    assert result.validation_status == "insufficient_data"
