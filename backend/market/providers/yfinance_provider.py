"""yfinance adapter ('yfinance') — unofficial, ~15-min delayed for NSE/BSE.

Role per the approved plan: historical candles and a delayed fallback quote
source. Unofficial: every payload is labelled delayed; no redistribution
licence exists (docs/DATA_SOURCES.md). Missing data is None, never 0.
"""
from __future__ import annotations

import logging
import math
import threading
from datetime import date, datetime, timedelta, timezone

from ..timeutil import iso_utc, ist_now
from .base import Candle, Instrument, Quote

logger = logging.getLogger(__name__)

# Interval -> (max period, default period) using yfinance period strings.
PERIOD_FOR_RANGE = {
    "1d": "1mo", "1w": "6mo", "1M": "max", "3M": "2y", "6M": "5y", "1Y": "5y", "5Y": "max",
}

INTERVAL_YF = {"1d": "1d", "1w": "1wk", "1M": "1mo"}

IST = timezone(timedelta(hours=5, minutes=30))


def _finite(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


class YfinanceProvider:
    name = "yfinance"
    capabilities = frozenset({"quotes", "index_quotes", "historical"})

    def __init__(self, rate_per_sec: float = 1.0, daily_budget: int = 20000):
        try:
            import yfinance as yf
        except ImportError:
            raise RuntimeError("yfinance package required for YfinanceProvider")
        self._yf = yf
        self.client = None  # yfinance manages its own HTTP; budget noted in status
        self._rate = rate_per_sec
        self._budget = daily_budget
        self._lock = threading.Lock()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def status(self) -> dict:
        return {
            "name": self.name,
            "delayed": True,
            "note": "unofficial source; ~15 min delayed for NSE/BSE",
        }

    # ── quotes (delayed fallback) ──
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        symbols = [s for s in symbols if s]
        if not symbols:
            return out
        try:
            tickers = self._yf.Tickers(" ".join(symbols))
            for symbol in symbols:
                try:
                    fast = tickers.tickers[symbol].fast_info
                    last = _finite(fast.get("last_price"))
                    prev = _finite(fast.get("previous_close"))
                except Exception:  # noqa: BLE001 — per-symbol failure must not kill the batch
                    last, prev = None, None
                if last is None:
                    continue
                change = (last - prev) if prev else None
                out[symbol] = Quote(
                    symbol=symbol,
                    last=last,
                    prev_close=prev,
                    change=change,
                    change_pct=(change / prev * 100.0) if (change is not None and prev) else None,
                    as_of=ist_now().isoformat(),
                    provider=self.name,
                    price_kind="ltp",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance batch quotes failed: %s", exc)
        return out

    def get_index_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return self.get_quotes(symbols)

    # ── history ──
    def get_historical_candles(self, symbol: str, interval: str,
                               start: date, end: date) -> list[Candle]:
        yf_interval = INTERVAL_YF.get(interval)
        if yf_interval is None:
            return []
        try:
            hist = self._yf.Ticker(symbol).history(
                start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                interval=yf_interval, auto_adjust=False, progress=False, timeout=20,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance history failed for %s: %s", symbol, exc)
            return []
        if hist is None or hist.empty:
            return []

        candles = []
        is_daily_like = interval in ("1d", "1w", "1M")
        for idx, row in hist.iterrows():
            o = _finite(row.get("Open")); h = _finite(row.get("High"))
            l = _finite(row.get("Low")); c = _finite(row.get("Close"))
            if o is None or h is None or l is None or c is None:
                continue
            ts = int(idx.timestamp())
            if is_daily_like:
                # Align IST session date to UTC midnight (same rule as legacy code).
                dt = datetime.fromtimestamp(ts + 19800, timezone.utc)
                if interval == "1d":
                    ts = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())
                elif interval == "1w":
                    monday = dt - timedelta(days=dt.weekday())
                    ts = int(datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp())
                elif interval == "1M":
                    ts = int(datetime(dt.year, dt.month, 1, tzinfo=timezone.utc).timestamp())
            candles.append(Candle(
                time=ts, open=o, high=h, low=l, close=c,
                volume=_finite(row.get("Volume")),
            ))
        return candles

    def get_instrument_master(self, exchange: str) -> list[Instrument]:
        return []

    def get_market_status(self) -> None:
        return None
