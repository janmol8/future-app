from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..breakout_engine import BreakoutEngine
from ..market_config import brk_tol, eq_tol, gap_tol, line_tol
from ..primitives import Candle, PatternCandidate, PatternTarget, Pivot, clamp_confidence
from ..trendline_engine import TrendlineEngine
from ..volume_engine import VolumeEngine


def _ctx(ctx: dict):
    return ctx["candles"], ctx["pivots"], ctx["atr"], ctx.get("live_bar_index", len(ctx["candles"]) - 1), ctx.get("market_cfg")


def _closed(index: int, live_bar_index: int) -> bool:
    return index < live_bar_index


def _trend_up(candles: List[Candle], i: int, length: int = 20) -> bool:
    j = max(0, i - length)
    return i > j and candles[i].close > candles[j].close


def _trend_down(candles: List[Candle], i: int, length: int = 20) -> bool:
    j = max(0, i - length)
    return i > j and candles[i].close < candles[j].close


def _line_points(candles: List[Candle], line: dict, start: int, end: int, typ: str, color: str) -> dict:
    return {
        "type": typ,
        "p1": {"time": candles[start].time, "price": TrendlineEngine.price_at(line, start)},
        "p2": {"time": candles[end].time, "price": TrendlineEngine.price_at(line, end)},
        "color": color,
    }


def _price_line(typ: str, price: float, color: str) -> dict:
    return {"type": typ, "price": float(price), "color": color}


def _state(breakout_index: Optional[int], end_index: int, live_bar_index: int) -> str:
    if breakout_index is None:
        return "emerging" if end_index >= live_bar_index - 8 else "forming"
    return "confirmed" if _closed(breakout_index, live_bar_index) else "emerging"


def _bar_start(live: int, lookback: int = 500, minimum: int = 0) -> int:
    return max(minimum, live - lookback)


def _targets(bias: str, entry: float, height: float, bulkowski_mult: float = 0.74) -> PatternTarget:
    if bias == "bear":
        return PatternTarget(entry - height, entry - 0.618 * height, entry - bulkowski_mult * height)
    return PatternTarget(entry + height, entry + 0.618 * height, entry + bulkowski_mult * height)


def _candidate(
    ctx: dict,
    name: str,
    family: str,
    typ: str,
    bias: str,
    start: int,
    end: int,
    pivots: List[Pivot],
    stop: float,
    targets: PatternTarget,
    confidence: float,
    reasons: List[str],
    breakout_index: Optional[int] = None,
    breakout_price: Optional[float] = None,
    upper_line: Optional[dict] = None,
    lower_line: Optional[dict] = None,
    labels: Optional[List[Tuple[int, float, str]]] = None,
    metadata: Optional[dict] = None,
) -> PatternCandidate:
    candles = ctx["candles"]
    live = ctx.get("live_bar_index", len(candles) - 1)
    color = "#089981" if bias == "bull" else "#F23645" if bias == "bear" else "#2962FF"
    top = max([p.price for p in pivots] + [candles[i].high for i in range(start, min(end + 1, len(candles)))])
    bottom = min([p.price for p in pivots] + [candles[i].low for i in range(start, min(end + 1, len(candles)))])
    draw_lines = [_price_line("stop", stop, "#FF9800"), _price_line("target_classical", targets.classical, "#089981"), _price_line("target_conservative", targets.conservative, "#888"), _price_line("target_bulkowski", targets.bulkowski, "#0080ff")]
    if upper_line:
        draw_lines.append(_line_points(candles, upper_line, start, end, "upper", color))
    if lower_line:
        draw_lines.append(_line_points(candles, lower_line, start, end, "lower" if "Triangle" not in name else "neckline", color))
    draw = {
        "lines": draw_lines,
        "zones": [{"type": "pattern_body", "top": top, "bottom": bottom, "start_time": candles[start].time, "end_time": candles[end].time, "color": color}],
        "labels": [{"time": candles[i].time, "price": price, "text": text, "color": color} for i, price, text in (labels or []) if 0 <= i < len(candles)],
        "arrows": [],
    }
    if breakout_index is not None and 0 <= breakout_index < len(candles):
        draw["arrows"].append({
            "time": candles[breakout_index].time,
            "price": breakout_price or candles[breakout_index].close,
            "direction": "up" if bias == "bull" else "down",
            "color": color,
            "text": ("BUY " if bias == "bull" else "SELL ") + f"{int(confidence)}%",
        })
    p = PatternCandidate(
        id=f"{name.replace(' ', '_')}_{start}_{end}",
        name=name,
        family=family, type=typ, bias=bias, state=_state(breakout_index, end, live),
        start_index=start, end_index=end, pivots=pivots, upper_line=upper_line, lower_line=lower_line,
        breakout_index=breakout_index, breakout_price=breakout_price, stop_loss=float(stop),
        targets=targets, confidence=clamp_confidence(name if family != "candlestick" else "Candlestick", confidence),
        reasons=reasons, metadata=metadata or {}, draw=draw,
    )
    if ctx.get("market_cfg") and ctx["market_cfg"].market_type == "india_equity":
        for c in candles[start:end + 1]:
            if c.open and abs(c.close - c.open) / c.open >= 0.04:
                p.low_confidence = True
                p.confidence = min(p.confidence, 55)
                p.reasons.append("Circuit-limit style day inside pattern")
                break
    return p


def _find_break(candles: List[Candle], atr: List[float], start: int, end: int, level_fn, direction: str, live: int, cfg) -> Tuple[Optional[int], Optional[float]]:
    for i in range(max(end + 1, 1), live + 1):
        level = level_fn(i)
        tol = brk_tol(atr[i], candles[i].close, cfg)
        prev_level = level_fn(i - 1)
        if direction == "up" and candles[i].close > level + tol and candles[i - 1].close <= prev_level:
            return i, candles[i].close
        if direction == "down" and candles[i].close < level - tol and candles[i - 1].close >= prev_level:
            return i, candles[i].close
    return None, None


class BaseDetector:
    name = "Base"
    family = "classic"
    type = "reversal"

    def detect(self, ctx: dict) -> List[PatternCandidate]:
        return []


