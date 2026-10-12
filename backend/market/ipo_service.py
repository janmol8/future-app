"""IPO service — provider sync, explicit status state machine, and the
listing-day automation that resolves a listed IPO to its exchange symbol.

State machine (idempotent transitions, every change logged to ipo_events):
    Upcoming -> Open -> Closed -> Allotment -> Listed   (+ Withdrawn | Postponed)
Status derives from provider data first, then provider dates in IST. The
listing-day loop matches by ISIN first, then normalised name — it never
guesses a symbol; unresolved cases go to review_queue as 'symbol pending'.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from .discovery import normalize_name
from .timeutil import ist_now, iso_utc

logger = logging.getLogger(__name__)

VALID_STATUSES = [
    "Upcoming", "Open", "Closed", "Allotment", "Listed",
    "Listed - symbol pending", "Withdrawn", "Postponed",
]


def derive_status(ipo_date_map: dict, today: date | None = None) -> str | None:
    """Derive status from dates when the provider doesn't give one.
    ipo_date_map: open/close/allotment/listing dates (date or None)."""
    today = today or ist_now().date()
    open_d = ipo_date_map.get("open")
    close_d = ipo_date_map.get("close")
    allot_d = ipo_date_map.get("allotment")
    listing_d = ipo_date_map.get("listing")
    if listing_d and today >= listing_d:
        return "Listed"
    if allot_d and today >= allot_d:
        return "Allotment"
    if close_d and today > close_d:
        return "Closed"
    if open_d and open_d <= today <= (close_d or open_d):
        return "Open"
    if open_d and today < open_d:
        return "Upcoming"
    return None


class IpoService:
    def __init__(self, db, ipo_providers, discovery=None, gmp_provider=None):
        self.db = db
        self.ipo_providers = [p for p in ipo_providers if p is not None]
        self.discovery = discovery
        self.gmp_provider = gmp_provider
        self.events: list[dict] = []

    def _emit(self, event_type: str, payload: dict) -> None:
        self.events.append({"type": event_type, "payload": payload, "at": iso_utc()})
        logger.info("ipo event: %s (%s)", event_type, payload.get("name"))

    # ── provider sync ──
    def refresh_from_providers(self) -> dict:
        stats = {"fetched": 0, "inserted": 0, "updated": 0}
        for provider in self.ipo_providers:
            try:
                records = provider.get_ipo_list()
            except Exception as exc:  # noqa: BLE001
                logger.warning("ipo provider %s failed: %s", provider.name, exc)
                continue
            for rec in records or []:
                stats["fetched"] += 1
                self._upsert_ipo(rec, provider.name, stats)
        return stats

    def _upsert_ipo(self, rec, provider_name: str, stats: dict) -> None:
        now = iso_utc()
        derived = derive_status({
            "open": rec.open_date, "close": rec.close_date,
            "allotment": rec.allotment_date, "listing": rec.listing_date,
        }) if rec.status is None else rec.status
        status = derived or "Upcoming"
        if status not in VALID_STATUSES:
            status = "Upcoming"
        dedupe = normalize_name(rec.name)

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM ipos WHERE dedupe_key = ?", (dedupe,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO ipos (dedupe_key, name, ipo_type, status, open_date,
                        close_date, allotment_date, listing_date, price_band_low,
                        price_band_high, issue_price, lot_size, issue_size_cr,
                        fresh_issue_cr, ofs_cr, exchanges, isin, source,
                        created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (dedupe, rec.name, rec.ipo_type, status,
                     rec.open_date.isoformat() if rec.open_date else None,
                     rec.close_date.isoformat() if rec.close_date else None,
                     rec.allotment_date.isoformat() if rec.allotment_date else None,
                     rec.listing_date.isoformat() if rec.listing_date else None,
                     rec.price_band_low, rec.price_band_high, rec.issue_price,
                     rec.lot_size, rec.issue_size_cr, rec.fresh_issue_cr,
                     rec.ofs_cr, json.dumps(rec.exchanges or []), rec.isin,
                     provider_name, now, now),
                )
                stats["inserted"] += 1
                self._emit("new_ipo_announced", {"name": rec.name, "status": status})
                row = conn.execute(
                    "SELECT * FROM ipos WHERE dedupe_key = ?", (dedupe,)
                ).fetchone()
            else:
                conn.execute(
                    """
                    UPDATE ipos SET ipo_type=COALESCE(?, ipo_type),
                        open_date=COALESCE(?, open_date), close_date=COALESCE(?, close_date),
                        allotment_date=COALESCE(?, allotment_date),
                        listing_date=COALESCE(?, listing_date),
                        price_band_low=COALESCE(?, price_band_low),
                        price_band_high=COALESCE(?, price_band_high),
                        issue_price=COALESCE(?, issue_price), lot_size=COALESCE(?, lot_size),
                        issue_size_cr=COALESCE(?, issue_size_cr), isin=COALESCE(?, isin),
                        exchanges=?, updated_at=?
                    WHERE ipo_id=?
                    """,
                    (rec.ipo_type,
                     rec.open_date.isoformat() if rec.open_date else None,
                     rec.close_date.isoformat() if rec.close_date else None,
                     rec.allotment_date.isoformat() if rec.allotment_date else None,
                     rec.listing_date.isoformat() if rec.listing_date else None,
                     rec.price_band_low, rec.price_band_high, rec.issue_price,
                     rec.lot_size, rec.issue_size_cr, rec.isin,
                     json.dumps(rec.exchanges or []), now, row["ipo_id"]),
                )
                stats["updated"] += 1

            self._transition(conn, row, status)
            conn.commit()

    def _transition(self, conn, row, new_status: str) -> None:
        """Idempotent state transition with audit log."""
        old = row["status"]
        if old == new_status:
            return
        conn.execute(
            "UPDATE ipos SET status = ?, updated_at = ? WHERE ipo_id = ?",
            (new_status, iso_utc(), row["ipo_id"]),
        )
        conn.execute(
            "INSERT INTO ipo_events (ipo_id, event_type, from_status, to_status, detail, created_at)"
            " VALUES (?, 'status_change', ?, ?, NULL, ?)",
            (row["ipo_id"], old, new_status, iso_utc()),
        )
        if new_status == "Open":
            self._emit("ipo_open", {"name": row["name"]})
        elif new_status == "Listed":
            self._emit("ipo_listed", {"name": row["name"]})

    # ── listing-day loop ──
    def run_listing_day_loop(self) -> dict:
        """From the listing date, resolve the security by ISIN first, then by
        normalised name, against tracked listings. On success attach the
        company, mark Listed, emit events. Failures land in review_queue."""
        stats = {"checked": 0, "resolved": 0, "pending": 0}
        today = ist_now().date()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ipos WHERE listing_date IS NOT NULL AND status LIKE 'Listed%'"
                " AND company_id IS NULL"
            ).fetchall()
        for row in rows:
            stats["checked"] += 1
            listing_d = date.fromisoformat(row["listing_date"])
            if listing_d > today:
                continue
            match = self._find_listing(conn, row)
            if match is None:
                stats["pending"] += 1
                self._mark_symbol_pending(conn, row)
                continue
            listing, company = match
            with self.db.connect() as conn2:
                conn2.execute(
                    "UPDATE ipos SET company_id = ?, status = 'Listed', updated_at = ?"
                    " WHERE ipo_id = ?",
                    (company["company_id"], iso_utc(), row["ipo_id"]),
                )
                conn2.execute(
                    "INSERT INTO ipo_events (ipo_id, event_type, from_status, to_status,"
                    " detail, created_at) VALUES (?, 'symbol_resolved', 'Listed - symbol pending',"
                    " 'Listed', ?, ?)",
                    (row["ipo_id"],
                     json.dumps({"exchange": listing["exchange"], "symbol": listing["symbol"]}),
                     iso_utc()),
                )
                conn2.commit()
            stats["resolved"] += 1
            self._emit("ipo_listed", {
                "name": row["name"],
                "exchange": listing["exchange"], "symbol": listing["symbol"],
            })
        return stats

    def _find_listing(self, conn, ipo_row):
        """ISIN first, then normalised name match on companies. Returns
        (listing, company) or None — never guesses."""
        if ipo_row["isin"]:
            listing = conn.execute(
                "SELECT * FROM listings WHERE isin = ? LIMIT 1", (ipo_row["isin"],)
            ).fetchone()
            if listing:
                company = conn.execute(
                    "SELECT * FROM companies WHERE company_id = ?", (listing["company_id"],)
                ).fetchone()
                if company:
                    return listing, company
        normalized = normalize_name(ipo_row["name"])
        if normalized:
            company = conn.execute(
                "SELECT * FROM companies WHERE normalized_name = ?", (normalized,)
            ).fetchone()
            if company:
                listing = conn.execute(
                    "SELECT * FROM listings WHERE company_id = ? ORDER BY listing_id LIMIT 1",
                    (company["company_id"],),
                ).fetchone()
                if listing:
                    return listing, company
        return None

    def _mark_symbol_pending(self, conn, row) -> None:
        """Keep 'Listed - symbol pending', queue for review once per state."""
        if row["status"] == "Listed - symbol pending":
            return
        conn.execute(
            "UPDATE ipos SET status = 'Listed - symbol pending', updated_at = ? WHERE ipo_id = ?",
            (iso_utc(), row["ipo_id"]),
        )
        conn.execute(
            "INSERT INTO review_queue (kind, payload, created_at) VALUES (?, ?, ?)",
            ("ipo_symbol_pending",
             json.dumps({"ipo_id": row["ipo_id"], "name": row["name"],
                         "isin": row["isin"], "listing_date": row["listing_date"]}),
             iso_utc()),
        )
        conn.commit()

    # ── GMP (manual entry arrives via admin API into ipo_gmp_snapshots) ──
    def refresh_gmp(self) -> dict:
        return {"gmp_enabled": self.gmp_provider is not None}

    # ── derived fields for the API ──
    @staticmethod
    def serialize(row: dict) -> dict:
        """Company-facing shape with derived, labelled fields (N/A when missing)."""
        issue = row.get("issue_price")
        out = dict(row)
        for key in ("listing_price_nse", "listing_price_bse"):
            price = row.get(key)
            out["listing_gain_pct_" + key.split("_")[-1]] = (
                round((price - issue) / issue * 100.0, 2)
                if (price and issue) else None
            )
        return out
