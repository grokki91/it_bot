# -*- coding: utf-8 -*-
"""Куда ведёт ссылка: заслон между чужим фидом и читателем.

Главный случай, ради которого всё затевалось:

    <a href="https://apnews.com@phish.tk/login">apnews</a>

Подпись читается как «apnews», переход идёт на phish.tk. Сеть здесь не
трогается: всё, что проверяется без запросов, и проверяется без запросов.
"""
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import newsfeed, rank, safety, storage  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False


class TestHost(unittest.TestCase):
    """Настоящий хост ссылки. Здесь ломается наивная проверка."""

    def test_userinfo_hides_the_real_host(self):
        self.assertEqual(safety.host("https://apnews.com@phish.tk/login"),
                         "phish.tk")
        self.assertEqual(safety.host("https://u:p@feeds.example.com/rss"),  # nd-redact: allow
                         "feeds.example.com")

    def test_www_and_port_stripped(self):
        self.assertEqual(safety.host("https://www.bbc.co.uk:443/news"),
                         "bbc.co.uk")

    def test_under_is_a_suffix_not_a_substring(self):
        """Имя издания в начале домена — это подделка, а не поддомен."""
        self.assertTrue(safety.under("amp.theguardian.com", "theguardian.com"))
        self.assertTrue(safety.under("theguardian.com", "theguardian.com"))
        self.assertFalse(
            safety.under("theguardian.com.secure-login.tk", "theguardian.com"))


class TestShape(unittest.TestCase):
    """Слой 0: что видно по одному виду ссылки, без сети и без базы."""

    def test_masked_host_is_rejected(self):
        self.assertIn("@", safety.shaped_badly("https://apnews.com@phish.tk/x"))

    def test_only_http_schemes(self):
        for url in ("javascript:alert(1)", "data:text/html,<b>hi",
                    "ftp://example.com/x", "file:///etc/passwd"):
            self.assertTrue(safety.shaped_badly(url), url)

    def test_ip_punycode_and_odd_port(self):
        self.assertTrue(safety.shaped_badly("http://192.168.1.1/news"))
        self.assertTrue(safety.shaped_badly("https://xn--80ak6aa92e.com/x"))
        self.assertTrue(safety.shaped_badly("https://example.com:8443/x"))

    def test_control_characters(self):
        """Перевод строки внутри href разваливает разметку сообщения."""
        self.assertTrue(safety.shaped_badly("https://example.com/x\nEvil"))
        self.assertTrue(safety.shaped_badly('https://example.com/"><b>'))

    def test_brand_bait(self):
        """Домен, который рядится под известное издание."""
        self.assertTrue(safety.shaped_badly("https://theguardian.com.login-x.tk/a"))

    def test_ordinary_links_pass(self):
        for url in ("https://www.theguardian.com/world/2026/aug/28/x",
                    "http://arstechnica.com/ai/",
                    "https://example.org/some/path?id=7"):
            self.assertEqual(safety.shaped_badly(url), "", url)


class TestVerdict(unittest.TestCase):
    """Слои 1-2: свой издатель, реестр изданий, наша собственная история."""

    def setUp(self):
        self.conn = storage.db()
        self.conn.execute("DELETE FROM hosts")
        self.conn.commit()
        self.saved = {k: CFG[k] for k in CFG}

    def tearDown(self):
        CFG.update(self.saved)
        self.conn.close()

    def test_own_publisher_is_enough(self):
        mark, _why = safety.verdict(
            self.conn, "https://www.theguardian.com/world/x", "guardian-world")
        self.assertEqual(mark, safety.OK)

    def test_masked_host_never_passes(self):
        mark, _why = safety.verdict(
            self.conn, "https://theguardian.com@phish.tk/x", "guardian-world")
        self.assertEqual(mark, safety.UNSAFE)

    def test_unknown_domain_is_unknown_not_unsafe(self):
        """Незнакомый домен — это «не знаем», а не приговор: половина хороших
        ссылок с Hacker News ведёт в чей-то личный блог."""
        mark, _why = safety.verdict(self.conn, "https://some-blog.dev/post",
                                    "hackernews")
        self.assertEqual(mark, safety.UNKNOWN)

    def test_unknown_domain_in_a_risky_zone_is_unsafe(self):
        mark, why = safety.verdict(self.conn, "https://xk39dl.tk/win", "hackernews")
        self.assertEqual(mark, safety.UNSAFE)
        self.assertIn(".tk", why)

    def test_familiar_domain_earns_trust_by_history(self):
        """Домен, который мы видим неделями и десятками записей, — не однодневка."""
        old = "2020-01-01T00:00:00+00:00"
        self.conn.execute(
            "INSERT INTO hosts(host, first_seen, last_seen, seen) VALUES (?,?,?,?)",
            ("some-blog.dev", old, old, 50))
        self.conn.commit()
        mark, why = safety.verdict(self.conn, "https://some-blog.dev/post",
                                   "hackernews")
        self.assertEqual(mark, safety.OK)
        self.assertIn("знаком", why)

    def test_note_hosts_counts_what_we_see(self):
        rows = [item("https://some-blog.dev/1", "Раз"),
                item("https://some-blog.dev/2", "Два"),
                item("https://other.example/3", "Три")]
        safety.note_hosts(self.conn, rows)
        seen = dict(self.conn.execute("SELECT host, seen FROM hosts"))
        self.assertEqual(seen["some-blog.dev"], 2)
        self.assertEqual(seen["other.example"], 1)