class DoubleTopDetector(BaseDetector):
    name = "Double Top"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for a, b, c in zip(pivots, pivots[1:], pivots[2:]):
            if (a.kind, b.kind, c.kind) != ("high", "low", "high"):
                continue
            sep = c.index - a.index
            if sep < 8 or sep > 180:
                continue
            tol = eq_tol(max(a.atr, c.atr), max(a.price, c.price), cfg)
            if abs(a.price - c.price) > tol:
                continue
            peak = max(a.price, c.price)
            if peak - b.price < max(2 * max(a.atr, c.atr), 0.025 * peak):
                continue
            brk, brk_price = _find_break(candles, atr, b.index, c.index, lambda i, lvl=b.price: lvl, "down", live, cfg)
            entry = brk_price or candles[min(live, c.index)].close
            height = peak - b.price
            conf = 58 + min(22, height / max(a.atr, 1e-9) * 3) + (10 if brk else 0)
            sharp1 = (a.price - min(candles[max(0, a.index - 2)].low, candles[min(len(candles) - 1, a.index + 2)].low)) / max(a.atr, 1e-9)
            sharp2 = (c.price - min(candles[max(0, c.index - 2)].low, candles[min(len(candles) - 1, c.index + 2)].low)) / max(c.atr, 1e-9)
            adam_eve = "Adam-Adam" if sharp1 > 1.5 and sharp2 > 1.5 else "Adam-Eve" if max(sharp1, sharp2) > 1.5 else "Eve-Eve"
            out.append(_candidate(ctx, self.name, "classic", "reversal", "bear", a.index, brk or c.index, [a, b, c], peak + 0.5 * c.atr, PatternTarget(b.price - height, b.price - 0.618 * height, b.price - 0.17 * height), conf, [f"Peak equality within {tol:.4f}", f"Adam/Eve: {adam_eve}"], brk, brk_price, labels=[(a.index, a.price, "P1"), (b.index, b.price, "L1"), (c.index, c.price, "P2")], metadata={"adam_eve": adam_eve}))
        return out


class DoubleBottomDetector(BaseDetector):
    name = "Double Bottom"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for a, b, c in zip(pivots, pivots[1:], pivots[2:]):
            if (a.kind, b.kind, c.kind) != ("low", "high", "low"):
                continue
            if c.index - a.index < 8 or c.index - a.index > 180:
                continue
            tol = eq_tol(max(a.atr, c.atr), max(a.price, c.price), cfg)
            if abs(a.price - c.price) > tol:
                continue
            trough = min(a.price, c.price)
            if b.price - trough < max(2 * max(a.atr, c.atr), 0.025 * b.price):
                continue
            brk, brk_price = _find_break(candles, atr, b.index, c.index, lambda i, lvl=b.price: lvl, "up", live, cfg)
            height = b.price - trough
            conf = 58 + min(22, height / max(a.atr, 1e-9) * 3) + (10 if brk else 0)
            out.append(_candidate(ctx, self.name, "classic", "reversal", "bull", a.index, brk or c.index, [a, b, c], trough - 0.5 * c.atr, PatternTarget(b.price + height, b.price + 0.618 * height, b.price + 0.70 * height), conf, [f"Bottom equality within {tol:.4f}"], brk, brk_price, labels=[(a.index, a.price, "B1"), (b.index, b.price, "H1"), (c.index, c.price, "B2")]))
        return out


class TripleTopDetector(BaseDetector):
    name = "Triple Top"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for win in _pivot_windows(pivots, 5, ("high", "low", "high", "low", "high")):
            p1, t1, p2, t2, p3 = win
            peaks = [p1.price, p2.price, p3.price]
            if (max(peaks) - min(peaks)) / max(peaks) > 0.02:
                continue
            neckline = min(t1.price, t2.price)
            brk, brk_price = _find_break(candles, atr, t2.index, p3.index, lambda i, lvl=neckline: lvl, "down", live, cfg)
            height = max(peaks) - neckline
            out.append(_candidate(ctx, self.name, "classic", "reversal", "bear", p1.index, brk or p3.index, win, max(peaks) + 0.5 * p3.atr, PatternTarget(neckline - height, neckline - 0.618 * height, neckline - 0.64 * height), 66 + (12 if brk else 0), ["Three near-equal peaks"], brk, brk_price))
        return out


class TripleBottomDetector(BaseDetector):
    name = "Triple Bottom"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for win in _pivot_windows(pivots, 5, ("low", "high", "low", "high", "low")):
            b1, h1, b2, h2, b3 = win
            lows = [b1.price, b2.price, b3.price]
            if (max(lows) - min(lows)) / max(lows) > 0.02:
                continue
            neckline = max(h1.price, h2.price)
            brk, brk_price = _find_break(candles, atr, h2.index, b3.index, lambda i, lvl=neckline: lvl, "up", live, cfg)
            height = neckline - min(lows)
            out.append(_candidate(ctx, self.name, "classic", "reversal", "bull", b1.index, brk or b3.index, win, min(lows) - 0.5 * b3.atr, PatternTarget(neckline + height, neckline + 0.618 * height, neckline + 0.64 * height), 66 + (12 if brk else 0), ["Three near-equal troughs"], brk, brk_price, metadata={"morph_history": ["Double Bottom -> Triple Bottom"]}))
        return out


def _pivot_windows(pivots: List[Pivot], size: int, kinds: Sequence[str]) -> Iterable[List[Pivot]]:
    for i in range(0, len(pivots) - size + 1):
        win = pivots[i:i + size]
        if tuple(p.kind for p in win) == tuple(kinds):
            yield win


class HeadShouldersDetector(BaseDetector):
    name = "Head and Shoulders"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for ls, t1, head, t2, rs in _pivot_windows(pivots, 5, ("high", "low", "high", "low", "high")):
            if not (head.price > ls.price and head.price > rs.price and _trend_up(candles, ls.index)):
                continue
            shoulder_diff = abs(ls.price - rs.price) / min(ls.price, rs.price)
            if shoulder_diff > 0.05:
                continue
            neckline = TrendlineEngine.fit_ols([{"index": t1.index, "price": t1.price}, {"index": t2.index, "price": t2.price}])
            scale = max(atr[head.index], candles[head.index].close * 0.01)
            if TrendlineEngine.slope_degrees(neckline["slope"], scale) > 15:
                continue
            brk, brk_price = _find_break(candles, atr, t2.index, rs.index, lambda i: TrendlineEngine.price_at(neckline, i), "down", live, cfg)
            neck_head = TrendlineEngine.price_at(neckline, head.index)
            entry = brk_price or candles[min(live, rs.index)].close
            height = max(0.0, head.price - neck_head)
            vol_score = 10 if brk and VolumeEngine.is_breakout_volume(candles, brk) else 0
            conf = 40 * (1 - shoulder_diff / 0.05) + 35 + vol_score
            out.append(_candidate(ctx, self.name, "classic", "reversal", "bear", ls.index, brk or rs.index, [ls, t1, head, t2, rs], rs.price + 0.5 * rs.atr, _targets("bear", entry, height, 0.74), conf, [f"Shoulder asymmetry {shoulder_diff:.1%}", "Neckline confirmed"], brk, brk_price, lower_line=neckline, labels=[(ls.index, ls.price, "LS"), (head.index, head.price, "H"), (rs.index, rs.price, "RS")]))
        return out


