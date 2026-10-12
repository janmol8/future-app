"""Market Blueprint — new API surface. Existing routes (/api/quote, /api/ohlc,
/api/symbols, /api/stream) keep working via shims in app.py; this Blueprint
serves the richer endpoints. API routes read from the DB/cache only — never
call providers directly (only the scheduler does).
"""
from __future__ import annotations

import json
import queue

from flask import Blueprint, jsonify, request

from .timeutil import iso_utc
from .alerts_service import DEFAULT_USER_KEY

market_bp = Blueprint("market", __name__)

# Set by init_market_blueprint(app, runtime).
_runtime = None
_event_queues: list[queue.Queue] = []
_sse_lock = None


def push_quote_events(quotes: list[dict]) -> None:
    """Called by the runtime after scheduler quote refreshes; pushes changes
    to all connected SSE clients without blocking the scheduler."""
    for q in list(_event_queues):
        try:
            q.put_nowait({"type": "quotes", "quotes": quotes})
        except queue.Full:
            pass  # slow client: drop rather than back-pressure the scheduler


def init_market_blueprint(runtime, event_queues, sse_lock) -> None:
    global _runtime, _event_queues, _sse_lock
    _runtime = runtime
    _event_queues = event_queues
    _sse_lock = sse_lock


def _db():
    return _runtime.db


def _na(value):
    """Backend contract: missing = None; the UI renders N/A."""
    return value


# ── market overview ──
@market_bp.route("/api/market/overview")
def market_overview():
    store = _runtime.store
    indices = []
    with _db().connect() as conn:
        rows = conn.execute("SELECT * FROM indices ORDER BY universe DESC, index_id").fetchall()
        index_rows = [dict(r) for r in rows]
    for idx in index_rows:
        quote = store.get_quote(idx["symbol"])
        indices.append({
            "symbol": idx["symbol"],
            "name": idx["name"],
            "exchange": idx["exchange"],
            "quote": quote,
        })
    return jsonify({
        "indices": indices,
        "market_status": _runtime.store.get_market_status_row(),
        "new_listings": _runtime.discovery.recently_listed()[:10],
        "ipos": _ipo_summaries(),
        "last_updated": iso_utc(),
        "sources": _runtime.router.status()["chain"],
    })


