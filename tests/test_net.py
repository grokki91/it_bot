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

from newsdigest import net, sources  # noqa: E402


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
        got_src, items, error = sources.fetch_source(src)
        self.assertIs(got_src, src)
        self.assertEqual(items, [])
        self.assertEqual(error, "HTTP 0")

    def test_fetch_source_does_not_retry_on_network_failure(self):
        """Повтор с другим UA — для 403/405/429/451, а не для оборванной сети."""
        sources.fetch_source(("bbc", "https://example.com/feed.xml", 1, "media"))
        self.assertEqual(net._open.calls, 1)

    def test_fetch_hackernews_returns_nothing(self):
        self.assertEqual(sources.fetch_hackernews(keywords=["ai"]), [])


if __name__ == "__main__":
    unittest.main()
