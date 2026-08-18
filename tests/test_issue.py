# -*- coding: utf-8 -*-
"""Выпуск экранами: оглавление, разделы и переходы между ними.

Сеть не трогается: Telegram подменён списками отправленного и правок.
"""
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import bot, config, feedback, issueview, render, storage  # noqa: E402
from newsdigest.config import CFG  # noqa: E402
from newsdigest.telegram import TG_LIMIT  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

CHAT = "77"


def cards(count, topic="ai", text="кратко", score=7.5):
    out = []
    for i in range(count):
        group = [item("https://%s.com/%d" % (topic, i), "Заголовок %s%d" % (topic, i),
                      "src-%s%d" % (topic, i))]
        out.append(({"headline": "Заголовок %s%d" % (topic, i), "what": text,
                     "why": "потому что"}, group, score - i * 0.1, "labs"))
    return out


def issue(*sizes, note="", text="кратко"):
    """Снимок выпуска из нескольких разделов — то, что ложится в базу."""
    names = ("ai", "medicine", "space", "science", "climate", "politics",
             "sports", "games")
    blocks = [(names[i], cards(size, names[i], text, 9.0 - i))
              for i, size in enumerate(sizes)]
    return issueview.snapshot(blocks, render.issue_info(blocks, 3775, note))


class TestSnapshot(unittest.TestCase):
    def test_keeps_everything_a_screen_needs(self):
        card = issue(2)["sections"][0]["cards"][0]
        for field in ("hash", "title", "what", "why", "url", "source", "score"):
            self.assertTrue(card[field], field)

    def test_survives_json(self):
        import json
        before = issue(2, 3)
        self.assertEqual(json.loads(json.dumps(before, ensure_ascii=False)), before)


class TestHub(unittest.TestCase):
    """Оглавление: минимум в шапке, главное за день и кнопки разделов."""

    def test_header_holds_only_the_issue_and_the_day(self):
        text, _shown = issueview.hub_text(issue(2, 2, 1))
        head = text.split("\n\n")[0]
        self.assertEqual(len(head.split("\n")), 2)
        self.assertIn("5 новостей", head)
        self.assertIn(render.day(), head)
        for gone in ("материалов за сутки", "раздела", "продолжение"):
            self.assertNotIn(gone, head)

    def test_shows_three_best_news_of_the_day(self):
        # порядок главного — по оценке, а не по порядку разделов
        blocks = [("ai", cards(2, "ai", score=7.0)),
                  ("medicine", cards(2, "medicine", score=9.0))]
        snapshot = issueview.snapshot(blocks, render.issue_info(blocks, 100))
        text, shown = issueview.hub_text(snapshot)
        self.assertEqual(shown, issueview.TOP_SHOWN)
        self.assertIn("ГЛАВНОЕ СЕГОДНЯ", text)
        self.assertIn("<b>1. Заголовок medicine0</b>", text)
        self.assertLess(text.index("Заголовок medicine1"),
                        text.index("Заголовок ai0"))
        self.assertNotIn("Заголовок ai1", text)     # четвёртая — уже под кнопкой

    def test_summary_is_cut_to_one_line(self):
        long_text = "Первое предложение сути. " + "И ещё много слов подряд. " * 8
        text, _shown = issueview.hub_text(issue(2, text=long_text))
        self.assertIn("Первое предложение сути.", text)
        self.assertNotIn("И ещё много слов подряд. И ещё", text)

    def test_sections_are_buttons_with_counts(self):
        snapshot = issue(5, 3, 4)
        _text, shown = issueview.hub_text(snapshot)
        keyboard = issueview.hub_keyboard(snapshot, 12, shown)
        labels = [row[0]["text"] for row in keyboard]
        self.assertIn("🤖 ИИ и технологии · 5", labels)
        data = [row[0]["callback_data"] for row in keyboard]
        self.assertIn("nav:12:sec:medicine", data)
        for row in keyboard:
            for button in row:
                self.assertLessEqual(len(button["callback_data"].encode()), 64)

    def test_rest_of_the_sections_hides_behind_one_button(self):
        snapshot = issue(*([1] * 8))
        text, shown = issueview.hub_screen(snapshot, 1)
        keyboard = issueview.hub_screen(snapshot, 1)[1]
        sections = [r for r in keyboard if r[0]["callback_data"].startswith("nav:1:sec:")]
        self.assertEqual(len(sections), issueview.SECTIONS_SHOWN)
        self.assertIn("Остальные 2 раздела", keyboard[-1][0]["text"])
        # «остальные» показывает все разделы и умеет свернуться обратно
        wide = issueview.hub_screen(snapshot, 1, issueview.SECS)[1]
        self.assertEqual(len([r for r in wide
                              if r[0]["callback_data"].startswith("nav:1:sec:")]), 8)
        self.assertEqual(wide[-1][0]["callback_data"], "nav:1:home")
        self.assertTrue(text)
        self.assertTrue(shown)

    def test_more_top_news_opens_the_rest(self):
        snapshot = issue(4, 4)
        keyboard = issueview.hub_screen(snapshot, 1)[1]
        self.assertEqual(keyboard[0][0]["callback_data"], "nav:1:top")
        self.assertIn("Ещё 4", keyboard[0][0]["text"])
        text, shown = issueview.hub_text(snapshot, issueview.TOP_MAX)
        self.assertEqual(shown, issueview.TOP_MAX)
        self.assertIn("<b>7. ", text)

    def test_empty_sections_are_named_on_the_sections_screen(self):
        snapshot = issue(2, 2, note="без новостей: Роботы")
        self.assertNotIn("без новостей", issueview.hub_screen(snapshot, 1)[0])
        self.assertIn("без новостей: Роботы",
                      issueview.hub_screen(snapshot, 1, issueview.SECS)[0])


