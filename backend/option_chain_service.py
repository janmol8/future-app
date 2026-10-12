import json
import math
import os
import time
import logging
import threading
import csv
import gzip
import io
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
import stock_provider
from market.config import OC_REFRESH_SECONDS, OC_CLOSED_REFRESH_SECONDS

logger = logging.getLogger(__name__)

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _parse_expiry_date(expiry: str) -> Optional[datetime]:
    if not expiry:
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry, fmt)
        except ValueError:
            continue
    return None

def time_to_expiry_years(expiry: str) -> float:
    expiry_dt = _parse_expiry_date(expiry)
    if not expiry_dt:
        return 1 / 365
    now = datetime.now()
    # NSE options expire near market close; avoid a zero-day divide-by-zero on expiry day.
    expiry_close = expiry_dt.replace(hour=15, minute=30, second=0, microsecond=0)
    seconds = max((expiry_close - now).total_seconds(), 6 * 60 * 60)
    return seconds / (365.0 * 24 * 60 * 60)

def _option_price(spot: float, strike: float, T: float, r: float, sigma: float, option_type: str) -> float:
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * T) * _norm_cdf(d2)
    return strike * math.exp(-r * T) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)

def implied_volatility_newton(market_price: float, spot: float, strike: float, T: float, r: float, option_type: str) -> float:
    if market_price <= 0 or spot <= 0 or strike <= 0 or T <= 0:
        return 0.0
    low, high = 0.01, 3.0
    for _ in range(40):
        mid = (low + high) / 2
        price = _option_price(spot, strike, T, r, mid, option_type)
        if price > market_price:
            high = mid
        else:
            low = mid
    return (low + high) / 2

def black_scholes_greeks(spot: float, strike: float, T: float, r: float, sigma: float, option_type: str) -> dict:
    empty = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    if spot <= 0 or strike <= 0 or T <= 0 or sigma <= 0:
        return empty

    sqrt_t = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf = _norm_pdf(d1)

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf * sigma) / (2 * sqrt_t) - r * strike * math.exp(-r * T) * _norm_cdf(d2)) / 365
        rho = strike * T * math.exp(-r * T) * _norm_cdf(d2) / 100
    else:
        delta = _norm_cdf(d1) - 1
        theta = (-(spot * pdf * sigma) / (2 * sqrt_t) + r * strike * math.exp(-r * T) * _norm_cdf(-d2)) / 365
        rho = -strike * T * math.exp(-r * T) * _norm_cdf(-d2) / 100

    gamma = pdf / (spot * sigma * sqrt_t)
    vega = spot * pdf * sqrt_t / 100
    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
        "rho": round(rho, 4),
    }

