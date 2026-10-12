"""NSE website access for legacy services — now a facade over the shared,
rate-limited HTTP layer (market.http.HttpClient).

Compliance notes (Phase 0 approval):
  - NO keep-alive thread. Cookies warm lazily on first use, through the same
    rate limiter and breaker as everything else.
  - NO browser fingerprint spoofing. Honest UA 'future-app/1.0'.
  - 401/403 are terminal for the call; the shared client's circuit breaker
    pauses traffic after repeated failures. There is no reset-and-retry loop.
  - ENABLE_NSE_SITE_ADAPTER=0 disables every outbound NSE call made here.
  - All URLs are inventoried in docs/DATA_SOURCES.md.
"""
from __future__ import annotations

import logging
import threading
import time

import requests

from market.config import (
    ENABLE_NSE_SITE_ADAPTER,
    HTTP_TIMEOUT,
    NSE_SITE_DAILY_BUDGET,
    NSE_SITE_RATE_PER_SEC,
)
from market.http import HttpClient

logger = logging.getLogger(__name__)

BASE_URL = "https://www.nseindia.com"
QUOTE_PAGE_URL = "https://www.nseindia.com/get-quotes/equity?symbol=SBIN"

# Honest headers — no Sec-CH-UA / X-Requested-With / DNT fingerprint set.
HEADERS = {
    "User-Agent": "future-app/1.0 (market data client)",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": QUOTE_PAGE_URL,
}

INDEX_MAP = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FINANCIAL SERVICES",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
    "SENSEX": "SENSEX",
    "BANKEX": "BANKEX",
}


def _now() -> float:
    return time.time()


