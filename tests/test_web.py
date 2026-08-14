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
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import bot, config, feedback, profiles, storage  # noqa: E402
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
            for table in ("outbox", "feedback", "saved", "subscribers", "leftover"):
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
        self.assertIn("Следующий выпуск", body["messages"][1]["html"])

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
