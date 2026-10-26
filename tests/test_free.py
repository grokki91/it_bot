# -*- coding: utf-8 -*-
"""Бесплатный маршрут: запрос сначала в OpenRouter, при любом сбое — в DeepSeek.

`llm.post_json` подменяется с развилкой по адресу, время — через `llm.time.time`.
"""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import (cli, config, llm, newsfeed, redact, spend,  # noqa: E402
                        storage, translate)
from newsdigest.config import CFG  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402

FREE = "openrouter.ai"
PAID = "api.deepseek.com"
#: полночь UTC и час до неё
MIDNIGHT = 20000 * 86400
EVENING = MIDNIGHT - 3600


def reply(items, usage=None):
    return 200, {"choices": [{"message": {"content": json.dumps({"items": items})}}],
                 "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 50}}, ""


class FreeCase(unittest.TestCase):
    def setUp(self):
        self.conn = storage.db()
        self.conn.execute("DELETE FROM spend")
        self.conn.execute("DELETE FROM shadow")
        self.conn.commit()
        spend._pending.clear()
        spend._shadow.clear()
        self.saved = (llm.post_json, llm.time.time, config.DS_KEY, config.OR_KEY,
                      dict(CFG))
        config.DS_KEY, config.OR_KEY = "test-key", "or-test-key"
        CFG.update(free_kinds="classify,translate", free_models="m1:free",
                   free_shadow="", llm_retries=1)
        llm._free_off.clear()
        llm._shadow_off.clear()
        llm._told.clear()
        llm._no_reasoning = llm._no_thinking = False
        llm._shadow_used[:] = ["", 0]
        self.now = EVENING
        llm.time.time = lambda: self.now
        self.calls = []
        self.free = [reply([{"id": 0, "section": "it"}])]
        self.paid = [reply([{"id": 0, "section": "science"}])]
        llm.post_json = self.fake

    def tearDown(self):
        llm._shadow_jobs.join()     # сверка в фоне не должна уйти в настоящую сеть
        (llm.post_json, llm.time.time, config.DS_KEY, config.OR_KEY, cfg) = self.saved
        CFG.clear()
        CFG.update(cfg)
        spend._pending.clear()
        spend._shadow.clear()
        self.conn.close()

    def fake(self, url, payload, headers, timeout):
        route = "free" if FREE in url else "paid"
        self.calls.append((route, json.loads(json.dumps(payload))))
        answers = self.free if route == "free" else self.paid
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def routes(self):
        return [route for route, _payload in self.calls]

    def ask(self, kind="classify", expect=(0,), **kw):
        return llm.llm_json("s", "u", "deepseek-v4-flash", kind=kind,
                            expect=expect, **kw)

    def row(self, kind="classify"):
        found = self.conn.execute("SELECT * FROM spend WHERE kind=?", (kind,)).fetchone()
        return dict(found) if found else {}


class TestRouting(FreeCase):
    def test_no_key_means_deepseek_as_before(self):
        config.OR_KEY = ""
        self.ask()
        self.assertEqual(self.routes(), ["paid"])
        payload = self.calls[0][1]
        self.assertEqual(set(payload), {"model", "messages", "max_tokens",
                                        "temperature", "stream",
                                        "response_format", "thinking"})
        self.assertNotIn("reasoning", payload)

    def test_kind_not_in_free_kinds_goes_to_deepseek(self):
        self.ask(kind="rank")
        self.assertEqual(self.routes(), ["paid"])

    def test_full_free_answer_is_taken(self):
        data, _used = self.ask()
        self.assertEqual(self.routes(), ["free"])
        self.assertEqual(data["items"][0]["section"], "it")
        payload = self.calls[0][1]
        self.assertEqual(payload["model"], "m1:free")
        self.assertNotIn("thinking", payload)
        self.assertEqual(payload["reasoning"], {"enabled": False})
        row = self.row()
        self.assertEqual((row["free_calls"], row["calls"], row["cost"]), (1, 0, 0))

    def test_incomplete_answer_falls_back_without_pause(self):
        data, _used = self.ask(expect=(0, 1))
        self.assertEqual(self.routes(), ["free", "paid"])
        self.assertEqual(data["items"][0]["section"], "science")
        self.assertEqual(self.row()["fallbacks"], 1)
        self.ask(expect=(0,))
        self.assertEqual(self.routes()[-1], "free")

    def test_daily_limit_rests_until_midnight(self):
        self.free = [(429, {"error": {"message": "Rate limit exceeded: free-models-per-day"}}, "")]
        self.ask()
        self.assertEqual(self.routes(), ["free", "paid"])
        self.now = MIDNIGHT - 1
        self.ask()
        self.assertEqual(self.routes(), ["free", "paid", "paid"])
        self.now = MIDNIGHT + 1
        self.free = [reply([{"id": 0, "section": "it"}])]
        self.ask()
        self.assertEqual(self.routes()[-1], "free")

    def test_rate_limit_tries_next_model(self):
        CFG["free_models"] = "m1:free,m2:free"
        self.free = [(429, {"error": {"message": "slow down"}}, ""),
                     reply([{"id": 0, "section": "it"}])]
        self.ask()
        self.assertEqual([p["model"] for r, p in self.calls if r == "free"],
                         ["m1:free", "m2:free"])
        self.ask()
        self.assertEqual(self.calls[-1][1]["model"], "m2:free")
        self.now += 61
        self.ask()
        self.assertEqual(self.calls[-1][1]["model"], "m1:free")

    def test_forbidden_model_does_not_stop_next_one(self):
        CFG["free_models"] = "m1:free,m2:free"
        self.free = [(403, {"error": {"message": "only available on agentic harnesses"}}, ""),
                     reply([{"id": 0, "section": "it"}])]
        data, _ = self.ask()
        self.assertEqual(data["items"][0]["section"], "it")
        self.ask()
        self.assertEqual([p["model"] for r, p in self.calls if r == "free"],
                         ["m1:free", "m2:free", "m2:free"])

    def test_no_models_means_deepseek_without_fallback(self):
        CFG["free_models"] = ""
        self.ask()
        self.assertEqual(self.routes(), ["paid"])
        self.assertFalse(self.row().get("fallbacks"))

    def test_silent_model_rests(self):
        self.free = [(0, None, "timeout")]
        self.ask()
        self.assertEqual(self.routes(), ["free", "paid"])
        self.now += CFG["free_pause_min"] * 60 - 1
        self.ask()
        self.assertEqual(self.routes()[-1], "paid")
        self.now += 2
        self.ask()
        self.assertEqual(self.routes()[-2:], ["free", "paid"])

    def test_bad_key_turns_route_off_with_one_warning(self):
        self.free = [(401, {"error": {"message": "No auth"}}, "")]
        with self.assertLogs(config.log, "WARNING") as logs:
            self.ask()
            self.ask()
        self.assertEqual(self.routes(), ["free", "paid", "paid"])
        self.assertEqual(sum("OpenRouter" in line for line in logs.output), 1)

    def test_strict_free_route_never_pays(self):
        self.free = [(503, None, "down")]
        with self.assertRaises(LLMError):
            self.ask(route="free")
        self.assertEqual(self.routes(), ["free"])

    def test_reasoning_rejected_is_dropped(self):
        self.free = [(400, {"error": {"message": "reasoning is not supported"}}, ""),
                     reply([{"id": 0, "section": "it"}])]
        self.ask()
        self.assertEqual(self.routes(), ["free", "free"])
        self.assertNotIn("reasoning", self.calls[1][1])


class TestShadow(FreeCase):
    """Сверка: в работу идёт DeepSeek, бесплатная отвечает в фоне, в базу — счётчики."""

    def setUp(self):
        super().setUp()
        CFG.update(free_shadow="rank,urgency,dupes,claims,cards", min_score=5.5)
        self.paid = [reply([{"id": 0, "score": 8}, {"id": 1, "score": 4}])]
        self.free = [reply([{"id": 0, "score": 7.5}, {"id": 1, "score": 6}])]

    def rank(self):
        data, _used = self.ask(kind="rank", expect=(0, 1))
        llm._shadow_jobs.join()
        return data

    def shadow(self, kind="rank"):
        spend.flush()
        found = self.conn.execute("SELECT asks, failed, items, agree, risky "
                                  "FROM shadow WHERE kind=?", (kind,)).fetchone()
        return tuple(found) if found else None

    def test_deepseek_answer_is_compared_in_background(self):
        data = self.rank()
        self.assertEqual(data["items"][0]["score"], 8)
        self.assertEqual(self.routes(), ["paid", "free"])
        self.assertNotIn("thinking", self.calls[1][1])
        # id 0: 8 против 7.5 — совпало; id 1: 4 против 6 — по разные стороны порога
        self.assertEqual(self.shadow(), (1, 0, 2, 1, 1))
        row = self.row("rank")
        self.assertEqual((row["calls"], row["free_calls"], row["fallbacks"]), (1, 0, 0))

    def test_not_compared_without_model_or_for_text_and_delegated_kinds(self):
        base = dict(CFG)
        self.free = [reply([{"id": 0, "section": "it"}])]
        for kind, cfg, route in (("rank", {"free_models": ""}, "paid"),
                                 ("cards", {}, "paid"),
                                 ("rank", {"free_shadow": ""}, "paid"),
                                 # уже идёт бесплатно — сверять не с чем
                                 ("classify", {"free_shadow": "classify"}, "free")):
            with self.subTest(kind=kind, cfg=cfg):
                self.calls.clear()
                CFG.update(base, **cfg)
                self.ask(kind=kind)
                llm._shadow_jobs.join()
                self.assertEqual(self.routes(), [route])
                self.assertIsNone(self.shadow(kind))

    def test_unusable_free_answer_is_counted_as_failed(self):
        self.free = [reply([{"id": 0, "score": 7.5}])]
        self.rank()
        self.assertEqual(self.shadow(), (1, 1, 0, 0, 0))
        self.assertFalse(self.row("rank")["fallbacks"])

    def test_daily_cap_survives_restart(self):
        CFG["free_shadow_max"] = 2
        for _ in range(3):
            self.rank()
        self.assertEqual(self.routes(), ["paid", "free", "paid", "free", "paid"])
        llm._shadow_used[:] = ["", 0]          # как после перезапуска
        self.rank()
        self.assertEqual(self.routes()[-1], "paid")
        self.assertEqual(self.shadow()[0], 2)

    def test_resting_route_is_not_asked(self):
        llm._free_off["*"] = MIDNIGHT
        self.rank()
        self.assertEqual(self.routes(), ["paid"])
        self.assertIsNone(self.shadow())

    def test_daily_limit_hit_by_shadow_pauses_route(self):
        self.free = [(429, {"error": {"message": "free-models-per-day"}}, "")]
        self.rank()
        self.rank()
        self.assertEqual(self.routes(), ["paid", "free", "paid"])
        self.assertEqual(self.shadow(), (1, 1, 0, 0, 0))

    def test_shadow_failure_does_not_stop_route(self):
        self.free = [(400, {"error": {"message": "context length exceeded"}}, ""),
                     reply([{"id": 0, "section": "it"}])]
        self.rank()
        self.assertEqual(self.shadow(), (1, 1, 0, 0, 0))
        self.assertNotIn("model:m1:free", llm._told)   # «уберите её» — не про сверку
        data, _used = self.ask()               # разделы по-прежнему бесплатно
        self.assertEqual(data["items"][0]["section"], "it")
        self.rank()                            # а сверка ранжирования отдыхает
        self.assertEqual(self.routes(), ["paid", "free", "free", "paid"])

    def test_full_queue_drops_extra_jobs(self):
        entered, release = threading.Event(), threading.Event()
        plain = self.fake

        def slow(url, payload, headers, timeout):
            if FREE in url:
                entered.set()
                release.wait(5)
            return plain(url, payload, headers, timeout)
        llm.post_json = slow
        self.ask(kind="rank", expect=(0, 1))
        self.assertTrue(entered.wait(5))       # нить сверки занята первым
        for _ in range(5):                     # четыре ждут, пятый лишний
            self.ask(kind="rank", expect=(0, 1))
        release.set()
        llm._shadow_jobs.join()
        self.assertEqual(self.routes().count("free"), 5)
        self.assertEqual(self.shadow()[0], 5)

    def test_compare_by_kind(self):
        CFG.update(breaking_alert_score=7.5, breaking_flash_score=9.0)

        def items(*rows):
            return {"items": [dict(row, id=idx) for idx, row in enumerate(rows)]}
        cases = (
            # «нового нет» против «есть» — опасно; same против less — нет
            ("dupes", items({"news": "same"}, {"news": "same"}, {"news": "less"}),
             items({"news": "more"}, {"news": "less"}, {"news": "LESS"}), (3, 1, 1)),
            ("claims", items({"verdict": "hold"}, {"verdict": "ok"}),
             items({"verdict": "ok"}, {"verdict": "caveat"}), (2, 0, 1)),
            # 9.5 и 9.1 — обе молнии; 7.0 и 8.0 — по разные стороны 🔔
            ("urgency", items({"urgency": 9.5}, {"urgency": 7.0}),
             items({"urgency": 9.1}, {"urgency": 8.0}), (2, 1, 1)),
            # неуверенный ответ — «раздела нет»
            ("classify", items({"section": "ai", "confidence": 0.3},
                               {"section": "ai", "confidence": 0.9}),
             items({"section": "", "confidence": 0}, {"section": "it", "confidence": 1}),
             (2, 1, 0)),
            # бесплатная промолчала или ответила неразборчиво — опасно
            ("rank", items({"score": 6}, {"score": 7}, {"score": "?"}),
             {"items": [{"id": 0, "score": "high"}]}, (2, 0, 2)),
        )
        for kind, paid, free, expected in cases:
            with self.subTest(kind=kind):
                self.assertEqual(llm.compare(kind, paid, free), expected)


class TestTranslate(FreeCase):
    def test_english_from_free_model_goes_to_deepseek(self):
        self.free = [reply([{"id": 0, "text": "Apple ships new chip"}])]
        self.paid = [reply([{"id": 0, "text": "Apple выпустила новый чип"}])]
        conn = storage.db()
        conn.execute("DELETE FROM translations")
        conn.commit()
        ready, _cost = translate.translated(conn, ["Apple ships new chip"])
        conn.close()
        self.assertEqual(self.routes(), ["free", "paid"])
        self.assertEqual(ready["Apple ships new chip"], "Apple выпустила новый чип")

    def test_feed_page_never_goes_free(self):
        self.paid = [reply([{"id": 0, "text": "Новый чип Apple"}])]
        conn = storage.db()
        conn.execute("DELETE FROM translations")
        conn.commit()
        newsfeed._silent_until = 0.0
        newsfeed.ask_model(conn, ["New Apple chip"])
        conn.close()
        self.assertEqual(self.routes(), ["paid"])


class TestThinking(FreeCase):
    def test_rejected_thinking_warns_once_and_is_not_sent_again(self):
        config.OR_KEY = ""
        CFG["llm_retries"] = 2
        self.paid = [(400, {"error": {"message": "unknown field thinking"}}, ""),
                     reply([], {"prompt_tokens": 10, "completion_tokens": 900,
                                "completion_tokens_details": {"reasoning_tokens": 800}})]
        with self.assertLogs(config.log, "WARNING") as logs:
            self.ask(kind="rank")
            self.ask(kind="rank")
        self.assertEqual(sum("thinking" in line for line in logs.output), 1)
        self.assertNotIn("thinking", self.calls[-1][1])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.row("rank")["tok_reason"], 1600)


