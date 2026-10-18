"""SQLite storage. One file, no server: cheap to host, easy to back up.

Every unit of work opens its own connection (`with db.tx() as conn:`), which keeps
FastAPI's threadpool workers isolated and commits or rolls back atomically.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    id              INTEGER PRIMARY KEY,
    phone           TEXT NOT NULL UNIQUE,
    name            TEXT,
    owner_name      TEXT,
    business_type   TEXT,
    country         TEXT,
    currency        TEXT NOT NULL DEFAULT 'USD',
    language        TEXT NOT NULL DEFAULT 'en',
    timezone        TEXT NOT NULL DEFAULT 'Africa/Nairobi',
    onboarding_step TEXT NOT NULL DEFAULT 'ask_name',
    pending_action  TEXT,
    daily_summary   INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY,
    business_id   INTEGER NOT NULL REFERENCES businesses(id),
    name          TEXT NOT NULL COLLATE NOCASE,
    unit          TEXT NOT NULL DEFAULT 'pcs',
    quantity      REAL NOT NULL DEFAULT 0,
    unit_cost     INTEGER NOT NULL DEFAULT 0,
    sell_price    INTEGER NOT NULL DEFAULT 0,
    reorder_level REAL NOT NULL DEFAULT 5,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (business_id, name)
);

CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    name        TEXT NOT NULL COLLATE NOCASE,
    phone       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (business_id, name)
);

-- kind: sale | expense | purchase | payment_in | owner_draw | capital_in
CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY,
    business_id  INTEGER NOT NULL REFERENCES businesses(id),
    kind         TEXT NOT NULL,
    amount       INTEGER NOT NULL,
    paid_amount  INTEGER NOT NULL DEFAULT 0,
    cost_amount  INTEGER NOT NULL DEFAULT 0,
    customer_id  INTEGER REFERENCES customers(id),
    category     TEXT,
    method       TEXT NOT NULL DEFAULT 'cash',
    note         TEXT,
    source       TEXT NOT NULL DEFAULT 'chat',
    external_ref TEXT,
    due_date     TEXT,
    occurred_at  TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    voided       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tx_business_time ON transactions (business_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_tx_customer ON transactions (customer_id);

CREATE TABLE IF NOT EXISTS transaction_lines (
    id             INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    item_id        INTEGER REFERENCES items(id),
    description    TEXT NOT NULL,
    quantity       REAL NOT NULL DEFAULT 1,
    unit_price     INTEGER NOT NULL DEFAULT 0,
    unit_cost      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id             INTEGER PRIMARY KEY,
    business_id    INTEGER NOT NULL REFERENCES businesses(id),
    item_id        INTEGER NOT NULL REFERENCES items(id),
    transaction_id INTEGER REFERENCES transactions(id),
    delta          REAL NOT NULL,
    reason         TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS invoices (
    id             INTEGER PRIMARY KEY,
    business_id    INTEGER NOT NULL REFERENCES businesses(id),
    number         TEXT NOT NULL,
    customer_id    INTEGER REFERENCES customers(id),
    transaction_id INTEGER REFERENCES transactions(id),
    total          INTEGER NOT NULL,
    due_date       TEXT,
    status         TEXT NOT NULL DEFAULT 'unpaid',
    file_path      TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (business_id, number)
);

-- Mobile money history (forwarded SMS or imported statements), used for the credit profile.
CREATE TABLE IF NOT EXISTS momo_transactions (
    id                 INTEGER PRIMARY KEY,
    business_id        INTEGER NOT NULL REFERENCES businesses(id),
    provider           TEXT NOT NULL,
    ref                TEXT NOT NULL,
    direction          TEXT NOT NULL,
    category           TEXT NOT NULL,
    amount             INTEGER NOT NULL,
    fee                INTEGER NOT NULL DEFAULT 0,
    balance            INTEGER,
    counterparty       TEXT,
    counterparty_phone TEXT,
    occurred_at        TEXT NOT NULL,
    raw                TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (business_id, provider, ref)
);
CREATE INDEX IF NOT EXISTS ix_momo_business_time ON momo_transactions (business_id, occurred_at);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    business_id INTEGER REFERENCES businesses(id),
    direction   TEXT NOT NULL,
    channel     TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_messages_business ON messages (business_id, id);

CREATE TABLE IF NOT EXISTS lesson_progress (
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    lesson_id   TEXT NOT NULL,
    sent_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    quiz_answer TEXT,
    correct     INTEGER,
    PRIMARY KEY (business_id, lesson_id)
);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount      INTEGER NOT NULL,
    message     TEXT NOT NULL,
    channel     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS consents (
    id          INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    kind        TEXT NOT NULL,
    granted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    revoked_at  TEXT
);

CREATE TABLE IF NOT EXISTS processed_inbound (
    message_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._init_lock = threading.Lock()
        self._initialised = False

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, detect_types=0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self._init_lock:
            if self._initialised:
                return
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = self.connect()
            try:
                if self.path != ":memory:":
                    conn.execute("PRAGMA journal_mode = WAL")
                conn.executescript(SCHEMA)
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                conn.commit()
            finally:
                conn.close()
            self._initialised = True

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.init()
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        from .config import get_settings

        _db = Database(get_settings().database_path)
        _db.init()
    return _db


def set_db(db: Database | None) -> None:
    global _db
    _db = db
