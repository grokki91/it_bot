# -*- coding: utf-8 -*-
"""Заслон для секретов: что вырезается, что остаётся и где он стоит.

Ключи в этом файле игрушечные, но по форме настоящие — иначе правила нечем
проверять. Поэтому строки с ними помечены «nd-redact: allow»: так проверка
`digest.py scrub --check` (она же в CI) понимает, что это пример.
"""
import io
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import config, llm, redact  # noqa: E402

MASK = redact.MASK


class TestPatterns(unittest.TestCase):
    """Формы, по которым секрет узнаётся без подсказки."""

    def check(self, text, gone):
        out = redact.scrub(text)
        self.assertNotIn(gone, out)
        self.assertIn(MASK, out)
        return out

    def test_url_password(self):
        out = self.check("фид https://ivan:hunter2@example.com/rss.xml",  # nd-redact: allow
                         "hunter2")                      # nd-redact: allow
        self.assertIn("https://ivan:", out)              # хост остался читаемым
        self.assertIn("@example.com/rss.xml", out)

    def test_url_key_parameter(self):
        self.check("https://api.example.com/feed?api_key=abcdef123456&topic=ai",  # nd-redact: allow
                   "abcdef123456")                       # nd-redact: allow

    def test_url_access_token_parameter(self):
        self.check("https://x.dev/rss?page=2&access_token=zzzzzzzzzzzz",  # nd-redact: allow
                   "zzzzzzzzzzzz")                       # nd-redact: allow

    def test_assignment(self):
        self.check("DEEPSEEK_API_KEY=sk-0123456789abcdefghij",  # nd-redact: allow
                   "sk-0123456789abcdefghij")            # nd-redact: allow

    def test_assignment_in_json(self):
        self.check('{"password": "hunter2xyz"}', "hunter2xyz")  # nd-redact: allow

    def test_assignment_in_russian(self):
        self.check("пароль: hunter2xyz", "hunter2xyz")   # nd-redact: allow

    def test_bearer_header(self):
        self.check("Authorization: Bearer abcdefghijklmnopqrst",  # nd-redact: allow
                   "abcdefghijklmnopqrst")               # nd-redact: allow

    def test_telegram_token(self):
        self.check("бот 123456789:AAF-abcdefghijklmnopqrstuvwxyz012345 упал",  # nd-redact: allow
                   "AAF-abcdefghijklmnopqrstuvwxyz012345")  # nd-redact: allow

    def test_github_token(self):
        self.check("ghp_abcdefghijklmnopqrstuvwxyz0123456789",  # nd-redact: allow
                   "ghp_abcdefghijklmnopqrstuvwxyz0123456789")  # nd-redact: allow

    def test_aws_and_google_keys(self):
        self.check("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE")  # nd-redact: allow
        self.check("AIza" + "b" * 35, "AIza" + "b" * 35)  # nd-redact: allow

    def test_jwt(self):
        self.check("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abcdefgh",  # nd-redact: allow
                   "eyJzdWIiOiIxMjMifQ")                  # nd-redact: allow

    def test_private_key_block(self):
        text = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nAAAA\n"
                "-----END RSA PRIVATE KEY-----")
        self.assertEqual(redact.scrub(text), MASK)

    def test_mask_is_not_masked_again(self):
        """Правила не должны наслаиваться: от маски не остаётся хвостов."""
        once = redact.scrub("?api_key=abcdef123456")      # nd-redact: allow
        self.assertEqual(redact.scrub(once), once)
        self.assertEqual(once, "?api_key=" + MASK)


class TestNoFalsePositives(unittest.TestCase):
    """Через тот же фильтр идут новости — их портить нельзя."""

    def same(self, text):
        self.assertEqual(redact.scrub(text), text)

    def test_news_text_untouched(self):
        self.same("Nvidia представила чип Blackwell Ultra: до 288 ГБ памяти")
        self.same("Ученые нашли ключ: разгадка оказалась в белке-шапероне")
        self.same("Токен: как устроены невзаимозаменяемые токены — разбор")

    def test_ordinary_query_string_untouched(self):
        self.same("https://news.google.com/rss/search?q=ai&hl=ru&gl=RU")
        self.same("https://example.com/feed?keywords=robots&page=2")

    def test_short_values_are_not_secrets(self):
        self.same("pwd=1")


