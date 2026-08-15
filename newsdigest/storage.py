# -*- coding: utf-8 -*-
"""SQLite: схема, миграции и мелкие обёртки над таблицами служебных данных."""
from __future__ import annotations

import json
import sqlite3

from . import config
from .config import DB_FILE, HOME, log, now_iso

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

-- История отправленного персональна: у каждого подписчика свой дедуп.
CREATE TABLE IF NOT EXISTS sent (
    chat_id     TEXT NOT NULL DEFAULT '',
    url_hash    TEXT NOT NULL,
    sig         TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    source_id   TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'other',
    digest_date TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    PRIMARY KEY (chat_id, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_sent_at ON sent(sent_at);
CREATE INDEX IF NOT EXISTS idx_sent_chat ON sent(chat_id, sent_at);

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

-- Реакции 👍/👎 под карточками. Это единственный сигнал о вкусах читателя,
-- который у нас есть, — он правит прескоринг и подсказывает модели.
CREATE TABLE IF NOT EXISTS feedback (
    chat_id   TEXT NOT NULL,
    url_hash  TEXT NOT NULL,
    verdict   TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    category  TEXT NOT NULL DEFAULT 'other',
    title     TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    PRIMARY KEY (chat_id, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_feedback_at ON feedback(chat_id, at);

-- Подписчики. Пустая строка / 0 / -1 в настройке означает «как в CFG»,
-- поэтому личные настройки не расходятся с общими сами по себе.
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'private',
    role        TEXT NOT NULL DEFAULT 'member',
    topic       TEXT NOT NULL DEFAULT '',
    sections    TEXT NOT NULL DEFAULT '',
    per_section INTEGER NOT NULL DEFAULT 0,
    send_at     TEXT NOT NULL DEFAULT '',
    tz          TEXT NOT NULL DEFAULT '',
    language    TEXT NOT NULL DEFAULT '',
    max_items   INTEGER NOT NULL DEFAULT 0,
    min_score   REAL NOT NULL DEFAULT 0,
    silent      INTEGER NOT NULL DEFAULT -1,
    paused      INTEGER NOT NULL DEFAULT 0,
    last_digest TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved (
    chat_id   TEXT NOT NULL,
    url_hash  TEXT NOT NULL,
    title     TEXT NOT NULL DEFAULT '',
    url       TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    PRIMARY KEY (chat_id, url_hash)
);

-- Копии сообщений бота: их показывает веб-страница. Всё, что уходит в
-- Telegram, попадает и сюда, поэтому в браузере видно ровно то же самое.
-- message_id — номер того же сообщения в Telegram. По нему бот достаёт полную
-- раскладку кнопок, когда читатель разворачивает свёрнутые реакции: в самой
-- кнопке места нет (64 байта на всё), а в копии сообщения раскладка уже есть.
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'bot',
    text       TEXT NOT NULL,
    keyboard   TEXT NOT NULL DEFAULT '',
    message_id INTEGER NOT NULL DEFAULT 0,
    at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_chat ON outbox(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_outbox_message ON outbox(chat_id, message_id);
"""

#: сколько последних сообщений храним для страницы (на каждый чат)
OUTBOX_KEEP = 400


def table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone())


def columns(conn, table: str) -> set:
    return {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def ensure_column(conn, table: str, column: str, decl: str) -> bool:
    """Добавляет колонку, если её ещё нет. Возвращает True, если добавили.

    Апгрейд с прошлой версии не должен требовать «удалите базу и начните
    заново» — история отправленного это и защита от повторов тоже.
    """
    if column in columns(conn, table):
        return False
    conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
    conn.commit()
    return True


def upgrade(conn) -> None:
    """Подтягивает старую базу до текущей схемы.

    Вызывается ДО создания схемы: иначе индексы по новым колонкам не лягут
    на таблицу, оставшуюся от прошлой версии.
    """
    split_sent_by_chat(conn)
    add_sections(conn)
    add_outbox_message_id(conn)


def add_outbox_message_id(conn) -> None:
    """Связь копии сообщения с номером в Telegram (3.3, свёрнутые реакции)."""
    if table_exists(conn, "outbox"):
        ensure_column(conn, "outbox", "message_id", "INTEGER NOT NULL DEFAULT 0")


def add_sections(conn) -> None:
    """Разделы подписчика (3.2). У кого их нет — читает подборку по умолчанию.

    Кто раньше выбрал себе личную тему, тот выбирал именно её и не должен
    внезапно получить выпуск на двенадцать разделов: при переезде личная тема
    становится его личным списком разделов. Дальше он волен добавить ещё —
    /sections add. Общая тема (CFG['topic']) сюда не переносится: у владельца
    она стояла у всех по умолчанию и означала просто «о чём бот».
    """
    if not table_exists(conn, "subscribers"):
        return                      # новая база: колонки придут из SCHEMA
    ensure_column(conn, "subscribers", "per_section", "INTEGER NOT NULL DEFAULT 0")
    if not ensure_column(conn, "subscribers", "sections", "TEXT NOT NULL DEFAULT ''"):
        return
    cur = conn.execute("UPDATE subscribers SET sections=topic "
                       "WHERE sections='' AND topic!=''")
    conn.commit()
    if cur.rowcount:
        log.info("Личная тема %d подписчика(ов) стала их списком разделов",
                 cur.rowcount)


def split_sent_by_chat(conn) -> None:
    """История отправленного становится персональной.

    До 3.0 таблица `sent` была общей: один читатель — одна история. С
    подписчиками так нельзя, у каждого свой дедуп. Ключ меняется на
    (chat_id, url_hash), а старые записи достаются владельцу — он их и
    получал. Терять историю нельзя: это защита от повторов.
    """
    if not table_exists(conn, "sent") or "chat_id" in columns(conn, "sent"):
        return
    have = columns(conn, "sent")
    owner = str(getattr(config, "TG_CHAT", "") or "")
    conn.executescript("""
        CREATE TABLE sent_new (
            chat_id     TEXT NOT NULL DEFAULT '',
            url_hash    TEXT NOT NULL,
            sig         TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL,
            url         TEXT NOT NULL DEFAULT '',
            source_id   TEXT NOT NULL DEFAULT '',
            category    TEXT NOT NULL DEFAULT 'other',
            digest_date TEXT NOT NULL,
            sent_at     TEXT NOT NULL,
            PRIMARY KEY (chat_id, url_hash)
        );
    """)
    # source_id и category появились в 3.0: в базе 2.0 их нет, подставляем пусто
    picked = ["?"] + [name if name in have else default for name, default in (
        ("url_hash", "''"), ("sig", "''"), ("title", "''"), ("url", "''"),
        ("source_id", "''"), ("category", "'other'"),
        ("digest_date", "''"), ("sent_at", "''"))]
    conn.execute(
        "INSERT OR IGNORE INTO sent_new(chat_id,url_hash,sig,title,url,source_id,"
        "category,digest_date,sent_at) SELECT %s FROM sent" % ",".join(picked),
        (owner,))
    moved = conn.execute("SELECT COUNT(*) c FROM sent_new").fetchone()["c"]
    conn.executescript("DROP TABLE sent; ALTER TABLE sent_new RENAME TO sent;")
    conn.commit()
    if moved:
        log.info("История отправленного (%d записей) закреплена за chat_id %s",
                 moved, owner or "—")


def item_facts(conn, url_hash):
    """Заголовок, ссылка, источник и категория по хэшу — где бы они ни лежали."""
    for table in ("items", "sent"):
        row = conn.execute(
            "SELECT title, url, source_id, category FROM %s WHERE url_hash=?" % table,
            (url_hash,)).fetchone()
        if row:
            return dict(row)
    return {"title": "", "url": "", "source_id": "", "category": "other"}


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


def save_outbox(conn, chat_id, text, keyboard=None, kind="bot") -> int:
    """Кладёт копию сообщения для веб-страницы. Возвращает её номер.

    Хвост подрезаем изредка, а не каждый раз: лишний DELETE на каждое
    сообщение ничего не даёт, а страница живёт последними сотнями строк.
    """
    cur = conn.execute(
        "INSERT INTO outbox(chat_id, kind, text, keyboard, at) VALUES (?,?,?,?,?)",
        (str(chat_id), kind, text,
         json.dumps(keyboard, ensure_ascii=False) if keyboard else "", now_iso()))
    conn.commit()
    new_id = int(cur.lastrowid)
    if new_id % 25 == 0:
        conn.execute("DELETE FROM outbox WHERE chat_id=? AND id<=?",
                     (str(chat_id), new_id - OUTBOX_KEEP))
        conn.commit()
    return new_id


def link_outbox(conn, row_id, message_id) -> None:
    """Запоминает, каким номером сообщение ушло в Telegram."""
    conn.execute("UPDATE outbox SET message_id=? WHERE id=?",
                 (int(message_id), int(row_id)))
    conn.commit()


def outbox_keyboard(conn, chat_id, message_id) -> list:
    """Полная раскладка кнопок отправленного сообщения (пусто — не нашли)."""
    if not message_id:
        return []
    row = conn.execute(
        "SELECT keyboard FROM outbox WHERE chat_id=? AND message_id=? "
        "ORDER BY id DESC LIMIT 1", (str(chat_id), int(message_id))).fetchone()
    try:
        keyboard = json.loads(row["keyboard"]) if row and row["keyboard"] else []
    except ValueError:
        return []
    return keyboard if isinstance(keyboard, list) else []


def outbox_page(conn, chat_id, after=None, limit=60):
    """Сообщения чата: хвост ленты (after=None) или всё новее номера after."""
    if after is None:
        rows = list(conn.execute(
            "SELECT * FROM outbox WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit)))
        return rows[::-1]
    return list(conn.execute(
        "SELECT * FROM outbox WHERE chat_id=? AND id>? ORDER BY id LIMIT ?",
        (str(chat_id), int(after), limit)))


def db() -> sqlite3.Connection:
    HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    upgrade(conn)                 # сначала чиним старое, потом досоздаём новое
    conn.executescript(SCHEMA)
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
