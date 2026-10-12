"""SQLite-backed shared store for the market subsystem.

All API routes read from here; only the scheduler writes provider data.
Quotes keep their provider, as_of and fetched_at so the UI can always show
source + freshness, and stale data is flagged, never silently mixed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from .timeutil import iso_utc, utc_now, parse_iso

logger = logging.getLogger(__name__)

# A quote older than this is reported stale (UI shows the stale badge).
STALE_AFTER_SECONDS = 180


class MarketStore:
    def __init__(self, db):
        self.db = db

    # ── quotes ──
    def upsert_quote(self, q) -> None:
        """Persist a providers.base.Quote."""
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO quotes_latest (
                    symbol, exchange, last, prev_close, change, change_pct,
                    open, high, low, volume, value, as_of, fetched_at,
                    provider, price_kind, session, is_stale, error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL)
                ON CONFLICT(symbol) DO UPDATE SET
                    exchange=excluded.exchange,
                    last=excluded.last,
                    prev_close=excluded.prev_close,
                    change=excluded.change,
                    change_pct=excluded.change_pct,
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    volume=excluded.volume,
                    value=excluded.value,
                    as_of=excluded.as_of,
                    fetched_at=excluded.fetched_at,
                    provider=excluded.provider,
                    price_kind=excluded.price_kind,
                    session=excluded.session,
                    is_stale=0,
                    error=NULL
                """,
                (
                    q.symbol, q.exchange, q.last, q.prev_close, q.change, q.change_pct,
                    q.open, q.high, q.low, q.volume, q.value, q.as_of, q.fetched_at,
                    q.provider, q.price_kind, q.session,
                ),
            )
            conn.commit()

    def get_quote(self, symbol: str) -> dict | None:
        """Latest stored quote for a symbol; is_stale computed from fetched_at."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM quotes_latest WHERE symbol = ?", (symbol,)
            ).fetchone()
        if row is None:
            return None
        quote = dict(row)
        fetched = parse_iso(quote.get("fetched_at"))
        if fetched is not None:
            age = (utc_now() - fetched).total_seconds()
            quote["age_seconds"] = int(age)
            if age > STALE_AFTER_SECONDS:
                quote["is_stale"] = 1
        else:
            quote["age_seconds"] = None
        return quote

    def mark_all_stale(self) -> None:
        with self.db.connect() as conn:
            cutoff = iso_utc(utc_now() - timedelta(seconds=STALE_AFTER_SECONDS))
            conn.execute(
                "UPDATE quotes_latest SET is_stale = 1 WHERE fetched_at < ?", (cutoff,)
            )
            conn.commit()

    # ── provider status ──
    def record_provider_success(self, provider: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_status (provider, last_success_at, consecutive_failures, circuit_state)
                VALUES (?, ?, 0, 'closed')
                ON CONFLICT(provider) DO UPDATE SET
                    last_success_at=excluded.last_success_at,
                    consecutive_failures=0,
                    circuit_state='closed'
                """,
                (provider, iso_utc()),
            )
            conn.commit()

    def record_provider_failure(self, provider: str, error: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_status (provider, last_failure_at, last_error, consecutive_failures, circuit_state)
                VALUES (?, ?, ?, 1, 'closed')
                ON CONFLICT(provider) DO UPDATE SET
                    last_failure_at=excluded.last_failure_at,
                    last_error=excluded.last_error,
                    consecutive_failures=consecutive_failures + 1
                """,
                (provider, iso_utc(), error[:500]),
            )
            conn.commit()

    def provider_health(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM provider_status ORDER BY provider").fetchall()
        return [dict(r) for r in rows]

    # ── interest registry (what browsers are currently watching) ──
    def register_interest(self, symbol: str, ttl_seconds: int = 120) -> None:
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO interest_registry (symbol, added_at, last_seen_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    expires_at=excluded.expires_at
                """,
                (symbol, iso_utc(now), iso_utc(now),
                 iso_utc(now + timedelta(seconds=ttl_seconds))),
            )
            conn.commit()

    def active_interest(self) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT symbol FROM interest_registry WHERE expires_at > ?",
                (iso_utc(),),
            ).fetchall()
        return [r["symbol"] for r in rows]

    def prune_interest(self) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "DELETE FROM interest_registry WHERE expires_at <= ?", (iso_utc(),)
            )
            conn.commit()
            return cur.rowcount

    # ── universe and index symbol lists (from DB, never hard-coded) ──
    def index_symbols(self) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT symbol FROM indices ORDER BY index_id").fetchall()
        return [r["symbol"] for r in rows]

    def universe_symbols(self, limit: int = 500) -> list[str]:
        """NIFTY 500 constituents (or best-available universe) for batched refresh."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT ic.symbol FROM index_constituents ic"
                " JOIN indices i ON i.index_id = ic.index_id"
                " WHERE i.universe = 1 LIMIT ?", (limit,),
            ).fetchall()
        return [r["symbol"] for r in rows]

    def upsert_status(self, status) -> None:
        """Persist market status into quotes_latest under a reserved symbol."""
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO quotes_latest (symbol, exchange, last, prev_close, change,
                    change_pct, open, high, low, volume, value, as_of, fetched_at,
                    provider, price_kind, session, is_stale, error)
                VALUES ('__market_status__', NULL, NULL, NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL, ?, ?, 'calendar', 'status', ?, 0, NULL)
                ON CONFLICT(symbol) DO UPDATE SET
                    as_of=excluded.as_of,
                    fetched_at=excluded.fetched_at,
                    provider=excluded.provider,
                    session=excluded.session,
                    is_stale=0
                """,
                (status.as_of, iso_utc(), status.state),
            )
            conn.commit()

    def get_market_status_row(self) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM quotes_latest WHERE symbol = '__market_status__'"
            ).fetchone()
        return dict(row) if row else None

    def recent_job_runs(self, limit: int = 20) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def add_symbol_aliases(db, aliases: list[tuple[str, str]]) -> None:
    """aliases: [(alias, canonical_symbol)] — keeps legacy Yahoo symbols working."""
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO symbol_aliases (alias, symbol, created_at)
            VALUES (?, ?, ?)
            """,
            [(alias, sym, iso_utc()) for alias, sym in aliases],
        )
        conn.commit()
