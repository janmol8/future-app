from __future__ import annotations

from typing import List

from .primitives import Candle


class VolumeEngine:
    @staticmethod
    def vma(candles: List[Candle], index: int, period: int = 20) -> float:
        if not candles:
            return 1.0
        index = max(0, min(index, len(candles) - 1))
        start = max(0, index - period + 1)
        vols = [max(0.0, candles[i].volume) for i in range(start, index + 1)]
        return sum(vols) / len(vols) if vols else 1.0

    @staticmethod
    def build_vma_array(candles: List[Candle], period: int = 20) -> List[float]:
        return [VolumeEngine.vma(candles, i, period) for i in range(len(candles))]

    @staticmethod
    def is_breakout_volume(candles: List[Candle], index: int, mult: float = 1.5, period: int = 20) -> bool:
        if index <= 0 or index >= len(candles):
            return False
        return candles[index].volume >= mult * VolumeEngine.vma(candles, index - 1, period)

    @staticmethod
    def volume_trend(candles: List[Candle], start: int, end: int) -> str:
        if end - start < 5:
            return "flat"
        first = sum(c.volume for c in candles[start:start + 3]) / 3
        last = sum(c.volume for c in candles[end - 2:end + 1]) / 3
        if last > first * 1.2:
            return "expanding"
        if last < first * 0.8:
            return "contracting"
        return "flat"

