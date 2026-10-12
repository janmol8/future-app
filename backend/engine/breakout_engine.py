from __future__ import annotations

from typing import List

from .primitives import Candle


class BreakoutEngine:
    @staticmethod
    def break_above(candles: List[Candle], index: int, level: float, atr: float, pct: float = 0.10) -> bool:
        return 1 <= index < len(candles) and candles[index].close > level + atr * pct and candles[index - 1].close <= level

    @staticmethod
    def break_below(candles: List[Candle], index: int, level: float, atr: float, pct: float = 0.10) -> bool:
        return 1 <= index < len(candles) and candles[index].close < level - atr * pct and candles[index - 1].close >= level

    @staticmethod
    def two_bar_confirm(candles: List[Candle], start: int, level: float, direction: str) -> bool:
        count = 0
        for i in range(start, min(start + 5, len(candles))):
            ok = candles[i].close > level if direction == "up" else candles[i].close < level
            count = count + 1 if ok else 0
            if count >= 2:
                return True
        return False

