# -*- coding: utf-8 -*-
"""Расход токенов: что уходит в запрос и за что мы платим второй раз.

Проверки здесь не про качество ответа, а про его цену. Ошибка в них не
уронит выпуск — она просто сделает каждый запрос дороже, причём молча, и
заметить это можно будет только по счёту в конце месяца.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import (classify, config, llm, pipeline,  # noqa: E402
                        storage, textutil)

from test_core import item  # noqa: E402
from test_pipeline import CHAT, PipelineCase  # noqa: E402


class TestLeadOf(unittest.TestCase):
    """Лид новости: платим за содержание, а не за служебный текст."""

    def lead(self, title, summary, limit=300):
        return textutil.lead_of(title, summary, limit)

    def test_a_repeated_title_is_dropped(self):
        self.assertEqual(
            self.lead("Nvidia показала Rubin",
                      "Nvidia показала Rubin. Ускоритель выйдет весной."),
            "Ускоритель выйдет весной.")

    def test_case_and_punctuation_do_not_save_the_repeat(self):
        self.assertEqual(
            self.lead("Rust добавил async",
                      "RUST ДОБАВИЛ ASYNC — трейты приняты в stable."),
            "трейты приняты в stable.")

    def test_a_different_beginning_is_kept(self):
        text = "Ускоритель выйдет весной, обещают вдвое больше памяти."
        self.assertEqual(self.lead("Nvidia показала Rubin", text), text)

    def test_feed_tails_are_dropped(self):
        for tail in ("The post Что-то appeared first on Хабр.",
                     "Continue reading on our site",
                     "Читать далее",
                     "Подробнее на сайте",
                     "[…]"):
            self.assertEqual(self.lead("", "Событие случилось. " + tail),
                             "Событие случилось.", tail)

    def test_a_tail_word_inside_a_sentence_is_kept(self):
        """«Подробнее» бывает и словом из новости, а не подписью ленты."""
        text = "Учёный рассказал подробнее о планах команды."
        self.assertEqual(self.lead("", text), text)

    def test_two_tails_in_a_row_are_dropped(self):
        self.assertEqual(self.lead("", "Событие случилось. Читать далее […]"),
                         "Событие случилось.")

    def test_a_summary_that_is_only_the_title_becomes_empty(self):
        """Заголовок модель и так видит рядом — платить за него дважды
        незачем."""
        self.assertEqual(self.lead("Vim празднует тридцатилетие",
                                   "Vim празднует тридцатилетие"), "")

    def test_the_limit_holds(self):
        self.assertEqual(len(self.lead("", "я" * 900)), 300)
        self.assertEqual(len(self.lead("", "я" * 900, 500)), 500)

    def test_nothing_at_all_is_survivable(self):
        self.assertEqual(self.lead(None, None), "")


class TestTask(unittest.TestCase):
    """Сообщение с заданием: компактный json и переменная шапка."""

    def test_json_goes_without_spaces(self):
        text = llm.task("Пары", [{"id": 0, "a": "раз"}])
        self.assertIn('[{"id":0,"a":"раз"}]', text)

    def test_cyrillic_is_not_escaped(self):
        """\\u0440\\u0430\\u0437 — это втрое больше токенов, чем «раз»."""
        self.assertNotIn("\\u04", llm.task("Пары", [{"a": "раз"}]))

    def test_the_head_comes_before_the_data(self):
        text = llm.task("Кандидаты", [{"id": 0}], "Читатель: инженер")
        self.assertTrue(text.startswith("Читатель: инженер"))
        self.assertIn("Кандидаты (json):", text)

    def test_an_empty_head_line_is_skipped(self):
        self.assertTrue(llm.task("Строки", [{"id": 0}], "").startswith("\nСтроки"))


class TestCachedPrefix(unittest.TestCase):
    """Системный промпт — общее начало запроса, и оно должно быть общим.

    Провайдер считает кэшированный токен в разы дешевле обычного, но кэш
    работает по совпадению НАЧАЛА запроса. Стоит подставить в инструкцию
    портрет читателя — и у двух разделов общего начала не остаётся вовсе.
    """

    def setUp(self):
        self.saved = (llm.post_json, config.DS_KEY)
        config.DS_KEY = "test-key"
        self.seen = []

        def fake_post_json(url, payload, headers, timeout):
            self.seen.append(payload["messages"])
            return 200, {"choices": [{"message": {"content": '{"items": []}'}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, ""

        llm.post_json = fake_post_json
        classify.llm_json = llm.llm_json

    def tearDown(self):
        llm.post_json, config.DS_KEY = self.saved

    def systems(self):
        return [m[0]["content"] for m in self.seen]

    def users(self):
        return [m[1]["content"] for m in self.seen]

    def cluster(self, title="Заголовок"):
        return [dict(item("https://e.com/1", title, "openai"),
                     summary="Суть события")]

    def test_ranking_keeps_one_system_prompt_for_every_reader(self):
        llm.rank_clusters([self.cluster()], "инженер-разработчик")
        llm.rank_clusters([self.cluster()], "трейдер, следит за биржей")
        self.assertEqual(self.systems()[0], self.systems()[1])
        self.assertIn("инженер-разработчик", self.users()[0])
        self.assertIn("трейдер", self.users()[1])

    def test_cards_keep_one_system_prompt_for_every_language(self):
        picked = [(self.cluster(), 9.0, "labs")]
        llm.summarize_batch(picked, "инженер", "русский")
        llm.summarize_batch(picked, "инженер", "english")
        self.assertEqual(self.systems()[0], self.systems()[1])
        self.assertIn("Язык ответа: english", self.users()[1])

    def test_no_prompt_carries_a_leftover_placeholder(self):
        """Промпт больше не форматируется — фигурная скобка в нём теперь
        часть примера json, и подставлять в неё нечего."""
        for name in ("RANK_SYSTEM", "BREAKING_SYSTEM", "SUM_SYSTEM", "TR_SYSTEM",
                     "DUP_SYSTEM", "CLAIM_SYSTEM"):
            text = getattr(llm, name)
            self.assertNotIn("{persona}", text, name)
            self.assertNotIn("{language}", text, name)
            self.assertNotIn("{{", text, name)


class TestCardCache(PipelineCase):
    """Карточка одной и той же новости пишется один раз на всех."""

    def summarized(self, picked, persona, language):
        self.calls.append(len(picked))
        return self.fake_summarize(picked, persona, language)

    def setUp(self):
        PipelineCase.setUp(self)
        self.calls = []
        pipeline.summarize = self.summarized

    def test_the_second_reader_gets_the_cards_for_free(self):
        self.fill(3)
        pipeline.build_and_send(chat_id=CHAT)
        self.assertEqual(self.calls, [3])

        # второй подписчик, те же разделы и тот же язык: новости для него
        # свежие (своя история), а карточки к ним уже написаны
        pipeline.build_and_send(chat_id="78")
        self.assertEqual(self.calls, [3])

    def test_a_new_event_is_still_written(self):
        self.fill(3)
        pipeline.build_and_send(chat_id=CHAT)
        self.fill(4)
        pipeline.build_and_send(chat_id="78")
        self.assertEqual(self.calls, [3, 1])

    def test_a_silent_model_is_not_remembered(self):
        """Модель промолчала — в выпуск ушёл заголовок из фида. Запомнить это
        значит навсегда оставить новость без карточки."""
        conn = storage.db()
        try:
            storage.remember_cards(conn, [("k", {"headline": "", "what": ""})])
            self.assertEqual(storage.cards_known(conn, ["k"]), {})
        finally:
            conn.close()

    def test_what_the_model_wrote_comes_back_whole(self):
        conn = storage.db()
        try:
            card = {"headline": "Заголовок", "what": "суть", "why": "важно"}
            storage.remember_cards(conn, [("k", card)])
            self.assertEqual(storage.cards_known(conn, ["k"]), {"k": card})
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
