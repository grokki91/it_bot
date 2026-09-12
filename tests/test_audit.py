# -*- coding: utf-8 -*-
"""Аудит показа новостей: отчёт должен собираться на живой схеме.

Смысл теста один: запросы аудита ходят по всем таблицам сразу, и разойтись со
схемой они могут молча — отчёт просто не напечатает раздел. Поэтому здесь
заполняется база всех видов записями и проверяется, что отчёт дошёл до конца.
"""
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import storage  # noqa: E402
from newsdigest.textutil import signature  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "nd_audit", os.path.join(ROOT, "tools", "audit.py"))
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def ago(hours):
    return (datetime.now(timezone.utc)
            - timedelta(hours=hours)).isoformat(timespec="seconds")


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.conn = storage.db()
        for table in ("items", "sent", "feedback", "health", "runs", "leftover",
                      "claims"):
            self.conn.execute("DELETE FROM %s" % table)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def seed(self):
        conn = self.conn
        for at in range(8):
            title = "Событие %d: чипы, модели и деньги" % at
            conn.execute(
                "INSERT INTO items(url_hash,url,source_id,tier,category,title,"
                "summary,published_at,fetched_at,sig,social,state,section,"
                "route_conf,safe,safe_why) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("h%d" % at, "https://example.com/%d" % at,
                 "theverge" if at % 2 else "arstechnica", 2, "media", title,
                 "текст заметки", ago(at + 2), ago(at), signature(title), 0.0,
                 "new", "ai" if at % 2 else "", 0.7 if at % 2 else 0.0,
                 "unsafe" if at == 3 else "ok", "чужой домен" if at == 3 else ""))
            conn.execute(
                "INSERT INTO sent(chat_id,url_hash,sig,title,url,source_id,"
                "category,section,headline,summary,caveat,score,breaking,"
                "digest_date,sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("777", "h%d" % at, signature(title), title,
                 "https://example.com/%d" % at,
                 "theverge" if at % 2 else "arstechnica", "media", "ai",
                 "Карточка %d" % at, "суть", "препринт" if at == 1 else "",
                 5.0 + at * 0.5, 0, "2026-01-01", ago(at * 6)))
        conn.execute("INSERT INTO feedback(chat_id,url_hash,verdict,source_id,"
                     "category,title,at) VALUES ('777','h1','up','theverge',"
                     "'media','t',?)", (ago(5),))
        conn.execute("INSERT INTO feedback(chat_id,url_hash,verdict,source_id,"
                     "category,title,at) VALUES ('777','h2','down','arstechnica',"
                     "'media','t',?)", (ago(5),))
        conn.execute("INSERT INTO health(source_id,ok_at,err,err_at,fails,"
                     "last_count,empty,empty_at) VALUES ('tass',?,'HTTP 404',?,"
                     "7,0,0,'')", (ago(40), ago(2)))
        conn.execute("INSERT INTO health(source_id,ok_at,err,err_at,fails,"
                     "last_count,empty,empty_at) VALUES ('lenta',?,'',''"
                     ",0,0,9,?)", (ago(3), ago(300)))
        conn.execute("INSERT INTO runs(kind,at,status,stats) VALUES "
                     "('digest',?,'ok','{\"cost\": 0.02, \"candidates\": 90,"
                     " \"selected\": 7}')", (ago(6),))
        conn.execute("INSERT INTO leftover(chat_id,url_hash,title,url,source_id,"
                     "category,score,at,shown) VALUES ('777','hx','t','u',"
                     "'theverge','media',7.4,?,0)", (ago(6),))
        conn.execute("INSERT INTO claims(sig,verdict,note,at) VALUES "
                     "('s1','hold','один источник',?)", (ago(4),))
        conn.commit()

    def run_audit(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = audit.main(["--db", str(storage.DB_FILE)] + list(args))
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_report_reaches_the_end(self):
        """Отчёт должен дойти до последнего раздела на заполненной базе."""
        self.seed()
        text = self.run_audit("--days", "7", "--examples")
        for mark in ("Источники: отвечают ли", "чем они взвешены",
                     "собрано → показано", "Ранжирование", "реакций читателя",
                     "свежесть и повторы", "Разделы и маршрутизация",
                     "Достоверность показанного", "Прогоны"):
            self.assertIn(mark, text)
        self.assertNotIn("пропускаю:", text)   # ни один запрос не разошёлся со схемой
        self.assertIn("tass", text)            # сбоящий источник виден
        self.assertIn("lenta", text)           # и молчащий тоже

    def test_empty_database(self):
        """Пустая база — не повод падать: у нового сервера она такая."""
        text = self.run_audit("--days", "30")
        self.assertIn("Прогоны", text)
        self.assertNotIn("Traceback", text)

    def test_chat_id_is_not_printed(self):
        """Номера чатов в отчёт не уходят — он делается для показа наружу."""
        self.seed()
        text = self.run_audit("--days", "7", "--examples")
        self.assertNotIn("777", text)
        self.assertIn("чат-1", text)

    def test_missing_database(self):
        code = audit.main(["--db", os.path.join(ROOT, "нет-такой-базы.db")])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