def _ipo_summaries() -> list[dict]:
    with _db().connect() as conn:
        rows = conn.execute(
            "SELECT ipo_id, name, ipo_type, status, open_date, close_date, listing_date"
            " FROM ipos ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
    return [dict(r) for r in rows]


# ── market status ──
@market_bp.route("/api/market/status")
def market_status():
    row = _runtime.store.get_market_status_row()
    if row is None:
        return jsonify({"state": "UNKNOWN", "holiday_name": None, "next_open": None}), 200
    return jsonify({
        "state": row.get("session"),
        "as_of": row.get("as_of"),
        "fetched_at": row.get("fetched_at"),
        "provider": row.get("provider"),
    })


# ── stock search (symbol / name / ISIN / BSE code / legacy Yahoo) ──
@market_bp.route("/api/market/search")
def market_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    like = f"%{q.upper()}%"
    alias = q.upper()
    with _db().connect() as conn:
        rows = conn.execute(
            """
            SELECT l.symbol, l.exchange, l.isin, l.bse_code, c.company_name, c.company_id
            FROM listings l JOIN companies c ON c.company_id = l.company_id
            WHERE UPPER(l.symbol) LIKE ? OR UPPER(c.company_name) LIKE ?
               OR UPPER(l.isin) = ? OR CAST(l.bse_code AS TEXT) = ?
               OR UPPER(l.symbol) IN (SELECT symbol FROM symbol_aliases WHERE UPPER(alias) = ?)
            LIMIT 20
            """,
            (like, like, q.upper(), q, alias),
        ).fetchall()
    return jsonify({"results": [
        {
            "symbol": r["symbol"], "exchange": r["exchange"],
            "name": r["company_name"], "isin": r["isin"],
            "bse_code": r["bse_code"], "company_id": r["company_id"],
        }
        for r in rows
    ]})


# ── stock detail ──
@market_bp.route("/api/market/stocks/<symbol>")
def stock_detail(symbol: str):
    symbol = symbol.strip().upper()[:32]
    store = _runtime.store
    quote = store.get_quote(symbol)
    with _db().connect() as conn:
        row = conn.execute(
            """
            SELECT l.symbol, l.exchange, l.isin, l.bse_code, l.series, l.listed_on,
                   c.company_name, c.company_id, c.sector
            FROM listings l JOIN companies c ON c.company_id = l.company_id
            WHERE UPPER(l.symbol) = ? LIMIT 1
            """,
            (symbol,),
        ).fetchone()
    listing = dict(row) if row else None
    return jsonify({
        "symbol": symbol,
        "listing": listing,
        "quote": quote,
        "history_start": _runtime.history.history_start(symbol),
        "last_updated": iso_utc(),
    })


# ── history ──
@market_bp.route("/api/market/history")
def market_history():
    symbol = (request.args.get("symbol") or "").strip().upper()[:32]
    range_key = (request.args.get("range") or "1M").strip()[:4]
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    result = _runtime.history.get_history(symbol, range_key)
    return jsonify(result)


# ── IPO list / detail ──
@market_bp.route("/api/market/ipos")
def ipo_list():
    status_filter = (request.args.get("status") or "").strip()[:32]
    query = "SELECT * FROM ipos"
    params = ()
    if status_filter:
        query += " WHERE status = ?"
        params = (status_filter,)
    query += (" ORDER BY CASE status WHEN 'Open' THEN 0 WHEN 'Upcoming' THEN 1"
              " WHEN 'Closed' THEN 2 WHEN 'Allotment' THEN 3 ELSE 4 END,"
              " open_date IS NULL, open_date DESC")
    with _db().connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return jsonify({"ipos": [_serialize_ipo(dict(r)) for r in rows]})


_last_ipo_refresh = {"t": 0.0}


@market_bp.route("/api/market/ipos/refresh", methods=["POST"])
def ipo_refresh():
    """Fetch IPOs from the providers now (throttled to once per 30 s)."""
    import time as _time
    if _time.time() - _last_ipo_refresh["t"] < 30:
        return jsonify({"throttled": True, "retry_after": 30})
    _last_ipo_refresh["t"] = _time.time()
    stats = _runtime.ipo_service.refresh_from_providers()
    return jsonify({"throttled": False, "stats": stats})


def _serialize_ipo(row: dict) -> dict:
    from .ipo_service import IpoService
    return IpoService.serialize(row)


@market_bp.route("/api/market/ipos/<int:ipo_id>")
def ipo_detail(ipo_id: int):
    with _db().connect() as conn:
        row = conn.execute("SELECT * FROM ipos WHERE ipo_id = ?", (ipo_id,)).fetchone()
        if row is None:
            return jsonify({"error": "not found"}), 404
        subs = conn.execute(
            "SELECT total_x, qib_x, nii_x, retail_x, as_of, source"
            " FROM ipo_subscription_snapshots WHERE ipo_id = ? ORDER BY as_of DESC LIMIT 1",
            (ipo_id,),
        ).fetchone()
        gmp = conn.execute(
            "SELECT gmp_rupees, gmp_pct, as_of, source FROM ipo_gmp_snapshots"
            " WHERE ipo_id = ? ORDER BY as_of DESC LIMIT 1",
            (ipo_id,),
        ).fetchone()
        events = conn.execute(
            "SELECT event_type, from_status, to_status, detail, created_at"
            " FROM ipo_events WHERE ipo_id = ? ORDER BY id DESC LIMIT 30",
            (ipo_id,),
        ).fetchall()
        symbol_info = None
        if row["company_id"]:
            lrow = conn.execute(
                "SELECT exchange, symbol FROM listings WHERE company_id = ?", (row["company_id"],)
            ).fetchone()
            symbol_info = dict(lrow) if lrow else None
    out = _serialize_ipo(dict(row))
    out["subscription"] = dict(subs) if subs else None
    gmp_out = dict(gmp) if gmp else None
    if gmp_out:
        gmp_out["label"] = "GMP — unofficial, as of " + (gmp_out.get("as_of") or "")
    out["gmp"] = gmp_out
    out["events"] = [dict(e) for e in events]
    out["listing"] = symbol_info
    return jsonify(out)


# ── notifications ──
@market_bp.route("/api/market/notifications")
def notifications_list():
    user_key = (request.args.get("user_key") or "").strip()[:64]
    if not user_key:
        return jsonify({"error": "user_key required"}), 400
    with _db().connect() as conn:
        rows = conn.execute(
            "SELECT id, event_type, title, body, created_at, read_at FROM notifications"
            " WHERE user_key = ? ORDER BY id DESC LIMIT 50", (user_key,),
        ).fetchall()
    return jsonify({
        "notifications": [dict(r) for r in rows],
        "unread": _runtime.alerts.unread_count(user_key),
    })


@market_bp.route("/api/market/notifications/read", methods=["POST"])
def notifications_mark_read():
    body = request.get_json(silent=True) or {}
    user_key = (body.get("user_key") or DEFAULT_USER_KEY)
    nid = body.get("id")
    try:
        updated = _runtime.alerts.mark_read(
            user_key, int(nid) if nid is not None else None)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid id"}), 400
    return jsonify({
        "updated": updated,
        "unread": _runtime.alerts.unread_count(user_key),
    })


# ── price alerts (durable; replaces the legacy in-memory USER_ALERTS) ──
@market_bp.route("/api/market/alerts", methods=["GET", "POST"])
def price_alerts_route():
    svc = _runtime.alerts
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        try:
            alert = svc.create_price_alert(
                symbol=str(body.get("symbol") or ""),
                condition=str(body.get("condition") or ""),
                value=body.get("value"),
                email=(body.get("email") or None),
                user_key=(body.get("user_key") or DEFAULT_USER_KEY),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"success": True, "alert": alert}), 201
    user_key = (request.args.get("user_key") or DEFAULT_USER_KEY)
    return jsonify({"alerts": svc.list_price_alerts(user_key)})


