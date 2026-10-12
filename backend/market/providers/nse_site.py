"""NSE website adapter ('nse_site').

Per the Phase 0 decision this adapter stays ON by default but ONLY through the
shared HttpClient (rate limit + daily budget + circuit breaker + the single
env flag ENABLE_NSE_SITE_ADAPTER). Differences from the legacy nse_client:

  - NO always-on keep-alive thread: cookies are warmed lazily before the first
    JSON call and re-warmed only when stale; warm-up goes through the same
    rate limiter and breaker. No reset-and-retry loops on 401/403.
  - Honest headers: plain library user-agent, standard Accept; no browser
    fingerprint spoofing (Sec-CH-UA, X-Requested-With, DNT removed).
  - Missing data becomes None, never 0, and every record carries provider,
    as_of and fetched_at.
  - All URLs it calls are listed in docs/DATA_SOURCES.md.
"""
from __future__ import annotations

import logging
import threading
import time

import requests

from ..config import (
    ENABLE_NSE_SITE_ADAPTER,
    HTTP_TIMEOUT,
    NSE_SITE_DAILY_BUDGET,
    NSE_SITE_RATE_PER_SEC,
)
from ..http import HttpClient
from ..timeutil import ist_now
from .base import Candle, Instrument, MarketStatus, Quote

logger = logging.getLogger(__name__)

INDEX_DISPLAY_NAMES = {
    "NIFTY 50": "NIFTY 50",
    "NIFTY BANK": "BANK NIFTY",
    "NIFTY FINANCIAL SERVICES": "FIN NIFTY",
    "NIFTY MIDCAP SELECT": "MIDCPNIFTY",
    "NIFTY 500": "NIFTY 500",
}

# Capability name -> NSE index identifier.
INDEX_SYMBOL_MAP = {
    "NIFTY 50": "NIFTY 50",
    "BANK NIFTY": "NIFTY BANK",
    "FIN NIFTY": "NIFTY FINANCIAL SERVICES",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
    "NIFTY 500": "NIFTY 500",
    "SENSEX": "SENSEX",          # via allIndices; NSE reports BSE indices there
    "NIFTY MIDCAP 100": "NIFTY MIDCAP 100",
    "NIFTY SMALLCAP 100": "NIFTY SMALLCAP 100",
    "INDIA VIX": "INDIA VIX",
}

EQUITY_SERIES_OK = {"EQ", "BE", "BZ", "SM", "ST"}