class OptionChainService:
    """Fetches NSE option chain data and enriches it with Greeks and OI analytics."""

    OC_URL_INDEX = "https://www.nseindia.com/api/option-chain-indices?symbol={}"
    OC_URL_EQUITIES = "https://www.nseindia.com/api/option-chain-equities?symbol={}"
    INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX"}
    BSE_API_BASE = "https://api.bseindia.com/BseIndiaAPI/api"
    BSE_REALTIME_BASE = "https://api.bseindia.com/RealTimeBseIndiaAPI/api"
    BSE_HEADERS = {
        # Honest client identity (no browser fingerprint spoofing, per the
        # Phase 0 approval). Migration note: these on-demand calls are legacy
        # paths outside the shared HTTP layer; see docs/DATA_SOURCES.md.
        "User-Agent": "future-app/1.0 (market data client)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
    }
    RISK_FREE_RATE = 0.065

    def __init__(self, nse_client_instance):
        self.nse = nse_client_instance
        self._cache = {}
        self._cache_ttl = 0.8
        self._snapshot_ttl = 1.0
        self._stale_snapshot_ttl = 300
        self._failure_ttl = 2
        self._disk_snapshot_ttl = 6 * 60 * 60
        self._disk_cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "option_chain_cache")
        os.makedirs(self._disk_cache_dir, exist_ok=True)
        self._lot_size_cache = {"ts": 0, "lots": {}, "source": None}
        
        # Background worker state
        self._bg_snapshots = {}
        self._bg_lock = threading.Lock()
        self._bg_active_symbols = set()
        self._bg_threads = {} # Dict of symbol -> thread
        self._bg_last_access = {}  # symbol -> last time a client asked for it
        self._bg_idle_stop = 120.0  # stop a worker nobody has read for this long
        self.on_snapshot = None  # optional callback(symbol, snapshot) after each fresh fetch
        self._stop_bg = False

    def _cache_get(self, key):
        item = self._cache.get(key)
        if item and time.time() - item["ts"] < self._cache_ttl:
            return item["value"]
        return None

    def _cache_set(self, key, value):
        self._cache[key] = {"ts": time.time(), "value": value}
        return value

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None, num_strikes: int = 20, force_refresh: bool = False) -> dict:
        """Serve the shared background snapshot for (symbol, expiry).

        One worker per (symbol, expiry) refreshes NSE every OC_REFRESH_SECONDS no matter how
        many browsers/streams are watching, so upstream load no longer scales
        with clients. Falls back to a direct fetch (with stale/disk fallbacks)
        only until the first snapshot lands."""
        symbol = self._clean_symbol(symbol)

        if symbol == "SENSEX":  # BSE source, own cache
            return self._get_bse_option_chain(symbol, expiry, num_strikes, force_refresh=force_refresh)

        key = (symbol, expiry or None)
        self._bg_last_access[key] = time.time()
        self._ensure_bg_fetch(symbol, expiry)

        with self._bg_lock:
            snap = self._bg_snapshots.get(key)
        if snap:
            max_age = max(OC_REFRESH_SECONDS * 3, 10)
            if time.time() - snap.get("timestamp", 0) <= max_age or not self._market_is_open():
                out = dict(snap)
                out["server_ts"] = time.time()
                out["is_live"] = True
                return out
        return self._get_option_chain_direct(symbol, expiry, num_strikes, force_refresh=False)

    def _ensure_bg_fetch(self, symbol: str, expiry: Optional[str] = None):
        key = (symbol, expiry or None)
        with self._bg_lock:
            t = self._bg_threads.get(key)
            if t is None or not t.is_alive():
                self._stop_bg = False
                t = threading.Thread(target=self._symbol_worker, args=(symbol, expiry), daemon=True)
                self._bg_threads[key] = t
                t.start()
                logger.info("Background worker started for %s %s", symbol, expiry or "(nearest expiry)")

    @staticmethod
    def _market_is_open() -> bool:
        """Cheap IST weekday/session check (09:00-15:35). Holidays simply fail
        to fetch and fall into the slow failure back-off below."""
        ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        if ist.weekday() >= 5:
            return False
        minutes = ist.hour * 60 + ist.minute
        return 9 * 60 <= minutes <= 15 * 60 + 35

    def _symbol_worker(self, symbol: str, expiry: Optional[str] = None):
        """Worker for one (symbol, expiry). Refreshes the full snapshot every
        OC_REFRESH_SECONDS (default 2 s) while the market is open, slows to
        OC_CLOSED_REFRESH_SECONDS when closed, backs off when NSE refuses us
        (403 / breaker open / budget) and exits when nobody has asked for it
        for a while."""
        key = (symbol, expiry or None)
        logger.info("OptionChain Worker for %s %s started", symbol, expiry or "(nearest)")
        failures = 0
        while not self._stop_bg:
            try:
                if time.time() - self._bg_last_access.get(key, 0) > self._bg_idle_stop:
                    logger.info("OptionChain Worker for %s idle, stopping", key)
                    return
                start_t = time.time()
                data = self._fetch_sync(symbol, expiry, 50, bypass_cache=True)

                if data and not data.get("error"):
                    failures = 0
                    with self._bg_lock:
                        self._bg_snapshots[key] = data
                    cb = self.on_snapshot
                    if cb:
                        try:
                            cb(symbol, data)
                        except Exception as exc:  # never let a consumer kill the loop
                            logger.debug("on_snapshot callback failed: %s", exc)
                    period = OC_REFRESH_SECONDS if self._market_is_open() else OC_CLOSED_REFRESH_SECONDS
                    wait = max(0.0, period - (time.time() - start_t))
                else:
                    failures += 1
                    # 3s, 6s, 12s ... capped at 60s. The shared HTTP layer's
                    # breaker also pauses traffic; this just stops us spinning.
                    wait = min(60.0, 3.0 * (2 ** min(failures - 1, 5)))
                    logger.warning("BG FETCH FAILED: %s (attempt %d), retry in %.0fs", key, failures, wait)

                slept = 0.0
                while not self._stop_bg and slept < wait:
                    time.sleep(0.1)
                    slept += 0.1
            except Exception as e:
                logger.error("Worker Error for %s: %s", key, e)
                time.sleep(5)

    def _fetch_sync(self, symbol: str, expiry: Optional[str] = None, num_strikes: int = 20, bypass_cache: bool = False) -> dict:
        start_time = time.time()
        snapshot_key = f"oc_{symbol}_{expiry or 'nearest'}_{num_strikes}"
        
        # Check cache unless bypassed
        if not bypass_cache:
            cached = self._cache_get(snapshot_key)
            if cached: return cached

        # Fetch expiries first
        all_expiries = self.get_expiry_dates(symbol)
        if not all_expiries:
            return self._unavailable_payload(symbol, "Expiry data unavailable")

        if not expiry or expiry not in all_expiries:
            expiry = all_expiries[0]

        # Fetch raw data
        raw = self._fetch_raw(symbol, expiry, bypass_cache=bypass_cache)
        if not raw:
            # NSE is unreachable — fall back to disk cache before declaring failure
            disk = self._disk_cache_get(snapshot_key)
            if disk:
                return disk
            disk_latest = self._disk_cache_find_latest(f"oc_{symbol}_")
            if disk_latest:
                return disk_latest
            return self._unavailable_payload(symbol, "NSE data fetch failed")

        records = raw.get("records", {})
        filtered = raw.get("filtered", {})
        spot_price = float(records.get("underlyingValue") or 0.0)

        # Extract Spot Data. On live refreshes the chain payload's own
        # underlyingValue is the freshest price; skip the extra quote call.
        try:
            from stock_provider import get_live_quote
            live_quote = None if bypass_cache else get_live_quote(symbol, cache_ttl=5)
            if live_quote:
                spot_data = {
                    "price": spot_price or live_quote.get("price", 0.0),
                    "change": live_quote.get("change", 0.0),
                    "pChange": live_quote.get("changePercent", 0.0),
                }
            else:
                spot_data = self._extract_spot(records, filtered)
                if spot_price: spot_data["price"] = spot_price
        except Exception:
            spot_data = self._extract_spot(records, filtered)
            if spot_price: spot_data["price"] = spot_price

        spot = spot_data["price"]
        if spot <= 0:
            return {"error": "Invalid spot price", "symbol": symbol}

        expiry_data = [
            d for d in records.get("data", [])
            if d.get("expiryDate") == expiry or d.get("expiryDates") == expiry or (d.get("CE") or {}).get("expiryDate") == expiry
        ]
        if not expiry_data and "data" in records:
            expiry_data = records["data"]

        strike_map = {
            row.get("strikePrice", 0): {"CE": row.get("CE", {}), "PE": row.get("PE", {})}
            for row in expiry_data
        }
        strike_map.pop(0, None)
        
        if not strike_map:
            return {"error": f"No data for expiry {expiry}", "symbol": symbol}

        T = time_to_expiry_years(expiry)
        r = self.RISK_FREE_RATE
        lot_size = self.get_lot_size(symbol)
        all_strikes = sorted(strike_map.keys())
        atm_strike = min(all_strikes, key=lambda k: abs(k - spot))
        atm_idx = all_strikes.index(atm_strike)
        
        low = max(0, atm_idx - num_strikes)
        high = min(len(all_strikes), atm_idx + num_strikes + 1)
        selected_strikes = all_strikes[low:high]

        result_strikes = []
        total_call_oi = 0
        total_put_oi = 0

        for strike in selected_strikes:
            ce = strike_map[strike]["CE"]
            pe = strike_map[strike]["PE"]
            
            ce_ltp = self._pick_num(ce, "lastPrice", "ltp")
            pe_ltp = self._pick_num(pe, "lastPrice", "ltp")
            ce_oi = self._pick_num(ce, "openInterest", "openinterest", "oi")
            pe_oi = self._pick_num(pe, "openInterest", "openinterest", "oi")
            ce_oic = self._pick_num(ce, "changeinOpenInterest", "changeInOpenInterest", "changeInOI", "oiChange")
            pe_oic = self._pick_num(pe, "changeinOpenInterest", "changeInOpenInterest", "changeInOI", "oiChange")
            
            total_call_oi += ce_oi
            total_put_oi += pe_oi

            ce_iv_raw = ce.get("impliedVolatility", 0.0) or 0.0
            pe_iv_raw = pe.get("impliedVolatility", 0.0) or 0.0
            ce_sigma = ce_iv_raw / 100.0 if ce_iv_raw > 0 else 0.0
            pe_sigma = pe_iv_raw / 100.0 if pe_iv_raw > 0 else 0.0

            ce_greeks = black_scholes_greeks(spot, strike, T, r, ce_sigma, "call")
            pe_greeks = black_scholes_greeks(spot, strike, T, r, pe_sigma, "put")

            result_strikes.append({
                "strike": strike,
                "is_atm": strike == atm_strike,
                "call": self._format_side(ce, ce_ltp, ce_sigma, ce_greeks, ce_oi, ce_oic, lot_size),
                "put": self._format_side(pe, pe_ltp, pe_sigma, pe_greeks, pe_oi, pe_oic, lot_size),
            })

        result = {
            "symbol": symbol,
            "expiry": expiry,
            "spot": round(spot_data["price"], 2),
            "spot_change": round(spot_data["change"], 2),
            "spot_pchange": round(spot_data["pChange"], 2),
            "atm_strike": atm_strike,
            "lot_size": lot_size,
            "expiry_dates": all_expiries,
            "timestamp": int(time.time()),
            "fetch_latency": round(time.time() - start_time, 3),
            "pcr": round(total_put_oi / total_call_oi, 3) if total_call_oi else 0.0,
            "max_pain": self._compute_max_pain(selected_strikes, strike_map),
            "total_call_oi": int(total_call_oi),
            "total_put_oi": int(total_put_oi),
            "strikes": result_strikes,
            "source": "nse",
        }
        
        self._cache_set(snapshot_key, result)
        self._disk_cache_set(snapshot_key, result)
        return result
    def _unavailable_payload(self, symbol: str, message: str = None) -> dict:
        return {
            "unavailable": True,
            "symbol": symbol,
            "message": message or "NSE option-chain data is temporarily unavailable. Retrying in background.",
            "timestamp": int(time.time()),
            "strikes": [],
            "source": "nse",
        }

    def _cache_get_stale(self, key, ttl=None):
        item = self._cache.get(key)
        if item and time.time() - item["ts"] < (ttl or self._stale_snapshot_ttl):
            value = item["value"]
            if isinstance(value, dict):
                stale_value = dict(value)
                stale_value["stale"] = True
                stale_value["cache_age"] = round(time.time() - item["ts"], 2)
                return stale_value
            return value
        return None

    def _disk_cache_path(self, key: str) -> str:
        safe_key = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in key)
        return os.path.join(self._disk_cache_dir, f"{safe_key}.json")

    def _disk_cache_set(self, key: str, value: dict) -> None:
        try:
            payload = {"ts": time.time(), "value": value}
            with open(self._disk_cache_path(key), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
        except Exception as exc:
            logger.debug("Option chain disk cache write failed for %s: %s", key, exc)

    def _disk_cache_get(self, key: str, ttl=None) -> Optional[dict]:
        try:
            if ttl is None:
                ttl = self._disk_snapshot_ttl
            path = self._disk_cache_path(key)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            age = time.time() - float(payload.get("ts", 0))
            if age > ttl:
                return None
            value = payload.get("value")
            if isinstance(value, dict):
                value = dict(value)
                value["stale"] = True
                value["cache_age"] = round(age, 2)
                value["source"] = value.get("source") or "nse"
                return value
        except Exception as exc:
            logger.debug("Option chain disk cache read failed for %s: %s", key, exc)
        return None

    def _disk_cache_find_latest(self, prefix: str, ttl=None) -> Optional[dict]:
        try:
            if ttl is None:
                ttl = self._disk_snapshot_ttl
            safe_prefix = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in prefix)
            matches = []
            for name in os.listdir(self._disk_cache_dir):
                if name.startswith(safe_prefix) and name.endswith(".json"):
                    path = os.path.join(self._disk_cache_dir, name)
                    matches.append((os.path.getmtime(path), path))
            for _, path in sorted(matches, reverse=True):
                with open(path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                age = time.time() - float(payload.get("ts", 0))
                if age > ttl:
                    continue
                value = payload.get("value")
                if isinstance(value, dict):
                    value = dict(value)
                    value["stale"] = True
                    value["cache_age"] = round(age, 2)
                    value["source"] = value.get("source") or "nse"
                    return value
        except Exception as exc:
            logger.debug("Option chain disk cache scan failed for %s: %s", prefix, exc)
        return None

    def _clean_symbol(self, symbol: str) -> str:
        return (symbol or "NIFTY").upper().replace(".NS", "").replace(".BO", "")

    def get_lot_size(self, symbol: str) -> int:
        symbol = self._clean_symbol(symbol)
        fallback = {
            "NIFTY": 65,
            "BANKNIFTY": 30,
            "FINNIFTY": 65,
            "MIDCPNIFTY": 140,
            "NIFTYNXT50": 25,
            "SENSEX": 20,
            "BANKEX": 30,
            "RELIANCE": 500,
        }

        if symbol in ("SENSEX", "BANKEX"):
            return fallback[symbol]

        lots = self._load_fo_lot_sizes()
        lot_size = lots.get(symbol) or fallback.get(symbol)
        if lot_size:
            return int(lot_size)
        return 1

    def _load_fo_lot_sizes(self) -> dict:
        if self._lot_size_cache["lots"] and time.time() - self._lot_size_cache["ts"] < 24 * 60 * 60:
            return self._lot_size_cache["lots"]

        try:
            from datetime import date
            # Honest client identity (no browser spoofing, per Phase 0 approval).
            headers = {"User-Agent": "future-app/1.0 (market data client)",
                       "Referer": "https://www.nseindia.com/"}
            base = "https://nsearchives.nseindia.com/content/fo/NSE_FO_contract_{}.csv.gz"
            today = date.today()
            for offset in range(0, 12):
                dt = today - timedelta(days=offset)
                url = base.format(dt.strftime("%d%m%Y"))
                resp = requests.get(url, headers=headers, timeout=20)
                if resp.status_code != 200 or not resp.content:
                    continue

                payload = gzip.decompress(resp.content).decode("utf-8", "ignore")
                lots = {}
                for row in csv.DictReader(io.StringIO(payload)):
                    symbol = (row.get("TckrSymb") or "").upper().strip()
                    opt_type = (row.get("OptnTp") or "").upper().strip()
                    if not symbol or opt_type not in ("CE", "PE"):
                        continue
                    lot_raw = row.get("NewBrdLotQty") or row.get("MinLot") or "0"
                    try:
                        lot_size = int(float(lot_raw))
                    except (TypeError, ValueError):
                        continue
                    if lot_size > 0:
                        lots[symbol] = lot_size

                if lots:
                    self._lot_size_cache = {"ts": time.time(), "lots": lots, "source": url}
                    logger.info("Loaded %s FO lot sizes from %s", len(lots), url)
                    return lots
        except Exception as exc:
            logger.warning("Unable to load NSE FO lot sizes: %s", exc)

        return self._lot_size_cache.get("lots", {})

    def _fetch_raw(self, symbol: str, expiry: Optional[str] = None, bypass_cache: bool = False) -> Optional[dict]:
        symbol = self._clean_symbol(symbol)
        ttl = 0 if bypass_cache else self._cache_ttl

        type_param = "Indices" if symbol in self.INDEX_SYMBOLS else "Equity"
        params = {"type": type_param, "symbol": symbol}
        if expiry:
            params["expiry"] = expiry

        data = self.nse.get(
            "/api/option-chain-v3",
            params=params,
            referer_type="option-chain",
            cache_ttl=ttl,
        )
        if data and data.get("records", {}).get("data"):
            return data

        # Legacy fallback for older NSE sessions / cached gateways.
        legacy_path = "/api/option-chain-indices" if symbol in self.INDEX_SYMBOLS else "/api/option-chain-equities"
        legacy = self.nse.get(
            legacy_path,
            params={"symbol": symbol},
            referer_type="option-chain",
            cache_ttl=ttl,
        )
        if legacy and legacy.get("records", {}).get("data"):
            return legacy
        return None

    def get_expiry_dates(self, symbol: str) -> list:
        symbol = self._clean_symbol(symbol)
        if symbol == "SENSEX":
            return self._get_bse_expiry_dates()

        # Expiries are stable, cache for 5 minutes
        cached = self._cache_get(f"expiries_{symbol}")
        if cached:
            return cached

        data = self.nse.get(
            "/api/option-chain-contract-info",
            params={"symbol": symbol},
            referer_type="option-chain",
            cache_ttl=300,
        )

        expiries = []
        if data:
            expiries = data.get("expiryDates", []) or data.get("records", {}).get("expiryDates", [])

        # Fallback for older NSE sessions where the bulk endpoints still include expiries.
        type_param = "Indices" if symbol in self.INDEX_SYMBOLS else "Equity"
        data = self.nse.get(
            "/api/option-chain-v3",
            params={"type": type_param, "symbol": symbol},
            referer_type="option-chain",
            cache_ttl=300,
        ) if not expiries else None
        if data and "records" in data:
            expiries = data["records"].get("expiryDates", [])

        if not expiries:
            legacy_path = "/api/option-chain-indices" if symbol in self.INDEX_SYMBOLS else "/api/option-chain-equities"
            data = self.nse.get(
                legacy_path,
                params={"symbol": symbol},
                referer_type="option-chain",
                cache_ttl=300,
            )
            if data and "records" in data:
                expiries = data["records"].get("expiryDates", [])
        
        if not expiries:
            fallback = self._disk_cache_find_latest(f"oc_{symbol}_")
            if fallback and fallback.get("expiry_dates"):
                return self._cache_set(f"expiries_{symbol}", fallback["expiry_dates"])
            return []
            
        return self._cache_set(f"expiries_{symbol}", expiries)

    def _get_option_chain_direct(self, symbol: str, expiry: Optional[str] = None, num_strikes: int = 20, force_refresh: bool = False) -> dict:
        symbol = self._clean_symbol(symbol)

        # SENSEX (BSE) handling
        if symbol == "SENSEX":
            return self._get_bse_option_chain(symbol, expiry, num_strikes, force_refresh=force_refresh)

        snapshot_key = f"oc_{symbol}_{expiry or 'nearest'}_{num_strikes}"
        cached = None if force_refresh else self._cache_get(snapshot_key)
        if cached:
            return cached
        failure_key = f"oc_fail_{symbol}_{expiry or 'nearest'}_{num_strikes}"
        recent_failure = self._cache_get_stale(failure_key, ttl=self._failure_ttl)
        if recent_failure:
            stale = self._cache_get_stale(snapshot_key)
            if stale:
                return stale
            disk_stale = self._disk_cache_get(snapshot_key)
            if disk_stale:
                return disk_stale
            return recent_failure

        # Fetch expiries first if expiry not provided, as v3 works best when expiry is passed
        all_expiries = self.get_expiry_dates(symbol)
        if not all_expiries:
            stale = self._cache_get_stale(snapshot_key, ttl=self._stale_snapshot_ttl)
            if stale:
                return stale
            disk_stale = self._disk_cache_get(snapshot_key)
            if disk_stale:
                return disk_stale
            disk_latest = self._disk_cache_find_latest(f"oc_{symbol}_")
            if disk_latest:
                return disk_latest
            logger.error("No valid expiry dates found on NSE for %s.", symbol)
            return self._cache_set(failure_key, self._unavailable_payload(
                symbol,
                "NSE option-chain expiry data is temporarily unavailable. Retrying in background.",
            ))

        if not expiry or expiry not in all_expiries:
            expiry = all_expiries[0]

        raw = self._fetch_raw(symbol, expiry, bypass_cache=force_refresh)
        if not raw:
            stale = self._cache_get_stale(snapshot_key)
            if stale:
                return stale
            disk_stale = self._disk_cache_get(snapshot_key)
            if disk_stale:
                return disk_stale
            disk_latest = self._disk_cache_find_latest(f"oc_{symbol}_")
            if disk_latest:
                return disk_latest
            logger.error("Option chain data unavailable from NSE for %s", symbol)
            return self._cache_set(failure_key, self._unavailable_payload(symbol))

        records = raw.get("records", {})
        filtered = raw.get("filtered", {})
        
        # Priority 1: use the option-chain payload itself. During live refreshes this avoids
        # NSE quote-equity calls, which are often blocked for stocks and can freeze updates.
        spot_price = float(records.get("underlyingValue") or 0.0)

        spot_data = self._extract_spot(records, filtered)
        if spot_price:
            spot_data["price"] = spot_price

        if not force_refresh:
            try:
                from stock_provider import get_live_quote
                live_quote = get_live_quote(symbol, cache_ttl=5)
                if live_quote:
                    spot_data = {
                        "price": spot_price or live_quote.get("price", 0.0),
                        "change": live_quote.get("change", spot_data.get("change", 0.0)),
                        "pChange": live_quote.get("changePercent", spot_data.get("pChange", 0.0)),
                    }
            except Exception:
                pass

        spot = spot_data["price"]
        if spot <= 0:
            logger.error("NSE returned invalid spot price for %s", symbol)
            return {"error": "Invalid spot price from NSE. Data unavailable.", "symbol": symbol}

        expiry_data = [
            d for d in records.get("data", [])
            if d.get("expiryDate") == expiry or d.get("expiryDates") == expiry or (d.get("CE") or {}).get("expiryDate") == expiry
        ]
        
        # If the API already pre-filtered it (v3 behavior), just use all data
        if not expiry_data and "data" in records:
            expiry_data = records["data"]

        strike_map = {
            row.get("strikePrice", 0): {"CE": row.get("CE", {}), "PE": row.get("PE", {})}
            for row in expiry_data
        }
        strike_map.pop(0, None)
        if not strike_map:
            return {"error": f"No market data for expiry {expiry}", "symbol": symbol}

        T = time_to_expiry_years(expiry)
        r = self.RISK_FREE_RATE
        lot_size = self.get_lot_size(symbol)
        all_strikes = sorted(strike_map.keys())
        atm_strike = min(all_strikes, key=lambda k: abs(k - spot))
        atm_idx = all_strikes.index(atm_strike)
        low = max(0, atm_idx - num_strikes)
        high = min(len(all_strikes), atm_idx + num_strikes + 1)
        selected_strikes = all_strikes[low:high]

        result_strikes = []
        total_call_oi = 0
        total_put_oi = 0

        for strike in selected_strikes:
            ce = strike_map[strike]["CE"]
            pe = strike_map[strike]["PE"]
            ce_ltp = self._pick_num(ce, "lastPrice", "ltp")
            pe_ltp = self._pick_num(pe, "lastPrice", "ltp")
            ce_oi = self._pick_num(ce, "openInterest", "openinterest", "oi")
            pe_oi = self._pick_num(pe, "openInterest", "openinterest", "oi")
            ce_oic = self._pick_num(ce, "changeinOpenInterest", "changeInOpenInterest", "changeInOI", "oiChange")
            pe_oic = self._pick_num(pe, "changeinOpenInterest", "changeInOpenInterest", "changeInOI", "oiChange")
            total_call_oi += ce_oi
            total_put_oi += pe_oi

            ce_iv_raw = ce.get("impliedVolatility", 0.0) or 0.0
            pe_iv_raw = pe.get("impliedVolatility", 0.0) or 0.0
            ce_sigma = ce_iv_raw / 100.0 if ce_iv_raw > 0 else (
                implied_volatility_newton(ce_ltp, spot, strike, T, r, "call") if ce_ltp > 0 else 0.0
            )
            pe_sigma = pe_iv_raw / 100.0 if pe_iv_raw > 0 else (
                implied_volatility_newton(pe_ltp, spot, strike, T, r, "put") if pe_ltp > 0 else 0.0
            )

            ce_greeks = black_scholes_greeks(spot, strike, T, r, ce_sigma, "call")
            pe_greeks = black_scholes_greeks(spot, strike, T, r, pe_sigma, "put")
            result_strikes.append({
                "strike": strike,
                "is_atm": strike == atm_strike,
                "call": self._format_side(ce, ce_ltp, ce_sigma, ce_greeks, ce_oi, ce_oic, lot_size),
                "put": self._format_side(pe, pe_ltp, pe_sigma, pe_greeks, pe_oi, pe_oic, lot_size),
            })

        result = {
            "symbol": symbol,
            "expiry": expiry,
            "spot": round(spot_data["price"], 2),
            "spot_change": round(spot_data["change"], 2),
            "spot_pchange": round(spot_data["pChange"], 2),
            "atm_strike": atm_strike,
            "lot_size": lot_size,
            "expiry_dates": all_expiries,
            "timestamp": int(time.time()),
            "pcr": round(total_put_oi / total_call_oi, 3) if total_call_oi else 0.0,
            "max_pain": self._compute_max_pain(selected_strikes, strike_map),
            "total_call_oi": int(total_call_oi),
            "total_put_oi": int(total_put_oi),
            "strikes": result_strikes,
            "source": "nse",
        }
        self._cache_set(snapshot_key, result)
        self._disk_cache_set(snapshot_key, result)
        if expiry:
            self._cache_set(f"oc_{symbol}_{expiry}_{num_strikes}", result)
            self._disk_cache_set(f"oc_{symbol}_{expiry}_{num_strikes}", result)
        return result

    def _num(self, value, default=0.0) -> float:
        """Robustly convert value to float, handling strings, None, and common placeholders."""
        if value is None or value == "" or value == "N/A" or value == "-":
            return default
        try:
            if isinstance(value, str):
                return float(value.replace(",", "").replace("%", "").strip())
            return float(value)
        except (ValueError, TypeError):
            return default

    def _pick_num(self, source: dict, *keys: str, default=0.0) -> float:
        """Read numeric values from NSE payloads despite inconsistent field casing."""
        if not source:
            return default
        lowered = {str(k).lower(): v for k, v in source.items()}
        for key in keys:
            if key in source:
                return self._num(source.get(key), default)
            lookup_key = key.lower()
            if lookup_key in lowered:
                return self._num(lowered[lookup_key], default)
        return default

    def _derive_change_percent(self, ltp: float, change: float, payload_percent: float, side: dict) -> float:
        if payload_percent != 0.0:
            return payload_percent

        prev_close = self._pick_num(
            side,
            "previousClose",
            "prevClose",
            "prev_close",
            "closePrice",
            "prevPrice",
            "underlyingPrevClose",
        )
        if prev_close == 0.0 and ltp and change and ltp != change:
            prev_close = ltp - change
        if prev_close:
            return (change / prev_close) * 100
        if ltp and change:
            base = ltp - change
            if base:
                return (change / base) * 100
        return 0.0

    def _extract_spot(self, records: dict, filtered: dict) -> dict:
        """Returns a dict with price, change, and pChange."""
        spot = records.get("underlyingValue", 0.0) or 0.0
        # Search in records first
        if spot:
            return {
                "price": float(spot),
                "change": self._num(records.get("change") or records.get("priceChange")),
                "pChange": self._num(records.get("pChange") or records.get("pchange") or records.get("percentChange")),
            }
        
        # Search in filtered data
        for row in filtered.get("data", []) or []:
            for side in ("CE", "PE"):
                side_data = row.get(side) or {}
                value = side_data.get("underlyingValue")
                if value:
                    return {
                        "price": float(value),
                        "change": self._num(side_data.get("change") or side_data.get("priceChange")),
                        "pChange": self._num(side_data.get("pChange") or side_data.get("pchange") or side_data.get("percentChange")),
                    }
        return {"price": 0.0, "change": 0.0, "pChange": 0.0}

    def _format_side(self, side, ltp, sigma, greeks, oi, oi_change, lot_size=1):
        p_chg = self._pick_num(side, "pChange", "pchange", "percentChange", "perChange")
        chg = self._pick_num(side, "change", "priceChange", "netChange")
        volume = int(self._pick_num(side, "totalTradedVolume", "volume"))
        bid = round(self._pick_num(side, "bidprice", "bidPrice", "buyPrice1"), 2)
        ask = round(self._pick_num(side, "askPrice", "askprice", "sellPrice1"), 2)
        premium = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else round(float(ltp or 0.0), 2)
        buy_price = ask if ask > 0 else premium
        sell_price = bid if bid > 0 else premium
        
        # Fallback calculation if NSE omits percentage change.
        p_chg = self._derive_change_percent(float(ltp or 0.0), float(chg or 0.0), p_chg, side)
            
        return {
            "ltp": round(float(ltp or 0.0), 2),
            "premium": premium,
            "lot_size": int(lot_size or 1),
            "buy_funds": round(buy_price * int(lot_size or 1), 2),
            "sell_value": round(sell_price * int(lot_size or 1), 2),
            "change": round(float(chg or 0.0), 2),
            "pChange": round(float(p_chg or 0.0), 2),
            "iv": round(sigma * 100, 2),
            "delta": greeks["delta"],
            "gamma": greeks["gamma"],
            "theta": greeks["theta"],
            "vega": greeks["vega"],
            "rho": greeks["rho"],
            "oi": int(oi or 0),
            "oi_change": int(oi_change or 0),
            "volume": volume,
            "bid": bid,
            "ask": ask,
        }

    def _compute_max_pain(self, strikes: list, strike_map: dict) -> float:
        min_pain = float("inf")
        max_pain_strike = strikes[0] if strikes else 0

        for test_strike in strikes:
            total_loss = 0
            for k, v in strike_map.items():
                ce_oi = v["CE"].get("openInterest", 0) or 0
                pe_oi = v["PE"].get("openInterest", 0) or 0
                if test_strike > k:
                    total_loss += (test_strike - k) * ce_oi
                if k > test_strike:
                    total_loss += (k - test_strike) * pe_oi
            if total_loss < min_pain:
                min_pain = total_loss
                max_pain_strike = test_strike

        return max_pain_strike


    def _compute_max_pain(self, strikes: list, strike_map: dict) -> float:
        min_pain = float("inf")
        max_pain_strike = strikes[0] if strikes else 0

        for test_strike in strikes:
            total_loss = 0
            for k, v in strike_map.items():
                ce_oi = v["CE"].get("openInterest", 0) or 0
                pe_oi = v["PE"].get("openInterest", 0) or 0
                if test_strike > k:
                    total_loss += (test_strike - k) * ce_oi
                if k > test_strike:
                    total_loss += (k - test_strike) * pe_oi
            if total_loss < min_pain:
                min_pain = total_loss
                max_pain_strike = test_strike

        return max_pain_strike

    def get_historical_oi(self, symbol: str, expiry: str, strike: float) -> dict:
        symbol_clean = self._clean_symbol(symbol)
        instrument_type = "OPTIDX" if symbol_clean in self.INDEX_SYMBOLS else "OPTSTK"
        url = (
            "https://www.nseindia.com/api/historical/fo/derivatives"
            f"?instrumentType={instrument_type}&symbol={symbol_clean}"
            f"&expiryDate={expiry}&optionType=CE&strikePrice={int(strike)}"
            "&dateRange=&fromDate=&toDate=&dataType=allFO"
        )
        logger.info("NSE historical OI URL: %s", url)
        try:
            # Route through the shared HTTP layer (rate limit + breaker +
            # budget + single-flight) rather than a raw session call.
            path = url.replace(self.nse.BASE_URL, "") if hasattr(self.nse, "BASE_URL") else url
            data = self.nse.get(path, cache_ttl=15)
            if data:
                return {"status": "ok", "data": data.get("data", [])}
            return {"status": "error", "message": "unavailable (rate limit, breaker or blocked)"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def _bse_get(self, path: str, params: dict = None, cache_ttl: float = 1.0) -> Optional[dict]:
        params = dict(params or {})
        cache_key = ("bse", path, tuple(sorted(params.items())))
        if cache_ttl > 0:
            cached = self._cache_get(cache_key)
            if cached:
                return cached
        if cache_ttl < 2:
            params["_"] = int(time.time() * 1000)

        url = f"{self.BSE_API_BASE}{path}"
        try:
            resp = requests.get(url, params=params, headers=self.BSE_HEADERS, timeout=8)
            logger.info("BSE API CALL: %s | STATUS: %s", path, resp.status_code)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if cache_ttl > 0:
                self._cache_set(cache_key, data)
            return data
        except Exception as exc:
            logger.warning("BSE GET failed for %s: %s", path, exc)
            return None

    def _bse_realtime_get(self, path: str, cache_ttl: float = 0.5) -> Optional[dict | list]:
        cache_key = ("bse_realtime", path)
        if cache_ttl > 0:
            cached = self._cache_get(cache_key)
            if cached:
                return cached

        url = f"{self.BSE_REALTIME_BASE}{path}"
        try:
            resp = requests.get(
                url,
                params={"_": int(time.time() * 1000)},
                headers=self.BSE_HEADERS,
                timeout=5,
            )
            logger.info("BSE REALTIME CALL: %s | STATUS: %s", path, resp.status_code)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if cache_ttl > 0:
                self._cache_set(cache_key, data)
            return data
        except Exception as exc:
            logger.warning("BSE realtime GET failed for %s: %s", path, exc)
            return None

    def _get_bse_sensex_realtime_quote(self) -> Optional[dict]:
        data = self._bse_realtime_get("/GetSensexDatanew/w", cache_ttl=0.5)
        if not isinstance(data, list):
            return None
        for row in data:
            name = (row.get("indxnm") or "").upper()
            code = str(row.get("indxcode") or "")
            if "SENSEX" in name or code == "16":
                return {
                    "price": self._num(row.get("ltp")),
                    "change": self._num(row.get("chg")),
                    "pChange": self._num(row.get("perchg")),
                    "timestamp_text": row.get("dttm"),
                    "source": row.get("source"),
                }
        return None

    def _get_bse_expiry_dates(self) -> list:
        cached = self._cache_get("bse_expiries_SENSEX")
        if cached:
            return cached

        data = self._bse_get("/ddlExpiry_IV/w", {"scrip_cd": "1"}, cache_ttl=300)
        expiries = []
        for row in (data or {}).get("Table1", []) or []:
            expiry = (row.get("ExpiryDate") or "").strip()
            if expiry:
                expiries.append(expiry)
        return self._cache_set("bse_expiries_SENSEX", expiries)

    def _bse_side(self, row: dict, prefix: str) -> dict:
        key = lambda name: f"{prefix}_{name}" if prefix else name
        return {
            "lastPrice": self._num(row.get(key("Last_Trd_Price"))),
            "openInterest": self._num(row.get(key("Open_Interest"))),
            "changeinOpenInterest": self._num(row.get(key("Absolute_Change_OI"))),
            "totalTradedVolume": self._num(row.get(key("Vol_Traded"))),
            "bidPrice": self._num(row.get(key("BidPrice"))),
            "askPrice": self._num(row.get(key("OfferPrice"))),
            "change": self._num(row.get(key("NetChange"))),
            "impliedVolatility": self._num(row.get(key("IV"))),
        }

    def _get_bse_option_chain(self, symbol: str, expiry: Optional[str] = None, num_strikes: int = 20, force_refresh: bool = False) -> dict:
        """Fetch BSE SENSEX option chain using BSE's public derivatives API."""
        snapshot_key = f"bse_oc_SENSEX_{expiry or 'nearest'}_{num_strikes}"
        if not force_refresh:
            cached = self._cache_get(snapshot_key)
            if cached:
                return cached

        try:
            all_expiries = self._get_bse_expiry_dates()
            if all_expiries and (not expiry or expiry not in all_expiries):
                expiry = all_expiries[0]

            raw = self._bse_get(
                "/DerivOptionChain_IV/w",
                {"Expiry": expiry or "", "scrip_cd": "1", "strprice": "0"},
                cache_ttl=0 if force_refresh else self._cache_ttl,
            )
            rows = (raw or {}).get("Table", []) or []
            if not rows:
                return self._unavailable_payload("SENSEX", "BSE SENSEX option-chain data is temporarily unavailable.")

            option_chain_spot = self._num(rows[0].get("UlaValue"))
            spot = option_chain_spot
            spot_change = 0.0
            spot_pchange = 0.0
            realtime_quote = self._get_bse_sensex_realtime_quote()
            if realtime_quote and realtime_quote.get("price", 0) > 0:
                spot = realtime_quote["price"]
                spot_change = realtime_quote["change"]
                spot_pchange = realtime_quote["pChange"]

            if spot <= 0:
                return self._unavailable_payload("SENSEX", "BSE returned an invalid SENSEX spot price.")

            T = time_to_expiry_years(expiry)
            r = self.RISK_FREE_RATE
            lot_size = self.get_lot_size("SENSEX")
            strike_map = {}
            for row in rows:
                strike = self._num(row.get("Strike_Price1") or row.get("Strike_Price"))
                if strike <= 0:
                    continue
                ce = self._bse_side(row, "C")
                pe = self._bse_side(row, "")
                strike_map[strike] = {"CE": ce, "PE": pe}

            all_strikes = sorted(strike_map.keys())
            if not all_strikes:
                return self._unavailable_payload("SENSEX", "BSE returned no SENSEX strike data.")

            atm_strike = min(all_strikes, key=lambda k: abs(k - spot))
            atm_idx = all_strikes.index(atm_strike)
            low = max(0, atm_idx - num_strikes)
            high = min(len(all_strikes), atm_idx + num_strikes + 1)
            selected_strikes = all_strikes[low:high]

            result_strikes = []
            total_call_oi = 0
            total_put_oi = 0
            for strike in selected_strikes:
                ce = strike_map[strike]["CE"]
                pe = strike_map[strike]["PE"]
                ce_ltp = self._pick_num(ce, "lastPrice")
                pe_ltp = self._pick_num(pe, "lastPrice")
                ce_oi = self._pick_num(ce, "openInterest")
                pe_oi = self._pick_num(pe, "openInterest")
                ce_oic = self._pick_num(ce, "changeinOpenInterest")
                pe_oic = self._pick_num(pe, "changeinOpenInterest")
                total_call_oi += ce_oi
                total_put_oi += pe_oi

                ce_sigma = self._pick_num(ce, "impliedVolatility") / 100
                pe_sigma = self._pick_num(pe, "impliedVolatility") / 100
                if ce_sigma <= 0 and ce_ltp > 0:
                    ce_sigma = implied_volatility_newton(ce_ltp, spot, strike, T, r, "call")
                if pe_sigma <= 0 and pe_ltp > 0:
                    pe_sigma = implied_volatility_newton(pe_ltp, spot, strike, T, r, "put")

                ce_greeks = black_scholes_greeks(spot, strike, T, r, ce_sigma, "call")
                pe_greeks = black_scholes_greeks(spot, strike, T, r, pe_sigma, "put")
                result_strikes.append({
                    "strike": strike,
                    "is_atm": strike == atm_strike,
                    "call": self._format_side(ce, ce_ltp, ce_sigma, ce_greeks, ce_oi, ce_oic, lot_size),
                    "put": self._format_side(pe, pe_ltp, pe_sigma, pe_greeks, pe_oi, pe_oic, lot_size),
                })

            result = {
                "symbol": "SENSEX",
                "expiry": expiry,
                "spot": round(spot, 2),
                "spot_change": round(spot_change, 2),
                "spot_pchange": round(spot_pchange, 2),
                "option_chain_spot": round(option_chain_spot, 2),
                "spot_source": "bse_realtime" if realtime_quote else "bse_option_chain",
                "spot_timestamp": (realtime_quote or {}).get("timestamp_text"),
                "atm_strike": atm_strike,
                "lot_size": lot_size,
                "expiry_dates": all_expiries,
                "timestamp": int(time.time()),
                "pcr": round(total_put_oi / total_call_oi, 3) if total_call_oi else 0.0,
                "max_pain": self._compute_max_pain(selected_strikes, strike_map),
                "total_call_oi": int(total_call_oi),
                "total_put_oi": int(total_put_oi),
                "strikes": result_strikes,
                "source": "bse",
            }
            self._cache_set(snapshot_key, result)
            return result
        except Exception as e:
            logger.exception("BSE SENSEX option-chain error")
            return {"error": f"BSE SENSEX error: {str(e)}", "symbol": "SENSEX"}
