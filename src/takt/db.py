"""プラットフォームDB — ユーザー・テナント・セッション管理"""

import hashlib
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


# --- Password hashing ---

def hash_password(plain: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(plain.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt:{salt.hex()}:{h.hex()}"


def verify_password(plain: str, password_hash: str) -> bool:
    _, salt_hex, hash_hex = password_hash.split(":")
    salt = bytes.fromhex(salt_hex)
    h = hashlib.scrypt(plain.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return h.hex() == hash_hex


# --- Dataclasses ---

@dataclass
class User:
    id: str
    tenant_id: str
    email: str
    display_name: str
    role: str  # "platform_admin" | "tenant_admin" | "user"
    is_active: bool = True


@dataclass
class TenantRecord:
    id: str
    name: str
    is_active: bool = True
    created_at: str = ""


@dataclass
class AuthSession:
    token: str
    user_id: str
    expires_at: str


# --- PlatformDB ---

class PlatformDB:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tenants (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('platform_admin', 'tenant_admin', 'user')),
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
                CREATE INDEX IF NOT EXISTS idx_sessions_expires ON auth_sessions(expires_at);
            """)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Tenants ---

    def create_tenant(self, tenant_id: str, name: str) -> TenantRecord:
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tenants (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (tenant_id, name, now, now),
            )
        return TenantRecord(id=tenant_id, name=name, created_at=now)

    def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        if not row:
            return None
        return TenantRecord(id=row["id"], name=row["name"], is_active=bool(row["is_active"]), created_at=row["created_at"])

    def list_tenants(self) -> list[TenantRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM tenants ORDER BY created_at").fetchall()
        return [TenantRecord(id=r["id"], name=r["name"], is_active=bool(r["is_active"]), created_at=r["created_at"]) for r in rows]

    def update_tenant(self, tenant_id: str, *, name: str | None = None, is_active: bool | None = None):
        updates, params = [], []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(int(is_active))
        if not updates:
            return
        updates.append("updated_at = ?")
        params.append(self._now())
        params.append(tenant_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE tenants SET {', '.join(updates)} WHERE id = ?", params)

    # --- Users ---

    def create_user(self, tenant_id: str, email: str, password: str, display_name: str, role: str) -> User:
        user_id = str(uuid.uuid4())
        now = self._now()
        pw_hash = hash_password(password)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (id, tenant_id, email, password_hash, display_name, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, tenant_id, email, pw_hash, display_name, role, now, now),
            )
        return User(id=user_id, tenant_id=tenant_id, email=email, display_name=display_name, role=role)

    def get_user_by_email(self, email: str) -> tuple[User, str] | None:
        """Returns (User, password_hash) or None."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            return None
        user = User(id=row["id"], tenant_id=row["tenant_id"], email=row["email"], display_name=row["display_name"], role=row["role"], is_active=bool(row["is_active"]))
        return user, row["password_hash"]

    def get_user_by_id(self, user_id: str) -> User | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return User(id=row["id"], tenant_id=row["tenant_id"], email=row["email"], display_name=row["display_name"], role=row["role"], is_active=bool(row["is_active"]))

    def list_users(self, tenant_id: str | None = None) -> list[User]:
        with self._conn() as conn:
            if tenant_id:
                rows = conn.execute("SELECT * FROM users WHERE tenant_id = ? ORDER BY created_at", (tenant_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [User(id=r["id"], tenant_id=r["tenant_id"], email=r["email"], display_name=r["display_name"], role=r["role"], is_active=bool(r["is_active"])) for r in rows]

    def update_user(self, user_id: str, *, display_name: str | None = None, role: str | None = None, is_active: bool | None = None, password: str | None = None):
        updates, params = [], []
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name)
        if role is not None:
            updates.append("role = ?")
            params.append(role)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(int(is_active))
        if password is not None:
            updates.append("password_hash = ?")
            params.append(hash_password(password))
        if not updates:
            return
        updates.append("updated_at = ?")
        params.append(self._now())
        params.append(user_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)

    # --- Auth Sessions ---

    def create_auth_session(self, user_id: str, expires_days: int = 7) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=expires_days)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO auth_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, now.isoformat(), expires.isoformat()),
            )
        return token

    def get_auth_session(self, token: str) -> AuthSession | None:
        now = self._now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM auth_sessions WHERE token = ? AND expires_at > ?",
                (token, now),
            ).fetchone()
        if not row:
            return None
        return AuthSession(token=row["token"], user_id=row["user_id"], expires_at=row["expires_at"])

    def delete_auth_session(self, token: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))

    def cleanup_expired_sessions(self):
        with self._conn() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (self._now(),))
