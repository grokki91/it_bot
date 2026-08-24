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

from newsdigest import breaking, storage, subscribers, translate  # noqa: E402
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
            for table in ("items", "sent", "meta", "runs", "feedback",
                          "subscribers", "alerts"):
                conn.execute("DELETE FROM %s" % table)
            conn.commit()
            subscribers.add(conn, CHAT, role="member", title="тест")
            self.sub = subscribers.get(conn, CHAT)
        finally:
            conn.close()

        self.sent = []
        self.saved_cfg = {k: CFG[k] for k in CFG}
        CFG["use_kev"] = False        # тесты в сеть не ходят
        self._real = (breaking.tg_send, breaking.rate_urgency, breaking.summarize,
                      breaking.local_now)
        breaking.tg_send = lambda chat, text, keyboard=None, silent=None: \
            self.sent.append((chat, text))
        breaking.rate_urgency = lambda groups, persona: (
            [{"id": i, "urgency": 9.2, "scope": "global", "category": "labs"}
             for i in range(len(groups))], {"in": 5, "out": 5})
        breaking.summarize = lambda picked, persona, lang: (
            {0: {"headline": "Срочный заголовок", "what": "суть", "why": "важно"}},
            {"in": 5, "out": 5})
        breaking.local_now = Clock(12)

    def tearDown(self):
        (breaking.tg_send, breaking.rate_urgency, breaking.summarize,
         breaking.local_now) = self._real
        CFG.update(self.saved_cfg)

    def urgency(self, value, scope="global"):
        """Подменить оценку срочности: уровень выбирается именно по ней."""
        breaking.rate_urgency = lambda groups, persona: (
            [{"id": i, "urgency": value, "scope": scope, "category": "labs"}
             for i in range(len(groups))], {"in": 5, "out": 5})

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

    def test_history_remembers_that_it_was_urgent(self):
        """Метка нужна странице: по ней лента и уведомления рисуют срочное."""
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        breaking.check(chat_id=CHAT)
        conn = storage.db()
        try:
            row = conn.execute("SELECT breaking FROM sent").fetchone()
            self.assertEqual(row["breaking"], 1)
        finally:
            conn.close()

    def test_single_source_is_not_breaking(self):
        self.fill(["theverge"])
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.sent, [])

    def test_low_model_score_blocks_send(self):
        self.urgency(6.0)
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        conn = storage.db()
        status = conn.execute("SELECT status FROM runs ORDER BY id DESC").fetchone()
        conn.close()
        self.assertEqual(status["status"], "below-threshold")

    def test_model_failure_stays_silent(self):
        def broken(groups, persona):
            raise LLMError("нет связи")

        breaking.rate_urgency = broken
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.sent, [])

    def test_quiet_hours_block_a_local_flash(self):
        """Ночью отраслевая молния ждёт: она уходит в очередь важного."""
        breaking.local_now = Clock(3)
        CFG["breaking_quiet"] = "23:00-08:00"
        self.urgency(9.5, scope="industry")
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.sent, [])

    def test_daily_limit(self):
        """Исчерпанный лимит молний не теряет событие: оно уходит в очередь."""
        CFG["breaking_max_per_day"] = 1
        CFG["alert_max_per_day"] = 1
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 1)

        self.fill(["openai", "arstechnica", "venturebeat"],
                  title="Совсем другое крупное событие в отрасли",
                  tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 0)   # молнией — уже нельзя
        conn = storage.db()
        try:
            self.assertEqual(len(breaking.pending_alerts(conn, CHAT)), 1)
            # оба лимита исчерпаны — проверку больше не запускаем
            self.assertIn("лимит", breaking.why_not(conn, chat_id=CHAT))
            # у другого подписчика счётчики свои
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


class TestLanguage(BreakingCase):
    """Срочное будит человека ночью — тем более оно должно быть понятным."""

    ENGLISH = "Major Lab Releases A New Frontier Model"

    def setUp(self):
        super().setUp()
        self.asked = []
        self._real_tr = translate.translate_texts
        translate.translate_texts = self.fake_translate
        conn = storage.db()
        conn.execute("DELETE FROM translations")
        conn.commit()
        conn.close()

    def tearDown(self):
        translate.translate_texts = self._real_tr
        super().tearDown()

    def fake_translate(self, texts, language):
        self.asked.extend(texts)
        return ({i: "Русский перевод строки %d" % i for i in range(len(texts))},
                {"in": 5, "out": 5})

    def test_fallback_card_is_translated(self):
        """Карточка не написалась — заголовок из фида идёт через перевод."""
        def broken(picked, persona, language):
            raise LLMError("модель недоступна")

        breaking.summarize = broken
        self.fill(["openai", "theverge", "techcrunch"], title=self.ENGLISH,
                  tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 1)
        self.assertIn("Русский перевод", self.sent[0][1])
        self.assertNotIn(self.ENGLISH, self.sent[0][1])

    def test_russian_card_is_not_touched(self):
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id=CHAT), 1)
        self.assertEqual(self.asked, [])
        self.assertIn("Срочный заголовок", self.sent[0][1])


