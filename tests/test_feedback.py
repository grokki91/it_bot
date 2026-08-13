# -*- coding: utf-8 -*-
"""Реакции: запись, влияние на прескоринг, подсказка модели, нажатие кнопки."""
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import bot, config, feedback, rank, storage  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

CHAT = "42"


class FeedbackCase(unittest.TestCase):
    def setUp(self):
        self.conn = storage.db()
        for table in ("feedback", "saved", "items", "sent"):
            self.conn.execute("DELETE FROM %s" % table)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def vote(self, url_hash, verdict, source="src", category="media", title="Т"):
        feedback.record(self.conn, CHAT, url_hash, verdict,
                        {"source_id": source, "category": category, "title": title})


class TestRecording(FeedbackCase):
    def test_vote_can_be_changed(self):
        self.vote("h1", feedback.UP)
        self.vote("h1", feedback.DOWN)
        rows = list(self.conn.execute("SELECT verdict FROM feedback"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], feedback.DOWN)

    def test_bookmark_toggles(self):
        self.assertTrue(feedback.save_bookmark(self.conn, CHAT, "h1", {"title": "A"}))
        self.assertEqual(len(feedback.bookmarks(self.conn, CHAT)), 1)
        self.assertFalse(feedback.save_bookmark(self.conn, CHAT, "h1", {"title": "A"}))
        self.assertEqual(feedback.bookmarks(self.conn, CHAT), [])

    def test_bookmarks_are_per_chat(self):
        feedback.save_bookmark(self.conn, CHAT, "h1", {"title": "A"})
        self.assertEqual(feedback.bookmarks(self.conn, "999"), [])


class TestAffinity(FeedbackCase):
    def test_liked_source_scores_higher_than_disliked(self):
        for i in range(4):
            self.vote("up%d" % i, feedback.UP, source="good", category="labs")
            self.vote("dn%d" % i, feedback.DOWN, source="bad", category="media")
        aff = feedback.Affinity.load(self.conn, CHAT)
        self.assertGreater(aff.sources["good"], 0.5)
        self.assertLess(aff.sources["bad"], -0.5)
        liked, disliked = aff.top()
        self.assertEqual(liked[0][0], "good")
        self.assertEqual(disliked[0][0], "bad")

    def test_single_vote_is_damped(self):
        self.vote("h1", feedback.UP, source="fresh")
        aff = feedback.Affinity.load(self.conn, CHAT)
        # одна реакция не должна перевешивать всё: 1/(1+0+2)
        self.assertAlmostEqual(aff.sources["fresh"], 1 / 3.0, places=6)

    def test_empty_affinity_is_falsy(self):
        self.assertFalse(feedback.Affinity.load(self.conn, CHAT))

    def test_prescore_shifts_towards_liked_source(self):
        for i in range(5):
            self.vote("up%d" % i, feedback.UP, source="good", category="labs")
        good = [item("https://g.com/1", "Событие", "good", category="labs")]
        other = [item("https://o.com/1", "Событие", "other", category="labs")]
        self.assertAlmostEqual(rank.prescore(good), rank.prescore(other), places=6)
        ordering = feedback.weighted_prescore(self.conn, CHAT)
        self.assertGreater(ordering(good), ordering(other))

    def test_zero_weight_disables_influence(self):
        for i in range(5):
            self.vote("up%d" % i, feedback.UP, source="good")
        saved = CFG["feedback_weight"]
        CFG["feedback_weight"] = 0
        try:
            ordering = feedback.weighted_prescore(self.conn, CHAT)
            group = [item("https://g.com/1", "Событие", "good")]
            self.assertAlmostEqual(ordering(group), rank.prescore(group), places=9)
        finally:
            CFG["feedback_weight"] = saved


class TestPersonaHint(FeedbackCase):
    def test_empty_without_feedback(self):
        self.assertEqual(feedback.persona_hint(self.conn, CHAT), "")

    def test_mentions_both_sides(self):
        self.vote("h1", feedback.UP, title="Понравившаяся новость")
        self.vote("h2", feedback.DOWN, title="Скучная новость")
        hint = feedback.persona_hint(self.conn, CHAT)
        self.assertIn("Понравившаяся новость", hint)
        self.assertIn("Скучная новость", hint)
        self.assertIn("важное событие публикуй в любом случае", hint)


class TestCallback(unittest.TestCase):
    """Полный путь нажатия: апдейт → база → всплывашка → новая разметка."""

    def setUp(self):
        self.answers, self.edits = [], []
        self._real = (bot.tg_answer_callback, bot.tg_edit_markup, bot.tg_send)
        bot.tg_answer_callback = lambda cb_id, text="", alert=False: \
            self.answers.append(text)
        bot.tg_edit_markup = lambda chat, mid, kb: self.edits.append(kb)
        bot.tg_send = lambda *a, **kw: None
        self._owner = config.TG_CHAT
        config.TG_CHAT = CHAT
        conn = storage.db()
        for table in ("feedback", "saved", "items"):
            conn.execute("DELETE FROM %s" % table)
        conn.execute("INSERT INTO items(url_hash,url,source_id,tier,category,title,"
                     "fetched_at) VALUES ('hash1','https://e.com/1','openai',1,'labs',"
                     "'Крупный релиз','2026-01-01T00:00:00+00:00')")
        conn.commit()
        conn.close()

    def tearDown(self):
        bot.tg_answer_callback, bot.tg_edit_markup, bot.tg_send = self._real
        config.TG_CHAT = self._owner

    def press(self, data, chat_id=CHAT, keyboard=None):
        default = [[{"text": "1 👍", "callback_data": "fb:up:hash1"},
                    {"text": "1 👎", "callback_data": "fb:down:hash1"},
                    {"text": "1 🔖", "callback_data": "fb:save:hash1"}]]
        bot.handle_update({"update_id": 2, "callback_query": {
            "id": "cb1", "data": data,
            "message": {"message_id": 5, "chat": {"id": chat_id},
                        "reply_markup": {"inline_keyboard": keyboard or default}},
        }}, worker=None)

    def test_upvote_is_stored_with_facts_from_items(self):
        self.press("fb:up:hash1")
        conn = storage.db()
        row = conn.execute("SELECT * FROM feedback").fetchone()
        conn.close()
        self.assertEqual(row["verdict"], "up")
        self.assertEqual(row["source_id"], "openai")
        self.assertEqual(row["category"], "labs")
        self.assertEqual(row["title"], "Крупный релиз")
        self.assertIn("👍", self.answers[0])
        self.assertEqual(self.edits[0][0][0]["text"], "1 👍✓")

    def test_bookmark_toggles_through_button(self):
        self.press("fb:save:hash1")
        self.assertEqual(self.edits[-1][0][2]["text"], "1 🔖✓")
        self.press("fb:save:hash1", keyboard=self.edits[-1])
        self.assertEqual(self.edits[-1][0][2]["text"], "1 🔖")
        conn = storage.db()
        self.assertEqual(feedback.bookmarks(conn, CHAT), [])
        conn.close()

    def test_stranger_cannot_vote(self):
        self.press("fb:up:hash1", chat_id="999")
        conn = storage.db()
        count = conn.execute("SELECT COUNT(*) c FROM feedback").fetchone()["c"]
        conn.close()
        self.assertEqual(count, 0)
        self.assertEqual(self.edits, [])

    def test_garbage_callback_is_answered_and_ignored(self):
        self.press("что-то не то")
        self.assertEqual(self.edits, [])
        self.assertEqual(self.answers, [""])


if __name__ == "__main__":
    unittest.main()
