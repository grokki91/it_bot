# -*- coding: utf-8 -*-
"""Срочные новости: пороги, тихие часы, лимит на сутки, отправка."""
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import breaking, storage, subscribers  # noqa: E402
from newsdigest.config import CFG, now_iso  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

CHAT = "55"


class Clock:
    """Подмена local_now(): проверяем тихие часы без ожидания ночи."""

    def __init__(self, hour, minute=0):
        self.hour, self.minute = hour, minute

    def __call__(self):
        return datetime(2026, 8, 13, self.hour, self.minute)


class BreakingCase(unittest.TestCase):
    def setUp(self):
        conn = storage.db()
        try:
            for table in ("items", "sent", "meta", "runs", "feedback", "subscribers"):
                conn.execute("DELETE FROM %s" % table)
            conn.commit()
            subscribers.add(conn, CHAT, role="member", title="тест")
            self.sub = subscribers.get(conn, CHAT)
        finally:
            conn.close()

        self.sent = []
        self.saved_cfg = {k: CFG[k] for k in CFG}
        self._real = (breaking.tg_send, breaking.rank_clusters, breaking.summarize,
                      breaking.local_now)
        breaking.tg_send = lambda chat, text, keyboard=None, silent=None: \
            self.sent.append((chat, text))
        breaking.rank_clusters = lambda groups, persona: (
            [{"id": i, "score": 9.2, "category": "labs"} for i in range(len(groups))],
            {"in": 5, "out": 5})
        breaking.summarize = lambda picked, persona, lang: (
            {0: {"headline": "Срочный заголовок", "what": "суть", "why": "важно"}},
            {"in": 5, "out": 5})
        breaking.local_now = Clock(12)

    def tearDown(self):
        (breaking.tg_send, breaking.rank_clusters, breaking.summarize,
         breaking.local_now) = self._real
        CFG.update(self.saved_cfg)

    def fill(self, sources, title="Крупная лаборатория выпустила новую модель",
             social=0.0, tiers=None):
        """Одно и то же событие от нескольких источников (ссылки — разные)."""
        conn = storage.db()
        try:
            for source in sources:
                row = item("https://%s.com/%d" % (source, abs(hash(title)) % 10000),
                           title, source, tier=(tiers or {}).get(source, 2),
                           social=social)
                conn.execute(
                    "INSERT OR REPLACE INTO items(url_hash,url,source_id,tier,category,"
                    "title,summary,published_at,fetched_at,sig,social) VALUES "
                    "(:url_hash,:url,:source_id,:tier,:category,:title,:summary,"
                    ":published_at,:fetched_at,:sig,:social)",
                    dict(row, fetched_at=now_iso()))
            conn.commit()
        finally:
            conn.close()


