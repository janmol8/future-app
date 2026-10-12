from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

from .atr_engine import ATREngine
from .market_config import get_market_config
from .pivot_engine import PivotEngine
from .primitives import Candle, PatternCandidate
from .volume_engine import VolumeEngine
from .patterns import (
    AscendingTriangleDetector,
    BearFlagDetector,
    BradDetector,
    BraaDetector,
    BroadeningTopDetector,
    BumpRunDetector,
    BullFlagDetector,
    CandlestickDetector,
    ChannelDownDetector,
    ChannelUpDetector,
    ClosingPriceReversalDetector,
    CupHandleDetector,
    DeadCatBounceDetector,
    DescendingTriangleDetector,
    DiamondBottomDetector,
    DiamondTopDetector,
    DivingBoardDetector,
    DoubleBottomDetector,
    DoubleTopDetector,
    FallingWedgeDetector,
    GapDetector,
    HeadShouldersDetector,
    HookReversalDetector,
    HornBottomDetector,
    HornTopDetector,
    InvHeadShouldersDetector,
    IslandReversalDetector,
    KeyReversalDetector,
    LongIslandDetector,
    MeasuredMoveDetector,
    NR4Detector,
    NR7Detector,
    OpenCloseReversalDetector,
    PennantDetector,
    PipeBottomDetector,
    PipeTopDetector,
    PivotPointReversalDetector,
    RectangleDetector,
    RisingWedgeDetector,
    RoundingBottomDetector,
    RoundingTopDetector,
    SpikesDetector,
    SymmetricalTriangleDetector,
    ThreeFallingPeaksDetector,
    ThreeRisingValleysDetector,
    TripleBottomDetector,
    TripleTopDetector,
)

TIMEFRAME_PIVOT_PARAMS = {
    "1m": {"left": 3, "right": 3, "gamma": 1.5},
    "3m": {"left": 3, "right": 3, "gamma": 1.5},
    "5m": {"left": 3, "right": 3, "gamma": 1.5},
    "15m": {"left": 4, "right": 4, "gamma": 1.5},
    "30m": {"left": 4, "right": 4, "gamma": 1.5},
    "1h": {"left": 5, "right": 5, "gamma": 1.5},
    "4h": {"left": 5, "right": 5, "gamma": 1.5},
    "1d": {"left": 5, "right": 5, "gamma": 1.5},
    "1D": {"left": 5, "right": 5, "gamma": 1.5},
    "1w": {"left": 2, "right": 2, "gamma": 1.2},
    "1W": {"left": 2, "right": 2, "gamma": 1.2},
    "1mo": {"left": 2, "right": 2, "gamma": 1.2},
    "1M": {"left": 2, "right": 2, "gamma": 1.2},
}


@dataclass
class ScannerConfig:
    lookback: int = 120
    anchorMode: str = "current"
    anchorDate: Optional[str] = None
    minConfidence: int = 40
    rightBars: int = 5


def compute_sma_array(candles: List[Candle], period: int) -> List[Optional[float]]:
    closes = np.array([c.close for c in candles], dtype=float)
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        start = max(0, i - period + 1)
        out.append(float(np.mean(closes[start:i + 1])))
    return out


def compute_ema_array(candles: List[Candle], period: int) -> List[Optional[float]]:
    if not candles:
        return []
    alpha = 2 / (period + 1)
    ema = float(candles[0].close)
    out = []
    for c in candles:
        ema = alpha * float(c.close) + (1 - alpha) * ema
        out.append(ema)
    return out


def normalize_interval(interval: str) -> str:
    value = (interval or "1d").strip()
    if value == "1M":
        return "1mo"
    return value


