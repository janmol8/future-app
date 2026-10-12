"""History service — serves candles from price_history, fetching only missing
ranges from the provider chain. New listings return the history they have
(never an error for 5Y); adjusted vs unadjusted is flagged per row.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from .timeutil import iso_utc
from datetime import datetime as _datetime  # noqa: F401  (used in nested funcs)

logger = logging.getLogger(__name__)

# Range name -> (days back, interval served).
RANGES = {
    "1D": (7, "1m"),      # intraday: last 7 days of 1-minute bars (provider limit)
    "1W": (7, "1d"),
    "1M": (30, "1d"),
    "3M": (90, "1d"),
    "6M": (180, "1d"),
    "1Y": (365, "1d"),
    "5Y": (365 * 5, "1w"),
}

# Fetch-multiplying guard: never ask a provider for a range older than this.
MAX_HISTORY_DAYS = 365 * 20

# Windows ending within this many seconds of 'now' can still grow, so they are
# refetched — but at most once per RECENT_REFRESH_MIN_SECONDS per (symbol, interval).
RECENT_GRACE_SECONDS = 6 * 3600
RECENT_REFRESH_MIN_SECONDS = 30 * 60


def _parse_iso(value: str):
    from datetime import datetime
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class HistoryService:
    def __init__(self, db, router):
        self.db = db
        self.router = router

    # ── storage ──
    def _store_candles(self, conn, symbol: str, interval: str, candles) -> None:
        now = iso_utc()
        rows = [
            (symbol, interval, c.time, c.open, c.high, c.low, c.close, c.volume, 0, now)
            for c in candles
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO price_history
                (symbol, interval, ts, open, high, low, close, volume, adjusted, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        if rows:
            conn.execute(
                """
                INSERT INTO history_coverage (symbol, interval, first_ts, last_ts, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval) DO UPDATE SET
                    first_ts = MIN(COALESCE(first_ts, excluded.first_ts), excluded.first_ts),
                    last_ts = MAX(COALESCE(last_ts, excluded.last_ts), excluded.last_ts),
                    updated_at = excluded.updated_at
                """,
                (symbol, interval, rows[0][2], rows[-1][2], now),
            )

    def _read_candles(self, conn, symbol: str, interval: str, start_ts: int, end_ts: int):
        return conn.execute(
            "SELECT ts, open, high, low, close, volume, adjusted FROM price_history"
            " WHERE symbol = ? AND interval = ? AND ts >= ? AND ts <= ? ORDER BY ts",
            (symbol, interval, start_ts, end_ts),
        ).fetchall()

    # ── gap math ──
    def _missing_windows(self, have, start_ts: int, end_ts: int) -> list[tuple[int, int]]:
        """Compute [start,end) windows with no stored candles."""
        if not have:
            return [(start_ts, end_ts)]
        windows = []
        cursor = start_ts
        for row in have:
            if row["ts"] > cursor:
                windows.append((cursor, row["ts"] - 1))
            cursor = max(cursor, row["ts"] + 1)
        if cursor <= end_ts:
            windows.append((cursor, end_ts))
        return windows

    def _attempted_ledger(self, conn, symbol: str, interval: str) -> list:
        return conn.execute(
            "SELECT first_ts, last_ts, updated_at FROM history_coverage"
            " WHERE symbol = ? AND interval = ?",
            (symbol, interval + "@attempted"),
        ).fetchall()

    def _record_attempted(self, conn, symbol: str, interval: str,
                          w_start: int, w_end: int) -> None:
        now = iso_utc()
        conn.execute(
            """
            INSERT INTO history_coverage (symbol, interval, first_ts, last_ts, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol, interval) DO UPDATE SET
                first_ts = MIN(COALESCE(first_ts, excluded.first_ts), excluded.first_ts),
                last_ts = MAX(COALESCE(last_ts, excluded.last_ts), excluded.last_ts),
                updated_at = excluded.updated_at
            """,
            (symbol, interval + "@attempted", w_start, w_end, now),
        )

    # ── public ──
    def get_history(self, symbol: str, range_key: str = "1M",
                    interval: str | None = None) -> dict:
        """Candles for a range, from cache first. Returns dict with candles,
        flags (partial, earliest). Never errors on long ranges for new stocks."""
        range_key = (range_key or "1M").upper()
        days_back, default_interval = RANGES.get(range_key, (30, "1d"))
        interval = interval or default_interval
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=min(days_back, MAX_HISTORY_DAYS))
        start_ts, end_ts = int(start_dt.timestamp()), int(end_dt.timestamp())

        with self.db.connect() as conn:
            have = self._read_candles(conn, symbol, interval, start_ts, end_ts)
            windows = self._missing_windows(have, start_ts, end_ts)
            # Skip windows we already asked the provider for and that returned
            # nothing (or only old data) — otherwise every request re-hits the
            # chain for gaps that simply don't exist upstream. Windows touching
            # 'now' are always retried so live history keeps growing.
            attempted = self._attempted_ledger(conn, symbol, interval)
            def _already_attempted(w):
                if w[1] >= end_ts - RECENT_GRACE_SECONDS:
                    # Recent window: retry, but rate-limit to one upstream hit
                    # per RECENT_REFRESH_MIN_SECONDS (ledger's updated_at).
                    # Live prices come from quotes; history may lag slightly.
                    recent = [a for a in attempted
                              if a["first_ts"] <= w[0] and w[1] <= a["last_ts"] and a["updated_at"]]
                    if recent:
                        newest = max(_parse_iso(a["updated_at"]) for a in recent)
                        if newest is not None:
                            age = (datetime.now(timezone.utc) - newest).total_seconds()
                            if age < RECENT_REFRESH_MIN_SECONDS:
                                return True
                    return False
                return any(a["first_ts"] <= w[0] and w[1] <= a["last_ts"] for a in attempted)
            windows = [w for w in windows if not _already_attempted(w)]

            fetched = 0
            for w_start, w_end in windows:
                self._record_attempted(conn, symbol, interval, w_start, w_end)
                candles = self.router.get_historical_candles(
                    symbol,
                    interval,
                    datetime.fromtimestamp(w_start, timezone.utc).date(),
                    datetime.fromtimestamp(w_end, timezone.utc).date(),
                )
                candles = [c for c in candles if w_start <= c.time <= w_end]
                if candles:
                    self._store_candles(conn, symbol, interval, candles)
                    fetched += len(candles)
            if fetched:
                have = self._read_candles(conn, symbol, interval, start_ts, end_ts)

        candles_out = [
            {"time": r["ts"], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]}
            for r in have
        ]
        coverage = None
        if candles_out:
            coverage = {"first": candles_out[0]["time"], "last": candles_out[-1]["time"]}
        return {
            "symbol": symbol,
            "range": range_key,
            "interval": interval,
            "candles": candles_out,
            "coverage": coverage,
            "partial": bool(windows) and fetched == 0 and not have,
            "source": self.router.status()["chain"][0] if self.router.providers else None,
        }

    def history_start(self, symbol: str) -> str | None:
        """Earliest daily bar — the UI's 'history starts <date>' hint."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT MIN(ts) AS first FROM price_history WHERE symbol = ? AND interval = '1d'",
                (symbol,),
            ).fetchone()
        if row and row["first"]:
            return datetime.fromtimestamp(row["first"], timezone.utc).date().isoformat()
        return None
