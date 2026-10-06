import sqlite3
import hashlib
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "datavault.db")


def get_db():
    # timeout=15: if the db is briefly locked by another connection, wait
    # up to 15s and retry instead of raising "database is locked" instantly.
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets readers run concurrently with a writer (huge reduction in locking).
    conn.execute("PRAGMA journal_mode = WAL")
    # Belt-and-braces: also set SQLite's internal busy handler timeout (ms).
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    with open(os.path.join(BASE_DIR, "schema.sql"), "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def migrate_db():
    if not os.path.exists(DB_PATH):
        return
    conn = get_db()

    existing_user_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    new_user_cols = {
        "avatar": "TEXT",
        "bio": "TEXT",
        "last_login": "TEXT",
        "current_login": "TEXT",
    }
    for col, col_type in new_user_cols.items():
        if col not in existing_user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")

    existing_dataset_cols = {row["name"] for row in conn.execute("PRAGMA table_info(datasets)")}
    if "is_locked" not in existing_dataset_cols:
        conn.execute("ALTER TABLE datasets ADD COLUMN is_locked INTEGER NOT NULL DEFAULT 0")
    if "source_type" not in existing_dataset_cols:
        conn.execute("ALTER TABLE datasets ADD COLUMN source_type TEXT NOT NULL DEFAULT 'admin'")

    # Create chat_history table if missing
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "chat_history" not in tables:
        conn.execute("""
            CREATE TABLE chat_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                dataset_id  INTEGER,
                dataset_name TEXT,
                question    TEXT NOT NULL,
                answer      TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

    if "login_sessions" not in tables:
        conn.execute("""
            CREATE TABLE login_sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                login_at    TEXT NOT NULL DEFAULT (datetime('now')),
                logout_at   TEXT,
                duration_seconds INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

    if "password_resets" not in tables:
        conn.execute("""
            CREATE TABLE password_resets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                token       TEXT NOT NULL UNIQUE,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at  TEXT NOT NULL,
                used        INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

    migrate_chat_sessions(conn)
    conn.commit()
    conn.close()


def calc_hash(index, data, previous_hash, timestamp):
    payload = f"{index}{data}{previous_hash}{timestamp}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def add_block(data: str):
    conn = get_db()
    last = conn.execute("SELECT * FROM blockchain ORDER BY id DESC LIMIT 1").fetchone()
    if last is None:
        index = 0
        previous_hash = "0" * 64
    else:
        index = last["block_index"] + 1
        previous_hash = last["hash"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_hash = calc_hash(index, data, previous_hash, timestamp)
    conn.execute(
        "INSERT INTO blockchain (block_index, data, hash, previous_hash, timestamp) VALUES (?, ?, ?, ?, ?)",
        (index, data, new_hash, previous_hash, timestamp),
    )
    conn.commit()
    conn.close()


def log_history(admin_id, actor_name, action, details=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO history (admin_id, actor_name, action, details) VALUES (?, ?, ?, ?)",
        (admin_id, actor_name, action, details),
    )
    conn.commit()
    conn.close()


def add_notification(user_id, message):
    conn = get_db()
    conn.execute(
        "INSERT INTO notifications (user_id, message) VALUES (?, ?)",
        (user_id, message),
    )
    conn.commit()
    conn.close()


def save_chat(user_id, dataset_id, dataset_name, question, answer):
    conn = get_db()
    conn.execute(
        "INSERT INTO chat_history (user_id, dataset_id, dataset_name, question, answer) VALUES (?, ?, ?, ?, ?)",
        (user_id, dataset_id, dataset_name, question, answer),
    )
    conn.commit()
    conn.close()


def get_chat_history(user_id, limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return rows


def delete_chat(chat_id, user_id):
    conn = get_db()
    conn.execute("DELETE FROM chat_history WHERE id = ? AND user_id = ?", (chat_id, user_id))
    conn.commit()
    conn.close()


def delete_all_chat(user_id):
    conn = get_db()
    conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def migrate_chat_sessions(conn):
    """Add chat_sessions + chat_messages tables and ds_type/ds_id columns."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "chat_sessions" not in tables:
        conn.execute("""
            CREATE TABLE chat_sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                title      TEXT,
                ds_type    TEXT,
                ds_id      TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
    else:
        # add columns if missing
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(chat_sessions)")}
        if "ds_type" not in cols:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN ds_type TEXT")
        if "ds_id" not in cols:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN ds_id TEXT")

    if "chat_messages" not in tables:
        conn.execute("""
            CREATE TABLE chat_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role       TEXT NOT NULL CHECK (role IN ('user','assistant')),
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
            )
        """)


# ──────────────────────────────────────────────────────────────────────
# Login-session tracking (for "time spent on portal" analytics)
# ──────────────────────────────────────────────────────────────────────
def start_login_session(user_id):
    """Open a new login_sessions row, return its id."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO login_sessions (user_id, login_at) VALUES (?, datetime('now'))",
        (user_id,),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def end_login_session(session_row_id):
    """Close a login_sessions row and compute its duration in seconds."""
    if not session_row_id:
        return
    conn = get_db()
    conn.execute(
        """UPDATE login_sessions
           SET logout_at = datetime('now'),
               duration_seconds = CAST((julianday(datetime('now')) - julianday(login_at)) * 86400 AS INTEGER)
           WHERE id = ? AND logout_at IS NULL""",
        (session_row_id,),
    )
    conn.commit()
    conn.close()


def close_stale_session(user_id):
    """Safety net: close any session left dangling (e.g. browser closed without logout)."""
    conn = get_db()
    conn.execute(
        """UPDATE login_sessions
           SET logout_at = datetime('now'),
               duration_seconds = CAST((julianday(datetime('now')) - julianday(login_at)) * 86400 AS INTEGER)
           WHERE user_id = ? AND logout_at IS NULL""",
        (user_id,),
    )
    conn.commit()
    conn.close()


def get_user_sessions(user_id, limit=200):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM login_sessions WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return rows


def total_time_spent(user_id):
    """Total seconds spent on the portal across all closed sessions, plus any open one (live)."""
    conn = get_db()
    closed = conn.execute(
        "SELECT COALESCE(SUM(duration_seconds),0) s FROM login_sessions WHERE user_id=? AND duration_seconds IS NOT NULL",
        (user_id,),
    ).fetchone()["s"]
    open_row = conn.execute(
        "SELECT login_at FROM login_sessions WHERE user_id=? AND logout_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    live = 0
    if open_row:
        live_row_conn = get_db()
        live = live_row_conn.execute(
            "SELECT CAST((julianday(datetime('now')) - julianday(?)) * 86400 AS INTEGER) s",
            (open_row["login_at"],),
        ).fetchone()["s"]
        live_row_conn.close()
    return closed + live


# ──────────────────────────────────────────────────────────────────────
# Forgot-password tokens
# ──────────────────────────────────────────────────────────────────────
def create_password_reset(user_id, token, minutes_valid=30):
    conn = get_db()
    conn.execute(
        "INSERT INTO password_resets (user_id, token, expires_at) VALUES (?, ?, datetime('now', ?))",
        (user_id, token, f"+{minutes_valid} minutes"),
    )
    conn.commit()
    conn.close()


def get_valid_reset(token):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM password_resets WHERE token=? AND used=0 AND expires_at > datetime('now')",
        (token,),
    ).fetchone()
    conn.close()
    return row


def use_password_reset(token):
    conn = get_db()
    conn.execute("UPDATE password_resets SET used=1 WHERE token=?", (token,))
    conn.commit()
    conn.close()
