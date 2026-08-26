# -*- coding: utf-8 -*-
"""Тесты диалогового слоя: разбор команд, доступ, расписание, фоновая очередь.

Команд в Telegram нет, и страница их больше не выполняет, поэтому здесь
обработчики зовутся напрямую через HANDLERS. Сеть не трогается: tg_send
подменяется на список отправленных сообщений.
"""
import logging
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import bot, config, prefsview, sections  # noqa: E402
from newsdigest import storage, subscribers, translate  # noqa: E402
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
        self._reply = CFG["chat_reply"]
        bot._REFUSED.clear()
        bot._ANSWERED.clear()
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
        CFG["chat_reply"] = self._reply

    def sub(self, chat_id=None):
        conn = storage.db()
        try:
            return subscribers.get(conn, chat_id or self.OWNER)
        finally:
            conn.close()

    def command(self, text, chat_id=None, worker=None):
        """Обработчик команды напрямую — без транспорта и разбора апдейта."""
        name, args = bot.parse_command(text)
        cmd = bot.HANDLERS.get(name)
        self.assertIsNotNone(cmd, "нет такой команды: %s" % text)
        conn = storage.db()
        try:
            return cmd.fn(bot.Ctx(chat_id or self.OWNER, args, conn, worker)) or ""
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

    def test_command_from_member_is_not_executed(self):
        conn = storage.db()
        subscribers.add(conn, "888", role="member")
        conn.close()
        self.message("/feed list", chat_id="888")
        self.assertNotIn("источников раздела", self.sent.texts())
        self.assertIn("только присылаю выпуски", self.sent.texts())


class TestTelegramIsSendOnly(BotCase):
    """В чате бот только рассылает: на любую команду — одна и та же справка."""

    def test_command_answers_with_the_schedule_and_changes_nothing(self):
        self.message("/pause")
        self.assertFalse(self.sub()["paused"])     # команда не выполнена
        text = self.sent.texts()
        self.assertIn("только присылаю выпуски", text)
        self.assertIn("Рассылка:", text)
        self.assertIn("Следующий выпуск:", text)
        self.assertIn("через", text)

    def test_every_command_gets_the_same_answer(self):
        self.message("/pause")
        bot._ANSWERED.clear()                      # как будто прошла минута
        self.message("/status")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[0][1], self.sent[1][1])

    def test_burst_of_commands_is_answered_once(self):
        self.message("/status")
        before = len(self.sent)
        self.message("/status")
        self.message("/digest")
        self.assertEqual(len(self.sent), before)   # автоответчиком не работаем

    def test_chat_reply_off_keeps_full_silence(self):
        CFG["chat_reply"] = "off"
        self.message("/status")
        self.message("привет")
        self.assertEqual(self.sent, [])

    def test_non_command_ignored(self):
        self.message("просто текст")
        self.assertEqual(self.sent, [])

    def test_buttons_still_work(self):
        answers = []
        bot.tg_answer_callback = lambda cb, text="", alert=False: answers.append(text)
        bot.tg_edit_markup = lambda *a, **kw: None
        bot.handle_update({"update_id": 5, "callback_query": {
            "id": "c", "data": "fb:up:hash1",
            "message": {"message_id": 1, "chat": {"id": self.OWNER}}}})
        self.assertIn("👍", answers[0])


