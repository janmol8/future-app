from __future__ import annotations

from typing import List

from .primitives import Candle, Pivot


class PivotEngine:
    @staticmethod
    def detect(candles: List[Candle], atr_array: List[float], left: int = 5, right: int = 5, gamma: float = 1.5) -> List[Pivot]:
        pivots: List[Pivot] = []
        n = len(candles)
        if n < left + right + 1:
            return pivots
        last_high = candles[0].close
        last_low = candles[0].close
        for i in range(left, n - right):
            atr = max(atr_array[i], candles[i].close * 0.005, 1e-9)
            h = candles[i].high
            l = candles[i].low
            left_highs = [candles[i - k].high for k in range(1, left + 1)]
            right_highs = [candles[i + k].high for k in range(1, right + 1)]
            left_lows = [candles[i - k].low for k in range(1, left + 1)]
            right_lows = [candles[i + k].low for k in range(1, right + 1)]
            is_high = all(v <= h for v in left_highs + right_highs) and any(v < h for v in left_highs) and any(v < h for v in right_highs)
            is_low = all(v >= l for v in left_lows + right_lows) and any(v > l for v in left_lows) and any(v > l for v in right_lows)
            if is_high:
                prominence = abs(h - last_low)
                if prominence >= gamma * atr:
                    pivot = Pivot(i, "high", h, atr, i + right, prominence / atr, left + right)
                    if pivots and pivots[-1].kind == "high":
                        if pivot.price > pivots[-1].price:
                            pivots[-1] = pivot
                    else:
                        pivots.append(pivot)
                    last_high = h
            if is_low:
                prominence = abs(last_high - l)
                if prominence >= gamma * atr:
                    pivot = Pivot(i, "low", l, atr, i + right, prominence / atr, left + right)
                    if pivots and pivots[-1].kind == "low":
                        if pivot.price < pivots[-1].price:
                            pivots[-1] = pivot
                    else:
                        pivots.append(pivot)
                    last_low = l
        pivots.sort(key=lambda p: (p.index, 0 if p.kind == "low" else 1))
        return pivots