class InvHeadShouldersDetector(BaseDetector):
    name = "Inverse Head and Shoulders"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for ls, t1, head, t2, rs in _pivot_windows(pivots, 5, ("low", "high", "low", "high", "low")):
            if not (head.price < ls.price and head.price < rs.price and _trend_down(candles, ls.index)):
                continue
            diff = abs(ls.price - rs.price) / max(min(ls.price, rs.price), 1e-9)
            if diff > 0.05:
                continue
            neckline = TrendlineEngine.fit_ols([{"index": t1.index, "price": t1.price}, {"index": t2.index, "price": t2.price}])
            scale = max(atr[head.index], candles[head.index].close * 0.01)
            if TrendlineEngine.slope_degrees(neckline["slope"], scale) > 15:
                continue
            brk, brk_price = _find_break(candles, atr, t2.index, rs.index, lambda i: TrendlineEngine.price_at(neckline, i), "up", live, cfg)
            entry = brk_price or candles[min(live, rs.index)].close
            height = max(0.0, TrendlineEngine.price_at(neckline, head.index) - head.price)
            conf = 40 * (1 - diff / 0.05) + 35 + (10 if brk and VolumeEngine.is_breakout_volume(candles, brk) else 0)
            out.append(_candidate(ctx, self.name, "classic", "reversal", "bull", ls.index, brk or rs.index, [ls, t1, head, t2, rs], rs.price - 0.5 * rs.atr, _targets("bull", entry, height, 0.74), conf, [f"Shoulder asymmetry {diff:.1%}", "Neckline confirmed"], brk, brk_price, upper_line=neckline, labels=[(ls.index, ls.price, "LS"), (head.index, head.price, "H"), (rs.index, rs.price, "RS")]))
        return out


class ThreeFallingPeaksDetector(BaseDetector):
    name = "Three Falling Peaks"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for win in _pivot_windows(pivots, 5, ("high", "low", "high", "low", "high")):
            h1, l1, h2, l2, h3 = win
            if h1.price > h2.price > h3.price:
                neck = min(l1.price, l2.price)
                brk, brk_price = _find_break(candles, atr, l2.index, h3.index, lambda i, lvl=neck: lvl, "down", live, cfg)
                height = h1.price - neck
                out.append(_candidate(ctx, self.name, "classic", "reversal", "bear", h1.index, brk or h3.index, win, h3.price + 0.5 * h3.atr, PatternTarget(neck - height, neck - 0.618 * height, neck - 0.23 * height), 62 + (12 if brk else 0), ["Strictly lower swing highs"], brk, brk_price))
        return out


class ThreeRisingValleysDetector(BaseDetector):
    name = "Three Rising Valleys"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for win in _pivot_windows(pivots, 5, ("low", "high", "low", "high", "low")):
            l1, h1, l2, h2, l3 = win
            if l1.price < l2.price < l3.price:
                neck = max(h1.price, h2.price)
                brk, brk_price = _find_break(candles, atr, h2.index, l3.index, lambda i, lvl=neck: lvl, "up", live, cfg)
                height = neck - l1.price
                out.append(_candidate(ctx, self.name, "classic", "reversal", "bull", l1.index, brk or l3.index, win, l3.price - 0.5 * l3.atr, PatternTarget(neck + height, neck + 0.618 * height, neck + 0.57 * height), 62 + (12 if brk else 0), ["Strictly higher swing lows"], brk, brk_price))
        return out


class RoundingTopDetector(BaseDetector):
    name = "Rounding Top"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for start in range(max(0, live - 500), max(0, live - 50), 25):
            end = min(start + 80, live - 1)
            if end - start < 50:
                continue
            xs = np.arange(start, end + 1)
            ys = np.array([candles[i].high for i in xs])
            a, b, c = np.polyfit(xs, ys, 2)
            if a >= 0:
                continue
            rim = max(min(candles[start].low, candles[end].low), 1e-9)
            brk, brk_price = _find_break(candles, atr, start, end, lambda i, lvl=rim: lvl, "down", live, cfg)
            if brk:
                height = max(ys) - rim
                out.append(_candidate(ctx, self.name, "classic", "reversal", "bear", start, brk, [], max(ys) + atr[end], _targets("bear", brk_price, height, 0.74), 64, ["Quadratic curvature negative"], brk, brk_price))
        return out[:3]


class RoundingBottomDetector(BaseDetector):
    name = "Rounding Bottom"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for start in range(max(0, live - 500), max(0, live - 50), 25):
            end = min(start + 80, live - 1)
            if end - start < 50:
                continue
            xs = np.arange(start, end + 1)
            ys = np.array([candles[i].low for i in xs])
            a, b, c = np.polyfit(xs, ys, 2)
            if a <= 0:
                continue
            rim = min(candles[start].high, candles[end].high)
            depth = rim - min(ys)
            if depth < 5 * atr[end]:
                continue
            brk, brk_price = _find_break(candles, atr, start, end, lambda i, lvl=rim: lvl, "up", live, cfg)
            if brk:
                out.append(_candidate(ctx, self.name, "classic", "reversal", "bull", start, brk, [], min(ys) - atr[end], _targets("bull", brk_price, depth, 0.74), 64, ["Quadratic curvature positive"], brk, brk_price))
        return out[:3]


class TriangleBase(BaseDetector):
    name = "Triangle"
    bias = "neutral"
    direction = "up"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        first_window = max(0, len(pivots) - 30)
        for i in range(first_window, len(pivots) - 5):
            win = pivots[i:i + 6]
            highs = [p for p in win if p.kind == "high"]
            lows = [p for p in win if p.kind == "low"]
            if len(highs) < 2 or len(lows) < 2:
                continue
            upper = TrendlineEngine.fit_ols([{"index": p.index, "price": p.price} for p in highs])
            lower = TrendlineEngine.fit_ols([{"index": p.index, "price": p.price} for p in lows])
            if not self._shape(upper, lower):
                continue
            start, end = win[0].index, win[-1].index
            direction = self.direction
            bias = self.bias
            if bias == "neutral":
                up, up_price = _find_break(candles, atr, start, end, lambda x: TrendlineEngine.price_at(upper, x), "up", live, cfg)
                dn, dn_price = _find_break(candles, atr, start, end, lambda x: TrendlineEngine.price_at(lower, x), "down", live, cfg)
                if up and (not dn or up <= dn):
                    direction, bias, brk, brk_price = "up", "bull", up, up_price
                elif dn:
                    direction, bias, brk, brk_price = "down", "bear", dn, dn_price
                else:
                    brk = brk_price = None
            else:
                brk, brk_price = _find_break(candles, atr, start, end, lambda x: TrendlineEngine.price_at(upper if direction == "up" else lower, x), direction, live, cfg)
            height = max(p.price for p in win) - min(p.price for p in win)
            entry = brk_price or candles[min(live, end)].close
            stop = min(p.price for p in lows) - atr[end] if bias == "bull" else max(p.price for p in highs) + atr[end]
            out.append(_candidate(ctx, self.name, "classic", "bilateral", bias, start, brk or end, win, stop, _targets("bull" if bias != "bear" else "bear", entry, height, 0.68), 62 + min(16, 8 * (upper.get("r2", 0) + lower.get("r2", 0))), [f"Upper slope {upper['slope']:.4f}", f"Lower slope {lower['slope']:.4f}"], brk, brk_price, upper_line=upper, lower_line=lower))
        return out[:6]

    def _shape(self, upper, lower):
        return False