class TestGrouped(BreakingCase):
    """Группировка подписчиков: один запрос к модели на одинаковые разделы."""

    def setUp(self):
        super().setUp()
        self.ranked = []          # по запросу на каждый заход модели
        self.written = []         # то же для карточек

        def rank(groups, persona):
            self.ranked.append(len(groups))
            return ([{"id": i, "urgency": 9.2, "scope": "global",
                      "category": "labs"} for i in range(len(groups))],
                    {"in": 5, "out": 5})

        def write(picked, persona, language):
            self.written.append(language)
            return ({0: {"headline": "Срочный заголовок", "what": "суть",
                         "why": "важно"}}, {"in": 5, "out": 5})

        breaking.rate_urgency = rank
        breaking.summarize = write

    def reader(self, chat_id, topics="", **fields):
        conn = storage.db()
        try:
            subscribers.add(conn, chat_id, role="member", title="тест")
            if topics:
                subscribers.set_field(conn, chat_id, "sections", topics)
            for field, value in fields.items():
                subscribers.set_field(conn, chat_id, field, value)
            return subscribers.get(conn, chat_id)
        finally:
            conn.close()

    def chats(self):
        return sorted(chat for chat, _text in self.sent)

    def test_same_sections_share_one_ranking(self):
        readers = [self.reader(c, "ai") for c in ("101", "102", "103")]
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check_all(readers), 3)
        self.assertEqual(self.chats(), ["101", "102", "103"])
        self.assertEqual(self.ranked, [1])     # один заход на всю группу
        self.assertEqual(self.written, ["русский"])   # и одна карточка на всех

    def test_different_sections_are_ranked_apart(self):
        readers = [self.reader("101", "ai"), self.reader("102", "crypto")]
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.fill(["coindesk", "cointelegraph", "ethereum-blog"],
                  title="Крупное обновление сети", tiers={"ethereum-blog": 1})
        self.assertEqual(breaking.check_all(readers), 2)
        self.assertEqual(self.chats(), ["101", "102"])
        self.assertEqual(self.ranked, [1, 1])  # разделы разные — запросы тоже

    def test_history_stays_personal_inside_group(self):
        readers = [self.reader("101", "ai"), self.reader("102", "ai")]
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check(chat_id="101"), 1)

        self.sent, self.ranked, self.written = [], [], []
        self.assertEqual(breaking.check_all(readers), 1)
        self.assertEqual(self.chats(), ["102"])
        self.assertEqual(self.ranked, [1])     # 101 своё уже видел — остался один

    def test_language_of_the_card_stays_personal(self):
        readers = [self.reader("101", "ai"),
                   self.reader("102", "ai", language="english")]
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check_all(readers), 2)
        self.assertEqual(self.ranked, [1])
        self.assertEqual(sorted(self.written), ["english", "русский"])

    def test_blocked_readers_do_not_reach_the_model(self):
        readers = [self.reader("101", "ai", paused=1),
                   self.reader("102", "ai", paused=1)]
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check_all(readers), 0)
        self.assertEqual(self.ranked, [])      # платить не за кого

    def test_model_failure_leaves_everyone_silent(self):
        def broken(groups, persona):
            raise LLMError("нет связи")

        breaking.rate_urgency = broken
        readers = [self.reader("101", "ai"), self.reader("102", "ai")]
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})
        self.assertEqual(breaking.check_all(readers), 0)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()


