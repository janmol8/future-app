"""
Stock data provider using yfinance.
Fetches historical OHLC data and search results for Indian stocks, indices, and global stocks.
"""
import yfinance as yf
import os

cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yf_cache")
os.makedirs(cache_dir, exist_ok=True)
try:
    yf.set_tz_cache_location(cache_dir)
except AttributeError:
    pass

import pandas as pd
from datetime import datetime, timedelta, timezone
import time
import math
import logging
import csv
import io
import requests
import os
import json
from nse_client import nse_client

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logger = logging.getLogger(__name__)
_provider_cache = {}


def _cache_get(key, ttl):
    item = _provider_cache.get(key)
    if item and time.time() - item["ts"] <= ttl:
        return item["value"]
    return None


def _cache_set(key, value):
    if value is not None:
        _provider_cache[key] = {"ts": time.time(), "value": value}
    return value

# Interval mapping: our internal name -> yfinance interval
INTERVAL_MAP = {
    "1s":  "1m",
    "5s":  "1m",
    "15s": "1m",
    "30s": "1m",
    "1m":  "1m",
    "2m":  "2m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "4h":  "1h",
    "1d":  "1d",
    "1w":  "1wk",
    "1M":  "1mo"
}

# NSE/BSE Trading Holidays (Official NSE Circulars)
# Holiday source is now dynamic: the live calendar loaded by the market
# runtime (market/calendar_service.py, refreshed from the provider chain).
# The old hard-coded NSE_HOLIDAYS table was removed per the Phase 0 approval --
# stale year tables silently misclassified holidays.

def _holiday_set() -> set:
    """Live holiday dates (YYYY-MM-DD). Falls back to an empty set when the
    market runtime is not initialised (imports, tests, first request)."""
    try:
        from app import _market_runtime as _rt   # lazy: avoid import cycle
        return set((getattr(_rt, "holidays", None) or {}).keys())
    except Exception:
        return set()


def is_indian_market_open():
    """Check if the Indian stock market (NSE/BSE) is currently open."""
    # IST is UTC + 5:30
    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))

    # Weekend check
    if ist_now.weekday() >= 5:
        return False

    # Holiday check
    today_str = ist_now.strftime("%Y-%m-%d")
    if today_str in _holiday_set():
        return False

    # Time check (9:15 AM to 3:30 PM IST)
    market_start = ist_now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = ist_now.replace(hour=15, minute=30, second=0, microsecond=0)

    return market_start <= ist_now <= market_end

def get_indian_market_session():
    """Return NSE equity session using IST: PRE_MARKET, REGULAR, POST_MARKET, or CLOSED."""
    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    if ist_now.weekday() >= 5:
        return "CLOSED"
    today_str = ist_now.strftime("%Y-%m-%d")
    if today_str in _holiday_set():
        return "CLOSED"
    mins = ist_now.hour * 60 + ist_now.minute
    if 540 <= mins < 555:
        return "PRE_MARKET"
    if 555 <= mins < 930:
        return "REGULAR"
    if 930 <= mins < 960:
        return "POST_MARKET"
    return "CLOSED"

