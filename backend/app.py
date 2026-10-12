"""
Future App - Flask Backend
Serves OHLC data for stocks (yfinance) and crypto (Binance) with technical indicators.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context, session
from flask_cors import CORS
import json
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re
import math
import logging
import threading
import websocket
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from nse_client import nse_client
from nse_premarket_service import NSEPreMarketService
from option_chain_service import OptionChainService, _option_price, time_to_expiry_years
from market.config import OC_REFRESH_SECONDS, OC_ENABLE_NSE_WS
from decimal import Decimal
import uuid
from trading_engine.paper_trade_account import PaperTradeAccount
from trading_engine.models import (
    Order, Position, Wallet, Trade, OrderStatus, OrderSide, OrderType,
    PositionSide, TimeInForce, JournalEntry, BTCUSDT_RISK_TIERS
)

import stock_provider
import crypto_provider
import indicators
import pattern_detector
from backtester import Backtester
from screener import run_screener
from ai_engine import AIEngine
from auth_service import AuthService
from functools import wraps
import collections
import requests
from subscription_service import SubscriptionService
from paper_trade import engine as pt_engine

# New Futures Trading Imports
# ...
app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(32)
CORS(app)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("future_app")

# Helper to get current IST time
def get_ist_now():
    # Use UTC then add 5:30 to be absolute about timezone independence
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

# ---------------------------------------------------------------------------
# Shared quote store shims (per-symbol 0.5 s pollers were removed).
# Only the market scheduler calls providers; every route/stream reads the
# shared SQLite store. _get_cached_quote converts a store row to the legacy
# quote shape so existing endpoints and the SSE stream keep working.
# ---------------------------------------------------------------------------
_INDEX_STORE_MAP = {
    "^NSEI": "NIFTY 50", "NIFTY": "NIFTY 50",
    "^NSEBANK": "BANK NIFTY", "BANKNIFTY": "BANK NIFTY",
    "FINNIFTY": "FIN NIFTY", "MIDCPNIFTY": "MIDCPNIFTY",
    "^BSESN": "SENSEX", "SENSEX": "SENSEX",
    "^INDIAVIX": "INDIA VIX",
}

def _store_symbol(symbol: str) -> str:
    """Map a legacy Yahoo-style symbol to its canonical store key."""
    s = (symbol or "").strip().upper()
    if s in _INDEX_STORE_MAP:
        return _INDEX_STORE_MAP[s]
    if s.endswith(".NS") or s.endswith(".BO"):
        s = s[:-3]
    return s

def _store_quote_to_legacy(row, requested: str):
    """Store row -> legacy quote dict. Missing values stay None (never 0)."""
    if not row or row.get("last") is None:
        return None
    as_of = row.get("as_of")
    try:
        ts = int(datetime.fromisoformat(as_of).timestamp()) if as_of else int(time.time())
    except (ValueError, TypeError):
        ts = int(time.time())
    return {
        "symbol": requested,
        "price": row["last"],
        "change": row.get("change"),
        "changePercent": row.get("change_pct"),
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "volume": row.get("volume"),          # None when the source lacks it
        "previousClose": row.get("prev_close"),
        "timestamp": ts,
        "session": row.get("session"),
        "is_open": row.get("session") == "REGULAR",
        "is_stale": bool(row.get("is_stale")),
        "source": row.get("provider"),
        "as_of": as_of,
    }

def _get_cached_quote(symbol: str):
    """Latest scheduler-refreshed quote from the shared store (None if absent)."""
    try:
        row = market_runtime.store.get_quote(_store_symbol(symbol))
    except Exception:
        return None
    return _store_quote_to_legacy(row, symbol)

def _ensure_poller(symbol: str):
    """Legacy hook. Quote refresh now happens centrally in the scheduler;
    browsers instead register interest so the scheduler knows what to refresh."""
    try:
        market_runtime.store.register_interest(_store_symbol(symbol), ttl_seconds=120)
    except Exception:
        pass

def _register_subscriber(symbol: str):
    _ensure_poller(symbol)

def _unregister_subscriber(symbol: str):
    pass  # interest expires via TTL
# ---------------------------------------------------------------------------# Holiday/special-session calendar now comes from the market subsystem
# (provider calendar cached in config/market_calendar.json). The static
# tables were removed — see backend/market/calendar_service.py.

# ---------------------------------------------------------------------------
# Market status: reads the scheduler-refreshed status (+ calendar fallback)
# ---------------------------------------------------------------------------

def fetch_nse_holiday_list() -> dict:
    """Holiday map from the market calendar service (provider calendar cached
    daily in config/market_calendar.json). Kept for route compatibility."""
    return market_runtime.holidays

def get_market_session():
    """(session_str, holiday_name) from the shared calendar service."""
    from market.calendar_service import compute_status, load_special_sessions
    status = compute_status(
        market_runtime.holidays,
        special_sessions=load_special_sessions(),
    )
    state = "PRE_MARKET" if status.state == "PRE_OPEN" else status.state
    logger.info("IST Time: %s, Session: %s", get_ist_now().strftime('%H:%M:%S'), state)
    return state, status.holiday_name

def is_indian_symbol(symbol: str) -> bool:
    s = (symbol or "").strip().upper()
    if not s:
        return False
    if (
        s.endswith(".NS") or s.endswith(".BO") or
        s.startswith("^NSE") or s.startswith("^BSE") or
        s.startswith("^CNX") or s.startswith("^INDIA")
    ):
        return True
    try:
        resolved = stock_provider.resolve_symbol(s)
    except Exception:
        return False
    return (
        resolved.endswith(".NS") or resolved.endswith(".BO") or
        resolved.startswith("^NSE") or resolved.startswith("^BSE") or
        resolved.startswith("^CNX") or resolved.startswith("^INDIA")
    )

# Helper for JSON serialization of futures-related objects
def serialize_futures(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, '__dict__'):
        return {k: serialize_futures(v) for k, v in obj.__dict__.items()}
    if isinstance(obj, dict):
        return {k: serialize_futures(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_futures(x) for x in obj]
    return obj

# Initialize modules
ai_engine = AIEngine()
auth_service = AuthService()
subscription_service = SubscriptionService()
futures_account = PaperTradeAccount(data_file="futures_trade_data.json")
nse_premarket_service = NSEPreMarketService(nse_client)
option_chain_svc = OptionChainService(nse_client)


def _publish_chain_spot(symbol: str, snap: dict) -> None:
    """Every option-chain fetch carries a fresh underlying price. Write it to
    the shared quote store so the chart, header and watchlist move at the same
    ~2 s cadence without any extra NSE calls."""
    try:
        spot = float(snap.get("spot") or 0)
        if spot <= 0:
            return
        from market.providers.base import Quote
        key = _store_symbol(symbol)
        change = float(snap.get("spot_change") or 0)
        prev = spot - change
        market_runtime.store.upsert_quote(Quote(
            symbol=key, last=spot, prev_close=prev if prev > 0 else None,
            change=change, change_pct=float(snap.get("spot_pchange") or 0),
            provider="option_chain",
            session="REGULAR" if option_chain_svc._market_is_open() else "CLOSED",
        ))
    except Exception as exc:
        logger.debug("publish chain spot skipped for %s: %s", symbol, exc)


option_chain_svc.on_snapshot = _publish_chain_spot
OHLC_CACHE = {}
# ⚡ Dynamic TTL per interval — fresh for intraday (matches refreshLastCandle rate), longer for history
_OHLC_TTL_MAP = {
    '1M': 600,   # monthly  → 10 min
    '1w': 600,   # weekly   → 10 min
    '1wk':600,
    '1d': 120,   # daily    → 2 min  (NSE quote is authoritative, but OHLC still used for history)
    '4h':  60,   # 4-hour   → 60 s
    '1h':  30,   # 1-hour   → 30 s
    '30m': 15,   # 30-min   → 15 s
    '15m': 10,   # 15-min   → 10 s
    '5m':  8,    # 5-min    → 8 s
    '1m':  5,    # 1-min    → 5 s  (matches frontend refreshLastCandle interval)
}
OHLC_CACHE_TTL = 30  # fallback default

def _ohlc_ttl(interval: str) -> int:
    """Return cache TTL in seconds for a given OHLC interval."""
    return _OHLC_TTL_MAP.get(interval, OHLC_CACHE_TTL)
_OHLC_CACHE_LOCK = threading.Lock()

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

# In-memory alerts storage for demo
# Price alerts are durable now: user_price_alerts table via
# market.alerts_service (the old in-memory USER_ALERTS list is gone).

# ─── Security: HTTP hardening headers (after_request) ─────────────────────────
@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault("X-Frame-Options",        "DENY")
    resp.headers.setdefault("X-Content-Type-Options",  "nosniff")
    resp.headers.setdefault("X-XSS-Protection",        "1; mode=block")
    resp.headers.setdefault("Referrer-Policy",          "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy",       "geolocation=(), microphone=()")
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp

# ─── Security: Simple in-process rate limiter ─────────────────────────────────
_RATE_BUCKETS: dict = {}
_RATE_LOCK_RL = threading.Lock()

def _rate_limit(key: str, max_calls: int = 10, window: int = 60) -> bool:
    """Return True (blocked) when key exceeds max_calls within window seconds."""
    import time as _t
    now = _t.time()
    with _RATE_LOCK_RL:
        bucket = _RATE_BUCKETS.setdefault(key, collections.deque())
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if len(bucket) >= max_calls:
            return True
        bucket.append(now)
        if len(_RATE_BUCKETS) > 5000:
            expired = [k for k, v in _RATE_BUCKETS.items() if not v or v[-1] < now - window * 2]
            for k in expired[:500]:
                _RATE_BUCKETS.pop(k, None)
        return False

def rate_limited(max_calls: int = 10, window: int = 60):
    """Decorator: rate-limit endpoint by IP."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip  = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
            key = f"{ip}:{request.path}"
            if _rate_limit(key, max_calls, window):
                return jsonify({"error": "Too many requests — please wait a moment."}), 429
            return f(*args, **kwargs)
        return wrapped
    return decorator

# ─── Helper: determine if symbol is crypto ────────────────────────────────────
def is_crypto_symbol(symbol: str) -> bool:
    s = symbol.strip().upper()
    for c in crypto_provider.COMMON_CRYPTO:
        if c["symbol"] == s:
            return True
    for suffix in ["USDT", "USDC", "BUSD", "FDUSD"]:
        if s.endswith(suffix):
            base = s[:-len(suffix)]
            if 2 <= len(base) <= 10:
                return True
    return False


