# -*- coding: utf-8 -*-
"""Разделы: справочник, выбор, подборка «по паре новостей из каждого».

Сеть и модель не трогаются: ранжирование и карточки подменены заглушками.
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

from newsdigest import bot, config, pipeline, sections  # noqa: E402
from newsdigest import storage, subscribers, userprofiles  # noqa: E402
from newsdigest.config import CFG, now_iso  # noqa: E402
from newsdigest.profiles import BUILTIN, DEFAULT_SECTIONS, PROFILES, title  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False


class TestCatalog(unittest.TestCase):
    """Каталог разделов должен быть цельным: по нему ходят все команды."""

    def test_asked_for_sections_exist(self):
        for name in ("medicine", "health", "politics", "economy", "sports",
                     "space", "incidents", "cinema", "hardware", "robots",
                     "games", "climate"):
            self.assertIn(name, PROFILES, name)

    def test_climate_is_separated_from_science(self):
        """Климат вынесен из науки: свои источники, свой портрет читателя.

        И идёт раньше науки — одно событие показывается один раз, в разделе,
        который стоит в списке выше, иначе разделять было незачем.
        """
        self.assertLess(DEFAULT_SECTIONS.index("climate"),
                        DEFAULT_SECTIONS.index("science"))
        self.assertNotIn("climate", BUILTIN["science"]["keywords"])
        self.assertNotIn("климат", BUILTIN["science"]["persona"].split("НЕ")[0])
        science = {f[0] for f in BUILTIN["science"]["feeds"]}
        self.assertFalse(science & {f[0] for f in BUILTIN["climate"]["feeds"]})

    def test_every_section_is_complete(self):
        for name, body in BUILTIN.items():
            self.assertTrue(body.get("title"), name)
            self.assertTrue(body.get("emoji"), name)
            self.assertTrue(body.get("persona"), name)
            self.assertTrue(body.get("feeds"), name)

    def test_feeds_are_well_formed(self):
        for name, body in BUILTIN.items():
            for feed in body["feeds"]:
                self.assertEqual(len(feed), 4, "%s: %r" % (name, feed))
                source_id, url, tier, category = feed
                self.assertTrue(url.startswith("https://"), url)
                self.assertIn(tier, userprofiles.TIERS, url)
                self.assertIn(category, userprofiles.CATEGORIES, url)
                self.assertTrue(source_id.strip(), url)

    def test_source_id_means_one_feed_everywhere(self):
        """Один id — одна ссылка. Иначе раздел увидит чужие материалы:
        фильтр подписчика работает именно по именам источников."""
        urls = {}
        for name, body in BUILTIN.items():
            for source_id, url, _tier, _cat in body["feeds"]:
                self.assertEqual(urls.setdefault(source_id, url), url,
                                 "%s в разделе %s ведёт на другой фид"
                                 % (source_id, name))

    def test_default_selection_covers_the_ask(self):
        self.assertTrue(set(DEFAULT_SECTIONS) <= set(PROFILES))
        self.assertGreaterEqual(len(DEFAULT_SECTIONS), 10)
        self.assertEqual(len(DEFAULT_SECTIONS), len(set(DEFAULT_SECTIONS)))

    def test_names_do_not_collide(self):
        seen = {}
        for name, body in BUILTIN.items():
            for alias in (name, body["title"]) + tuple(body.get("aliases") or ()):
                self.assertEqual(seen.setdefault(alias.lower(), name), name,
                                 "имя %r занято дважды" % alias)


class TestResolve(unittest.TestCase):
    def test_by_id_title_and_alias(self):
        self.assertEqual(sections.resolve("medicine"), "medicine")
        self.assertEqual(sections.resolve("Медицина"), "medicine")
        self.assertEqual(sections.resolve("мед"), "medicine")
        self.assertEqual(sections.resolve("  СПОРТ "), "sports")
        self.assertEqual(sections.resolve("железо"), "hardware")
        self.assertEqual(sections.resolve("кино и сериалы"), "cinema")

    def test_prefix_when_unambiguous(self):
        self.assertEqual(sections.resolve("космо"), "space")
        self.assertEqual(sections.resolve("происшеств"), "incidents")

    def test_unknown_is_empty(self):
        self.assertEqual(sections.resolve("погода"), "")
        self.assertEqual(sections.resolve(""), "")

    def test_parse_list(self):
        found, unknown = sections.parse("спорт, космос кино")
        self.assertEqual(found, ["sports", "space", "cinema"])
        self.assertEqual(unknown, [])

    def test_parse_reports_unknown_and_drops_repeats(self):
        found, unknown = sections.parse("спорт спорт погода")
        self.assertEqual(found, ["sports"])
        self.assertEqual(unknown, ["погода"])


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.saved = {"sections": CFG["sections"], "per_section": CFG["per_section"],
                      "topic": CFG["topic"]}

    def tearDown(self):
        CFG.update(self.saved)

    def test_default_plan(self):
        CFG["sections"] = ""
        self.assertEqual(sections.plan(), DEFAULT_SECTIONS)

    def test_global_override(self):
        CFG["sections"] = "спорт,космос"
        self.assertEqual(sections.plan(), ["sports", "space"])

    def test_personal_wins_over_global(self):
        CFG["sections"] = "спорт"
        conn = storage.db()
        try:
            subscribers.add(conn, "plan-1", role="member")
            subscribers.set_field(conn, "plan-1", "sections", "medicine,cinema")
            sub = subscribers.get(conn, "plan-1")
        finally:
            conn.close()
        self.assertEqual(sections.plan(sub), ["medicine", "cinema"])
        self.assertEqual(sections.per_section(sub), CFG["per_section"])

    def test_persona_mentions_every_section(self):
        text = sections.persona(["sports", "space"])
        self.assertIn(title("sports"), text)
        self.assertIn(title("space"), text)
        # один раздел говорит своим голосом, а не списком интересов
        self.assertEqual(sections.persona(["ai"]), PROFILES["ai"]["persona"])


class DigestCase(unittest.TestCase):
    """Общая обвязка: подменённая модель и материалы прямо в базе."""

    CHAT = "88"
    PLAN = ["ai", "crypto", "cybersec"]
    #: у каждого раздела свои события: одинаковые заголовки бот справедливо
    #: считает одной новостью и во второй раздел её уже не пустит
    TITLES = {
        "ai": ["Новая модель обошла соперников в тестах",
               "Библиотека инференса ускорилась вдвое",
               "Открыты веса модели на семьдесят миллиардов",
               "Цены на API снизили втрое",
               "Агентный фреймворк вышел из беты",
               "Бенчмарк рассуждений переписали"],
        "crypto": ["Биржа объявила о делистинге токена",
                   "Обновление сети сократило комиссии",
                   "Регулятор одобрил биржевой фонд",
                   "Мост между блокчейнами перезапустили",
                   "Стейблкоин прошёл аудит резервов",
                   "Хардфорк назначили на осень"],
        "cybersec": ["Найдена дыра в почтовом сервере",
                     "Ботнет захватил домашние роутеры",
                     "Утекла база клиентов оператора",
                     "Вышел патч для гипервизора",
                     "Шифровальщик атаковал больницы",
                     "Отозваны сертификаты центра доверия"],
    }

    def setUp(self):
        conn = storage.db()
        for table in ("items", "sent", "leftover", "feedback", "meta", "runs",
                      "subscribers"):
            conn.execute("DELETE FROM %s" % table)
        conn.commit()
        conn.close()

        self.saved = {key: CFG[key] for key in
                      ("sections", "per_section", "topic", "min_score")}
        CFG["sections"] = ",".join(self.PLAN)
        CFG["per_section"] = 2
        CFG["topic"] = "ai"

        self.sent = []
        self._real = (pipeline.tg_send, pipeline.rank_clusters, pipeline.summarize)
        pipeline.tg_send = lambda chat, text, keyboard=None, silent=None: \
            self.sent.append((chat, text, keyboard))
        pipeline.rank_clusters = lambda clusters, persona: (
            [{"id": i, "score": 9.0 - i * 0.1, "category": "labs"}
             for i in range(len(clusters))], {"in": 10, "out": 5})
        pipeline.summarize = lambda picked, persona, language: (
            {i: {"headline": "Карточка %d" % i, "what": "суть", "why": "важно"}
             for i in range(len(picked))}, {"in": 10, "out": 5})

    def tearDown(self):
        pipeline.tg_send, pipeline.rank_clusters, pipeline.summarize = self._real
        CFG.update(self.saved)

    def fill(self, topic, titles=None, offset=0):
        sources = [f[0] for f in PROFILES[topic]["feeds"]]
        titles = titles or self.TITLES[topic]
        conn = storage.db()
        try:
            for i, headline in enumerate(titles):
                row = item("https://%s.example/%d" % (topic, i + offset), headline,
                           sources[i % len(sources)])
                conn.execute(
                    "INSERT OR REPLACE INTO items(url_hash,url,source_id,tier,"
                    "category,title,summary,published_at,fetched_at,sig,social) "
                    "VALUES (:url_hash,:url,:source_id,:tier,:category,:title,"
                    ":summary,:published_at,:fetched_at,:sig,:social)",
                    dict(row, fetched_at=now_iso()))
            conn.commit()
        finally:
            conn.close()

    def text(self):
        return "\n".join(t for _chat, t, _kb in self.sent)

    def sent_rows(self):
        conn = storage.db()
        try:
            return list(conn.execute("SELECT * FROM sent WHERE chat_id=?",
                                     (self.CHAT,)))
        finally:
            conn.close()


class TestMorningDigest(DigestCase):
    def test_two_news_from_every_section(self):
        for topic in self.PLAN:
            self.fill(topic)
        stats = pipeline.build_and_send(chat_id=self.CHAT)
        self.assertEqual(stats["sections"], 3)
        self.assertEqual(stats["selected"], 6)

        text = self.text()
        for topic in self.PLAN:
            self.assertIn(title(topic), text)

        # ровно по две новости из источников каждого раздела
        for topic in self.PLAN:
            own = {f[0] for f in PROFILES[topic]["feeds"]}
            mine = [r for r in self.sent_rows() if r["source_id"] in own]
            self.assertEqual(len(mine), 2, topic)

    def test_pair_comes_from_different_sources(self):
        self.fill("ai")
        pipeline.build_and_send(chat_id=self.CHAT)
        sources = [r["source_id"] for r in self.sent_rows()]
        self.assertEqual(len(sources), len(set(sources)))

    def test_one_story_does_not_land_in_two_sections(self):
        """Одно событие в двух разделах — это дубль, а не два повода."""
        same = "Взлом биржи оставил инженеров без сна"
        self.fill("crypto", [same] + self.TITLES["crypto"][:2])
        self.fill("cybersec", [same] + self.TITLES["cybersec"][:2], offset=50)
        pipeline.build_and_send(chat_id=self.CHAT)
        titles = [r["title"] for r in self.sent_rows()]
        self.assertEqual(titles.count(same), 1)
        self.assertEqual(len(titles), len(set(titles)))

    def test_empty_sections_are_named(self):
        self.fill("ai")
        pipeline.build_and_send(chat_id=self.CHAT)
        text = self.text()
        self.assertIn("без новостей", text)
        self.assertIn(title("crypto"), text)

    def test_nothing_new_second_time(self):
        for topic in self.PLAN:
            self.fill(topic, self.TITLES[topic][:2])   # ровно на один выпуск
        pipeline.build_and_send(chat_id=self.CHAT)
        before = len(self.sent)
        self.assertEqual(pipeline.build_and_send(chat_id=self.CHAT)["sent"], 0)
        self.assertEqual(len(self.sent), before)

    def test_each_is_configurable(self):
        for topic in self.PLAN:
            self.fill(topic)
        CFG["per_section"] = 3
        stats = pipeline.build_and_send(chat_id=self.CHAT)
        self.assertEqual(stats["selected"], 9)

    def test_single_section_gets_a_full_digest(self):
        CFG["sections"] = "ai"
        self.fill("ai")
        stats = pipeline.build_and_send(chat_id=self.CHAT)
        self.assertEqual(stats["selected"], len(self.TITLES["ai"]))


class TestSectionOnDemand(DigestCase):
    def test_top_of_one_section(self):
        self.fill("crypto")
        stats = pipeline.build_section("crypto", 3, chat_id=self.CHAT)
        self.assertEqual(stats["selected"], 3)
        self.assertEqual(stats["sections"], 1)
        self.assertIn(title("crypto"), self.text())

    def test_count_is_bounded(self):
        self.fill("ai")
        stats = pipeline.build_section("ai", 99, chat_id=self.CHAT)
        self.assertLessEqual(stats["selected"], CFG["section_max_items"])

    def test_next_request_returns_other_news(self):
        """Повторный запрос не должен пересказывать то же самое."""
        self.fill("ai")
        pipeline.build_section("ai", 2, chat_id=self.CHAT)
        first = {r["title"] for r in self.sent_rows()}
        pipeline.build_section("ai", 2, chat_id=self.CHAT)
        second = {r["title"] for r in self.sent_rows()} - first
        self.assertTrue(second)
        self.assertFalse(first & second)

    def test_request_does_not_close_the_morning(self):
        conn = storage.db()
        try:
            subscribers.add(conn, self.CHAT, role="member")
            sub = subscribers.get(conn, self.CHAT)
        finally:
            conn.close()
        self.fill("ai")
        pipeline.build_section("ai", 2, sub=sub)
        conn = storage.db()
        try:
            self.assertEqual(subscribers.get(conn, self.CHAT)["last_digest"], "")
        finally:
            conn.close()


class TestSectionCommands(unittest.TestCase):
    OWNER = "100500"

    def setUp(self):
        self.saved_cfg = dict(CFG)
        self.saved_env = dict(os.environ)
        self.saved_owner = config.TG_CHAT
        config.TG_CHAT = self.OWNER
        if config.ENV_FILE.exists():
            config.ENV_FILE.unlink()
        self.sent = []
        self._real_send = bot.tg_send
        bot.tg_send = lambda chat, text, keyboard=None, silent=None: \
            self.sent.append((str(chat), text))
        conn = storage.db()
        try:
            conn.execute("DELETE FROM subscribers")
            conn.commit()
            subscribers.ensure_owner(conn)
        finally:
            conn.close()

    def tearDown(self):
        bot.tg_send = self._real_send
        config.TG_CHAT = self.saved_owner
        CFG.clear()
        CFG.update(self.saved_cfg)
        os.environ.clear()
        os.environ.update(self.saved_env)

    def message(self, text, chat_id=None, worker=None):
        """Обработчик команды напрямую: интерфейса у команд больше нет."""
        name, args = bot.parse_command(text)
        cmd = bot.HANDLERS[name]
        conn = storage.db()
        try:
            reply = cmd.fn(bot.Ctx(chat_id or self.OWNER, args, conn, worker))
        finally:
            conn.close()
        if reply:
            self.sent.append((chat_id or self.OWNER, reply))
        return "\n".join(t for _c, t in self.sent)

    def test_list_shows_every_section(self):
        text = self.message("/sections")
        for name in ("medicine", "sports", "cinema"):
            self.assertIn(title(name), text)
        self.assertIn("✅", text)

    def test_add_and_remove(self):
        self.message("/sections rm спорт")
        self.assertNotIn("sports", sections.plan())
        self.sent = []
        self.message("/sections add спорт")
        self.assertIn("sports", sections.plan())

    def test_only_and_reset(self):
        self.message("/sections only спорт, космос")
        self.assertEqual(sections.plan(), ["sports", "space"])
        self.sent = []
        text = self.message("/sections reset")
        self.assertEqual(sections.plan(), DEFAULT_SECTIONS)
        self.assertIn("по умолчанию", text)

    def test_cannot_empty_the_list(self):
        self.message("/sections only спорт")
        self.sent = []
        text = self.message("/sections rm спорт")
        self.assertIn("ни одного раздела", text)
        self.assertEqual(sections.plan(), ["sports"])

    def test_unknown_section_is_explained(self):
        text = self.message("/sections add погода")
        self.assertIn("Не знаю раздел", text)

    def test_set_sections_from_settings(self):
        self.message("/set sections медицина,кино")
        self.assertEqual(sections.plan(), ["medicine", "cinema"])
        self.assertEqual(os.environ["ND_SECTIONS"], "medicine,cinema")

    def test_news_needs_a_known_section(self):
        text = self.message("/news погода", worker=bot.Worker())
        self.assertIn("Не знаю раздел", text)
        self.assertNotIn("Собираю", text)

    def test_news_reports_what_it_collects(self):
        worker = bot.Worker()          # без start(): задача просто встанет в очередь
        text = self.message("/news медицина 7", worker=worker)
        self.assertIn("топ-7", text)
        self.assertIn(title("medicine"), text)
        self.assertEqual(worker.queue.get_nowait()[0], "news:medicine")

    def test_count_is_parsed_off_the_name(self):
        self.assertEqual(bot.split_count(["кино", "и", "сериалы", "10"]),
                         ("кино и сериалы", 10))
        self.assertEqual(bot.split_count(["спорт"]), ("спорт", 0))
        self.assertEqual(bot.split_count([]), ("", 0))


#: схема подписчиков версии 3.1 — без разделов
OLD_SUBSCRIBERS = """
CREATE TABLE subscribers (
    chat_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'private',
    role        TEXT NOT NULL DEFAULT 'member',
    topic       TEXT NOT NULL DEFAULT '',
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
"""


class TestUpgradeFrom31(unittest.TestCase):
    """Кто выбрал себе тему, тот на ней и остаётся — молча его не переселяем."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="nd31-"))
        self._saved = (storage.DB_FILE, storage.HOME)
        storage.DB_FILE, storage.HOME = self.home / "digest.db", self.home
        old = sqlite3.connect(str(storage.DB_FILE))
        old.executescript(OLD_SUBSCRIBERS)
        old.executemany(
            "INSERT INTO subscribers(chat_id, role, topic, created_at) "
            "VALUES (?,?,?,?)",
            [("1", "owner", "", now_iso()), ("2", "member", "crypto", now_iso())])
        old.commit()
        old.close()

    def tearDown(self):
        storage.DB_FILE, storage.HOME = self._saved

    def test_personal_topic_becomes_personal_sections(self):
        conn = storage.db()
        try:
            self.assertEqual(subscribers.get(conn, "2")["sections"], "crypto")
            self.assertEqual(sections.plan(subscribers.get(conn, "2")), ["crypto"])
            # у кого личной темы не было — тот получает подборку по всем разделам
            self.assertEqual(subscribers.get(conn, "1")["sections"], "")
            self.assertEqual(sections.plan(subscribers.get(conn, "1")),
                             sections.defaults())
        finally:
            conn.close()

    def test_upgrade_runs_once(self):
        conn = storage.db()
        subscribers.set_field(conn, "2", "sections", "sports")
        conn.close()
        conn = storage.db()
        try:
            self.assertEqual(subscribers.get(conn, "2")["sections"], "sports")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
