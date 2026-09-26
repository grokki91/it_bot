# -*- coding: utf-8 -*-
"""Проверка списка лент: что считать мёртвым и когда валить CI.

Сеть не трогаем — подменяем `download`. Смысл теста в правилах вердикта:
CI должен падать от мёртвого нового адреса и не падать от защиты от роботов,
которую сервер может и не встретить.
"""
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

_spec = importlib.util.spec_from_file_location(
    "nd_feedcheck", os.path.join(ROOT, "tools", "feedcheck.py"))
feedcheck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(feedcheck)

FEED = (b"<rss><channel><item><title>T</title><link>https://e.com/1</link>"
        b"<pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item></channel></rss>")


class Case(unittest.TestCase):
    def setUp(self):
        self.real = (feedcheck.download, feedcheck.rows, feedcheck.known_at,
                     feedcheck.RETRY_AFTER, feedcheck.resolves)
        feedcheck.RETRY_AFTER = 0
        feedcheck.resolves = lambda url: True

    def tearDown(self):
        (feedcheck.download, feedcheck.rows, feedcheck.known_at,
         feedcheck.RETRY_AFTER, feedcheck.resolves) = self.real

    def answer(self, *replies):
        """Каждый вызов `download` — следующий ответ из списка."""
        replies = list(replies)
        feedcheck.download = lambda url: replies.pop(0) if len(replies) > 1 \
            else replies[0]

    def verdict(self, *replies):
        self.answer(*replies)
        return feedcheck.verdict(feedcheck.probe(("кандидат", "ai", "x", "https://e.com/x")))


class VerdictCase(Case):
    def test_live_feed(self):
        self.assertEqual(self.verdict((200, FEED)), "warn")   # запись 2024 года
        yesterday = format_datetime(datetime.now(timezone.utc) - timedelta(days=1))
        fresh = FEED.replace(b"Mon, 01 Jan 2024 10:00:00 GMT", yesterday.encode())
        self.assertEqual(self.verdict((200, fresh)), "ok")

    def test_gone_for_good(self):
        self.assertEqual(self.verdict((404, b"")), "dead")
        self.assertEqual(self.verdict((200, b"<!DOCTYPE html><html><p>moved</html>")),
                         "dead")
        self.assertEqual(self.verdict((200, b"<rss><channel/></rss>")), "dead")

    def test_no_domain_is_dead_but_timeout_is_not(self):
        feedcheck.resolves = lambda url: False
        self.assertEqual(self.verdict((0, b"")), "dead")
        feedcheck.resolves = lambda url: True
        self.assertEqual(self.verdict((0, b"")), "warn")

    def test_robot_guard_is_a_warning(self):
        """403 и пустая двухсотка: сервер может увидеть их иначе, чем CI."""
        self.assertEqual(self.verdict((403, b"")), "warn")
        self.assertEqual(self.verdict((429, b"")), "warn")
        self.assertEqual(self.verdict((200, b"  \n")), "warn")

    def test_not_a_feed_shows_where_it_broke(self):
        self.answer((200, b"<!DOCTYPE html><html><p>moved</html>"))
        note = feedcheck.probe(("кандидат", "ai", "x", "https://e.com/x"))["note"]
        self.assertIn("<!DOCTYPE html>", note)


class GateCase(Case):
    """С --new-since CI падает только от нового и определённо мёртвого."""

    def run_main(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = feedcheck.main(["--new-since", "REF"])
        return code, out.getvalue()

    def setUp(self):
        super().setUp()
        feedcheck.known_at = lambda ref: {"https://e.com/old"}

    def serve(self, **urls):
        """Адрес -> список ответов по очереди (последний повторяется)."""
        feedcheck.rows = lambda: [("подборка", "ai", name, "https://e.com/%s" % name)
                                  for name in urls]
        queues = {"https://e.com/%s" % k: list(v) for k, v in urls.items()}
        feedcheck.download = lambda url: queues[url].pop(0) \
            if len(queues[url]) > 1 else queues[url][0]

    def test_dead_old_address_does_not_fail_the_build(self):
        self.serve(old=[(404, b"")])
        self.assertEqual(self.run_main()[0], 0)

    def test_dead_new_address_fails_it(self):
        self.serve(old=[(200, FEED)], fresh=[(404, b"")])
        code, text = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("https://e.com/fresh", text)

    def test_guarded_new_address_only_warns(self):
        self.serve(fresh=[(403, b"")])
        self.assertEqual(self.run_main()[0], 0)

    def test_placeholder_page_gets_a_second_chance(self):
        """Заглушка The Register посреди обычной работы — не смерть ленты."""
        self.serve(fresh=[(200, b"<!DOCTYPE html><html><p>wait</html>"), (200, FEED)])
        self.assertEqual(self.run_main()[0], 0)


class KnownAtCase(unittest.TestCase):
    def test_addresses_are_read_from_git(self):
        """Что было в коде раньше — из git: иначе «новых» не отличить."""
        urls = feedcheck.known_at("HEAD")
        self.assertIn("https://www.nasa.gov/feed/", urls)


if __name__ == "__main__":
    unittest.main()