def _finite_positive(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n > 0 else None
    except (TypeError, ValueError):
        return None

def get_live_quote(symbol: str, cache_ttl=10):
    """
    Fetch the absolute latest price and daily change for a stock/index.
    Uses nse_client.get_quote for Indian equities/indices, falling back to yfinance.
    """
    yf_symbol = resolve_symbol(symbol)
    cache_key = ("quote", yf_symbol)
    cached = _cache_get(cache_key, cache_ttl)
    if cached is not None:
        return cached
        
    is_indian = (yf_symbol.endswith(".NS") or yf_symbol.endswith(".BO") or 
                 yf_symbol.startswith("^NSE") or yf_symbol.startswith("^BSE") or 
                 yf_symbol.startswith("^CNX"))
    market_session = get_indian_market_session() if is_indian else "REGULAR"

    # 1. Primary Source: Authoritative NSE Data for Indian Stocks/Indices
    if is_indian:
        try:
            # During pre-market, try to get exact IEP
            if market_session == "PRE_MARKET":
                pm_data = nse_client.get_premarket_price(symbol)
                if pm_data and pm_data.get("iep"):
                    iep = float(pm_data["iep"])
                    prev_close = float(pm_data.get("prevClose", 0))
                    if prev_close > 0:
                        change = iep - prev_close
                        change_pct = (change / prev_close) * 100
                    else:
                        change = float(pm_data.get("perChange", 0)) # fallback to raw
                        change_pct = float(pm_data.get("perChange", 0))
                        
                    return _cache_set(cache_key, {
                        "symbol": symbol,
                        "price": round(iep, 2),
                        "change": round(change, 2),
                        "changePercent": round(change_pct, 2),
                        "open": round(iep, 2),
                        "high": round(iep, 2),
                        "low": round(iep, 2),
                        "volume": int(pm_data.get("totalVolume", 0)),
                        "previousClose": round(prev_close, 2),
                        "timestamp": int(time.time()),
                        "session": market_session,
                        "is_open": is_indian_market_open()
                    })

            # Regular / Post Market: Get exact authoritative quote from NSE
            nse_data = nse_client.get_quote(symbol)
            if nse_data and nse_data.get("lastPrice"):
                last_price = float(nse_data["lastPrice"])
                prev_close = float(nse_data.get("previousClose", 0))
                
                # Force precise mathematical calculation
                if prev_close > 0:
                    change = last_price - prev_close
                    change_pct = (change / prev_close) * 100
                else:
                    change = float(nse_data.get("change", 0))
                    change_pct = float(nse_data.get("pChange", 0))

                return _cache_set(cache_key, {
                    "symbol": symbol,
                    "price": round(last_price, 2),
                    "change": round(change, 2),
                    "changePercent": round(change_pct, 2),
                    "open": round(float(nse_data.get("open", last_price)), 2),
                    "high": round(float(nse_data.get("high", last_price)), 2),
                    "low": round(float(nse_data.get("low", last_price)), 2),
                    "volume": 0,
                    "previousClose": round(prev_close, 2),
                    "timestamp": int(time.time()),
                    "session": market_session,
                    "is_open": is_indian_market_open()
                })
        except Exception as e:
            logger.warning("NSE get_quote failed for %s, falling back to yfinance: %s", symbol, e)


    # 2. Fallback Source: yfinance (for Global stocks, Forex, Commodities, or NSE failure)
    try:
        ticker = yf.Ticker(yf_symbol)
        
        try:
            live_price = ticker.fast_info['last_price']
            prev_close = ticker.fast_info.get('previous_close', 0)
        except:
            live_price = None
            prev_close = 0

        candles = fetch_ohlc(symbol, "1d", 5)
        if not candles or len(candles) == 0:
            return None
            
        last_candle = candles[-1]
        
        ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        today_date_str = ist_now.strftime("%Y-%m-%d")
        
        last_candle_ist_ts = last_candle["time"] + 19800
        last_candle_date_str = datetime.fromtimestamp(last_candle_ist_ts, timezone.utc).strftime("%Y-%m-%d")
        
        if live_price is None or math.isnan(live_price) or live_price == 0:
            live_price = float(last_candle["close"])
            
        if prev_close == 0:
            if last_candle_date_str == today_date_str:
                if len(candles) > 1:
                    prev_close = float(candles[-2]["close"])
                else:
                    prev_close = float(last_candle["open"])
            else:
                prev_close = float(last_candle["close"])
        
        if prev_close == 0: prev_close = live_price

        change = round(live_price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close != 0 else 0
        
        final_high = max(float(last_candle["high"]), live_price)
        final_low = min(float(last_candle["low"]), live_price)

        return _cache_set(cache_key, {
            "symbol": symbol,
            "price": round(live_price, 2),
            "change": change,
            "changePercent": change_pct,
            "open": round(float(last_candle["open"]), 2),
            "high": round(final_high, 2),
            "low": round(final_low, 2),
            "volume": float(last_candle["volume"]),
            "previousClose": round(prev_close, 2),
            "timestamp": int(time.time()),
            "session": market_session,
            "is_open": is_indian_market_open() if is_indian else True
        })
    except Exception:
        logger.exception("Error in fallback get_live_quote for %s", symbol)
        return _cache_get(cache_key, 300)


# ── Interval to Data Range Mapping ──
SUGGESTED_LOOKBACK = {
    "1s":  "7d",
    "5s":  "7d",
    "30s": "7d",
    "1m":  "7d",
    "5m":  "30d",
    "15m": "60d",
    "30m": "60d",
    "1h":  "730d",
    "4h":  "730d",
    "1d":  "max",
    "1w":  "max",
    "1M":  "max",
}

MAX_PERIOD = {
    "1s":  "7d",
    "5s":  "7d",
    "15s": "7d",
    "30s": "7d",
    "1m":  "7d",
    "2m":  "60d",
    "5m":  "60d",
    "15m": "60d",
    "30m": "60d",
    "1h":  "730d",
    "4h":  "730d",
    "1d":  "max",
    "1w":  "max",
    "1M":  "max",
}

DEFAULT_PERIOD = {
    "1s":  "1d",
    "5s":  "1d",
    "15s": "1d",
    "30s": "1d",
    "1m":  "1d",
    "2m":  "5d",
    "5m":  "5d",
    "15m": "30d",
    "30m": "30d",
    "1h":  "60d",
    "4h":  "60d",
    "1d":  "365d",
    "1w":  "max",
    "1M":  "max",
}

# ── Global & Indian Indices (Yahoo Finance tickers) ──
INDIAN_INDICES = [
    {"symbol": "^NSEI",      "name": "Nifty 50",                        "exchange": "NSE"},
    {"symbol": "^NSEBANK",   "name": "Nifty Bank",                      "exchange": "NSE"},
    {"symbol": "^INDIAVIX",  "name": "India VIX",                       "exchange": "NSE"},
    {"symbol": "CL=F",       "name": "Crude Oil",                       "exchange": "NYMEX", "type": "index"},
    {"symbol": "GC=F",       "name": "Gold",                            "exchange": "COMEX", "type": "index"},
    {"symbol": "SI=F",       "name": "Silver",                          "exchange": "COMEX", "type": "index"},
    {"symbol": "^CNXIT",     "name": "Nifty IT",                        "exchange": "NSE"},
    {"symbol": "GC=F",       "name": "Gold",                            "exchange": "COMEX"},
    {"symbol": "SI=F",       "name": "Silver",                          "exchange": "COMEX"},
    {"symbol": "^CNXIT",     "name": "Nifty IT",                        "exchange": "NSE"},
    {"symbol": "^CNXFIN",    "name": "Nifty Financial Services",        "exchange": "NSE"},
    {"symbol": "^CNXAUTO",   "name": "Nifty Auto",                      "exchange": "NSE"},
    {"symbol": "^CNXPHARMA", "name": "Nifty Pharma",                    "exchange": "NSE"},
    {"symbol": "^CNXFMCG",   "name": "Nifty FMCG",                      "exchange": "NSE"},
    {"symbol": "^CNXPSUBANK","name": "Nifty PSU Bank",                  "exchange": "NSE"},
    {"symbol": "^BSESN",     "name": "S&P BSE Sensex",                  "exchange": "BSE"},
    {"symbol": "^NIFTYMDCP50","name": "Nifty Midcap 50",                 "exchange": "NSE"},
    {"symbol": "^NIFTYMID100","name": "Nifty Midcap 100",               "exchange": "NSE"},
    {"symbol": "^CNXSMALL",  "name": "Nifty Smallcap 100",              "exchange": "NSE"},
]

GLOBAL_INDICES = [
    {"symbol": "^GSPC",      "name": "S&P 500",                         "exchange": "US"},
    {"symbol": "^DJI",       "name": "Dow Jones Industrial Average",    "exchange": "US"},
    {"symbol": "^IXIC",      "name": "Nasdaq Composite",                "exchange": "US"},
    {"symbol": "^FTSE",      "name": "FTSE 100",                        "exchange": "UK"},
    {"symbol": "^GDAXI",     "name": "DAX Performance Index",           "exchange": "Germany"},
    {"symbol": "^N225",      "name": "Nikkei 225",                      "exchange": "Japan"},
    {"symbol": "^HSI",       "name": "Hang Seng Index",                 "exchange": "Hong Kong"},
]

POPULAR_GLOBAL_STOCKS = [
    ("AAPL", "Apple Inc."), ("TSLA", "Tesla Inc."), ("MSFT", "Microsoft"), 
    ("GOOGL", "Google"), ("NVDA", "Nvidia"), ("AMZN", "Amazon"), ("META", "Meta"),
]

COMMODITIES_FOREX = [
    {"symbol": "GC=F",       "name": "Gold Futures",                    "exchange": "COMEX", "type": "index"},
    {"symbol": "SI=F",       "name": "Silver Futures",                  "exchange": "COMEX", "type": "index"},
    {"symbol": "CL=F",       "name": "Crude Oil Futures",               "exchange": "NYMEX", "type": "index"},
    {"symbol": "USDINR=X",   "name": "USD/INR",                         "exchange": "FOREX", "type": "forex"},
    {"symbol": "EURINR=X",   "name": "EUR/INR",                         "exchange": "FOREX", "type": "forex"},
    {"symbol": "GBPINR=X",   "name": "GBP/INR",                         "exchange": "FOREX", "type": "forex"},
]

POPULAR_NSE_STOCKS = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "SBIN", "LICI", "ITC", "HINDUNILVR"]

_nse_master_data = None
_nse_master_loaded = False
_bse_master_data = None

def _download_nse_master_list():
    """Download and cache NSE symbol list from official CSV."""
    global _nse_master_data, _nse_master_loaded
    if _nse_master_loaded:
        return _nse_master_data
    
    cache_file = "nse_master_cache.json"
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                _nse_master_data = json.load(f)
            _nse_master_loaded = True
            return _nse_master_data
        except: pass

    urls = [
        "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
        "https://www.nseindia.com/content/equities/EQUITY_L.csv"
    ]
    
    master_list = []
    for url in urls:
        try:
            resp = requests.get(url, timeout=15, headers={
                # Honest client identity (no browser spoofing, per Phase 0).
                "User-Agent": "future-app/1.0 (market data client)",
                "Accept": "text/csv,application/csv,*/*",
            })
            if resp.status_code == 200:
                content = resp.content.decode("utf-8-sig", errors="ignore")
                reader = csv.DictReader(io.StringIO(content))
                for row in reader:
                    sym = row.get("SYMBOL", "").strip()
                    name = row.get("NAME OF COMPANY", row.get("Company Name", row.get("name", ""))).strip()
                    if sym and name:
                        master_list.append({
                            "symbol": sym,
                            "name": name,
                            "exchange": "NSE",
                            "type": "stock",
                        })
                if master_list:
                    break
        except:
            continue

    # If NSE download failed, fall back to popular stocks
    if not master_list:
        for sym in POPULAR_NSE_STOCKS:
            master_list.append({
                "symbol": sym,
                "name": sym,
                "exchange": "NSE",
                "type": "stock",
            })

    # Save to cache
    try:
        with open(cache_file, "w") as f:
            json.dump(master_list, f)
    except:
        pass

    _nse_master_data = master_list
    _nse_master_loaded = True
    return _nse_master_data


def get_all_nse_stocks():
    """Get all NSE stocks from master data."""
    return _download_nse_master_list()


def resolve_symbol(symbol: str) -> str:
    """Normalize symbol for yfinance - add .NS/.BO suffix or keep index/forex tags."""
    s = symbol.strip().upper()

    # 0. Fix common index symbol mismatches
    if s == "^BANKNIFTY" or s == "BANKNIFTY" or s == "BANKNIFTY1!":
        return "^NSEBANK"
    if s == "NIFTY" or s == "NIFTY1!" or s == "GIFTNIFTY" or s == "GIFTNIFTY1!" or s == "SGXNIFTY":
        return "^NSEI"

    # 1. Indices/Commodities/Forex usually have special characters
    if s.startswith("^") or s.endswith("=F") or s.endswith("=X"):
        return s

    # Check if it's already in COMMODITIES_FOREX
    for item in COMMODITIES_FOREX:
        if item["symbol"].upper() == s or item["symbol"].replace("=F", "").replace("=X", "").upper() == s:
            return item["symbol"]

    # 2. Global stocks (no .NS/.BO) stay as-is
    global_tickers = {s for s, _ in POPULAR_GLOBAL_STOCKS}
    if s in global_tickers:
        return s

    # 3. Handle BSE/NSE suffixes
    if s.endswith(".NS") or s.endswith(".BO"):
        return s

    # Check if it's a crypto suffix - skip
    for suffix in ["USDT", "USDC", "BUSD", "FDUSD"]:
        if s.endswith(suffix):
            return s

    # Default to .NS for Indian stocks if not specified
    return s + ".NS"


def fetch_ohlc(symbol: str, interval: str = "1d", range_days: int = 365, offset_days: int = 0):
    """
    Fetch historical OHLC data for a stock or index symbol.
    Supports offset_days for loading more historical data (pagination).
    Returns list of candle dicts.
    """
    from datetime import timezone
    yf_interval = INTERVAL_MAP.get(interval, "1d")
    yf_symbol = resolve_symbol(symbol)
    cache_key = ("ohlc", yf_symbol, interval, int(range_days), int(offset_days))
    cached = _cache_get(cache_key, 20 if interval in ("1s", "5s", "15s", "30s", "1m") else 300)
    if cached is not None:
        return cached

    def download_history(dl_symbol):
        hist_dl = None
        for attempt in range(3):
            try:
                hist_dl = yf.download(
                    dl_symbol,
                    period=period,
                    interval=yf_interval,
                    progress=False,
                    auto_adjust=False,
                    threads=False,
                    timeout=20,
                )
                if hist_dl is not None and not hist_dl.empty:
                    break
            except Exception as exc:
                logger.warning("yfinance download failed for %s attempt %s: %s", dl_symbol, attempt + 1, exc)
                time.sleep(0.35 * (attempt + 1))
        if hist_dl is not None and not hist_dl.empty and isinstance(hist_dl.columns, pd.MultiIndex):
            if dl_symbol in hist_dl.columns.get_level_values(-1):
                hist_dl = hist_dl.xs(dl_symbol, axis=1, level=-1)
            else:
                hist_dl.columns = hist_dl.columns.get_level_values(0)
        return hist_dl

    if range_days <= 7:
        period = "7d"
    elif range_days <= 30:
        period = "1mo"
    elif range_days <= 90:
        period = "3mo"
    elif range_days <= 180:
        period = "6mo"
    elif range_days <= 365:
        period = "1y"
    else:
        period = "max"

    # Apply offset: if offset > 0, we need to go further back in time
    if offset_days > 0 and period != "max":
        # Calculate start and end dates for offset loading
        end_date = datetime.now(timezone.utc) - timedelta(days=offset_days)
        start_date = end_date - timedelta(days=range_days)
        try:
            ticker = yf.Ticker(yf_symbol)
            hist = ticker.history(start=start_date.strftime("%Y-%m-%d"),
                                  end=end_date.strftime("%Y-%m-%d"),
                                  interval=yf_interval, auto_adjust=False, timeout=20)
        except Exception:
            logger.exception("yfinance offset history failed for %s", yf_symbol)
            return _cache_get(cache_key, 3600)
    else:
        max_p = MAX_PERIOD.get(interval, "max")
        if max_p != "max":
            period_rank = {"7d": 0, "1mo": 1, "3mo": 2, "6mo": 3, "1y": 4, "2y": 5, "5y": 6, "10y": 7, "max": 8}
            max_rank = period_rank.get(max_p, 8)
            if period_rank.get(period, 8) > max_rank:
                period = max_p

        hist = None
        try:
            ticker = yf.Ticker(yf_symbol)
            # Strategy for 1m: try 7d, if empty try 5d, 2d, 1d
            if interval == "1m":
                for p in ["7d", "5d", "2d", "1d"]:
                    hist = ticker.history(period=p, interval=yf_interval, auto_adjust=False, timeout=15)
                    if hist is not None and not hist.empty:
                        break
            else:
                hist = ticker.history(period=period, interval=yf_interval, auto_adjust=False, timeout=20)
        except Exception as exc:
            logger.warning("Primary yfinance history failed for %s: %s", yf_symbol, exc)
            hist = None

        if hist is None or hist.empty:
            # Try alternate suffix
            if yf_symbol.endswith(".NS"):
                alt = yf_symbol.replace(".NS", ".BO")
            elif yf_symbol.endswith(".BO"):
                alt = yf_symbol.replace(".BO", ".NS")
            elif not yf_symbol.startswith("^"):
                alt = yf_symbol + ".NS"
            else:
                alt = None
            try:
                if alt:
                    ticker = yf.Ticker(alt)
                    if interval == "1m":
                        for p in ["7d", "5d", "2d", "1d"]:
                            hist = ticker.history(period=p, interval=yf_interval, auto_adjust=False, timeout=15)
                            if hist is not None and not hist.empty:
                                break
                    else:
                        hist = ticker.history(period=period, interval=yf_interval, auto_adjust=False, timeout=20)
            except Exception as exc:
                logger.warning("Alternate yfinance history failed for %s: %s", yf_symbol, exc)
                pass

        if hist is None or hist.empty:
            try:
                hist = download_history(yf_symbol)
            except Exception:
                logger.exception("Fallback yfinance download failed for %s", yf_symbol)
                return _cache_get(cache_key, 3600)

    if hist is None or hist.empty:
        return _cache_get(cache_key, 3600)

    # --- Robust Gap Filling Logic for Daily Bars (especially for Indian Indices) ---
    is_indian = symbol.endswith(".NS") or symbol.endswith(".BO") or \
                symbol.startswith("^NSE") or symbol.startswith("^BSE") or symbol.startswith("^CNX")
    if interval == "1d" and is_indian:
        market_is_open = is_indian_market_open()
        
        try:
            # 1. Detect Gaps
            has_gap = False
            if not hist.empty:
                # Check for gaps > 1.5 days in the last 7 sessions
                cutoff = hist.index[-1] - pd.Timedelta(days=7)
                recent_hist = hist[hist.index >= cutoff]
                if len(recent_hist) > 1:
                    has_gap = (recent_hist.index.to_series().diff() > pd.Timedelta(days=1.5)).any()
            
            is_sparse = len(hist) < 3
            
            # 2. Reconstruct if needed
            # IMPORTANT: Skip ALL reconstruction when market is open
            # During market hours, today's bar is incomplete and any 1m data
            # would show live tick as close instead of actual final close
            if (has_gap or is_sparse) and not market_is_open:
                ticker = yf.Ticker(yf_symbol)
                m_hist = ticker.history(period="7d", interval="1m", auto_adjust=False)
                if not m_hist.empty:
                    # Aggregate 1m to 1d using IST date
                    # Note: yfinance 1m data is already localized to Asia/Kolkata
                    m_hist['ist_date'] = m_hist.index.date
                    daily_recon = m_hist.groupby('ist_date').agg({
                        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                    })
                    # Set index to IST midnight (which is 18:30 UTC of previous day)
                    daily_recon.index = pd.to_datetime(daily_recon.index).tz_localize("Asia/Kolkata")
                    
                    if not daily_recon.empty:
                        if hist.empty:
                            hist = daily_recon
                        else:
                            # Prioritize RECONSTRUCTED data for the last few days
                            # This ensures historical gaps are filled
                            hist = pd.concat([hist, daily_recon])
                            hist = hist[~hist.index.duplicated(keep='last')].sort_index()
        except Exception:
            logger.exception("Error during reconstruction for %s", symbol)

    candles = []
    ist_offset = 19800 # 5.5 hours in seconds
    for idx, row in hist.iterrows():
        ts = int(idx.timestamp())
        
        # Align timestamps to IST boundaries for Indian stocks to match yfinance (00:00 IST = 18:30 UTC)
        # Note: idx is already localized. .timestamp() returns UTC epoch.
        if is_indian:
            if interval == "1d":
                # Lightweight Charts treats Unix timestamps as UTC dates for daily bars.
                # Store the NSE session date at UTC midnight so a June 12 candle is
                # displayed as June 12, not the previous UTC evening.
                dt = datetime.fromtimestamp(ts + ist_offset, timezone.utc)
                ts = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())
            elif interval == "1w":
                dt = datetime.fromtimestamp(ts + ist_offset, timezone.utc)
                monday = dt - timedelta(days=dt.weekday())
                ts = int(datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp())
            elif interval == "1M":
                dt = datetime.fromtimestamp(ts + ist_offset, timezone.utc)
                ts = int(datetime(dt.year, dt.month, 1, tzinfo=timezone.utc).timestamp())
        
        # Safety: handle NaN values
        o, h, l, c, v = row.get("Open", 0), row.get("High", 0), row.get("Low", 0), row.get("Close", 0), row.get("Volume", 0)
        if any(math.isnan(x) for x in [o, h, l, c]): continue
        
        candles.append({
            "time": ts,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": float(v)
        })
    return _cache_set(cache_key, candles)


