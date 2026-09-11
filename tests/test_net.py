# -*- coding: utf-8 -*-
"""HTTP-слой: сетевые сбои не выходят наружу исключениями.

Сеть не трогаем — подменяем net._open.
"""
import os
import socket
import ssl
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import net, sources, storage  # noqa: E402


class _Broken:
    """Подменяет net._open и запоминает, сколько раз его дёрнули."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def __call__(self, url, data=None, headers=None, timeout=30, method=None):
        self.calls += 1
        raise self.exc


class NetFailure(unittest.TestCase):
    def setUp(self):
        self.real_open = net._open
        self.addCleanup(setattr, net, "_open", self.real_open)

    def fails_with(self, exc):
        net._open = _Broken(exc)
        return net.http_get("https://example.com/feed.xml")

    def test_timeout(self):
        self.assertEqual(self.fails_with(socket.timeout("timed out")), (0, b""))

    def test_dns(self):
        exc = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        self.assertEqual(self.fails_with(exc), (0, b""))

    def test_tls(self):
        exc = ssl.SSLError(1, "certificate verify failed")
        self.assertEqual(self.fails_with(exc), (0, b""))

    def test_connection_refused(self):
        exc = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        self.assertEqual(self.fails_with(exc), (0, b""))

    def test_http_error_still_gives_code_and_body(self):
        """Ответ сервера — не сбой: код и тело доходят до вызывающего."""
        exc = urllib.error.HTTPError("https://example.com/", 404, "Not Found", {},
                                     None)
        exc.read = lambda: b"nope"
        net._open = _Broken(exc)
        self.assertEqual(net.http_get("https://example.com/"), (404, b"nope"))

    def test_success_passes_through(self):
        net._open = lambda url, **kw: (200, b"<rss/>")
        self.assertEqual(net.http_get("https://example.com/"), (200, b"<rss/>"))


class CallersSurviveFailure(unittest.TestCase):
    def setUp(self):
        self.real_open = net._open
        self.addCleanup(setattr, net, "_open", self.real_open)
        net._open = _Broken(socket.timeout("timed out"))

    def test_fetch_source_reports_http_0(self):
        src = ("bbc", "https://example.com/feed.xml", 1, "media")
        got_src, items, _total, error = sources.fetch_source(src)
        self.assertIs(got_src, src)
        self.assertEqual(items, [])
        self.assertEqual(error, "HTTP 0")

    def test_fetch_source_does_not_retry_on_network_failure(self):
        """Повтор с другим UA — для 403/405/429/451, а не для оборванной сети."""
        sources.fetch_source(("bbc", "https://example.com/feed.xml", 1, "media"))
        self.assertEqual(net._open.calls, 1)

    def test_fetch_hackernews_returns_nothing(self):
        self.assertEqual(sources.fetch_hackernews(keywords=["ai"]), [])


def _feed(*dates) -> bytes:
    items = "".join("<item><title>t%d</title><link>https://e.com/%d</link>"
                    "<pubDate>%s</pubDate></item>" % (i, i, d)
                    for i, d in enumerate(dates))
    return ("<rss><channel>%s</channel></rss>" % items).encode("utf-8")


class QuietIsNotBroken(unittest.TestCase):
    """Блог без свежего — это не молчащая лента.

    `window_hours` = 30, а `rust-blog` пишет раз в несколько недель. Если
    считать «ноль свежего» поломкой, в отчёте окажутся исправные блоги, а
    по-настоящему сломанный источник в этом списке потеряется.
    """

    def setUp(self):
        self.real_open = net._open
        self.addCleanup(setattr, net, "_open", self.real_open)

    def serve(self, raw):
        net._open = lambda url, **kw: (200, raw)
        return sources.fetch_source(("blog", "https://e.com/feed", 1, "media"))

    def test_stale_entries_are_counted_but_not_fresh(self):
        _src, items, total, err = self.serve(_feed("Mon, 01 Jan 2024 10:00:00 GMT"))
        self.assertEqual(err, "")
        self.assertEqual(items, [])
        self.assertEqual(total, 1)

    def test_feed_without_entries_at_all_is_empty(self):
        _src, items, total, err = self.serve(b"<rss><channel/></rss>")
        self.assertEqual(err, "")
        self.assertEqual((items, total), ([], 0))

    def test_health_calls_stale_feed_ok_and_empty_feed_quiet(self):
        conn = storage.db()
        self.addCleanup(conn.close)
        sources.mark_health(conn, "stale-blog", True, count=0, total=7)
        sources.mark_health(conn, "dead-feed", True, count=0, total=0)
        state = {row["source_id"]: sources.feed_state(row)
                 for row in sources.health_map(conn).values()}
        self.assertEqual(state["stale-blog"]["empty"], 0)
        self.assertEqual(state["dead-feed"]["empty"], 1)


if __name__ == "__main__":
    unittest.main()