class TestCheck(unittest.TestCase):
    """Точка входа: проверка при сборе. Сеть подменена."""

    def setUp(self):
        self.conn = storage.db()
        self.conn.execute("DELETE FROM hosts")
        self.conn.commit()
        self.saved = {k: CFG[k] for k in CFG}
        CFG["safebrowsing"] = False         # без ключа слой и так молчит
        self._resolve = safety.resolve

    def tearDown(self):
        safety.resolve = self._resolve
        CFG.update(self.saved)
        self.conn.close()

    def test_marks_each_item(self):
        rows = [item("https://www.theguardian.com/world/x", "Своя", "guardian-world"),
                item("https://apnews.com@phish.tk/x", "Подделка", "guardian-world")]
        CFG["safe_resolve"] = False
        stats = safety.check(self.conn, rows)
        self.assertEqual(rows[0]["safe"], safety.OK)
        self.assertEqual(rows[1]["safe"], safety.UNSAFE)
        self.assertEqual(stats["unsafe"], 1)

    def test_shortener_is_unrolled_and_the_target_is_published(self):
        """Читателю полезнее видеть, куда он идёт, чем bit.ly."""
        safety.resolve = lambda _url: "https://www.theguardian.com/world/real"
        rows = [item("https://bit.ly/3xYz", "Через сокращатель", "guardian-world")]
        stats = safety.check(self.conn, rows)
        self.assertEqual(rows[0]["safe"], safety.OK)
        self.assertEqual(rows[0]["url"], "https://www.theguardian.com/world/real")
        self.assertEqual(stats["resolved"], 1)

    def test_shortener_that_does_not_unroll_is_unsafe(self):
        """Не смогли выяснить, куда ведёт, — не показываем."""
        safety.resolve = lambda _url: ""
        rows = [item("https://bit.ly/3xYz", "Тупик", "guardian-world")]
        safety.check(self.conn, rows)
        self.assertEqual(rows[0]["safe"], safety.UNSAFE)

    def test_switch_off_leaves_everything_alone(self):
        CFG["safe_links"] = False
        rows = [item("https://apnews.com@phish.tk/x", "Подделка", "guardian-world")]
        safety.check(self.conn, rows)
        self.assertEqual(rows[0]["safe"], safety.OK)


class TestFaceMoves(unittest.TestCase):
    """Приговор ссылке — не приговор новости: лицо кластера уступает место."""

    def setUp(self):
        self.saved = {k: CFG[k] for k in CFG}

    def tearDown(self):
        CFG.update(self.saved)

    def test_unsafe_item_never_becomes_the_face(self):
        bad = item("https://apnews.com@phish.tk/x", "Землетрясение в Японии",
                   "phishy", tier=1, age_h=0)
        bad["safe"] = safety.UNSAFE
        good = item("https://www.theguardian.com/world/quake",
                    "Землетрясение в Японии", "guardian-world", tier=2, age_h=2)
        good["safe"] = safety.OK
        # у плохого tier выше и он свежее — по прежним правилам лицом был бы он
        self.assertIs(rank.primary_of([bad, good]), good)

    def test_event_without_a_single_usable_link_is_dropped(self):
        bad = item("https://phish.tk/x", "Событие", "phishy")
        bad["safe"] = safety.UNSAFE
        kept, dropped = safety.drop_unsafe([[bad]])
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 1)

    def test_event_keeps_living_while_one_link_is_fine(self):
        bad = item("https://phish.tk/x", "Событие", "phishy")
        bad["safe"] = safety.UNSAFE
        good = item("https://www.theguardian.com/world/x", "Событие", "guardian-world")
        good["safe"] = safety.OK
        kept, dropped = safety.drop_unsafe([[bad, good]])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 0)

    def test_strict_mode_publishes_only_what_was_vouched_for(self):
        unknown = item("https://some-blog.dev/x", "Событие", "hackernews")
        unknown["safe"] = safety.UNKNOWN
        self.assertTrue(safety.safe(unknown))
        CFG["safe_strict"] = True
        self.assertFalse(safety.safe(unknown))

    def test_item_without_a_verdict_behaves_as_before(self):
        """Материалы, собранные до появления проверки, не должны пропасть."""
        old = item("https://example.org/x", "Старая новость")
        self.assertTrue(safety.safe(old))


class TestPageGuard(unittest.TestCase):
    """Второй рубеж — у самого HTML: страница открыта всем."""

    def test_masked_host_does_not_reach_the_page(self):
        self.assertEqual(newsfeed.outward("https://apnews.com@phish.tk/x"), "")
        self.assertEqual(newsfeed.outward("javascript:alert(1)"), "")

    def test_ordinary_link_passes(self):
        url = "https://www.theguardian.com/world/x"
        self.assertEqual(newsfeed.outward(url), url)


if __name__ == "__main__":
    unittest.main()
