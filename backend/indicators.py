from typing import Dict, List
import math
import traceback

import numpy as np


EMPTY_SR = {
    "support": [],
    "resistance": [],
    "order_blocks": [],
    "dynamic_zone": {},
    "timestamp": 0,
    "current_price": 0,
}


def _finite_float(value, default=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _sanitize_candles(candles: List[Dict]) -> List[Dict]:
    clean = []
    for candle in candles or []:
        if not isinstance(candle, dict):
            continue
        time = _finite_float(candle.get("time"))
        open_ = _finite_float(candle.get("open"))
        high = _finite_float(candle.get("high"))
        low = _finite_float(candle.get("low"))
        close = _finite_float(candle.get("close"))
        volume = _finite_float(candle.get("volume"))
        if None in (time, open_, high, low, close, volume):
            continue
        high = max(high, open_, close)
        low = min(low, open_, close)
        clean.append({
            **candle,
            "time": int(time),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": max(volume, 0.0),
        })
    return sorted(clean, key=lambda x: x["time"])


def _extract_closes(candles) -> List[float]:
    return [float(i["close"]) for i in candles]


def _extract_highs(candles) -> List[float]:
    return [float(i["high"]) for i in candles]


def _extract_lows(candles) -> List[float]:
    return [float(i["low"]) for i in candles]


def _extract_times(candles) -> List[int]:
    return [int(i["time"]) for i in candles]


def _extract_volumes(candles) -> List[float]:
    return [float(i["volume"]) for i in candles]


def sma(series, period):
    period = int(period)
    if period <= 0 or len(series) < period:
        return []
    return [float(np.mean(series[i:i + period])) for i in range(0, len(series) - period + 1)]


def ema(series, period):
    period = int(period)
    if period <= 0 or len(series) < period:
        return []
    alpha = 2.0 / (period + 1)
    out = [float(np.mean(series[:period]))]
    for i in range(period, len(series)):
        out.append(alpha * series[i] + (1 - alpha) * out[-1])
    return out


def rma(series, period):
    period = int(period)
    if period <= 0 or len(series) < period:
        return []
    alpha = 1.0 / period
    out = [float(np.mean(series[:period]))]
    for i in range(period, len(series)):
        out.append(alpha * series[i] + (1 - alpha) * out[-1])
    return out


def wma(series, period):
    period = int(period)
    if period <= 0 or len(series) < period:
        return []
    weights = np.arange(1, period + 1)
    total = float(weights.sum())
    return [float(np.dot(series[i:i + period], weights) / total) for i in range(0, len(series) - period + 1)]


def _safe_round(value):
    value = _finite_float(value)
    return round(value, 2) if value is not None else None


def _point(time, value, **extra):
    value = _safe_round(value)
    if value is None:
        return None
    point = {"time": int(time), "value": value}
    for key, val in extra.items():
        if isinstance(val, float):
            val = _safe_round(val)
        if val is not None:
            point[key] = val
    return point


def _clean_points(points):
    return [p for p in points if p and "time" in p and _finite_float(p.get("value")) is not None]


def _empty_like(indicator_type):
    if indicator_type == "macd":
        return {"macd": [], "signal": [], "histogram": []}
    if indicator_type in ("bollinger", "keltner", "donchian"):
        return {"upper": [], "middle": [], "lower": []}
    if indicator_type in ("stoch", "stochrsi"):
        return {"k": [], "d": []}
    if indicator_type == "adx":
        return {"adx": [], "plus_di": [], "minus_di": []}
    if indicator_type == "ichimoku":
        return {"conversion": [], "base": [], "spanA": [], "spanB": [], "lagging": []}
    if indicator_type == "pivotpoints":
        return {"pivot": [], "r1": [], "r2": [], "r3": [], "s1": [], "s2": [], "s3": []}
    if indicator_type == "wavetrend":
        return {"wt1": [], "wt2": []}
    if indicator_type == "aroon":
        return {"up": [], "down": []}
    if indicator_type == "vortex":
        return {"plus": [], "minus": []}
    return []


def _int(params, key, default, minimum=1):
    value = params.get(key, default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _float(params, key, default, minimum=None):
    value = _finite_float(params.get(key, default), default)
    if minimum is not None:
        value = max(value, minimum)
    return value


def _source(candles, source="close"):
    source = (source or "close").lower()
    if source == "open":
        return [i["open"] for i in candles]
    if source == "high":
        return [i["high"] for i in candles]
    if source == "low":
        return [i["low"] for i in candles]
    if source in ("hl2", "median"):
        return [(i["high"] + i["low"]) / 2 for i in candles]
    if source in ("hlc3", "typical"):
        return [(i["high"] + i["low"] + i["close"]) / 3 for i in candles]
    if source in ("ohlc4", "average"):
        return [(i["open"] + i["high"] + i["low"] + i["close"]) / 4 for i in candles]
    return _extract_closes(candles)


def _with_times(times, values, start_index):
    return _clean_points([_point(times[start_index + i], value) for i, value in enumerate(values) if start_index + i < len(times)])


def _sma_aligned(values, period):
    period = int(period)
    out = [None] * len(values)
    if period <= 0:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if all(_finite_float(v) is not None for v in window):
            out[i] = float(np.mean(window))
    return out


def compute_atr(candles, period=14) -> List[Dict]:
    try:
        if len(candles or []) > 5000:
            candles = candles[-5000:]
        candles = _sanitize_candles(candles)
        period = max(1, int(period or 14))
        if len(candles) <= period:
            return []
        highs, lows, closes, times = _extract_highs(candles), _extract_lows(candles), _extract_closes(candles), _extract_times(candles)
        tr = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, len(candles))
        ]
        vals = rma(tr, period)
        return _with_times(times, vals, period)
    except Exception as e:
        print(f"[INDICATOR ERROR] atr: {e}")
        return []


