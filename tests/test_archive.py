# -*- coding: utf-8 -*-
"""Архив источников: убрать молчащее из обхода, но не потерять.

Лента, которой нет две недели, не чинится ожиданием: домен продан, издание
закрылось, сайт закрылся от роботов. Держать её в обходе — это шесть
бесполезных запросов в сутки и строка в списке проблемных, на которую
перестают смотреть. Но и удалять нельзя: сайты возвращаются.
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import cli, daemon, sources, storage, trust, userprofiles  # noqa: E402
from newsdigest.config import CFG, PROFILES_FILE  # noqa: E402
from newsdigest.profiles import PROFILES  # noqa: E402

VICTIM = "wmo"          # встроенная лента климата
TOPIC = "climate"


def ago(days):
    return (datetime.now(timezone.utc)
            - timedelta(days=days)).isoformat(timespec="seconds")


class Case(unittest.TestCase):
    def setUp(self):
        if PROFILES_FILE.exists():
            PROFILES_FILE.unlink()
        userprofiles.apply()
        self.conn = storage.db()
        self.conn.execute("DELETE FROM health")
        self.conn.execute("DELETE FROM archive")
        self.conn.commit()
        self.real_fetch = cli.fetch_source

    def tearDown(self):
        cli.fetch_source = self.real_fetch
        self.conn.close()
        if PROFILES_FILE.exists():
            PROFILES_FILE.unlink()
        userprofiles.apply()

    def silent(self, source_id=VICTIM, days=30, empty=False):
        """Кладёт в health источник, который молчит столько дней."""
        if empty:
            self.conn.execute(
                "INSERT INTO health(source_id, ok_at, err, err_at, fails,"
                " last_count, empty, empty_at, fail_since)"
                " VALUES (?,?,'','',0,0,?,?,'')",
                (source_id, ago(days), CFG["quiet_after_empty"], ago(days)))
        else:
            self.conn.execute(
                "INSERT INTO health(source_id, ok_at, err, err_at, fails,"
                " last_count, empty, empty_at, fail_since)"
                " VALUES (?,'','HTTP 404',?,40,0,0,'',?)",
                (source_id, ago(1), ago(days)))
        self.conn.commit()


class HealthCase(Case):
    """Сколько недель источника нет — вопрос о времени, а не о числе попыток."""

    def test_first_failure_starts_the_clock(self):
        sources.mark_health(self.conn, VICTIM, False, "HTTP 404")
        first = self.conn.execute("SELECT fail_since FROM health WHERE source_id=?",
                                  (VICTIM,)).fetchone()["fail_since"]
        self.assertTrue(first)
        sources.mark_health(self.conn, VICTIM, False, "HTTP 404")
        again = self.conn.execute("SELECT fail_since, fails FROM health "
                                  "WHERE source_id=?", (VICTIM,)).fetchone()
        self.assertEqual(again["fail_since"], first)   # счёт от ПЕРВОГО сбоя
        self.assertEqual(again["fails"], 2)

    def test_a_success_stops_the_clock(self):
        sources.mark_health(self.conn, VICTIM, False, "HTTP 404")
        sources.mark_health(self.conn, VICTIM, True, count=3, total=5)
        row = self.conn.execute("SELECT fail_since, fails FROM health "
                                "WHERE source_id=?", (VICTIM,)).fetchone()
        self.assertEqual(row["fail_since"], "")
        self.assertEqual(row["fails"], 0)

    def test_thirty_failures_in_one_day_are_not_a_month(self):
        """`fails` считает попытки: при частых перезапусках их набегает много."""
        for _ in range(30):
            sources.mark_health(self.conn, VICTIM, False, "HTTP 404")
        row = self.conn.execute("SELECT * FROM health WHERE source_id=?",
                                (VICTIM,)).fetchone()
        self.assertEqual(sources.silent_days(row)[0], 0)
        self.assertEqual(sources.stale_rows(self.conn), [])

    def test_an_empty_feed_is_silent_too(self):
        """Двухсотка с нулём записей — то же выпадение из выпуска, только тихое."""
        self.silent(days=30, empty=True)
        row = self.conn.execute("SELECT * FROM health WHERE source_id=?",
                                (VICTIM,)).fetchone()
        days, why, _since = sources.silent_days(row)
        self.assertEqual((days >= 29, why), (True, "отвечает пустотой"))

    def test_a_blog_that_posts_rarely_is_not_silent(self):
        """Пустых обходов мало — это тихая неделя, а не поломка."""
        self.conn.execute(
            "INSERT INTO health(source_id, ok_at, err, err_at, fails, last_count,"
            " empty, empty_at, fail_since) VALUES (?,?,'','',0,0,1,?,'')",
            (VICTIM, ago(20), ago(20)))
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM health WHERE source_id=?",
                                (VICTIM,)).fetchone()
        self.assertEqual(sources.silent_days(row)[0], 0)


class ArchiveCase(Case):
    def test_long_silence_leaves_the_rotation(self):
        self.silent(days=30)
        gone = sources.archive_stale(self.conn)
        self.assertEqual([r["source_id"] for r in gone], [VICTIM])
        alive = {f[0] for f in PROFILES[TOPIC]["feeds"]}
        self.assertNotIn(VICTIM, alive)          # больше не опрашивается

    def test_the_archive_keeps_what_is_needed_to_come_back(self):
        was = next(f for f in PROFILES[TOPIC]["feeds"] if f[0] == VICTIM)
        self.silent(days=30)
        sources.archive_stale(self.conn)
        row = storage.archive_rows(self.conn)[0]
        self.assertEqual((row["source_id"], row["topic"], row["url"],
                          row["tier"], row["category"]),
                         (VICTIM, TOPIC, was[1], was[2], was[3]))
        self.assertIn("не отвечает", row["reason"])

    def test_a_short_silence_is_left_alone(self):
        self.silent(days=CFG["archive_after_days"] - 1)
        self.assertEqual(sources.archive_stale(self.conn), [])
        self.assertIn(VICTIM, {f[0] for f in PROFILES[TOPIC]["feeds"]})

    def test_zero_days_turns_archiving_off(self):
        self.silent(days=400)
        was = CFG["archive_after_days"]
        CFG["archive_after_days"] = 0
        try:
            self.assertEqual(sources.archive_stale(self.conn), [])
        finally:
            CFG["archive_after_days"] = was

    def test_health_is_forgotten_so_the_count_starts_over(self):
        self.silent(days=30)
        sources.archive_stale(self.conn)
        row = self.conn.execute("SELECT COUNT(*) n FROM health "
                                "WHERE source_id=?", (VICTIM,)).fetchone()
        self.assertEqual(row["n"], 0)


class RestoreCase(Case):
    """Одна команда: пройтись по архиву, увидеть ожившее и вернуть его."""

    def answers(self, alive_url):
        def fake(src):
            source_id, url, _tier, _category = src
            if url == alive_url:
                return src, [{"title": "t"}] * 4, 17, ""
            return src, [], 0, "HTTP 404"
        return fake

    def run_cli(self, restore=False):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.check_archive(restore)
        self.assertEqual(code, 0)
        return out.getvalue()

    def archived(self):
        self.silent(days=30)
        sources.archive_stale(self.conn)
        return storage.archive_rows(self.conn)[0]

    def test_empty_archive_says_so(self):
        cli.fetch_source = self.answers("никто")
        self.assertIn("Архив пуст", self.run_cli())

    def test_still_dead_is_reported_but_not_restored(self):
        self.archived()
        cli.fetch_source = self.answers("никто")
        text = self.run_cli(restore=True)
        self.assertIn("Ожившего нет", text)
        self.assertNotIn(VICTIM, {f[0] for f in PROFILES[TOPIC]["feeds"]})

    def test_revived_feed_is_offered_but_not_written(self):
        row = self.archived()
        cli.fetch_source = self.answers(row["url"])
        text = self.run_cli()
        self.assertIn("Ожило: 1", text)
        self.assertNotIn(VICTIM, {f[0] for f in PROFILES[TOPIC]["feeds"]})

    def test_restore_puts_it_back_where_it_was(self):
        row = self.archived()
        cli.fetch_source = self.answers(row["url"])
        self.run_cli(restore=True)
        feeds = {f[0]: f for f in PROFILES[TOPIC]["feeds"]}
        self.assertIn(VICTIM, feeds)
        self.assertEqual(feeds[VICTIM][1], row["url"])
        self.assertEqual(storage.archive_rows(self.conn), [])

    def test_the_registry_still_knows_a_restored_source(self):
        """Имя — ключ к классу и доверию: архив не должен его потерять."""
        was = trust.meta(VICTIM)
        row = self.archived()
        cli.fetch_source = self.answers(row["url"])
        self.run_cli(restore=True)
        now = trust.meta(VICTIM)
        self.assertEqual((now["kind"], now["wire"]), (was["kind"], was["wire"]))

    def test_a_new_address_from_the_replacements_revives_it(self):
        """Лента могла переехать уже после того, как уехала в архив."""
        from newsdigest import candidates
        row = self.archived()
        moved = candidates.REPLACEMENTS[VICTIM][0][0]
        cli.fetch_source = self.answers(moved)
        self.run_cli(restore=True)
        feeds = {f[0]: f for f in PROFILES[TOPIC]["feeds"]}
        self.assertEqual(feeds[VICTIM][1], moved)

    def test_the_flags_are_wired(self):
        args = cli.build_parser().parse_args(["feeds", "--archive", "--restore"])
        self.assertTrue(args.archive and args.restore)


class NoticeCase(Case):
    """Про архив надо сказать: иначе раздел незаметно останется без источника."""

    def test_owner_is_told_what_left_and_why(self):
        self.silent(days=30)
        sent = []
        real_send, real_chat = daemon.tg_send, daemon.config.TG_CHAT
        daemon.tg_send = lambda chat, text, **kw: sent.append((chat, text))
        daemon.config.TG_CHAT = "777"
        try:
            daemon.retire_silent()
        finally:
            daemon.tg_send, daemon.config.TG_CHAT = real_send, real_chat
        self.assertEqual(len(sent), 1)
        self.assertIn(VICTIM, sent[0][1])
        self.assertIn("не отвечает", sent[0][1])
        self.assertIn("feeds --archive", sent[0][1])

    def test_nothing_to_say_nothing_sent(self):
        sent = []
        real_send, real_chat = daemon.tg_send, daemon.config.TG_CHAT
        daemon.tg_send = lambda chat, text, **kw: sent.append(text)
        daemon.config.TG_CHAT = "777"
        try:
            daemon.retire_silent()
        finally:
            daemon.tg_send, daemon.config.TG_CHAT = real_send, real_chat
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