class TestMyTopics(BotCase):
    """Экран «Мои темы»: кнопками, потому что команд в чате нет."""

    MEMBER = "77"

    def setUp(self):
        super().setUp()
        self.saved_top = CFG["favorites"]
        CFG["favorites"] = ""
        conn = storage.db()
        try:
            subscribers.add(conn, self.MEMBER, role="member")
        finally:
            conn.close()
        self.answers = []
        self.edits = []
        self._real_answer = bot.tg_answer_callback
        self._real_edit = bot.tg_edit_text
        bot.tg_answer_callback = \
            lambda cb, text="", alert=False: self.answers.append(text)
        bot.tg_edit_text = lambda chat, mid, text, keyboard=None: \
            self.edits.append((text, keyboard or []))
        self.addCleanup(self.restore)

    def restore(self):
        bot.tg_answer_callback = self._real_answer
        bot.tg_edit_text = self._real_edit
        CFG["favorites"] = self.saved_top

    def press(self, data, chat_id=None):
        bot.handle_update({"update_id": 1, "callback_query": {
            "id": "c", "data": data,
            "message": {"message_id": 1,
                        "chat": {"id": chat_id or self.MEMBER}}}})
        return self.edits[-1] if self.edits else ("", [])

    def top(self, chat_id=None):
        return sections.favorites(self.sub(chat_id or self.MEMBER))

    def test_screen_lists_every_section_and_fits_the_callback_limit(self):
        _text, keyboard = self.press(prefsview.route(prefsview.OPEN, "", 12))
        data = [row[0]["callback_data"] for row in keyboard]
        self.assertIn("pref:fav:medicine:12", data)
        self.assertIn("nav:12:home", data)      # возврат в выпуск
        for row in keyboard:
            for button in row:
                self.assertLessEqual(len(button["callback_data"].encode()), 64)

    def test_marking_and_unmarking_a_section(self):
        text, _kb = self.press("pref:fav:sports:0")
        self.assertEqual(self.top(), ["sports"])
        self.assertIn("№1", self.answers[-1])
        self.assertIn("1. ", text)
        self.press("pref:fav:space:0")
        self.assertEqual(self.top(), ["sports", "space"])
        # повторное нажатие снимает отметку, соседи не разъезжаются
        self.press("pref:fav:sports:0")
        self.assertEqual(self.top(), ["space"])
        self.assertIn("Убрал", self.answers[-1])

    def test_marked_sections_lead_the_digest(self):
        self.press("pref:fav:sports:0")
        self.assertEqual(sections.plan(self.sub(self.MEMBER))[0], "sports")

    def test_sixth_section_is_refused_out_loud(self):
        for name in ("sports", "space", "cinema", "games", "medicine"):
            self.press("pref:fav:%s:0" % name)
        self.press("pref:fav:politics:0")
        self.assertEqual(len(self.top()), sections.MAX_FAVORITES)
        self.assertNotIn("politics", self.top())
        self.assertIn("Уже %d" % sections.MAX_FAVORITES, self.answers[-1])

    def test_reset_returns_the_usual_order(self):
        self.press("pref:fav:sports:0")
        text, keyboard = self.press("pref:clear::0")
        self.assertEqual(self.top(), [])
        self.assertIn("обычным порядком", text)
        self.assertNotIn("♻️ Сбросить", [row[0]["text"] for row in keyboard])

    def test_member_top_does_not_touch_anyone_else(self):
        self.press("pref:fav:sports:0")
        self.assertEqual(CFG["favorites"], "")
        self.assertEqual(self.top(self.OWNER), [])

    def test_owner_sets_the_common_order(self):
        self.press("pref:fav:cinema:0", chat_id=self.OWNER)
        self.assertEqual(CFG["favorites"], "cinema")
        self.assertEqual(self.top(self.MEMBER), ["cinema"])   # общий по умолчанию

    def test_stranger_cannot_open_it(self):
        self.press("pref:open::0", chat_id="999")
        self.assertEqual(self.edits, [])
        self.assertIn("личный бот", self.answers[-1])

    def test_schedule_answer_carries_the_button(self):
        keys = []
        bot.tg_send = lambda chat, text, keyboard=None, silent=None: \
            keys.append(keyboard)
        bot.answer_schedule(self.MEMBER)
        self.assertEqual(keys[0][0][0]["callback_data"], "pref:open::0")


