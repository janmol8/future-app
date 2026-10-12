"""Phase 3 tests: event bus -> notifications (dedupe, throttle, prefs),
durable price alerts evaluated only against real stored quotes, legacy
alert import, and the new API endpoints."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from database import Database
from market.alerts_service import AlertNotificationService, DEFAULT_USER_KEY


@pytest.fixture()
def db(tmp_path):
    return Database(data_dir=str(tmp_path))


@pytest.fixture()
def svc(db):
    return AlertNotificationService(db, store=None)


class FakeStore:
    def __init__(self, quotes=None):
        self.quotes = quotes or {}

    def get_quote(self, symbol):
        return self.quotes.get(symbol.upper())


# ── event fan-out ──
def test_emit_inserts_notification_row(db, svc):
    # Domain events honour prefs (default OFF); price alerts bypass them.
    assert svc.emit("ipo_listed", {"name": "Newco"}) == 0      # not opted in
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notification_prefs (user_key, event_type, enabled, updated_at)"
            " VALUES (?, 'ipo_listed', 1, datetime('now'))",
            (DEFAULT_USER_KEY,),
        )
        conn.commit()
    n = svc.emit("ipo_listed", {"name": "Newco"}, title="Newco listed")
    assert n == 1
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM notifications").fetchall()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "ipo_listed"
    assert rows[0]["read_at"] is None


def test_emit_same_dedupe_key_is_idempotent(db, svc):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notification_prefs (user_key, event_type, enabled, updated_at)"
            " VALUES (?, 'ipo_listed', 1, datetime('now'))",
            (DEFAULT_USER_KEY,),
        )
        conn.commit()
    payload = {"name": "Newco"}
    assert svc.emit("ipo_listed", payload, dedupe_key="ipo_listed:Newco") == 1
    assert svc.emit("ipo_listed", payload, dedupe_key="ipo_listed:Newco") == 0
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    assert count == 1


def test_emit_unknown_event_type_dropped(svc):
    assert svc.emit("totally_made_up", {"x": 1}) == 0


def test_prefs_default_off_and_optin_works(db, svc):
    # Not opted in: nothing stored.
    assert svc.emit("new_stock_added", {"symbol": "NEWCO"}) == 0
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notification_prefs (user_key, event_type, enabled, updated_at)"
            " VALUES (?, 'new_stock_added', 1, datetime('now'))",
            (DEFAULT_USER_KEY,),
        )
        conn.commit()
    assert svc.emit("new_stock_added", {"symbol": "NEWCO2"}) == 1


def test_price_alert_bypasses_prefs(db, svc):
    assert svc.emit("price_alert", {"symbol": "TCS"},
                    dedupe_key="price_alert:1:x", title="t") == 1


def test_throttle_blocks_repeats_within_window(db, svc):
    # Same subject (TCS) with different dedupe keys: the throttle (not the
    # UNIQUE constraint) must block the second within the window.
    assert svc.emit("price_alert", {"symbol": "TCS"},
                    dedupe_key="price_alert:9:2026-09-28", title="a") == 1
    assert svc.emit("price_alert", {"symbol": "TCS"},
                    dedupe_key="price_alert:9:2026-09-29", title="b") == 0
    # A different subject is not throttled.
    assert svc.emit("price_alert", {"symbol": "INFY"},
                    dedupe_key="price_alert:8:2026-09-28", title="c") == 1


# ── price alerts ──
def test_price_alert_requires_valid_condition(svc):
    with pytest.raises(ValueError):
        svc.create_price_alert("TCS", "sideways", 100)
    with pytest.raises(ValueError):
        svc.create_price_alert("TCS", "above", -5)


def test_price_alert_no_quote_never_fires(db, svc):
    svc.store = FakeStore({})          # no data at all
    svc.create_price_alert("TCS", "above", 100)
    assert svc.evaluate_price_alerts() == []


def test_price_alert_fires_on_real_quote_and_notifies(db, svc):
    svc.store = FakeStore({"TCS": SimpleNamespace(last=101.0)})
    svc.create_price_alert("TCS", "above", 100)
    fired = svc.evaluate_price_alerts()
    assert len(fired) == 1
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM notifications").fetchone()
        alerts = conn.execute("SELECT triggered FROM user_price_alerts").fetchone()
    assert row is not None and row["event_type"] == "price_alert"
    assert alerts["triggered"] == 1


def test_price_alert_fires_once_only(db, svc):
    svc.store = FakeStore({"TCS": SimpleNamespace(last=101.0)})
    svc.create_price_alert("TCS", "above", 100)
    assert len(svc.evaluate_price_alerts()) == 1
    assert svc.evaluate_price_alerts() == []


def test_price_alert_below_condition(db, svc):
    svc.store = FakeStore({"INFY": SimpleNamespace(last=99.0)})
    svc.create_price_alert("INFY", "below", 100)
    assert len(svc.evaluate_price_alerts()) == 1


def test_delete_price_alert(db, svc):
    alert = svc.create_price_alert("TCS", "above", 100)
    assert svc.delete_price_alert(alert["id"]) is True
    assert svc.delete_price_alert(alert["id"]) is False
    assert svc.list_price_alerts() == []


# ── legacy import ──
def test_import_legacy_alerts_idempotent(db, svc):
    legacy = [
        {"id": 1727500000000, "symbol": "TCS", "condition": ">",
         "value": 3500, "email": "a@b.c"},
        {"id": 1727500000001, "symbol": "INFY", "condition": "below",
         "value": 1400, "email": None},
        {"id": 1727500000002, "symbol": "BAD", "condition": "???", "value": 1},
    ]
    assert svc.import_legacy_alerts(legacy) == 2
    assert svc.import_legacy_alerts(legacy) == 0        # idempotent (legacy_id UNIQUE)
    alerts = svc.list_price_alerts()
    assert {a["symbol"] for a in alerts} == {"TCS", "INFY"}
    assert {a["condition"] for a in alerts} == {"above", "below"}


# ── reads ──
def test_unread_and_mark_read(db, svc):
    svc.emit("price_alert", {"symbol": "TCS"}, dedupe_key="d1", title="t1")
    svc.emit("price_alert", {"symbol": "INFY"}, dedupe_key="d2", title="t2")
    assert svc.unread_count() == 2
    svc.mark_read()
    assert svc.unread_count() == 0


# ── blueprint endpoints ──
@pytest.fixture()
def client(tmp_path, db):
    from flask import Flask
    from market import blueprint as bp
    from market.runtime import MarketRuntime

    runtime = MarketRuntime(db)
    runtime.alerts = AlertNotificationService(db, store=FakeStore({}))
    app = Flask(__name__)
    app.register_blueprint(bp.market_bp)
    bp.init_market_blueprint(runtime, [], __import__("threading").Lock())
    return app.test_client()


def test_alerts_api_roundtrip(client):
    r = client.post("/api/market/alerts", json={
        "symbol": "tcs", "condition": "above", "value": 3500,
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body["alert"]["symbol"] == "TCS"
    r = client.get("/api/market/alerts")
    assert len(r.get_json()["alerts"]) == 1
    alert_id = body["alert"]["id"]
    assert client.delete(f"/api/market/alerts/{alert_id}").status_code == 200
    assert client.delete(f"/api/market/alerts/{alert_id}").status_code == 404


def test_alerts_api_rejects_bad_condition(client):
    r = client.post("/api/market/alerts", json={
        "symbol": "TCS", "condition": "equal", "value": 1,
    })
    assert r.status_code == 400


def test_notifications_api_lists_and_marks_read(client):
    runtime = __import__("market.blueprint", fromlist=["_runtime"])._runtime
    runtime.alerts.emit("price_alert", {"symbol": "TCS"},
                        dedupe_key="api-test:1", title="hello")
    r = client.get("/api/market/notifications?user_key=local")
    body = r.get_json()
    assert body["unread"] >= 1
    r = client.post("/api/market/notifications/read", json={"user_key": "local"})
    assert r.get_json()["unread"] == 0