class TestQuietHours(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(breaking.parse_quiet("23:00-08:00"), (1380, 480))
        self.assertIsNone(breaking.parse_quiet(""))
        self.assertIsNone(breaking.parse_quiet("всю ночь"))

    def test_window_over_midnight(self):
        saved = CFG["breaking_quiet"]
        CFG["breaking_quiet"] = "23:00-08:00"
        try:
            self.assertTrue(breaking.in_quiet_hours(Clock(2)()))
            self.assertTrue(breaking.in_quiet_hours(Clock(23, 30)()))
            self.assertFalse(breaking.in_quiet_hours(Clock(12)()))
            self.assertFalse(breaking.in_quiet_hours(Clock(8)()))
        finally:
            CFG["breaking_quiet"] = saved

    def test_daytime_window(self):
        saved = CFG["breaking_quiet"]
        CFG["breaking_quiet"] = "10:00-14:00"
        try:
            self.assertTrue(breaking.in_quiet_hours(Clock(11)()))
            self.assertFalse(breaking.in_quiet_hours(Clock(15)()))
        finally:
            CFG["breaking_quiet"] = saved


class TestHotDetection(BreakingCase):
    def group(self, sources, tiers=None, social=0.0):
        return [item("https://%s.com/x" % s, "Одно событие", s,
                     tier=(tiers or {}).get(s, 2), social=social) for s in sources]

    def test_needs_several_sources_and_a_primary(self):
        self.assertFalse(breaking.is_hot(self.group(["a", "b"])))
        self.assertFalse(breaking.is_hot(self.group(["a", "b", "c"])))
        self.assertTrue(breaking.is_hot(
            self.group(["a", "b", "c"], tiers={"a": 1})))

    def test_hacker_news_alone_is_enough(self):
        self.assertTrue(breaking.is_hot(self.group(["hackernews"], social=0.95)))
        self.assertFalse(breaking.is_hot(self.group(["hackernews"], social=0.5)))


class TestCheck(BreakingCase):
    def test_sends_confirmed_event(self):
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 1)
        self.assertIn("⚡", self.sent[0][1])
        self.assertIn("Срочный заголовок", self.sent[0][1])
        self.assertIn("подтверждают", self.sent[0][1])

    def test_marks_as_sent_so_digest_will_not_repeat(self):
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        breaking.check(chat_id=CHAT)
        conn = storage.db()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM sent").fetchone()["c"], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM items WHERE state='new'"
                             ).fetchone()["c"], 0)
            self.assertEqual(breaking.check(chat_id=CHAT), 0)   # второй раз — молчок
        finally:
            conn.close()

    def test_single_source_is_not_breaking(self):
        self.fill(["theverge"])
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.sent, [])

    def test_low_model_score_blocks_send(self):
        breaking.rank_clusters = lambda groups, persona: (
            [{"id": 0, "score": 6.0, "category": "labs"}], {"in": 5, "out": 5})
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        conn = storage.db()
        status = conn.execute("SELECT status FROM runs ORDER BY id DESC").fetchone()
        conn.close()
        self.assertEqual(status["status"], "below-threshold")

    def test_model_failure_stays_silent(self):
        def broken(groups, persona):
            raise LLMError("нет связи")

        breaking.rank_clusters = broken
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.sent, [])

    def test_quiet_hours_block(self):
        breaking.local_now = Clock(3)
        CFG["breaking_quiet"] = "23:00-08:00"
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)

    def test_daily_limit(self):
        CFG["breaking_max_per_day"] = 1
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 1)
        self.fill(["openai", "arstechnica", "venturebeat"],
                  title="Совсем другое крупное событие в отрасли",
                  tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        conn = storage.db()
        try:
            self.assertIn("лимит", breaking.why_not(conn, chat_id=CHAT))
            # у другого подписчика счётчик свой
            self.assertEqual(breaking.why_not(conn, chat_id="другой"), "")
        finally:
            conn.close()

    def test_switch_off(self):
        CFG["breaking"] = False
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)

    def test_pause_blocks(self):
        conn = storage.db()
        try:
            subscribers.set_field(conn, CHAT, "paused", 1)
            paused = subscribers.get(conn, CHAT)
        finally:
            conn.close()
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(sub=paused), 0)

    def test_history_is_personal(self):
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 1)
        self.assertEqual(breaking.check(chat_id=CHAT), 0)      # ему уже слали
        self.assertEqual(breaking.check(chat_id="другой"), 1)  # а этому ещё нет

    def test_foreign_topic_sources_are_ignored(self):
        # источники крипто-темы не должны всплыть у читателя ai
        self.fill(["coindesk", "cointelegraph", "ethereum-blog"],
                  title="Крупное обновление сети", tiers={"ethereum-blog": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)

    def test_old_items_are_out_of_window(self):
        conn = storage.db()
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        for source in ("openai", "theverge", "techcrunch"):
            row = item("https://%s.com/x" % source, "Вчерашнее большое событие",
                       source, tier=1 if source == "openai" else 2)
            conn.execute(
                "INSERT INTO items(url_hash,url,source_id,tier,category,title,summary,"
                "published_at,fetched_at,sig,social) VALUES (:url_hash,:url,:source_id,"
                ":tier,:category,:title,:summary,:published_at,:fetched_at,:sig,:social)",
                dict(row, fetched_at=old))
        conn.commit()
        conn.close()
        self.assertEqual(breaking.check(chat_id=CHAT), 0)


if __name__ == "__main__":
    unittest.main()