def aggregate_candles(candles, interval_sec, symbol=""):
    """
    Robustly aggregate smaller candles into a larger timeframe based on timestamps.
    """
    if not candles: return []
    aggregated = []
    current_group = []
    
    is_indian = symbol.endswith(".NS") or symbol.endswith(".BO") or symbol.startswith("^NSE") or symbol.startswith("^BSE")
    ist_offset = 19800
    
    def get_bucket_start(ts):
        if is_indian and interval_sec >= 86400:
            return ((ts + ist_offset) // interval_sec) * interval_sec - ist_offset
        return (ts // interval_sec) * interval_sec

    # Start time of the first bucket
    bucket_start = get_bucket_start(candles[0]["time"])
    
    for c in candles:
        while c["time"] >= bucket_start + interval_sec:
            if current_group:
                aggregated.append({
                    "time": bucket_start,
                    "open": current_group[0]["open"],
                    "high": max(x["high"] for x in current_group),
                    "low": min(x["low"] for x in current_group),
                    "close": current_group[-1]["close"],
                    "volume": sum(x["volume"] for x in current_group),
                })
                current_group = []
            bucket_start = get_bucket_start(c["time"])
        current_group.append(c)
        
    if current_group:
        aggregated.append({
            "time": bucket_start,
            "open": current_group[0]["open"],
            "high": max(x["high"] for x in current_group),
            "low": min(x["low"] for x in current_group),
            "close": current_group[-1]["close"],
            "volume": sum(x["volume"] for x in current_group),
        })
    return aggregated


def get_ohlc_data(symbol, interval, range_days, offset_days=0):
    """Universal helper to fetch and aggregate OHLC data."""
    crypto_mode = is_crypto_symbol(symbol)
    interval_info = parse_custom_interval(interval)
    source_interval = interval_info["bestSourceInterval"]
    interval_sec = interval_info["ms"] // 1000
    cache_key = (symbol.upper(), interval, round(float(range_days), 4), int(offset_days))
    with _OHLC_CACHE_LOCK:
        cached = OHLC_CACHE.get(cache_key)
    if cached and time.time() - cached["ts"] < _ohlc_ttl(interval):
        return cached["candles"]

    try:
        if crypto_mode:
            interval_ms_val = interval_ms(interval)
            interval_in_days = interval_ms_val / 86400000.0
            limit = min(max(int(range_days / interval_in_days) if interval_in_days > 0 else 100, 100), 50000)
            
            start_time = None
            if offset_days > 0 or range_days > 0.1:
                now_ts = int(time.time())
                start_time = now_ts - int((range_days + offset_days) * 86400)
            
            source_ms = interval_ms(source_interval)
            agg_factor = max(1, interval_ms_val // source_ms)
            candles = crypto_provider.fetch_ohlc(symbol, source_interval, limit * agg_factor, start_time=start_time)
        else:
            fetch_range = max(int(range_days), 1)
            candles = stock_provider.fetch_ohlc(symbol, source_interval, fetch_range, offset_days=offset_days)

        if not candles: return None
        
        # Ensure chronological order (oldest first)
        candles = sorted(candles, key=lambda x: x["time"])

        for c in candles:
            for key in ["open", "high", "low", "close"]:
                if math.isnan(c[key]): c[key] = 0
            if math.isnan(c["volume"]): c["volume"] = 0

        if interval_info.get("needsAggregation", False):
            candles = aggregate_candles(candles, interval_sec, symbol=symbol)
        with _OHLC_CACHE_LOCK:
            OHLC_CACHE[cache_key] = {"ts": time.time(), "candles": candles}
            if len(OHLC_CACHE) > 500:
                oldest_key = min(OHLC_CACHE, key=lambda k: OHLC_CACHE[k]["ts"])
                OHLC_CACHE.pop(oldest_key, None)
        return candles
    except Exception:
        logger.exception("get_ohlc_data failed for %s (%s)", symbol, interval)
        return None


# ─── Routes ───────────────────────────────────────────────────────────────────

def get_symbol_metadata(symbol: str):
    """Retrieve metadata (name, type) for a given symbol."""
    s = symbol.strip().upper()
    
    # 1. Check Crypto
    if is_crypto_symbol(s):
        for c in crypto_provider.COMMON_CRYPTO:
            if c["symbol"] == s:
                return {"name": c["name"], "type": "crypto"}
        return {"name": s.replace("USDT", ""), "type": "crypto"}
    
    # 2. Check Indian Indices
    for idx in stock_provider.INDIAN_INDICES:
        if idx["symbol"] == s:
            return {"name": idx["name"], "type": "index"}
            
    # 3. Check Commodities/Forex
    for item in stock_provider.COMMODITIES_FOREX:
        if item["symbol"] == s:
            return {"name": item["name"], "type": item["type"]}
            
    # 4. Check NSE Stocks
    for stock in (stock_provider._nse_master_data or []):
        if stock["symbol"] == s or f"{stock['symbol']}.NS" == s:
            return {"name": stock["name"], "type": "stock"}
            
    # 5. Check BSE Stocks
    for stock in (stock_provider._bse_master_data or []):
        if stock["symbol"] == s or f"{stock['symbol']}.BO" == s:
            return {"name": stock["name"], "type": "stock"}
            
    # 6. Global Stocks
    for sym, name in stock_provider.POPULAR_GLOBAL_STOCKS:
        if sym == s:
            return {"name": name, "type": "stock"}
            
    return {"name": s.replace(".NS", "").replace(".BO", "").replace("^", ""), "type": "stock"}


@app.route("/")
def index():
    resp = send_from_directory(FRONTEND_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.route("/favicon.ico")
def favicon():
    return Response(status=204)

@app.route("/privacy")
def privacy_policy():
    return send_from_directory(FRONTEND_DIR, "privacy.html")

@app.route("/terms")
def terms_of_service():
    return send_from_directory(FRONTEND_DIR, "terms.html")

@app.route("/checkout")
def checkout_page():
    return send_from_directory(FRONTEND_DIR, "checkout.html")

@app.route("/css/<path:filename>")
def serve_css(filename):
    resp = send_from_directory(os.path.join(FRONTEND_DIR, "css"), filename)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.route("/js/<path:filename>")
def serve_js(filename):
    resp = send_from_directory(os.path.join(FRONTEND_DIR, "js"), filename)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.route("/webfonts/<path:filename>")
def serve_webfonts(filename):
    return send_from_directory(os.path.join(FRONTEND_DIR, "webfonts"), filename)

@app.route("/api/ohlc")
def api_ohlc():
    symbol = request.args.get("symbol", "").strip()
    interval = request.args.get("interval", "1d").strip()
    range_days = request.args.get("range", "365").strip()
    offset_str = request.args.get("offset", "0").strip()

    if not symbol: return jsonify({"error": "symbol is required"}), 400
    symbol = symbol[:40]
    _bad = set("<>/\\|;&`$ '")
    if any(c in _bad for c in symbol):
        return jsonify({"error": "Invalid symbol"}), 400
    try: range_days = float(range_days)
    except: range_days = 365.0
    try: offset_days = int(offset_str)
    except: offset_days = 0

    # New history path: daily/weekly/monthly Indian symbols serve from the
    # shared price_history cache (scheduler/provider chain), missing ranges
    # fetched once and stored. Other intervals and non-Indian symbols keep the
    # legacy path unchanged.
    if interval in ("1d", "1w", "1M") and is_indian_symbol(symbol):
        try:
            store_symbol = _store_symbol(symbol)
            if range_days <= 31: range_key = "1M"
            elif range_days <= 93: range_key = "3M"
            elif range_days <= 185: range_key = "6M"
            elif range_days <= 370: range_key = "1Y"
            else: range_key = "5Y"
            interval_key = {"1d": "1d", "1w": "1w", "1M": "1M"}[interval]
            result = market_runtime.history.get_history(store_symbol, range_key, interval_key)
            if result.get("candles"):
                meta = get_symbol_metadata(symbol)
                return jsonify({
                    "symbol": symbol, "name": meta["name"], "type": meta["type"],
                    "interval": interval, "candles": result["candles"],
                    "count": len(result["candles"]),
                    "source": result.get("source"),
                    "coverage": result.get("coverage"),
                    "history_start": market_runtime.history.history_start(store_symbol),
                })
        except Exception as exc:
            logger.warning("new history path failed for %s, using legacy: %s", symbol, exc)

    candles = get_ohlc_data(symbol, interval, range_days, offset_days)
    if candles is None or len(candles) == 0:
        return jsonify({"error": f"No data found for {symbol}", "symbol": symbol})
    
    meta = get_symbol_metadata(symbol)
    return jsonify({
        "symbol": symbol, "name": meta["name"], "type": meta["type"], 
        "interval": interval, "candles": candles, "count": len(candles)
    })


@app.route("/api/symbols")
def api_search():
    query = request.args.get("query", "").strip()
    if not query:
        results = []
        seen = set()
        # DB listings first (includes newly discovered stocks).
        try:
            with market_runtime.db.connect() as conn:
                db_rows = conn.execute(
                    "SELECT l.symbol, c.company_name, l.exchange FROM listings l"
                    " JOIN companies c ON c.company_id = l.company_id"
                    " WHERE l.status = 'listed' LIMIT 40"
                ).fetchall()
            for r in db_rows:
                if r["symbol"] not in seen:
                    seen.add(r["symbol"])
                    results.append({"symbol": r["symbol"], "name": r["company_name"],
                                    "exchange": r["exchange"], "type": "stock"})
        except Exception as exc:
            logger.warning("db symbol search unavailable: %s", exc)
        for s in stock_provider.search_all_symbols(""):
            if s["symbol"] not in seen: seen.add(s["symbol"]); results.append(s)
        for c in crypto_provider.COMMON_CRYPTO[:15]:
            if c["symbol"] not in seen: seen.add(c["symbol"]); results.append({"symbol": c["symbol"], "name": c["name"], "exchange": "Binance", "type": "crypto"})
        return jsonify(results[:100])

    all_results = []
    seen = set()
    # DB search: symbol / name / ISIN / BSE code / legacy Yahoo alias.
    try:
        like = f"%{query.upper()}%"
        with market_runtime.db.connect() as conn:
            db_rows = conn.execute(
                "SELECT l.symbol, c.company_name, l.exchange, l.isin, l.bse_code"
                " FROM listings l JOIN companies c ON c.company_id = l.company_id"
                " WHERE UPPER(l.symbol) LIKE ? OR UPPER(c.company_name) LIKE ?"
                "   OR UPPER(l.isin) = ? OR CAST(l.bse_code AS TEXT) = ?"
                "   OR UPPER(l.symbol) IN (SELECT symbol FROM symbol_aliases WHERE UPPER(alias) = ?)"
                " LIMIT 30",
                (like, like, query.upper(), query, query.upper()),
            ).fetchall()
        for r in db_rows:
            if r["symbol"] not in seen:
                seen.add(r["symbol"])
                all_results.append({"symbol": r["symbol"], "name": r["company_name"],
                                    "exchange": r["exchange"], "type": "stock"})
    except Exception as exc:
        logger.warning("db symbol search unavailable: %s", exc)
    all_results += [s for s in stock_provider.search_all_symbols(query) if s["symbol"] not in seen]
    crypto_results = crypto_provider.search_crypto(query)
    return jsonify((all_results + crypto_results)[:100])


def current_user():
    user_id = session.get("user_id")
    return auth_service.get_user(user_id) if user_id else None


@app.route("/api/auth/register", methods=["POST"])
@rate_limited(max_calls=5, window=60)
def api_auth_register():
    data = request.get_json(silent=True) or {}
    result = auth_service.register(data.get("email", ""), data.get("password", ""))
    if not result.get("success"):
        return jsonify(result), 400
    session["user_id"] = result["user"]["id"]
    return jsonify(result)


@app.route("/api/auth/login", methods=["POST"])
@rate_limited(max_calls=10, window=60)
def api_auth_login():
    data = request.get_json(silent=True) or {}
    result = auth_service.login(data.get("email", ""), data.get("password", ""))
    if not result.get("success"):
        return jsonify(result), 401
    session["user_id"] = result["user"]["id"]
    return jsonify(result)


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.pop("user_id", None)
    return jsonify({"success": True})


@app.route("/api/auth/me")
def api_auth_me():
    user = current_user()
    return jsonify({
        "authenticated": bool(user),
        "user": user,
        "subscription": subscription_service.status(user["id"] if user else None),
    })


@app.route("/api/subscription/plans")
def api_subscription_plans():
    return jsonify({"plans": list(subscription_service.plans().values())})


@app.route("/api/subscription/status")
def api_subscription_status():
    user = current_user()
    return jsonify(subscription_service.status(user["id"] if user else None))


@app.route("/api/subscription/razorpay/order", methods=["POST"])
def api_subscription_razorpay_order():
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    result = subscription_service.create_razorpay_order(user["id"], data.get("plan_id", "student_premium"))
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/subscription/razorpay/verify", methods=["POST"])
def api_subscription_razorpay_verify():
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    result = subscription_service.verify_payment(
        user["id"],
        data.get("plan_id", "student_premium"),
        data.get("razorpay_order_id", ""),
        data.get("razorpay_payment_id", ""),
        data.get("razorpay_signature", ""),
    )
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/subscription/razorpay/webhook", methods=["POST"])
def api_subscription_razorpay_webhook():
    raw_body = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not subscription_service.verify_webhook_signature(raw_body, signature):
        return jsonify({"success": False, "error": "Invalid webhook signature"}), 400
    payload = request.get_json(silent=True) or {}
    result = subscription_service.handle_webhook(payload)
    if not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


def validate_indicator_request(payload, require_indicator=True):
    payload = payload or {}
    candles = payload.get("candles")
    if not isinstance(candles, list):
        return None, ("candles must be a list", 400)
    if len(candles) > 10000:
        candles = candles[-10000:]
    indicator_id = (payload.get("indicator") or payload.get("id") or payload.get("type") or "").strip().lower()
    if require_indicator and not indicator_id:
        return None, ("indicator is required", 400)
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return None, ("params must be an object", 400)
    return {"indicator": indicator_id, "candles": candles, "params": params}, None


INDICATOR_META = {
    "sma": {"label": "SMA", "output": "single", "pane": "overlay", "params": {"period": 20, "source": "close"}},
    "ema": {"label": "EMA", "output": "single", "pane": "overlay", "params": {"period": 20, "source": "close"}},
    "wma": {"label": "WMA", "output": "single", "pane": "overlay", "params": {"period": 20, "source": "close"}},
    "hma": {"label": "HMA", "output": "single", "pane": "overlay", "params": {"period": 20}},
    "dema": {"label": "DEMA", "output": "single", "pane": "overlay", "params": {"period": 20}},
    "tema": {"label": "TEMA", "output": "single", "pane": "overlay", "params": {"period": 20}},
    "kama": {"label": "KAMA", "output": "single", "pane": "overlay", "params": {"period": 10, "fast_period": 2, "slow_period": 30}},
    "zlema": {"label": "ZLEMA", "output": "single", "pane": "overlay", "params": {"period": 20}},
    "vwap": {"label": "VWAP", "output": "single", "pane": "overlay", "params": {}},
    "linearreg": {"label": "Linear Reg", "output": "single", "pane": "overlay", "params": {"period": 20}},
    "ao": {"label": "Awesome Osc", "output": "single", "pane": "separate", "params": {}},
    "trix": {"label": "TRIX", "output": "single", "pane": "separate", "params": {"period": 18}},
    "supertrend": {"label": "Supertrend", "output": "single", "pane": "overlay", "params": {"period": 10, "multiplier": 3.0}},
    "ichimoku": {"label": "Ichimoku", "output": "multi", "pane": "overlay", "params": {"conv_period": 9, "base_period": 26, "span_b_period": 52, "displacement": 26}},
    "psar": {"label": "PSAR", "output": "single", "pane": "overlay", "params": {"start": 0.02, "increment": 0.02, "max_af": 0.20}},
    "heikinashi": {"label": "Heikin-Ashi", "output": "candle", "pane": "overlay", "params": {}},
    "pivotpoints": {"label": "Pivot Points", "output": "multi", "pane": "overlay", "params": {"pivot_type": "standard", "lookback_bars": 24}},
    "rsi": {"label": "RSI", "output": "single", "pane": "separate", "params": {"period": 14}},
    "macd": {"label": "MACD", "output": "multi", "pane": "separate", "params": {"fast": 12, "slow": 26, "signal": 9}},
    "stoch": {"label": "Stochastic", "output": "multi", "pane": "separate", "params": {"k_period": 14, "smooth": 3, "d_period": 3}},
    "stochrsi": {"label": "StochRSI", "output": "multi", "pane": "separate", "params": {"rsi_period": 14, "stoch_period": 14, "k_smooth": 3, "d_smooth": 3}},
    "cci": {"label": "CCI", "output": "single", "pane": "separate", "params": {"period": 20}},
    "williamsr": {"label": "Williams %R", "output": "single", "pane": "separate", "params": {"period": 14}},
    "roc": {"label": "ROC", "output": "single", "pane": "separate", "params": {"period": 12}},
    "momentum": {"label": "Momentum", "output": "single", "pane": "separate", "params": {"period": 10}},
    "adx": {"label": "ADX", "output": "multi", "pane": "separate", "params": {"period": 14}},
    "wavetrend": {"label": "WaveTrend", "output": "multi", "pane": "separate", "params": {"n1": 10, "n2": 21}},
    "tsi": {"label": "TSI", "output": "single", "pane": "separate", "params": {"long_period": 25, "short_period": 13}},
    "cmo": {"label": "CMO", "output": "single", "pane": "separate", "params": {"period": 14}},
    "uo": {"label": "Ultimate Osc", "output": "single", "pane": "separate", "params": {"period1": 7, "period2": 14, "period3": 28}},
    "aroon": {"label": "Aroon", "output": "multi", "pane": "separate", "params": {"period": 25}},
    "vortex": {"label": "Vortex", "output": "multi", "pane": "separate", "params": {"period": 14}},
    "bollinger": {"label": "Bollinger", "output": "multi", "pane": "overlay", "params": {"period": 20, "std_dev": 2.0}},
    "atr": {"label": "ATR", "output": "single", "pane": "separate", "params": {"period": 14}},
    "keltner": {"label": "Keltner", "output": "multi", "pane": "overlay", "params": {"period": 20, "atr_multiplier": 2.0}},
    "donchian": {"label": "Donchian", "output": "multi", "pane": "overlay", "params": {"period": 20}},
    "obv": {"label": "OBV", "output": "single", "pane": "separate", "params": {}},
    "mfi": {"label": "MFI", "output": "single", "pane": "separate", "params": {"period": 14}},
    "cmf": {"label": "CMF", "output": "single", "pane": "separate", "params": {"period": 20}},
}


@app.route("/api/indicator", methods=["POST"])
def api_indicator_post():
    req, error = validate_indicator_request(request.json)
    if error:
        message, status = error
        return jsonify({"status": "error", "error": message}), status
    params = dict(req["params"])
    period = params.pop("period", 20)
    result = indicators.compute_indicator(req["indicator"], req["candles"], period=period, **params)
    signal = indicators.compute_signal(req["indicator"], result)
    return jsonify({"status": "ok", "indicator": req["indicator"], "signal": signal, "data": result})


@app.route("/api/indicator/signal", methods=["POST"])
def indicator_signal():
    try:
        req = request.get_json(force=True) or {}
        indicator = req.get("indicator", "")
        candles = req.get("candles", [])
        params = dict(req.get("params") or {})
        period = params.pop("period", None)
        data = indicators.compute_indicator(indicator, candles, period=period, **params)
        signal = indicators.compute_signal(indicator, data)
        return jsonify({"status": "ok", "indicator": indicator, "signal": signal})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/sr_levels", methods=["POST"])
def api_sr_levels_post():
    req, error = validate_indicator_request(request.json, require_indicator=False)
    if error:
        message, status = error
        return jsonify({"status": "error", "error": message}), status
    interval = (request.json or {}).get("interval", "1d")
    params = dict(req["params"])
    result = indicators.compute_sr_levels(req["candles"], interval=interval, **params)
    return jsonify({"status": "ok", "data": result})

@app.route("/api/indicator/batch", methods=["POST"])
def api_indicator_batch_post():
    payload = request.json or {}
    candles = payload.get("candles")
    requested = payload.get("indicators")
    if not isinstance(candles, list):
        return jsonify({"status": "error", "error": "candles must be a list"}), 400
    if not isinstance(requested, list):
        return jsonify({"status": "error", "error": "indicators must be a list"}), 400

    results = {}
    for item in requested:
        if not isinstance(item, dict):
            continue
        indicator_id = (item.get("id") or item.get("indicator") or item.get("type") or "").strip().lower()
        if not indicator_id:
            continue
        params = item.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        if indicator_id == "sr_levels":
            data = indicators.compute_sr_levels(candles, interval=payload.get("interval", "1d"), **params)
        else:
            params = dict(params)
            period = params.pop("period", 20)
            data = indicators.compute_indicator(indicator_id, candles, period=period, **params)
        results[indicator_id] = {"data": data, "signal": indicators.compute_signal(indicator_id, data)}
    return jsonify({"status": "ok", "results": results})


@app.route("/api/indicator/meta", methods=["GET"])
def indicator_meta():
    return jsonify({"status": "ok", "indicators": INDICATOR_META})


@app.route("/api/indicators")
def api_indicators():
    symbol = request.args.get("symbol", "").strip()
    interval = request.args.get("interval", "1h").strip()
    indicator_type = request.args.get("type", "sma").strip().lower()
    period = request.args.get("period", "20").strip()
    if not symbol: return jsonify({"error": "symbol is required"}), 400
    try: period = int(period)
    except: period = 20

    range_days = range_for_interval(interval, indicator_type)

    candles = get_ohlc_data(symbol, interval, range_days)

    if not candles:
        return jsonify({"error": "No data available"}), 404

    if indicator_type == "sr_levels":
        if len(candles) < 20:
            return jsonify({"error": "Insufficient data"}), 400
        result = indicators.compute_sr_levels(candles, interval=interval, lookback=period)
        return jsonify(result)

    extra_params = request.args.to_dict(flat=True)
    for key in ("symbol", "interval", "type"):
        extra_params.pop(key, None)
    extra_params.pop("period", None)
    result = indicators.compute_indicator(indicator_type, candles, period=period, **extra_params)
    return jsonify(result)
@app.route("/api/patterns")
def api_patterns():
    symbol = request.args.get("symbol", "BTCUSDT").strip()
    interval = request.args.get("interval", "1d").strip()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400
    try:
        account_size = float(request.args.get("account_size", 100000))
        risk_pct = float(request.args.get("risk_pct", 1.0))
        lot_size = int(float(request.args.get("lot_size", 1)))
        max_patterns = int(float(request.args.get("max_patterns", 120)))
        lookback = int(float(request.args.get("lookback", 120)))
        min_confidence = int(float(request.args.get("min_confidence", 40)))
        live_bar_index = request.args.get("live_bar_index")
        live_bar_index = int(live_bar_index) if live_bar_index not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid risk parameters"}), 400
    anchor_mode = request.args.get("anchor_mode", "current").strip().lower()
    anchor_date = request.args.get("anchor_date") or None
    if anchor_mode not in {"current", "date"}:
        anchor_mode = "current"
    lookback = max(1, min(lookback, 3000))
    min_confidence = max(0, min(min_confidence, 100))

    range_map = {
        "1s": 1, "5s": 3, "15s": 5, "30s": 7,
        "1m": 5, "3m": 7, "5m": 14, "15m": 30, "30m": 60, "45m": 90,
        "1h": 180, "4h": 730, "1d": 3650, "1D": 3650, "1w": 3650, "1W": 3650,
        "1mo": 3650, "1M": 3650,
    }
    range_days = range_map.get(interval, 3650)
    candles = get_ohlc_data(symbol, interval, range_days)
    if not candles or len(candles) < 60:
        return jsonify({"patterns": [], "total": 0, "timeframe": interval, "symbol": symbol, "scanned_bars": len(candles or [])})

    from engine.risk_engine import RiskEngine
    from engine.scanner import LivePatternScanner

    scanner = LivePatternScanner(
        symbol=symbol,
        interval=interval,
        live_bar_index=live_bar_index,
        lookback=lookback,
        anchor_mode=anchor_mode,
        anchor_date=anchor_date,
        min_confidence=min_confidence,
    )
    normalized_candles = scanner._normalize_candles(candles)[-3000:]
    patterns = scanner.scan(candles)
    result = []
    max_patterns = max(1, min(max_patterns, 300))
    for p in patterns[:max_patterns]:
        p_dict = p.to_dict(normalized_candles)
        if p.breakout_price is not None:
            p_dict["risk"] = RiskEngine.compute(p, p.breakout_price, account_size, risk_pct, lot_size).__dict__
        result.append(p_dict)
    return jsonify({
        "patterns": result,
        "total": len(result),
        "timeframe": interval,
        "symbol": symbol,
        "scanned_bars": len(normalized_candles),
        "scan_window": {
            "start_index": scanner.scan_start_index,
            "end_index": scanner.scan_end_index,
            "lookback": lookback,
            "anchor_mode": anchor_mode,
            "anchor_date": anchor_date,
        },
        "scan_time_ms": scanner.scan_time_ms,
    })


@app.route("/api/patterns/risk", methods=["POST"])
def api_patterns_risk():
    data = request.get_json(silent=True) or {}
    pattern_data = data.get("pattern")
    pattern_id = data.get("pattern_id")
    symbol = data.get("symbol") or request.args.get("symbol", "BTCUSDT")
    interval = data.get("interval") or request.args.get("interval", "1d")
    account_size = float(data.get("account_size", request.args.get("account_size", 100000)))
    risk_pct = float(data.get("risk_pct", request.args.get("risk_pct", 1.0)))
    lot_size = int(float(data.get("lot_size", request.args.get("lot_size", 1))))

    from engine.primitives import PatternCandidate, PatternTarget
    from engine.risk_engine import RiskEngine
    from engine.scanner import LivePatternScanner

    if pattern_data:
        target = pattern_data.get("targets") or {}
        pattern = PatternCandidate(
            id=pattern_data.get("id", "PATTERN"),
            name=pattern_data.get("name", "Pattern"),
            family=pattern_data.get("family", "classic"),
            type=pattern_data.get("type", "event"),
            bias=pattern_data.get("bias", "neutral"),
            state=pattern_data.get("state", "emerging"),
            start_index=int(pattern_data.get("start_index", 0)),
            end_index=int(pattern_data.get("end_index", 0)),
            pivots=[],
            upper_line=pattern_data.get("upper_line"),
            lower_line=pattern_data.get("lower_line"),
            breakout_index=pattern_data.get("breakout_index"),
            breakout_price=pattern_data.get("breakout_price"),
            stop_loss=float(pattern_data.get("stop_loss", 0)),
            targets=PatternTarget(float(target.get("classical", 0)), float(target.get("conservative", 0)), float(target.get("bulkowski", 0))),
            confidence=int(pattern_data.get("confidence", 0)),
            reasons=pattern_data.get("reasons", []),
        )
        entry = float(data.get("entry_price", pattern.breakout_price or 0))
        return jsonify(RiskEngine.compute(pattern, entry, account_size, risk_pct, lot_size).__dict__)

    if not pattern_id:
        return jsonify({"error": "pattern_id or pattern is required"}), 400
    candles = get_ohlc_data(symbol, interval, 3650)
    scanner = LivePatternScanner(symbol=symbol, interval=interval)
    matches = [p for p in scanner.scan(candles) if p.id == pattern_id]
    if not matches:
        return jsonify({"error": "pattern not found"}), 404
    p = matches[0]
    entry = float(data.get("entry_price", p.breakout_price or 0))
    return jsonify(RiskEngine.compute(p, entry, account_size, risk_pct, lot_size).__dict__)

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    data = request.json
    symbol = data.get("symbol")
    interval = data.get("interval", "1d")
    strategy_config = data.get("strategy")
    
    if not symbol or not strategy_config:
        return jsonify({"error": "symbol and strategy are required"}), 400
        
    candles = get_ohlc_data(symbol, interval, 365)
    if not candles:
        return jsonify({"error": "No data found for backtest"}), 404
        
    try:
        bt = Backtester(candles)
        results = bt.run(strategy_config)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/avwap")
def api_avwap():
    symbol = request.args.get("symbol", "").strip()
    interval = request.args.get("interval", "1d").strip()
    anchor_time = request.args.get("anchor", "").strip()
    if not symbol or not anchor_time:
        return jsonify({"error": "symbol and anchor are required"}), 400
    
    try: anchor_ts = int(anchor_time)
    except: return jsonify({"error": "invalid anchor timestamp"}), 400
    
    candles = get_ohlc_data(symbol, interval, 365)
    if not candles: return jsonify({"error": "No data"}), 404
    
    data = pattern_detector.calculate_avwap(candles, anchor_ts)
    return jsonify({"symbol": symbol, "anchor": anchor_ts, "data": data})

@app.route("/api/quote")
def api_quote():
    symbol = request.args.get("symbol", "").strip()
    if not symbol: return jsonify({"error": "symbol is required"}), 400
    
    meta = get_symbol_metadata(symbol)
    crypto_mode = is_crypto_symbol(symbol)
    
    if crypto_mode:
        tick = crypto_provider.fetch_live_price(symbol)
        if tick:
            return jsonify({
                "symbol": symbol, "name": meta["name"], "type": meta["type"],
                "price": tick["price"], "change": tick.get("change", 0),
                "changePercent": tick.get("changePercent", 0), "high": tick.get("high", tick["price"]),
                "low": tick.get("low", tick["price"]), "volume": tick["volume"], "open": tick.get("open", tick["price"]),
                "previousClose": tick["price"] - tick.get("change", 0),
                "session": "REGULAR", "isIndian": False
            })
    else:
        quote = stock_provider.get_live_quote(symbol)
        if quote:
            is_indian = is_indian_symbol(symbol)
            if is_indian:
                session, _holiday = get_market_session()
            else:
                # For non-indian stocks (Global), use is_open flag
                session = "REGULAR" if quote.get("is_open", True) else "CLOSED"
                
            return jsonify({
                "symbol": symbol,
                "price": round(quote["price"], 2), 
                "preMarketPrice": quote.get("preMarketPrice"),
                "postMarketPrice": quote.get("postMarketPrice"),
                "change": round(quote["change"], 2),
                "changePercent": round(quote["changePercent"], 2), 
                "high": round(quote["high"], 2),
                "low": round(quote["low"], 2), "volume": quote["volume"], "open": round(quote["open"], 2),
                "previousClose": round(quote["previousClose"], 2),
                "is_open": quote.get("is_open", True),
                "session": session,
                "isIndian": is_indian
            })
        
        # Fallback to older method if get_live_quote fails
        candles = stock_provider.fetch_ohlc(symbol, "1d", 5)
        if candles and len(candles) > 0:
            last = candles[-1]; prev = candles[-2] if len(candles) > 1 else last
            price = last["close"]; change = round(price - prev["close"], 2)
            change_pct = round(change / prev["close"] * 100, 2) if prev["close"] != 0 else 0
            return jsonify({
                "symbol": symbol, "name": meta["name"], "type": meta["type"],
                "price": price, "change": change, "changePercent": change_pct,
                "high": last["high"], "low": last["low"], "volume": last["volume"], "open": last["open"],
                "previousClose": prev["close"],
                "session": get_market_session()[0] if is_indian_symbol(symbol) else "REGULAR",
                "isIndian": is_indian_symbol(symbol)
            })
    return jsonify({"error": "No data"}), 404


# Option Chain Routes
@app.route("/api/option-chain")
def api_option_chain():
    symbol = request.args.get("symbol", "NIFTY").strip().upper()
    expiry = request.args.get("expiry", None)
    strikes = int(request.args.get("strikes", 20))
    force_refresh = request.args.get("refresh", "").lower() in ("1", "true", "yes")
    strikes = max(5, min(strikes, 75))

    # Triggered reload for robust NSE client changes
    try:
        result = option_chain_svc.get_option_chain(symbol, expiry, strikes, force_refresh=force_refresh)
        if "error" in result:
            return jsonify(result), 404
        resp = jsonify(result)
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp
    except Exception as e:
        logger.error(f"Option Chain API Error: {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": str(e)}), 500


@app.route("/api/option-chain/expiries")
def api_option_chain_expiries():
    symbol = request.args.get("symbol", "NIFTY").strip().upper()
    expiries = option_chain_svc.get_expiry_dates(symbol)
    return jsonify({"symbol": symbol, "expiry_dates": expiries})


@app.route("/api/option-chain/historical-oi")
def api_option_chain_hist_oi():
    symbol = request.args.get("symbol", "NIFTY").strip().upper()
    expiry = request.args.get("expiry", "").strip()
    strike = float(request.args.get("strike", 0))
    if not expiry or not strike:
        return jsonify({"error": "expiry and strike required"}), 400
    result = option_chain_svc.get_historical_oi(symbol, expiry, strike)
    return jsonify(result)


def _apply_option_mbp_update(chain_data: dict, mbp_row: dict) -> bool:
    """Patch one NSE option-chain WebSocket market-depth row into a full chain snapshot."""
    if not chain_data or not mbp_row:
        return False

    try:
        strike = float(mbp_row.get("strikePrice"))
    except (TypeError, ValueError):
        return False

    row = None
    for item in chain_data.get("strikes", []) or []:
        if float(item.get("strike", 0)) == strike:
            row = item
            break
    if not row:
        return False

    updated = False
    for source_key, target_key in (("CE", "call"), ("PE", "put")):
        source = mbp_row.get(source_key)
        target = row.get(target_key)
        if not source or not target:
            continue

        bid = float(source.get("buyPrice1") or target.get("bid") or 0)
        ask = float(source.get("sellPrice1") or target.get("ask") or 0)
        ltp = float(source.get("lastPrice") or target.get("ltp") or 0)
        premium = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else round(ltp, 2)
        lot_size = int(target.get("lot_size") or chain_data.get("lot_size") or 1)

        target["ltp"] = round(ltp, 2)
        target["premium"] = premium
        target["lot_size"] = lot_size
        target["buy_funds"] = round((ask if ask > 0 else premium) * lot_size, 2)
        target["sell_value"] = round((bid if bid > 0 else premium) * lot_size, 2)
        target["bid"] = round(bid, 2)
        target["ask"] = round(ask, 2)
        target["change"] = round(float(source.get("change") or target.get("change") or 0), 2)
        target["volume"] = int(float(source.get("totalTradedVolume") or target.get("volume") or 0))
        updated = True

    if updated:
        _recompute_option_mark_prices(chain_data)
        chain_data["timestamp"] = int(time.time())
        chain_data["stream_ts"] = mbp_row.get("timestamp")
    return updated


def _recompute_option_mark_prices(chain_data: dict) -> None:
    """Recompute display premiums from latest spot + IV so rows move between LTP prints."""
    if not chain_data:
        return
    try:
        spot = float(chain_data.get("spot") or 0)
        T = time_to_expiry_years(chain_data.get("expiry"))
    except (TypeError, ValueError):
        return
    if spot <= 0:
        return

    for row in chain_data.get("strikes", []) or []:
        try:
            strike = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if strike <= 0:
            continue

        for side_name, opt_type in (("call", "call"), ("put", "put")):
            side = row.get(side_name) or {}
            sigma = float(side.get("iv") or 0) / 100
            mark = _option_price(spot, strike, T, 0.065, sigma, opt_type) if sigma > 0 else 0
            if mark > 0:
                side["mark"] = round(mark, 2)
                side["premium"] = side["mark"]
                lot_size = int(side.get("lot_size") or chain_data.get("lot_size") or 1)
                side["lot_size"] = lot_size
                side["buy_funds"] = round(side["premium"] * lot_size, 2)
                side["sell_value"] = round(side["premium"] * lot_size, 2)


@app.route("/api/option-chain/stream")
def api_option_chain_stream():
    symbol = request.args.get("symbol", "NIFTY").strip().upper()
    expiry = request.args.get("expiry", None)
    strikes = int(request.args.get("strikes", 20))
    strikes = max(5, min(strikes, 75))

    def generate():
        """SSE stream for the option chain.

        Strategy: serve snapshots from the per-symbol background worker
        (option_chain_service._bg_snapshots) which already refreshes at
        ~1s cadence. We do NOT open a second NSE WebSocket here — that path
        is the one hitting 403 from Akamai once the session cookies go stale,
        and the background worker is the authoritative refresh loop.

        When a WS message does arrive and can be applied, we patch the
        in-memory snapshot in place so downstream clients see mark-price
        updates immediately without waiting for the next background fetch.
        """
        # `expiry` is rebound below, so work on a local copy (reading the closure
        # variable and then assigning it raised UnboundLocalError and killed the stream).
        expiry_cur = expiry
        chain_data = None
        last_heartbeat = time.time()
        next_refresh = 0.0
        ws = None
        # NSE's streamer answers 403 to non-browser clients. We try it at most
        # ONCE per stream (and only if explicitly enabled); a 403 is terminal,
        # never retried in a loop. The 2 s snapshot feed below does not need it.
        ws_allowed = OC_ENABLE_NSE_WS
        ws_params = {"symbol": symbol}
        if expiry_cur:
            ws_params["expiry"] = expiry_cur
        ws_url = "wss://streamer.nseindia.com/streams/fo/mbp?" + urlencode(ws_params)

        try:
            while True:
                now = time.time()
                if now >= next_refresh:
                    raw = option_chain_svc.get_option_chain(symbol, expiry_cur, strikes, force_refresh=True)
                    if raw and not raw.get("error"):
                        chain_data = raw
                        if not expiry_cur:
                            expiry_cur = chain_data.get("expiry") or expiry_cur
                        _recompute_option_mark_prices(chain_data)
                        chain_data["timestamp"] = int(now)
                        yield f"data: {json.dumps({'type': 'option_chain', **chain_data}, default=str)}\n\n"
                        last_heartbeat = now
                        next_refresh = now + OC_REFRESH_SECONDS
                    else:
                        next_refresh = now + 1.0

                if ws_allowed and chain_data and ws is None:
                    ws_allowed = False  # single attempt, no retry loop
                    try:
                        ws = websocket.create_connection(
                            ws_url,
                            timeout=8,
                            header=[
                                "Origin: https://www.nseindia.com",
                                "User-Agent: future-app/1.0 (market data client)",
                            ],
                        )
                        ws.settimeout(0.25)
                        logger.info("Option chain WS connected: %s", ws_url)
                    except Exception as exc:
                        logger.info("Option chain WS unavailable (%s); using %.0fs snapshot feed",
                                    str(exc).split(" -+-+- ")[0], OC_REFRESH_SECONDS)
                        ws = None

                if ws is not None:
                    try:
                        raw_msg = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        raw_msg = None
                    except Exception:
                        ws = None
                        raw_msg = None
                    if raw_msg:
                        try:
                            mbp_row = json.loads(raw_msg)
                        except (ValueError, TypeError):
                            mbp_row = None
                        if mbp_row and _apply_option_mbp_update(chain_data, mbp_row):
                            chain_data["timestamp"] = int(time.time())
                            yield f"data: {json.dumps({'type': 'option_chain', **chain_data}, default=str)}\n\n"
                            last_heartbeat = time.time()
                else:
                    time.sleep(0.25)

                if chain_data is None:
                    time.sleep(0.1)
                    continue

                if time.time() - last_heartbeat >= 1.0:
                    last_heartbeat = time.time()
                    yield f"data: {json.dumps({'type': 'heartbeat', 'ts': int(last_heartbeat)})}\n\n"

        except GeneratorExit:
            logger.info("Stream connection closed for %s", symbol)
        except Exception as exc:
            logger.error("Option chain stream error for %s: %s", symbol, exc)
            # Never leak upstream headers/cookies to the browser.
            yield f"data: {json.dumps({'type': 'error', 'message': 'Live feed interrupted, retrying'})}\n\n"
            time.sleep(1)
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def interval_ms(interval: str) -> int:
    map_ms = {"1s": 1000, "5s": 5000, "15s": 15000, "30s": 30000, "1m": 60000, "2m": 120000, "5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000, "4h": 14400000, "1d": 86400000, "1w": 604800000, "1M": 2592000000}
    if interval in map_ms: return map_ms[interval]
    match = re.match(r"^(\d+)([smhdwM])", interval.strip())
    if match:
        num = int(match.group(1)); unit = match.group(2)
        multipliers = {"s": 1000, "m": 60000, "h": 3600000, "d": 86400000, "w": 604800000, "M": 2592000000}
        return num * multipliers.get(unit, 60000)
    return 86400000


def range_for_interval(interval: str, indicator_type: str = ""):
    """Align custom timeframe history with the closest standard timeframe."""
    sec = max(interval_ms(interval) // 1000, 1)
    if sec < 60:
        range_days = 5
    elif sec < 300:
        range_days = 7
    elif sec < 1800:
        range_days = 60
    elif sec < 3600:
        range_days = 90
    elif sec < 86400:
        range_days = 730
    else:
        range_days = 3650

    if indicator_type in ["sr_levels", "ichimoku", "pivotpoints"]:
        range_days = max(range_days, 1000)
    return range_days


def parse_custom_interval(interval: str):
    ms = interval_ms(interval); sec = ms // 1000
    
    # Custom display logic to keep 'm' lowercase for minutes and 'M' uppercase for months
    def format_display(s):
        if not s: return ""
        if s.endswith('m'): return s # keep minutes as 'm'
        return s.upper()

    standard_map = {60: "1m", 120: "2m", 300: "5m", 900: "15m", 1800: "30m", 3600: "1h", 86400: "1d", 604800: "1w", 2592000: "1M"}
    if sec in standard_map: return {"display": format_display(interval), "ms": ms, "bestSourceInterval": standard_map[sec], "needsAggregation": False}
    candidates = [
        (2592000, "1M"), (604800, "1w"), (86400, "1d"), (14400, "4h"),
        (3600, "1h"), (1800, "30m"), (900, "15m"), (300, "5m"),
        (120, "2m"), (60, "1m")
    ]
    for source_sec, source_interval in candidates:
        if sec > source_sec and sec % source_sec == 0:
            return {"display": format_display(interval), "ms": ms, "bestSourceInterval": source_interval, "needsAggregation": True}
    if sec < 60: return {"display": format_display(interval), "ms": ms, "bestSourceInterval": "1m", "needsAggregation": True}
    return {"display": format_display(interval), "ms": ms, "bestSourceInterval": "1d", "needsAggregation": True}


@app.route("/api/stream")
def api_stream():
    symbol = request.args.get("symbol", "").strip()
    interval = request.args.get("interval", "1m").strip()
    if not symbol: return jsonify({"error": "symbol required"}), 400
    symbol = symbol[:40]
    _bad = set("<>/\\|;&`$ '")
    if any(c in _bad for c in symbol):
        return jsonify({"error": "Invalid symbol"}), 400
    crypto_mode = is_crypto_symbol(symbol)
    interval_info = parse_custom_interval(interval)
    source_interval = interval_info["bestSourceInterval"]
    interval_sec = max(interval_info["ms"] // 1000, 1)

    def binance_stream_symbol(value: str) -> str:
        s = value.strip().upper()
        if not any(s.endswith(suf) for suf in ("USDT", "USDC", "BUSD", "FDUSD", "BTC", "ETH", "BNB")):
            s = f"{s}USDT"
        return s.lower()

    def rounded_time_for_tick(ts: int) -> int:
        ist_offset = 19800
        if interval_sec >= 86400:
            return ((ts + ist_offset) // interval_sec) * interval_sec - ist_offset
        return (ts // interval_sec) * interval_sec

    def generate_crypto_stream():
        ws = None
        last_tick_key = None
        last_heartbeat = time.time()
        baseline = crypto_provider.fetch_live_price(symbol) or {}
        prev_close = float(baseline.get("price", 0)) - float(baseline.get("change", 0) or 0)
        stream_symbol = binance_stream_symbol(symbol)
        ws_url = f"wss://stream.binance.com:9443/ws/{stream_symbol}@trade"

        while True:
            try:
                if ws is None:
                    ws = websocket.create_connection(ws_url, timeout=10)
                    ws.settimeout(1)
                    logger.info("Crypto trade WS connected: %s", ws_url)

                try:
                    raw_msg = ws.recv()
                except websocket.WebSocketTimeoutException:
                    if time.time() - last_heartbeat >= 10:
                        yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': int(time.time())})}\n\n"
                        last_heartbeat = time.time()
                    continue

                msg = json.loads(raw_msg)
                price = float(msg.get("p"))
                volume = float(msg.get("q", 0))
                ts = int((msg.get("T") or msg.get("E") or int(time.time() * 1000)) / 1000)
                trade_id = msg.get("t")
                tick_key = (trade_id, price, ts)
                if tick_key == last_tick_key:
                    continue
                last_tick_key = tick_key
                yield f"data: {json.dumps({'type': 'tick', 'symbol': symbol, 'price': price, 'volume': volume, 'timestamp': ts, 'timeRounded': rounded_time_for_tick(ts), 'prevClose': prev_close})}\n\n"

            except GeneratorExit:
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass
                break
            except Exception as exc:
                logger.warning("Crypto trade WS failed for %s, falling back briefly: %s", symbol, exc)
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass
                    ws = None
                tick = crypto_provider.fetch_live_price(symbol)
                if tick:
                    price = float(tick["price"])
                    ts = int(tick.get("timestamp") or time.time())
                    tick_key = ("rest", price, ts)
                    if tick_key != last_tick_key:
                        last_tick_key = tick_key
                        if not prev_close:
                            prev_close = price - float(tick.get("change", 0) or 0)
                        yield f"data: {json.dumps({'type': 'tick', 'symbol': symbol, 'price': price, 'volume': tick.get('volume', 0), 'timestamp': ts, 'timeRounded': rounded_time_for_tick(ts), 'prevClose': prev_close})}\n\n"
                time.sleep(0.5)
    
    # Start background poller for this symbol (non-crypto)
    if not crypto_mode:
        _ensure_poller(symbol)
        _register_subscriber(symbol)

    def generate():
        if crypto_mode:
            yield from generate_crypto_stream()
            return

        prev_close = None
        last_heartbeat = time.time()
        last_tick_key = None
        while True:
            try:
                now = time.time()
                # 1. Heartbeat every 15s to prevent silent drops
                if now - last_heartbeat > 15:
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': int(now)})}\n\n"
                    last_heartbeat = now

                # 2. Fetch Data
                ist_offset = 19800
                if crypto_mode:
                    tick = crypto_provider.fetch_live_price(symbol)
                    if tick:
                        price = tick["price"]
                        if prev_close is None: prev_close = price - tick.get("change", 0)
                        ts = int(tick.get("timestamp") or time.time())
                        if interval_sec >= 86400:
                            ts_rounded = ((ts + ist_offset) // interval_sec) * interval_sec - ist_offset
                        else:
                            ts_rounded = (ts // interval_sec) * interval_sec
                        tick_key = (round(float(price), 8), ts)
                        if tick_key == last_tick_key:
                            time.sleep(0.1)
                            continue
                        last_tick_key = tick_key
                        yield f"data: {json.dumps({'type': 'tick', 'symbol': symbol, 'price': price, 'volume': tick['volume'], 'timestamp': ts, 'timeRounded': ts_rounded, 'prevClose': prev_close})}\n\n"
                else:
                    # Read from background poller cache (updated every 500ms)
                    quote = _get_cached_quote(symbol)
                    
                    if quote:
                        price = quote["price"]
                        if prev_close is None:
                            prev_close = quote["previousClose"]

                        session = quote.get("session") or ("REGULAR" if quote.get("is_open", True) else "CLOSED")
                        if session in ("PRE_MARKET", "REGULAR", "POST_MARKET"):
                            ts = int(time.time())
                        else:
                            ts = int(quote.get("timestamp") or time.time())
                            
                        if interval_sec >= 86400:
                            is_indian = symbol.endswith(".NS") or symbol.endswith(".BO") or symbol.startswith("^NSE") or symbol.startswith("^BSE")
                            if is_indian:
                                # Align to IST midnight (18:30 UTC of the previous UTC day)
                                # This matches yfinance's historical daily candle timestamps
                                ts_rounded = ((ts + ist_offset) // 86400) * 86400 - ist_offset
                            else:
                                ts_rounded = (ts // 86400) * 86400
                        else:
                            ts_rounded = (ts // interval_sec) * interval_sec

                        # ⚡ Dedup by price only (not timestamp) — but always re-emit
                        # at least every 2s so the frontend candle doesn't appear frozen.
                        price_key = round(float(price), 2)
                        time_since_last = now - (last_tick_key[1] if last_tick_key else 0)
                        if price_key == (last_tick_key[0] if last_tick_key else None) and time_since_last < 2.0:
                            time.sleep(0.1)
                            continue
                        last_tick_key = (price_key, now)

                        # ⚡ Only include OHLC for daily/weekly/monthly intervals.
                        # For intraday: NSE quote open/high/low = ENTIRE DAY range, not candle range.
                        # Including them in intraday ticks would make every 1m/5m candle
                        # show the day's full high/low instead of the candle's own range.
                        if interval_sec >= 86400:
                            ohlc_fields = {
                                'open':  round(float(quote.get('open',  price)), 2),
                                'high':  round(float(quote.get('high',  price)), 2),
                                'low':   round(float(quote.get('low',   price)), 2),
                            }
                        else:
                            ohlc_fields = {}  # intraday: let frontend track from price extremes

                        _tick_payload = {
                            'type': 'tick',
                            'symbol': symbol,
                            'price': round(price, 2),
                            **ohlc_fields,
                            'volume': quote.get('volume', 0),
                            'timestamp': ts,
                            'timeRounded': ts_rounded,
                            'prevClose': prev_close,
                            'is_open': quote.get('is_open', True),
                            'session': session,
                        }
                        yield 'data: ' + json.dumps(_tick_payload) + '\n\n'
                
                time.sleep(0.1)  # tight loop — poller handles NSE rate
            except GeneratorExit:
                if not crypto_mode:
                    _unregister_subscriber(symbol)
                break
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                time.sleep(5)
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

@app.route("/api/ohlc-custom")
def api_ohlc_custom(): return api_ohlc()

@app.route("/api/screener", methods=["POST"])
def api_screener():
    filters = request.json or {}
    try:
        results = run_screener(filters)
        return jsonify(results)
    except Exception as exc:
        logger.error("Screener error: %s", exc, exc_info=True)
        return jsonify({"error": "Screener failed"}), 500

# SMTP settings from environment variables
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")

def send_email_notification(to_email, alert):
    if not SMTP_USER or not SMTP_PASS:
        print(f"SMTP not configured in .env file. Would send email to {to_email} for alert {alert['symbol']}")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = to_email
        msg['Subject'] = f"Future App Alert: {alert['symbol']} Triggered"
        
        body = f"Your alert for {alert['symbol']} has been triggered!\n\n"
        body += f"Condition: {alert['condition']} {alert['value']}\n"
        body += f"Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(alert['triggered_at']))}"
        
        msg.attach(MIMEText(body, 'plain'))
        
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print(f"Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

@app.route("/api/alerts", methods=["POST"])
def api_create_alert():
    """Legacy shape, durable storage: rows live in user_price_alerts and are
    evaluated by the scheduler against real stored quotes."""
    alert_data = request.json
    if not alert_data or "symbol" not in alert_data:
        return jsonify({"error": "Missing alert data"}), 400
    try:
        alert = _market_runtime.alerts.create_price_alert(
            symbol=str(alert_data.get("symbol") or ""),
            condition=str(alert_data.get("condition") or ""),
            value=alert_data.get("value"),
            email=alert_data.get("email") or None,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"success": True, "alert": alert})

@app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
def api_delete_alert(alert_id):
    if _market_runtime.alerts.delete_price_alert(alert_id):
        return jsonify({"success": True})
    return jsonify({"error": "Alert not found"}), 404

@app.route("/api/check-alerts")
def api_check_alerts():
    all_alerts = _market_runtime.alerts.list_price_alerts()
    triggered = [a for a in all_alerts if a.get("triggered")]
    return jsonify({"alerts": all_alerts, "triggered": triggered})

@app.route("/api/ai/analyze")
def api_ai_analyze():
    symbol = request.args.get("symbol", "").strip()
    interval = request.args.get("interval", "1h").strip()
    if not symbol: return jsonify({"error": "symbol required"}), 400
    
    # 1. Fetch deep history for engineering (at least 300 candles)
    candles = get_ohlc_data(symbol, interval, 365)
    if not candles: return jsonify({"error": "No data"}), 404
    
    # 2. Get S&R context
    sr_levels = indicators.compute_sr_levels(candles, interval)
    
    # 3. Predict
    prediction = ai_engine.predict(candles, sr_levels)
    return jsonify(prediction)

# ── Paper Trading API Routes ──────────────────────────────────────────────────

@app.route('/api/paper/account')
def paper_account():
    pt_engine.refresh_prices()
    return jsonify(pt_engine.get_account())

@app.route('/api/paper/order', methods=['POST'])
def paper_place_order():
    data = request.get_json(silent=True) or {}
    _allowed = {"symbol", "side", "order_type", "quantity", "price", "stop_price",
                "trigger_price", "stop_loss", "target", "display_name", "current_price"}
    safe = {k: v for k, v in data.items() if k in _allowed}
    if "symbol" not in safe:
        return jsonify({"success": False, "error": "symbol required", "message": "symbol required"}), 400
    result = pt_engine.place_order(**safe)
    return jsonify(result), (200 if result.get("success") else 400)

@app.route('/api/paper/order/<order_id>', methods=['DELETE'])
def paper_cancel_order(order_id):
    return jsonify(pt_engine.cancel_order(order_id))

@app.route('/api/paper/positions')
def paper_positions():
    pt_engine.refresh_prices()
    return jsonify(pt_engine.get_positions_with_pnl())

@app.route('/api/paper/orders')
def paper_orders():
    return jsonify(pt_engine.get_orders())

@app.route('/api/paper/history')
def paper_history():
    return jsonify(pt_engine.get_history())

@app.route('/api/paper/performance')
def paper_performance():
    return jsonify(pt_engine.get_performance_stats())

@app.route('/api/paper/close', methods=['POST'])
def paper_close():
    data = request.get_json(silent=True) or {}
    _sym = data.get('symbol')
    if not _sym:
        return jsonify({'success': False, 'error': 'symbol required'}), 400
    result = pt_engine.close_position(_sym, data.get('quantity'), data.get('current_price'))
    return jsonify(result), (200 if result.get('success') else 400)

@app.route('/api/paper/update-prices', methods=['POST'])
def paper_update_prices():
    data = request.get_json(silent=True) or {}
    result = pt_engine.update_prices(data.get('prices') or {})
    return jsonify(result)

@app.route('/api/paper/reset', methods=['POST'])
def paper_reset():
    data = request.get_json(silent=True) or {}
    pt_engine.reset_account(data.get('balance', 1000000))
    return jsonify({ 'success': True })

@app.route('/api/paper/pnl-history')
def paper_pnl_history():
    return jsonify(pt_engine.get_pnl_history())

# ── Futures Trading Routes ────────────────────────────────────────────────────

@app.route('/api/futures/account')
def futures_account_route():
    return jsonify(serialize_futures(futures_account.get_account_summary()))

@app.route('/api/futures/positions')
def futures_positions():
    return jsonify(serialize_futures(futures_account.get_all_positions()))

@app.route('/api/futures/orders')
def futures_orders():
    # Return only active orders (OPEN, PENDING, PARTIALLY_FILLED)
    active = [o for o in futures_account.orders.values()
              if o.status in (OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED)]
    return jsonify(serialize_futures(active))

@app.route('/api/futures/history')
def futures_history():
    # Return trades in reverse chronological order (newest first)
    trades = list(reversed(futures_account.trades))
    return jsonify(serialize_futures(trades))

@app.route('/api/futures/funding/history')
def futures_funding_history():
    return jsonify(serialize_futures(futures_account.funding_history))

@app.route('/api/futures/leverage', methods=['POST'])
def futures_leverage():
    data = request.json
    symbol = data.get('symbol')
    leverage = int(data.get('leverage', 20))
    position_side_val = data.get('position_side')
    
    with futures_account.lock:
        target_positions = []
        if position_side_val:
            side = PositionSide(position_side_val)
            if (symbol, side) in futures_account.positions:
                target_positions.append(futures_account.positions[(symbol, side)])
        else:
            for (s, side), pos in futures_account.positions.items():
                if s == symbol:
                    target_positions.append(pos)
                    
        for pos in target_positions:
            pos.leverage = leverage
            pos.isolated_margin = (pos.quantity * pos.entry_price) / leverage
            
    return jsonify({"success": True, "leverage": leverage})

@app.route('/api/futures/margin-mode', methods=['POST'])
def futures_margin_mode():
    data = request.json
    symbol = data.get('symbol', '')
    mode = data.get('mode', 'ISOLATED')
    result = futures_account.set_margin_mode(symbol, mode)
    return jsonify(result)

@app.route('/api/futures/hedge-mode', methods=['POST'])
def futures_hedge_mode():
    data = request.json
    enabled = data.get('hedge_mode', False)
    result = futures_account.set_hedge_mode(enabled)
    return jsonify(result)

@app.route('/api/futures/margin/adjust', methods=['POST'])
def futures_margin_adjust():
    data = request.json
    symbol = data.get('symbol', '')
    side = data.get('side', 'LONG')
    amount = Decimal(str(data.get('amount', 0)))
    result = futures_account.adjust_isolated_margin(symbol, side, amount)
    return jsonify(result)

@app.route('/api/futures/reset', methods=['POST'])
def futures_reset():
    data = request.json or {}
    balance = Decimal(str(data.get('balance', '10000')))
    return jsonify(serialize_futures(futures_account.reset_account(balance)))

@app.route('/api/futures/order', methods=['POST'])
def futures_place_order():
    data = request.json
    try:
        order = Order(
            order_id=str(uuid.uuid4()),
            symbol=data['symbol'],
            side=OrderSide(data['side']),
            order_type=OrderType(data['order_type']),
            quantity=Decimal(str(data['quantity'])),
            price=Decimal(str(data['price'])) if data.get('price') else None,
            stop_price=Decimal(str(data['stop_price'])) if data.get('stop_price') else None,
            trigger_price=Decimal(str(data['trigger_price'])) if data.get('trigger_price') else None,
            time_in_force=TimeInForce(data.get('time_in_force', 'GTC')),
            position_side=PositionSide(data['position_side']) if data.get('position_side') else None,
            reduce_only=data.get('reduce_only', False),
            post_only=data.get('post_only', False),
            leverage=int(data.get('leverage', 20)),
            margin_mode=data.get('margin_mode', 'ISOLATED'),
        )
        result = futures_account.place_order(order)
        return jsonify(serialize_futures(result))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/futures/order/<order_id>', methods=['DELETE'])
def futures_cancel_order(order_id):
    success = futures_account.cancel_order(order_id)
    return jsonify({"success": success})

@app.route('/api/futures/order/close', methods=['POST'])
def futures_close_position_route():
    data = request.json
    symbol = data.get('symbol')
    position_side_val = data.get('position_side')
    pct = float(data.get('pct', 100))
    
    # Try using the account's close_position method which supports pct
    result = futures_account.close_position(symbol, position_side_val, pct)
    return jsonify(serialize_futures(result))

@app.route('/api/futures/order/<order_id>')
def futures_get_order(order_id):
    order = futures_account.orders.get(order_id)
    if not order: return jsonify({"error": "Order not found"}), 404
    return jsonify(serialize_futures(order))

@app.route('/api/futures/order/<order_id>', methods=['PUT'])
def futures_update_order(order_id):
    data = request.json
    with futures_account.lock:
        if order_id not in futures_account.orders:
            return jsonify({"error": "Order not found"}), 404
        order = futures_account.orders[order_id]
        if order.status != OrderStatus.OPEN:
            return jsonify({"error": "Only OPEN orders can be updated"}), 400
        if 'quantity' in data: order.quantity = Decimal(str(data['quantity']))
        if 'price' in data: order.price = Decimal(str(data['price']))
    return jsonify({"success": True, "order": serialize_futures(order)})

@app.route('/api/futures/mark-price/<symbol>')
def futures_mark_price(symbol):
    data = futures_account.price_engine.get_symbol_data(symbol)
    price = futures_account.price_engine.get_mark_price(symbol)
    oi = futures_account.price_engine.get_open_interest(symbol)
    oi_delta = futures_account.price_engine.get_oi_delta(symbol)
    ls_ratio = futures_account.price_engine.get_long_short_ratio(symbol)
    funding_rate = futures_account.price_engine.get_funding_rate(symbol)
    data = data or {}
    
    return jsonify({
        "symbol": symbol,
        "mark_price": str(price),
        "index_price": str(data.get('indexPrice', '0')),
        "funding_rate": str(funding_rate),
        "open_interest": str(oi),
        "oi_delta": oi_delta,
        "long_short_ratio": ls_ratio,
        "next_funding_time": data.get('nextFundingTime')
    })

@app.route('/api/futures/risk-tiers/<symbol>')
def futures_risk_tiers(symbol):
    return jsonify(serialize_futures(BTCUSDT_RISK_TIERS))

@app.route('/api/futures/open-interest/<symbol>')
def futures_open_interest(symbol):
    return jsonify({"symbol": symbol, "open_interest": "1250.45", "unit": "BTC"})

@app.route('/api/futures/orderbook/<symbol>')
def futures_orderbook_route(symbol):
    book = futures_account.matching_engine.get_book(symbol)
    return jsonify(serialize_futures(book))

@app.route('/api/futures/liquidations')
def futures_liquidations():
    return jsonify([])

@app.route('/api/futures/analytics')
def futures_analytics():
    trades = futures_account.trades
    pnl_list = [float(t.realized_pnl) for t in trades]
    trade_pnl = [float(t.realized_pnl) for t in trades]
    
    if futures_account.equity_curve:
        equity_curve_data = futures_account.equity_curve[-500:]
        equity_curve = [e['equity'] for e in equity_curve_data]
        equity_timestamps = [e['timestamp'] for e in equity_curve_data]
    else:
        initial = float(futures_account.initial_balance)
        equity_curve = [initial]
        equity_timestamps = [0]
        curr = initial
        for t in trades:
            curr += float(t.realized_pnl)
            ts = t.timestamp.timestamp() if hasattr(t.timestamp, 'timestamp') else time.time()
            equity_curve.append(curr)
            equity_timestamps.append(int(ts))
    
    report = futures_account.analytics_engine.generate_full_report(trades, pnl_list, equity_curve)
    report['trade_pnl'] = trade_pnl
    report['equity_curve'] = [{
        'balance': report['equity_curve'][i]['balance'] if i < len(report['equity_curve']) else equity_curve[i],
        'timestamp': equity_timestamps[i] if i < len(equity_timestamps) else 0
    } for i in range(len(equity_curve))]
    return jsonify(serialize_futures(report))

@app.route('/api/futures/equity-curve')
def futures_equity_curve():
    # Use real persisted equity curve from the account
    equity_data = futures_account.equity_curve if futures_account.equity_curve else []
    if not equity_data:
        # Build a basic one from initial balance
        initial = float(futures_account.initial_balance)
        pnl_list = [float(t.realized_pnl) for t in futures_account.trades]
        eq = initial
        equity_data = [{'timestamp': 0, 'equity': eq}]
        for t in futures_account.trades:
            eq += float(t.realized_pnl)
            ts = t.timestamp.timestamp() if hasattr(t.timestamp, 'timestamp') else time.time()
            equity_data.append({'timestamp': int(ts), 'equity': eq})
    return jsonify({"equity": serialize_futures(equity_data)})

@app.route('/api/futures/analytics/emotions')
def futures_emotions():
    return jsonify(futures_account.journal_engine.get_emotion_performance_correlation())

@app.route('/api/futures/journal')
def futures_journal():
    return jsonify(serialize_futures(futures_account.journal_engine.entries))

@app.route('/api/futures/journal', methods=['POST'])
def futures_add_journal_route():
    data = request.json
    entry = JournalEntry(
        entry_id=str(uuid.uuid4()),
        timestamp=datetime.now(),
        symbol=data.get('symbol'),
        setup_type=data.get('setup_type', 'MANUAL'),
        emotion=data.get('emotion', 'NEUTRAL'),
        notes=data.get('notes', ''),
        behavioral_tags=data.get('tags', [])
    )
    futures_account.journal_engine.add_entry(entry)
    return jsonify({"success": True, "entry_id": entry.entry_id})

@app.route('/api/futures/journal/patterns')
def futures_journal_patterns_route():
    patterns = futures_account.journal_engine.detect_behavior_patterns(futures_account.journal_engine.entries)
    return jsonify({"patterns": patterns})

@app.route('/api/futures/ai/review-trade', methods=['POST'])
def futures_ai_review_route():
    data = request.json
    analysis = ai_engine.analyze_trade_for_journal(
        data.get('position_data', {}),
        data.get('trade_data', {}),
        data.get('market_context', {})
    )
    return jsonify({"analysis": analysis})

@app.route('/api/futures/ai/risk-check', methods=['POST'])
def futures_ai_risk_check_route():
    wallet_data = {"wallet_balance": float(futures_account.wallet.balance), "unrealized_pnl": 0.0}
    total_upnl = Decimal("0")
    positions_list = []
    for symbol, pos in futures_account.positions.items():
        mark_price = futures_account.price_engine.get_mark_price(symbol)
        upnl = futures_account.risk_engine.calculate_unrealized_pnl(pos.side, pos.quantity, pos.entry_price, mark_price)
        total_upnl += upnl
        positions_list.append({"symbol": symbol, "side": pos.side.value, "size": float(pos.quantity), "entry_price": float(pos.entry_price)})
    wallet_data["unrealized_pnl"] = float(total_upnl)
    report = ai_engine.check_portfolio_risk(positions_list, wallet_data)
    return jsonify({"risk_report": report})

@app.route('/api/futures/ai/market-summary/<symbol>')
def futures_ai_market_summary_route(symbol):
    mark_price = float(futures_account.price_engine.get_mark_price(symbol))
    funding_rate = float(futures_account.price_engine.get_funding_rate(symbol))
    depth = {"bids_total": [100000], "asks_total": [120000]}
    summary = ai_engine.get_market_summary(symbol, mark_price, funding_rate, depth, 0.03)
    return jsonify({"summary": summary})

@app.route('/api/futures/ai/size-advisor', methods=['POST'])
def futures_ai_size_advisor_route():
    data = request.json
    symbol = data.get('symbol')
    risk_pct = float(data.get('risk_pct', 1.0))
    leverage = int(data.get('leverage', 20))
    mark_price = float(futures_account.price_engine.get_mark_price(symbol))
    balance = float(futures_account.wallet.balance)
    volatility = 0.02
    advice = ai_engine.advise_position_size(symbol, balance, leverage, risk_pct, mark_price, volatility)
    return jsonify(advice)

@app.route("/api/extended-hours/<symbol>")
def get_extended_hours(symbol):
    session, holiday_name = get_market_session()
    result = {
        "symbol":  symbol.upper(),
        "session": session,
        "holidayName": holiday_name,
        "preMarketPrice":    None,
        "preMarketChange":   None,
        "postMarketPrice":   None,
        "postMarketChange":  None,
        "previousClose":     None,
        "openPrice":         None,
    }

    if session == "PRE_MARKET":
        payload = nse_premarket_service.get_symbol(symbol)
        data = payload.get("data")
        if data:
            result["preMarketPrice"]  = data.get("price") or data.get("iep")
            result["preMarketChange"] = data.get("percentageChange")
            result["previousClose"]   = data.get("previousClose")
            result["preMarketData"]   = data
        else:
            result["message"] = payload.get("message")

    elif session == "POST_MARKET" or session == "CLOSED" or session == "HOLIDAY":
        data = nse_client.get_postmarket_price(symbol)
        if data:
            result["postMarketPrice"] = data["closePrice"]
            result["postMarketChange"]= data["pChange"]
            result["previousClose"]   = data["prevClose"]

    elif session == "REGULAR":
        quote = nse_client.get_quote(symbol)
        if quote:
            result["openPrice"]    = quote["open"]
            result["previousClose"]= quote["previousClose"]

    return jsonify(result)

@app.route("/api/market-session")
def get_market_session_route():
    ist = get_ist_now()
    session, holiday_name = get_market_session()
    return jsonify({
        "session":     session,
        "holidayName": holiday_name,
        "isHoliday":   session == "HOLIDAY",
        "istTime":     ist.strftime("%H:%M:%S"),
        "istDate":     ist.strftime("%Y-%m-%d"),
    })


@app.route("/api/market-open-check")
def market_open_check():
    """
    Lightweight pre-start check called by the frontend before any live data is started.
    Returns whether today is a trading day, what the session is, and why it's closed if not.
    Fetches live holiday data from NSE (cached daily, fallback to static list).
    """
    ist        = get_ist_now()
    today_str  = ist.strftime("%Y-%m-%d")
    day_name   = ist.strftime("%A")   # Monday … Sunday

    # Weekend check (fastest — no API call needed)
    if ist.weekday() >= 5:
        return jsonify({
            "isTradingDay": False,
            "session":      "CLOSED",
            "holidayName":  None,
            "reason":       f"Weekend ({day_name})",
            "dayOfWeek":    day_name,
            "istDate":      today_str,
            "istTime":      ist.strftime("%H:%M:%S"),
            "holidaySource": "weekend",
        })

    # Live holiday lookup (cached after first call today)
    holidays = fetch_nse_holiday_list()
    holiday_source = "live" if _live_holiday_cache.get("fetched_date") == today_str else "static"

    if today_str in holidays:
        h_name = holidays[today_str]
        return jsonify({
            "isTradingDay": False,
            "session":      "HOLIDAY",
            "holidayName":  h_name,
            "reason":       f"NSE Market Holiday: {h_name}",
            "dayOfWeek":    day_name,
            "istDate":      today_str,
            "istTime":      ist.strftime("%H:%M:%S"),
            "holidaySource": holiday_source,
        })

    # It's a trading weekday — compute current session
    session, _ = get_market_session()
    return jsonify({
        "isTradingDay": True,
        "session":      session,
        "holidayName":  None,
        "reason":       "Trading day",
        "dayOfWeek":    day_name,
        "istDate":      today_str,
        "istTime":      ist.strftime("%H:%M:%S"),
        "holidaySource": holiday_source,
    })

@app.route("/api/market/preopen")
def api_market_preopen():
    category = request.args.get("category", "ALL")
    force = request.args.get("force", "0") == "1"
    return jsonify(nse_premarket_service.get_category(category, force=force))


@app.route("/api/market/preopen/<path:symbol>")
def api_market_preopen_symbol(symbol):
    category = request.args.get("category", "ALL")
    return jsonify(nse_premarket_service.get_symbol(symbol, category=category))

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found", "status": 404}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": str(e), "status": 500}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error("Unhandled Exception: %s", str(e), exc_info=True)
    _debug = os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    _msg = str(e) if _debug else "An internal error occurred"
    return jsonify({"error": _msg, "status": 500}), 500

# ---------------------------------------------------------------------------
# Market subsystem wiring (backend/market/). The runtime owns providers,
# router, services and the scheduler. Started only under __main__ so tests
# import this module with zero network activity.
# ---------------------------------------------------------------------------
from database import Database
from market.runtime import get_runtime
from market import blueprint as market_blueprint

_db = Database()
market_runtime = get_runtime(_db)
_market_runtime = market_runtime   # alias used by stock_provider's holiday lookup
_sse_event_queues: list = []
_sse_queues_lock = threading.Lock()

# Note: the legacy in-memory USER_ALERTS list was always lost on restart, so
# there is nothing to migrate at startup; import_legacy_alerts() remains for
# external one-off imports (e.g. from a backup dump).

def _push_quotes_to_sse(quotes_payload):
    market_blueprint.push_quote_events(quotes_payload)

market_runtime.on_quotes = _push_quotes_to_sse
app.register_blueprint(market_blueprint.market_bp)
market_blueprint.init_market_blueprint(market_runtime, _sse_event_queues, _sse_queues_lock)

def start_market_runtime():
    """Idempotent runtime start (scheduler + initial calendar)."""
    market_runtime.start()

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("APP_PORT", "5000")))
    # Vercel containers may run as non-root and cannot bind to privileged ports (<1024).
    # If PORT is set to a privileged port, fall back to 5000 and log clearly.
    if port < 1024:
        logger.warning("PORT=%d is a privileged port; non-root containers cannot bind. Falling back to 5000.", port)
        port = 5000
    requested_debug = os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    debug = requested_debug and host in local_hosts
    if requested_debug and not debug:
        logger.warning("Refusing to enable Flask debug mode on non-local host %s", host)
    start_market_runtime()
    try:
        app.run(host=host, port=port, debug=debug, threaded=True)
    except OSError as exc:
        logger.error("Failed to bind to %s:%d: %s", host, port, exc)
        raise