class TestSection(unittest.TestCase):
    """Экран раздела: новости, реакции и дорога назад."""

    def test_shows_the_section_with_its_news(self):
        snapshot = issue(3, 2)
        text, keyboard = issueview.screen(snapshot, 7, issueview.SEC, "ai")
        self.assertIn("<b>ИИ и технологии</b>", text)
        self.assertIn("3 новости", text)
        self.assertIn("Заголовок ai2", text)
        self.assertNotIn("Заголовок medicine0", text)
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:7:home")
        self.assertIn("К разделам", keyboard[-1][0]["text"])

    def test_reaction_row_per_news(self):
        snapshot = issue(3, 2)
        _text, keyboard = issueview.screen(snapshot, 7, issueview.SEC, "ai")
        rows = [r for r in keyboard if r[0]["callback_data"].startswith("fb:")]
        self.assertEqual(len(rows), 3)
        self.assertEqual([b["text"] for b in rows[0]][1:], ["👎", "🔖"])
        self.assertTrue(rows[0][0]["text"].startswith("👍 Заголовок"))

    def test_past_votes_are_marked(self):
        snapshot = issue(2, 2)
        url_hash = snapshot["sections"][0]["cards"][0]["hash"]
        _text, keyboard = issueview.screen(snapshot, 7, issueview.SEC, "ai",
                                           {url_hash: feedback.UP}, {url_hash})
        self.assertTrue(keyboard[0][0]["text"].endswith(render.MARK))
        self.assertTrue(keyboard[0][2]["text"].endswith(render.MARK))
        self.assertFalse(keyboard[0][1]["text"].endswith(render.MARK))

    def test_long_section_hides_the_tail_behind_a_button(self):
        snapshot = issue(9, 2)
        text, keyboard = issueview.screen(snapshot, 7, issueview.SEC, "ai")
        self.assertEqual(text.count("🔗"), issueview.SECTION_SHOWN)
        more = [r for r in keyboard if r[0]["callback_data"] == "nav:7:all:ai"]
        self.assertEqual(len(more), 1)
        self.assertIn("Ещё 4", more[0][0]["text"])
        whole, _kb = issueview.screen(snapshot, 7, issueview.ALL, "ai")
        self.assertEqual(whole.count("🔗"), 9)

    def test_huge_section_still_fits_the_message(self):
        snapshot = issue(40, text="очень длинный текст " * 40)
        text, _keyboard = issueview.screen(snapshot, 7, issueview.ALL, "ai")
        self.assertLessEqual(len(text), TG_LIMIT)
        self.assertIn("Заголовок ai0", text)

    def test_single_section_issue_opens_straight_at_the_news(self):
        """Ответ /news листать нечем: оглавление из одного пункта — лишний шаг."""
        snapshot = issue(4)
        text, keyboard = issueview.screen(snapshot, 7)
        self.assertIn("<b>ИИ и технологии</b>", text)
        self.assertNotIn("ГЛАВНОЕ СЕГОДНЯ", text)
        self.assertFalse([r for r in keyboard
                          if r[0]["callback_data"] == "nav:7:home"])

    def test_unknown_section_falls_back_to_the_hub(self):
        text, _keyboard = issueview.screen(issue(2, 2), 7, issueview.SEC, "нет")
        self.assertIn("ГЛАВНОЕ СЕГОДНЯ", text)


