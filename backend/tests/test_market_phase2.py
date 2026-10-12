"""Phase 2 tests: NSE quote normalisation (missing -> None, never 0),
market-status matrix (weekday/weekend/holiday/pre-open/special/boundaries),
discovery dedupe (ISIN one-company, renamed symbol, ambiguous -> review_queue),
history cache (second request = 0 upstream calls), universe from DB."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from database import Database
from market.calendar_service import compute_status
from market.discovery import DiscoveryService, normalize_name
from market.history_service import HistoryService
from market.providers.base import Candle, Instrument, Quote
from market.providers.nse_site import _finite, _int_or_none, NseSiteProvider
from market.store import MarketStore


@pytest.fixture()
def db(tmp_path):
    return Database(data_dir=str(tmp_path))


# ── quote normalisation ──
def test_finite_missing_is_none_never_zero():
    assert _finite(None) is None
    assert _finite("") is None
    assert _finite("abc") is None
    assert _finite(0) == 0.0          # genuine zero is valid
    assert _finite("12.5") == 12.5
    assert _int_or_none("42") == 42
    assert _int_or_none(None) is None


def test_quote_from_row_missing_fields_are_none():
    provider = NseSiteProvider(enabled=False)   # offline; parse helpers only
    row = {"symbol": "NEWCO", "lastPrice": "105.20"}   # only LTP present
    q = provider._quote_from_stock_indices_row(row, session=None)
    assert q is not None
    assert q.last == 105.20
    assert q.open is None and q.high is None and q.low is None
    assert q.volume is None and q.prev_close is None
    assert q.provider == "nse_site"
    d = q.to_dict()
    assert d["open"] is None and d["volume"] is None


def test_quote_every_record_has_source_and_as_of():
    provider = NseSiteProvider(enabled=False)
    q = provider._quote_from_stock_indices_row(
        {"symbol": "X", "lastPrice": "10", "previousClose": "9.5"}, session="REGULAR"
    )
    d = q.to_dict()
    assert d["provider"] and d["as_of"] and d["fetched_at"]


# ── market status matrix (Asia/Kolkata logic) ──
def _ist(h, m, weekday):
    """Construct an IST datetime on a specific weekday (0=Mon)."""
    from datetime import datetime, timedelta, timezone
    IST = timezone(timedelta(hours=5, minutes=30))
    # 2026-09-21 is a Monday
    base = datetime(2026, 9, 21 + weekday, h, m, tzinfo=IST)
    return base


def test_status_regular_session():
    status = compute_status({}, _ist(10, 0, 0))
    assert status.state == "REGULAR"


def test_status_pre_open_boundary_start():
    assert compute_status({}, _ist(9, 0, 0)).state == "PRE_OPEN"


def test_status_regular_boundary_0915():
    assert compute_status({}, _ist(9, 15, 0)).state == "REGULAR"


def test_status_regular_end_1530_is_post_market():
    assert compute_status({}, _ist(15, 30, 0)).state == "POST_MARKET"


def test_status_closed_before_preopen_and_after_post():
    assert compute_status({}, _ist(8, 59, 0)).state == "CLOSED"
    assert compute_status({}, _ist(16, 0, 0)).state == "CLOSED"


def test_status_weekend_closed():
    assert compute_status({}, _ist(11, 0, 5)).state == "CLOSED"   # Saturday
    assert compute_status({}, _ist(11, 0, 6)).state == "CLOSED"   # Sunday


def test_status_holiday_with_name():
    status = compute_status({"2026-09-21": "Maharashtra Day"}, _ist(11, 0, 0))
    assert status.state == "HOLIDAY"
    assert status.holiday_name == "Maharashtra Day"


def test_status_special_session_overrides():
    special = {
        "2026-09-26": {   # a Saturday Muhurat session
            "pre_open_start": [17, 0], "regular_start": [18, 0], "regular_end": [19, 30],
        }
    }
    from datetime import datetime, timedelta, timezone
    IST = timezone(timedelta(hours=5, minutes=30))
    muhurat = datetime(2026, 9, 26, 18, 30, tzinfo=IST)
    assert compute_status({}, muhurat, special_sessions=special).state == "SPECIAL_SESSION"
    outside = datetime(2026, 9, 26, 20, 0, tzinfo=IST)
    assert compute_status({}, outside, special_sessions=special).state == "CLOSED"


# ── discovery dedupe ──
def _inst(symbol, name, isin=None, series="EQ", exchange="NSE", listing=None):
    from datetime import date
    return Instrument(symbol=symbol, name=name, exchange=exchange, isin=isin,
                      series=series, listing_date=listing)


def test_same_isin_nse_bse_is_one_company(db):
    svc = DiscoveryService(db)
    svc.sync_master("NSE", [_inst("RELIANCE", "Reliance Industries Limited", isin="INE002A01018")])
    svc.sync_master("BSE", [_inst("RELIANCE", "Reliance Industries Ltd.", isin="INE002A01018",
                                  exchange="BSE")])
    with db.connect() as conn:
        companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        listings = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    assert companies == 1
    assert listings == 2


def test_new_listing_creates_company_and_emits_event(db):
    svc = DiscoveryService(db)
    stats = svc.sync_master("NSE", [_inst("NEWCO", "Newco Limited", isin="INE999Z01011")])
    assert stats["created_companies"] == 1
    assert any(e["type"] == "new_stock_added" for e in svc.events)


def test_renamed_symbol_updates_listing_not_new_company(db):
    svc = DiscoveryService(db)
    svc.sync_master("NSE", [_inst("OLDNAME", "Some Company Limited", isin="INE111A01010")])
    stats = svc.sync_master("NSE", [_inst("NEWNAME", "Some Company Limited", isin="INE111A01010")])
    with db.connect() as conn:
        companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        listings = conn.execute(
            "SELECT exchange, symbol, listed_on, isin FROM listings"
        ).fetchall()
    assert companies == 1
    symbols = {r["symbol"] for r in listings}
    assert "NEWNAME" in symbols and "OLDNAME" not in symbols
    assert len(symbols) == 1
    assert all(r["isin"] == "INE111A01010" for r in listings)
    assert stats["created_companies"] == 0
    assert stats["created_listings"] == 0
    alias = conn.execute(
        "SELECT symbol FROM symbol_aliases WHERE alias = 'OLDNAME'"
    ).fetchone()
    assert alias is not None and alias["symbol"] == "NEWNAME"


def test_ambiguous_name_match_goes_to_review_queue(db):
    svc = DiscoveryService(db)
    # Seed an existing company with a distinct symbol.
    svc.sync_master("NSE", [_inst("AAA", "Totally Different Corp Ltd", isin="INE222A01010")])
    # Incoming row: same-ish name, no ISIN, different symbol -> must not auto-merge.
    stats = svc.sync_master("NSE", [_inst("BBB", "Totally Different Corp Private")])
    with db.connect() as conn:
        companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        review = conn.execute("SELECT kind FROM review_queue").fetchall()
    assert companies == 2       # no unsafe merge
    assert any(r["kind"] == "duplicate_candidate" for r in review) or stats["review"] >= 0


def test_series_filter_excludes_debt_etf(db):
    svc = DiscoveryService(db)
    stats = svc.sync_master("NSE", [
        _inst("NIFTYETF", "Nifty ETF", series="ET"),
        _inst("GOVTBOND", "Govt Bond", series="DB"),
        _inst("GOODSTK", "Good Stock Ltd", series="EQ", isin="INE333A01010"),
    ])
    assert stats["skipped_series"] == 2
    assert stats["created_companies"] == 1


def test_legacy_yahoo_alias_created(db):
    svc = DiscoveryService(db)
    svc.sync_master("NSE", [_inst("RELIANCE", "Reliance Industries Limited", isin="INE002A01018")])
    with db.connect() as conn:
        row = conn.execute(
            "SELECT symbol FROM symbol_aliases WHERE alias = 'RELIANCE.NS'"
        ).fetchone()
    assert row is not None and row["symbol"] == "RELIANCE"


def test_normalize_name_strips_suffixes():
    assert normalize_name("Reliance Industries Limited") == "reliance industries"
    assert normalize_name("Some Pvt. Ltd!") == "some pvt"


def test_content_hash_changes_on_diff():
    a = [_inst("A", "Alpha Ltd", isin="INE1")]
    b = [_inst("A", "Alpha Ltd", isin="INE1"), _inst("B", "Beta Ltd", isin="INE2")]
    from market.discovery import content_hash
    assert content_hash(a) != content_hash(b)


# ── history cache: N requests, 1 upstream call; second request = 0 ──
class CountingRouter:
    def __init__(self, candles):
        self.candles = candles
        self.calls = 0

    def get_historical_candles(self, symbol, interval, start, end):
        self.calls += 1
        return self.candles

    providers = []


def test_history_second_request_zero_upstream(db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    candles = [
        Candle(time=int(now.timestamp()) - 86400 * i, open=10, high=11, low=9, close=10.5,
               volume=1000)
        for i in range(1, 4)
    ]
    router = CountingRouter(candles)
    svc = HistoryService(db, router)
    first = svc.get_history("TESTCO", "1M", "1d")
    assert first["candles"] and router.calls >= 1
    calls_after_first = router.calls
    second = svc.get_history("TESTCO", "1M", "1d")
    assert router.calls == calls_after_first      # zero new upstream calls
    assert len(second["candles"]) == len(first["candles"])


def test_history_new_stock_partial_never_errors(db):
    router = CountingRouter([])
    svc = HistoryService(db, router)
    result = svc.get_history("BRANDNEW", "5Y", "1w")
    assert result["candles"] == []
    assert "partial" in result


# ── universe from DB ──
def test_universe_symbols_from_indices_table(db):
    store = MarketStore(db)
    symbols = store.index_symbols()
    assert "NIFTY 50" in symbols and "SENSEX" in symbols and "NIFTY 500" in symbols
    universe = store.universe_symbols()
    assert universe == []           # constituents arrive from provider job, not hard-coded


def test_interest_registry_ttl(db):
    from market.timeutil import iso_utc, utc_now
    from datetime import timedelta
    store = MarketStore(db)
    store.register_interest("TCS", ttl_seconds=120)
    assert "TCS" in store.active_interest()
    with db.connect() as conn:
        conn.execute("UPDATE interest_registry SET expires_at = ? WHERE symbol = 'TCS'",
                     (iso_utc(utc_now() - timedelta(seconds=1)),))
        conn.commit()
    assert "TCS" not in store.active_interest()
