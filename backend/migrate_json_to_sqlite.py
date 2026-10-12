import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import Database, default_data_dir


def migrate(data_dir=None):
    root = Path(data_dir) if data_dir else default_data_dir()
    db = Database(data_dir=str(root))
    users_path = root / "users.json"
    subs_path = root / "subscriptions.json"
    imported = {"users": 0, "subscriptions": 0}

    with db.connect() as conn:
        if users_path.exists():
            users = json.loads(users_path.read_text(encoding="utf-8")).get("users", [])
            for user in users:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO users (id, email, password_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        user["email"],
                        user["password_hash"],
                        user.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    ),
                )
                imported["users"] += 1

        if subs_path.exists():
            subs = json.loads(subs_path.read_text(encoding="utf-8")).get("subscriptions", {})
            for user_id, sub in subs.items():
                expires_at = sub.get("expires_at") or (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                grace_until = sub.get("grace_until") or (
                    datetime.fromisoformat(expires_at) + timedelta(days=3)
                ).isoformat()
                activated_at = sub.get("activated_at") or datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO subscriptions
                        (user_id, plan_id, status, payment_id, order_id, activated_at, expires_at, grace_until, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        sub.get("plan_id", "student_premium"),
                        sub.get("status", "active"),
                        sub.get("payment_id"),
                        sub.get("order_id"),
                        activated_at,
                        expires_at,
                        grace_until,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                imported["subscriptions"] += 1

    return imported


def main():
    parser = argparse.ArgumentParser(description="Import legacy Future App JSON auth/subscription data into SQLite.")
    parser.add_argument("--data-dir", default=None, help="Directory containing users.json/subscriptions.json")
    args = parser.parse_args()
    result = migrate(args.data_dir)
    print(result)


if __name__ == "__main__":
    main()