class TestCommands(BotCase):
    """Обработчики команд: интерфейса у них нет, а разбор и доступ живут."""

    def test_help_lists_commands(self):
        text = self.command("/help")
        for name in ("/digest", "/more", "/status", "/pause"):
            self.assertIn(name, text)
        self.assertIn("на странице", text)

    def test_start_is_alias_of_help(self):
        self.assertIn("/digest", self.command("/start"))

    def test_pause_and_resume(self):
        self.command("/pause")
        self.assertTrue(self.sub()["paused"])
        text = self.command("/resume")
        self.assertFalse(self.sub()["paused"])
        self.assertIn("включена", text)

    def test_pause_is_personal(self):
        conn = storage.db()
        subscribers.add(conn, "888", role="member")
        conn.close()
        self.command("/pause", chat_id="888")
        self.assertTrue(self.sub("888")["paused"])
        self.assertFalse(self.sub()["paused"])

    def test_member_can_unsubscribe_owner_cannot(self):
        conn = storage.db()
        subscribers.add(conn, "888", role="member")
        conn.close()
        self.command("/stop", chat_id="888")
        self.assertFalse(bot.is_allowed("888"))
        self.assertIn("владелец", self.command("/stop"))
        self.assertTrue(bot.is_allowed(self.OWNER))

    def test_more_without_stock(self):
        self.assertIn("Запас пуст", self.command("/more"))

    def test_more_returns_and_consumes_stock(self):
        conn = storage.db()
        storage.save_leftover(conn, self.OWNER, [
            {"url_hash": "h%d" % i, "title": "Новость %d" % i,
             "url": "https://e.com/%d" % i, "source_id": "src",
             "category": "media", "score": 9 - i} for i in range(4)])
        conn.close()
        text = self.command("/more 2")
        self.assertIn("Новость 0", text)
        self.assertIn("Новость 1", text)
        self.assertNotIn("Новость 2", text)
        self.assertIn("Новость 2", self.command("/more 2"))   # следующие

    def test_more_shows_the_russian_title(self):
        """Запас, собранный до перевода, тоже показываем по-русски — из кэша."""
        english = "Chrome Enables Cache Partitioning For All Users"
        russian = "Chrome включил партиционирование кэша"
        conn = storage.db()
        try:
            storage.save_leftover(conn, self.OWNER, [
                {"url_hash": "h9", "title": english, "url": "https://e.com/9",
                 "source_id": "src", "category": "media", "score": 9.0}])
            translate.remember(conn, [(english, russian)])
        finally:
            conn.close()
        text = self.command("/more")
        self.assertIn(russian, text)
        self.assertNotIn(english, text)

    def test_status_reports_schedule(self):
        text = self.command("/status")
        self.assertIn("Выпуски:", text)
        self.assertIn("следующий", text)

    def test_settings_shows_topic(self):
        self.assertIn(config.CFG["topic"], self.command("/settings"))


