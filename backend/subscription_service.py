import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import requests

from database import Database


PLANS = {
    "free": {
        "id": "free",
        "name": "Free Simulator",
        "price_inr": 0,
        "features": ["paper_trading", "charts", "watchlist", "basic_indicators"],
    },
    "student_premium": {
        "id": "student_premium",
        "name": "Student Premium",
        "price_inr": 299,
        "features": ["lessons", "advanced_backtesting", "ai_commentary", "progress_tracking"],
    },
}


class SubscriptionService:
    def __init__(self, data_dir: Optional[str] = None, db: Optional[Database] = None):
        self.db = db or Database(data_dir=data_dir)
        self.grace_days = int(os.getenv("SUBSCRIPTION_GRACE_DAYS", "3"))

    def plans(self) -> Dict:
        return PLANS

    def _free_status(self, reason: str = "free") -> Dict:
        return {"tier": "free", "active": False, "status": reason, "features": PLANS["free"]["features"]}

    def status(self, user_id: Optional[str]) -> Dict:
        if not user_id:
            return self._free_status()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM subscriptions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                return self._free_status()
            status = self._normalized_status(row)
            if status["status"] != row["status"]:
                conn.execute(
                    "UPDATE subscriptions SET status = ?, updated_at = ? WHERE user_id = ?",
                    (status["status"], datetime.now(timezone.utc).isoformat(), user_id),
                )
            return status

    def _normalized_status(self, row) -> Dict:
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromisoformat(row["expires_at"])
        grace_until = datetime.fromisoformat(row["grace_until"]) if row["grace_until"] else None
        plan = PLANS.get(row["plan_id"], PLANS["free"])
        if row["status"] in {"cancelled", "payment_failed"}:
            return self._free_status(row["status"])
        if expires_at > now:
            return {
                "tier": plan["id"],
                "active": True,
                "status": "active",
                "features": plan["features"],
                "expires_at": row["expires_at"],
                "grace_until": row["grace_until"],
            }
        if grace_until and grace_until > now:
            return {
                "tier": plan["id"],
                "active": True,
                "status": "grace",
                "features": plan["features"],
                "expires_at": row["expires_at"],
                "grace_until": row["grace_until"],
            }
        return self._free_status("expired")

    def create_razorpay_order(self, user_id: str, plan_id: str) -> Dict:
        plan = PLANS.get(plan_id)
        if not plan or plan["price_inr"] <= 0:
            return {"success": False, "error": "Invalid paid plan"}

        key_id = os.getenv("RAZORPAY_KEY_ID", "")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
        if not key_id or not key_secret:
            return {"success": False, "error": "Razorpay keys are not configured"}

        receipt = f"future_{user_id[:8]}_{uuid.uuid4().hex[:10]}"
        payload = {
            "amount": int(plan["price_inr"] * 100),
            "currency": "INR",
            "receipt": receipt,
            "notes": {"user_id": user_id, "plan_id": plan_id},
        }
        auth = base64.b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("ascii")
        resp = requests.post(
            "https://api.razorpay.com/v1/orders",
            json=payload,
            headers={"Authorization": f"Basic {auth}"},
            timeout=20,
        )
        if resp.status_code >= 400:
            return {"success": False, "error": "Razorpay order creation failed"}
        data = resp.json()
        self.record_payment_event(user_id, plan_id, data.get("id"), None, "order.created", data)
        return {"success": True, "order": data, "key_id": key_id, "plan": plan}

    def verify_payment(self, user_id: str, plan_id: str, order_id: str, payment_id: str, signature: str) -> Dict:
        secret = os.getenv("RAZORPAY_KEY_SECRET", "")
        if not secret:
            return {"success": False, "error": "Razorpay keys are not configured"}
        body = f"{order_id}|{payment_id}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature or ""):
            return {"success": False, "error": "Invalid payment signature"}
        return self.activate_subscription(user_id, plan_id, order_id, payment_id, "payment.verified")

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        secret = os.getenv("RAZORPAY_WEBHOOK_SECRET") or os.getenv("RAZORPAY_KEY_SECRET", "")
        if not secret:
            return False
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def handle_webhook(self, payload: Dict) -> Dict:
        event = payload.get("event", "")
        payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
        notes = payment.get("notes") or {}
        user_id = notes.get("user_id")
        plan_id = notes.get("plan_id", "student_premium")
        order_id = payment.get("order_id")
        payment_id = payment.get("id")
        if not user_id or not order_id:
            return {"success": False, "error": "Webhook missing user_id/order_id notes"}
        self.record_payment_event(user_id, plan_id, order_id, payment_id, event, payload)
        if event in {"payment.captured", "order.paid"}:
            return self.activate_subscription(user_id, plan_id, order_id, payment_id, event)
        if event in {"payment.failed", "subscription.charged.failed"}:
            self.mark_payment_failed(user_id)
        return {"success": True, "ignored": event not in {"payment.captured", "order.paid"}}

    def activate_subscription(self, user_id: str, plan_id: str, order_id: str, payment_id: str, event_type: str) -> Dict:
        plan = PLANS.get(plan_id)
        if not plan or plan["price_inr"] <= 0:
            return {"success": False, "error": "Invalid paid plan"}
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=30)
        grace_until = expires_at + timedelta(days=self.grace_days)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO subscriptions
                    (user_id, plan_id, status, payment_id, order_id, activated_at, expires_at, grace_until, updated_at)
                VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    plan_id=excluded.plan_id,
                    status='active',
                    payment_id=excluded.payment_id,
                    order_id=excluded.order_id,
                    activated_at=excluded.activated_at,
                    expires_at=excluded.expires_at,
                    grace_until=excluded.grace_until,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id, plan_id, payment_id, order_id, now.isoformat(),
                    expires_at.isoformat(), grace_until.isoformat(), now.isoformat(),
                ),
            )
        self.record_payment_event(user_id, plan_id, order_id, payment_id, event_type, {"activated": True})
        return {"success": True, "subscription": self.status(user_id)}

    def mark_payment_failed(self, user_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE subscriptions SET status = 'payment_failed', updated_at = ? WHERE user_id = ?",
                (datetime.now(timezone.utc).isoformat(), user_id),
            )

    def record_payment_event(self, user_id, plan_id, order_id, payment_id, event_type, payload) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO payment_events
                    (user_id, plan_id, order_id, payment_id, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, plan_id, order_id, payment_id, event_type,
                    json.dumps(payload, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
