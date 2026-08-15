# -*- coding: utf-8 -*-
"""Настройки из чата: разбор значений, проверки, запись в env-файл."""
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import bot, config, settings, storage, subscribers  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False


class SettingsCase(unittest.TestCase):
    def setUp(self):
        self.saved_cfg = dict(CFG)
        self.saved_env = dict(os.environ)
        if config.ENV_FILE.exists():
            config.ENV_FILE.unlink()

    def tearDown(self):
        CFG.clear()
        CFG.update(self.saved_cfg)
        os.environ.clear()
        os.environ.update(self.saved_env)


class TestParsers(unittest.TestCase):
    def test_bool_words(self):
        for word in ("вкл", "да", "on", "1", "TRUE"):
            self.assertTrue(settings.as_bool(word), word)
        for word in ("выкл", "нет", "off", "0", "False"):
            self.assertFalse(settings.as_bool(word), word)
        with self.assertRaises(settings.Invalid):
            settings.as_bool("может быть")

    def test_time(self):
        self.assertEqual(settings.as_time("9:5"), "09:05")
        for bad in ("25:00", "09-00", "утром", "09:70"):
            with self.assertRaises(settings.Invalid, msg=bad):
                settings.as_time(bad)

    def test_quiet_range(self):
        self.assertEqual(settings.as_quiet("23:00-8:00"), "23:00-08:00")
        self.assertEqual(settings.as_quiet("нет"), "")
        with self.assertRaises(settings.Invalid):
            settings.as_quiet("ночью")

    def test_numbers_are_bounded(self):
        self.assertEqual(settings.as_float(1, 10)("7,5"), 7.5)
        with self.assertRaises(settings.Invalid):
            settings.as_float(1, 10)("11")
        with self.assertRaises(settings.Invalid):
            settings.as_int(1, 20)("много")

    def test_unknown_topic_lists_available(self):
        with self.assertRaises(settings.Invalid) as caught:
            settings.as_topic("несуществующая")
        self.assertIn("ai", str(caught.exception))

    def test_unknown_timezone(self):
        with self.assertRaises(settings.Invalid):
            settings.as_tz("Средиземье/Шир")


class TestApply(SettingsCase):
    def test_writes_env_and_config(self):
        key, shown = settings.apply("time", "08:30")
        self.assertEqual((key, shown), ("time", "08:30"))
        self.assertEqual(CFG["send_at"], "08:30")
        self.assertIn("ND_SEND_AT=08:30", config.ENV_FILE.read_text(encoding="utf-8"))

    def test_survives_reload(self):
        settings.apply("score", "7.5")
        config.load_env()                      # как при перезапуске демона
        self.assertEqual(CFG["min_score"], 7.5)

    def test_aliases_work(self):
        settings.apply("порог", "6")
        self.assertEqual(CFG["min_score"], 6.0)
        settings.apply("send_at", "10:00")
        self.assertEqual(CFG["send_at"], "10:00")

    def test_bool_roundtrip(self):
        settings.apply("breaking", "выкл")
        self.assertFalse(CFG["breaking"])
        config.load_env()
        self.assertFalse(CFG["breaking"])
        settings.apply("breaking", "вкл")
        config.load_env()
        self.assertTrue(CFG["breaking"])

    def test_empty_quiet_hours_persist(self):
        settings.apply("quiet", "нет")
        config.load_env()
        self.assertEqual(CFG["breaking_quiet"], "")

    def test_min_max_consistency(self):
        CFG["min_items"], CFG["max_items"] = 5, 8
        with self.assertRaises(settings.Invalid) as caught:
            settings.apply("max", "3")
        self.assertIn("минимума", str(caught.exception))
        with self.assertRaises(settings.Invalid):
            settings.apply("min", "9")
        self.assertEqual((CFG["min_items"], CFG["max_items"]), (5, 8))

    def test_unknown_setting(self):
        with self.assertRaises(settings.Invalid):
            settings.apply("цвет", "синий")

    def test_bad_value_changes_nothing(self):
        before = CFG["send_at"]
        with self.assertRaises(settings.Invalid):
            settings.apply("time", "завтра")
        self.assertEqual(CFG["send_at"], before)
        self.assertFalse(config.ENV_FILE.exists())

    def test_feedback_style(self):
        self.assertEqual(settings.as_style(" Rows "), "rows")
        self.assertEqual(settings.as_style("свёрнуто"), "compact")
        with self.assertRaises(settings.Invalid):
            settings.as_style("наполовину")

    def test_overview_covers_every_setting(self):
        rows = settings.overview()
        self.assertEqual(len(rows), len(settings.SPEC))
        for name, value, describe in rows:
            self.assertTrue(value != "" or name == "quiet")
            self.assertTrue(describe)


