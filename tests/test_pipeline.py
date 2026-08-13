# -*- coding: utf-8 -*-
"""Сквозной прогон выпуска без сети: модель и Telegram подменены заглушками."""
import contextlib
import io
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import feedback, pipeline, storage, subscribers  # noqa: E402
from newsdigest.config import CFG, now_iso  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402
from newsdigest.profiles import PROFILES  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

CHAT = "77"


class PipelineCase(unittest.TestCase):
    def setUp(self):
        conn = storage.db()
        for table in ("items", "sent", "leftover", "feedback", "meta", "runs",
                      "subscribers"):
            conn.execute("DELETE FROM %s" % table)
        conn.commit()
        conn.close()
        self.saved_topic = CFG["topic"]
        CFG["topic"] = "ai"

        self.sent = []
        self.ranked_personas = []
        self._real = (pipeline.tg_send, pipeline.rank_clusters, pipeline.summarize)
        pipeline.tg_send = lambda chat, text, keyboard=None, silent=None: \
            self.sent.append((chat, text, keyboard))
        pipeline.rank_clusters = self.fake_rank
        pipeline.summarize = self.fake_summarize

    def tearDown(self):
        pipeline.tg_send, pipeline.rank_clusters, pipeline.summarize = self._real
        CFG["topic"] = self.saved_topic

    def fake_rank(self, clusters, persona):
        """Все кандидаты выше порога — так проверяется отбор, а не порог."""
        self.ranked_personas.append(persona)
        return ([{"id": i, "score": 9.0 - i * 0.1, "category": "labs"}
                 for i in range(len(clusters))], {"in": 10, "out": 5})

    def fake_summarize(self, picked, persona, language):
        return ({i: {"headline": "Карточка %d" % i, "what": "суть", "why": "важно"}
                 for i in range(len(picked))}, {"in": 10, "out": 5})

    #: заголовки нарочно про разное — иначе кластеризация справедливо склеит
    #: их в одно событие, и в выпуске окажется одна новость вместо шести
    TITLES = [
        "Postgres 18 ускорил вакуум",
        "Nvidia показала ускоритель Rubin",
        "Rust добавил асинхронные трейты",
        "Kubernetes отказался от dockershim",
        "Firefox переписал рендеринг шрифтов",
        "Обнаружена уязвимость в OpenSSH",
        "Vim празднует тридцатилетие",
        "SQLite научился читать parquet",
        "Ceph выпустил релиз Squid",
        "Chrome включил партиционирование кэша",
        "Debian заморозил ветку trixie",
        "Zig переехал на собственный бэкенд",
        "Blender ускорил трассировку лучей",
        "Redis сменил лицензию обратно",
        "Curl отказался от поддержки gopher",
    ]

    def source_ids(self, topic="ai"):
        """Материал попадает в выпуск, только если его источник есть в теме."""
        return [f[0] for f in PROFILES[topic]["feeds"]]

    def fill(self, count, topic="ai"):
        sources = self.source_ids(topic)
        conn = storage.db()
        try:
            for i in range(count):
                row = item("https://e.com/%s/%d" % (topic, i),
                           self.TITLES[i % len(self.TITLES)],
                           sources[i % len(sources)])
                conn.execute(
                    "INSERT OR REPLACE INTO items(url_hash,url,source_id,tier,category,"
                    "title,summary,published_at,fetched_at,sig,social) VALUES "
                    "(:url_hash,:url,:source_id,:tier,:category,:title,:summary,"
                    ":published_at,:fetched_at,:sig,:social)",
                    dict(row, fetched_at=now_iso()))
            conn.commit()
        finally:
            conn.close()


