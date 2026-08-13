# -*- coding: utf-8 -*-
"""SQLite: схема, миграции и мелкие обёртки над таблицами служебных данных."""
from __future__ import annotations

import json
import sqlite3

from .config import DB_FILE, HOME, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    url_hash     TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    tier         INTEGER NOT NULL DEFAULT 2,
    category     TEXT NOT NULL DEFAULT 'other',
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    fetched_at   TEXT NOT NULL,
    sig          TEXT NOT NULL DEFAULT '',
    social       REAL NOT NULL DEFAULT 0,
    state        TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);

CREATE TABLE IF NOT EXISTS sent (
    url_hash    TEXT PRIMARY KEY,
    sig         TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    digest_date TEXT NOT NULL,
    sent_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sent_at ON sent(sent_at);

CREATE TABLE IF NOT EXISTS health (
    source_id  TEXT PRIMARY KEY,
    ok_at      TEXT,
    err        TEXT,
    err_at     TEXT,
    fails      INTEGER NOT NULL DEFAULT 0,
    last_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id      INTEGER PRIMARY KEY,
    kind    TEXT NOT NULL,
    at      TEXT NOT NULL,
    status  TEXT NOT NULL,
    stats   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);

-- Кандидаты, которые модель отранжировала, но в выпуск они не влезли.
-- Это запас для команды /more: показать их стоит без нового запроса к LLM.
CREATE TABLE IF NOT EXISTS leftover (
    id        INTEGER PRIMARY KEY,
    chat_id   TEXT NOT NULL DEFAULT '',
    url_hash  TEXT NOT NULL,
    title     TEXT NOT NULL,
    url       TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    category  TEXT NOT NULL DEFAULT 'other',
    score     REAL NOT NULL DEFAULT 0,
    at        TEXT NOT NULL,
    shown     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_leftover_chat ON leftover(chat_id, shown, score);
"""


def ensure_column(conn, table: str, column: str, decl: str) -> bool:
    """Добавляет колонку, если её ещё нет. Возвращает True, если добавили.

    Апгрейд с прошлой версии не должен требовать «удалите базу и начните
    заново» — история отправленного это и защита от повторов тоже.
    """
    have = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
    if column in have:
        return False
    conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
    conn.commit()
    return True


def migrate(conn) -> None:
    """Догоняет схему до текущей версии на уже существующей базе."""
    return None


def save_leftover(conn, chat_id, rows) -> None:
    """Запоминает хвост ранжирования: то, что не влезло в сегодняшний выпуск."""
    conn.execute("DELETE FROM leftover WHERE chat_id=?", (str(chat_id),))
    conn.executemany(
        "INSERT INTO leftover(chat_id,url_hash,title,url,source_id,category,score,at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(str(chat_id), r["url_hash"], r["title"], r["url"], r["source_id"],
          r["category"], r["score"], now_iso()) for r in rows])
    conn.commit()


def take_leftover(conn, chat_id, limit):
    """Отдаёт следующие непоказанные новости из хвоста и помечает их показанными."""
    rows = list(conn.execute(
        "SELECT * FROM leftover WHERE chat_id=? AND shown=0 "
        "ORDER BY score DESC LIMIT ?", (str(chat_id), limit)))
    if rows:
        conn.executemany("UPDATE leftover SET shown=1 WHERE id=?",
                         [(r["id"],) for r in rows])
        conn.commit()
    return rows


def db() -> sqlite3.Connection:
    HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def meta_get(conn, key, default=""):
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def meta_set(conn, key, value):
    conn.execute("INSERT INTO meta(k, v) VALUES (?, ?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))
    conn.commit()


def log_run(conn, kind, status, stats):
    conn.execute("INSERT INTO runs(kind, at, status, stats) VALUES (?,?,?,?)",
                 (kind, now_iso(), status, json.dumps(stats, ensure_ascii=False)))
    conn.execute("DELETE FROM runs WHERE id NOT IN "
                 "(SELECT id FROM runs ORDER BY id DESC LIMIT 200)")
    conn.commit()