class LivePatternScanner:
    def __init__(
        self,
        symbol: str,
        interval: str,
        live_bar_index: Optional[int] = None,
        config: Optional[ScannerConfig] = None,
        lookback: Optional[int] = None,
        anchor_mode: str = "current",
        anchor_date: Optional[str] = None,
        min_confidence: Optional[int] = None,
    ):
        self.symbol = symbol
        self.interval = normalize_interval(interval)
        self.market_cfg = get_market_config(symbol)
        self.live_bar_index = live_bar_index
        self.config = config or ScannerConfig(
            lookback=lookback if lookback is not None else 120,
            anchorMode=anchor_mode,
            anchorDate=anchor_date,
            minConfidence=min_confidence if min_confidence is not None else 40,
        )
        self.scan_time_ms = 0
        self.scan_start_index = 0
        self.scan_end_index = 0

    def scan(self, candles_raw: list) -> List[PatternCandidate]:
        start_time = time.perf_counter()
        all_candles = self._normalize_candles(candles_raw)[-3000:]
        if len(all_candles) < 60 or self._is_flat(all_candles):
            self.scan_time_ms = int((time.perf_counter() - start_time) * 1000)
            return []

        scan_start, scan_end = self._scan_window(all_candles)
        self.scan_start_index = scan_start
        self.scan_end_index = scan_end
        candles = all_candles[scan_start:scan_end + 1]
        if len(candles) < 60 or self._is_flat(candles):
            self.scan_time_ms = int((time.perf_counter() - start_time) * 1000)
            return []

        atr_array = ATREngine.build_array(candles, 14)
        self._adjust_market_atr(candles, atr_array)
        sma50 = compute_sma_array(candles, 50)
        ema20 = compute_ema_array(candles, 20)
        vol_sma20 = VolumeEngine.build_vma_array(candles, 20)
        params = TIMEFRAME_PIVOT_PARAMS.get(self.interval, TIMEFRAME_PIVOT_PARAMS["1d"])
        pivots = PivotEngine.detect(candles, atr_array, left=params["left"], right=params["right"], gamma=params["gamma"])
        live_bar_index = self._relative_live_bar_index(scan_start, scan_end, len(candles))
        right_window = max(int(self.config.rightBars or 5), params["right"])
        pivots = [p for p in pivots if p.confirmed_at <= live_bar_index - right_window]

        ctx = {
            "candles": candles,
            "atr": atr_array,
            "sma50": sma50,
            "ema20": ema20,
            "vol_sma20": vol_sma20,
            "pivots": pivots,
            "live_bar_index": live_bar_index,
            "interval": self.interval,
            "market_cfg": self.market_cfg,
            "right_window": right_window,
        }
        results: List[PatternCandidate] = []
        for detector in self._detectors():
            try:
                results.extend(detector.detect(ctx))
            except Exception as exc:
                print(f"[PatternScanner] {detector.__class__.__name__} failed: {exc}")
        results = self._dedupe(results)
        results = self._resolve_overlaps(results)
        results = self._flag_conflicts(results)
        results = self._enforce_non_repaint(results, live_bar_index, right_window)
        results = self._mark_stale(results, candles)
        results = [p for p in results if p.confidence >= int(self.config.minConfidence or 0)]
        results = [self._to_absolute(p, scan_start, all_candles) for p in results]
        state_order = {"confirmed": 0, "emerging": 1, "forming": 2, "stale": 3, "invalidated": 4}
        results.sort(key=lambda p: (state_order.get(p.state, 5), -p.confidence, p.start_index))
        self.scan_time_ms = int((time.perf_counter() - start_time) * 1000)
        return results

    def _scan_window(self, candles: List[Candle]) -> Tuple[int, int]:
        end_index = len(candles) - 1
        if (self.config.anchorMode or "current").lower() == "date" and self.config.anchorDate:
            anchor_ts = self._parse_anchor_date(self.config.anchorDate)
            if anchor_ts is not None:
                for i, candle in enumerate(candles):
                    if candle.time <= anchor_ts:
                        end_index = i
                    else:
                        break
        lookback = max(1, min(int(self.config.lookback or 120), len(candles)))
        start_index = max(0, end_index - lookback + 1)
        return start_index, end_index

    def _parse_anchor_date(self, value: str) -> Optional[int]:
        try:
            clean = value.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            return int(dt.timestamp())
        except Exception:
            return None

    def _relative_live_bar_index(self, scan_start: int, scan_end: int, scan_len: int) -> int:
        if self.live_bar_index is None:
            return scan_len
        if scan_start <= self.live_bar_index <= scan_end + 1:
            return max(0, min(self.live_bar_index - scan_start, scan_len))
        return max(0, min(self.live_bar_index, scan_len))

    def _enforce_non_repaint(self, patterns: List[PatternCandidate], live_bar_index: int, right_window: int) -> List[PatternCandidate]:
        for p in patterns:
            if p.state != "confirmed":
                continue
            if p.breakout_index is None or p.breakout_index >= live_bar_index:
                p.state = "emerging"
                continue
            latest_confirmed = p.breakout_index
            if p.pivots:
                latest_confirmed = max(latest_confirmed, max(pv.confirmed_at for pv in p.pivots))
            if latest_confirmed > live_bar_index - right_window:
                p.state = "emerging"
        return patterns

    def _to_absolute(self, p: PatternCandidate, offset: int, candles: List[Candle]) -> PatternCandidate:
        if offset <= 0:
            p.draw["overlay"] = self._build_overlay(p, candles)
            return p
        old_id = p.id
        p.start_index += offset
        p.end_index += offset
        if p.breakout_index is not None:
            p.breakout_index += offset
        for pivot in p.pivots:
            pivot.index += offset
            pivot.confirmed_at += offset
        p.id = f"{old_id}_abs_{offset}"
        p.draw["overlay"] = self._build_overlay(p, candles)
        return p

    def _build_overlay(self, p: PatternCandidate, candles: List[Candle]) -> Optional[dict]:
        if p.state != "confirmed" or p.breakout_index is None:
            return None
        if not (0 <= p.start_index < len(candles) and 0 <= p.breakout_index < len(candles)):
            return None
        direction = "buy" if p.bias == "bull" else "sell" if p.bias == "bear" else "buy"
        zone_color = "rgba(127,119,221,0.12)" if p.bias == "bull" else "rgba(216,90,48,0.12)"
        time_to_index = {c.time: i for i, c in enumerate(candles)}
        pivot_labels = []
        for label in p.draw.get("labels", []):
            bar = time_to_index.get(label.get("time"))
            if bar is not None:
                pivot_labels.append({"bar": bar, "price": label.get("price"), "text": label.get("text")})
        if not pivot_labels:
            pivot_labels = [{"bar": pv.index, "price": pv.price, "text": f"P{i + 1}"} for i, pv in enumerate(p.pivots)]
        return {
            "patternZone": {
                "startBar": p.start_index,
                "endBar": p.breakout_index,
                "color": zone_color,
            },
            "entrySignal": {
                "bar": p.breakout_index,
                "direction": direction,
                "price": p.breakout_price or candles[p.breakout_index].close,
            },
            "stopLoss": {
                "price": p.stop_loss,
                "color": "#c0392b",
            },
            "targets": [
                {"label": "T1", "price": p.targets.classical, "color": "#089981"},
                {"label": "T2", "price": p.targets.bulkowski, "color": "#7bd88f"},
            ],
            "pivotLabels": pivot_labels,
            "confidenceBadge": {
                "value": p.confidence,
                "bar": p.start_index,
            },
            "rangeStartBar": self.scan_start_index,
            "rangeEndBar": self.scan_end_index,
        }

    def _detectors(self):
        short_tf = self.interval in {"1m", "3m", "5m"}
        detectors = [
            HeadShouldersDetector(), InvHeadShouldersDetector(),
            DoubleTopDetector(), DoubleBottomDetector(),
            TripleTopDetector(), TripleBottomDetector(),
            RoundingTopDetector(), RoundingBottomDetector(),
            DiamondTopDetector(), DiamondBottomDetector(),
            BumpRunDetector(),
            ThreeFallingPeaksDetector(), ThreeRisingValleysDetector(),
            IslandReversalDetector(), DeadCatBounceDetector(),
            BullFlagDetector(), BearFlagDetector(), PennantDetector(),
            MeasuredMoveDetector(), LongIslandDetector(),
            RectangleDetector(), AscendingTriangleDetector(), DescendingTriangleDetector(), SymmetricalTriangleDetector(),
            RisingWedgeDetector(), FallingWedgeDetector(), BroadeningTopDetector(), BraaDetector(), BradDetector(),
            ChannelUpDetector(), ChannelDownDetector(),
            NR4Detector(), NR7Detector(), HookReversalDetector(), KeyReversalDetector(),
            ClosingPriceReversalDetector(), OpenCloseReversalDetector(), PivotPointReversalDetector(),
            PipeTopDetector(), PipeBottomDetector(), HornTopDetector(), HornBottomDetector(),
            SpikesDetector(), GapDetector(), CandlestickDetector(),
        ]
        if not short_tf:
            detectors.insert(16, CupHandleDetector())
        if self.interval == "1w":
            detectors.insert(17, DivingBoardDetector())
        return detectors

    def _normalize_candles(self, rows: list) -> List[Candle]:
        candles: List[Candle] = []
        for row in rows or []:
            try:
                c = Candle(
                    time=int(row.get("time")),
                    open=float(row.get("open")),
                    high=float(row.get("high")),
                    low=float(row.get("low")),
                    close=float(row.get("close")),
                    volume=float(row.get("volume", 0) or 0),
                )
            except (TypeError, ValueError, AttributeError):
                continue
            if not all(math.isfinite(v) and v > 0 for v in (c.time, c.open, c.high, c.low, c.close)):
                continue
            c.high = max(c.high, c.open, c.close)
            c.low = min(c.low, c.open, c.close)
            candles.append(c)
        candles.sort(key=lambda x: x.time)
        return candles

    def _is_flat(self, candles: List[Candle]) -> bool:
        closes = [c.close for c in candles]
        return max(closes) == min(closes)

    def _adjust_market_atr(self, candles: List[Candle], atr_array: List[float]) -> None:
        if self.market_cfg.market_type != "india_equity":
            return
        for i, c in enumerate(candles):
            if c.open and abs(c.close - c.open) / c.open >= 0.04 and i > 0:
                atr_array[i] = atr_array[i - 1]

    def _dedupe(self, patterns: List[PatternCandidate]) -> List[PatternCandidate]:
        best: Dict[tuple, PatternCandidate] = {}
        for p in patterns:
            key = (p.name, p.start_index, p.end_index, tuple(sorted(pv.index for pv in p.pivots)))
            if key not in best or p.confidence > best[key].confidence:
                best[key] = p
        return list(best.values())

    def _resolve_overlaps(self, patterns: List[PatternCandidate]) -> List[PatternCandidate]:
        by_conf = sorted(patterns, key=lambda p: -p.confidence)
        for child in by_conf:
            child_set = {pv.index for pv in child.pivots}
            for parent in by_conf:
                if parent is child:
                    continue
                parent_set = {pv.index for pv in parent.pivots}
                if child.start_index >= parent.start_index and child.end_index <= parent.end_index:
                    if child_set and child_set.issubset(parent_set):
                        child.nested_in = parent.id
                        break
        return patterns

    def _flag_conflicts(self, patterns: List[PatternCandidate]) -> List[PatternCandidate]:
        actionable = [p for p in patterns if p.bias in ("bull", "bear") and p.state in ("confirmed", "emerging")]
        for i, p in enumerate(actionable):
            for q in actionable[i + 1:]:
                if abs(p.end_index - q.end_index) <= 5 and p.bias != q.bias and abs(p.confidence - q.confidence) <= 10:
                    p.conflicted = True
                    q.conflicted = True
                    p.metadata["requires_mtf_confirmation"] = True
                    q.metadata["requires_mtf_confirmation"] = True
        return patterns

    def _mark_stale(self, patterns: List[PatternCandidate], candles: List[Candle]) -> List[PatternCandidate]:
        current_price = candles[-1].close
        for p in patterns:
            if p.state in ("confirmed", "invalidated") or not p.pivots:
                continue
            height = max(pv.price for pv in p.pivots) - min(pv.price for pv in p.pivots)
            if height > 0 and abs(current_price - p.targets.classical) > 2.0 * height:
                p.state = "stale"
        return patterns
