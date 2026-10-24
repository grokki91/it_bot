# -*- coding: utf-8 -*-
"""Журнал расхода на модель: пишется из единственной двери к ней, не тормозит
запрос и показывается в `status` по назначениям."""
import contextlib
import io
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import classify, cli, config, llm, spend, storage  # noqa: E402
from newsdigest.config import CFG  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402


class SpendCase(unittest.TestCase):
    def setUp(self):
        self.conn = storage.db()
        self.conn.execute("DELETE FROM spend")
        self.conn.commit()
        spend._pending.clear()
        self.saved = (llm.post_json, config.DS_KEY)
        config.DS_KEY = "test-key"

    def tearDown(self):
        llm.post_json, config.DS_KEY = self.saved
        spend._pending.clear()
        self.conn.close()

    def answer(self, content='{"items": []}', usage=None):
        usage = usage if usage is not None else {
            "prompt_tokens": 2000, "completion_tokens": 100,
            "prompt_cache_hit_tokens": 1500}

        def fake(url, payload, headers, timeout):
            return 200, {"choices": [{"message": {"content": content}}],
                         "usage": usage}, ""
        llm.post_json = fake

    def rows(self):
        return {row["kind"]: dict(row) for row in self.conn.execute(
            "SELECT * FROM spend")}


class TestRecording(SpendCase):
    def test_every_request_lands_in_the_journal(self):
        self.answer()
        llm.llm_json("s", "u", "model", kind="rank")
        llm.llm_json("s", "u", "model", kind="rank")
        llm.llm_json("s", "u", "model", kind="cards")
        rows = self.rows()
        self.assertEqual(rows["rank"]["calls"], 2)
        self.assertEqual(rows["rank"]["tok_in"], 4000)
        self.assertEqual(rows["rank"]["tok_cached"], 3000)
        self.assertEqual(rows["rank"]["tok_out"], 200)
        self.assertEqual(rows["cards"]["calls"], 1)

    def test_cost_matches_the_price_list(self):
        self.answer()
        llm.llm_json("s", "u", "model", kind="rank")
        usage = {"in": 2000, "cached": 1500, "out": 100}
        self.assertAlmostEqual(self.rows()["rank"]["cost"], llm.llm_cost(usage))

    def test_unlabelled_request_is_other(self):
        self.answer()
        llm.llm_json("s", "u", "model")
        self.assertIn("other", self.rows())

    def test_unparsable_answer_is_still_paid_for(self):
        """Деньги ушли, даже если ответ не разобрался: учёт идёт до разбора."""
        self.answer(content="не сегодня")
        with self.assertRaises(LLMError):
            llm.llm_json("s", "u", "model", kind="cards")
        self.assertEqual(self.rows()["cards"]["calls"], 1)

    def test_failed_request_costs_nothing(self):
        llm.post_json = lambda url, payload, headers, timeout: (401, {}, "no")
        with self.assertRaises(LLMError):
            llm.llm_json("s", "u", "model", kind="cards")
        self.assertEqual(self.rows(), {})

    def test_every_caller_is_labelled(self):
        """Вызов без метки попал бы в «прочее», и разбивка перестала бы
        показывать, куда уходят деньги."""
        seen, expected = [], []
        real, real_classify = llm.llm_json, classify.llm_json

        def spy(system, user, model, max_tokens=3000, kind="", **kwargs):
            seen.append(kind)
            expected.append(list(kwargs.get("expect", ())))
            return {"items": []}, {"in": 0, "out": 0, "cached": 0}
        llm.llm_json = classify.llm_json = spy
        group = [{"title": "t", "summary": "s", "source_id": "src", "tier": 1,
                  "published_at": "", "url": "https://example.com/a"}]
        try:
            llm.rank_clusters([group], "p")
            llm.rate_urgency([group], "p")
            llm.judge_duplicates([("a", "b")])
            llm.judge_claims([{"title": "t"}])
            llm.summarize_batch([(group, 5, "")], "p", "русский", offset=3)
            llm.translate_texts(["x"], "русский")
            classify.ask_model([{"title": "t", "summary": "s"}], ["it"])
        finally:
            llm.llm_json, classify.llm_json = real, real_classify
        self.assertEqual(seen, ["rank", "urgency", "dupes", "claims",
                                "cards", "translate", "classify"])
        # без `expect` бесплатный ответ не с чем сверить на полноту
        self.assertEqual(expected, [[0], [0], [0], [0], [3], [0], [0]])
        known = {kind for kind, _label in spend.LABELS}
        self.assertTrue(set(seen) <= known)


