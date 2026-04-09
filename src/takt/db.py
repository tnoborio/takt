"""プラットフォームDB — ユーザー・テナント・セッション管理（PostgreSQL）"""

import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import RealDictCursor


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
    def __init__(self, database_url: str):
        self.database_url = database_url

    def _conn(self):
        conn = psycopg2.connect(self.database_url, cursor_factory=RealDictCursor)
        conn.autocommit = True
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Tenants ---

    def create_tenant(self, tenant_id: str, name: str) -> TenantRecord:
        now = self._now()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tenants (id, name, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
                (tenant_id, name, True, now, now),
            )
        finally:
            conn.close()
        return TenantRecord(id=tenant_id, name=name, created_at=now)

    def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return TenantRecord(id=row["id"], name=row["name"], is_active=row["is_active"], created_at=row["created_at"])

    def list_tenants(self) -> list[TenantRecord]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM tenants ORDER BY created_at")
            rows = cur.fetchall()
        finally:
            conn.close()
        return [TenantRecord(id=r["id"], name=r["name"], is_active=r["is_active"], created_at=r["created_at"]) for r in rows]

    def update_tenant(self, tenant_id: str, *, name: str | None = None, is_active: bool | None = None):
        updates, params = [], []
        if name is not None:
            updates.append("name = %s")
            params.append(name)
        if is_active is not None:
            updates.append("is_active = %s")
            params.append(is_active)
        if not updates:
            return
        updates.append("updated_at = %s")
        params.append(self._now())
        params.append(tenant_id)
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(f"UPDATE tenants SET {', '.join(updates)} WHERE id = %s", params)
        finally:
            conn.close()

    # --- Users ---

    def create_user(self, tenant_id: str, email: str, password: str, display_name: str, role: str) -> User:
        user_id = str(uuid.uuid4())
        now = self._now()
        pw_hash = hash_password(password)
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (id, tenant_id, email, password_hash, display_name, role, is_active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (user_id, tenant_id, email, pw_hash, display_name, role, True, now, now),
            )
        finally:
            conn.close()
        return User(id=user_id, tenant_id=tenant_id, email=email, display_name=display_name, role=role)

    def get_user_by_email(self, email: str) -> tuple[User, str] | None:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        user = User(id=row["id"], tenant_id=row["tenant_id"], email=row["email"], display_name=row["display_name"], role=row["role"], is_active=row["is_active"])
        return user, row["password_hash"]

    def get_user_by_id(self, user_id: str) -> User | None:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return User(id=row["id"], tenant_id=row["tenant_id"], email=row["email"], display_name=row["display_name"], role=row["role"], is_active=row["is_active"])

    def list_users(self, tenant_id: str | None = None) -> list[User]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            if tenant_id:
                cur.execute("SELECT * FROM users WHERE tenant_id = %s ORDER BY created_at", (tenant_id,))
            else:
                cur.execute("SELECT * FROM users ORDER BY created_at")
            rows = cur.fetchall()
        finally:
            conn.close()
        return [User(id=r["id"], tenant_id=r["tenant_id"], email=r["email"], display_name=r["display_name"], role=r["role"], is_active=r["is_active"]) for r in rows]

    def update_user(self, user_id: str, *, display_name: str | None = None, role: str | None = None, is_active: bool | None = None, password: str | None = None):
        updates, params = [], []
        if display_name is not None:
            updates.append("display_name = %s")
            params.append(display_name)
        if role is not None:
            updates.append("role = %s")
            params.append(role)
        if is_active is not None:
            updates.append("is_active = %s")
            params.append(is_active)
        if password is not None:
            updates.append("password_hash = %s")
            params.append(hash_password(password))
        if not updates:
            return
        updates.append("updated_at = %s")
        params.append(self._now())
        params.append(user_id)
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", params)
        finally:
            conn.close()

    # --- Auth Sessions ---

    def create_auth_session(self, user_id: str, expires_days: int = 7) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=expires_days)
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO auth_sessions (token, user_id, created_at, expires_at) VALUES (%s, %s, %s, %s)",
                (token, user_id, now.isoformat(), expires.isoformat()),
            )
        finally:
            conn.close()
        return token

    def get_auth_session(self, token: str) -> AuthSession | None:
        now = self._now()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM auth_sessions WHERE token = %s AND expires_at > %s",
                (token, now),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return AuthSession(token=row["token"], user_id=row["user_id"], expires_at=row["expires_at"])

    def delete_auth_session(self, token: str):
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM auth_sessions WHERE token = %s", (token,))
        finally:
            conn.close()

    def cleanup_expired_sessions(self):
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM auth_sessions WHERE expires_at < %s", (self._now(),))
        finally:
            conn.close()

    # --- Chat Sessions ---

    def create_session(self, tenant_id: str, user_id: str = "", title: str = "") -> str:
        session_id = str(uuid.uuid4())
        now = self._now()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sessions (session_id, tenant_id, user_id, title, created_at, last_active) VALUES (%s, %s, %s, %s, %s, %s)",
                (session_id, tenant_id, user_id, title, now, now),
            )
        finally:
            conn.close()
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sessions WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def update_session(self, session_id: str, *, title: str | None = None, sdk_session_id: str | None = None):
        updates, params = [], []
        if title is not None:
            updates.append("title = %s")
            params.append(title)
        if sdk_session_id is not None:
            updates.append("sdk_session_id = %s")
            params.append(sdk_session_id)
        updates.append("last_active = %s")
        params.append(self._now())
        params.append(session_id)
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = %s", params)
        finally:
            conn.close()

    def list_sessions(self, user_id: str) -> list[dict]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT session_id, title, created_at, last_active FROM sessions WHERE user_id = %s ORDER BY last_active DESC",
                (user_id,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def add_message(self, session_id: str, role: str, content: str):
        now = self._now()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (%s, %s, %s, %s)",
                (session_id, role, content, now),
            )
        finally:
            conn.close()

    def get_messages(self, session_id: str) -> list[dict]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT role, content, created_at FROM messages WHERE session_id = %s ORDER BY id",
                (session_id,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def record_usage(self, session_id: str, model: str, input_tokens: int, output_tokens: int):
        now = self._now()
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO usage (session_id, model, input_tokens, output_tokens, created_at) VALUES (%s, %s, %s, %s, %s)",
                (session_id, model, input_tokens, output_tokens, now),
            )
            cur.execute(
                "UPDATE sessions SET last_active = %s WHERE session_id = %s",
                (now, session_id),
            )
        finally:
            conn.close()