class TestKnownValues(unittest.TestCase):
    """Свой токен вырезается точным совпадением, где бы он ни всплыл."""

    def setUp(self):
        redact.forget()

    def tearDown(self):
        redact.forget()

    def test_remembered_value_disappears(self):
        redact.remember("qwerty-очень-секретное-значение")
        out = redact.scrub("упало на qwerty-очень-секретное-значение, увы")
        self.assertNotIn("qwerty-очень", out)
        self.assertIn(MASK, out)

    def test_short_value_is_ignored(self):
        redact.remember("ab")
        self.assertEqual(redact.scrub("таблица ab уехала"), "таблица ab уехала")

    def test_secret_name(self):
        self.assertTrue(redact.secret_name("TELEGRAM_BOT_TOKEN"))
        self.assertTrue(redact.secret_name("ND_WEB_TOKEN"))
        self.assertFalse(redact.secret_name("ND_TOPIC"))

    def test_load_env_remembers_own_secrets(self):
        """config подписывает живые ключи на вырезание сам."""
        saved = config.DS_KEY
        try:
            config.DS_KEY = "sk-живой-ключ-из-окружения"
            config.remember_secrets()
            self.assertIn(MASK, redact.scrub("ключ sk-живой-ключ-из-окружения"))
        finally:
            config.DS_KEY = saved


class TestStructures(unittest.TestCase):
    def test_scrub_json_walks_nested(self):
        data = {"items": [{"url": "https://x/f?token=abcdef123456",  # nd-redact: allow
                           "title": "Обычный заголовок"}]}
        out = redact.scrub_json(data)
        self.assertEqual(out["items"][0]["url"], "https://x/f?token=" + MASK)
        self.assertEqual(out["items"][0]["title"], "Обычный заголовок")

    def test_safe_url(self):
        self.assertEqual(redact.safe_url("https://a:b1234567@h/f"),  # nd-redact: allow
                         "https://a:%s@h/f" % MASK)  # nd-redact: allow


class TestLogFilter(unittest.TestCase):
    """Лог вставляют в issue — секретов в нём быть не должно."""

    def record(self, msg, *args):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(redact.SecretFilter())
        logger = logging.getLogger("nd-test-redact")
        logger.handlers, logger.propagate, logger.level = [handler], False, 10
        logger.warning(msg, *args)
        return stream.getvalue()

    def test_message_is_cleaned(self):
        out = self.record("не достучались до https://u:pw12345678@h/f")  # nd-redact: allow
        self.assertNotIn("pw12345678", out)
        self.assertIn(MASK, out)

    def test_arguments_are_cleaned(self):
        out = self.record("источник %s: %s", "https://h/f?api_key=abcdef123456",  # nd-redact: allow
                          "таймаут")
        self.assertNotIn("abcdef123456", out)
        self.assertIn("таймаут", out)

    def test_broken_record_does_not_break_logging(self):
        out = self.record("число %d", 5)
        self.assertIn("число 5", out)


class TestPromptGuard(unittest.TestCase):
    """Главное: к модели секрет не уходит, каким бы путём он ни пришёл."""

    def setUp(self):
        self.saved = (llm.post_json, config.DS_KEY)
        self.sent = {}
        config.DS_KEY = "test-key"

        def fake_post_json(url, payload, headers, timeout):
            self.sent.update(payload)
            return 200, {"choices": [{"message": {"content": '{"items": []}'}}],
                         "usage": {}}, ""
        llm.post_json = fake_post_json

    def tearDown(self):
        llm.post_json, config.DS_KEY = self.saved

    def body(self):
        return "\n".join(m["content"] for m in self.sent["messages"])

    def test_secret_in_user_message_never_leaves(self):
        llm.llm_json("портрет читателя",
                     'Новости: [{"url": "https://h/f?api_key=abcdef123456"}]',  # nd-redact: allow
                     "model")
        self.assertNotIn("abcdef123456", self.body())
        self.assertIn(MASK, self.body())

    def test_secret_in_system_message_never_leaves(self):
        """Портрет читателя правится руками — туда попадает что угодно."""
        llm.llm_json("Читатель: админ, пароль от панели: hunter2xyz",  # nd-redact: allow
                     "новости", "model")
        self.assertNotIn("hunter2xyz", self.body())

    def test_own_api_key_never_leaves_in_text(self):
        redact.remember(config.DS_KEY + "-длинный-хвост")
        try:
            llm.llm_json("s", "ключ " + config.DS_KEY + "-длинный-хвост", "model")
            self.assertNotIn("длинный-хвост", self.body())
        finally:
            redact.forget()

    def test_clean_text_goes_through_unchanged(self):
        llm.llm_json("портрет", "Nvidia представила чип", "model")
        self.assertIn("Nvidia представила чип", self.body())


class TestCheckCommand(unittest.TestCase):
    """`digest.py scrub --check` — то, что стоит в CI перед PR."""

    def test_finds_secret_line(self):
        found = redact.check_lines(["обычная строка",
                                    "TOKEN=ghp_abcdefghijklmnopqrstuvwxyz01"])  # nd-redact: allow
        self.assertEqual([number for number, _hits in found], [2])

    def test_allow_marker_skips_line(self):
        line = "пример: TOKEN=ghp_abcdefghijklmnopqrstuvwxyz01  # " + redact.ALLOW  # nd-redact: allow
        self.assertEqual(redact.check_lines([line]), [])


if __name__ == "__main__":
    unittest.main()
