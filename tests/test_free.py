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
        self.conn.commit()
        spend._pending.clear()
        self.saved = (llm.post_json, llm.time.time, config.DS_KEY, config.OR_KEY,
                      dict(CFG))
        config.DS_KEY, config.OR_KEY = "test-key", "or-test-key"
        CFG.update(free_kinds="classify,translate", free_models="m1:free",
                   llm_retries=1)
        llm._free_off.clear()
        llm._told.clear()
        llm._no_reasoning = llm._no_thinking = False
        self.now = EVENING
        llm.time.time = lambda: self.now
        self.calls = []
        self.free = [reply([{"id": 0, "section": "it"}])]
        self.paid = [reply([{"id": 0, "section": "science"}])]
        llm.post_json = self.fake

    def tearDown(self):
        (llm.post_json, llm.time.time, config.DS_KEY, config.OR_KEY, cfg) = self.saved
        CFG.clear()
        CFG.update(cfg)
        spend._pending.clear()
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
