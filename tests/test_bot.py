# -*- coding: utf-8 -*-
"""Тесты диалогового слоя: разбор команд, доступ, фоновая очередь.

Сеть не трогается: tg_send подменяется на список отправленных сообщений.
"""
import logging
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import bot, config, storage, subscribers  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False


class Sent(list):
    """Подмена tg_send: собирает (chat_id, text) вместо похода в Telegram."""

    def __call__(self, chat_id, text, keyboard=None, silent=None):
        self.append((str(chat_id), text))
        return {"message_id": len(self)}

    def texts(self):
        return "\n".join(t for _c, t in self)


class BotCase(unittest.TestCase):
    OWNER = "100500"

    def setUp(self):
        self.sent = Sent()
        self._real_send = bot.tg_send
        bot.tg_send = self.sent
        self._real_owner = config.TG_CHAT
        config.TG_CHAT = self.OWNER
        self._signup = CFG["signup"]
        bot._REFUSED.clear()
        conn = storage.db()
        try:
            for table in ("leftover", "meta", "subscribers", "sent"):
                conn.execute("DELETE FROM %s" % table)
            conn.commit()
            subscribers.ensure_owner(conn)
        finally:
            conn.close()

    def tearDown(self):
        bot.tg_send = self._real_send
        config.TG_CHAT = self._real_owner
        CFG["signup"] = self._signup

    def sub(self, chat_id=None):
        conn = storage.db()
        try:
            return subscribers.get(conn, chat_id or self.OWNER)
        finally:
            conn.close()

    def message(self, text, chat_id=None):
        bot.handle_update({"update_id": 1, "message": {
            "chat": {"id": chat_id or self.OWNER, "type": "private"},
            "from": {"username": "tester"},
            "text": text}}, worker=None)


class TestParse(unittest.TestCase):
    def test_plain_command(self):
        self.assertEqual(bot.parse_command("/status"), ("status", []))

    def test_with_bot_name_and_args(self):
        self.assertEqual(bot.parse_command("/more@my_digest_bot 7"), ("more", ["7"]))

    def test_not_a_command(self):
        self.assertEqual(bot.parse_command("привет"), (None, []))
        self.assertEqual(bot.parse_command(""), (None, []))


class TestAccess(BotCase):
    def test_owner_is_allowed(self):
        self.assertTrue(bot.is_allowed(self.OWNER))
        self.assertFalse(bot.is_allowed("999"))

    def test_stranger_gets_pending_and_owner_is_asked(self):
        CFG["signup"] = "ask"
        self.message("/start", chat_id="999")
        self.assertIn("Заявка отправлена", self.sent.texts())
        self.assertIn("просится на дайджест", self.sent.texts())
        self.assertEqual(self.sub("999")["role"], "pending")
        self.assertFalse(bot.is_allowed("999"))

    def test_pending_chat_is_not_spammed(self):
        CFG["signup"] = "ask"
        self.message("/start", chat_id="999")
        before = len(self.sent)
        self.message("/status", chat_id="999")
        self.message("/digest", chat_id="999")
        self.assertEqual(len(self.sent), before)

    def test_signup_off_refuses_once(self):
        CFG["signup"] = "off"
        self.message("/digest", chat_id="999")
        self.message("/status", chat_id="999")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("личный бот", self.sent.texts())

    def test_signup_open_subscribes_immediately(self):
        CFG["signup"] = "open"
        self.message("/start", chat_id="999")
        self.assertTrue(bot.is_allowed("999"))
        self.assertIn("Подписал", self.sent.texts())

    def test_owner_approves_by_button(self):
        CFG["signup"] = "ask"
        self.message("/start", chat_id="999")
        bot.tg_answer_callback = lambda *a, **kw: None
        bot.tg_edit_markup = lambda *a, **kw: None
        bot.handle_update({"update_id": 3, "callback_query": {
            "id": "c", "data": "sub:ok:999",
            "message": {"message_id": 1, "chat": {"id": self.OWNER}}}}, worker=None)
        self.assertTrue(bot.is_allowed("999"))
        self.assertEqual(self.sub("999")["role"], "member")

    def test_member_cannot_approve(self):
        CFG["signup"] = "ask"
        self.message("/start", chat_id="999")
        answers = []
        bot.tg_answer_callback = lambda cb, text="", alert=False: answers.append(text)
        bot.handle_update({"update_id": 4, "callback_query": {
            "id": "c", "data": "sub:ok:999",
            "message": {"message_id": 1, "chat": {"id": "999"}}}}, worker=None)
        self.assertIn("владелец", answers[0])
        self.assertFalse(bot.is_allowed("999"))

    def test_legacy_extra_chats_become_subscribers(self):
        conn = storage.db()
        try:
            conn.execute("DELETE FROM subscribers")
            storage.meta_set(conn, "extra_chats", "999, 777")
            subscribers.ensure_owner(conn)
        finally:
            conn.close()
        self.assertTrue(bot.is_allowed("999"))
        self.assertTrue(bot.is_allowed("777"))

    def test_owner_only_command_is_blocked_for_members(self):
        conn = storage.db()
        subscribers.add(conn, "888", role="member")
        conn.close()
        self.message("/feed list", chat_id="888")
        self.assertIn("только владельцу", self.sent.texts())


