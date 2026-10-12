"""Environment-driven configuration for the market subsystem.

Every tunable is here so ops changes never touch code. Values are read once
at import; backend/.env (loaded by app.py via python-dotenv) is the source.
"""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


# ── Provider chains (ordered fallback; first healthy wins) ──
MARKET_DATA_PROVIDERS = _csv("MARKET_DATA_PROVIDER", "nse_site,yfinance")
IPO_DATA_PROVIDERS = _csv("IPO_DATA_PROVIDER", "nse_ipo,manual")
GMP_PROVIDER = os.getenv("GMP_PROVIDER", "").strip()  # unset/empty = GMP feature OFF

# The NSE website adapter is approved ON by default but only through the shared
# HTTP layer; ENABLE_NSE_SITE_ADAPTER=0 turns it off without a code change.
ENABLE_NSE_SITE_ADAPTER = _bool("ENABLE_NSE_SITE_ADAPTER", True)

# ── API keys (never hard-coded; empty means feature unavailable) ──
MARKET_DATA_API_KEY = os.getenv("MARKET_DATA_API_KEY", "").strip()
UPSTOX_API_TOKEN = os.getenv("UPSTOX_API_TOKEN", "").strip()
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "").strip()
IPO_DATA_API_KEY = os.getenv("IPO_DATA_API_KEY", "").strip()
GMP_API_KEY = os.getenv("GMP_API_KEY", "").strip()

# ── Shared HTTP layer ──
HTTP_TIMEOUT = _float("HTTP_TIMEOUT", 10.0)
HTTP_MAX_RETRIES = _int("HTTP_MAX_RETRIES", 3)
HTTP_BACKOFF_BASE = _float("HTTP_BACKOFF_BASE", 0.5)   # seconds
HTTP_BACKOFF_MAX = _float("HTTP_BACKOFF_MAX", 8.0)     # seconds

# ── Circuit breaker ──
BREAKER_FAILURE_THRESHOLD = _int("BREAKER_FAILURE_THRESHOLD", 5)
BREAKER_COOLDOWN_SECONDS = _float("BREAKER_COOLDOWN_SECONDS", 120.0)

# ── Rate limiting / budgets (per provider; token bucket) ──
# NSE website: rate for the option-chain background loop + SSE stream.
# The loop targets ~1 snapshot / s; the SSE stream reads from the same
# in-memory snapshot so it does not add extra upstream calls.
NSE_SITE_RATE_PER_SEC = _float("NSE_SITE_RATE_PER_SEC", 1.5)      # ~1 req / 0.67 s
# 2 s option-chain cadence over a 6.25 h session is ~11k chain calls, plus
# quotes/expiries. 4000 ran out within ~2 h and left the app on 403/"budget
# exhausted" for the rest of the day.
NSE_SITE_DAILY_BUDGET = _int("NSE_SITE_DAILY_BUDGET", 16000)
# Option-chain + spot refresh cadence (seconds) while the market is open.
OC_REFRESH_SECONDS = _float("OC_REFRESH_SECONDS", 2.0)
# Closed-market cadence: keep the last snapshot warm without burning budget.
OC_CLOSED_REFRESH_SECONDS = _float("OC_CLOSED_REFRESH_SECONDS", 60.0)
# NSE's streamer WebSocket answers 403 to non-browser clients. Off by default;
# set OC_ENABLE_NSE_WS=1 only if you have legitimate access.
OC_ENABLE_NSE_WS = _bool("OC_ENABLE_NSE_WS", False)
YF_RATE_PER_SEC = _float("YF_RATE_PER_SEC", 1.0)
YF_DAILY_BUDGET = _int("YF_DAILY_BUDGET", 20000)

# ── Scheduler ──
ENABLE_SCHEDULER = _bool("ENABLE_SCHEDULER", True)
INTERVAL_INDEX_REFRESH = _int("INTERVAL_INDEX_REFRESH", 10)         # in-session seconds
INTERVAL_STATUS_REFRESH = _int("INTERVAL_STATUS_REFRESH", 10)
INTERVAL_WATCHLIST_REFRESH = _int("INTERVAL_WATCHLIST_REFRESH", 15)
INTERVAL_UNIVERSE_REFRESH = _int("INTERVAL_UNIVERSE_REFRESH", 60)
INTERVAL_IPO_LIST_REFRESH = _int("INTERVAL_IPO_LIST_REFRESH", 600)   # 10 min (new-IPO detection)
INTERVAL_IPO_BIDDING_REFRESH = _int("INTERVAL_IPO_BIDDING_REFRESH", 300)
INTERVAL_GMP_REFRESH = _int("INTERVAL_GMP_REFRESH", 900)             # 15 min
INTERVAL_MASTER_REFRESH = _int("INTERVAL_MASTER_REFRESH", 86400)     # daily
INTERVAL_LISTING_DAY_LOOP = _int("INTERVAL_LISTING_DAY_LOOP", 300)   # 5 min

# ── Discovery / listings ──
NEW_LISTING_WINDOW_DAYS = _int("NEW_LISTING_WINDOW_DAYS", 30)
# NSE series values treated as trackable equities (configurable — SME included).
EQUITY_SERIES = _csv("EQUITY_SERIES", "EQ,BE,BZ,SM,ST")

# ── GMP ──
GMP_MAX_AGE_HOURS = _int("GMP_MAX_AGE_HOURS", 24)

# ── Alerts ──
ALERT_IPO_CLOSING_LEAD_HOURS = _int("ALERT_IPO_CLOSING_LEAD_HOURS", 24)
ALERT_SUBSCRIPTION_THROTTLE_MINUTES = _int("ALERT_SUBSCRIPTION_THROTTLE_MINUTES", 60)
ALERT_SUBSCRIPTION_MIN_CHANGE_PCT = _float("ALERT_SUBSCRIPTION_MIN_CHANGE_PCT", 5.0)


def describe() -> dict:
    """Non-secret summary for /api/market/health and startup logs."""
    return {
        "market_data_providers": MARKET_DATA_PROVIDERS,
        "ipo_data_providers": IPO_DATA_PROVIDERS,
        "gmp_enabled": bool(GMP_PROVIDER),
        "nse_site_adapter": ENABLE_NSE_SITE_ADAPTER,
        "scheduler_enabled": ENABLE_SCHEDULER,
        "new_listing_window_days": NEW_LISTING_WINDOW_DAYS,
        "http_timeout": HTTP_TIMEOUT,
        "max_retries": HTTP_MAX_RETRIES,
        "breaker_threshold": BREAKER_FAILURE_THRESHOLD,
        "breaker_cooldown_s": BREAKER_COOLDOWN_SECONDS,
    }