def _finite(value) -> float | None:
    """Parse a numeric field; missing/garbage becomes None, never 0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> int | None:
    f = _finite(value)
    return int(f) if f is not None else None


def _quote_from_indices_row(item: dict) -> dict | None:
    last = _finite(item.get("lastPrice") or item.get("last"))
    if last is None:
        return None
    prev = _finite(item.get("previousClose"))
    change = _finite(item.get("change"))
    pct = _finite(item.get("pChange") or item.get("percentChange"))
    if change is None and prev:
        change = last - prev
    if pct is None and prev and change is not None:
        pct = (change / prev) * 100.0
    high = (_finite(item.get("dayHigh")) or _finite(item.get("high"))
            or _finite((item.get("intradayHighLow") or {}).get("max")))
    low = (_finite(item.get("dayLow")) or _finite(item.get("low"))
           or _finite((item.get("intradayHighLow") or {}).get("min")))
    return {
        "lastPrice": last,
        "change": change,
        "pChange": pct,
        "open": _finite(item.get("open")),
        "high": high,
        "low": low,
        "close": last,
        "previousClose": prev,
    }


class NSEClient:
    """Legacy call surface; every outbound call goes through HttpClient."""

    BASE_URL = BASE_URL
    # Legacy attribute: option_chain_service sends raw requests with these.
    HEADERS = HEADERS

    def __init__(self, client: HttpClient | None = None,
                 warmup_client: HttpClient | None = None,
                 enabled: bool | None = None):
        self.enabled = ENABLE_NSE_SITE_ADAPTER if enabled is None else enabled
        self.client = client or HttpClient(
            name="nse_site",
            rate_per_sec=NSE_SITE_RATE_PER_SEC,
            daily_budget=NSE_SITE_DAILY_BUDGET,
            timeout=HTTP_TIMEOUT,
        )
        # Warm-up requests go through their own limiter so they can never
        # bypass the budget or the breaker.
        self.warmup_client = warmup_client or HttpClient(
            name="nse_site_warmup",
            rate_per_sec=max(NSE_SITE_RATE_PER_SEC, 1.0) / 2,
            daily_budget=max(NSE_SITE_DAILY_BUDGET // 10, 200),
            timeout=HTTP_TIMEOUT,
        )
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._cookies_warmed_at = 0.0
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._error_count = 0

    # ── cache (legacy shape, thread-safe) ──
    def _cache_get(self, key, ttl=3):
        with self._cache_lock:
            item = self._cache.get(key)
            if item and _now() - item["ts"] <= ttl:
                return item["value"]
        return None

    def _cache_set(self, key, value):
        if value is not None:
            with self._cache_lock:
                self._cache[key] = {"ts": _now(), "value": value}
        return value

    # ── session warm-up (lazy, shared breaker/budget) ──
    def _refresh_session(self) -> bool:
        """Warm-up: visit the home page once to collect session cookies.
        Goes through the warm-up client's limiter/breaker/budget. Failure is
        not retried in a loop; the next caller re-attempts after backoff."""
        if not self.enabled:
            return False
        resp = self.warmup_client.get(BASE_URL)
        if resp is None:
            logger.info("nse warm-up unavailable (breaker/budget/disabled)")
            return False
        self.session.cookies.update(self.warmup_client.session.cookies)
        self.client.session.cookies.update(self.warmup_client.session.cookies)
        self._cookies_warmed_at = time.monotonic()
        return True

    def _ensure_session(self) -> bool:
        if not self.enabled:
            return False
        if self._cookies_warmed_at and time.monotonic() - self._cookies_warmed_at < 240:
            return True
        return self._refresh_session()

    # ── core GET through the shared client ──
    def get(self, path: str, params: dict = None, referer_type: str = "home",
            cache_ttl: float = 15) -> dict | None:
        if not self.enabled:
            return None
        url = f"{BASE_URL}{path}"
        if cache_ttl < 2:
            params = dict(params or {})
            params["_"] = int(_now() * 1000)
        cache_key = (path, tuple(sorted((params or {}).items())))
        cached = self._cache_get(cache_key, ttl=cache_ttl)
        if cached is not None:
            return cached
        self._ensure_session()
        resp = self.client.get(url, params=params, headers=dict(HEADERS))
        if resp is None:
            self._error_count += 1
            return None
        if resp.status_code in (401, 403):
            # Terminal per policy: invalidate the warm-up stamp only; the
            # shared client's breaker decides when traffic resumes. No
            # reset-and-retry loop.
            self._cookies_warmed_at = 0.0
            self._error_count += 1
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.info("nse_client: non-JSON 200 from %s", path)
            self._error_count += 1
            return None
        self._error_count = 0
        return self._cache_set(cache_key, data)

    # ── quotes (legacy dict shape preserved; missing fields are None) ──
    def get_quote(self, symbol: str) -> dict | None:
        sym = (symbol or "").upper().replace(".NS", "")
        if sym in INDEX_MAP:
            target = INDEX_MAP[sym]
            data = self.get("/api/equity-stockIndices",
                            params={"index": target}, cache_ttl=2)
            if data and "data" in data:
                for item in data["data"]:
                    if (item.get("symbol") or "").upper() == target or \
                       (item.get("index") or "").upper() == target:
                        return _quote_from_indices_row(item)
            data = self.get("/api/allIndices", cache_ttl=5)
            if data and "data" in data:
                for item in data["data"]:
                    if (item.get("index") or "").upper() == target:
                        return _quote_from_indices_row(item)
            return None
        data = self.get("/api/quote-equity", params={"symbol": sym})
        if not data or "priceInfo" not in data:
            return None
        pi = data["priceInfo"]
        last = _finite(pi.get("lastPrice"))
        if last is None:
            return None
        intraday = pi.get("intraDayHighLow") or {}
        return {
            "lastPrice": last,
            "change": _finite(pi.get("change")),
            "pChange": _finite(pi.get("pChange")),
            "open": _finite(pi.get("open")),
            "high": _finite(intraday.get("max")),
            "low": _finite(intraday.get("min")),
            "close": _finite(pi.get("close")) or last,
            "previousClose": _finite(pi.get("previousClose")),
        }

    def get_premarket_price(self, symbol: str) -> dict | None:
        data = self.get("/api/market-data-pre-open", params={"key": "ALL"},
                        cache_ttl=15)
        if not data or "data" not in data:
            return None
        sym = (symbol or "").upper().replace(".NS", "")
        for item in data["data"]:
            meta = item.get("metadata") or {}
            if (meta.get("symbol") or "").upper() == sym:
                pre = (item.get("detail") or {}).get("preOpenMarket") or {}
                iep = pre.get("IEP") or pre.get("finalPrice")
                if iep:
                    return {
                        "iep": _finite(iep),
                        "perChange": _finite(pre.get("perChange")),
                        "prevClose": _finite(pre.get("previousClose")),
                        "totalVolume": _int_or_none(pre.get("total")),
                    }
                return None
        return None

    def get_postmarket_price(self, symbol: str) -> dict | None:
        q = self.get_quote(symbol)
        if not q:
            return None
        close = q.get("close")
        if close is None:
            return None
        return {"closePrice": close, "pChange": q.get("pChange")}

    # ── status (surfaced by /api/market/health) ──
    def status(self) -> dict:
        return {
            "name": "nse_legacy_facade",
            "enabled": self.enabled,
            "client": self.client.status(),
            "warmup": self.warmup_client.status(),
        }


nse_client = NSEClient()