class TestReport(FreeCase):
    def test_status_shows_free_and_reasoning(self):
        spend.add("classify", {"in": 1000, "out": 100}, 0.0, route="free")
        spend.fallback("classify")
        spend.add("classify", {"in": 1000, "out": 100, "reason": 5000}, 0.001)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.spend_report(self.conn, "")
        text = out.getvalue()
        self.assertIn("бесплатно 1, откатов 1", text)
        self.assertIn("рассуждения 5k", text)
        self.assertIn("сэкономлено", text)
        self.assertIn("DeepSeek рассуждает", text)


class TestShadowReport(FreeCase):
    def test_status_gives_verdict_and_env_line(self):
        CFG.update(free_shadow="rank,urgency,dupes,claims,classify")
        spend.shadow("rank", asks=30, failed=2, items=250, agree=240, risky=2)
        spend.shadow("urgency", asks=5, items=50, agree=50)
        spend.shadow("dupes", asks=30, items=300, agree=200, risky=3)
        spend.shadow("claims", asks=30, items=300, agree=295, risky=9)
        spend.shadow("classify", asks=30, items=300, agree=300)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.shadow_report(self.conn)
        lines = {line.split()[0]: line for line in out.getvalue().splitlines()
                 if line.startswith("  ")}
        self.assertIn("сверено 250, совпало 96%, опасных 0.8%", lines["ранжирование"])
        self.assertIn("(запросов 30, брак 2) → можно включить", lines["ранжирование"])
        self.assertIn("→ рано", lines["срочное"])
        self.assertIn("→ не включать", lines["дубли"])
        self.assertIn("→ не включать", lines["фактчек"])    # 3% опасных
        self.assertIn("→ уже бесплатно", lines["разделы"])
        self.assertIn("ND_FREE_KINDS=classify,translate,rank", lines["Включить:"])

    def test_silent_without_route_and_history(self):
        config.OR_KEY = ""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.shadow_report(self.conn)
        self.assertEqual(out.getvalue(), "")

    def test_old_base_without_table(self):
        conn = sqlite3.connect(":memory:")
        self.assertEqual(spend.shadow_totals(conn), [])
        conn.close()


