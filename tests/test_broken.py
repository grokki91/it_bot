# -*- coding: utf-8 -*-
"""Сломанные ленты: их надо УВИДЕТЬ и починить одной командой.

Список фидов стареет молча: лента отвечает 404 полгода, источник сутки
заглушён, потом пробуется снова — и так до бесконечности. В выпуске его нет, а
в глаза это не бросается.
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import candidates, cli, storage, trust, userprofiles  # noqa: E402
from newsdigest.config import PROFILES_FILE, now_iso  # noqa: E402
from newsdigest.profiles import PROFILES  # noqa: E402


class ReplaceFeedCase(unittest.TestCase):
    """Переезд ленты — это правка адреса, а не новый источник."""

    def setUp(self):
        if PROFILES_FILE.exists():
            PROFILES_FILE.unlink()
        userprofiles.apply()

    tearDown = setUp

    def test_name_survives_the_move(self):
        """Имя — ключ к доверию, истории и здоровью: менять его нельзя."""
        was = trust.meta("ap-topnews")
        userprofiles.replace_feed("ap-topnews", "https://example.com/ap.rss")
        now = trust.meta("ap-topnews")
        self.assertEqual(now["kind"], was["kind"])
        self.assertTrue(now["wire"])
        urls = {f[0]: f[1] for f in PROFILES["politics"]["feeds"]}
        self.assertEqual(urls["ap-topnews"], "https://example.com/ap.rss")

    def test_tier_and_category_stay(self):
        _id, _url, tier, category = userprofiles.replace_feed(
            "wmo", "https://example.com/wmo.xml")
        self.assertEqual((tier, category),
                         next((f[2], f[3]) for f in PROFILES["climate"]["feeds"]
                              if f[0] == "wmo"))

    def test_unknown_source_is_refused(self):
        with self.assertRaises(ValueError):
            userprofiles.replace_feed("нет-такого", "https://example.com/x.xml")

    def test_a_link_that_is_not_a_link_is_refused(self):
        with self.assertRaises(ValueError):
            userprofiles.replace_feed("wmo", "example.com/wmo.xml")


class BrokenCase(unittest.TestCase):
    """`feeds --broken`: показать сломанное и прописать найденный переезд."""

    def setUp(self):
        if PROFILES_FILE.exists():
            PROFILES_FILE.unlink()
        userprofiles.apply()
        self.conn = storage.db()
        self.conn.execute("DELETE FROM health")
        self.conn.execute(
            "INSERT INTO health(source_id, ok_at, err, err_at, fails, last_count,"
            " empty, empty_at) VALUES ('wmo', '', 'HTTP 404', ?, 31, 0, 0, '')",
            (now_iso(),))
        self.conn.commit()
        self.real_fetch = cli.fetch_source

    def tearDown(self):
        cli.fetch_source = self.real_fetch
        self.conn.close()
        if PROFILES_FILE.exists():
            PROFILES_FILE.unlink()
        userprofiles.apply()

    def answers(self, alive_url):
        """Отвечает только один адрес — тот, что назвали живым."""
        def fake(src):
            source_id, url, _tier, _category = src
            if url == alive_url:
                return src, [{"title": "t"}], 12, ""
            return src, [], 0, "HTTP 404"
        return fake

    def run_cli(self, adopt=False):
        # зовём саму команду, а не main: тот перечитывает окружение в CFG и
        # тащит в общий прогон значения, оставленные соседними тестами
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.check_broken(adopt)
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_the_flag_is_wired_to_the_command(self):
        args = cli.build_parser().parse_args(["feeds", "--broken", "--adopt"])
        self.assertTrue(args.broken and args.adopt)
        self.assertIs(args.func, cli.cmd_feeds)

    def test_broken_feed_is_listed_with_its_candidates(self):
        cli.fetch_source = self.answers("не отвечает никто")
        text = self.run_cli()
        self.assertIn("wmo", text)
        self.assertIn("не отвечал ни разу", text)
        self.assertIn("адрес не существует", text)   # подсказка по 404

    def test_a_working_replacement_is_offered_but_not_written(self):
        alive = candidates.REPLACEMENTS["wmo"][0][0]
        cli.fetch_source = self.answers(alive)
        text = self.run_cli()
        self.assertIn("Нашлась замена", text)
        self.assertFalse(PROFILES_FILE.exists())     # без --adopt ничего не пишем

    def test_adopt_writes_the_replacement_and_forgets_the_failures(self):
        alive = candidates.REPLACEMENTS["wmo"][0][0]
        cli.fetch_source = self.answers(alive)
        self.run_cli(adopt=True)
        urls = {f[0]: f[1] for f in PROFILES["climate"]["feeds"]}
        self.assertEqual(urls["wmo"], alive)
        # счётчик сбоев про старый адрес: иначе новый молчит ещё сутки
        row = self.conn.execute("SELECT COUNT(*) n FROM health "
                                "WHERE source_id='wmo'").fetchone()
        self.assertEqual(row["n"], 0)

    def test_a_feed_that_answers_again_is_not_replaced(self):
        """Сбой бывает временным: адрес живой — менять нечего."""
        current = next(f[1] for f in PROFILES["climate"]["feeds"] if f[0] == "wmo")
        cli.fetch_source = self.answers(current)
        text = self.run_cli(adopt=True)
        self.assertIn("сбой был временным", text)
        self.assertFalse(PROFILES_FILE.exists())

    def test_healthy_feeds_are_not_touched(self):
        self.conn.execute("DELETE FROM health")
        self.conn.commit()
        cli.fetch_source = self.answers("никто")
        text = self.run_cli()
        self.assertIn("Сломанных источников нет", text)


class ReplacementsTableCase(unittest.TestCase):
    """Таблица переездов: имена в ней должны существовать, адреса — быть новыми."""

    def test_every_name_is_a_real_source(self):
        known = {f[0] for topic in PROFILES for f in PROFILES[topic]["feeds"]}
        unknown = sorted(set(candidates.REPLACEMENTS) - known)
        self.assertEqual(unknown, [], "переезд описан для несуществующей ленты")

    def test_replacement_is_not_the_current_address(self):
        """Предлагать тот же адрес бессмысленно: он и так проверяется первым."""
        current = {f[0]: f[1] for topic in PROFILES
                   for f in PROFILES[topic]["feeds"]}
        same = [sid for sid, rows in candidates.REPLACEMENTS.items()
                if any(url == current.get(sid) for url, _why in rows)]
        self.assertEqual(same, [])

    def test_addresses_are_links(self):
        for source_id, rows in candidates.REPLACEMENTS.items():
            for url, why in rows:
                self.assertTrue(url.startswith("https://"), source_id)
                self.assertTrue(why, source_id)


if __name__ == "__main__":
    unittest.main()