class TestRoutes(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(issueview.parse(issueview.route(3, "sec", "ai")),
                         (3, "sec", "ai"))
        self.assertEqual(issueview.parse(issueview.route(3)), (3, "home", ""))

    def test_alien_data_is_not_ours(self):
        for data in ("fb:up:hash", "sub:ok:1", "nav:", "nav:x:home", ""):
            self.assertEqual(issueview.parse(data), (0, "", ""))


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.conn = storage.db()
        self.conn.execute("DELETE FROM issues")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_saved_issue_comes_back(self):
        ident = storage.save_issue(self.conn, CHAT, issue(2, 1))
        self.assertEqual(storage.load_issue(self.conn, CHAT, ident)["count"], 3)

    def test_issue_of_another_chat_is_not_given_away(self):
        ident = storage.save_issue(self.conn, CHAT, issue(1))
        self.assertEqual(storage.load_issue(self.conn, "999", ident), {})
        self.assertEqual(storage.load_issue(self.conn, CHAT, 0), {})

    def test_old_issues_are_dropped(self):
        idents = [storage.save_issue(self.conn, CHAT, issue(1))
                  for _ in range(storage.ISSUES_KEEP + 3)]
        self.assertEqual(storage.load_issue(self.conn, CHAT, idents[0]), {})
        self.assertTrue(storage.load_issue(self.conn, CHAT, idents[-1]))


class TestNavigation(unittest.TestCase):
    """Нажатие кнопки правит то же сообщение, а не присылает новое."""

    def setUp(self):
        self.answers, self.edits = [], []
        self._real = (bot.tg_answer_callback, bot.tg_edit_text, bot.tg_send)
        bot.tg_answer_callback = lambda cb_id, text="", alert=False: \
            self.answers.append(text)
        bot.tg_edit_text = lambda chat, mid, text, kb=None: \
            self.edits.append((chat, mid, text, kb))
        bot.tg_send = lambda *a, **kw: None
        self._owner = config.TG_CHAT
        config.TG_CHAT = CHAT
        conn = storage.db()
        for table in ("issues", "feedback", "saved", "subscribers"):
            conn.execute("DELETE FROM %s" % table)
        conn.commit()
        self.ident = storage.save_issue(conn, CHAT, issue(3, 2, 1))
        conn.close()

    def tearDown(self):
        bot.tg_answer_callback, bot.tg_edit_text, bot.tg_send = self._real
        config.TG_CHAT = self._owner

    def press(self, data, chat_id=CHAT):
        bot.handle_update({"update_id": 1, "callback_query": {
            "id": "cb", "data": data,
            "message": {"message_id": 9, "chat": {"id": chat_id}},
        }}, worker=None)

    def test_section_opens_in_place(self):
        self.press("nav:%d:sec:medicine" % self.ident)
        chat, message_id, text, keyboard = self.edits[-1]
        self.assertEqual((chat, message_id), (CHAT, 9))
        self.assertIn("<b>Медицина</b>", text)
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:%d:home" % self.ident)

    def test_back_returns_to_the_hub(self):
        self.press("nav:%d:sec:medicine" % self.ident)
        self.press("nav:%d:home" % self.ident)
        self.assertIn("ГЛАВНОЕ СЕГОДНЯ", self.edits[-1][2])

    def test_vote_inside_a_section_keeps_navigation(self):
        self.press("nav:%d:sec:ai" % self.ident)
        keyboard = self.edits[-1][3]
        vote = keyboard[0][0]["callback_data"]
        conn = storage.db()
        conn.execute("DELETE FROM items")
        conn.commit()
        conn.close()
        bot.handle_update({"update_id": 2, "callback_query": {
            "id": "cb", "data": vote,
            "message": {"message_id": 9, "chat": {"id": CHAT},
                        "reply_markup": {"inline_keyboard": keyboard}},
        }}, worker=None)
        conn = storage.db()
        verdicts, _saved = feedback.press_state(conn, CHAT)
        conn.close()
        self.assertEqual(len(verdicts), 1)
        # оценка отметилась, а кнопки перехода остались на месте
        self.press("nav:%d:sec:ai" % self.ident)
        keyboard = self.edits[-1][3]
        self.assertTrue(keyboard[0][0]["text"].endswith(render.MARK))
        self.assertEqual(keyboard[-1][0]["callback_data"], "nav:%d:home" % self.ident)

    def test_message_too_old_to_edit_says_so(self):
        """Telegram не даёт править сообщения старше двух суток."""
        def refuse(chat, mid, text, kb=None):
            raise RuntimeError("Telegram отклонил запрос: 400: message can't be edited")

        bot.tg_edit_text = refuse
        self.press("nav:%d:sec:ai" % self.ident)
        self.assertIn("слишком старый", self.answers[-1])

    def test_old_issue_says_so_instead_of_failing(self):
        self.press("nav:%d:sec:ai" % (self.ident + 500))
        self.assertEqual(self.edits, [])
        self.assertIn("старый", self.answers[-1])

    def test_stranger_gets_nothing(self):
        self.press("nav:%d:sec:ai" % self.ident, chat_id="999")
        self.assertEqual(self.edits, [])


class TestButtonsOff(unittest.TestCase):
    def test_no_reactions_but_navigation_stays(self):
        CFG["feedback_buttons"] = False
        try:
            _text, keyboard = issueview.screen(issue(3, 2), 1, issueview.SEC, "ai")
        finally:
            CFG["feedback_buttons"] = True
        self.assertEqual(len(keyboard), 1)
        self.assertIn("К разделам", keyboard[0][0]["text"])


if __name__ == "__main__":
    unittest.main()