class RectangleDetector(TriangleBase):
    name = "Rectangle"
    bias = "neutral"

    def _shape(self, upper, lower):
        return abs(upper["slope"]) <= 0.005 and abs(lower["slope"]) <= 0.005


class AscendingTriangleDetector(TriangleBase):
    name = "Ascending Triangle"
    bias = "bull"
    direction = "up"

    def _shape(self, upper, lower):
        return abs(upper["slope"]) <= 0.005 and lower["slope"] > 0.005


class DescendingTriangleDetector(TriangleBase):
    name = "Descending Triangle"
    bias = "bear"
    direction = "down"

    def _shape(self, upper, lower):
        return abs(lower["slope"]) <= 0.005 and upper["slope"] < -0.005


class SymmetricalTriangleDetector(TriangleBase):
    name = "Symmetrical Triangle"
    bias = "neutral"

    def _shape(self, upper, lower):
        if not (upper["slope"] < 0 and lower["slope"] > 0):
            return False
        su, sl = abs(upper["slope"]), abs(lower["slope"])
        return min(su, sl) / max(su, sl, 1e-9) >= 0.70


class RisingWedgeDetector(TriangleBase):
    name = "Rising Wedge"
    bias = "bear"
    direction = "down"

    def _shape(self, upper, lower):
        return lower["slope"] > upper["slope"] > 0


class FallingWedgeDetector(TriangleBase):
    name = "Falling Wedge"
    bias = "bull"
    direction = "up"

    def _shape(self, upper, lower):
        return upper["slope"] < lower["slope"] < 0


class BroadeningTopDetector(TriangleBase):
    name = "Broadening Top"
    bias = "neutral"

    def _shape(self, upper, lower):
        return upper["slope"] > 0 and lower["slope"] < 0


class BraaDetector(TriangleBase):
    name = "Broadening Right-Angled Ascending"
    bias = "neutral"

    def _shape(self, upper, lower):
        return abs(lower["slope"]) <= 0.005 and upper["slope"] > 0


class BradDetector(TriangleBase):
    name = "Broadening Right-Angled Descending"
    bias = "neutral"

    def _shape(self, upper, lower):
        return abs(upper["slope"]) <= 0.005 and lower["slope"] < 0


class ChannelUpDetector(TriangleBase):
    name = "Channel Up"
    bias = "bear"
    direction = "down"

    def _shape(self, upper, lower):
        return upper["slope"] > 0 and lower["slope"] > 0 and abs(upper["slope"] - lower["slope"]) <= 0.005


class ChannelDownDetector(TriangleBase):
    name = "Channel Down"
    bias = "bull"
    direction = "up"

    def _shape(self, upper, lower):
        return upper["slope"] < 0 and lower["slope"] < 0 and abs(upper["slope"] - lower["slope"]) <= 0.005


class FlagDetector(BaseDetector):
    name = "Bull Flag"
    bias = "bull"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        n = min(live, len(candles) - 1)
        for pole_start in range(max(0, n - 220), max(0, n - 15)):
            pole_end = min(pole_start + 10, n - 6)
            if pole_end <= pole_start:
                continue
            move = candles[pole_end].close - candles[pole_start].close
            if self.bias == "bear":
                move = -move
            if move < max(3 * atr[pole_end], 0.05 * candles[pole_start].close):
                continue
            for end in range(pole_end + 5, min(pole_end + 25, n) + 1):
                closes = [candles[i].close for i in range(pole_end, end + 1)]
                retrace = (max(closes) - min(closes)) / max(move, 1e-9)
                if retrace > 0.55:
                    continue
                line_pts = [{"index": i, "price": candles[i].high if self.bias == "bull" else candles[i].low} for i in range(pole_end, end + 1)]
                line = TrendlineEngine.fit_ols(line_pts)
                if self.bias == "bull" and line["slope"] >= 0:
                    continue
                if self.bias == "bear" and line["slope"] <= 0:
                    continue
                direction = "up" if self.bias == "bull" else "down"
                brk, brk_price = _find_break(candles, atr, pole_end, end, lambda x: TrendlineEngine.price_at(line, x), direction, live, cfg)
                if brk or end >= live - 20:
                    entry = brk_price or candles[end].close
                    stop = min(closes) - atr[end] if self.bias == "bull" else max(closes) + atr[end]
                    out.append(_candidate(ctx, self.name, "classic", "continuation", self.bias, pole_start, brk or end, [], stop, _targets(self.bias, entry, move, 1.0), 60 + (12 if brk else 0), ["Flagpole and counter-trend channel"], brk, brk_price, upper_line=line if self.bias == "bull" else None, lower_line=line if self.bias == "bear" else None))
                    break
        return out[:4]


class BullFlagDetector(FlagDetector):
    name = "Bull Flag"
    bias = "bull"


class BearFlagDetector(FlagDetector):
    name = "Bear Flag"
    bias = "bear"


class PennantDetector(TriangleBase):
    name = "Pennant"
    bias = "neutral"

    def _shape(self, upper, lower):
        return upper["slope"] < 0 and lower["slope"] > 0


class CupHandleDetector(BaseDetector):
    name = "Cup and Handle"
    type = "continuation"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        if len(candles) < 80 or ctx.get("interval") in ("1m", "3m", "5m"):
            return []
        out = []
        for p1, trough, p2, handle in _pivot_windows(pivots, 4, ("high", "low", "high", "low")):
            if abs(p1.price - p2.price) / max(p1.price, p2.price) > 0.03:
                continue
            depth = min(p1.price, p2.price) - trough.price
            if depth <= 3 * atr[p2.index] or handle.price < trough.price + 0.5 * depth:
                continue
            handle_len = handle.index - p2.index
            cup_len = p2.index - p1.index
            if cup_len <= 0 or not (0.05 <= handle_len / cup_len <= 0.45):
                continue
            brk, brk_price = _find_break(candles, atr, p2.index, handle.index, lambda i, lvl=p2.price: lvl, "up", live, cfg)
            entry = brk_price or candles[min(live, handle.index)].close
            out.append(_candidate(ctx, self.name, "classic", "continuation", "bull", p1.index, brk or handle.index, [p1, trough, p2, handle], handle.price - 0.5 * handle.atr, PatternTarget(p2.price + depth, p2.price + 0.618 * depth, p2.price + 0.61 * depth), 66 + (12 if brk else 0), ["Rounded cup with handle in upper half"], brk, brk_price))
        return out


