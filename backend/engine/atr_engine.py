from __future__ import annotations

import math
from typing import List

from .primitives import Candle


class ATREngine:
    @staticmethod
    def calculate(candles: List[Candle], index: int, period: int = 14) -> float:
        if not candles:
            return 0.0
        index = max(0, min(index, len(candles) - 1))
        if index == 0:
            base = candles[0].high - candles[0].low
            return base if base > 0 else max(candles[0].close * 0.005, 1e-9)
        start = max(1, index - period + 1)
        total = 0.0
        count = 0
        for i in range(start, index + 1):
            c = candles[i]
            prev = candles[i - 1]
            if not all(math.isfinite(v) for v in (c.high, c.low, prev.close)):
                continue
            tr = max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close))
            if tr > 0:
                total += tr
                count += 1
        fallback = max(candles[index].close * 0.005, candles[index].high - candles[index].low, 1e-9)
        return total / count if count else fallback

    @staticmethod
    def build_array(candles: List[Candle], period: int = 14) -> List[float]:
        return [ATREngine.calculate(candles, i, period) for i in range(len(candles))]

