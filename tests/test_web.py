# -*- coding: utf-8 -*-
"""Тесты страницы: доступ по паролю, лента, команды, кнопки, санитайзер.

Сеть не трогается: сервер поднимается на 127.0.0.1 со случайным портом,
Telegram подменён заглушкой.
"""
import json
import logging
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import bot, config, feedback, profiles, sections, storage  # noqa: E402
from newsdigest import subscribers, web  # noqa: E402
from newsdigest.config import CFG, now_iso  # noqa: E402
from newsdigest.profiles import profile  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

TOKEN = "пароль-для-теста"
OWNER = "4242"


class WebCase(unittest.TestCase):
    def setUp(self):
        self.saved_token = CFG["web_token"]
        self.saved_owner = config.TG_CHAT
        CFG["web_token"] = TOKEN
        config.TG_CHAT = OWNER
        self.sent = []
        self._real_send = web.tg_send
        web.tg_send = lambda chat, text, keyboard=None, silent=None: \
            self.sent.append((str(chat), text))

        conn = storage.db()
        try:
            for table in ("outbox", "feedback", "saved", "subscribers",
                          "leftover", "sent"):
                conn.execute("DELETE FROM %s" % table)
            conn.commit()
            subscribers.ensure_owner(conn)
        finally:
            conn.close()

        self.server = web.build(worker=None, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.base = "http://%s:%d" % (host, port)
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        web.tg_send = self._real_send
        CFG["web_token"] = self.saved_token
        config.TG_CHAT = self.saved_owner
        web._FAILS.clear()

    # ------------------------------------------------------------ помощники
    def ask(self, path, body=None, headers=None):
        """Возвращает (код, тело-словарь). Ошибки HTTP — не исключение."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data,
                                         method="POST" if data else "GET")
        request.add_header("Content-Type", "application/json")
        if self.cookie:
            request.add_header("Cookie", self.cookie)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as res:
                raw, code, info = res.read(), res.status, res.info()
        except urllib.error.HTTPError as exc:
            raw, code, info = exc.read(), exc.code, exc.headers
        cookie = info.get("Set-Cookie")
        if cookie and cookie.startswith(web.COOKIE + "="):
            self.cookie = cookie.split(";", 1)[0]
        if info.get_content_type() == "text/html":
            return code, raw.decode("utf-8")
        return code, json.loads(raw.decode("utf-8")) if raw else {}

    def login(self, token=TOKEN):
        return self.ask("/api/login", {"token": token})

    def outbox(self):
        conn = storage.db()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM outbox WHERE chat_id=? ORDER BY id", (OWNER,))]
        finally:
            conn.close()

    def delivered(self, url_hash, title, source_id, section="", score=0.0,
                  summary="", url="", minute=0, chat=OWNER):
        """Новость, которая читателю уже уходила, — из неё и состоит лента."""
        conn = storage.db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sent(chat_id,url_hash,sig,title,url,"
                "source_id,category,section,headline,summary,score,digest_date,"
                "sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (chat, url_hash, "", title,
                 url or "https://example.com/%s" % url_hash, source_id, "media",
                 section, title, summary, score, "2026-08-17",
                 "2026-08-17T09:%02d:00+00:00" % minute))
            conn.commit()
        finally:
            conn.close()


class TestAuth(WebCase):
    def test_page_opens_without_password(self):
        code, body = self.ask("/")
        self.assertEqual(code, 200)
        self.assertIn("Дайджест", body)

    def test_api_requires_password(self):
        code, body = self.ask("/api/feed")
        self.assertEqual(code, 401)
        self.assertIn("пароль", body["error"])

    def test_login_opens_the_feed(self):
        self.assertEqual(self.login()[0], 200)
        self.assertTrue(self.cookie)
        code, body = self.ask("/api/feed")
        self.assertEqual(code, 200)
        self.assertIn("state", body)

    def test_wrong_password_refused(self):
        code, body = self.login("не тот")
        self.assertEqual(code, 403)
        self.assertFalse(self.cookie)
        self.assertEqual(self.ask("/api/feed")[0], 401)

    def test_bearer_token_works_for_scripts(self):
        CFG["web_token"] = "ascii-token-42"     # в заголовок кириллица не влезет
        code, _body = self.ask("/api/feed",
                               headers={"Authorization": "Bearer ascii-token-42"})
        self.assertEqual(code, 200)

    def test_cookie_does_not_contain_the_password(self):
        self.login()
        self.assertNotIn(TOKEN, self.cookie)

    def test_logout_closes_access(self):
        self.login()
        self.ask("/api/logout", {})
        self.cookie = web.COOKIE + "="            # браузер стёр значение
        self.assertEqual(self.ask("/api/feed")[0], 401)


class TestFeed(WebCase):
    def test_bot_messages_appear(self):
        conn = storage.db()
        storage.save_outbox(conn, OWNER, "Привет <b>мир</b>")
        conn.close()
        self.login()
        _code, body = self.ask("/api/feed")
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["html"], "Привет <b>мир</b>")
        self.assertEqual(body["last"], body["messages"][0]["id"])

    def test_only_new_messages_after_id(self):
        conn = storage.db()
        first = storage.save_outbox(conn, OWNER, "раз")
        storage.save_outbox(conn, OWNER, "два")
        conn.close()
        self.login()
        _code, body = self.ask("/api/feed?after=%d" % first)
        self.assertEqual([m["html"] for m in body["messages"]], ["два"])

    def test_other_chats_are_not_shown(self):
        conn = storage.db()
        storage.save_outbox(conn, "999", "чужое")
        conn.close()
        self.login()
        _code, body = self.ask("/api/feed")
        self.assertEqual(body["messages"], [])

    def test_state_reports_sections_and_schedule(self):
        self.login()
        _code, body = self.ask("/api/feed")
        ids = [s["id"] for s in body["state"]["sections"]]
        self.assertIn(CFG["topic"], ids)
        self.assertGreaterEqual(body["state"]["each"], 1)
        self.assertIn("в ", body["state"]["next"])
        self.assertTrue(body["state"]["owner"])
        self.assertTrue(any(c["name"] == "digest" for c in body["commands"]))

    def test_telegram_messages_are_mirrored(self):
        """Всё, что уходит в чат, попадает на страницу — это и есть замена."""
        from newsdigest import telegram
        telegram.mirror(OWNER, "📡 <b>Дайджест</b>", [[{"text": "1 👍",
                                                        "callback_data": "fb:up:h1"}]])
        self.login()
        _code, body = self.ask("/api/feed")
        message = body["messages"][0]
        self.assertIn("Дайджест", message["html"])
        self.assertEqual(message["buttons"][0][0]["text"], "1 👍")
        self.assertFalse(message["buttons"][0][0]["pressed"])
        self.assertFalse(message["fold"])        # одна новость — прятать нечего

    def test_many_reaction_rows_are_folded_on_the_page(self):
        """Кнопки приезжают все, но страница прячет их под одну — как в чате."""
        from newsdigest import telegram
        keyboard = [[{"text": "%d 👍" % n, "callback_data": "fb:up:h%d" % n},
                     {"text": "%d 👎" % n, "callback_data": "fb:down:h%d" % n}]
                    for n in (1, 2, 3)]
        telegram.mirror(OWNER, "📡 <b>Дайджест</b>", keyboard)
        self.login()
        _code, body = self.ask("/api/feed")
        message = body["messages"][-1]
        self.assertTrue(message["fold"])
        self.assertEqual(len(message["buttons"]), 3)

    def test_rows_style_shows_everything_at_once(self):
        from newsdigest import telegram
        keyboard = [[{"text": "%d 👍" % n, "callback_data": "fb:up:h%d" % n}]
                    for n in (1, 2)]
        telegram.mirror(OWNER, "выпуск", keyboard)
        CFG["feedback_style"] = "rows"
        try:
            self.login()
            _code, body = self.ask("/api/feed")
        finally:
            CFG["feedback_style"] = "compact"
        self.assertFalse(body["messages"][-1]["fold"])


class TestCommands(WebCase):
    def test_help_answers_on_the_page(self):
        self.login()
        _code, body = self.ask("/api/command", {"text": "/help"})
        kinds = [m["kind"] for m in body["messages"]]
        self.assertEqual(kinds, ["me", "bot"])
        self.assertIn("/digest", body["messages"][1]["html"])
        self.assertEqual(self.sent, [])          # в Telegram не дублируем

    def test_slash_is_optional(self):
        self.login()
        _code, body = self.ask("/api/command", {"text": "status"})
        self.assertIn("Выпуски:", body["messages"][1]["html"])

    def test_unknown_command(self):
        self.login()
        _code, body = self.ask("/api/command", {"text": "/нетакой"})
        self.assertIn("Не знаю команду", body["messages"][1]["html"])

    def test_heavy_command_needs_daemon(self):
        self.login()
        _code, body = self.ask("/api/command", {"text": "/digest"})
        self.assertIn("Фоновые задачи недоступны", body["messages"][1]["html"])

    def test_settings_change_survives(self):
        self.login()
        saved = CFG["min_score"]
        try:
            _code, body = self.ask("/api/command", {"text": "/set score 7.5"})
            self.assertIn("7.5", body["messages"][1]["html"])
            self.assertEqual(CFG["min_score"], 7.5)
        finally:
            CFG["min_score"] = saved

    def test_owner_command_blocked_without_chat_id(self):
        config.TG_CHAT = ""
        self.login()
        _code, body = self.ask("/api/command", {"text": "/feed list"})
        self.assertIn("только для владельца", body["messages"][1]["html"])

    def test_command_failure_is_reported_not_crashed(self):
        def boom(ctx):
            raise RuntimeError("сломалось")

        real = bot.HANDLERS["status"].fn
        bot.HANDLERS["status"].fn = boom
        try:
            self.login()
            code, body = self.ask("/api/command", {"text": "/status"})
        finally:
            bot.HANDLERS["status"].fn = real
        self.assertEqual(code, 200)
        self.assertIn("сломалось", body["messages"][1]["html"])


class TestButtons(WebCase):
    def press(self, data):
        return self.ask("/api/react", {"data": data})

    def test_thumbs_up_is_recorded(self):
        self.login()
        _code, body = self.press("fb:up:hash1")
        self.assertIn("👍", body["toast"])
        self.assertTrue(body["press"]["pressed"]["up"])
        conn = storage.db()
        try:
            row = conn.execute("SELECT verdict FROM feedback WHERE chat_id=? AND "
                               "url_hash='hash1'", (OWNER,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["verdict"], feedback.UP)

    def test_thumbs_down_replaces_thumbs_up(self):
        self.login()
        self.press("fb:up:hash2")
        _code, body = self.press("fb:down:hash2")
        self.assertFalse(body["press"]["pressed"]["up"])
        self.assertTrue(body["press"]["pressed"]["down"])

    def test_bookmark_toggles(self):
        self.login()
        _code, body = self.press("fb:save:hash3")
        self.assertTrue(body["press"]["pressed"]["save"])
        _code, body = self.press("fb:save:hash3")
        self.assertFalse(body["press"]["pressed"]["save"])

    def test_pressed_state_comes_back_with_the_feed(self):
        conn = storage.db()
        storage.save_outbox(conn, OWNER, "новость",
                            [[{"text": "1 👍", "callback_data": "fb:up:hash4"},
                              {"text": "1 🔖", "callback_data": "fb:save:hash4"}]])
        conn.close()
        self.login()
        self.press("fb:up:hash4")
        _code, body = self.ask("/api/feed")
        row = body["messages"][0]["buttons"][0]
        self.assertTrue(row[0]["pressed"])
        self.assertFalse(row[1]["pressed"])

    def test_signup_button_approves(self):
        conn = storage.db()
        subscribers.add(conn, "777", role="pending")
        conn.close()
        self.login()
        _code, body = self.press("sub:ok:777")
        self.assertIn("Пустил", body["toast"])
        self.assertTrue(bot.is_allowed("777"))
        self.assertEqual(self.sent[0][0], "777")

    def test_garbage_press_is_ignored(self):
        self.login()
        code, body = self.press("что-то не то")
        self.assertEqual(code, 200)
        self.assertEqual(body["toast"], "")


class TestDigestOnThePage(WebCase):
    """Главное обещание: выпуск, заказанный со страницы, приходит на страницу."""

    def setUp(self):
        WebCase.setUp(self)
        from newsdigest import pipeline, telegram

        self.server.worker = bot.Worker().start()
        self._real = (bot.collect, pipeline.rank_clusters, pipeline.summarize,
                      telegram.tg_call)
        bot.collect = lambda: None             # сеть в тестах не трогаем
        pipeline.rank_clusters = lambda clusters, persona: (
            [{"id": i, "score": 9.0, "category": "labs"}
             for i in range(len(clusters))], {"in": 1, "out": 1})
        pipeline.summarize = lambda picked, persona, language: (
            {i: {"headline": "Карточка %d" % i, "what": "суть", "why": "важно"}
             for i in range(len(picked))}, {"in": 1, "out": 1})
        telegram.tg_call = lambda method, payload, **kw: {"message_id": 1}

        conn = storage.db()
        try:
            conn.execute("DELETE FROM items")
            conn.execute("DELETE FROM sent")
            sources = [f[0] for f in profile("ai")["feeds"]]
            for i, title in enumerate(("Postgres ускорил вакуум",
                                       "Nvidia показала ускоритель",
                                       "Rust добавил асинхронные трейты")):
                row = item("https://e.com/%d" % i, title, sources[i])
                conn.execute(
                    "INSERT OR REPLACE INTO items(url_hash,url,source_id,tier,"
                    "category,title,summary,published_at,fetched_at,sig,social) "
                    "VALUES (:url_hash,:url,:source_id,:tier,:category,:title,"
                    ":summary,:published_at,:fetched_at,:sig,:social)",
                    dict(row, fetched_at=now_iso()))
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        from newsdigest import pipeline, telegram
        (bot.collect, pipeline.rank_clusters, pipeline.summarize,
         telegram.tg_call) = self._real
        WebCase.tearDown(self)

    def test_digest_from_the_page_lands_on_the_page(self):
        self.login()
        _code, body = self.ask("/api/command", {"text": "/digest"})
        self.assertIn("Собираю", body["messages"][-1]["html"])

        self.server.worker.queue.join()
        _code, body = self.ask("/api/feed?after=%d" % body["last"])
        html = "\n".join(m["html"] for m in body["messages"])
        self.assertIn(profiles.title("ai"), html)     # заголовок раздела
        self.assertIn("Карточка 0", html)
        # и кнопки под выпуском живые
        keys = [b for m in body["messages"] for row in m["buttons"] for b in row]
        self.assertTrue(any(b["data"].startswith("fb:up:") for b in keys))


class TestNews(WebCase):
    """Лента новостей: то, ради чего страница перестала быть чатом."""

    def setUp(self):
        WebCase.setUp(self)
        self.delivered("h1", "В Иране заявили о проходе через пролив", "ria",
                       "incidents", 8.4, "Тегеран ответил на заявление США.",
                       "https://ria.ru/one", minute=1)
        self.delivered("h2", "Переговоры по Украине: главы МИД встретятся",
                       "interfax", "politics", 7.8,
                       "Экстренная встреча 18 августа.",
                       "https://www.interfax.ru/two", minute=2)
        self.delivered("h3", "Иран и нефть: цены пошли вверх", "rbc", "economy",
                       7.1, "", "https://rbc.ru/three", minute=3)
        # запись без раздела: так выглядит история, собранная до обновления
        self.delivered("h4", "Apple представила новые MacBook", "theverge",
                       "", 0.0, "", "https://www.theverge.com/four", minute=4)

    def news(self, params=""):
        return self.ask("/api/news" + params)[1]

    def test_news_needs_password(self):
        code, body = self.ask("/api/news")
        self.assertEqual(code, 401)
        self.assertIn("пароль", body["error"])

    def test_cards_carry_section_source_and_score(self):
        self.login()
        items = self.news()["items"]
        self.assertEqual(len(items), 4)
        first = items[0]                       # свежая новость идёт первой
        self.assertEqual(first["hash"], "h4")
        card = [i for i in items if i["hash"] == "h1"][0]
        self.assertEqual(card["section"], "incidents")
        self.assertEqual(card["label"], profiles.title("incidents"))
        self.assertEqual(card["source"], "ria.ru")     # домен, а не source_id
        self.assertEqual(card["score"], 8.4)
        self.assertIn("Тегеран", card["summary"])
        self.assertTrue(card["tone"])

    def test_old_row_gets_its_section_from_the_source(self):
        """У истории до обновления раздела нет — берём его по источнику."""
        self.login()
        card = [i for i in self.news()["items"] if i["hash"] == "h4"][0]
        self.assertEqual(card["section"], sections.by_source("theverge"))
        self.assertTrue(card["section"])

    def test_section_filter(self):
        self.login()
        items = self.news("?section=incidents")["items"]
        self.assertEqual([i["hash"] for i in items], ["h1"])

    def test_section_filter_reaches_rows_without_a_section(self):
        self.login()
        topic = sections.by_source("theverge")
        items = self.news("?section=" + topic)["items"]
        self.assertIn("h4", [i["hash"] for i in items])

    def test_unknown_section_shows_everything(self):
        self.login()
        self.assertEqual(len(self.news("?section=no-such-thing")["items"]), 4)

    def test_search_ignores_case_in_russian(self):
        """LOWER() в SQLite кириллицу не знает — поиск обязан знать."""
        self.login()
        found = self.news("?q=" + urllib.parse.quote("иран"))["items"]
        self.assertEqual(sorted(i["hash"] for i in found), ["h1", "h3"])

    def test_search_looks_into_summary_and_source(self):
        self.login()
        self.assertEqual([i["hash"] for i in
                          self.news("?q=" + urllib.parse.quote("Тегеран"))["items"]],
                         ["h1"])
        self.assertEqual([i["hash"] for i in self.news("?q=interfax")["items"]],
                         ["h2"])

    def test_search_finds_other_forms_of_the_word(self):
        """«Иране» и «Иран» — одна новость: искать по падежам читатель не обязан."""
        self.login()
        found = self.news("?q=" + urllib.parse.quote("Иране"))["items"]
        self.assertEqual(sorted(i["hash"] for i in found), ["h1", "h3"])
        # и наоборот: плашка «Иран» из популярных тем находит «в Иране»
        found = self.news("?q=" + urllib.parse.quote("Ирана"))["items"]
        self.assertEqual(sorted(i["hash"] for i in found), ["h1", "h3"])

    def test_search_words_narrow_the_answer(self):
        self.login()
        found = self.news("?q=" + urllib.parse.quote("иран нефть"))["items"]
        self.assertEqual([i["hash"] for i in found], ["h3"])

    def test_nothing_found_is_not_an_error(self):
        self.login()
        body = self.news("?q=" + urllib.parse.quote("криптовалюты"))
        self.assertEqual(body["items"], [])
        self.assertFalse(body["more"])

    def test_show_more_pages_through_the_history(self):
        for n in range(25):
            self.delivered("p%d" % n, "Новость номер %d" % n, "ria", "incidents",
                           6.0, minute=10 + n)
        self.login()
        first = self.news()
        self.assertEqual(len(first["items"]), 20)
        self.assertTrue(first["more"])
        second = self.news("?offset=20")
        self.assertEqual(len(second["items"]), 9)       # 25 плюс четыре из setUp
        self.assertFalse(second["more"])
        # страницы не пересекаются
        self.assertFalse(set(i["hash"] for i in first["items"]) &
                         set(i["hash"] for i in second["items"]))

    def test_panels_come_with_the_first_page_only(self):
        self.login()
        self.assertIn("side", self.news())
        self.assertNotIn("side", self.news("?offset=20"))

    def test_menu_counts_sections(self):
        self.login()
        menu = self.news()["side"]["menu"]
        home = menu[0]
        self.assertEqual(home["id"], "")
        self.assertEqual(home["count"], 4)
        found = [m for m in menu if m["id"] == "incidents"]
        self.assertEqual(found[0]["count"], 1)

    def test_popular_sources_carry_the_rating(self):
        self.delivered("h5", "Ещё одна новость РИА", "ria", "incidents", 9.0,
                       url="https://ria.ru/five", minute=5)
        self.login()
        sources = self.news()["side"]["sources"]
        top = sources[0]
        self.assertEqual(top["name"], "ria.ru")
        self.assertEqual(top["count"], 2)
        self.assertEqual(top["rating"], 8.7)            # среднее 8.4 и 9.0

    def test_popular_topics_are_words_from_headlines(self):
        self.login()
        words = [t["word"] for t in self.news()["side"]["topics"]]
        self.assertIn("Иран", words)                    # встречается дважды
        self.assertNotIn("новые", words)                # служебное слово

    def test_saved_view_shows_bookmarks(self):
        self.login()
        self.ask("/api/react", {"data": "fb:save:h2"})
        items = self.news("?view=saved")["items"]
        self.assertEqual([i["hash"] for i in items], ["h2"])
        self.assertTrue(items[0]["saved"])
        # раздел и суть подтянулись из истории, а не потерялись
        self.assertEqual(items[0]["section"], "politics")
        self.assertIn("Экстренная", items[0]["summary"])

    def test_bookmark_removed_leaves_the_saved_view(self):
        self.login()
        self.ask("/api/react", {"data": "fb:save:h2"})
        self.ask("/api/react", {"data": "fb:save:h2"})
        self.assertEqual(self.news("?view=saved")["items"], [])

    def test_liked_view_shows_only_thumbs_up(self):
        self.login()
        self.ask("/api/react", {"data": "fb:up:h1"})
        self.ask("/api/react", {"data": "fb:down:h3"})
        items = self.news("?view=liked")["items"]
        self.assertEqual([i["hash"] for i in items], ["h1"])
        self.assertEqual(items[0]["verdict"], "up")

    def test_pressed_state_shows_on_the_cards(self):
        self.login()
        self.ask("/api/react", {"data": "fb:save:h1"})
        card = [i for i in self.news()["items"] if i["hash"] == "h1"][0]
        self.assertTrue(card["saved"])
        self.assertEqual(card["verdict"], "")

    def test_other_readers_history_is_not_shown(self):
        self.delivered("z1", "Чужая новость", "ria", "incidents", 7.0,
                       minute=9, chat="999")
        self.login()
        self.assertNotIn("z1", [i["hash"] for i in self.news()["items"]])

    def test_unknown_view_falls_back_to_the_feed(self):
        self.login()
        body = self.news("?view=whatever")
        self.assertEqual(body["view"], "news")
        self.assertEqual(len(body["items"]), 4)

    def test_state_says_when_the_sources_were_read(self):
        conn = storage.db()
        storage.meta_set(conn, "last_collect", "2026-08-17T15:27:00+00:00")
        conn.close()
        self.login()
        self.assertRegex(self.news()["state"]["collected"], r"^\d{2}:\d{2}$")


class TestSanitizer(unittest.TestCase):
    def test_keeps_telegram_tags(self):
        self.assertEqual(web.safe_html("<b>жирно</b> и <i>курсив</i>"),
                         "<b>жирно</b> и <i>курсив</i>")

    def test_links_open_safely(self):
        out = web.safe_html('<a href="https://e.com/x">ссылка</a>')
        self.assertIn('href="https://e.com/x"', out)
        self.assertIn('rel="noopener noreferrer"', out)

    def test_javascript_href_dropped(self):
        out = web.safe_html('<a href="javascript:alert(1)">клик</a>')
        self.assertNotIn("javascript", out)

    def test_script_becomes_text(self):
        out = web.safe_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_image_onerror_is_not_a_tag(self):
        out = web.safe_html('<img src=x onerror="alert(1)">')
        self.assertNotIn("<img", out)

    def test_quote_in_url_cannot_break_out_of_href(self):
        # ссылка приходит из чужого фида — кавычка в ней не должна дать
        # дописать свой атрибут
        out = web.safe_html("""<a href='https://e.com/"onmouseover="alert(1)'>x</a>""")
        self.assertNotIn('onmouseover="alert', out)
        self.assertIn("&quot;", out)

    def test_link_attributes_are_dropped(self):
        out = web.safe_html('<a href="https://e.com" onclick="alert(1)">x</a>')
        self.assertNotIn("onclick", out)


if __name__ == "__main__":
    unittest.main()
