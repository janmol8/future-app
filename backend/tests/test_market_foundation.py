"""Foundation tests: migrations, scheduler lease exclusivity (two workers),
router failover with last-good stale fallback, and TTL cache behaviour.
All offline — tmp_path SQLite and fake providers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from database import Database
from market.cache import TTLCache
from market.router import MarketRouter
from market.scheduler import run_guarded
from market.store import MarketStore


@pytest.fixture()
def db(tmp_path):
    return Database(data_dir=str(tmp_path))


def test_migrations_are_idempotent(db):
    v1 = db.migration_version()
    db.run_migrations()
    assert db.migration_version() == v1
    assert db.migration_version() >= 4


def test_wal_mode_enabled(db):
    with db.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_new_tables_exist(db):
    with db.connect() as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    expected = {
        "schema_migrations", "job_runs", "job_leases",
        "companies", "listings", "symbol_aliases", "indices", "index_constituents",
        "quotes_latest", "price_history", "history_coverage",
        "ipos", "ipo_subscription_snapshots", "ipo_gmp_snapshots", "ipo_events",
        "provider_status", "interest_registry", "review_queue",
        "notification_prefs", "notifications",
    }
    assert expected <= names


# ── fake providers for router tests ──
class FakeQuote:
    def __init__(self, symbol, last):
        self.symbol = symbol
        self.last = last
        self.prev_close = last - 1
        self.change = 1.0
        self.change_pct = 1.5
        self.open = last - 2
        self.high = last + 1
        self.low = last - 2
        self.volume = 1000
        self.value = None
        self.as_of = "2026-09-28T10:00:00+05:30"
        self.fetched_at = "2026-09-28T04:30:00+00:00"

    def to_dict(self):
        return {
            "symbol": self.symbol, "exchange": None, "last": self.last,
            "prev_close": self.prev_close, "change": self.change,
            "change_pct": self.change_pct, "open": self.open, "high": self.high,
            "low": self.low, "volume": self.volume, "value": self.value,
            "as_of": self.as_of, "fetched_at": self.fetched_at,
            "provider": "good", "price_kind": "ltp", "session": None,
            "is_stale": False,
        }


class GoodProvider:
    name = "good"
    capabilities = frozenset({"quotes", "historical"})

    def __init__(self):
        self.calls = 0

    def supports(self, cap):
        return cap in self.capabilities

    def get_quotes(self, symbols):
        self.calls += 1
        return {s: FakeQuote(s, 100.0) for s in symbols}

    def get_market_status(self):
        from market.providers.base import MarketStatus
        return MarketStatus(state="REGULAR", provider="good")

    def get_instrument_master(self, exchange):
        return []

    def get_historical_candles(self, symbol, interval, start, end):
        return []

    def get_holiday_calendar(self, year):
        return {}

    def status(self):
        return {"name": self.name}


class FailingProvider:
    name = "failing"
    capabilities = frozenset({"quotes", "historical"})

    def __init__(self, store=None):
        self.store = store

    def supports(self, cap):
        return cap in self.capabilities

    should_fail = True

    def get_quotes(self, symbols):
        if self.should_fail:
            raise RuntimeError("primary down")
        return {}

    def get_market_status(self):
        raise RuntimeError("primary down")

    def get_instrument_master(self, toadsl=None):
        return []

    def get_historical_candles(self, *a, **k):
        return []

    def get_holiday_calendar(self, year):
        return {}

    def status(self):
        return {"name": "failing"}


def test_router_fails_over_to_backup(db):
    store = MarketStore(db)
    router = MarketRouter([FailingProvider(store), GoodProvider()], store=store)
    quotes = router.get_quotes(["RELIANCE"])
    assert "RELIANCE" in quotes
    assert quotes["RELIANCE"]["last"] == 100.0
    health = {r["provider"]: r for r in store.provider_health()}
    assert health["failing"]["consecutive_failures"] >= 1
    assert health["good"]["last_success_at"] is not None


def test_router_last_good_stale_fallback(db):
    store = MarketStore(db)
    router = MarketRouter([FailingProvider(store)], store=store)
    # Seed a last-good quote, then make the provider raise forever.
    from market.providers.base import Quote
    store.upsert_quote(Quote(symbol="TCS", last=3500.0, provider="good"))
    FailingProvider.should_fail = True
    quotes = router.get_quotes(["TCS"])
    assert quotes["TCS"]["is_stale"] == 1
    assert quotes["TCS"]["last"] == 3500.0


def test_router_never_invents_missing_values(db):
    store = MarketStore(db)
    router = MarketRouter([FailingProvider(store)], store=store)
    quotes = router.get_quotes(["UNKNOWN"])
    assert "UNKNOWN" not in quotes          # absent, not zero


def test_scheduler_lease_excludes_second_worker(db):
    """A fresh lease held by another worker excludes this caller."""
    from market import scheduler as sched
    calls = []

    def body():
        calls.append(1)

    # Worker A takes the lease and "crashes" without releasing.
    conn = db.connect()
    original_holder = sched._Holder.holder_id
    sched._Holder.holder_id = "worker-a"
    assert sched.acquire_lease(conn, "duel_job", 60.0) is True
    conn.commit()
    conn.close()

    # Worker B (this process) must be excluded while the lease is fresh.
    sched._Holder.holder_id = original_holder
    status_b = run_guarded(db, "duel_job", body, lease_ttl=60.0)
    assert status_b == "skipped_locked"
    assert calls == []

    # After the lease expires, B can take over.
    with db.connect() as conn2:
        conn2.execute(
            "UPDATE job_leases SET lease_until = '2000-01-01T00:00:00+00:00' "
            "WHERE job_name = 'duel_job'"
        )
        conn2.commit()
    status_b2 = run_guarded(db, "duel_job", body, lease_ttl=60.0)
    assert status_b2 == "ok"
    assert calls == [1]


def test_scheduler_releases_lease_after_run(db):
    """Sequential runs by the same holder must both execute (lease released)."""
    calls = []

    def body():
        calls.append(1)

    assert run_guarded(db, "loop_job", body, lease_ttl=60.0) == "ok"
    assert run_guarded(db, "loop_job", body, lease_ttl=60.0) == "ok"
    assert calls == [1, 1]
    with db.connect() as conn:
        leftover = conn.execute(
            "SELECT COUNT(*) FROM job_leases WHERE job_name='loop_job'"
        ).fetchone()[0]
    assert leftover == 0


def test_scheduler_records_errors(db):
    def bad():
        raise ValueError("boom")

    status = run_guarded(db, "bad_job", bad, lease_ttl=60.0)
    assert status == "error"
    with db.connect() as conn:
        row = conn.execute(
            "SELECT detail FROM job_runs WHERE job_name='bad_job' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert "boom" in row["detail"]


def test_ttl_cache_single_flight():
    cache = TTLCache()
    cache.set("k", {"v": 1})
    assert cache.get("k", ttl=10) == {"v": 1}
    assert cache.get("k", ttl=0) is None     # expired
    assert cache.get("missing", ttl=10) is None