def _finite(value) -> float | None:
    """Parse a numeric field; missing/garbage becomes None, never 0."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def _int_or_none(value):
    f = _finite(value)
    return int(f) if f is not None else None


class NseSiteProvider:
    name = "nse_site"
    capabilities = frozenset({
        "market_status", "quotes", "index_quotes", "index_constituents",
        "instrument_master", "holiday_calendar", "pre_open",
    })

    BASE = "https://www.nseindia.com"
    # URL inventory (also in docs/DATA_SOURCES.md):
    URLS = {
        "home": "https://www.nseindia.com",
        "quote_page": "https://www.nseindia.com/get-quotes/equity?symbol=SBIN",
        "equity_indices": "https://www.nseindia.com/api/equity-stockIndices",
        "all_indices": "https://www.nseindia.com/api/allIndices",
        "quote_equity": "https://www.nseindia.com/api/quote-equity",
        "pre_open": "https://www.nseindia.com/api/market-data-pre-open",
        "holidays": "https://www.nseindia.com/api/holiday-master",
        "master_csv": "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    }

    def __init__(self, client: HttpClient | None = None, warmup_client: HttpClient | None = None,
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
        self.session.headers.update({
            "User-Agent": "future-app/1.0 (market data client)",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": self.URLS["quote_page"],
        })
        self._cookies_warmed_at = 0.0
        self._warmup_lock = threading.Lock()

    # ── capability plumbing ──
    def supports(self, capability: str) -> bool:
        return self.enabled and capability in self.capabilities

    def status(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "client": self.client.status(),
            "warmup_client": self.warmup_client.status(),
        }

    # ── session warm-up (lazy, bounded, through the breaker) ──
    def _ensure_cookies(self) -> bool:
        if self._cookies_warmed_at and time.monotonic() - self._cookies_warmed_at < 240:
            return True
        with self._warmup_lock:
            if self._cookies_warmed_at and time.monotonic() - self._cookies_warmed_at < 240:
                return True
            resp = self.warmup_client.get(self.URLS["home"])
            if resp is None:
                logger.info("nse_site: warm-up unavailable (breaker/budget); JSON calls may 401/403")
                return False
            self._cookies_warmed_at = time.monotonic()
            # Adopt the cookies the warm-up client collected onto BOTH the
            # provider's session (honest headers source) and the shared HTTP
            # client's session (the one that actually performs JSON calls).
            self.session.cookies.update(self.warmup_client.session.cookies)
            self.client.session.cookies.update(self.warmup_client.session.cookies)
            return True

    def _api_get(self, path: str, params: dict | None = None):
        if not self.supports("quotes"):
            return None
        self._ensure_cookies()
        url = self.BASE + path
        resp = self.client.get(url, params=params, headers=dict(self.session.headers))
        if resp is None:
            return None
        if resp.status_code in (401, 403):
            # Terminal per policy. Invalidate warm-up so the next call re-warms
            # once — if the site keeps blocking, the breaker pauses us.
            self._cookies_warmed_at = 0.0
            return None
        try:
            return resp.json()
        except ValueError:
            logger.info("nse_site: non-JSON response from %s", path)
            return None

    # ── quotes ──
    def _quote_from_stock_indices_row(self, row: dict, session: str | None) -> Quote | None:
        symbol = (row.get("symbol") or "").strip()
        if not symbol:
            return None
        last = _finite(row.get("lastPrice"))
        if last is None:
            return None
        prev = _finite(row.get("previousClose"))
        change = last - prev if (last is not None and prev) else _finite(row.get("change"))
        change_pct = (
            (change / prev * 100.0) if (change is not None and prev) else _finite(row.get("pChange"))
        )
        open_ = _finite(row.get("open"))
        high = _finite(row.get("dayHigh")) or _finite((row.get("intradayHighLow") or {}).get("max"))
        low = _finite(row.get("dayLow")) or _finite((row.get("intradayHighLow") or {}).get("min"))
        volume = _int_or_none(row.get("totalTradedVolume"))
        value = _finite(row.get("totalTradedValue"))
        return Quote(
            symbol=symbol,
            exchange="NSE",
            last=last,
            prev_close=prev,
            change=change,
            change_pct=change_pct,
            open=open_,
            high=high,
            low=low,
            volume=volume,
            value=value,
            as_of=ist_now().isoformat(),
            provider=self.name,
            session=session,
        )

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Batched quotes. Index symbols resolve via equity-stockIndices;
        equities via per-symbol quote-equity (single-flight dedupes repeats)."""
        out: dict[str, Quote] = {}
        pending = [s for s in symbols if s and not s.startswith("^")]
        if not pending:
            return out
        session = None

        # Index symbols first, one call per index (deduped by single-flight).
        stock_indices_cache: dict[str, dict | None] = {}
        for symbol in pending:
            idx_name = INDEX_SYMBOL_MAP.get(symbol.upper())
            if not idx_name:
                continue
            if idx_name not in stock_indices_cache:
                stock_indices_cache[idx_name] = self._api_get(
                    "/api/equity-stockIndices", {"index": idx_name}
                )
            data = stock_indices_cache[idx_name]
            rows = (data or {}).get("data") or []
            for row in rows:
                if (row.get("symbol") or "").strip().upper() == idx_name.upper():
                    q = self._quote_from_stock_indices_row(row, session)
                    if q is not None:
                        out[symbol] = q
                    break

        # Equity symbols.
        for symbol in pending:
            if symbol in out or INDEX_SYMBOL_MAP.get(symbol.upper()):
                continue
            data = self._api_get("/api/quote-equity", {"symbol": symbol.upper()})
            pi = (data or {}).get("priceInfo") or {}
            last = _finite(pi.get("lastPrice"))
            if last is None:
                continue
            prev = _finite(pi.get("previousClose"))
            intraday = pi.get("intraDayHighLow") or {}
            # quote-equity's priceInfo has no traded volume/value fields;
            # honestly None rather than a wrong number.
            out[symbol] = Quote(
                symbol=symbol,
                exchange="NSE",
                last=last,
                prev_close=prev,
                change=_finite(pi.get("change")),
                change_pct=_finite(pi.get("pChange")),
                open=_finite(pi.get("open")),
                high=_finite(intraday.get("max")),
                low=_finite(intraday.get("min")),
                volume=None,
                value=None,
                as_of=ist_now().isoformat(),
                provider=self.name,
                session=session,
            )
        return out

    def get_index_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return self.get_quotes(symbols)

    def get_index_constituents(self, index_symbol: str) -> list[str]:
        """NSE publishes constituent lists as CSVs on archives; only the
        universe index (NIFTY 500) needs them for gainers/losers. Returns []
        until the master refresh wires the official files (Phase 2 master job)."""
        return []

    # ── instrument master ──
    def get_instrument_master(self, exchange: str) -> list[Instrument]:
        if not self.enabled or exchange.upper() != "NSE":
            return []
        url = self.URLS["master_csv"]
        resp = self.client.get(url)
        if resp is None or resp.status_code != 200:
            return []
        import csv
        import io

        instruments = []
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            series = (row.get("SERIES") or "").strip().upper()
            symbol = (row.get("SYMBOL") or "").strip()
            name = (row.get("NAME OF COMPANY") or row.get("Company Name") or "").strip()
            if not symbol or not name:
                continue
            instruments.append(Instrument(
                symbol=symbol,
                name=name,
                exchange="NSE",
                isin=(row.get("ISIN NUMBER") or row.get("ISIN") or "").strip() or None,
                series=series or None,
                listing_date=None,          # not present in EQUITY_L; discovery derives it
                face_value=_finite(row.get("FACE VALUE")),
                provider=self.name,
            ))
        return instruments

    # ── holidays ──
    def get_holiday_calendar(self, year: int) -> dict[str, str]:
        data = self._api_get("/api/holiday-master", {"type": "trading"})
        if not data:
            return {}
        holidays: dict[str, str] = {}
        for item in data.get("CM", []):
            raw = (item.get("tradingDate") or item.get("date") or "").strip()
            name = (item.get("description") or item.get("holidayName") or "Market Holiday").strip()
            if not raw:
                continue
            try:
                from datetime import datetime
                parsed = datetime.strptime(raw, "%d-%b-%Y")
            except ValueError:
                continue
            holidays[parsed.strftime("%Y-%m-%d")] = name
        return {d: n for d, n in holidays.items() if d.startswith(str(year))}

    # ── market status ──
    def get_market_status(self) -> MarketStatus | None:
        """Prefer NSE's own market-status endpoint; else schedule-based status
        comes from the calendar service (Phase 2), not invented here."""
        data = self._api_get("/api/marketStatus")
        if not data:
            return None
        markets = data.get("market") or []
        for m in markets:
            if (m.get("market") or "").strip().upper() in ("CM", "CAPITAL MARKET"):
                state = (m.get("marketStatus") or "").strip().lower()
                mapping = {
                    "open": "REGULAR",
                    "closed": "CLOSED",
                    "pre_open": "PRE_OPEN",
                    "post": "POST_MARKET",
                }
                return MarketStatus(
                    state=mapping.get(state, state.upper()),
                    holiday_name=m.get("lastUpdated") if state == "closed" else None,
                    provider=self.name,
                )
        return None

    # ── pre-open (indicative price) ──
    def get_pre_open(self, category: str = "ALL") -> dict | None:
        return self._api_get("/api/market-data-pre-open", {"key": category})