class MeasuredMoveDetector(BaseDetector):
    name = "Measured Move"
    type = "continuation"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for a, b, c in _pivot_windows(pivots, 3, ("low", "high", "low")):
            ab = b.price - a.price
            bc = b.price - c.price
            if ab <= 0 or not (0.382 <= bc / ab <= 0.618):
                continue
            brk, brk_price = _find_break(candles, atr, b.index, c.index, lambda i, lvl=b.price: lvl, "up", live, cfg)
            entry = brk_price or candles[min(live, c.index)].close
            out.append(_candidate(ctx, "Measured Move Up", "classic", "continuation", "bull", a.index, brk or c.index, [a, b, c], c.price - 0.5 * c.atr, PatternTarget(c.price + ab, c.price + 0.618 * ab, c.price + 0.60 * ab), 60 + (10 if brk else 0), ["AB=CD Fibonacci retracement"], brk, brk_price))
        for a, b, c in _pivot_windows(pivots, 3, ("high", "low", "high")):
            ab = a.price - b.price
            bc = c.price - b.price
            if ab <= 0 or not (0.382 <= bc / ab <= 0.618):
                continue
            brk, brk_price = _find_break(candles, atr, b.index, c.index, lambda i, lvl=b.price: lvl, "down", live, cfg)
            entry = brk_price or candles[min(live, c.index)].close
            out.append(_candidate(ctx, "Measured Move Down", "classic", "continuation", "bear", a.index, brk or c.index, [a, b, c], c.price + 0.5 * c.atr, PatternTarget(c.price - ab, c.price - 0.618 * ab, c.price - 0.60 * ab), 60 + (10 if brk else 0), ["AB=CD Fibonacci retracement"], brk, brk_price))
        return out


class GapDetector(BaseDetector):
    name = "Gap"
    family = "event"
    type = "event"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        if getattr(cfg, "market_type", "") == "crypto":
            return []
        out = []
        for i in range(max(1, _bar_start(live)), min(live + 1, len(candles))):
            up = candles[i].low > candles[i - 1].high + gap_tol(atr[i], candles[i].close, cfg)
            down = candles[i].high < candles[i - 1].low - gap_tol(atr[i], candles[i].close, cfg)
            if not (up or down):
                continue
            bias = "bull" if up else "bear"
            gap_h = candles[i].low - candles[i - 1].high if up else candles[i - 1].low - candles[i].high
            classification = "Breakaway" if VolumeEngine.is_breakout_volume(candles, i, 1.5) else "Common"
            entry = candles[i].close
            stop = candles[i - 1].high if up else candles[i - 1].low
            out.append(_candidate(ctx, f"{classification} Gap {'Up' if up else 'Down'}", "event", "event", bias, i - 1, i, [], stop, _targets(bias, entry, max(gap_h, atr[i]), 0.82), 48 + (15 if classification == "Breakaway" else 0), [f"{classification} gap"], i if _closed(i, live) else None, entry if _closed(i, live) else None))
        return out[-12:]


class NR4Detector(BaseDetector):
    name = "NR4"
    family = "event"
    type = "event"

    def detect(self, ctx):
        return _narrow_range(ctx, 4, "NR4")


class NR7Detector(BaseDetector):
    name = "NR7"
    family = "event"
    type = "event"

    def detect(self, ctx):
        return _narrow_range(ctx, 7, "NR7")


def _narrow_range(ctx, length, name):
    candles, pivots, atr, live, cfg = _ctx(ctx)
    out = []
    for i in range(max(length - 1, _bar_start(live)), live + 1):
        rng = candles[i].high - candles[i].low
        if rng < min(candles[j].high - candles[j].low for j in range(i - length + 1, i)):
            inside = i > 0 and candles[i].high < candles[i - 1].high and candles[i].low > candles[i - 1].low
            conf = 45 + (15 if inside and name == "NR7" else 0)
            entry = candles[i].close
            target = PatternTarget(candles[i].high + rng, candles[i].high + 0.618 * rng, candles[i].high + rng)
            p = _candidate(ctx, name, "event", "event", "neutral", i, i, [], candles[i].low, target, conf, ["Narrowest range setup" + (" and inside bar" if inside else "")], None, None)
            p.metadata["breakout_levels"] = {"up": candles[i].high, "down": candles[i].low}
            out.append(p)
    return out[-20:]


