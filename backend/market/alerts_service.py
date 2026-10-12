"""Alerts + notifications (Phase 3).

Two halves:

1. Event fan-out. Services emit domain events (new_stock_added,
   new_ipo_announced, ipo_open, ipo_listed, price_alert). Each event becomes a
   row in `notifications` per opted-in user — the UNIQUE(user_key, dedupe_key)
   constraint makes re-emission idempotent, and a throttle window stops
   repeat spam. Per-user prefs live in `notification_prefs` (default OFF);
   `price_alert` notifications bypass prefs because the user explicitly
   created the alert.

2. Price alerts, persisted in `user_price_alerts` (replacing the old
   in-memory USER_ALERTS list which died on every restart). Evaluation reads
   quotes ONLY from the MarketStore — missing/stale prices are skipped, never
   guessed, so alerts can never fire off fake data.
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta

from .config import ALERT_SUBSCRIPTION_THROTTLE_MINUTES
from .timeutil import iso_utc, parse_iso, utc_now

logger = logging.getLogger(__name__)

DEFAULT_USER_KEY = "local"
DEFAULT_ON_EVENTS = frozenset({"new_ipo_announced", "ipo_open"})

EVENT_TYPES = {
    "new_stock_added", "new_ipo_announced", "ipo_open", "ipo_listed",
    "ipo_closing_soon", "price_alert",
}

_CONDITION_MAP = {
    ">": "above", ">=": "above", "above": "above", "gte": "above",
    "<": "below", "<=": "below", "below": "below", "lte": "below",
}


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


class AlertNotificationService:
    def __init__(self, db, store=None):
        self.db = db
        self.store = store
        self._throttle: dict[tuple, object] = {}
        self._lock = threading.Lock()
        self._ensure_schema()

    # ── schema ──
    def _ensure_schema(self) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_price_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_key TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    condition TEXT NOT NULL,          -- above | below
                    target_price REAL NOT NULL,
                    email TEXT,
                    triggered INTEGER NOT NULL DEFAULT 0,
                    triggered_at TEXT,
                    legacy_id TEXT,                   -- dedupe for one-time imports
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alerts_user ON user_price_alerts (user_key)"
            )
            # Migrate tables created before legacy_id existed, then enforce
            # uniqueness via index (ALTER TABLE cannot add UNIQUE columns;
            # multiple NULLs are allowed, which is what we want).
            if not _column_exists(conn, "user_price_alerts", "legacy_id"):
                conn.execute("ALTER TABLE user_price_alerts ADD COLUMN legacy_id TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_legacy"
                " ON user_price_alerts (legacy_id)"
            )
            conn.commit()

    # ── legacy migration ──
    def import_legacy_alerts(self, legacy_alerts: list[dict]) -> int:
        """One-time import of the old in-memory USER_ALERTS rows. Safe to
        call repeatedly: legacy rows have millisecond-epoch ids, which are
        reused as the dedupe key so nothing is imported twice."""
        if not legacy_alerts:
            return 0
        imported = 0
        now = iso_utc()
        with self.db.connect() as conn:
            for a in legacy_alerts:
                try:
                    legacy_id = str(a.get("id") or "")
                    symbol = str(a.get("symbol") or "").strip()
                    target = float(a.get("value"))
                    condition = _CONDITION_MAP.get(str(a.get("condition") or "").strip().lower())
                    if not legacy_id or not symbol or target <= 0 or condition is None:
                        continue
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO user_price_alerts"
                        " (user_key, symbol, condition, target_price, email, legacy_id, created_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (DEFAULT_USER_KEY, symbol, condition, target,
                         a.get("email") or None, legacy_id, now),
                    )
                    imported += cur.rowcount if cur.rowcount > 0 else 0
                except (TypeError, ValueError):
                    continue
            conn.commit()
        if imported:
            logger.info("imported %d legacy USER_ALERTS into user_price_alerts", imported)
        return imported

    # ── price alert CRUD ──
    def create_price_alert(self, symbol: str, condition: str, value: float,
                           email: str | None = None,
                           user_key: str = DEFAULT_USER_KEY) -> dict:
        cond = _CONDITION_MAP.get((condition or "").strip().lower())
        if not symbol or cond is None:
            raise ValueError("symbol and condition (above/below) required")
        value = float(value)
        if value <= 0:
            raise ValueError("target price must be positive")
        now = iso_utc()
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO user_price_alerts"
                " (user_key, symbol, condition, target_price, email, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (user_key, symbol.strip().upper(), cond, value, email or None, now),
            )
            alert_id = cur.lastrowid
            conn.commit()
        return {"id": alert_id, "symbol": symbol.strip().upper(), "condition": cond,
                "value": value, "email": email or None, "triggered": False,
                "created_at": now}

    def delete_price_alert(self, alert_id: int, user_key: str = DEFAULT_USER_KEY) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute(
                "DELETE FROM user_price_alerts WHERE id = ? AND user_key = ?",
                (alert_id, user_key),
            )
            conn.commit()
        return cur.rowcount > 0

    def list_price_alerts(self, user_key: str = DEFAULT_USER_KEY) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id, symbol, condition, target_price AS value, email,"
                " triggered, triggered_at, created_at FROM user_price_alerts"
                " WHERE user_key = ? ORDER BY id DESC",
                (user_key,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── evaluation ──
    def evaluate_price_alerts(self) -> list[dict]:
        """Check every untriggered alert against the latest REAL stored quote.
        Missing quotes are skipped — never synthesised."""
        if self.store is None:
            return []
        with self.db.connect() as conn:
            alerts = conn.execute(
                "SELECT * FROM user_price_alerts WHERE triggered = 0"
            ).fetchall()
        fired = []
        for a in alerts:
            quote = self.store.get_quote(a["symbol"])
            last = getattr(quote, "last", None) if quote is not None else None
            if last is None:
                continue
            hit = (a["condition"] == "above" and last >= a["target_price"]) or \
                  (a["condition"] == "below" and last <= a["target_price"])
            if not hit:
                continue
            now = iso_utc()
            with self.db.connect() as conn:
                cur = conn.execute(
                    "UPDATE user_price_alerts SET triggered = 1, triggered_at = ?"
                    " WHERE id = ? AND triggered = 0",
                    (now, a["id"]),
                )
                conn.commit()
            if cur.rowcount == 0:
                continue
            fired.append(dict(a))
            self.emit(
                "price_alert",
                user_keys=[a["user_key"]],
                dedupe_key=f"price_alert:{a['id']}:{now[:10]}",
                title=f"{a['symbol']} alert hit",
                body=f"{a['symbol']} is now {last:g} "
                     f"({'above' if a['condition'] == 'above' else 'below'} your "
                     f"target {a['target_price']:g}). Data may be delayed.",
            )
        return fired

    # ── event fan-out ──
    def emit(self, event_type: str, payload: dict | None = None,
             user_keys: list[str] | None = None, dedupe_key: str | None = None,
             title: str | None = None, body: str | None = None) -> int:
        """Persist a notification per user. Returns rows actually inserted
        (0 when throttled, deduped, or not opted in)."""
        if event_type not in EVENT_TYPES:
            logger.debug("dropping unknown event type %s", event_type)
            return 0
        payload = payload or {}
        user_keys = user_keys or [DEFAULT_USER_KEY]
        inserted = 0
        now = utc_now()
        with self._lock:
            self._throttle = {k: v for k, v in self._throttle.items()
                              if (now - v).total_seconds() < ALERT_SUBSCRIPTION_THROTTLE_MINUTES * 60}
        with self.db.connect() as conn:
            for user_key in user_keys:
                dk = (dedupe_key or f"{event_type}:{payload.get('symbol') or payload.get('name') or ''}")
                dk = dk[:180]
                # Throttle per (user, event, subject) so repeated emissions
                # about the same thing cannot spam the inbox even when the
                # dedupe key varies (e.g. per-day alert refires).
                subject = str(payload.get("symbol") or payload.get("name") or dk)[:64]
                with self._lock:
                    key = (user_key, event_type, subject)
                    last = self._throttle.get(key)
                    if last is not None and \
                       (now - last).total_seconds() < ALERT_SUBSCRIPTION_THROTTLE_MINUTES * 60:
                        continue
                # Prefs: everything OFF until the user opts in — except
                # price alerts they created themselves.
                if event_type != "price_alert":
                    pref = conn.execute(
                        "SELECT enabled FROM notification_prefs"
                        " WHERE user_key = ? AND event_type = ?",
                        (user_key, event_type),
                    ).fetchone()
                    # New-IPO / IPO-open alerts are ON by default so a fresh
                    # release is never missed; a saved pref still wins.
                    if pref is None:
                        if event_type not in DEFAULT_ON_EVENTS:
                            continue
                    elif not pref["enabled"]:
                        continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO notifications"
                    " (user_key, event_type, title, body, dedupe_key, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (user_key, event_type,
                     title or f"{event_type}: {payload.get('symbol') or payload.get('name') or ''}",
                     body or (", ".join(f"{k}={v}" for k, v in sorted(payload.items()))
                              if payload else None),
                     dk, iso_utc(now)),
                )
                inserted += cur.rowcount if cur.rowcount > 0 else 0
                with self._lock:
                    self._throttle[(user_key, event_type, subject)] = now
            conn.commit()
        if inserted:
            logger.info("notifications: %d row(s) for %s", inserted, event_type)
        return inserted

    def drain_service_events(self, events: list[dict]) -> int:
        """Map service-layer events (discovery/ipo) to notifications."""
        count = 0
        for ev in events or []:
            count += self.emit(
                ev.get("type") or "",
                payload=ev.get("payload") or {},
                dedupe_key=f"{ev.get('type')}:{(ev.get('payload') or {}).get('symbol') or (ev.get('payload') or {}).get('name') or ''}",
            )
        return count

    # ── reads ──
    def unread_count(self, user_key: str = DEFAULT_USER_KEY) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_key = ? AND read_at IS NULL",
                (user_key,),
            ).fetchone()
        return row[0] if row else 0

    def mark_read(self, user_key: str = DEFAULT_USER_KEY,
                  notification_id: int | None = None) -> int:
        with self.db.connect() as conn:
            if notification_id is None:
                cur = conn.execute(
                    "UPDATE notifications SET read_at = ?"
                    " WHERE user_key = ? AND read_at IS NULL",
                    (iso_utc(), user_key),
                )
            else:
                cur = conn.execute(
                    "UPDATE notifications SET read_at = ?"
                    " WHERE user_key = ? AND id = ? AND read_at IS NULL",
                    (iso_utc(), user_key, notification_id),
                )
            conn.commit()
        return cur.rowcount