class TestSetCommand(SettingsCase):
    """Тот же путь, но через сообщение в чате."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self._send = bot.tg_send
        bot.tg_send = lambda chat, text, keyboard=None, silent=None: \
            self.sent.append(text)
        self._owner = config.TG_CHAT
        config.TG_CHAT = "1"
        bot._REFUSED.clear()

    def tearDown(self):
        bot.tg_send = self._send
        config.TG_CHAT = self._owner
        super().tearDown()

    def say(self, text):
        bot.handle_update({"update_id": 1, "message": {
            "chat": {"id": "1"}, "from": {}, "text": text}}, worker=None)
        return self.sent[-1]

    def test_set_applies(self):
        self.assertIn("08:45", self.say("/set time 08:45"))
        self.assertEqual(CFG["send_at"], "08:45")

    def test_set_reports_next_issue(self):
        self.assertIn("Следующий выпуск", self.say("/set time 08:45"))

    def test_set_explains_bad_value(self):
        reply = self.say("/set score сто")
        self.assertIn("⚠️", reply)
        self.assertIn("от 1.0 до 10.0", reply)

    def test_set_without_value_shows_current(self):
        self.assertIn("сейчас", self.say("/set language"))

    def test_bare_set_lists_everything(self):
        reply = self.say("/set")
        for name in ("topic", "time", "score", "breaking"):
            self.assertIn(name, reply)

    def test_settings_lists_everything(self):
        reply = self.say("/settings")
        self.assertIn("/set", reply)
        self.assertIn("topic", reply)

    def test_topic_switch_mentions_sources(self):
        reply = self.say("/set topic crypto")
        self.assertEqual(CFG["topic"], "crypto")
        self.assertIn("Источников", reply)

    def test_multiword_value(self):
        self.say("/set language английский язык")
        self.assertEqual(CFG["language"], "английский язык")


class TestPersonalSettings(SettingsCase):
    """Владелец правит настройки для всех, подписчик — только свои."""

    OWNER, MEMBER = "1", "2"

    def setUp(self):
        super().setUp()
        self.conn = storage.db()
        self.conn.execute("DELETE FROM subscribers")
        self.conn.commit()
        self._owner = config.TG_CHAT
        config.TG_CHAT = self.OWNER
        subscribers.ensure_owner(self.conn)
        subscribers.add(self.conn, self.MEMBER, role="member")

    def tearDown(self):
        self.conn.close()
        config.TG_CHAT = self._owner
        super().tearDown()

    def member(self):
        return subscribers.get(self.conn, self.MEMBER)

    def test_owner_change_is_global(self):
        key, shown, scope = settings.apply_for(self.conn, self.OWNER, True,
                                               "time", "07:15")
        self.assertEqual((key, shown, scope), ("time", "07:15", "global"))
        self.assertEqual(CFG["send_at"], "07:15")

    def test_member_change_is_personal(self):
        _key, shown, scope = settings.apply_for(self.conn, self.MEMBER, False,
                                                "time", "21:00")
        self.assertEqual((shown, scope), ("21:00", "personal"))
        self.assertEqual(self.member()["send_at"], "21:00")
        self.assertNotEqual(CFG["send_at"], "21:00")   # общая не тронута

    def test_member_cannot_touch_global_only(self):
        with self.assertRaises(settings.Invalid) as caught:
            settings.apply_for(self.conn, self.MEMBER, False, "every", "2")
        self.assertIn("владелец", str(caught.exception))

    def test_personal_view_marks_own_values(self):
        settings.apply_for(self.conn, self.MEMBER, False, "topic", "crypto")
        view = settings.personal_view(self.member())
        self.assertEqual(view, {"topic": "crypto"})
        self.assertEqual(settings.personal_view(subscribers.get(
            self.conn, self.OWNER)), {})

    def test_personal_silent_roundtrip(self):
        settings.apply_for(self.conn, self.MEMBER, False, "silent", "вкл")
        self.assertEqual(self.member()["silent"], 1)
        self.assertEqual(subscribers.overrides(self.member()), {"silent": True})

    def test_bad_personal_value_changes_nothing(self):
        with self.assertRaises(settings.Invalid):
            settings.apply_for(self.conn, self.MEMBER, False, "topic", "ерунда")
        self.assertEqual(self.member()["topic"], "")

    def test_global_change_during_overlay_is_not_lost(self):
        """Правка настроек во время чужого выпуска не должна откатиться."""
        settings.apply_for(self.conn, self.MEMBER, False, "max", "6")
        sub = self.member()
        with subscribers.overlay(sub):
            self.assertEqual(CFG["max_items"], 6)
            settings.apply_for(self.conn, self.OWNER, True, "max", "12")
        self.assertEqual(CFG["max_items"], 12)

    def test_member_max_below_global_minimum_is_rejected(self):
        CFG["min_items"] = 5
        with self.assertRaises(settings.Invalid):
            settings.apply_for(self.conn, self.MEMBER, False, "max", "3")


if __name__ == "__main__":
    unittest.main()
