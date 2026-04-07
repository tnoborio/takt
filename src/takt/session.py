"""セッション管理 + 利用量計測（SQLite per テナント）"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class SessionStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    title TEXT DEFAULT '',
                    sdk_session_id TEXT,
                    created_at TEXT NOT NULL,
                    last_active TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # Migrate: add columns if missing
            cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
            if "user_id" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
            if "title" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT DEFAULT ''")
            if "sdk_session_id" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN sdk_session_id TEXT")

    def create_session(self, user_id: str = "", title: str = "") -> str:
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, user_id, title, created_at, last_active) VALUES (?, ?, ?, ?, ?)",
                (session_id, user_id, title, now, now),
            )
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def update_session(self, session_id: str, *, title: str | None = None, sdk_session_id: str | None = None):
        updates, params = [], []
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if sdk_session_id is not None:
            updates.append("sdk_session_id = ?")
            params.append(sdk_session_id)
        updates.append("last_active = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(session_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?", params)

    def list_sessions(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT session_id, title, created_at, last_active FROM sessions WHERE user_id = ? ORDER BY last_active DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_message(self, session_id: str, role: str, content: str):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            )

    def get_messages(self, session_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_usage(self, session_id: str, model: str, input_tokens: int, output_tokens: int):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO usage (session_id, model, input_tokens, output_tokens, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, model, input_tokens, output_tokens, now),
            )
            conn.execute(
                "UPDATE sessions SET last_active = ? WHERE session_id = ?",
                (now, session_id),
            )
