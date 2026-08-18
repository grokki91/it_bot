# -*- coding: utf-8 -*-
"""Тесты страницы: доступ по паролю, лента, уведомления, настройки, кнопки.

Страница только показывает: команд, строки ввода и истории их запусков на
ней нет — это проверяется отдельно. Сеть не трогается: сервер поднимается
на 127.0.0.1 со случайным портом.
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

from newsdigest import config, feedback, newsfeed, profiles  # noqa: E402
from newsdigest import sections, storage, subscribers, translate, web  # noqa: E402
from newsdigest.config import CFG, now_iso  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402

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

        conn = storage.db()
        try:
            for table in ("outbox", "feedback", "saved", "subscribers",
                          "leftover", "sent", "items", "translations"):
                conn.execute("DELETE FROM %s" % table)
            conn.commit()
            subscribers.ensure_owner(conn)
        finally:
            conn.close()
        # лента помнит между запросами, за какие строки к модели ходить не
        # стоит; между тестами эта память мешает
        newsfeed._stubborn.clear()
        newsfeed._silent_until = 0.0

        self.server = web.build(worker=None, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.base = "http://%s:%d" % (host, port)
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
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

    def delivered(self, url_hash, title, source_id, section="", score=0.0,
                  summary="", url="", minute=0, chat=OWNER, headline=None,
                  hour=9):
        """Новость, которая читателю уже уходила, — из неё и состоит лента.

        headline='' — запись, сделанная до появления карточки в истории:
        заголовок у неё только из фида, а сути нет вовсе. Час отправки нужен
        «Уведомлениям»: по разрыву во времени они и делят историю на рассылки.
        """
        conn = storage.db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sent(chat_id,url_hash,sig,title,url,"
                "source_id,category,section,headline,summary,score,digest_date,"
                "sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (chat, url_hash, "", title,
                 url or "https://example.com/%s" % url_hash, source_id, "media",
                 section, title if headline is None else headline, summary,
                 score, "2026-08-17",
                 "2026-08-17T%02d:%02d:00+00:00" % (hour, minute)))
            conn.commit()
        finally:
            conn.close()

    def material(self, url_hash, title, summary, source_id="theverge"):
        """Сам материал из фида: из него карточка берёт текст, если в истории
        его нет."""
        conn = storage.db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO items(url_hash,url,source_id,title,"
                "summary,fetched_at,state) VALUES (?,?,?,?,?,?,'sent')",
                (url_hash, "https://example.com/%s" % url_hash, source_id,
                 title, summary, now_iso()))
            conn.commit()
        finally:
            conn.close()

    def stored(self, url_hash, chat=OWNER):
        """Строка истории — что от карточки осело в базе."""
        conn = storage.db()
        try:
            return conn.execute(
                "SELECT * FROM sent WHERE chat_id=? AND url_hash=?",
                (chat, url_hash)).fetchone()
        finally:
            conn.close()


class TestAuth(WebCase):
    def test_page_opens_without_password(self):
        code, body = self.ask("/")
        self.assertEqual(code, 200)
        self.assertIn("Дайджест", body)

    def test_api_requires_password(self):
        code, body = self.ask("/api/alerts")
        self.assertEqual(code, 401)
        self.assertIn("пароль", body["error"])

    def test_login_opens_the_page(self):
        self.assertEqual(self.login()[0], 200)
        self.assertTrue(self.cookie)
        code, body = self.ask("/api/alerts")
        self.assertEqual(code, 200)
        self.assertIn("state", body)

    def test_wrong_password_refused(self):
        code, body = self.login("не тот")
        self.assertEqual(code, 403)
        self.assertFalse(self.cookie)
        self.assertEqual(self.ask("/api/alerts")[0], 401)

    def test_bearer_token_works_for_scripts(self):
        CFG["web_token"] = "ascii-token-42"     # в заголовок кириллица не влезет
        code, _body = self.ask("/api/alerts",
                               headers={"Authorization": "Bearer ascii-token-42"})
        self.assertEqual(code, 200)

    def test_cookie_does_not_contain_the_password(self):
        self.login()
        self.assertNotIn(TOKEN, self.cookie)

    def test_logout_closes_access(self):
        self.login()
        self.ask("/api/logout", {})
        self.cookie = web.COOKIE + "="            # браузер стёр значение
        self.assertEqual(self.ask("/api/alerts")[0], 401)


class TestAlerts(WebCase):
    """Уведомления: коротко о каждой рассылке и ссылки на главное из неё."""

    def alerts(self):
        return self.ask("/api/alerts")[1]

    def digest(self, hour, hashes, section="politics"):
        """Одна рассылка: новости, ушедшие подряд, минута в минуту."""
        for n, url_hash in enumerate(hashes):
            self.delivered(url_hash, "Новость %s" % url_hash, "ria", section,
                           7.0 + n / 10.0, url="https://ria.ru/%s" % url_hash,
                           hour=hour, minute=n)

    def test_state_reports_sections_and_schedule(self):
        self.login()
        body = self.alerts()
        ids = [s["id"] for s in body["state"]["sections"]]
        self.assertIn(CFG["topic"], ids)
        self.assertGreaterEqual(body["state"]["each"], 1)
        self.assertIn("в ", body["state"]["next"])
        self.assertTrue(body["state"]["owner"])

    def test_mailing_says_how_many_news_and_when(self):
        self.digest(9, ["a1", "a2", "a3"])
        self.login()
        mail = self.alerts()["alerts"]
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail[0]["count"], 3)
        self.assertEqual(mail[0]["sections"], 1)
        self.assertRegex(mail[0]["time"], r"^\d{2}:\d{2}$")
        self.assertIn("августа", mail[0]["when"])

    def test_only_five_links_and_the_most_important_first(self):
        self.digest(9, ["b%d" % n for n in range(8)])
        self.login()
        links = self.alerts()["alerts"][0]["links"]
        self.assertEqual(len(links), newsfeed.MAILING_LINKS)
        scores = [link["score"] for link in links]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(links[0]["url"], "https://ria.ru/b7")
        self.assertEqual(links[0]["source"], "ria.ru")

    def test_two_digests_of_the_day_are_two_notifications(self):
        self.digest(9, ["m1", "m2"])
        self.digest(21, ["e1", "e2", "e3"])
        self.login()
        mail = self.alerts()["alerts"]
        self.assertEqual([m["count"] for m in mail], [3, 2])   # свежая сверху

    def test_news_of_one_digest_do_not_split(self):
        """Выпуск ложится в историю не мгновенно — минуты внутри него не в счёт."""
        self.digest(9, ["s%d" % n for n in range(6)])
        self.login()
        self.assertEqual(len(self.alerts()["alerts"]), 1)

    def test_last_marks_the_freshest_mailing(self):
        self.digest(9, ["l1"])
        self.login()
        body = self.alerts()
        self.assertEqual(body["last"], body["alerts"][0]["id"])

    def test_nothing_sent_yet_is_not_an_error(self):
        self.login()
        body = self.alerts()
        self.assertEqual(body["alerts"], [])
        self.assertEqual(body["last"], "")

    def test_other_chats_are_not_shown(self):
        self.delivered("z1", "Чужая новость", "ria", "politics", 7.0, chat="999")
        self.login()
        self.assertEqual(self.alerts()["alerts"], [])

    def test_link_of_a_foreign_scheme_is_dropped(self):
        """Ссылка приходит из чужого фида — javascript: наружу не пускаем."""
        self.delivered("j1", "Новость", "ria", "politics", 7.0,
                       url="javascript:alert(1)")
        self.login()
        link = self.alerts()["alerts"][0]["links"][0]
        self.assertEqual(link["url"], "")
        self.assertEqual(link["source"], "ria")

    def test_bot_messages_are_not_shown_anymore(self):
        """Переписка с ботом на странице больше не живёт — только рассылки."""
        conn = storage.db()
        storage.save_outbox(conn, OWNER, "Привет <b>мир</b>")
        conn.close()
        self.login()
        body = self.alerts()
        self.assertNotIn("messages", body)
        self.assertEqual(body["alerts"], [])


class TestNoCommands(WebCase):
    """Управление живёт на VPS: страница команд не показывает и не выполняет."""

    def test_command_endpoint_is_gone(self):
        self.login()
        code, _body = self.ask("/api/command", {"text": "/digest"})
        self.assertEqual(code, 404)

    def test_page_has_no_input_line(self):
        _code, page = self.ask("/")
        self.assertNotIn('id="ask"', page)
        self.assertNotIn("/api/command", page)

    def test_page_does_not_mention_commands(self):
        _code, page = self.ask("/")
        for command in ("/digest", "/more", "/status", "/set ", "/news "):
            self.assertNotIn(command, page)

    def test_settings_text_names_no_commands(self):
        """Описания настроек тоже читает человек — команд в них быть не должно."""
        self.login()
        for opt in self.ask("/api/tools")[1]["settings"]:
            self.assertNotRegex(opt["about"], r"/[a-z]",
                                "команда в описании настройки %s" % opt["name"])

    def test_page_has_no_collect_button(self):
        _code, page = self.ask("/")
        self.assertNotIn("Собрать выпуск", page)
        self.assertNotIn("Собрать новости", page)


class TestTools(WebCase):
    """Настройки: подписчики и значения настроек, всё только для чтения."""

    def tools(self):
        return self.ask("/api/tools")[1]

    def test_needs_password(self):
        code, body = self.ask("/api/tools")
        self.assertEqual(code, 401)
        self.assertIn("пароль", body["error"])

    def test_settings_are_listed_with_values(self):
        self.login()
        opts = {opt["name"]: opt for opt in self.tools()["settings"]}
        self.assertIn("breaking", opts)
        self.assertIn(opts["breaking"]["value"], ("вкл", "выкл"))
        self.assertTrue(opts["breaking"]["about"])
        self.assertEqual(opts["time"]["value"], CFG["send_at"])

    def test_personal_value_is_marked(self):
        conn = storage.db()
        try:
            subscribers.set_field(conn, OWNER, "send_at", "07:15")
        finally:
            conn.close()
        self.login()
        opts = {opt["name"]: opt for opt in self.tools()["settings"]}
        self.assertEqual(opts["time"]["value"], "07:15")
        self.assertTrue(opts["time"]["own"])

    def test_subscribers_are_listed(self):
        conn = storage.db()
        try:
            subscribers.add(conn, "777", role="member", title="Ваня")
            subscribers.add(conn, "999", role="pending")
        finally:
            conn.close()
        self.login()
        people = {man["chat"]: man for man in self.tools()["readers"]}
        self.assertEqual(people[OWNER]["role"], "owner")
        self.assertEqual(people["777"]["title"], "Ваня")
        self.assertEqual(people["999"]["role"], "pending")
        self.assertFalse(people["777"]["paused"])
        self.assertIn("умолчанию", people["777"]["own"])

    def test_personal_settings_of_a_reader_are_shown(self):
        conn = storage.db()
        try:
            subscribers.add(conn, "777", role="member", title="Ваня")
            subscribers.set_field(conn, "777", "send_at", "08:30")
        finally:
            conn.close()
        self.login()
        people = {man["chat"]: man for man in self.tools()["readers"]}
        self.assertIn("08:30", people["777"]["own"])

    def test_timezone_comes_along(self):
        self.login()
        self.assertTrue(self.tools()["tz"])


class TestButtons(WebCase):
    """Кнопки 👍/👎/🔖 под карточками — единственное, что страница меняет."""

    def press(self, data):
        return self.ask("/api/react", {"data": data})

    def test_thumbs_up_is_recorded(self):
        self.login()
        _code, body = self.press("fb:up:hash1")
        self.assertIn("👍", body["toast"])
        self.assertTrue(body["pressed"]["up"])
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
        self.assertFalse(body["pressed"]["up"])
        self.assertTrue(body["pressed"]["down"])

    def test_bookmark_toggles(self):
        self.login()
        _code, body = self.press("fb:save:hash3")
        self.assertTrue(body["pressed"]["save"])
        _code, body = self.press("fb:save:hash3")
        self.assertFalse(body["pressed"]["save"])

    def test_press_needs_password(self):
        code, _body = self.press("fb:up:hash5")
        self.assertEqual(code, 401)

    def test_garbage_press_is_ignored(self):
        self.login()
        code, body = self.press("что-то не то")
        self.assertEqual(code, 200)
        self.assertEqual(body["toast"], "")

    def test_signup_press_does_nothing(self):
        """Заявки одобряет владелец в чате: страница чужие подписки не трогает."""
        conn = storage.db()
        subscribers.add(conn, "777", role="pending")
        conn.close()
        self.login()
        _code, body = self.press("sub:ok:777")
        self.assertEqual(body["toast"], "")
        conn = storage.db()
        try:
            self.assertEqual(subscribers.get(conn, "777")["role"], "pending")
        finally:
            conn.close()


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

    def test_card_carries_the_text_of_the_news(self):
        """Превью — это заголовок И текст: по одному заголовку новость не понять."""
        self.login()
        card = [i for i in self.news()["items"] if i["hash"] == "h1"][0]
        self.assertEqual(card["title"], "В Иране заявили о проходе через пролив")
        self.assertEqual(card["summary"], "Тегеран ответил на заявление США.")

    def test_card_without_a_summary_takes_it_from_the_material(self):
        """У записи до 3.5 сути нет — текст берём из самого материала."""
        self.material("h4", "Apple представила новые MacBook",
                      "Ноутбуки получили процессор M6 и поступят в продажу "
                      "на следующей неделе.")
        self.login()
        card = [i for i in self.news()["items"] if i["hash"] == "h4"][0]
        self.assertIn("M6", card["summary"])

    def test_long_text_is_cut_by_the_word(self):
        self.delivered("h9", "Длинная новость", "ria", "incidents", 7.0,
                       summary="Подробности события. " * 40, minute=9)
        self.login()
        card = [i for i in self.news()["items"] if i["hash"] == "h9"][0]
        self.assertLessEqual(len(card["summary"]), newsfeed.LEAD + 1)
        self.assertTrue(card["summary"].endswith("…"))
        self.assertNotIn("Подроб…", card["summary"])

    def test_search_looks_into_the_material_text(self):
        """Найтись должно и то, что видно в карточке, а лежит в материалах."""
        self.material("h4", "Apple представила новые MacBook",
                      "Ноутбуки получили процессор M6.")
        self.login()
        found = self.news("?q=" + urllib.parse.quote("процессор"))["items"]
        self.assertEqual([i["hash"] for i in found], ["h4"])

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

    def test_filters_keep_several_sections_at_once(self):
        """Фильтры страницы: «только происшествия и экономика»."""
        self.login()
        body = self.news("?sections=incidents,economy")
        self.assertEqual(sorted(i["hash"] for i in body["items"]), ["h1", "h3"])
        self.assertEqual(body["sections"], ["incidents", "economy"])

    def test_filter_of_one_section_works_like_the_section_itself(self):
        self.login()
        items = self.news("?sections=incidents")["items"]
        self.assertEqual([i["hash"] for i in items], ["h1"])

    def test_filters_reach_rows_without_a_section(self):
        """У старых записей раздела нет — фильтр находит их по источнику."""
        self.login()
        topic = sections.by_source("theverge")
        items = self.news("?sections=" + topic + ",politics")["items"]
        self.assertEqual(sorted(i["hash"] for i in items), ["h2", "h4"])

    def test_filters_understand_human_names(self):
        """Плашки шлют идентификаторы, но руками можно набрать и по-русски."""
        self.login()
        found = self.news("?sections=" + urllib.parse.quote("политика"))["items"]
        self.assertEqual([i["hash"] for i in found], ["h2"])

    def test_unknown_names_drop_out_of_the_filter(self):
        self.login()
        body = self.news("?sections=politics,no-such-thing")
        self.assertEqual([i["hash"] for i in body["items"]], ["h2"])
        self.assertEqual(body["sections"], ["politics"])

    def test_empty_filter_shows_everything(self):
        self.login()
        body = self.news("?sections=")
        self.assertEqual(len(body["items"]), 4)
        self.assertEqual(body["sections"], [])

    def test_open_section_beats_the_filters(self):
        """Читатель зашёл в раздел — показываем раздел, а не набор плашек."""
        self.login()
        body = self.news("?section=politics&sections=incidents,economy")
        self.assertEqual([i["hash"] for i in body["items"]], ["h2"])
        self.assertEqual(body["section"], "politics")

    def test_filters_work_together_with_search(self):
        self.login()
        found = self.news("?sections=incidents,economy&q=" +
                          urllib.parse.quote("иран"))["items"]
        self.assertEqual(sorted(i["hash"] for i in found), ["h1", "h3"])
        narrow = self.news("?sections=economy&q=" +
                           urllib.parse.quote("иран"))["items"]
        self.assertEqual([i["hash"] for i in narrow], ["h3"])

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


class TestNewsLanguage(WebCase):
    """Лента на языке читателя — чем бы ни закончилась сборка выпуска.

    Модель подменена: проверяется не качество перевода, а то, что английская
    карточка вообще замечена, переведена один раз и осела в истории.
    """

    ENGLISH = "Boot Option Submitted Ahead Of Linux Kernel Release"
    LEAD = "A last minute pull request was submitted for the new boot option."
    #: чем «переводит» подменённая модель: номер говорит, какую строку она
    #: получила, а кириллицы в ответе достаточно, чтобы он сошёл за перевод
    RUSSIAN = "Русский текст строки номер %d"

    def setUp(self):
        WebCase.setUp(self)
        self.asked = []
        self._real_translate = translate.translate_texts
        translate.translate_texts = self.fake

    def tearDown(self):
        translate.translate_texts = self._real_translate
        WebCase.tearDown(self)

    def fake(self, texts, language):
        self.asked.append(list(texts))
        return ({i: self.RUSSIAN % i for i in range(len(texts))},
                {"in": 10, "out": 5})

    def english(self, url_hash="e1", headline=None):
        self.delivered(url_hash, self.ENGLISH, "phoronix", "hardware", 8.0,
                       summary=self.LEAD, minute=1, headline=headline)

    def items(self):
        return self.ask("/api/news")[1]["items"]

    def test_english_card_becomes_russian(self):
        self.english()
        self.login()
        card = self.items()[0]
        self.assertEqual(card["title"], self.RUSSIAN % 0)
        self.assertEqual(card["summary"], self.RUSSIAN % 1)

    def test_whole_page_costs_one_request(self):
        for n in range(5):
            self.delivered("e%d" % n, "%s %d" % (self.ENGLISH, n), "phoronix",
                           "hardware", 8.0, summary=self.LEAD, minute=n)
        self.login()
        self.items()
        self.assertEqual(len(self.asked), 1)

    def test_old_row_without_a_card_is_translated_too(self):
        """До 3.5 в истории лежал только заголовок из фида — и тот английский."""
        self.english(headline="")
        self.material("e1", self.ENGLISH, self.LEAD, "phoronix")
        self.login()
        card = self.items()[0]
        self.assertEqual(card["title"], self.RUSSIAN % 0)
        self.assertEqual(card["summary"], self.RUSSIAN % 1)

    def test_translation_settles_in_the_history(self):
        self.english()
        self.login()
        self.items()
        row = self.stored("e1")
        self.assertEqual(row["headline"], self.RUSSIAN % 0)
        self.assertEqual(row["summary"], self.RUSSIAN % 1)

    def test_second_visit_is_free(self):
        self.english()
        self.login()
        self.items()
        self.asked = []
        card = self.items()[0]
        self.assertEqual(self.asked, [])
        self.assertEqual(card["title"], self.RUSSIAN % 0)

    def test_search_finds_the_card_by_its_russian_words(self):
        """Перевод осел в истории — значит, и поиск теперь по-русски."""
        self.english()
        self.login()
        self.items()
        found = self.ask("/api/news?q=" + urllib.parse.quote("русская"))[1]["items"]
        self.assertEqual([i["hash"] for i in found], ["e1"])

    def test_russian_card_never_reaches_the_model(self):
        self.delivered("r1", "Ядро Linux получило новую опцию загрузки", "ria",
                       "hardware", 8.0, summary="Патч приняли перед релизом.",
                       minute=1)
        self.login()
        self.items()
        self.assertEqual(self.asked, [])

    def test_unreachable_model_leaves_the_card_readable(self):
        def broken(texts, language):
            self.asked.append(list(texts))
            raise LLMError("модель недоступна")

        translate.translate_texts = broken
        self.english()
        self.login()
        card = self.items()[0]
        self.assertEqual(card["title"], self.ENGLISH)      # лента всё же открылась
        self.assertEqual(card["summary"], self.LEAD)

    def test_unreachable_model_is_not_asked_on_every_scroll(self):
        """Иначе каждая прокрутка ленты ждёт таймаутов и повторов."""
        def broken(texts, language):
            self.asked.append(list(texts))
            raise LLMError("модель недоступна")

        translate.translate_texts = broken
        self.english()
        self.login()
        self.items()
        self.items()
        self.assertEqual(len(self.asked), 1)

    def test_line_the_model_refuses_is_not_paid_for_twice(self):
        """Строку вроде «Bump version to 4.7.2» модель вернёт как есть — и это
        не повод спрашивать её о том же на каждом показе ленты."""
        def partly(texts, language):
            self.asked.append(list(texts))
            return ({i: (self.RUSSIAN % i if i == 0 else text)
                     for i, text in enumerate(texts)}, {"in": 1, "out": 1})

        translate.translate_texts = partly
        self.english()
        self.login()
        self.items()
        self.assertEqual(self.items()[0]["summary"], self.LEAD)
        self.assertEqual(len(self.asked), 1)

    def test_switch_off_stops_asking_the_model(self):
        CFG["translate"] = False
        try:
            self.english()
            self.login()
            card = self.items()[0]
        finally:
            CFG["translate"] = True
        self.assertEqual(self.asked, [])
        self.assertEqual(card["title"], self.ENGLISH)

    def test_cached_translation_works_without_the_model(self):
        """Кэш перевода — не роскошь: он закрывает почти всю ленту даром."""
        conn = storage.db()
        try:
            translate.remember(conn, [(self.ENGLISH, "Опция загрузки в ядре")])
        finally:
            conn.close()
        CFG["translate"] = False
        try:
            self.english()
            self.login()
            card = self.items()[0]
        finally:
            CFG["translate"] = True
        self.assertEqual(card["title"], "Опция загрузки в ядре")

    def test_bookmarks_speak_russian_too(self):
        self.english()
        self.login()
        self.ask("/api/react", {"data": "fb:save:e1"})
        card = self.ask("/api/news?view=saved")[1]["items"][0]
        self.assertEqual(card["title"], self.RUSSIAN % 0)


class TestOutwardLinks(unittest.TestCase):
    """Адреса приходят из чужих фидов — на страницу пускаем только http(s)."""

    def test_http_links_pass(self):
        self.assertEqual(newsfeed.outward("https://e.com/x"), "https://e.com/x")
        self.assertEqual(newsfeed.outward("HTTP://e.com/x"), "HTTP://e.com/x")

    def test_javascript_is_dropped(self):
        self.assertEqual(newsfeed.outward("javascript:alert(1)"), "")

    def test_data_url_is_dropped(self):
        self.assertEqual(newsfeed.outward("data:text/html,<script>"), "")

    def test_empty_stays_empty(self):
        self.assertEqual(newsfeed.outward(""), "")
        self.assertEqual(newsfeed.outward(None), "")


if __name__ == "__main__":
    unittest.main()