class TestBuildAndSend(PipelineCase):
    def test_sends_and_records_history(self):
        self.fill(6)
        stats = pipeline.build_and_send(chat_id=CHAT)
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(stats["selected"], min(6, CFG["max_items"]))
        chat, text, keyboard = self.sent[0]
        self.assertEqual(chat, CHAT)
        self.assertIn("Карточка 0", text)
        self.assertEqual(len(keyboard), stats["selected"])

        conn = storage.db()
        try:
            sent_rows = list(conn.execute("SELECT * FROM sent"))
            self.assertEqual(len(sent_rows), stats["selected"])
            self.assertTrue(all(r["source_id"] for r in sent_rows))
            unsent = conn.execute(
                "SELECT COUNT(*) c FROM items WHERE state='new'").fetchone()["c"]
            self.assertEqual(unsent, 6 - stats["selected"])
        finally:
            conn.close()

    def test_second_run_finds_nothing_new(self):
        self.fill(6)
        pipeline.build_and_send(chat_id=CHAT)
        before = len(self.sent)
        stats = pipeline.build_and_send(chat_id=CHAT)
        self.assertEqual(stats["sent"], 0)
        self.assertEqual(len(self.sent), before)

    def test_leftover_is_saved_for_more(self):
        extra = 4
        self.fill(CFG["max_items"] + extra)
        stats = pipeline.build_and_send(chat_id=CHAT)
        self.assertEqual(stats["selected"], CFG["max_items"])
        conn = storage.db()
        try:
            rows = storage.take_leftover(conn, CHAT, 10)
            self.assertEqual(len(rows), extra)
            self.assertTrue(all(r["title"] for r in rows))
            # хвост отсортирован по оценке модели, лучшее — первым
            self.assertEqual([r["score"] for r in rows],
                             sorted((r["score"] for r in rows), reverse=True))
        finally:
            conn.close()

    def test_empty_database_is_not_an_error(self):
        stats = pipeline.build_and_send(chat_id=CHAT)
        self.assertEqual(stats["sent"], 0)
        self.assertEqual(self.sent, [])

    def test_dry_run_sends_nothing(self):
        self.fill(5)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            stats = pipeline.build_and_send(dry_run=True, chat_id=CHAT)
        self.assertIn("dry-run", out.getvalue())
        self.assertEqual(self.sent, [])
        self.assertEqual(stats["sent"], 0)
        conn = storage.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM sent").fetchone()["c"], 0)
        conn.close()

    def test_ranking_failure_degrades_to_prescore(self):
        self.fill(5)

        def broken(clusters, persona):
            raise LLMError("модель недоступна")

        pipeline.rank_clusters = broken
        stats = pipeline.build_and_send(chat_id=CHAT)
        self.assertEqual(stats["sent"], 1)          # выпуск всё равно ушёл

    def test_summary_failure_falls_back_to_titles(self):
        self.fill(5)

        def broken(picked, persona, language):
            raise LLMError("модель недоступна")

        pipeline.summarize = broken
        pipeline.build_and_send(chat_id=CHAT)
        self.assertIn(self.TITLES[0], self.sent[0][1])

    def test_feedback_reaches_the_prompt(self):
        conn = storage.db()
        feedback.record(conn, CHAT, "x", feedback.UP,
                        {"title": "Прошлая любимая новость", "source_id": "s"})
        conn.close()
        self.fill(3)
        pipeline.build_and_send(chat_id=CHAT)
        self.assertIn("Прошлая любимая новость", self.ranked_personas[0])

    def test_buttons_can_be_switched_off(self):
        CFG["feedback_buttons"] = False
        try:
            self.fill(3)
            pipeline.build_and_send(chat_id=CHAT)
            self.assertIsNone(self.sent[0][2])
        finally:
            CFG["feedback_buttons"] = True


class TestPerSubscriber(PipelineCase):
    """У каждого подписчика своя тема, свои лимиты и своя история."""

    def subscriber(self, chat_id, **fields):
        conn = storage.db()
        try:
            subscribers.add(conn, chat_id, role="member")
            for field, value in fields.items():
                subscribers.set_field(conn, chat_id, field, value)
            return subscribers.get(conn, chat_id)
        finally:
            conn.close()

    def test_history_is_personal(self):
        self.fill(5)
        first = self.subscriber("a")
        second = self.subscriber("b")
        pipeline.build_and_send(sub=first)
        pipeline.build_and_send(sub=second)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual({chat for chat, _t, _k in self.sent}, {"a", "b"})
        # повтор тому же читателю — молчок, а второму всё ещё есть что слать
        self.assertEqual(pipeline.build_and_send(sub=first)["sent"], 0)

    def test_personal_topic_filters_sources(self):
        self.fill(5, topic="ai")
        self.fill(5, topic="crypto")
        sub = self.subscriber("c", topic="crypto")
        pipeline.build_and_send(sub=sub)
        conn = storage.db()
        try:
            sources = {r["source_id"] for r in
                       conn.execute("SELECT source_id FROM sent WHERE chat_id='c'")}
        finally:
            conn.close()
        self.assertTrue(sources)
        self.assertTrue(sources <= set(self.source_ids("crypto")),
                        "в крипто-выпуск просочились чужие источники: %s" % sources)

    def test_personal_limit_applies(self):
        self.fill(12)
        sub = self.subscriber("d", max_items=3)
        stats = pipeline.build_and_send(sub=sub)
        self.assertEqual(stats["selected"], 3)
        self.assertEqual(CFG["max_items"], 8)      # общая настройка не съехала

    def test_overlay_restores_even_after_failure(self):
        self.fill(3)
        sub = self.subscriber("e", max_items=2, language="английский")

        def boom(picked, persona, language):
            raise RuntimeError("что-то сломалось")

        pipeline.summarize = boom
        with self.assertRaises(RuntimeError):
            pipeline.build_and_send(sub=sub)
        self.assertEqual(CFG["max_items"], 8)
        self.assertEqual(CFG["language"], "русский")

    def test_last_digest_is_recorded(self):
        self.fill(4)
        sub = self.subscriber("f")
        pipeline.build_and_send(sub=sub)
        conn = storage.db()
        try:
            fresh = subscribers.get(conn, "f")
            self.assertTrue(fresh["last_digest"])
            self.assertEqual(subscribers.due(conn), [])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