class TestSchedule(BotCase):
    """Расписание: выпуск в назначенное время и, если попросили, ещё раз."""

    def setUp(self):
        super().setUp()
        self.saved = {k: CFG[k] for k in ("send_at", "per_day")}

    def tearDown(self):
        CFG.update(self.saved)
        super().tearDown()

    def test_twice_a_day_is_send_at_plus_twelve_hours(self):
        CFG["send_at"], CFG["per_day"] = "09:00", 2
        self.assertEqual(subscribers.slots_for(None), [9 * 60, 21 * 60])
        self.assertEqual(subscribers.schedule_human(None), "09:00 и 21:00")

        CFG["send_at"] = "21:00"           # тот же набор с другого конца
        self.assertEqual(subscribers.slots_for(None), [9 * 60, 21 * 60])

        CFG["per_day"] = 1
        self.assertEqual(subscribers.slots_for(None), [21 * 60])
        self.assertEqual(subscribers.schedule_human(None), "21:00")

    def test_slot_number_grows_through_the_day(self):
        CFG["send_at"], CFG["per_day"] = "09:00", 2
        day = "2026-08-15"
        for hour, expected in ((8, 0), (9, 1), (20, 1), (21, 2), (23, 2)):
            now = datetime(2026, 8, 15, hour, 0)
            self.assertEqual(subscribers.slot_index(None, now), expected, hour)
            if expected:
                self.assertEqual(subscribers.slot_mark(None, now),
                                 "%s#%d" % (day, expected))

    def test_next_send_walks_to_the_nearest_slot(self):
        CFG["send_at"], CFG["per_day"] = "09:00", 2
        at = lambda hour: subscribers.next_send_human(  # noqa: E731
            None, datetime(2026, 8, 15, hour, 0))
        self.assertIn("сегодня в 09:00", at(8))
        self.assertIn("сегодня в 21:00", at(10))
        self.assertIn("завтра в 09:00", at(22))

        CFG["per_day"] = 1
        self.assertIn("завтра в 09:00", at(10))

    def test_next_send_gives_the_moment_and_the_wait(self):
        CFG["send_at"], CFG["per_day"] = "09:00", 2
        day = datetime(2026, 8, 15, 10, 20)
        self.assertEqual(subscribers.next_send_at(None, day),
                         datetime(2026, 8, 15, 21, 0))
        self.assertEqual(subscribers.left_human(None, day), "10 ч 40 мин")

        night = datetime(2026, 8, 15, 22, 30)     # ближайший — уже завтрашний
        self.assertEqual(subscribers.next_send_at(None, night),
                         datetime(2026, 8, 16, 9, 0))
        self.assertEqual(subscribers.left_human(None, night), "10 ч 30 мин")

        self.assertEqual(subscribers.left_human(
            None, datetime(2026, 8, 15, 20, 45)), "15 мин")
        self.assertEqual(subscribers.left_human(
            None, datetime(2026, 8, 15, 20, 0)), "1 ч")

    def test_broken_time_falls_back_to_nine(self):
        CFG["send_at"], CFG["per_day"] = "лунный полдень", 1
        self.assertIn("09:00", subscribers.next_send_human(None))

    def test_personal_time_wins_over_global(self):
        conn = storage.db()
        try:
            subscribers.add(conn, "888", role="member")
            subscribers.set_field(conn, "888", "send_at", "23:59")
            sub = subscribers.get(conn, "888")
        finally:
            conn.close()
        CFG["per_day"] = 1
        self.assertEqual(subscribers.send_at_for(sub), (23, 59))
        self.assertIn("23:59", subscribers.next_send_human(sub))

    def test_due_respects_pause_and_the_mark(self):
        CFG["send_at"], CFG["per_day"] = "00:00", 1
        conn = storage.db()
        try:
            self.assertIn(self.OWNER, [s["chat_id"] for s in subscribers.due(conn)])

            subscribers.set_field(conn, self.OWNER, "paused", 1)
            self.assertEqual(subscribers.due(conn), [])

            subscribers.set_field(conn, self.OWNER, "paused", 0)
            sub = subscribers.get(conn, self.OWNER)
            subscribers.set_last_digest(conn, self.OWNER, subscribers.slot_mark(sub))
            self.assertEqual(subscribers.due(conn), [])
        finally:
            conn.close()

    def test_second_slot_is_not_closed_by_the_first(self):
        # 00:00 и 12:00 — в какое бы время ни шли тесты, один слот уже позади
        CFG["send_at"], CFG["per_day"] = "00:00", 2
        conn = storage.db()
        try:
            sub = subscribers.get(conn, self.OWNER)
            now = subscribers.now_for(sub)
            here = subscribers.slot_index(sub, now)
            subscribers.set_last_digest(conn, self.OWNER,
                                        subscribers.slot_mark(sub, now))
            self.assertEqual(subscribers.due(conn), [])

            neighbour = "%s#%d" % (now.strftime("%Y-%m-%d"), 3 - here)
            subscribers.set_last_digest(conn, self.OWNER, neighbour)
            self.assertIn(self.OWNER, [s["chat_id"] for s in subscribers.due(conn)])
        finally:
            conn.close()

    def test_old_date_mark_is_upgraded_not_repeated(self):
        """Метка из прошлой версии — просто дата — закрывает день целиком."""
        CFG["send_at"], CFG["per_day"] = "00:00", 2
        conn = storage.db()
        try:
            today = subscribers.now_for(subscribers.get(conn, self.OWNER))
            conn.execute("UPDATE subscribers SET last_digest=? WHERE chat_id=?",
                         (today.strftime("%Y-%m-%d"), self.OWNER))
            conn.commit()
        finally:
            conn.close()
        conn = storage.db()                # миграция идёт при открытии базы
        try:
            self.assertEqual(subscribers.get(conn, self.OWNER)["last_digest"],
                             "%s#2" % today.strftime("%Y-%m-%d"))
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