@market_bp.route("/api/market/alerts/<int:alert_id>", methods=["DELETE"])
def price_alert_delete(alert_id):
    user_key = (request.args.get("user_key") or DEFAULT_USER_KEY)
    ok = _runtime.alerts.delete_price_alert(alert_id, user_key)
    if ok:
        return jsonify({"success": True})
    return jsonify({"error": "alert not found"}), 404


@market_bp.route("/api/market/notifications/prefs", methods=["GET", "PUT"])
def notification_prefs():
    user_key = (request.args.get("user_key") or "").strip()[:64]
    if not user_key:
        return jsonify({"error": "user_key required"}), 400
    if request.method == "PUT":
        body = request.get_json(silent=True) or {}
        enabled_map = body.get("prefs") or {}
        with _db().connect() as conn:
            for event_type, enabled in list(enabled_map.items())[:20]:
                conn.execute(
                    """
                    INSERT INTO notification_prefs (user_key, event_type, enabled, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_key, event_type) DO UPDATE SET
                        enabled=excluded.enabled, updated_at=excluded.updated_at
                    """,
                    (user_key, str(event_type)[:32], 1 if enabled else 0, iso_utc()),
                )
            conn.commit()
    with _db().connect() as conn:
        rows = conn.execute(
            "SELECT event_type, enabled, email_enabled FROM notification_prefs WHERE user_key = ?",
            (user_key,),
        ).fetchall()
    return jsonify({"prefs": {r["event_type"]: bool(r["enabled"]) for r in rows}})


# ── data health ──
@market_bp.route("/api/market/health")
def market_health():
    return jsonify({
        "config": _runtime_config_summary(),
        "providers": _runtime.store.provider_health(),
        "router": _runtime.router.status(),
        "recent_jobs": _runtime.store.recent_job_runs(10),
        "last_updated": iso_utc(),
    })


def _runtime_config_summary() -> dict:
    from .config import describe
    return describe()


# ── SSE stream (shared store; pushes only changes) ──
@market_bp.route("/api/market/stream")
def market_stream():
    from flask import Response

    symbols_param = (request.args.get("symbols") or "")[:2000]
    symbols = [s.strip().upper()[:32] for s in symbols_param.split(",") if s.strip()][:50]

    def generate():
        q = queue.Queue(maxsize=100)
        with (_sse_lock or __import__("threading").Lock()):
            _event_queues.append(q)
        try:
            # Initial snapshot from the shared store.
            payload = {s: _runtime.store.get_quote(s) for s in symbols}
            yield f"data: {json.dumps({'quotes': payload, 'type': 'snapshot'})}\n\n"
            import time
            deadline = time.monotonic() + 600  # client reconnects periodically
            while time.monotonic() < deadline:
                try:
                    event = q.get(timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield f": keepalive\n\n"
        finally:
            with (_sse_lock or __import__("threading").Lock()):
                if q in _event_queues:
                    _event_queues.remove(q)

    return Response(generate(), mimetype="text/event-stream")