class TestDoctor(FreeCase):
    def test_empty_models_hint_lists_free_ids(self):
        CFG["free_models"] = ""
        listing = {"data": [{"id": "a/x:free"}, {"id": "b/paid"}, {"id": "c/y:free"}]}

        def fake_get(url, timeout=None, ua=None, headers=None):
            if url.endswith("/models"):
                return 200, json.dumps(listing).encode()
            return 200, b'{"data": {}}'
        saved, cli.http_get = cli.http_get, fake_get
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                cli.free_doctor()
        finally:
            cli.http_get = saved
        text = out.getvalue()
        self.assertIn("ND_FREE_MODELS не задан", text)
        self.assertIn("a/x:free, c/y:free", text)
        self.assertNotIn("b/paid", text)
        self.assertEqual(self.calls, [])


class TestSecretAndSchema(unittest.TestCase):
    def test_openrouter_key_is_masked_after_load_env(self):
        saved = (config.TG_TOKEN, config.TG_CHAT, config.DS_KEY, config.OR_KEY,
                 config.SB_KEY, dict(CFG))
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-0123456789abcdef0123"  # nd-redact: allow
        try:
            config.load_env()
            self.assertNotIn("0123456789abcdef0123",
                             redact.scrub("key sk-or-v1-0123456789abcdef0123"))  # nd-redact: allow
        finally:
            del os.environ["OPENROUTER_API_KEY"]
            redact.forget()
            (config.TG_TOKEN, config.TG_CHAT, config.DS_KEY, config.OR_KEY,
             config.SB_KEY, cfg) = saved
            CFG.clear()
            CFG.update(cfg)

    def test_old_spend_table_gets_new_columns(self):
        path = os.path.join(tempfile.mkdtemp(prefix="ndold-"), "old.db")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE spend (day TEXT NOT NULL, kind TEXT NOT NULL, "
                     "calls INTEGER NOT NULL DEFAULT 0, tok_in INTEGER NOT NULL DEFAULT 0, "
                     "tok_cached INTEGER NOT NULL DEFAULT 0, tok_out INTEGER NOT NULL DEFAULT 0, "
                     "cost REAL NOT NULL DEFAULT 0, PRIMARY KEY (day, kind))")
        conn.execute("INSERT INTO spend VALUES ('2026-10-01', 'rank', 3, 30, 10, 5, 0.5)")
        conn.commit()
        storage.add_free_spend(conn)
        row = dict(conn.execute("SELECT * FROM spend").fetchone())
        conn.close()
        self.assertEqual((row["calls"], row["cost"]), (3, 0.5))
        for column in ("tok_reason", "free_calls", "free_in", "free_out", "fallbacks"):
            self.assertEqual(row[column], 0)


if __name__ == "__main__":
    unittest.main()
