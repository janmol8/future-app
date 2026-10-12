"""
Crypto data provider using Binance public API.
Fetches historical OHLC data and live prices for cryptocurrency pairs.
"""
import requests
import time
import logging
from datetime import datetime, timedelta

BINANCE_BASE = "https://api.binance.com"
logger = logging.getLogger(__name__)
_session = requests.Session()
_cache = {}


def _cache_get(key, ttl):
    item = _cache.get(key)
    if item and time.time() - item["ts"] <= ttl:
        return item["value"]
    return None


def _cache_set(key, value):
    if value is not None:
        _cache[key] = {"ts": time.time(), "value": value}
    return value


def _get_json(path, params=None, timeout=10, retries=2):
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(f"{BINANCE_BASE}{path}", params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (418, 429, 500, 502, 503, 504):
                time.sleep(0.35 * (attempt + 1))
                continue
            logger.warning("Binance HTTP %s for %s", resp.status_code, path)
            return None
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(0.35 * (attempt + 1))
    logger.warning("Binance request failed for %s: %s", path, last_error)
    return None

# Binance interval strings
INTERVAL_MAP = {
    "1s":  "1s",
    "5s":  "1s",
    "15s": "1s",
    "30s": "1s",
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1d",
    "1w":  "1w",
    "1M":  "1M",
}

# Duration in milliseconds for each interval
INTERVAL_MS = {
    "1s":  1 * 1000,
    "1m":  60 * 1000,
    "5m":  5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h":  60 * 60 * 1000,
    "4h":  4 * 60 * 60 * 1000,
    "1d":  24 * 60 * 60 * 1000,
    "1w":  7 * 24 * 60 * 60 * 1000,
    "1M":  30 * 24 * 60 * 60 * 1000,
}

# How many candles back in ms for each interval
MAX_LOOKBACK_MS = {
    "1s":  3 * 24 * 3600 * 1000,        # 3 days
    "1m":  7 * 24 * 60 * 60 * 1000,      # 7 days
    "5m":  30 * 24 * 60 * 60 * 1000,     # 30 days
    "15m": 60 * 24 * 60 * 60 * 1000,     # 60 days
    "30m": 60 * 24 * 60 * 60 * 1000,     # 60 days
    "1h":  365 * 24 * 60 * 60 * 1000,    # 1 year
    "4h":  365 * 24 * 60 * 60 * 1000,    # 1 year
    "1d":  20 * 365 * 24 * 60 * 60 * 1000, # 20 years
    "1w":  20 * 365 * 24 * 60 * 60 * 1000, # 20 years
    "1M":  20 * 365 * 24 * 60 * 60 * 1000, # 20 years
}

COMMON_CRYPTO = [
    # Top 50+ crypto by market cap
    {"symbol": "BTCUSDT", "name": "Bitcoin", "exchange": "Binance"},
    {"symbol": "ETHUSDT", "name": "Ethereum", "exchange": "Binance"},
    {"symbol": "BNBUSDT", "name": "BNB", "exchange": "Binance"},
    {"symbol": "SOLUSDT", "name": "Solana", "exchange": "Binance"},
    {"symbol": "XRPUSDT", "name": "XRP", "exchange": "Binance"},
    {"symbol": "ADAUSDT", "name": "Cardano", "exchange": "Binance"},
    {"symbol": "DOGEUSDT", "name": "Dogecoin", "exchange": "Binance"},
    {"symbol": "AVAXUSDT", "name": "Avalanche", "exchange": "Binance"},
    {"symbol": "DOTUSDT", "name": "Polkadot", "exchange": "Binance"},
    {"symbol": "MATICUSDT", "name": "Polygon", "exchange": "Binance"},
    {"symbol": "LINKUSDT", "name": "Chainlink", "exchange": "Binance"},
    {"symbol": "UNIUSDT", "name": "Uniswap", "exchange": "Binance"},
    {"symbol": "ATOMUSDT", "name": "Cosmos", "exchange": "Binance"},
    {"symbol": "LTCUSDT", "name": "Litecoin", "exchange": "Binance"},
    {"symbol": "BCHUSDT", "name": "Bitcoin Cash", "exchange": "Binance"},
    {"symbol": "TRXUSDT", "name": "TRON", "exchange": "Binance"},
    {"symbol": "APTUSDT", "name": "Aptos", "exchange": "Binance"},
    {"symbol": "ARBUSDT", "name": "Arbitrum", "exchange": "Binance"},
    {"symbol": "OPUSDT", "name": "Optimism", "exchange": "Binance"},
    {"symbol": "PEPEUSDT", "name": "Pepe", "exchange": "Binance"},
    {"symbol": "FILUSDT", "name": "Filecoin", "exchange": "Binance"},
    {"symbol": "NEARUSDT", "name": "NEAR Protocol", "exchange": "Binance"},
    {"symbol": "AAVEUSDT", "name": "Aave", "exchange": "Binance"},
    {"symbol": "ALGOUSDT", "name": "Algorand", "exchange": "Binance"},
    {"symbol": "SUIUSDT", "name": "Sui", "exchange": "Binance"},
    {"symbol": "TIAUSDT", "name": "Celestia", "exchange": "Binance"},
    {"symbol": "SEIUSDT", "name": "Sei", "exchange": "Binance"},
    {"symbol": "INJUSDT", "name": "Injective", "exchange": "Binance"},
    {"symbol": "FETUSDT", "name": "Fetch.ai", "exchange": "Binance"},
    {"symbol": "STXUSDT", "name": "Stacks", "exchange": "Binance"},
    {"symbol": "SANDUSDT", "name": "The Sandbox", "exchange": "Binance"},
    {"symbol": "MANAUSDT", "name": "Decentraland", "exchange": "Binance"},
    {"symbol": "AXSUSDT", "name": "Axie Infinity", "exchange": "Binance"},
    {"symbol": "XTZUSDT", "name": "Tezos", "exchange": "Binance"},
    {"symbol": "EOSUSDT", "name": "EOS", "exchange": "Binance"},
    {"symbol": "ICXUSDT", "name": "ICON", "exchange": "Binance"},
    {"symbol": "VETUSDT", "name": "VeChain", "exchange": "Binance"},
    {"symbol": "THETAUSDT", "name": "Theta Network", "exchange": "Binance"},
    {"symbol": "GRTUSDT", "name": "The Graph", "exchange": "Binance"},
    {"symbol": "RUNEUSDT", "name": "THORChain", "exchange": "Binance"},
    {"symbol": "FTMUSDT", "name": "Fantom", "exchange": "Binance"},
    {"symbol": "CRVUSDT", "name": "Curve DAO", "exchange": "Binance"},
    {"symbol": "MKRUSDT", "name": "Maker", "exchange": "Binance"},
    {"symbol": "COMPUSDT", "name": "Compound", "exchange": "Binance"},
    {"symbol": "ANKRUSDT", "name": "Ankr", "exchange": "Binance"},
    {"symbol": "CHZUSDT", "name": "Chiliz", "exchange": "Binance"},
    {"symbol": "ENJUSDT", "name": "Enjin Coin", "exchange": "Binance"},
    {"symbol": "BATUSDT", "name": "Basic Attention", "exchange": "Binance"},
    {"symbol": "ZILUSDT", "name": "Zilliqa", "exchange": "Binance"},
    {"symbol": "ONEUSDT", "name": "Harmony", "exchange": "Binance"},
]


def fetch_ohlc(symbol: str, interval: str = "1d", limit: int = 500, start_time: int = None):
    """
    Fetch historical OHLC data from Binance.
    Supports fetching more than 1000 candles if needed (via multiple requests).
    """
    # Special handling for simulated intervals (5s, 15s, etc.)
    base_interval = interval
    aggregation_factor = 1
    
    if interval == "5s":
        base_interval = "1s"
        aggregation_factor = 5
    elif interval == "15s":
        base_interval = "1s"
        aggregation_factor = 15
    elif interval == "30s":
        base_interval = "1s"
        aggregation_factor = 30

    binance_interval = INTERVAL_MAP.get(base_interval, "1d")
    binance_symbol = symbol.strip().upper()

    if not any(binance_symbol.endswith(s) for s in ["USDT", "BTC", "ETH", "BNB", "BUSD"]):
        binance_symbol = binance_symbol + "USDT"

    cache_key = ("ohlc", binance_symbol, interval, int(limit), int(start_time or 0))
    cached = _cache_get(cache_key, 20 if interval in ("1s", "5s", "15s", "30s", "1m") else 120)
    if cached is not None:
        return cached

    all_candles = []
    current_start = start_time * 1000 if start_time else None
    
    # If no start_time, we want the latest candles. Binance gives latest by default if no startTime/endTime.
    # But if we want more than 1000, we'd need to use endTime for pagination.
    # For now, we'll just handle the case where startTime is provided for historical data.
    
    target_count = limit * aggregation_factor
    max_per_request = 1000

    try:
        while len(all_candles) < target_count:
            params = {
                "symbol": binance_symbol,
                "interval": binance_interval,
                "limit": min(target_count - len(all_candles), max_per_request),
            }
            if current_start:
                params["startTime"] = int(current_start)
            
            data = _get_json("/api/v3/klines", params=params, timeout=10, retries=2)
            if not data or not isinstance(data, list):
                break

            batch = []
            for k in data:
                batch.append({
                    "time": k[0] // 1000,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            
            all_candles.extend(batch)
            
            if len(data) < max_per_request:
                break # No more data available
            
            # Update current_start to just after the last candle received
            current_start = data[-1][0] + 1
            
            # Safety break to avoid infinite loops
            if not start_time and len(all_candles) >= 1000:
                break

        if not all_candles:
            return None

        # Simple aggregation for simulated intervals
        if aggregation_factor > 1:
            aggregated = []
            for i in range(0, len(all_candles), aggregation_factor):
                chunk = all_candles[i:i+aggregation_factor]
                if not chunk: continue
                aggregated.append({
                    "time": chunk[0]["time"],
                    "open": chunk[0]["open"],
                    "high": max(c["high"] for c in chunk),
                    "low": min(c["low"] for c in chunk),
                    "close": chunk[-1]["close"],
                    "volume": sum(c["volume"] for c in chunk),
                })
            return _cache_set(cache_key, aggregated)

        return _cache_set(cache_key, all_candles)

    except Exception:
        logger.exception("Error fetching crypto OHLC for %s", symbol)
        return _cache_get(cache_key, 3600)


def search_crypto(query: str):
    """Search for crypto by symbol or name."""
    query = query.lower().strip()
    results = []
    for c in COMMON_CRYPTO:
        clean_sym = c["symbol"].replace("USDT", "")
        if query in clean_sym.lower() or query in c["name"].lower():
            results.append({
                "symbol": c["symbol"],
                "name": c["name"],
                "exchange": "Binance",
                "type": "crypto",
            })
    return results[:20]


def fetch_live_price(symbol: str):
    """
    Fetch the latest live price and daily change (since 00:00 UTC) for a crypto symbol.
    """
    s = symbol.strip().upper()
    if not any(s.endswith(suf) for suf in ["USDT", "BTC", "ETH", "BNB", "BUSD"]):
        s = s + "USDT"
    cache_key = ("live", s)
    cached = _cache_get(cache_key, 1)
    if cached is not None:
        return cached
    try:
        # 1. Get current ticker for live price and volume
        data = _get_json("/api/v3/ticker/24hr", params={"symbol": s}, timeout=5, retries=2)
        if not data:
            return None
        last_price = float(data["lastPrice"])
        
        # 2. Get today's daily kline to determine true daily open (00:00 UTC)
        # This ensures the change matches Future App's daily change
        k_data = _get_json("/api/v3/klines", params={"symbol": s, "interval": "1d", "limit": 1}, timeout=3, retries=1)
        if k_data:
            if k_data:
                day_open = float(k_data[0][1]) # kline open price
                change = last_price - day_open
                change_pct = (change / day_open * 100) if day_open != 0 else 0
                
                return _cache_set(cache_key, {
                    "price": last_price,
                    "volume": float(data["volume"]),
                    "high": float(data["highPrice"]),
                    "low": float(data["lowPrice"]),
                    "open": day_open,
                    "change": round(change, 4),
                    "changePercent": round(change_pct, 2),
                    "timestamp": int(time.time()),
                })

        # Fallback to rolling 24h if klines fail
        return _cache_set(cache_key, {
            "price": last_price,
            "volume": float(data["volume"]),
            "high": float(data["highPrice"]),
            "low": float(data["lowPrice"]),
            "open": float(data["openPrice"]),
            "change": float(data["priceChange"]),
            "changePercent": float(data["priceChangePercent"]),
            "timestamp": int(time.time()),
        })
    except Exception:
        logger.exception("Error fetching live crypto price for %s", s)
        return _cache_get(cache_key, 60)