def search_all_symbols(query: str):
    """Search for symbols across Indian stocks, indices, and global stocks."""
    results = []
    q = query.strip().upper()
    if not q:
        return _get_default_symbols()

    # Priority 1: Exact matches in Indices
    for idx in INDIAN_INDICES + GLOBAL_INDICES:
        if q == idx["symbol"].upper() or q == idx["name"].upper():
            results.append(_make_search_result(idx["symbol"], idx["name"], idx["exchange"], "index"))

    # Priority 2: Exact matches in Global Stocks
    for sym, name in POPULAR_GLOBAL_STOCKS:
        if q == sym.upper():
            results.append(_make_search_result(sym, name, "Global", "stock"))

    # Priority 3: Exact matches in Commodities
    for item in COMMODITIES_FOREX:
        if q == item["symbol"].upper() or q == item["symbol"].replace("=F", "").replace("=X", "").upper():
            results.append(_make_search_result(item["symbol"], item["name"], item["exchange"], item["type"]))

    # Priority 4: Partial matches in Indices
    for idx in INDIAN_INDICES + GLOBAL_INDICES:
        if (q in idx["symbol"].upper() or q in idx["name"].upper()) and not any(r["symbol"] == idx["symbol"] for r in results):
            results.append(_make_search_result(idx["symbol"], idx["name"], idx["exchange"], "index"))

    # Priority 5: Indian Stocks (Master List)
    all_stocks = get_all_nse_stocks()
    for s in all_stocks:
        if q == s["symbol"].upper():
            if not any(r["symbol"] == s["symbol"] for r in results):
                results.append(_make_search_result(s["symbol"], s["name"], s["exchange"], s["type"]))
        elif q in s["symbol"].upper() or q in s["name"].upper():
            if not any(r["symbol"] == s["symbol"] for r in results):
                results.append(_make_search_result(s["symbol"], s["name"], s["exchange"], s["type"]))
        if len(results) > 80: break

    # Priority 6: Global Stocks (Partial)
    for sym, name in POPULAR_GLOBAL_STOCKS:
        if (q in sym.upper() or q in name.upper()) and not any(r["symbol"] == sym for r in results):
            results.append(_make_search_result(sym, name, "Global", "stock"))

    return results[:100]


def _make_search_result(symbol, name, exchange, type_):
    return {"symbol": symbol, "name": name, "exchange": exchange, "type": type_}


def _get_default_symbols():
    """Return default list when no query."""
    results = []
    # Indices
    for idx in INDIAN_INDICES[:8]:
        results.append(_make_search_result(idx["symbol"], idx["name"], idx["exchange"], "index"))
    # Forex/Gold
    for item in COMMODITIES_FOREX[:6]:
        results.append(_make_search_result(item["symbol"], item["name"], item["exchange"], "index"))
    # Nifty 50 stocks
    for sym in POPULAR_NSE_STOCKS[:10]:
        results.append(_make_search_result(sym, sym, "NSE", "stock"))
    return results