class TestCommands(BotCase):
    def test_help_lists_commands(self):
        self.message("/help")
        text = self.sent.texts()
        for name in ("/digest", "/more", "/status", "/pause"):
            self.assertIn(name, text)

    def test_start_is_alias_of_help(self):
        self.message("/start")
        self.assertIn("/digest", self.sent.texts())

    def test_unknown_command(self):
        self.message("/nosuchthing")
        self.assertIn("Не знаю команду", self.sent.texts())

    def test_non_command_ignored(self):
        self.message("просто текст")
        self.assertEqual(self.sent, [])

    def test_pause_and_resume(self):
        self.message("/pause")
        self.assertTrue(self.sub()["paused"])
        self.message("/resume")
        self.assertFalse(self.sub()["paused"])
        self.assertIn("паузе", self.sent.texts())

    def test_pause_is_personal(self):
        conn = storage.db()
        subscribers.add(conn, "888", role="member")
        conn.close()
        self.message("/pause", chat_id="888")
        self.assertTrue(self.sub("888")["paused"])
        self.assertFalse(self.sub()["paused"])

    def test_member_can_unsubscribe_owner_cannot(self):
        conn = storage.db()
        subscribers.add(conn, "888", role="member")
        conn.close()
        self.message("/stop", chat_id="888")
        self.assertFalse(bot.is_allowed("888"))
        self.message("/stop")
        self.assertIn("владелец", self.sent.texts())
        self.assertTrue(bot.is_allowed(self.OWNER))

    def test_more_without_stock(self):
        self.message("/more")
        self.assertIn("Запас пуст", self.sent.texts())

    def test_more_returns_and_consumes_stock(self):
        conn = storage.db()
        storage.save_leftover(conn, self.OWNER, [
            {"url_hash": "h%d" % i, "title": "Новость %d" % i,
             "url": "https://e.com/%d" % i, "source_id": "src",
             "category": "media", "score": 9 - i} for i in range(4)])
        conn.close()
        self.message("/more 2")
        self.assertIn("Новость 0", self.sent.texts())
        self.assertIn("Новость 1", self.sent.texts())
        self.assertNotIn("Новость 2", self.sent.texts())
        self.message("/more 2")            # второй заход отдаёт следующие
        self.assertIn("Новость 2", self.sent.texts())

    def test_status_reports_schedule(self):
        self.message("/status")
        self.assertIn("Следующий выпуск", self.sent.texts())

    def test_settings_shows_topic(self):
        self.message("/settings")
        self.assertIn(config.CFG["topic"], self.sent.texts())


class TestSchedule(BotCase):
    def test_next_send_switches_to_tomorrow(self):
        saved = CFG["send_at"]
        sub = self.sub()
        try:
            CFG["send_at"] = "00:01"       # уже прошло
            self.assertIn("завтра", subscribers.next_send_human(sub))
            CFG["send_at"] = "23:59"       # ещё будет
            self.assertIn("сегодня", subscribers.next_send_human(sub))
            CFG["send_at"] = "лунный полдень"
            self.assertIn("09:00", subscribers.next_send_human(sub))
        finally:
            CFG["send_at"] = saved

    def test_personal_time_wins_over_global(self):
        conn = storage.db()
        try:
            subscribers.add(conn, "888", role="member")
            subscribers.set_field(conn, "888", "send_at", "23:59")
            sub = subscribers.get(conn, "888")
        finally:
            conn.close()
        self.assertEqual(subscribers.send_at_for(sub), (23, 59))
        self.assertIn("23:59", subscribers.next_send_human(sub))

    def test_due_respects_pause_and_last_digest(self):
        conn = storage.db()
        try:
            subscribers.set_field(conn, self.OWNER, "send_at", "00:01")
            self.assertIn(self.OWNER, [s["chat_id"] for s in subscribers.due(conn)])

            subscribers.set_field(conn, self.OWNER, "paused", 1)
            self.assertEqual(subscribers.due(conn), [])

            subscribers.set_field(conn, self.OWNER, "paused", 0)
            today = subscribers.now_for(subscribers.get(conn, self.OWNER))
            subscribers.set_last_digest(conn, self.OWNER, today.strftime("%Y-%m-%d"))
            self.assertEqual(subscribers.due(conn), [])
        finally:
            conn.close()


class TestWorker(unittest.TestCase):
    def test_runs_and_deduplicates(self):
        worker = bot.Worker().start()
        started, release = threading.Event(), threading.Event()

        def slow():
            started.set()
            release.wait(5)

        self.assertTrue(worker.submit("digest", slow))
        started.wait(5)
        self.assertEqual(worker.busy(), "digest")
        self.assertFalse(worker.submit("digest", slow))   # уже выполняется
        self.assertTrue(worker.submit("collect", lambda: None))
        release.set()
        worker.queue.join()
        self.assertEqual(worker.busy(), "")

    def test_exception_does_not_kill_thread(self):
        worker = bot.Worker().start()
        done = threading.Event()

        def boom():
            raise RuntimeError("тест")

        worker.submit("boom", boom)
        worker.submit("after", done.set)
        self.assertTrue(done.wait(5))


if __name__ == "__main__":
    unittest.main()