class TestBusyDatabase(SpendCase):
    def test_busy_database_neither_fails_nor_stalls_the_request(self):
        """Выпуск мог оставить базу занятой посреди транзакции: ждать её по
        тридцать секунд на каждый запрос к модели нельзя."""
        other = storage.db()
        other.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            spend.add("rank", {"in": 100, "cached": 0, "out": 10}, 0.5)
            self.assertLess(time.monotonic() - started, 3.0)
            self.assertEqual(self.rows(), {})
        finally:
            other.rollback()
            other.close()
        spend.flush()                    # база свободна — накопленное уходит
        self.assertEqual(self.rows()["rank"]["calls"], 1)

    def test_nothing_is_lost_or_doubled(self):
        other = storage.db()
        other.execute("BEGIN IMMEDIATE")
        try:
            spend.add("rank", {"in": 100, "cached": 0, "out": 10}, 0.5)
            spend.add("rank", {"in": 100, "cached": 0, "out": 10}, 0.5)
        finally:
            other.rollback()
            other.close()
        spend.add("rank", {"in": 100, "cached": 0, "out": 10}, 0.5)
        row = self.rows()["rank"]
        self.assertEqual(row["calls"], 3)
        self.assertEqual(row["tok_in"], 300)
        self.assertAlmostEqual(row["cost"], 1.5)

    def test_missing_table_is_not_fatal(self):
        """База старой версии без таблицы: запрос к модели от этого не падает."""
        self.conn.execute("ALTER TABLE spend RENAME TO spend_off")
        self.conn.commit()
        try:
            spend.add("rank", {"in": 1, "cached": 0, "out": 1}, 0.1)
        finally:
            self.conn.execute("ALTER TABLE spend_off RENAME TO spend")
            self.conn.commit()


class TestReport(SpendCase):
    def seed(self, day, kind, cost, calls=1, tok_in=1000, cached=0, tok_out=100):
        self.conn.execute(
            "INSERT INTO spend(day, kind, calls, tok_in, tok_cached, tok_out, cost)"
            " VALUES (?,?,?,?,?,?,?)", (day, kind, calls, tok_in, cached,
                                        tok_out, cost))
        self.conn.commit()

    def test_totals_are_grouped_and_ordered_by_cost(self):
        today = spend.today()
        self.seed(today, "rank", 0.02)
        self.seed(today, "cards", 0.05)
        self.seed(spend.since(7), "rank", 0.01)
        rows = spend.totals(self.conn, 7)
        self.assertEqual([r["kind"] for r in rows], ["cards", "rank"])
        self.assertAlmostEqual(rows[1]["cost"], 0.03)

    def test_old_days_are_outside_the_window(self):
        self.seed("2020-01-01", "rank", 9.0)
        self.assertEqual(spend.totals(self.conn, 7), [])

    def test_status_shows_the_breakdown(self):
        self.seed(spend.today(), "rank", 0.02, calls=7, tok_in=4000, cached=3000)
        self.seed(spend.today(), "classify", 0.01)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.spend_report(self.conn, "")
        text = out.getvalue()
        self.assertIn("$0.0300", text)
        self.assertIn("ранжирование", text)
        self.assertIn("разделы", text)
        self.assertIn("75%", text)               # доля кэша провайдера

    def test_status_extrapolates_from_the_days_it_has(self):
        """Журнал ведётся с сегодня: умножать сумму одного дня на 4.3 нельзя."""
        self.seed(spend.today(), "rank", 0.10)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.spend_report(self.conn, "")
        self.assertIn("≈ $3.00/мес", out.getvalue())

    def test_empty_journal_falls_back_and_says_so(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.spend_report(self.conn, "2020-01-01")
        self.assertIn("журнал запросов пока пуст", out.getvalue())


class TestRetention(SpendCase):
    def test_collect_cleanup_keeps_the_window(self):
        self.conn.execute(
            "INSERT INTO spend(day, kind, cost) VALUES ('2020-01-01', 'rank', 1)")
        self.conn.execute(
            "INSERT INTO spend(day, kind, cost) VALUES (?, 'rank', 1)",
            (spend.today(),))
        self.conn.commit()
        self.assertGreater(CFG["keep_spend_days"], 30)
        from newsdigest import sources
        hackernews = CFG["use_hackernews"]
        CFG["use_hackernews"] = False        # тесты в сеть не ходят
        try:
            sources.collect(topics=[])       # пустой обход: нужна только уборка
        finally:
            CFG["use_hackernews"] = hackernews
        days = [r["day"] for r in self.conn.execute("SELECT day FROM spend")]
        self.assertEqual(days, [spend.today()])


if __name__ == "__main__":
    unittest.main()
