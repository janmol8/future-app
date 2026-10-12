"""Listing discovery — turns raw instrument-master rows into companies,
listings and aliases using the spec's dedupe order:

  1. ISIN match, 2. existing exchange symbol, 3. normalised name + fuzzy
  score + corroboration (mid-confidence -> review_queue, never auto-merge),
  4. otherwise create.

Content hashes make the daily master diff cheap; series filtering excludes
debt/ETF/MF/warrants; upserts are idempotent. Emits events for new stocks.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import uuid
from datetime import date, timedelta

from .config import EQUITY_SERIES, NEW_LISTING_WINDOW_DAYS
from .providers.base import Instrument
from .timeutil import iso_utc

logger = logging.getLogger(__name__)

# Trailing corporate endings stripped before name comparison. Bare
# "pvt"/"private" are deliberately kept: "X Pvt Ltd" and "X Ltd" are legally
# distinct entities and must not collapse to the same key.
_STOP_SUFFIXES = (" limited", " ltd", " inc", " llp")
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalize_name(name: str) -> str:
    s = _PUNCT.sub(" ", (name or "").lower())
    s = " ".join(s.split())          # collapse whitespace
    changed = True
    while changed and s:
        changed = False
        for suffix in _STOP_SUFFIXES:
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                changed = True
    return s


def content_hash(rows) -> str:
    canonical = json.dumps(
        sorted(
            (r.symbol, r.name, r.isin or "", r.series or "")
            for r in rows
        ),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DiscoveryService:
    def __init__(self, db, router=None):
        self.db = db
        self.router = router
        self.events: list[dict] = []       # drained by the event bus (Phase 3)

    # ── db helpers ──
    def _companies_by_isin(self, conn, isin: str):
        return conn.execute(
            "SELECT c.* FROM companies c JOIN listings l ON l.company_id = c.company_id "
            "WHERE l.isin = ? LIMIT 1", (isin,),
        ).fetchone()

    def _company_by_name(self, conn, normalized: str):
        return conn.execute(
            "SELECT * FROM companies WHERE normalized_name = ?", (normalized,)
        ).fetchone()

    def _listing(self, conn, exchange: str, symbol: str):
        return conn.execute(
            "SELECT * FROM listings WHERE exchange = ? AND symbol = ?",
            (exchange, symbol),
        ).fetchone()

    def _enqueue_review(self, conn, kind: str, payload: dict, score: float) -> None:
        conn.execute(
            "INSERT INTO review_queue (kind, payload, score, created_at) VALUES (?, ?, ?, ?)",
            (kind, json.dumps(payload), score, iso_utc()),
        )

    def _emit(self, event_type: str, payload: dict) -> None:
        self.events.append({"type": event_type, "payload": payload,
                            "at": iso_utc()})
        logger.info("event %s: %s", event_type, payload.get("symbol") or payload.get("company_name"))

    # ── main entry ──
    def sync_master(self, exchange: str, instruments: list[Instrument],
                    content_hash_value: str | None = None) -> dict:
        """Diff one exchange's master against the DB and upsert differences.
        Returns stats; events for newly tracked symbols are queued in self.events."""
        allowed_series = {s.strip().upper() for s in EQUITY_SERIES}
        stats = {"exchange": exchange, "seen": 0, "created_companies": 0,
                 "created_listings": 0, "updated": 0, "review": 0, "skipped_series": 0,
                 "new_listings": []}
        now = iso_utc()

        with self.db.connect() as conn:
            for inst in instruments:
                series = (inst.series or "").upper()
                if series and series not in allowed_series:
                    stats["skipped_series"] += 1
                    continue
                stats["seen"] += 1

                # ── resolve company ──
                company = None
                match_kind = None
                if inst.isin:
                    company = self._companies_by_isin(conn, inst.isin)
                    if company:
                        match_kind = "isin"
                if company is None:
                    listing = self._listing(conn, inst.exchange, inst.symbol)
                    if listing:
                        company = conn.execute(
                            "SELECT * FROM companies WHERE company_id = ?", (listing["company_id"],)
                        ).fetchone()
                        match_kind = "symbol"

                # Rename guard: same ISIN already on file but under a different
                # symbol -> move the existing listing to the new symbol instead of
                # inserting a duplicate listing row. The old symbol is kept as an
                # alias so history and watchlists keep working.
                if company is not None and match_kind == "isin":
                    existing = conn.execute(
                        "SELECT * FROM listings WHERE company_id = ? AND exchange = ?"
                        " AND symbol != ?",
                        (company["company_id"], inst.exchange, inst.symbol),
                    ).fetchone()
                    if existing and self._listing(conn, inst.exchange, inst.symbol) is None:
                        conn.execute(
                            "UPDATE listings SET symbol = ?, updated_at = ? WHERE listing_id = ?",
                            (inst.symbol, now, existing["listing_id"]),
                        )
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_aliases (alias, exchange, symbol, created_at)"
                            " VALUES (?, ?, ?, ?)",
                            (existing["symbol"], inst.exchange, inst.symbol, now),
                        )
                        logger.info("symbol rename: %s -> %s (%s)",
                                    existing["symbol"], inst.symbol, inst.isin)
                        stats["updated"] += 1
                        continue
                if company is None:
                    normalized = normalize_name(inst.name)
                    if normalized:
                        candidate = conn.execute(
                            "SELECT * FROM companies WHERE normalized_name = ?", (normalized,)
                        ).fetchone()
                        if candidate:
                            # Name match needs corroboration; approximate with
                            # symbol similarity as the fuzzy component.
                            fuzzy = difflib.SequenceMatcher(
                                None, normalized, normalize_name(inst.symbol)
                            ).ratio()
                            score = 0.6 + 0.3 * fuzzy
                            if score >= 0.85:
                                company = candidate
                                match_kind = "name"
                            else:
                                # Not a confident merge: record the candidate for
                                # human review, but still create the incoming row as
                                # its own company — ambiguity must never swallow a
                                # real new listing.
                                self._enqueue_review(conn, "duplicate_candidate", {
                                    "incoming": {"symbol": inst.symbol, "name": inst.name,
                                                 "exchange": inst.exchange, "isin": inst.isin},
                                    "candidate_company_id": candidate["company_id"],
                                }, score)
                                stats["review"] += 1
                if company is None:
                    # Create new company.
                    company_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO companies (company_id, company_name, normalized_name, sector,"
                        " listing_date, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (company_id, inst.name, normalize_name(inst.name), None,
                         inst.listing_date.isoformat() if inst.listing_date else None,
                         "active", now, now),
                    )
                    company = conn.execute(
                        "SELECT * FROM companies WHERE company_id = ?", (company_id,)
                    ).fetchone()
                    match_kind = "new"
                    stats["created_companies"] += 1

                # ── resolve listing ──
                listing = self._listing(conn, inst.exchange, inst.symbol)
                if listing is None:
                    conn.execute(
                        "INSERT INTO listings (company_id, exchange, symbol, bse_code, isin,"
                        " series, status, listed_on, delisted_on, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?, 'listed', ?, NULL, ?, ?)",
                        (company["company_id"], inst.exchange, inst.symbol, inst.bse_code,
                         inst.isin, inst.series,
                         inst.listing_date.isoformat() if inst.listing_date else None,
                         now, now),
                    )
                    stats["created_listings"] += 1
                    stats["new_listings"].append({
                        "exchange": inst.exchange, "symbol": inst.symbol,
                        "name": inst.name, "isin": inst.isin,
                        "is_new_listing": (
                            inst.listing_date is not None
                            and (date.today() - inst.listing_date).days <= NEW_LISTING_WINDOW_DAYS
                        ),
                    })
                    self._emit("new_stock_added", {
                        "exchange": inst.exchange, "symbol": inst.symbol,
                        "company_name": inst.name, "isin": inst.isin,
                    })
                else:
                    # Update mutable fields idempotently.
                    isin = inst.isin or listing["isin"]
                    conn.execute(
                        "UPDATE listings SET isin = COALESCE(?, isin),"
                        " series = COALESCE(?, series), bse_code = COALESCE(?, bse_code),"
                        " status = 'listed', delisted_on = NULL, updated_at = ?"
                        " WHERE listing_id = ?",
                        (inst.isin, inst.series, inst.bse_code, now, listing["listing_id"]),
                    )
                    stats["updated"] += 1

                # Alias: legacy Yahoo symbol -> canonical listing.
                if inst.exchange == "NSE":
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_aliases (alias, exchange, symbol, created_at)"
                        " VALUES (?, 'NSE', ?, ?)",
                        (f"{inst.symbol}.NS", inst.symbol, now),
                    )

            # Content-hash memo for this exchange's master.
            if content_hash_value:
                conn.execute(
                    "INSERT OR REPLACE INTO history_coverage (symbol, interval, first_ts, last_ts, updated_at)"
                    " VALUES (?, 'master_hash', NULL, NULL, ?)",
                    (f"master:{exchange}", content_hash_value),
                )
            conn.commit()
        return stats

    def recently_listed(self, window_days=None) -> list[dict]:
        """Listings with listing_date within NEW_LISTING_WINDOW_DAYS of today."""
        window = window_days or NEW_LISTING_WINDOW_DAYS
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=window)).isoformat()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT l.exchange, l.symbol, l.listed_on, c.company_name"
                " FROM listings l JOIN companies c ON c.company_id = l.company_id"
                " WHERE l.listed_on IS NOT NULL AND l.listed_on >= ?"
                " ORDER BY l.listed_on DESC", (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]
