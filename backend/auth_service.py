import re
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from werkzeug.security import check_password_hash, generate_password_hash

from database import Database


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthService:
    def __init__(self, data_dir: Optional[str] = None, db: Optional[Database] = None):
        self.db = db or Database(data_dir=data_dir)

    def _public_user(self, row) -> Dict:
        return {
            "id": row["id"],
            "email": row["email"],
            "created_at": row["created_at"],
        }

    def get_user(self, user_id: str) -> Optional[Dict]:
        if not user_id:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id, email, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return self._public_user(row) if row else None

    def register(self, email: str, password: str) -> Dict:
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            return {"success": False, "error": "Valid email is required"}
        _pwd = password or ""
        if len(_pwd) < 8:
            return {"success": False, "error": "Password must be at least 8 characters"}
        if not re.search(r'[0-9!@#$%^&*()_+\-=\[\]{}|;:,.?]', _pwd):
            return {"success": False, "error": "Password must include at least one number or special character"}

        user = {
            "id": str(uuid.uuid4()),
            "email": email,
            "password_hash": generate_password_hash(password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with self.db.connect() as conn:
                conn.execute(
                    "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (user["id"], user["email"], user["password_hash"], user["created_at"]),
                )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                return {"success": False, "error": "Account already exists"}
            raise
        return {"success": True, "user": self._public_user(user)}

    def login(self, email: str, password: str) -> Dict:
        email = (email or "").strip().lower()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id, email, password_hash, created_at FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        if row and check_password_hash(row["password_hash"], password or ""):
            return {"success": True, "user": self._public_user(row)}
        return {"success": False, "error": "Invalid email or password"}
