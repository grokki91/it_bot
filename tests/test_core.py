# -*- coding: utf-8 -*-
"""Смоук-тесты ядра: разбор фидов, дедупликация, отбор, рендер, база.

Запуск:  python3 -m unittest discover -s tests -v
Сеть и Telegram не трогаются: всё, что ходит наружу, здесь не вызывается.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import (feedback, feedparse, rank, render, storage,  # noqa: E402
                        textutil)
from newsdigest.config import CFG  # noqa: E402


def item(url, title, source="src", tier=2, category="media", social=0.0, age_h=1):
    published = datetime.now(timezone.utc) - timedelta(hours=age_h)
    return {
        "url_hash": textutil.url_hash(url),
        "url": textutil.canonical_url(url),
        "source_id": source,
        "tier": tier,
        "category": category,
        "title": title,
        "summary": title,
        "published_at": published.isoformat(timespec="seconds"),
        "sig": textutil.signature(title),
        "social": social,
    }


class TestUrls(unittest.TestCase):
    def test_tracking_stripped(self):
        a = textutil.canonical_url("https://www.example.com/post/?utm_source=tw&id=7")
        b = textutil.canonical_url("http://example.com/post?id=7&fbclid=xxx")
        self.assertEqual(a, b)
        self.assertEqual(textutil.url_hash(a), textutil.url_hash(b))

    def test_amp_and_mobile_host(self):
        self.assertEqual(textutil.canonical_url("https://m.example.com/x/amp"),
                         textutil.canonical_url("https://example.com/x"))

    def test_non_http_untouched(self):
        self.assertEqual(textutil.canonical_url("mailto:a@b.c"), "mailto:a@b.c")


class TestSimilarity(unittest.TestCase):
    def test_same_event_different_wording(self):
        a = textutil.signature("OpenAI releases GPT-6 with 2M context window")
        b = textutil.signature("GPT-6 released by OpenAI: 2M context window")
        self.assertGreater(textutil.similarity(a, b), CFG["similarity"])

    def test_unrelated_stay_apart(self):
        a = textutil.signature("Postgres 18 improves vacuum performance")
        b = textutil.signature("Nvidia announces new datacenter GPU")
        self.assertLess(textutil.similarity(a, b), CFG["similarity"])

    def test_empty_signature(self):
        self.assertEqual(textutil.similarity("", "anything"), 0.0)


class TestFeedParse(unittest.TestCase):
    RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Hello &amp; goodbye</title><link>https://e.com/1</link>
        <description>&lt;p&gt;Body text&lt;/p&gt;</description>
        <pubDate>Mon, 06 Sep 2021 12:00:00 GMT</pubDate></item>
    </channel></rss>"""

    ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Atom entry</title>
        <link rel="alternate" href="https://e.com/2"/>
        <summary>Short</summary><updated>2021-09-06T12:00:00Z</updated></entry>
    </feed>"""

    def test_rss(self):
        entries = feedparse.parse_feed(self.RSS)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Hello & goodbye")
        self.assertEqual(entries[0]["summary"], "Body text")
        self.assertEqual(entries[0]["published"].year, 2021)

    def test_atom_prefers_alternate_link(self):
        entries = feedparse.parse_feed(self.ATOM)
        self.assertEqual(entries[0]["link"], "https://e.com/2")

    def test_leading_junk_and_control_chars(self):
        raw = b"\xef\xbb\xbf\n" + self.RSS.replace(b"Body text", b"Body\x07text")
        self.assertEqual(len(feedparse.parse_feed(raw)), 1)

    def test_dates(self):
        self.assertIsNone(feedparse.parse_date(""))
        self.assertIsNone(feedparse.parse_date("вчера"))
        for value in ("Mon, 06 Sep 2021 12:00:00 GMT", "2021-09-06T12:00:00Z",
                      "2021-09-06T12:00:00.123+0300"):
            parsed = feedparse.parse_date(value)
            self.assertIsNotNone(parsed, value)
            self.assertIsNotNone(parsed.tzinfo, value)


class TestCluster(unittest.TestCase):
    def test_duplicates_merge_once(self):
        items = [
            item("https://a.com/1", "OpenAI releases GPT-6 with huge context", "a"),
            item("https://b.com/1", "GPT-6 released by OpenAI with huge context", "b"),
            item("https://c.com/1", "Rust 2.0 roadmap published", "c"),
        ]
        groups = rank.cluster(items, CFG["similarity"])
        self.assertEqual(sorted(len(g) for g in groups), [1, 2])

    def test_primary_is_lowest_tier(self):
        group = [item("https://n.com/1", "News", "hn", tier=3),
                 item("https://o.com/1", "News", "openai", tier=1)]
        self.assertEqual(rank.primary_of(group)["source_id"], "openai")

    def test_prescore_rewards_tier_and_corroboration(self):
        weak = [item("https://x.com/1", "Something", "x", tier=3, age_h=40)]
        strong = [item("https://a.com/1", "Something", "a", tier=1),
                  item("https://b.com/1", "Something", "b", tier=2)]
        self.assertGreater(rank.prescore(strong), rank.prescore(weak))


class TestSelect(unittest.TestCase):
    def setUp(self):
        self.saved = {k: CFG[k] for k in
                      ("max_items", "min_items", "min_score",
                       "max_per_source", "max_per_category")}
        CFG.update(max_items=4, min_items=1, min_score=5.0,
                   max_per_source=2, max_per_category=3)

    def tearDown(self):
        CFG.update(self.saved)

    def test_source_limit_respected(self):
        shortlist = [[item("https://a.com/%d" % i, "T%d" % i, "same")] for i in range(6)]
        ranking = [{"id": i, "score": 9.0, "category": "media"} for i in range(6)]
        picked = rank.select(ranking, shortlist)
        self.assertEqual(len(picked), 2)

    def test_threshold_relaxes_when_empty(self):
        shortlist = [[item("https://a.com/%d" % i, "T%d" % i, "s%d" % i)]
                     for i in range(3)]
        ranking = [{"id": i, "score": 4.2, "category": "media"} for i in range(3)]
        self.assertTrue(rank.select(ranking, shortlist))

    def test_garbage_ranking_ignored(self):
        shortlist = [[item("https://a.com/1", "T", "s")]]
        ranking = [{"id": "нет", "score": None}, {"id": 99, "score": 9.0},
                   {"id": 0, "score": 9.0, "category": "labs"}]
        picked = rank.select(ranking, shortlist)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0][2], "labs")


class TestRender(unittest.TestCase):
    def cards(self, count, text="кратко"):
        out = []
        for i in range(count):
            group = [item("https://a.com/%d" % i, "Заголовок %d" % i, "src%d" % i)]
            out.append(({"headline": "Заголовок %d" % i, "what": text,
                         "why": "потому что"}, group, 7.5, "labs"))
        return out

    def test_escapes_html(self):
        group = [item("https://a.com/1", "<b>bold</b> & co", "src")]
        text = render.render([({"headline": "<b>bold</b> & co", "what": "", "why": ""},
                               group, 7.0, "media")], 10)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; &amp; co", text)

    def test_fits_one_message(self):
        messages = render.fit_message(self.cards(8), 100)
        self.assertEqual(len(messages), 1)
        text, chunk = messages[0]
        self.assertIn("Заголовок 7", text)
        self.assertEqual(len(chunk), 8)

    def test_splits_when_too_long(self):
        # даже в самом сжатом виде (trim=2) остаются заголовки со ссылками,
        # поэтому длинный выпуск обязан развалиться на несколько сообщений
        cards = self.cards(30, "очень длинный текст " * 40)
        cards = [(dict(c, headline=c["headline"] + " — " + "слово " * 12), g, s, k)
                 for c, g, s, k in cards]
        messages = render.fit_message(cards, 100)
        self.assertGreater(len(messages), 1)
        for text, chunk in messages:
            self.assertLessEqual(len(text), 4096)
            self.assertTrue(chunk)
        # ни одна карточка не потерялась и не задвоилась
        self.assertEqual(sum(len(chunk) for _t, chunk in messages), len(cards))

    def test_keyboard_matches_numbering(self):
        cards = self.cards(3)
        keyboard = render.feedback_keyboard(cards)
        self.assertEqual(len(keyboard), 3)
        self.assertEqual([b["text"] for b in keyboard[0]], ["1 👍", "1 👎", "1 🔖"])
        for row in keyboard:
            for button in row:
                self.assertLessEqual(len(button["callback_data"].encode()), 64)

    def test_keyboard_can_be_switched_off(self):
        CFG["feedback_buttons"] = False
        try:
            self.assertIsNone(render.feedback_keyboard(self.cards(2)))
        finally:
            CFG["feedback_buttons"] = True

    def test_collapse_hides_rows_behind_one_button(self):
        keyboard = render.feedback_keyboard(self.cards(6))
        folded = render.collapse(keyboard)
        self.assertEqual(len(folded), 1)
        self.assertEqual(len(folded[0]), 1)
        self.assertIn("(6)", folded[0][0]["text"])
        self.assertEqual(folded[0][0]["callback_data"], render.MORE)

    def test_single_news_stays_as_is(self):
        # три кнопки под срочной новостью прятать не за чем
        keyboard = render.feedback_keyboard(self.cards(1))
        self.assertEqual(render.collapse(keyboard), keyboard)

    def test_expand_restores_rows_with_marks(self):
        keyboard = render.feedback_keyboard(self.cards(3))
        url_hash = keyboard[1][0]["callback_data"].split(":")[2]
        rows = render.expand(keyboard, {url_hash: feedback.UP}, set())
        self.assertEqual(len(rows), 4)                      # 3 новости + «свернуть»
        self.assertEqual(rows[1][0]["text"], "2 👍✓")        # оценка видна сразу
        self.assertEqual(rows[1][1]["text"], "2 👎")
        self.assertEqual(rows[-1][0]["callback_data"], render.LESS)
        # исходную раскладку разворачивание не портит
        self.assertEqual(keyboard[1][0]["text"], "2 👍")

    def test_delivery_follows_style(self):
        keyboard = render.feedback_keyboard(self.cards(4))
        CFG["feedback_style"] = "rows"
        try:
            self.assertEqual(render.for_delivery(keyboard), keyboard)
        finally:
            CFG["feedback_style"] = "compact"
        self.assertEqual(len(render.for_delivery(keyboard)), 1)

    def test_signup_keyboard_is_never_folded(self):
        keyboard = [[{"text": "✅ Пустить", "callback_data": "sub:ok:1"},
                     {"text": "🚫 Нет", "callback_data": "sub:no:1"}],
                    [{"text": "ещё", "callback_data": "sub:ok:2"}]]
        self.assertEqual(render.for_delivery(keyboard), keyboard)

    def test_mark_pressed_is_exclusive_for_verdicts(self):
        keyboard = render.feedback_keyboard(self.cards(2))
        up = keyboard[0][0]["callback_data"]
        down = keyboard[0][1]["callback_data"]
        save = keyboard[0][2]["callback_data"]

        render.mark_pressed(keyboard, up)
        self.assertEqual(keyboard[0][0]["text"], "1 👍✓")

        render.mark_pressed(keyboard, save)
        self.assertEqual(keyboard[0][2]["text"], "1 🔖✓")
        self.assertEqual(keyboard[0][0]["text"], "1 👍✓")   # закладка не сбила оценку

        render.mark_pressed(keyboard, down)
        self.assertEqual(keyboard[0][0]["text"], "1 👍")     # 👎 снял 👍
        self.assertEqual(keyboard[0][1]["text"], "1 👎✓")
        self.assertEqual(keyboard[0][2]["text"], "1 🔖✓")    # а закладка осталась

        render.mark_pressed(keyboard, save, pressed=False)
        self.assertEqual(keyboard[0][2]["text"], "1 🔖")
        self.assertEqual(keyboard[1][0]["text"], "2 👍")     # соседний ряд не тронут


class TestSending(unittest.TestCase):
    """Что уходит в Telegram и что при этом остаётся в базе."""

    def setUp(self):
        from newsdigest import telegram
        self.payloads = []
        self._real = telegram.tg_call
        telegram.tg_call = lambda method, payload, **kw: (
            self.payloads.append(payload) or {"message_id": 4242})
        conn = storage.db()
        conn.execute("DELETE FROM outbox")
        conn.commit()
        conn.close()

    def tearDown(self):
        from newsdigest import telegram
        telegram.tg_call = self._real

    def test_long_keyboard_goes_out_folded_but_is_stored_whole(self):
        from newsdigest import telegram
        keyboard = [[{"text": "%d 👍" % n, "callback_data": "fb:up:h%d" % n}]
                    for n in (1, 2, 3)]
        telegram.tg_send("77", "выпуск", keyboard=keyboard)
        sent = self.payloads[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0]["callback_data"], render.MORE)

        conn = storage.db()
        try:
            # по номеру сообщения бот найдёт полную раскладку и развернёт её
            self.assertEqual(len(storage.outbox_keyboard(conn, "77", 4242)), 3)
        finally:
            conn.close()


class TestStorage(unittest.TestCase):
    def test_schema_meta_and_migration(self):
        conn = storage.db()
        try:
            storage.meta_set(conn, "k", "v")
            self.assertEqual(storage.meta_get(conn, "k"), "v")
            self.assertEqual(storage.meta_get(conn, "нет", "по умолчанию"),
                             "по умолчанию")
            self.assertTrue(storage.ensure_column(conn, "items", "probe", "TEXT"))
            self.assertFalse(storage.ensure_column(conn, "items", "probe", "TEXT"))
            storage.log_run(conn, "test", "ok", {"a": 1})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