class CandlestickDetector(BaseDetector):
    name = "Candlestick"
    family = "candlestick"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for i in range(max(2, _bar_start(live)), live + 1):
            c = candles[i]
            body = abs(c.close - c.open)
            rng = max(c.high - c.low, 1e-9)
            upper = c.high - max(c.open, c.close)
            lower = min(c.open, c.close) - c.low
            small = body / rng <= 0.15
            doji = body / rng <= 0.10
            uptrend = _trend_up(candles, i - 1, 10)
            downtrend = _trend_down(candles, i - 1, 10)
            patterns = []
            if doji:
                patterns.append(("Doji", "neutral", 42))
            if doji and upper <= 0.10 * rng and lower / rng >= 0.60 and downtrend:
                patterns.append(("Dragonfly Doji", "bull", 56))
            if doji and lower <= 0.10 * rng and upper / rng >= 0.60 and uptrend:
                patterns.append(("Gravestone Doji", "bear", 56))
            if small and lower >= 2 * max(body, 1e-9) and upper <= 0.25 * max(body, 1e-9):
                patterns.append(("Hammer" if downtrend else "Hanging Man", "bull" if downtrend else "bear", 58))
            if small and upper >= 2 * max(body, 1e-9) and lower <= 0.25 * max(body, 1e-9):
                patterns.append(("Inverted Hammer" if downtrend else "Shooting Star", "bull" if downtrend else "bear", 55))
            if i >= 1:
                p = candles[i - 1]
                prev_body = (min(p.open, p.close), max(p.open, p.close))
                cur_body = (min(c.open, c.close), max(c.open, c.close))
                if c.close > c.open and p.close < p.open and cur_body[0] <= prev_body[0] and cur_body[1] >= prev_body[1] and downtrend:
                    patterns.append(("Bullish Engulfing", "bull", 63))
                if c.close < c.open and p.close > p.open and cur_body[0] <= prev_body[0] and cur_body[1] >= prev_body[1] and uptrend:
                    patterns.append(("Bearish Engulfing", "bear", 63))
                if c.high <= p.high and c.low >= p.low:
                    patterns.append(("Inside Bar", "neutral", 45))
                if c.high >= p.high and c.low <= p.low:
                    patterns.append(("Outside Bar", "neutral", 47))
                if abs(c.high - p.high) / max(c.high, p.high) <= 0.001 and p.close > p.open and c.close < c.open:
                    patterns.append(("Tweezer Top", "bear", 57))
                if abs(c.low - p.low) / max(c.low, p.low, 1e-9) <= 0.001 and p.close < p.open and c.close > c.open:
                    patterns.append(("Tweezer Bottom", "bull", 57))
            if i >= 2:
                a, b = candles[i - 2], candles[i - 1]
                if a.close < a.open and abs(b.close - b.open) <= 0.25 * (a.high - a.low) and c.close > c.open and c.close > (a.open + a.close) / 2:
                    patterns.append(("Morning Star", "bull", 66))
                if a.close > a.open and abs(b.close - b.open) <= 0.25 * (a.high - a.low) and c.close < c.open and c.close < (a.open + a.close) / 2:
                    patterns.append(("Evening Star", "bear", 66))
            if body <= 0.25 * rng and max(upper, lower) >= 0.66 * rng and min(upper, lower) <= 0.15 * rng:
                patterns.append(("Pin Bar", "bull" if lower > upper else "bear", 58))
            for name, bias, conf in patterns:
                stop = c.low if bias != "bear" else c.high
                entry = c.close
                height = max(atr[i], rng)
                out.append(_candidate(ctx, name, "candlestick", "event", bias, i, i, [], stop, _targets("bull" if bias != "bear" else "bear", entry, height, 0.74), conf, ["Candlestick rulebook geometry"], None, None))
        out.extend(_multi_candle_patterns(ctx))
        return out[-50:]


def _multi_candle_patterns(ctx):
    candles, pivots, atr, live, cfg = _ctx(ctx)
    out = []
    for i in range(max(4, _bar_start(live)), live + 1):
        win = candles[i - 2:i + 1]
        if all(c.close > c.open for c in win) and all(win[k].close > win[k - 1].close for k in (1, 2)):
            out.append(_candidate(ctx, "Three White Soldiers", "candlestick", "event", "bull", i - 2, i, [], min(c.low for c in win), _targets("bull", candles[i].close, 3 * atr[i], 0.74), 66, ["Three consecutive long bullish bars"]))
        if all(c.close < c.open for c in win) and all(win[k].close < win[k - 1].close for k in (1, 2)):
            out.append(_candidate(ctx, "Three Black Crows", "candlestick", "event", "bear", i - 2, i, [], max(c.high for c in win), _targets("bear", candles[i].close, 3 * atr[i], 0.74), 66, ["Three consecutive long bearish bars"]))
        if i >= 4:
            c1, c2, c3, c4, c5 = candles[i - 4:i + 1]
            if c1.close > c1.open and c5.close > c5.open and c5.close > c1.high and all(c.low >= c1.low and c.high <= c1.high for c in (c2, c3, c4)):
                out.append(_candidate(ctx, "Rising Three Methods", "candlestick", "continuation", "bull", i - 4, i, [], c1.low, _targets("bull", c5.close, c1.high - c1.low, 0.74), 63, ["Bullish continuation five-candle structure"]))
            if c1.close < c1.open and c5.close < c5.open and c5.close < c1.low and all(c.low >= c1.low and c.high <= c1.high for c in (c2, c3, c4)):
                out.append(_candidate(ctx, "Falling Three Methods", "candlestick", "continuation", "bear", i - 4, i, [], c1.high, _targets("bear", c5.close, c1.high - c1.low, 0.74), 63, ["Bearish continuation five-candle structure"]))
    return out


class SpikesDetector(BaseDetector):
    name = "Spikes and Tails"
    family = "event"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for i in range(max(1, _bar_start(live)), live):
            c = candles[i]
            rng = c.high - c.low
            body = abs(c.close - c.open)
            if rng >= 1.5 * atr[i] and body <= 0.25 * rng:
                lower = min(c.open, c.close) - c.low
                upper = c.high - max(c.open, c.close)
                if lower / rng >= 0.60 and candles[i + 1].close > c.low:
                    out.append(_candidate(ctx, "Bullish Spike Tail", "event", "event", "bull", i, i + 1, [], c.low, _targets("bull", candles[i + 1].close, rng, 0.74), 58, ["Long lower tail rejection"], i + 1, candles[i + 1].close))
                if upper / rng >= 0.60 and candles[i + 1].close < c.high:
                    out.append(_candidate(ctx, "Bearish Spike Tail", "event", "event", "bear", i, i + 1, [], c.high, _targets("bear", candles[i + 1].close, rng, 0.74), 58, ["Long upper tail rejection"], i + 1, candles[i + 1].close))
        return out[-20:]


class HookReversalDetector(BaseDetector):
    name = "Hook Reversal"
    family = "event"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for i in range(max(2, _bar_start(live)), live):
            p, c = candles[i - 1], candles[i]
            pr = p.high - p.low
            cr = max(c.high - c.low, 1e-9)
            if pr >= 1.5 * atr[i - 1] and c.high < p.high and c.low > p.low:
                if p.close > p.open and p.high - c.open <= 0.10 * pr and c.close - c.low <= 0.15 * cr and _trend_up(candles, i - 1):
                    if candles[i + 1].close < c.close:
                        out.append(_candidate(ctx, "Hook Reversal Bearish", "event", "event", "bear", i - 1, i + 1, [], p.high, _targets("bear", candles[i + 1].close, pr, 0.74), 62, ["Inside hook after wide bullish bar"], i + 1, candles[i + 1].close))
                if p.close < p.open and c.open - p.low <= 0.10 * pr and c.high - c.close <= 0.15 * cr and _trend_down(candles, i - 1):
                    if candles[i + 1].close > c.close:
                        out.append(_candidate(ctx, "Hook Reversal Bullish", "event", "event", "bull", i - 1, i + 1, [], p.low, _targets("bull", candles[i + 1].close, pr, 0.74), 62, ["Inside hook after wide bearish bar"], i + 1, candles[i + 1].close))
        return out[-10:]


