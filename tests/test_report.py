# -*- coding: utf-8 -*-
"""Отчёт за период: он отвечает на вопрос «стало лучше или хуже»."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import cli, storage  # noqa: E402
from newsdigest.config import now_iso  # noqa: E402


class Args:
    def __init__(self, days=7):
        self.days = days


class ReportCase(unittest.TestCase):
    def setUp(self):
        conn = storage.db()
        try:
            for table in ("sent", "items", "feedback", "runs"):
                conn.execute("DELETE FROM %s" % table)
            conn.commit()
        finally:
            conn.close()

    def sent(self, section, source_id, score=8.0, breaking=0, n=1):
        conn = storage.db()
        try:
            for i in range(n):
                key = "%s-%s-%d" % (section, source_id, i)
                conn.execute(
                    "INSERT INTO sent(chat_id,url_hash,title,url,source_id,"
                    "section,score,breaking,digest_date,sent_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("1", key, "Новость", "http://x/" + key, source_id, section,
                     score, breaking, "2026-08-24", now_iso()))
            conn.commit()
        finally:
            conn.close()

    def run_report(self, days=7):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli.cmd_report(Args(days))
        return buffer.getvalue()


class TestReport(ReportCase):
    def test_empty_database_does_not_crash(self):
        text = self.run_report()
        self.assertIn("выпусков не было", text)

    def test_sections_are_counted(self):
        self.sent("ai", "openai", n=3)
        self.sent("politics", "ap-topnews", n=1)
        text = self.run_report()
        self.assertIn("ИИ и технологии", text)
        self.assertIn("всего новостей: 4", text)

    def test_silent_sections_are_named(self):
        """Раздел в подборке, но новостей не дал — это надо увидеть."""
        self.sent("ai", "openai")
        text = self.run_report()
        self.assertIn("Ни одной новости за период", text)
        self.assertIn("Космос", text)

    def test_dominant_source_is_flagged(self):
        """Один сайт занял больше четверти выпуска — перекос."""
        self.sent("ai", "openai", n=9)
        self.sent("politics", "ap-topnews", n=1)
        text = self.run_report()
        self.assertIn("больше четверти", text)

    def test_balanced_sources_are_not_flagged(self):
        for source in ("openai", "arstechnica", "theverge", "techcrunch",
                       "venturebeat", "techreview", "theregister", "quanta"):
            self.sent("ai", source)
        text = self.run_report()
        self.assertNotIn("больше четверти", text)

    def test_feedback_share(self):
        conn = storage.db()
        try:
            for n, verdict in enumerate(["up", "up", "up", "down"]):
                conn.execute("INSERT INTO feedback(chat_id,url_hash,verdict,at) "
                             "VALUES (?,?,?,?)", ("1", "f%d" % n, verdict, now_iso()))
            conn.commit()
        finally:
            conn.close()
        self.assertIn("75%", self.run_report())

    def test_routing_share_is_reported(self):
        conn = storage.db()
        try:
            for n in range(10):
                conn.execute(
                    "INSERT INTO items(url_hash,url,source_id,title,fetched_at,"
                    "section) VALUES (?,?,?,?,?,?)",
                    ("i%d" % n, "http://y/%d" % n, "openai", "T", now_iso(),
                     "ai" if n < 9 else ""))
            conn.commit()
        finally:
            conn.close()
        text = self.run_report()
        self.assertIn("раздел определён: 9 из 10", text)
        self.assertNotIn("Пополните словарь", text)   # 90% — это нормально

    def test_poor_routing_is_flagged(self):
        conn = storage.db()
        try:
            for n in range(10):
                conn.execute(
                    "INSERT INTO items(url_hash,url,source_id,title,fetched_at,"
                    "section) VALUES (?,?,?,?,?,?)",
                    ("i%d" % n, "http://y/%d" % n, "openai", "T", now_iso(),
                     "ai" if n < 5 else ""))
            conn.commit()
        finally:
            conn.close()
        self.assertIn("Пополните словарь", self.run_report())


class TestBar(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(cli.bar(0, 4), "····")
        self.assertEqual(cli.bar(1, 4), "████")
        self.assertEqual(cli.bar(0.5, 4), "██··")

    def test_out_of_range_is_clamped(self):
        self.assertEqual(cli.bar(-1, 4), "····")
        self.assertEqual(cli.bar(9, 4), "████")


if __name__ == "__main__":
    unittest.main()
