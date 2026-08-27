# -*- coding: utf-8 -*-
"""Обновление базы версии 2.0 на месте: история не должна теряться.

У работающего бота в digest.db лежит история отправленного за два месяца —
это и есть защита от повторов. Обновление обязано её сохранить, а не
предложить «удалите базу и начните заново».
"""
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import config, newsfeed, storage, subscribers  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

#: схема таблицы sent, какой она была в версии 2.0
OLD_SENT = """
CREATE TABLE sent (
    url_hash    TEXT PRIMARY KEY,
    sig         TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    digest_date TEXT NOT NULL,
    sent_at     TEXT NOT NULL
);
"""


class TestUpgradeFrom20(unittest.TestCase):
    OWNER = "424242"

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="ndold-"))
        self.db_file = self.home / "digest.db"
        self._saved = (storage.DB_FILE, storage.HOME, config.TG_CHAT)
        storage.DB_FILE, storage.HOME = self.db_file, self.home
        config.TG_CHAT = self.OWNER

        old = sqlite3.connect(str(self.db_file))
        old.executescript(OLD_SENT)
        old.executescript("""
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            CREATE TABLE runs (id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                at TEXT NOT NULL, status TEXT NOT NULL, stats TEXT NOT NULL DEFAULT '{}');
        """)
        old.executemany(
            "INSERT INTO sent(url_hash,sig,title,url,digest_date,sent_at) "
            "VALUES (?,?,?,?,?,?)",
            [("h%d" % i, "сиг %d" % i, "Старая новость %d" % i,
              "https://e.com/%d" % i, "2026-08-01", "2026-08-01T09:00:00+00:00")
             for i in range(7)])
        # копии сообщений версии 3.2: без message_id
        old.executescript("""
            CREATE TABLE outbox (id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL
                DEFAULT '', kind TEXT NOT NULL DEFAULT 'bot', text TEXT NOT NULL,
                keyboard TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
        """)
        old.execute("INSERT INTO outbox(chat_id,text,keyboard,at) VALUES "
                    "(?,'старый выпуск','[]','2026-08-01T09:00:00+00:00')",
                    (self.OWNER,))
        old.execute("INSERT INTO meta(k,v) VALUES ('extra_chats','555, 666')")
        old.execute("INSERT INTO meta(k,v) VALUES ('paused','1')")
        old.execute("INSERT INTO meta(k,v) VALUES ('last_digest_date','2026-08-01')")
        old.commit()
        old.close()

    def tearDown(self):
        storage.DB_FILE, storage.HOME, config.TG_CHAT = self._saved

    def test_history_survives_and_belongs_to_owner(self):
        conn = storage.db()
        try:
            rows = list(conn.execute("SELECT * FROM sent ORDER BY url_hash"))
            self.assertEqual(len(rows), 7)
            self.assertTrue(all(r["chat_id"] == self.OWNER for r in rows))
            self.assertEqual(rows[0]["title"], "Старая новость 0")
            self.assertEqual(rows[0]["sig"], "сиг 0")
            # новые колонки на месте и заполнены значениями по умолчанию
            self.assertEqual(rows[0]["category"], "other")
        finally:
            conn.close()

    def test_history_gets_card_columns(self):
        """Лента на странице читает раздел, суть и оценку прямо из истории.

        У записей версии 2.0 их нет — колонки должны появиться пустыми, а не
        уронить обновление: раздел такой новости лента достанет по источнику.
        """
        conn = storage.db()
        try:
            row = conn.execute("SELECT * FROM sent WHERE url_hash='h0'").fetchone()
            self.assertEqual(row["section"], "")
            self.assertEqual(row["headline"], "")
            self.assertEqual(row["summary"], "")
            self.assertEqual(row["score"], 0)
        finally:
            conn.close()

    def test_history_gets_the_urgent_mark(self):
        """Метка «срочное» появляется нулём: какая из старых новостей приходила
        вне расписания, задним числом уже не сказать."""
        conn = storage.db()
        try:
            row = conn.execute(
                "SELECT * FROM sent WHERE url_hash='h0'").fetchone()
            self.assertEqual(row["breaking"], 0)
        finally:
            conn.close()

    def test_subscribers_get_the_favorites_column(self):
        """Личный топ разделов — новая колонка, и она приезжает пустой."""
        conn = storage.db()
        try:
            owner = subscribers.ensure_owner(conn)
            self.assertEqual(owner["favorites"], "")
            self.assertIn("favorites", subscribers.PERSONAL)
        finally:
            conn.close()

    def test_history_gets_into_the_search_index(self):
        """Индекс новый, история старая: её надо переиндексировать один раз.

        Триггеры наполняют индекс с этого дня, а накопленное за два месяца
        они не видели — без переиндексации поиск потерял бы всю историю.
        """
        conn = storage.db()
        try:
            self.assertTrue(storage.searchable(conn))
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) c FROM sent_fts").fetchone()["c"], 7)
            rows, _more = newsfeed.page(conn, self.OWNER, query="новость",
                                        limit=50)
            self.assertEqual(len(rows), 7)
            rows, _more = newsfeed.page(conn, self.OWNER, query="старая новость 3")
            self.assertEqual([r["url_hash"] for r in rows], ["h3"])
        finally:
            conn.close()

    def test_index_is_rebuilt_when_its_contents_change(self):
        """Поменялся состав индексируемого текста — индекс собирается заново."""
        storage.db().close()
        conn = storage.db()
        try:
            conn.execute("DELETE FROM sent_fts")
            conn.commit()
            storage.meta_set(conn, "search_index", "прошлая версия")
        finally:
            conn.close()
        conn = storage.db()
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) c FROM sent_fts").fetchone()["c"], 7)
            self.assertEqual(storage.meta_get(conn, "search_index"),
                             storage.SEARCH_VERSION)
        finally:
            conn.close()

    def test_index_is_not_rebuilt_on_every_open(self):
        """Переиндексация — дело разовое: она читает всю историю целиком."""
        storage.db().close()
        conn = storage.db()
        try:
            conn.execute("INSERT INTO sent_fts(rowid, text) "
                         "VALUES (777, 'метка того, что индекс не пересобрали')")
            conn.commit()
        finally:
            conn.close()
        conn = storage.db()
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) c FROM sent_fts").fetchone()["c"], 8)
        finally:
            conn.close()

    def test_history_stays_writable_without_fts5(self):
        """FTS5 пропал из сборки — триггеры снимаются, а история пишется.

        Триггер ссылается на таблицу, которой SQLite без FTS5 не понимает:
        останься он на месте, упала бы вся запись истории, а не поиск.
        """
        storage.db().close()
        saved = storage.SEARCH_SCHEMA
        storage.SEARCH_SCHEMA = ("CREATE VIRTUAL TABLE IF NOT EXISTS "
                                 "нет_такого USING fts_которого_нет(text);")
        try:
            conn = storage.db()
            try:
                self.assertFalse([r for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND sql LIKE '%sent_fts%'")])
                conn.execute("INSERT INTO sent(chat_id,url_hash,title,"
                             "digest_date,sent_at) VALUES (?,'новый','Свежая "
                             "новость','2026-08-02','2026-08-02T09:00:00+00:00')",
                             (self.OWNER,))
                conn.commit()
                rows, _more = newsfeed.page(conn, self.OWNER, query="свежая")
                self.assertEqual([r["url_hash"] for r in rows], ["новый"])
            finally:
                conn.close()
        finally:
            storage.SEARCH_SCHEMA = saved

    def test_index_returns_with_fts5(self):
        """FTS5 вернулся — индекс собирается заново вместе с триггерами."""
        self.test_history_stays_writable_without_fts5()
        conn = storage.db()
        try:
            self.assertTrue(storage.searchable(conn))
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) c FROM sent_fts").fetchone()["c"], 8)
            rows, _more = newsfeed.page(conn, self.OWNER, query="свежая")
            self.assertEqual([r["url_hash"] for r in rows], ["новый"])
        finally:
            conn.close()

    def test_migration_is_idempotent(self):
        storage.db().close()
        conn = storage.db()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM sent").fetchone()["c"], 7)
        finally:
            conn.close()

    def test_per_chat_key_allows_same_item_for_two_readers(self):
        conn = storage.db()
        try:
            conn.execute("INSERT INTO sent(chat_id,url_hash,title,digest_date,sent_at)"
                         " VALUES ('другой','h0','Старая новость 0','2026-08-02','x')")
            conn.commit()
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) c FROM sent WHERE url_hash='h0'").fetchone()["c"], 2)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO sent(chat_id,url_hash,title,digest_date,"
                             "sent_at) VALUES ('другой','h0','дубль','2026-08-02','x')")
        finally:
            conn.close()

    def test_outbox_gets_message_id(self):
        """Старые копии сообщений остаются, у новых появляется номер в Telegram."""
        conn = storage.db()
        try:
            row = conn.execute("SELECT * FROM outbox").fetchone()
            self.assertEqual(row["text"], "старый выпуск")
            self.assertEqual(row["message_id"], 0)      # старое развернуть нечем
            self.assertEqual(storage.outbox_keyboard(conn, self.OWNER, 0), [])

            new = storage.save_outbox(conn, self.OWNER, "выпуск",
                                      [[{"text": "1 👍",
                                         "callback_data": "fb:up:h1"}]])
            storage.link_outbox(conn, new, 77)
            keyboard = storage.outbox_keyboard(conn, self.OWNER, 77)
            self.assertEqual(keyboard[0][0]["callback_data"], "fb:up:h1")
        finally:
            conn.close()

    def test_legacy_settings_move_to_subscribers(self):
        conn = storage.db()
        try:
            subscribers.ensure_owner(conn)
            owner = subscribers.get(conn, self.OWNER)
            self.assertEqual(owner["role"], "owner")
            self.assertTrue(owner["paused"])          # общая пауза стала личной
            # день из одиночной версии закрывается целиком: «#N» — номер
            # последнего выпуска суток, иначе пришёл бы лишний выпуск
            self.assertEqual(owner["last_digest"],
                             "2026-08-01#%d" % subscribers.per_day())

            members = {s["chat_id"] for s in subscribers.all_rows(conn)}
            self.assertEqual(members, {self.OWNER, "555", "666"})
            self.assertEqual(storage.meta_get(conn, "extra_chats"), "")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
