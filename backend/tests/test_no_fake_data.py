"""No-fake-data guard (Phase 5).

The spec's hardest rule: the app must never show invented numbers. These
tests pin that property — statically (no holiday tables, no random price
generators in runtime code) and behaviourally (empty market -> empty/N-A,
never zeros; failed provider -> None/stale flag, never a fabricated quote).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND = Path(__file__).resolve().parents[1]
RUNTIME_FILES = [
    BACKEND / "market" / "providers" / "nse_site.py",
    BACKEND / "market" / "providers" / "yfinance_provider.py",
    BACKEND / "market" / "providers" / "manual_ipo.py",
    BACKEND / "market" / "store.py",
    BACKEND / "market" / "router.py",
    BACKEND / "market" / "history_service.py",
    BACKEND / "market" / "discovery.py",
    BACKEND / "market" / "ipo_service.py",
    BACKEND / "nse_client.py",
    BACKEND / "stock_provider.py",
]


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_hardcoded_holiday_tables():
    """Holiday knowledge must come from the provider calendar, not code."""
    banned = re.compile(r"^NSE_HOLIDAYS\s*=|_NSE_HOLIDAY_NAMES\s*=", re.M)
    for f in RUNTIME_FILES:
        assert not banned.search(_source(f)), \
            f"{f.name}: hard-coded holiday table re-introduced"


def test_no_random_price_generation_in_runtime_code():
    """random.* may exist only for jitter/backoff, never near price/quote/candle."""
    banned = re.compile(
        r"random\.\w+\s*\([^)]*\)", re.M
    )
    for f in RUNTIME_FILES:
        src = _source(f)
        for m in banned.finditer(src):
            line = src[max(0, src.rfind("\n", 0, m.start()) + 1):m.end()]
            assert not re.search(r"price|quote|candle|last|open|high|low", line, re.I), \
                f"{f.name}: suspicious random usage: {line.strip()}"


def test_no_demo_or_seed_price_literals():
    """No 'demo price' style seeding in runtime modules."""
    banned = re.compile(r"(demo_price|sample_quote|fake_quote|mock_price)\s*=", re.I)
    for f in RUNTIME_FILES:
        assert not banned.search(_source(f)), \
            f"{f.name}: demo/seed price generator present"


def test_router_never_invents_on_total_failure(tmp_path):
    """With no providers, quote/attempt returns None/empty — never a dict
    with numeric fields."""
    from database import Database
    from market.router import MarketRouter
    from market.store import MarketStore

    db = Database(data_dir=str(tmp_path / "guard_db"))
    store = MarketStore(db)
    router = MarketRouter([], store=store)
    assert router.get_quotes(["RELIANCE"]) == {}
    assert router.get_market_status() is None


def test_empty_market_serves_empty_and_stale_not_zero(tmp_path):
    from database import Database
    from market.store import MarketStore

    db = Database(data_dir=str(tmp_path / "guard_db2"))
    store = MarketStore(db)
    assert store.get_quote("NIFTY 50") is None
    assert store.universe_symbols() == []
    # A stored None-field quote stays None (no zero coercion).
    from market.providers.base import Quote
    store.upsert_quote(Quote(
        symbol="X", exchange="NSE", last=10.5, prev_close=None, change=None,
        change_pct=None, open=None, high=None, low=None, volume=None,
        value=None, as_of="2026-09-28T10:00:00+00:00", provider="t",
        session=None, fetched_at="2026-09-28T10:00:00+00:00",
    ))
    d = store.get_quote("X")
    assert d["last"] == 10.5
    assert d["open"] is None and d["volume"] is None and d["prev_close"] is None
