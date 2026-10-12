"""Market calendar + session status. Replaces stock_provider.NSE_HOLIDAYS and
app._NSE_HOLIDAY_NAMES with a provider calendar cached in config/market_calendar.json.

Session rules (defaults = current code): pre-open 09:00-09:15, regular
09:15-15:30 IST, Mon-Fri; special sessions (Muhurat) come from the calendar
file. Logic in Asia/Kolkata. Startup warns when the calendar lacks the year.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta

from .providers.base import MarketStatus
from .timeutil import ist_now, ist_date_str, to_ist

logger = logging.getLogger(__name__)

PRE_OPEN_START = (9, 0)
REGULAR_START = (9, 15)
REGULAR_END = (15, 30)
POST_MARKET_END = (16, 0)

CALENDAR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "market_calendar.json",
)

_special_cache = {"loaded_date": None, "data": {}}
_cache_lock = threading.Lock()


def _load_calendar_file() -> dict:
    try:
        with open(CALENDAR_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_calendar.json unreadable: %s", exc)
        return {}


def compute_status(holidays: dict, now_ist: datetime | None = None,
                   special_sessions: dict | None = None) -> MarketStatus:
    """Pure function: session state for an IST instant given a holiday map.

    Session matrix: weekday/holiday/weekend, pre-open, regular, post-market,
    closed, plus special sessions (Muhurat / special Saturday) from config.
    """
    now_ist = to_ist(now_ist) if now_ist else ist_now()
    day = now_ist.strftime("%Y-%m-%d")
    minutes = now_ist.hour * 60 + now_ist.minute
    special_sessions = special_sessions or {}

    def at(hm):
        return hm[0] * 60 + hm[1]

    # Special-session hours are declared per date.
    special_for_day = special_sessions.get(day) or {}
    pre_open_start = at(special_for_day.get("pre_open_start") or PRE_OPEN_START)
    regular_start = at(special_for_day.get("regular_start") or REGULAR_START)
    regular_end = at(special_for_day.get("regular_end") or REGULAR_END)
    post_end = at(POST_MARKET_END)

    # A declared special session (e.g. Muhurat) wins over the normal schedule.
    if special_for_day:
        state = "SPECIAL_SESSION" if pre_open_start <= minutes < regular_end else "CLOSED"
        return MarketStatus(state=state, holiday_name=None,
                            next_open=_next_open(holidays, now_ist), provider="calendar")

    if now_ist.weekday() >= 5:
        state, holiday_name = "CLOSED", None
    elif day in holidays:
        state, holiday_name = "HOLIDAY", holidays[day]
    elif pre_open_start <= minutes < regular_start:
        state, holiday_name = "PRE_OPEN", None
    elif regular_start <= minutes < regular_end:
        state, holiday_name = "REGULAR", None
    elif regular_end <= minutes < post_end:
        state, holiday_name = "POST_MARKET", None
    else:
        state, holiday_name = "CLOSED", None

    return MarketStatus(state=state, holiday_name=holiday_name,
                        next_open=_next_open(holidays, now_ist), provider="calendar")


def _next_open(holidays: dict, now_ist: datetime) -> str | None:
    """Next REGULAR open instant (ISO IST) scanning up to 30 days ahead."""
    probe = now_ist.replace(hour=REGULAR_START[0], minute=REGULAR_START[1],
                            second=0, microsecond=0)
    candidate = probe if now_ist < probe else probe + timedelta(days=1)
    for _ in range(30):
        day = candidate.strftime("%Y-%m-%d")
        if candidate.weekday() < 5 and day not in holidays:
            return candidate.isoformat()
        candidate += timedelta(days=1)
    return None


def warn_if_calendar_incomplete(holidays: dict) -> None:
    year = ist_now().year
    if not any(d.startswith(str(year)) for d in holidays):
        logger.warning(
            "market calendar does not cover %d — holiday detection degraded "
            "(provider calendar unavailable or stale)", year,
        )


def load_special_sessions() -> dict:
    """{"YYYY-MM-DD": {"pre_open_start": [h,m], "regular_start": ..., "regular_end": ...}}"""
    data = _load_calendar_file()
    return data.get("special_sessions") or {}


def refresh_calendar(router=None) -> dict:
    """Fetch the holiday calendar from the provider chain (once per day),
    merge with config/market_calendar.json, persist, and warn if incomplete."""
    with _cache_lock:
        today = ist_date_str()
        if _special_cache["loaded_date"] == today and _special_cache["data"]:
            holidays = dict(_special_cache["data"].get("holidays") or {})
        else:
            holidays = dict(_load_calendar_file().get("holidays") or {})
            _special_cache["loaded_date"] = today
            _special_cache["data"] = {"holidays": holidays}

    if router is not None:
        try:
            provider_holidays = router.get_holiday_calendar(ist_now().year)
            if provider_holidays:
                holidays.update(provider_holidays)
        except Exception as exc:  # noqa: BLE001
            logger.warning("provider holiday calendar unavailable: %s", exc)

    warn_if_calendar_incomplete(holidays)
    _save_calendar(holidays)
    with _cache_lock:
        _special_cache["data"] = {"holidays": holidays}
    return holidays


def _save_calendar(holidays: dict) -> None:
    """Persist merged holidays back to config/market_calendar.json (special
    sessions are preserved untouched)."""
    try:
        data = _load_calendar_file()
        data["holidays"] = holidays
        with open(CALENDAR_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("could not persist market_calendar.json: %s", exc)


def current_status(router=None, holidays: dict | None = None) -> MarketStatus:
    """Convenience: today's status using cached/provider calendar."""
    if holidays is None:
        holidays = refresh_calendar(router)
    special = load_special_sessions()
    return compute_status(holidays, special_sessions=special)
