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

from newsdigest import bot, config, storage  # noqa: E402

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
        bot._REFUSED.clear()
        conn = storage.db()
        conn.execute("DELETE FROM leftover")
        conn.execute("DELETE FROM meta")
        conn.commit()
        conn.close()

    def tearDown(self):
        bot.tg_send = self._real_send
        config.TG_CHAT = self._real_owner

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

    def test_stranger_refused_once(self):
        self.message("/digest", chat_id="999")
        self.message("/status", chat_id="999")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("личный бот", self.sent.texts())

    def test_extra_chat_can_be_allowed(self):
        conn = storage.db()
        storage.meta_set(conn, "extra_chats", "999, 777")
        conn.close()
        self.assertTrue(bot.is_allowed("999"))
        self.assertTrue(bot.is_allowed("777"))


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
        conn = storage.db()
        self.assertTrue(bot.is_paused(conn))
        conn.close()
        self.message("/resume")
        conn = storage.db()
        self.assertFalse(bot.is_paused(conn))
        conn.close()
        self.assertIn("паузе", self.sent.texts())

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
        conn = storage.db()
        saved = config.CFG["send_at"]
        try:
            config.CFG["send_at"] = "00:01"       # уже прошло
            self.assertIn("завтра", bot.next_send_human(conn))
            config.CFG["send_at"] = "23:59"       # ещё будет
            self.assertIn("сегодня", bot.next_send_human(conn))
            config.CFG["send_at"] = "лунный полдень"
            self.assertIn("неверно", bot.next_send_human(conn))
        finally:
            config.CFG["send_at"] = saved
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