class KeyReversalDetector(BaseDetector):
    name = "Key Reversal"
    family = "event"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for i in range(max(1, _bar_start(live)), live + 1):
            p, c = candles[i - 1], candles[i]
            if c.high > p.high and c.low < p.low and VolumeEngine.is_breakout_volume(candles, i, 1.5):
                if c.open > p.close and c.close < p.low:
                    out.append(_candidate(ctx, "Key Reversal Bearish", "event", "event", "bear", i - 1, i, [], c.high, _targets("bear", c.close, c.high - c.low, 0.74), 66, ["Outside day with bearish close"], i if _closed(i, live) else None, c.close))
                if c.open < p.close and c.close > p.high:
                    out.append(_candidate(ctx, "Key Reversal Bullish", "event", "event", "bull", i - 1, i, [], c.low, _targets("bull", c.close, c.high - c.low, 0.74), 66, ["Outside day with bullish close"], i if _closed(i, live) else None, c.close))
        return out[-10:]


class ClosingPriceReversalDetector(BaseDetector):
    name = "Closing Price Reversal"
    family = "event"

    def detect(self, ctx):
        return _close_reversal(ctx, weak=False)


class OpenCloseReversalDetector(BaseDetector):
    name = "Open-Close Reversal"
    family = "event"

    def detect(self, ctx):
        return _close_reversal(ctx, weak=True)


def _close_reversal(ctx, weak):
    candles, pivots, atr, live, cfg = _ctx(ctx)
    out = []
    for i in range(max(10, _bar_start(live)), live + 1):
        c = candles[i]
        rng = max(c.high - c.low, 1e-9)
        if c.high >= max(x.high for x in candles[i - 10:i + 1]) and c.high - c.open <= 0.2 * rng and c.close - c.low <= 0.2 * rng:
            if (weak and c.close > candles[i - 1].close) or ((not weak) and c.close < candles[i - 1].close):
                out.append(_candidate(ctx, "Open-Close Reversal Bearish" if weak else "Closing Price Reversal Bearish", "event", "event", "bear", i, i, [], c.high, _targets("bear", c.close, rng, 0.74), 52 if weak else 58, ["High close-reversal bar"], None, None))
        if c.low <= min(x.low for x in candles[i - 10:i + 1]) and c.open - c.low <= 0.2 * rng and c.high - c.close <= 0.2 * rng:
            if (weak and c.close < candles[i - 1].close) or ((not weak) and c.close > candles[i - 1].close):
                out.append(_candidate(ctx, "Open-Close Reversal Bullish" if weak else "Closing Price Reversal Bullish", "event", "event", "bull", i, i, [], c.low, _targets("bull", c.close, rng, 0.74), 52 if weak else 58, ["Low close-reversal bar"], None, None))
    return out[-10:]


class PivotPointReversalDetector(BaseDetector):
    name = "Pivot Point Reversal"
    family = "event"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for i in range(max(2, _bar_start(live)), live + 1):
            p = candles[i - 1]
            if p.high > candles[i - 2].high and p.high > candles[i].high and candles[i].close < p.low:
                height = p.high - p.low
                out.append(_candidate(ctx, "Pivot Point Reversal Bearish", "event", "event", "bear", i - 1, i, [], p.high, PatternTarget(candles[i].close - height, candles[i].close - 0.618 * height, candles[i].close - 0.74 * height), 61, ["Local peak break"], i, candles[i].close))
            if p.low < candles[i - 2].low and p.low < candles[i].low and candles[i].close > p.high:
                height = p.high - p.low
                out.append(_candidate(ctx, "Pivot Point Reversal Bullish", "event", "event", "bull", i - 1, i, [], p.low, PatternTarget(candles[i].close + height, candles[i].close + 0.618 * height, candles[i].close + 0.74 * height), 61, ["Local trough break"], i, candles[i].close))
        return out[-10:]


class PipeTopDetector(BaseDetector):
    name = "Pipe Top"
    family = "event"

    def detect(self, ctx):
        return _pipe(ctx, "top")


class PipeBottomDetector(BaseDetector):
    name = "Pipe Bottom"
    family = "event"

    def detect(self, ctx):
        return _pipe(ctx, "bottom")


def _pipe(ctx, kind):
    candles, pivots, atr, live, cfg = _ctx(ctx)
    out = []
    mult = 1.3 if ctx.get("interval") == "1w" else 1.0
    for i in range(max(1, _bar_start(live)), live + 1):
        a, b = candles[i - 1], candles[i]
        ar, br = a.high - a.low, b.high - b.low
        overlap = max(0, min(a.high, b.high) - max(a.low, b.low)) / max(min(ar, br), 1e-9)
        if ar >= 1.8 * atr[i - 1] and br >= 1.8 * atr[i] and overlap >= 0.70:
            if kind == "top" and abs(a.high - b.high) / max(a.high, b.high) <= 0.005 and a.close > a.open and b.close < b.open:
                h = max(a.high, b.high) - min(a.low, b.low)
                out.append(_candidate(ctx, "Pipe Top", "event", "event", "bear", i - 1, i, [], max(a.high, b.high), _targets("bear", b.close, h, 0.74), 62 * mult, ["Adjacent wide overlapping top bars"], i if _closed(i, live) else None, b.close))
            if kind == "bottom" and abs(a.low - b.low) / max(a.low, b.low, 1e-9) <= 0.005 and a.close < a.open and b.close > b.open:
                h = max(a.high, b.high) - min(a.low, b.low)
                out.append(_candidate(ctx, "Pipe Bottom", "event", "event", "bull", i - 1, i, [], min(a.low, b.low), _targets("bull", b.close, h, 0.74), 62 * mult, ["Adjacent wide overlapping bottom bars"], i if _closed(i, live) else None, b.close))
    return out[-10:]


class HornBottomDetector(BaseDetector):
    name = "Horn Bottom"
    family = "event"

    def detect(self, ctx):
        return _horn(ctx, "bottom")


class HornTopDetector(BaseDetector):
    name = "Horn Top"
    family = "event"

    def detect(self, ctx):
        return _horn(ctx, "top")