def _rsi_values(values, period):
    if len(values) <= period:
        return [], []
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_gains = rma(gains, period)
    avg_losses = rma(losses, period)
    vals = []
    for gain, loss in zip(avg_gains, avg_losses):
        vals.append(100 if loss == 0 else 100 - (100 / (1 + gain / loss)))
    return vals, list(range(period, period + len(vals)))


def compute_indicator(indicator_type: str, candles: list, period=None, **params):
    try:
        if len(candles or []) > 5000:
            candles = candles[-5000:]
        candles = _sanitize_candles(candles)
        indicator_type = (indicator_type or "").lower()
        if period is not None and "period" not in params:
            params["period"] = period

        defaults = {
            "rsi": 14, "trix": 18, "supertrend": 10, "stoch": 14, "cci": 20,
            "williamsr": 14, "roc": 12, "momentum": 10, "adx": 14, "atr": 14,
            "mfi": 14, "cmf": 20, "kama": 10, "aroon": 25, "vortex": 14,
        }
        current_period = _int(params, "period", defaults.get(indicator_type, 20))
        if len(candles) < current_period:
            return _empty_like(indicator_type)

        highs, lows = _extract_highs(candles), _extract_lows(candles)
        closes, times = _extract_closes(candles), _extract_times(candles)
        volumes = _extract_volumes(candles)
        values = _source(candles, params.get("source", "close"))
        n = len(closes)

        if indicator_type == "sma":
            return _with_times(times, sma(values, current_period), current_period - 1)

        if indicator_type == "ema":
            return _with_times(times, ema(values, current_period), current_period - 1)

        if indicator_type == "wma":
            return _with_times(times, wma(values, current_period), current_period - 1)

        if indicator_type == "hma":
            half_p, sqrt_p = max(1, current_period // 2), max(1, int(math.sqrt(current_period)))
            half_vals, full_vals = wma(values, half_p), wma(values, current_period)
            if not half_vals or not full_vals:
                return []
            diff = len(half_vals) - len(full_vals)
            raw = [2 * half_vals[diff + i] - full_vals[i] for i in range(len(full_vals))]
            vals = wma(raw, sqrt_p)
            return _with_times(times, vals, n - len(vals))

        if indicator_type == "dema":
            e1 = ema(values, current_period)
            e2 = ema(e1, current_period)
            vals = [2 * e1[-len(e2) + i] - e2[i] for i in range(len(e2))]
            return _with_times(times, vals, n - len(vals))

        if indicator_type == "tema":
            e1 = ema(values, current_period)
            e2 = ema(e1, current_period)
            e3 = ema(e2, current_period)
            vals = [3 * e1[-len(e3) + i] - 3 * e2[-len(e3) + i] + e3[i] for i in range(len(e3))]
            return _with_times(times, vals, n - len(vals))

        if indicator_type == "kama":
            fast_p, slow_p = _int(params, "fast_period", 2), _int(params, "slow_period", 30)
            fast_alpha, slow_alpha = 2.0 / (fast_p + 1), 2.0 / (slow_p + 1)
            kama_prev = closes[current_period]
            out = [_point(times[current_period], kama_prev)]
            for i in range(current_period + 1, n):
                net = abs(closes[i] - closes[i - current_period])
                vol = sum(abs(closes[j] - closes[j - 1]) for j in range(i - current_period + 1, i + 1))
                er = net / vol if vol else 0
                sc = (er * (fast_alpha - slow_alpha) + slow_alpha) ** 2
                kama_prev = kama_prev + sc * (closes[i] - kama_prev)
                out.append(_point(times[i], kama_prev))
            return _clean_points(out)

        if indicator_type == "zlema":
            lag = (current_period - 1) // 2
            adjusted = [2 * closes[i] - closes[max(0, i - lag)] for i in range(n)]
            return _with_times(times, ema(adjusted, current_period), current_period - 1)

        if indicator_type == "vwap":
            out, cum_pv, cum_v = [], 0.0, 0.0
            use_equal_weight = not any(v > 0 for v in volumes)
            for i in range(n):
                weight = 1.0 if use_equal_weight else volumes[i]
                cum_pv += closes[i] * weight
                cum_v += weight
                if cum_v > 0:
                    out.append(_point(times[i], cum_pv / cum_v))
            return _clean_points(out)

        if indicator_type in ("linearreg", "linearregression"):
            out, x = [], np.arange(current_period)
            for i in range(current_period - 1, n):
                slope, intercept = np.polyfit(x, closes[i - current_period + 1:i + 1], 1)
                out.append(_point(times[i], slope * (current_period - 1) + intercept))
            return _clean_points(out)

        if indicator_type == "ao":
            if n < 34:
                return []
            hl2 = [(h + l) / 2 for h, l in zip(highs, lows)]
            fast, slow = sma(hl2, 5), sma(hl2, 34)
            vals = [fast[29 + i] - slow[i] for i in range(len(slow))]
            return _with_times(times, vals, 33)

        if indicator_type == "trix":
            e1, e2 = ema(values, current_period), []
            e2 = ema(e1, current_period)
            e3 = ema(e2, current_period)
            vals = [100 * (e3[i] - e3[i - 1]) / e3[i - 1] if e3[i - 1] else 0 for i in range(1, len(e3))]
            return _with_times(times, vals, n - len(vals))

        if indicator_type == "supertrend":
            multiplier = _float(params, "multiplier", 3.0, 0.0)
            atr_data = compute_atr(candles, current_period)
            if not atr_data:
                return []
            upper, lower, dirs, out = [], [], [], []
            for i, row in enumerate(atr_data):
                idx = n - len(atr_data) + i
                mid = (highs[idx] + lows[idx]) / 2
                basic_upper = mid + multiplier * row["value"]
                basic_lower = mid - multiplier * row["value"]
                if i == 0:
                    curr_upper, curr_lower, direction = basic_upper, basic_lower, 1
                else:
                    prev_upper, prev_lower = upper[-1], lower[-1]
                    curr_upper = basic_upper if (basic_upper < prev_upper or closes[idx - 1] > prev_upper) else prev_upper
                    curr_lower = basic_lower if (basic_lower > prev_lower or closes[idx - 1] < prev_lower) else prev_lower
                    direction = dirs[-1]
                    if direction == 1 and closes[idx] < curr_lower:
                        direction = -1
                    if direction == -1 and closes[idx] > curr_upper:
                        direction = 1
                upper.append(curr_upper); lower.append(curr_lower); dirs.append(direction)
                out.append(_point(row["time"], curr_lower if direction == 1 else curr_upper, dir=direction))
            return _clean_points(out)

        if indicator_type == "ichimoku":
            conv_p = _int(params, "conv_period", 9)
            base_p = _int(params, "base_period", 26)
            span_b_p = _int(params, "span_b_period", 52)
            displacement = _int(params, "displacement", 26)
            if n < max(conv_p, base_p, span_b_p):
                return _empty_like("ichimoku")

            def donchian(p):
                vals = [(max(highs[i - p + 1:i + 1]) + min(lows[i - p + 1:i + 1])) / 2 for i in range(p - 1, n)]
                return vals, p - 1

            conv, conv_start = donchian(conv_p)
            base, base_start = donchian(base_p)
            shift = base_start - conv_start
            span_a = [(conv[shift + i] + base[i]) / 2 for i in range(min(len(base), len(conv) - shift))]
            span_b, span_b_start = donchian(span_b_p)
            return {
                "conversion": _with_times(times, conv, conv_start),
                "base": _with_times(times, base, base_start),
                "spanA": _clean_points([_point(times[min(base_start + i + displacement, n - 1)], v) for i, v in enumerate(span_a)]),
                "spanB": _clean_points([_point(times[min(span_b_start + i + displacement, n - 1)], v) for i, v in enumerate(span_b)]),
                "lagging": _clean_points([_point(times[displacement + i], closes[i]) for i in range(0, max(0, n - displacement))]),
            }

        if indicator_type == "psar":
            start = _float(params, "start", 0.02, 0.0)
            inc = _float(params, "increment", 0.02, 0.0)
            max_af = _float(params, "max_af", params.get("max", 0.20), 0.0)
            sar, ep, af, is_long = [lows[0]], highs[0], start, True
            for i in range(1, n):
                new_sar = sar[-1] + af * (ep - sar[-1])
                if is_long:
                    new_sar = min(new_sar, lows[max(0, i - 1)], lows[max(0, i - 2)])
                    if lows[i] < new_sar:
                        is_long, new_sar, ep, af = False, ep, lows[i], start
                    elif highs[i] > ep:
                        ep, af = highs[i], min(af + inc, max_af)
                else:
                    new_sar = max(new_sar, highs[max(0, i - 1)], highs[max(0, i - 2)])
                    if highs[i] > new_sar:
                        is_long, new_sar, ep, af = True, ep, highs[i], start
                    elif lows[i] < ep:
                        ep, af = lows[i], min(af + inc, max_af)
                sar.append(new_sar)
            return _with_times(times, sar, 0)

        if indicator_type == "heikinashi":
            out, prev_open, prev_close = [], None, None
            for i, c in enumerate(candles):
                ha_close = (c["open"] + c["high"] + c["low"] + c["close"]) / 4
                ha_open = (c["open"] + c["close"]) / 2 if i == 0 else (prev_open + prev_close) / 2
                ha_high, ha_low = max(c["high"], ha_open, ha_close), min(c["low"], ha_open, ha_close)
                item = {"time": c["time"], "open": _safe_round(ha_open), "high": _safe_round(ha_high), "low": _safe_round(ha_low), "close": _safe_round(ha_close), "volume": _safe_round(c["volume"])}
                if all(v is not None for k, v in item.items() if k != "time"):
                    out.append(item)
                prev_open, prev_close = ha_open, ha_close
            return out

        if indicator_type == "pivotpoints":
            lookback = _int(params, "lookback_bars", 24)
            pivot_type = (params.get("pivot_type") or "standard").lower()
            if n < lookback:
                return _empty_like("pivotpoints")
            keys = ["pivot", "r1", "r2", "r3", "s1", "s2", "s3"]
            if pivot_type == "camarilla":
                keys += ["r4", "s4"]
            out = {k: [] for k in keys}
            for i in range(lookback, n):
                h, l, c = max(highs[i - lookback:i]), min(lows[i - lookback:i]), closes[i - 1]
                rng, p = h - l, (h + l + c) / 3
                if pivot_type == "fibonacci":
                    levels = {"pivot": p, "r1": p + .382 * rng, "r2": p + .618 * rng, "r3": p + rng, "s1": p - .382 * rng, "s2": p - .618 * rng, "s3": p - rng}
                elif pivot_type == "woodie":
                    p = (h + l + 2 * c) / 4
                    levels = {"pivot": p, "r1": 2 * p - l, "s1": 2 * p - h, "r2": p + rng, "s2": p - rng, "r3": h + 2 * (p - l), "s3": l - 2 * (h - p)}
                elif pivot_type == "camarilla":
                    levels = {"pivot": p, "r1": c + rng * 1.1 / 12, "r2": c + rng * 1.1 / 6, "r3": c + rng * 1.1 / 4, "r4": c + rng * 1.1 / 2, "s1": c - rng * 1.1 / 12, "s2": c - rng * 1.1 / 6, "s3": c - rng * 1.1 / 4, "s4": c - rng * 1.1 / 2}
                else:
                    levels = {"pivot": p, "r1": 2 * p - l, "s1": 2 * p - h, "r2": p + rng, "s2": p - rng, "r3": h + 2 * (p - l), "s3": l - 2 * (h - p)}
                for key, value in levels.items():
                    out[key].append(_point(times[i], value))
            return {k: _clean_points(v) for k, v in out.items()}

        if indicator_type == "rsi":
            vals, idxs = _rsi_values(values, current_period)
            return _clean_points([_point(times[idxs[i]], v) for i, v in enumerate(vals) if idxs[i] < n])

        if indicator_type == "macd":
            fast, slow, sig = _int(params, "fast", 12), _int(params, "slow", 26), _int(params, "signal", 9)
            if fast >= slow or n < slow + sig - 1:
                return _empty_like("macd")
            fast_vals, slow_vals = ema(values, fast), ema(values, slow)
            raw = [fast_vals[slow - fast + i] - slow_vals[i] for i in range(len(slow_vals))]
            signal_vals = ema(raw, sig)
            macd_vals = raw[sig - 1:]
            hist = [macd_vals[i] - signal_vals[i] for i in range(len(signal_vals))]
            start = n - len(signal_vals)
            return {"macd": _with_times(times, macd_vals, start), "signal": _with_times(times, signal_vals, start), "histogram": _with_times(times, hist, start)}

        if indicator_type == "stoch":
            k_period, smooth, d_period = _int(params, "k_period", 14), _int(params, "smooth", 3), _int(params, "d_period", 3)
            if n < k_period + smooth + d_period - 2:
                return _empty_like("stoch")
            raw, raw_times = [], []
            for i in range(k_period - 1, n):
                hh, ll = max(highs[i - k_period + 1:i + 1]), min(lows[i - k_period + 1:i + 1])
                raw.append(100 * (closes[i] - ll) / (hh - ll) if hh != ll else 50)
                raw_times.append(times[i])
            slow_k = sma(raw, smooth)
            k_times = raw_times[smooth - 1:]
            d_vals = sma(slow_k, d_period)
            d_times = k_times[d_period - 1:]
            k_vals = slow_k[d_period - 1:]
            return {"k": _clean_points([_point(d_times[i], v) for i, v in enumerate(k_vals)]), "d": _clean_points([_point(d_times[i], v) for i, v in enumerate(d_vals)])}

        if indicator_type == "stochrsi":
            rsi_p, stoch_p = _int(params, "rsi_period", 14), _int(params, "stoch_period", 14)
            k_smooth, d_smooth = _int(params, "k_smooth", 3), _int(params, "d_smooth", 3)
            rsi_vals, rsi_idxs = _rsi_values(values, rsi_p)
            if len(rsi_vals) < stoch_p + k_smooth + d_smooth - 2:
                return _empty_like("stochrsi")
            rsi_by_candle = [None] * n
            for rsi_idx, rsi_value in zip(rsi_idxs, rsi_vals):
                if rsi_idx < n:
                    rsi_by_candle[rsi_idx] = rsi_value

            raw = [None] * n
            for i in range(n):
                window = rsi_by_candle[i - stoch_p + 1:i + 1] if i >= stoch_p - 1 else []
                if len(window) != stoch_p or any(_finite_float(v) is None for v in window):
                    continue
                lo, hi = min(window), max(window)
                raw[i] = 100 * (rsi_by_candle[i] - lo) / (hi - lo) if hi != lo else 50

            k_by_candle = _sma_aligned(raw, k_smooth)
            d_by_candle = _sma_aligned(k_by_candle, d_smooth)
            return {
                "k": _clean_points([_point(times[i], k_by_candle[i]) for i in range(n)]),
                "d": _clean_points([_point(times[i], d_by_candle[i]) for i in range(n)]),
            }

        if indicator_type == "cci":
            tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
            out = []
            for i in range(current_period - 1, n):
                window = tp[i - current_period + 1:i + 1]
                avg = float(np.mean(window))
                md = float(np.mean([abs(x - avg) for x in window]))
                out.append(_point(times[i], (tp[i] - avg) / (0.015 * md) if md else 0))
            return _clean_points(out)

        if indicator_type == "williamsr":
            out = []
            for i in range(current_period - 1, n):
                hh, ll = max(highs[i - current_period + 1:i + 1]), min(lows[i - current_period + 1:i + 1])
                out.append(_point(times[i], -100 * (hh - closes[i]) / (hh - ll) if hh != ll else -50))
            return _clean_points(out)

        if indicator_type == "roc":
            return _clean_points([_point(times[i], 100 * (closes[i] - closes[i - current_period]) / closes[i - current_period] if closes[i - current_period] else 0) for i in range(current_period, n)])

        if indicator_type == "momentum":
            return _clean_points([_point(times[i], closes[i] - closes[i - current_period]) for i in range(current_period, n)])

        if indicator_type == "adx":
            if n < current_period * 2:
                return _empty_like("adx")
            tr, pdm, mdm = [], [], []
            for i in range(1, n):
                up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
                pdm.append(up if up > down and up > 0 else 0)
                mdm.append(down if down > up and down > 0 else 0)
                tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
            str_tr, str_pdm, str_mdm = rma(tr, current_period), rma(pdm, current_period), rma(mdm, current_period)
            plus = [100 * p / t if t else 0 for p, t in zip(str_pdm, str_tr)]
            minus = [100 * m / t if t else 0 for m, t in zip(str_mdm, str_tr)]
            dx = [100 * abs(p - m) / (p + m) if (p + m) else 0 for p, m in zip(plus, minus)]
            adx_vals = rma(dx, current_period)
            adx_start = 2 * current_period - 1
            plus_aligned = plus[current_period - 1:]
            minus_aligned = minus[current_period - 1:]
            return {"adx": _with_times(times, adx_vals, adx_start), "plus_di": _with_times(times, plus_aligned, adx_start), "minus_di": _with_times(times, minus_aligned, adx_start)}

        if indicator_type == "bollinger":
            std_dev = _float(params, "std_dev", 2.0, 0.0)
            mids = sma(values, current_period)
            out = {"upper": [], "middle": [], "lower": []}
            start = current_period - 1
            for i, mid in enumerate(mids):
                idx = start + i
                sigma = float(np.std(values[idx - current_period + 1:idx + 1]))
                out["middle"].append(_point(times[idx], mid))
                out["upper"].append(_point(times[idx], mid + std_dev * sigma))
                out["lower"].append(_point(times[idx], mid - std_dev * sigma))
            return {k: _clean_points(v) for k, v in out.items()}

        if indicator_type == "atr":
            return compute_atr(candles, current_period)

        if indicator_type == "keltner":
            mult = _float(params, "atr_multiplier", 2.0, 0.0)
            ema_points = _with_times(times, ema(values, current_period), current_period - 1)
            atr_points = compute_atr(candles, current_period)
            ema_map = {p["time"]: p["value"] for p in ema_points}
            out = {"upper": [], "middle": [], "lower": []}
            for row in atr_points:
                t = row["time"]
                if t in ema_map:
                    mid = ema_map[t]
                    out["middle"].append(_point(t, mid))
                    out["upper"].append(_point(t, mid + mult * row["value"]))
                    out["lower"].append(_point(t, mid - mult * row["value"]))
            return {k: _clean_points(v) for k, v in out.items()}

        if indicator_type == "donchian":
            out = {"upper": [], "middle": [], "lower": []}
            for i in range(current_period - 1, n):
                upper, lower = max(highs[i - current_period + 1:i + 1]), min(lows[i - current_period + 1:i + 1])
                out["upper"].append(_point(times[i], upper))
                out["middle"].append(_point(times[i], (upper + lower) / 2))
                out["lower"].append(_point(times[i], lower))
            return {k: _clean_points(v) for k, v in out.items()}

        if indicator_type == "obv":
            total, out = 0.0, [_point(times[0], 0.0)]
            for i in range(1, n):
                if closes[i] > closes[i - 1]:
                    total += volumes[i]
                elif closes[i] < closes[i - 1]:
                    total -= volumes[i]
                out.append(_point(times[i], total))
            return _clean_points(out)

        if indicator_type == "mfi":
            tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
            mf = [t * v for t, v in zip(tp, volumes)]
            out = []
            for i in range(current_period, n):
                pos = sum(mf[j] for j in range(i - current_period + 1, i + 1) if tp[j] > tp[j - 1])
                neg = sum(mf[j] for j in range(i - current_period + 1, i + 1) if tp[j] < tp[j - 1])
                mfr = pos / neg if neg else 100
                out.append(_point(times[i], 100 - (100 / (1 + mfr))))
            return _clean_points(out)

        if indicator_type == "cmf":
            mfv = [(((c - l) - (h - c)) / (h - l) if h != l else 0) * v for h, l, c, v in zip(highs, lows, closes, volumes)]
            out = []
            for i in range(current_period - 1, n):
                vol_sum = sum(volumes[i - current_period + 1:i + 1])
                out.append(_point(times[i], sum(mfv[i - current_period + 1:i + 1]) / vol_sum if vol_sum else 0))
            return _clean_points(out)

        if indicator_type == "wavetrend":
            n1, n2 = _int(params, "n1", 10), _int(params, "n2", 21)
            ap = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
            esa = ema(ap, n1)
            ap_aligned = ap[n1 - 1:]
            d_vals = ema([abs(ap_aligned[i] - esa[i]) for i in range(len(esa))], n1)
            esa_aligned = esa[n1 - 1:]
            ap_aligned2 = ap_aligned[n1 - 1:]
            ci = [(ap_aligned2[i] - esa_aligned[i]) / (0.015 * d_vals[i]) if d_vals[i] else 0 for i in range(min(len(d_vals), len(esa_aligned), len(ap_aligned2)))]
            wt1 = ema(ci, n2)
            wt2 = sma(wt1, 4)
            wt1_start = n - len(wt1)
            wt2_start = n - len(wt2)
            return {"wt1": _with_times(times, wt1, wt1_start), "wt2": _with_times(times, wt2, wt2_start)}

        if indicator_type == "tsi":
            long_p, short_p = _int(params, "long_period", 25), _int(params, "short_period", 13)
            delta = [closes[i] - closes[i - 1] for i in range(1, n)]
            e_num = ema(ema(delta, long_p), short_p)
            e_den = ema(ema([abs(d) for d in delta], long_p), short_p)
            vals = [100 * e_num[i] / e_den[i] if e_den[i] else 0 for i in range(min(len(e_num), len(e_den)))]
            return _with_times(times, vals, n - len(vals))

        if indicator_type == "cmo":
            delta = [closes[i] - closes[i - 1] for i in range(1, n)]
            out = []
            for i in range(current_period - 1, len(delta)):
                window = delta[i - current_period + 1:i + 1]
                su, sd = sum(d for d in window if d > 0), sum(-d for d in window if d < 0)
                out.append(_point(times[i + 1], 100 * (su - sd) / (su + sd) if (su + sd) else 0))
            return _clean_points(out)

        if indicator_type == "uo":
            p1, p2, p3 = _int(params, "period1", 7), _int(params, "period2", 14), _int(params, "period3", 28)
            bp, tr = [], []
            for i in range(1, n):
                bp.append(closes[i] - min(lows[i], closes[i - 1]))
                tr.append(max(highs[i], closes[i - 1]) - min(lows[i], closes[i - 1]))
            out = []
            for i in range(p3 - 1, len(bp)):
                def avg(p):
                    tr_sum = sum(tr[i - p + 1:i + 1])
                    return sum(bp[i - p + 1:i + 1]) / tr_sum if tr_sum else 0
                out.append(_point(times[i + 1], 100 * (4 * avg(p1) + 2 * avg(p2) + avg(p3)) / 7))
            return _clean_points(out)

        if indicator_type == "aroon":
            out = {"up": [], "down": []}
            for i in range(current_period, n):
                # window has period+1 bars; argmax index 0=oldest, period=newest
                h_window = highs[i - current_period:i + 1]
                l_window = lows[i - current_period:i + 1]
                hh_idx, ll_idx = int(np.argmax(h_window)), int(np.argmin(l_window))
                out["up"].append(_point(times[i], 100 * hh_idx / current_period))
                out["down"].append(_point(times[i], 100 * ll_idx / current_period))
            return {k: _clean_points(v) for k, v in out.items()}

        if indicator_type == "vortex":
            vm_plus, vm_minus, tr = [], [], []
            for i in range(1, n):
                vm_plus.append(abs(highs[i] - lows[i - 1]))
                vm_minus.append(abs(lows[i] - highs[i - 1]))
                tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
            out = {"plus": [], "minus": []}
            for i in range(current_period - 1, len(tr)):
                tr_sum = sum(tr[i - current_period + 1:i + 1])
                out["plus"].append(_point(times[i + 1], sum(vm_plus[i - current_period + 1:i + 1]) / tr_sum if tr_sum else 0))
                out["minus"].append(_point(times[i + 1], sum(vm_minus[i - current_period + 1:i + 1]) / tr_sum if tr_sum else 0))
            return {k: _clean_points(v) for k, v in out.items()}

        print(f"[INDICATOR] Unknown type: {indicator_type}")
        return []

    except Exception as e:
        print(f"[INDICATOR ERROR] {indicator_type}: {e}")
        return _empty_like((indicator_type or "").lower())


def compute_signal(indicator_type: str, data):
    try:
        indicator_type = (indicator_type or "").lower()
        if indicator_type == "rsi" and data:
            last = data[-1]["value"]
            return "overbought" if last > 70 else ("oversold" if last < 30 else "neutral")
        if indicator_type == "macd" and data.get("histogram"):
            h = data["histogram"]
            if len(h) >= 2 and h[-2]["value"] <= 0 < h[-1]["value"]:
                return "bullish_cross"
            if len(h) >= 2 and h[-2]["value"] >= 0 > h[-1]["value"]:
                return "bearish_cross"
            return "neutral"
        if indicator_type in ("stoch", "stochrsi") and data.get("k") and data.get("d"):
            k, d = data["k"][-1]["value"], data["d"][-1]["value"]
            return "buy" if k > d and k < 20 else ("sell" if k < d and k > 80 else "neutral")
        if indicator_type == "adx" and data.get("adx"):
            last = data["adx"][-1]["value"]
            return "strong_trend" if last > 25 else ("ranging" if last < 20 else "neutral")
        if indicator_type == "supertrend" and data:
            if len(data) >= 2:
                prev_dir = data[-2].get("dir")
                curr_dir = data[-1].get("dir")
                if prev_dir == -1 and curr_dir == 1:
                    return "buy_signal"
                if prev_dir == 1 and curr_dir == -1:
                    return "sell_signal"
            return "bullish" if data[-1].get("dir") == 1 else "bearish"
        if indicator_type == "bollinger" and all(data.get(k) for k in ("upper", "middle", "lower")):
            upper, middle, lower = data["upper"][-1]["value"], data["middle"][-1]["value"], data["lower"][-1]["value"]
            bandwidth = ((upper - lower) / middle * 100) if middle else 0
            threshold = 5
            if len(data["upper"]) >= 2 and middle:
                prev_width = (data["upper"][-2]["value"] - data["lower"][-2]["value"]) / data["middle"][-2]["value"] * 100 if data["middle"][-2]["value"] else bandwidth
                return "squeeze" if bandwidth < threshold else ("expanding" if bandwidth > prev_width else "normal")
            return "squeeze" if bandwidth < threshold else "normal"
        if indicator_type == "cci" and data:
            last = data[-1]["value"]
            return "overbought" if last > 100 else ("oversold" if last < -100 else "neutral")
        if indicator_type == "williamsr" and data:
            last = data[-1]["value"]
            return "overbought" if last > -20 else ("oversold" if last < -80 else "neutral")
        if indicator_type == "wavetrend" and data.get("wt1") and data.get("wt2"):
            wt1, wt2 = data["wt1"], data["wt2"]
            if len(wt1) >= 2 and len(wt2) >= 2:
                if wt1[-2]["value"] <= wt2[-2]["value"] and wt1[-1]["value"] > wt2[-1]["value"] and wt2[-1]["value"] <= -60:
                    return "buy"
                if wt1[-2]["value"] >= wt2[-2]["value"] and wt1[-1]["value"] < wt2[-1]["value"] and wt2[-1]["value"] >= 60:
                    return "sell"
            return "neutral"
        if indicator_type == "roc" and data:
            last = data[-1]["value"]
            return "bullish" if last > 0 else ("bearish" if last < 0 else "neutral")
        if indicator_type == "momentum" and data:
            last = data[-1]["value"]
            return "bullish" if last > 0 else ("bearish" if last < 0 else "neutral")
        if indicator_type == "mfi" and data:
            last = data[-1]["value"]
            return "overbought" if last > 80 else ("oversold" if last < 20 else "neutral")
        if indicator_type == "cmf" and data:
            last = data[-1]["value"]
            return "buying" if last > 0.25 else ("selling" if last < -0.25 else "neutral")
        if indicator_type == "obv" and data:
            if len(data) >= 5:
                trend = data[-1]["value"] - data[-5]["value"]
                return "accumulation" if trend > 0 else ("distribution" if trend < 0 else "neutral")
            return "neutral"
        if indicator_type == "tsi" and data:
            if len(data) >= 2:
                if data[-2]["value"] < 0 < data[-1]["value"]:
                    return "bullish_cross"
                if data[-2]["value"] > 0 > data[-1]["value"]:
                    return "bearish_cross"
            last = data[-1]["value"]
            return "bullish" if last > 0 else "bearish"
        if indicator_type == "cmo" and data:
            last = data[-1]["value"]
            return "overbought" if last > 50 else ("oversold" if last < -50 else "neutral")
        if indicator_type == "aroon" and data.get("up") and data.get("down"):
            up = data["up"][-1]["value"]
            down = data["down"][-1]["value"]
            if up > 70 and down < 30:
                return "bullish"
            if down > 70 and up < 30:
                return "bearish"
            return "neutral"
        if indicator_type == "vortex" and data.get("plus") and data.get("minus"):
            plus = data["plus"][-1]["value"]
            minus = data["minus"][-1]["value"]
            return "bullish" if plus > minus else "bearish"
        if indicator_type == "ichimoku" and data.get("spanA") and data.get("spanB"):
            a = data["spanA"][-1]["value"]
            b = data["spanB"][-1]["value"]
            return "bullish_cloud" if a > b else "bearish_cloud"
        if indicator_type == "ao" and data:
            if len(data) >= 2:
                if data[-2]["value"] < 0 < data[-1]["value"]:
                    return "bullish_cross"
                if data[-2]["value"] > 0 > data[-1]["value"]:
                    return "bearish_cross"
            return "bullish" if data[-1]["value"] > 0 else "bearish"
    except Exception:
        pass
    return "neutral"


def compute_sr_levels(candles: list, interval: str = "1d", **params) -> Dict:
    try:
        if len(candles or []) > 5000:
            candles = candles[-5000:]
        candles = _sanitize_candles(candles)
        tf_config = {
            "1m": {"N": 2, "lookback": 500, "lim": 5, "atr_mult": 0.5, "ef": 9, "es": 21},
            "5m": {"N": 3, "lookback": 500, "lim": 6, "atr_mult": 0.8, "ef": 9, "es": 21},
            "15m": {"N": 3, "lookback": 500, "lim": 6, "atr_mult": 1.0, "ef": 20, "es": 50},
            "30m": {"N": 4, "lookback": 400, "lim": 7, "atr_mult": 1.2, "ef": 20, "es": 50},
            "1h": {"N": 4, "lookback": 400, "lim": 8, "atr_mult": 1.5, "ef": 20, "es": 50},
            "2h": {"N": 5, "lookback": 300, "lim": 7, "atr_mult": 1.5, "ef": 20, "es": 50},
            "4h": {"N": 5, "lookback": 300, "lim": 8, "atr_mult": 2.0, "ef": 21, "es": 55},
            "1d": {"N": 6, "lookback": 200, "lim": 8, "atr_mult": 2.0, "ef": 21, "es": 55},
            "1w": {"N": 6, "lookback": 100, "lim": 6, "atr_mult": 2.5, "ef": 21, "es": 55},
        }
        cfg = dict(tf_config.get(interval, {"N": 4, "lookback": 400, "lim": 7, "atr_mult": 1.5, "ef": 20, "es": 50}))
        lookback_override = _int(params, "lookback", cfg["lookback"], minimum=50)
        limit_override = _int(params, "limit", cfg["lim"], minimum=1)
        cfg["lookback"] = min(lookback_override, len(candles))
        cfg["lim"] = min(limit_override, 20)
        candles = candles[-cfg["lookback"]:]
        if len(candles) < cfg["N"] * 2 + 2:
            return dict(EMPTY_SR)
        highs, lows, closes = _extract_highs(candles), _extract_lows(candles), _extract_closes(candles)
        times, volumes = _extract_times(candles), _extract_volumes(candles)
        n, current_price = len(candles), closes[-1]
        atr_data = compute_atr(candles, 14)
        atr_val = atr_data[-1]["value"] if atr_data else max(current_price * 0.005, 0.01)
        cluster_threshold = atr_val * 0.5
        tolerance = atr_val * 0.25
        mean_vol = float(np.mean(volumes)) if volumes else 1.0
        mean_vol = mean_vol if mean_vol > 0 else 1.0

        def pivot_strength_high(i, price):
            return sum(1 for j in range(i - cfg["N"], i) if highs[j] < price) + sum(1 for j in range(i + 1, i + cfg["N"] + 1) if highs[j] < price)

        def pivot_strength_low(i, price):
            return sum(1 for j in range(i - cfg["N"], i) if lows[j] > price) + sum(1 for j in range(i + 1, i + cfg["N"] + 1) if lows[j] > price)

        pivots = []
        for i in range(cfg["N"], n - cfg["N"]):
            if highs[i] == max(highs[i - cfg["N"]:i + cfg["N"] + 1]):
                pivots.append({"price": highs[i], "index": i, "strength_raw": pivot_strength_high(i, highs[i]), "vol_score": volumes[i] / mean_vol})
            if lows[i] == min(lows[i - cfg["N"]:i + cfg["N"] + 1]):
                pivots.append({"price": lows[i], "index": i, "strength_raw": pivot_strength_low(i, lows[i]), "vol_score": volumes[i] / mean_vol})

        pivots.sort(key=lambda x: x["price"])
        clusters = []
        for p in pivots:
            if not clusters or abs(p["price"] - clusters[-1][-1]["price"]) > cluster_threshold:
                clusters.append([p])
            else:
                clusters[-1].append(p)

        levels = []
        for cluster in clusters:
            weights = [max(c["strength_raw"], 1) * max(c["vol_score"], 0.01) for c in cluster]
            price = sum(c["price"] * w for c, w in zip(cluster, weights)) / sum(weights)
            touches, in_zone = 0, False
            for h, l in zip(highs, lows):
                if l <= price + tolerance and h >= price - tolerance:
                    if not in_zone:
                        touches += 1
                    in_zone = True
                else:
                    in_zone = False
            levels.append({
                "price": price,
                "touches": touches,
                "max_strength": max(c["strength_raw"] for c in cluster),
                "max_vol": max(c["vol_score"] for c in cluster),
                "last_index": max(c["index"] for c in cluster),
            })

        max_touches = max([l["touches"] for l in levels] or [1])
        max_strength = max([l["max_strength"] for l in levels] or [1])
        selected = []
        for lvl in levels:
            price = lvl["price"]
            n_touches = lvl["touches"] / max_touches if max_touches else 0
            n_pivot = lvl["max_strength"] / max_strength if max_strength else 0
            n_recency = lvl["last_index"] / n
            n_vol = min(lvl["max_vol"] / 2, 1.0)
            str_score = n_touches * 40 + n_pivot * 25 + n_recency * 15 + n_vol * 20
            proximity = 1 - min(abs(price - current_price) / (current_price * 0.1), 1.0)
            lvl["final_score"] = str_score * 0.90 + proximity * 10
        levels.sort(key=lambda x: x["final_score"], reverse=True)
        for lvl in levels:
            if len(selected) >= cfg["lim"]:
                break
            if all(abs(lvl["price"] - existing["price"]) >= atr_val * cfg["atr_mult"] for existing in selected):
                selected.append(lvl)

        def finalize(rows):
            rows.sort(key=lambda x: x["final_score"], reverse=True)
            out = []
            for i, row in enumerate(rows):
                strength = 3 if i == 0 else (2 if i < 3 else 1)
                price = _safe_round(row["price"])
                if price is None:
                    continue
                step = 100 if price >= 1000 else 10
                out.append({
                    "price": price,
                    "zone_high": _safe_round(row["price"] + tolerance),
                    "zone_low": _safe_round(row["price"] - tolerance),
                    "strength": strength,
                    "touches": int(row["touches"]),
                    "final_score": _safe_round(row["final_score"]),
                    "round_number": abs(row["price"] % step) < tolerance,
                    "is_key_level": strength == 3,
                })
            return out

        support = finalize([l for l in selected if l["price"] < current_price])
        resistance = finalize([l for l in selected if l["price"] > current_price])

        order_blocks = []
        for i in range(max(1, n - 20), n - 1):
            if closes[i] < lows[i - 1] and closes[i + 1] > highs[i]:
                order_blocks.append({"type": "bullish", "top": _safe_round(highs[i]), "bottom": _safe_round(lows[i]), "price": _safe_round(highs[i]), "name": "DEMAND"})
            elif closes[i] > highs[i - 1] and closes[i + 1] < lows[i]:
                order_blocks.append({"type": "bearish", "top": _safe_round(highs[i]), "bottom": _safe_round(lows[i]), "price": _safe_round(lows[i]), "name": "SUPPLY"})

        fast, slow = ema(closes, cfg["ef"]), ema(closes, cfg["es"])
        common = min(len(fast), len(slow))
        fast, slow = fast[-common:], slow[-common:]
        start = n - common
        dynamic_zone = {
            "ema_fast": _with_times(times, fast[-500:], max(start, n - len(fast[-500:]))),
            "ema_slow": _with_times(times, slow[-500:], max(start, n - len(slow[-500:]))),
            "timeframe_label": f"EMA({cfg['ef']})/EMA({cfg['es']})",
        }
        return {
            "support": support,
            "resistance": resistance,
            "order_blocks": order_blocks[-2:],
            "dynamic_zone": dynamic_zone,
            "timestamp": int(times[-1]),
            "current_price": _safe_round(current_price),
        }
    except Exception as e:
        print(f"[SR ERROR]: {e}\n{traceback.format_exc()}")
        return dict(EMPTY_SR)