class TestLevels(BreakingCase):
    """Два уровня срочности: ⚡ молния сразу, 🔔 важное сводкой."""

    def confirmed(self, title="Крупная лаборатория выпустила новую модель"):
        self.fill(["openai", "theverge", "techcrunch"], title=title,
                  tiers={"openai": 1})

    def queued(self, chat_id=CHAT):
        conn = storage.db()
        try:
            return breaking.pending_alerts(conn, chat_id)
        finally:
            conn.close()

    def test_flash_goes_out_at_once(self):
        self.urgency(9.5)
        self.confirmed()
        self.assertEqual(breaking.check(chat_id=CHAT), 1)
        self.assertIn("⚡", self.sent[0][1])
        self.assertEqual(self.queued(), [])

    def test_alert_is_queued_not_sent(self):
        """Важное человека не будит: оно ждёт сводки."""
        self.urgency(8.0)
        self.confirmed()
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.queued()), 1)

    def test_below_alert_threshold_waits_for_the_issue(self):
        self.urgency(7.0)
        self.confirmed()
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.queued(), [])

    def test_queued_alert_does_not_repeat_in_the_digest(self):
        """Событие уже обещано читателю — плановый выпуск его повторять не должен."""
        self.urgency(8.0)
        self.confirmed()
        breaking.check(chat_id=CHAT)
        conn = storage.db()
        try:
            row = conn.execute("SELECT breaking FROM sent WHERE chat_id=?",
                               (CHAT,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["breaking"], 1)
        finally:
            conn.close()

    def test_bulletin_delivers_the_queue(self):
        self.urgency(8.0)
        self.confirmed()
        breaking.check(chat_id=CHAT)
        conn = storage.db()
        try:
            self.assertEqual(breaking.flush_alerts(conn, CHAT), 1)
            self.assertEqual(breaking.pending_alerts(conn, CHAT), [])
        finally:
            conn.close()
        self.assertIn("🔔", self.sent[0][1])
        self.assertIn("Срочный заголовок", self.sent[0][1])

    def test_bulletin_waits_for_its_interval(self):
        CFG["breaking_alert_every_h"] = 4
        self.urgency(8.0)
        self.confirmed()
        breaking.check(chat_id=CHAT)
        conn = storage.db()
        try:
            self.assertEqual(breaking.flush_alerts(conn, CHAT), 1)
            self.confirmed(title="Совсем другое крупное событие в отрасли")
            breaking.check(chat_id=CHAT)
            self.assertEqual(len(breaking.pending_alerts(conn, CHAT)), 1)
            # интервал ещё не прошёл — вторая сводка подождёт
            self.assertEqual(breaking.flush_alerts(conn, CHAT), 0)
        finally:
            conn.close()

    def test_empty_queue_sends_nothing(self):
        conn = storage.db()
        try:
            self.assertEqual(breaking.flush_alerts(conn, CHAT), 0)
        finally:
            conn.close()
        self.assertEqual(self.sent, [])


class TestQuietHoursLevels(BreakingCase):
    """Тихие часы: молния мирового масштаба будит, остальное копится к утру."""

    def setUp(self):
        super().setUp()
        CFG["breaking_quiet"] = "23:00-08:00"
        self.fill(["openai", "theverge", "techcrunch"], tiers={"openai": 1})

    def test_global_flash_wakes_you_up(self):
        """Ради землетрясения M7 человека будят и в три часа ночи."""
        breaking.local_now = Clock(3)
        self.urgency(9.8, scope="global")
        self.assertEqual(breaking.check(chat_id=CHAT), 1)
        self.assertIn("⚡", self.sent[0][1])

    def test_global_flash_can_be_forbidden(self):
        breaking.local_now = Clock(3)
        CFG["flash_override_quiet"] = False
        self.urgency(9.8, scope="global")
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.sent, [])

    def test_industry_flash_waits_until_morning(self):
        """Отраслевая молния ночью не будит, но и не теряется."""
        breaking.local_now = Clock(3)
        self.urgency(9.5, scope="industry")
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.sent, [])

        conn = storage.db()
        try:
            self.assertEqual(len(breaking.pending_alerts(conn, CHAT)), 1)
            # ночью сводку не отдаём
            self.assertEqual(breaking.flush_alerts(conn, CHAT), 0)
            # ...а утром накопленное догоняет
            breaking.local_now = Clock(9)
            self.assertEqual(breaking.flush_alerts(conn, CHAT), 1)
        finally:
            conn.close()
        self.assertIn("🔔", self.sent[0][1])

    def test_alert_queues_at_night(self):
        breaking.local_now = Clock(2)
        self.urgency(8.0)
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        conn = storage.db()
        try:
            self.assertEqual(len(breaking.pending_alerts(conn, CHAT)), 1)
        finally:
            conn.close()