def _horn(ctx, kind):
    candles, pivots, atr, live, cfg = _ctx(ctx)
    out = []
    mult = 1.3 if ctx.get("interval") == "1w" else 1.0
    for i in range(max(2, _bar_start(live)), live + 1):
        a, b, c = candles[i - 2], candles[i - 1], candles[i]
        if kind == "bottom":
            if min(a.open, a.close) - a.low >= 1.5 * atr[i - 2] and min(c.open, c.close) - c.low >= 1.5 * atr[i] and b.low > max(a.low, c.low) and abs(a.low - c.low) / max(a.low, 1e-9) <= 0.015:
                level = max(a.high, b.high, c.high)
                brk, brk_price = _find_break(candles, atr, i - 2, i, lambda x, lvl=level: lvl, "up", live, cfg)
                h = level - min(a.low, c.low)
                out.append(_candidate(ctx, "Horn Bottom", "event", "event", "bull", i - 2, brk or i, [], min(a.low, c.low), PatternTarget(level + h, level + 0.618 * h, b.high + 0.74 * h), 60 * mult, ["Three-bar horn bottom"], brk, brk_price))
        else:
            if a.high - max(a.open, a.close) >= 1.5 * atr[i - 2] and c.high - max(c.open, c.close) >= 1.5 * atr[i] and b.high < min(a.high, c.high) and abs(a.high - c.high) / max(a.high, 1e-9) <= 0.015:
                level = min(a.low, b.low, c.low)
                brk, brk_price = _find_break(candles, atr, i - 2, i, lambda x, lvl=level: lvl, "down", live, cfg)
                h = max(a.high, c.high) - level
                out.append(_candidate(ctx, "Horn Top", "event", "event", "bear", i - 2, brk or i, [], max(a.high, c.high), PatternTarget(level - h, level - 0.618 * h, b.low - 0.74 * h), 60 * mult, ["Three-bar horn top"], brk, brk_price))
    return out[-10:]


class DeadCatBounceDetector(BaseDetector):
    name = "Dead Cat Bounce"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for start in range(max(0, live - 260), max(0, live - 15)):
            low_idx = min(range(start + 1, min(start + 11, len(candles))), key=lambda i: candles[i].low)
            plunge = candles[start].close - candles[low_idx].low
            if plunge / max(candles[start].close, 1e-9) < 0.15:
                continue
            for end in range(low_idx + 4, min(low_idx + 13, live + 1)):
                bounce_high = max(candles[j].high for j in range(low_idx, end + 1))
                if (bounce_high - candles[low_idx].low) / plunge <= 0.382:
                    brk, brk_price = _find_break(candles, atr, low_idx, end, lambda i, lvl=candles[low_idx].low: lvl, "down", live, cfg)
                    if brk:
                        out.append(_candidate(ctx, self.name, "classic", "reversal", "bear", start, brk, [], (bounce_high + candles[low_idx].low) / 2, PatternTarget(bounce_high - plunge, bounce_high - 0.618 * plunge, bounce_high - 0.74 * plunge), 70, ["15% plunge and weak Fibonacci bounce"], brk, brk_price))
        return out[:3]


class LongIslandDetector(GapDetector):
    name = "Long Island"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        if getattr(cfg, "market_type", "") == "crypto":
            return out
        for i in range(max(1, live - 500), live - 25):
            up1 = candles[i].low > candles[i - 1].high + atr[i]
            dn1 = candles[i].high < candles[i - 1].low - atr[i]
            if not (up1 or dn1):
                continue
            for j in range(i + 25, min(i + 80, live + 1)):
                up2 = candles[j].low > candles[j - 1].high + atr[j]
                dn2 = candles[j].high < candles[j - 1].low - atr[j]
                if (up1 and up2) or (dn1 and dn2):
                    bias = "bull" if up1 else "bear"
                    h = max(c.high for c in candles[i:j + 1]) - min(c.low for c in candles[i:j + 1])
                    out.append(_candidate(ctx, self.name, "classic", "continuation", bias, i, j, [], min(c.low for c in candles[i:j + 1]) if bias == "bull" else max(c.high for c in candles[i:j + 1]), _targets(bias, candles[j - 1].close, 0.82 * h / 2, 1.0), 63, ["Entry and exit gaps with island consolidation"], j, candles[j].close))
                    break
        return out[-5:]


class IslandReversalDetector(GapDetector):
    name = "Island Reversal"

    def detect(self, ctx):
        candles, pivots, atr, live, cfg = _ctx(ctx)
        if getattr(cfg, "market_type", "") == "crypto":
            return []
        out = []
        for i in range(max(1, live - 300), live - 2):
            gap_up = candles[i].low > candles[i - 1].high + gap_tol(atr[i], candles[i].close, cfg)
            gap_dn = candles[i].high < candles[i - 1].low - gap_tol(atr[i], candles[i].close, cfg)
            for j in range(i + 1, min(i + 10, live + 1)):
                exit_dn = candles[j].high < min(c.low for c in candles[i:j]) - gap_tol(atr[j], candles[j].close, cfg)
                exit_up = candles[j].low > max(c.high for c in candles[i:j]) + gap_tol(atr[j], candles[j].close, cfg)
                if gap_up and exit_dn:
                    h, l = max(c.high for c in candles[i:j]), min(c.low for c in candles[i:j])
                    out.append(_candidate(ctx, "Island Reversal Top", "classic", "reversal", "bear", i, j, [], h, PatternTarget(l - (h - l), l - 0.618 * (h - l), l - 0.62 * (h - l)), 74, ["Exhaustion gap up and breakaway gap down"], j, candles[j].close))
                if gap_dn and exit_up:
                    h, l = max(c.high for c in candles[i:j]), min(c.low for c in candles[i:j])
                    out.append(_candidate(ctx, "Island Reversal Bottom", "classic", "reversal", "bull", i, j, [], l, PatternTarget(h + (h - l), h + 0.618 * (h - l), h + 0.82 * (h - l)), 74, ["Exhaustion gap down and breakaway gap up"], j, candles[j].close))
        return out[-6:]


class DiamondTopDetector(BroadeningTopDetector):
    name = "Diamond Top"


class DiamondBottomDetector(BroadeningTopDetector):
    name = "Diamond Bottom"


class BumpRunDetector(ChannelUpDetector):
    name = "BARR Top"


class DivingBoardDetector(BaseDetector):
    name = "Diving Board"

    def detect(self, ctx):
        if ctx.get("interval") != "1w":
            return []
        candles, pivots, atr, live, cfg = _ctx(ctx)
        out = []
        for start in range(0, max(0, live - 40)):
            for end in range(start + 16, min(start + 32, live - 4) + 1):
                closes = np.array([c.close for c in candles[start:end + 1]])
                if closes.std() / max(closes.mean(), 1e-9) > 0.010:
                    continue
                board = float(closes.mean())
                plunge_end = min(end + 4, live)
                low = min(c.low for c in candles[end + 1:plunge_end + 1])
                drop = (board - low) / board
                if 0.14 <= drop <= 0.40:
                    recovery = next((i for i in range(plunge_end + 1, min(plunge_end + 13, live + 1)) if candles[i].close >= board), None)
                    if recovery:
                        out.append(_candidate(ctx, self.name, "volatility", "reversal", "bull", start, recovery, [], low - atr[recovery], _targets("bull", candles[recovery].close, board - low, 0.73), 72, ["Flat weekly board, plunge, and recovery"], recovery, candles[recovery].close))
        return out[-3:]
