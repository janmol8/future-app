import hmac
import hashlib
from datetime import datetime, timedelta, timezone

from auth_service import AuthService
from subscription_service import SubscriptionService
from database import Database


def test_auth_register_login(tmp_path):
    auth = AuthService(db=Database(data_dir=str(tmp_path)))
    created = auth.register("student@example.com", "password123")
    assert created["success"] is True
    assert created["user"]["email"] == "student@example.com"

    duplicate = auth.register("student@example.com", "password123")
    assert duplicate["success"] is False

    logged_in = auth.login("student@example.com", "password123")
    assert logged_in["success"] is True
    assert logged_in["user"]["id"] == created["user"]["id"]

    bad = auth.login("student@example.com", "wrongpass")
    assert bad["success"] is False


def test_subscription_status_defaults_to_free(tmp_path):
    svc = SubscriptionService(db=Database(data_dir=str(tmp_path)))
    status = svc.status(None)
    assert status["tier"] == "free"
    assert status["active"] is False
    assert "paper_trading" in status["features"]


def test_verify_payment_activates_subscription(tmp_path, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    svc = SubscriptionService(db=Database(data_dir=str(tmp_path)))
    auth = AuthService(db=svc.db)
    user = auth.register("paid@example.com", "password123")["user"]
    order_id = "order_123"
    payment_id = "pay_123"
    signature = hmac.new(
        b"secret",
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    result = svc.verify_payment(user["id"], "student_premium", order_id, payment_id, signature)
    assert result["success"] is True
    assert result["subscription"]["active"] is True
    assert result["subscription"]["tier"] == "student_premium"


def test_verify_payment_rejects_bad_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    svc = SubscriptionService(db=Database(data_dir=str(tmp_path)))
    result = svc.verify_payment("user-1", "student_premium", "order_123", "pay_123", "bad")
    assert result["success"] is False
    assert "signature" in result["error"].lower()


def test_webhook_activates_subscription(tmp_path, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "webhook-secret")
    db = Database(data_dir=str(tmp_path))
    auth = AuthService(db=db)
    svc = SubscriptionService(db=db)
    user = auth.register("webhook@example.com", "password123")["user"]
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_webhook",
                    "order_id": "order_webhook",
                    "notes": {"user_id": user["id"], "plan_id": "student_premium"},
                }
            }
        },
    }
    import json
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(b"webhook-secret", raw, hashlib.sha256).hexdigest()
    assert svc.verify_webhook_signature(raw, sig) is True

    result = svc.handle_webhook(payload)
    assert result["success"] is True
    assert svc.status(user["id"])["tier"] == "student_premium"


def test_subscription_grace_and_expiry_transitions(tmp_path):
    db = Database(data_dir=str(tmp_path))
    auth = AuthService(db=db)
    svc = SubscriptionService(db=db)
    user = auth.register("expiry@example.com", "password123")["user"]
    now = datetime.now(timezone.utc)

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions
                (user_id, plan_id, status, activated_at, expires_at, grace_until, updated_at)
            VALUES (?, 'student_premium', 'active', ?, ?, ?, ?)
            """,
            (
                user["id"],
                (now - timedelta(days=31)).isoformat(),
                (now - timedelta(days=1)).isoformat(),
                (now + timedelta(days=1)).isoformat(),
                now.isoformat(),
            ),
        )
    grace = svc.status(user["id"])
    assert grace["tier"] == "student_premium"
    assert grace["status"] == "grace"

    with db.connect() as conn:
        conn.execute(
            "UPDATE subscriptions SET grace_until = ? WHERE user_id = ?",
            ((now - timedelta(hours=1)).isoformat(), user["id"]),
        )
    expired = svc.status(user["id"])
    assert expired["tier"] == "free"
    assert expired["status"] == "expired"
