from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketConfig:
    symbol: str
    market_type: str
    tolerance_mult: float = 1.0
    volume_weight_mult: float = 1.0
    weekend_gaps: bool = True


def get_market_config(symbol: str) -> MarketConfig:
    s = (symbol or "").upper()
    if s.endswith(("USDT", "BTC", "ETH")) or "-USD" in s:
        return MarketConfig(symbol=s, market_type="crypto", tolerance_mult=1.5, volume_weight_mult=0.5, weekend_gaps=False)
    if s.endswith(("=X", "USD=X", "INR=X")):
        return MarketConfig(symbol=s, market_type="forex", tolerance_mult=1.15, volume_weight_mult=0.5)
    if s.endswith((".NS", ".BO")):
        return MarketConfig(symbol=s, market_type="india_equity")
    return MarketConfig(symbol=s, market_type="equity")


def _mult(market_cfg=None) -> float:
    return float(getattr(market_cfg, "tolerance_mult", 1.0) or 1.0)


def eq_tol(atr: float, price: float, market_cfg=None) -> float:
    return _mult(market_cfg) * max(0.35 * atr, 0.005 * price)


def line_tol(atr: float, price: float, market_cfg=None) -> float:
    return _mult(market_cfg) * max(0.25 * atr, 0.004 * price)


def brk_tol(atr: float, price: float, market_cfg=None) -> float:
    return _mult(market_cfg) * max(0.20 * atr, 0.003 * price)


def gap_tol(atr: float, price: float, market_cfg=None) -> float:
    return _mult(market_cfg) * max(0.10 * atr, 0.002 * price)

