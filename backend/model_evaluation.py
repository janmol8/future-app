import argparse
import json
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Sequence

import numpy as np

from ai_engine import AIEngine


@dataclass
class ModelEvaluationResult:
    samples: int
    accuracy: Optional[float]
    win_rate: Optional[float]
    avg_forward_return_pct: Optional[float]
    max_drawdown_pct: Optional[float]
    long_signals: int
    short_or_flat_signals: int
    validation_status: str
    disclaimer: str


EDUCATIONAL_EVAL_DISCLAIMER = (
    "Backtest metrics are historical simulation results for education only. "
    "They are not investment advice and do not predict future returns."
)


def _forward_returns(closes: Sequence[float], horizon: int) -> np.ndarray:
    arr = np.asarray(closes, dtype=float)
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if len(arr) <= horizon:
        return np.asarray([], dtype=float)
    return (arr[horizon:] - arr[:-horizon]) / np.where(arr[:-horizon] == 0, np.nan, arr[:-horizon])


def _max_drawdown_pct(equity_curve: Sequence[float]) -> Optional[float]:
    if not equity_curve:
        return None
    equity = np.asarray(equity_curve, dtype=float)
    peaks = np.maximum.accumulate(equity)
    drawdowns = (equity - peaks) / np.where(peaks == 0, np.nan, peaks)
    if np.all(np.isnan(drawdowns)):
        return None
    return round(float(np.nanmin(drawdowns) * 100), 2)


def evaluate_model_on_candles(
    candles: List[Dict],
    model_path: str = "models/xgboost_quant_v1.joblib",
    horizon: int = 1,
    threshold: float = 0.5,
) -> ModelEvaluationResult:
    """Evaluate the local model on supplied historical candles without fetching data."""
    if len(candles) < 220:
        return ModelEvaluationResult(
            samples=0,
            accuracy=None,
            win_rate=None,
            avg_forward_return_pct=None,
            max_drawdown_pct=None,
            long_signals=0,
            short_or_flat_signals=0,
            validation_status="insufficient_data",
            disclaimer=EDUCATIONAL_EVAL_DISCLAIMER,
        )

    engine = AIEngine(model_path=model_path)
    if engine.model is None:
        return ModelEvaluationResult(
            samples=0,
            accuracy=None,
            win_rate=None,
            avg_forward_return_pct=None,
            max_drawdown_pct=None,
            long_signals=0,
            short_or_flat_signals=0,
            validation_status="model_unavailable",
            disclaimer=EDUCATIONAL_EVAL_DISCLAIMER,
        )

    features = engine.engineer_features(candles, {})
    feature_cols = features.drop(
        ["time", "open", "high", "low", "close", "volume", "ema20", "ema50", "ema200"],
        axis=1,
        errors="ignore",
    )
    usable = len(feature_cols) - horizon
    if usable <= 0:
        return ModelEvaluationResult(
            samples=0,
            accuracy=None,
            win_rate=None,
            avg_forward_return_pct=None,
            max_drawdown_pct=None,
            long_signals=0,
            short_or_flat_signals=0,
            validation_status="insufficient_data",
            disclaimer=EDUCATIONAL_EVAL_DISCLAIMER,
        )

    X = feature_cols.iloc[:usable]
    closes = [float(c["close"]) for c in candles]
    fwd = _forward_returns(closes, horizon)[:usable]
    labels = fwd > 0

    probs = engine.model.predict_proba(X)
    bull_probs = probs[:, -1] if len(probs.shape) == 2 else np.asarray(probs, dtype=float)
    signals = bull_probs >= threshold
    accuracy = float(np.mean(signals == labels))

    signal_returns = np.where(signals, fwd, -fwd)
    equity = np.cumprod(1 + np.nan_to_num(signal_returns, nan=0.0))
    wins = signal_returns > 0

    return ModelEvaluationResult(
        samples=int(usable),
        accuracy=round(accuracy, 4),
        win_rate=round(float(np.mean(wins)), 4) if len(wins) else None,
        avg_forward_return_pct=round(float(np.nanmean(signal_returns) * 100), 4),
        max_drawdown_pct=_max_drawdown_pct(equity.tolist()),
        long_signals=int(np.sum(signals)),
        short_or_flat_signals=int(len(signals) - np.sum(signals)),
        validation_status="historical_backtest_completed",
        disclaimer=EDUCATIONAL_EVAL_DISCLAIMER,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the local Future App model on historical candles JSON.")
    parser.add_argument("candles_json", help="Path to a JSON file containing OHLCV candle dictionaries.")
    parser.add_argument("--model-path", default="models/xgboost_quant_v1.joblib")
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    with open(args.candles_json, "r", encoding="utf-8") as fh:
        candles = json.load(fh)
    result = evaluate_model_on_candles(candles, args.model_path, args.horizon, args.threshold)
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
