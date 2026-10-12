import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_candles(prices, volumes=None):
    candles = []
    for i, p in enumerate(prices):
        open_ = prices[i - 1] if i > 0 else p
        high = max(open_, p) * 1.005
        low = min(open_, p) * 0.995
        candles.append({
            "time": 1700000000 + i * 86400,
            "open": open_,
            "high": high,
            "low": low,
            "close": p,
            "volume": volumes[i] if volumes else 1_000_000,
        })
    return candles


def double_top_prices(breakout_on_last=False):
    base = [
        90, 92, 95, 99, 104, 110, 118, 126, 120, 114, 108, 104,
        110, 116, 123, 126, 119, 110, 101, 96, 92, 90, 88, 86, 84, 82,
    ]
    tail = [80] if breakout_on_last else [80, 80.2, 80.3, 80.1, 80.4]
    return base + tail + [80 + i * 0.05 for i in range(60)]


def test_double_top_detected():
    from engine.scanner import LivePatternScanner

    candles = make_candles(double_top_prices())
    results = LivePatternScanner("TEST", "1d").scan(candles)
    tops = [p for p in results if p.name == "Double Top"]
    assert tops, "Should detect Double Top"
    assert tops[0].bias == "bear"
    assert tops[0].stop_loss > tops[0].breakout_price
    assert tops[0].targets.classical < tops[0].breakout_price


def test_risk_engine_position_sizing():
    from engine.primitives import PatternCandidate, PatternTarget
    from engine.risk_engine import RiskEngine

    pattern = PatternCandidate(
        id="P1", name="Test Pattern", family="classic", type="event", bias="bull", state="confirmed",
        start_index=0, end_index=1, pivots=[], upper_line=None, lower_line=None,
        breakout_index=1, breakout_price=1000, stop_loss=950,
        targets=PatternTarget(classical=1100, conservative=1060, bulkowski=1080),
        confidence=80, reasons=[],
    )
    risk = RiskEngine.compute(pattern, 1000, account_size=100000, risk_pct_per_trade=1, lot_size=5)
    assert risk.risk_amount == 50
    assert risk.position_size_units == 20
    assert risk.lots == 4
    assert risk.rr_classical == 2


def test_no_repainting_live_breakout_is_emerging():
    from engine.scanner import LivePatternScanner

    prices = [
        90, 92, 95, 99, 104, 110, 118, 126, 120, 114, 108, 104,
        110, 116, 123, 126, 121, 118, 116, 114, 112, 110, 108, 106,
        105, 104.8, 104.7, 104.6,
    ] + [104.8 + (i % 3) * 0.05 for i in range(35)] + [103]
    candles = make_candles(prices)
    scanner = LivePatternScanner("TEST", "1d", live_bar_index=len(candles) - 1)
    results = scanner.scan(candles)
    tops = [p for p in results if p.name == "Double Top"]
    assert tops
    assert tops[0].state != "confirmed"


def test_atr_tolerance_scales_with_volatility():
    from engine.market_config import eq_tol, get_market_config

    equity_tol = eq_tol(2, 100, get_market_config("RELIANCE.NS"))
    crypto_tol = eq_tol(2, 100, get_market_config("BTCUSDT"))
    high_vol_tol = eq_tol(8, 100, get_market_config("RELIANCE.NS"))
    assert crypto_tol > equity_tol
    assert high_vol_tol > equity_tol


def test_all_detectors_return_valid_candidates_on_random_walk():
    from engine.scanner import LivePatternScanner

    random.seed(7)
    prices = [100.0]
    for _ in range(220):
        prices.append(max(1.0, prices[-1] + random.uniform(-2.5, 2.5)))
    candles = make_candles(prices)
    results = LivePatternScanner("TEST", "1d").scan(candles)
    for p in results:
        assert p.id
        assert p.name
        assert p.state in {"forming", "emerging", "confirmed", "stale", "invalidated"}
        assert p.bias in {"bull", "bear", "neutral"}
        assert p.stop_loss is not None
        assert p.targets.classical is not None
        assert 0 <= p.confidence <= 100
