-- DataVault schema (SQLite)

DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS datasets;
DROP TABLE IF EXISTS requests;
DROP TABLE IF EXISTS blockchain;
DROP TABLE IF EXISTS history;
DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS chat_history;
DROP TABLE IF EXISTS user_datasets;
DROP TABLE IF EXISTS login_sessions;
DROP TABLE IF EXISTS password_resets;

CREATE TABLE users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    avatar         TEXT,
    bio            TEXT,
    last_login     TEXT,
    current_login  TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE datasets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    filename    TEXT NOT NULL,
    filepath    TEXT NOT NULL,
    uploaded_by INTEGER NOT NULL,
    is_locked   INTEGER NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL DEFAULT 'admin',  -- admin, sample, user_upload, kaggle, json
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);

CREATE TABLE requests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    dataset_id       INTEGER NOT NULL,
    requested_change TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Approved', 'Rejected')),
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(id)
);

CREATE TABLE blockchain (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    block_index   INTEGER NOT NULL,
    data          TEXT NOT NULL,
    hash          TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    timestamp     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER,
    actor_name TEXT NOT NULL,
    action     TEXT NOT NULL,
    details    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (admin_id) REFERENCES users(id)
);

CREATE TABLE notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    message    TEXT NOT NULL,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE chat_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    dataset_id  INTEGER,
    dataset_name TEXT,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Tracks each login -> logout window, for every role, so we can compute
-- "time spent on portal" per user.
CREATE TABLE login_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    login_at    TEXT NOT NULL DEFAULT (datetime('now')),
    logout_at   TEXT,
    duration_seconds INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- One-time tokens issued for the "Forgot password" flow.
CREATE TABLE password_resets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    token       TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
