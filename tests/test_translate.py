# -*- coding: utf-8 -*-
"""Язык выпуска: всё, что видит читатель, приходит к нему по-русски.

Модель здесь подменена: проверяется не качество перевода, а то, что чужой язык
вообще замечен, переведён один раз и потом берётся из кэша.
"""
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import storage, translate  # noqa: E402
from newsdigest.config import CFG  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

ENGLISH = "Boot Option Submitted Ahead Of Linux Kernel Release"
RUSSIAN = "Опция загрузки внесена перед релизом ядра Linux"


class TestForeign(unittest.TestCase):
    """Кириллица — единственный дешёвый признак, и ошибаться он должен в
    безопасную сторону: лучше не тронуть русский заголовок, чем перевести его
    второй раз."""

    def test_english_is_foreign(self):
        self.assertTrue(translate.foreign(ENGLISH))

    def test_russian_is_not(self):
        self.assertFalse(translate.foreign(RUSSIAN))

    def test_russian_with_latin_names_is_not(self):
        """Названия продуктов латиницей — норма для русского заголовка."""
        self.assertFalse(translate.foreign(
            "GitHub Copilot Workspace вышел в открытый доступ"))

    def test_short_strings_are_left_alone(self):
        for text in ("GPT-5", "Redis 8.0", "", None, "Nvidia Rubin"):
            self.assertFalse(translate.foreign(text), text)

    def test_other_language_is_never_foreign(self):
        """Читателю с /set language english мы ничего не переводим."""
        self.assertFalse(translate.foreign(ENGLISH, "english"))


class TestImproved(unittest.TestCase):
    """Что считать переводом, а что — эхом исходной строки."""

    def test_russian_answer_is_a_translation(self):
        self.assertTrue(translate.improved(ENGLISH, RUSSIAN))

    def test_echo_is_not(self):
        self.assertFalse(translate.improved(ENGLISH, ENGLISH))
        self.assertFalse(translate.improved(ENGLISH, ""))

    def test_names_heavy_translation_is_accepted(self):
        """Заголовок из одних имён и по-русски останется наполовину латинским."""
        self.assertTrue(translate.improved(
            "Nvidia Rubin CES 2026 keynote: GeForce RTX 6090 unveiled",
            "Nvidia на CES 2026 показала GeForce RTX 6090"))


class TranslateCase(unittest.TestCase):
    def setUp(self):
        conn = storage.db()
        conn.execute("DELETE FROM translations")
        conn.commit()
        conn.close()
        self.conn = storage.db()
        self.asked = []
        self._real = translate.translate_texts
        translate.translate_texts = self.fake

    def tearDown(self):
        translate.translate_texts = self._real
        self.conn.close()

    #: чем «переводит» подменённая модель: строка по-русски и номер, по которому
    #: видно, какой именно текст она переводила
    TRANSLATED = "Русский перевод строки номер %d"

    def fake(self, texts, language):
        self.asked.append(list(texts))
        return ({i: self.TRANSLATED % i for i in range(len(texts))},
                {"in": 10, "out": 5})


class TestLocalize(TranslateCase):
    def test_foreign_card_is_translated(self):
        card = {"headline": ENGLISH, "what": "A last minute pull request "
                                             "was submitted for the option",
                "why": ""}
        translate.localize(self.conn, [card])
        self.assertEqual(card["headline"], self.TRANSLATED % 0)
        self.assertEqual(card["what"], self.TRANSLATED % 1)
        self.assertEqual(card["why"], "")

    def test_russian_card_never_reaches_the_model(self):
        card = {"headline": RUSSIAN, "what": "Патч добавили перед релизом",
                "why": "Ядро соберётся с новой опцией"}
        cost = translate.localize(self.conn, [card])
        self.assertEqual(self.asked, [])
        self.assertEqual(cost, 0.0)
        self.assertEqual(card["headline"], RUSSIAN)

    def test_second_pass_takes_the_cache(self):
        first, second = {"headline": ENGLISH}, {"headline": ENGLISH}
        translate.localize(self.conn, [first])
        translate.localize(self.conn, [second])
        self.assertEqual(len(self.asked), 1, "за тем же заголовком ходили дважды")
        self.assertEqual(second["headline"], first["headline"])

    def test_one_request_for_the_whole_issue(self):
        cards = [{"headline": "%s %d" % (ENGLISH, i)} for i in range(5)]
        translate.localize(self.conn, cards)
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(len(self.asked[0]), 5)

    def test_untranslated_answer_is_not_remembered(self):
        """Модель вернула ту же английскую строку — это не перевод."""
        translate.translate_texts = lambda texts, language: (
            {i: text for i, text in enumerate(texts)}, {"in": 1, "out": 1})
        card = {"headline": ENGLISH}
        translate.localize(self.conn, [card])
        self.assertEqual(card["headline"], ENGLISH)
        self.assertEqual(translate.cached(self.conn, [ENGLISH]), {})

    def test_name_heavy_russian_headline_survives_a_false_alarm(self):
        """Кириллицы мало из-за названий — заголовок съездит к модели зря.

        Эхо в ответ мы не принимаем, поэтому читатель всё равно увидит свой
        текст: ошибаться проверка должна в эту сторону, а не в обратную.
        """
        title = "Hugging Face выпустил Transformers 5.0"
        translate.translate_texts = lambda texts, language: (
            {i: text for i, text in enumerate(texts)}, {"in": 1, "out": 1})
        card = {"headline": title}
        translate.localize(self.conn, [card])
        self.assertEqual(card["headline"], title)

    def test_model_failure_keeps_the_original(self):
        def broken(texts, language):
            raise LLMError("модель недоступна")

        translate.translate_texts = broken
        card = {"headline": ENGLISH, "what": "Nothing to see here at all"}
        self.assertEqual(translate.localize(self.conn, [card]), 0.0)
        self.assertEqual(card["headline"], ENGLISH)     # выпуск всё равно уйдёт

    def test_switch_off_disables_translation(self):
        CFG["translate"] = False
        try:
            card = {"headline": ENGLISH}
            translate.localize(self.conn, [card])
        finally:
            CFG["translate"] = True
        self.assertEqual(self.asked, [])
        self.assertEqual(card["headline"], ENGLISH)

    def test_titles_of_the_leftover(self):
        rows = [{"title": ENGLISH}, {"title": RUSSIAN}]
        translate.localize_titles(self.conn, rows)
        self.assertEqual(rows[0]["title"], self.TRANSLATED % 0)
        self.assertEqual(rows[1]["title"], RUSSIAN)


class TestKnown(TranslateCase):
    """Кэш умеет отдавать перевод без единого запроса — на этом стоят
    мгновенные /more и /saved."""

    def test_known_returns_cached_translation(self):
        translate.remember(self.conn, [(ENGLISH, RUSSIAN)])
        self.assertEqual(translate.known(self.conn, ENGLISH), RUSSIAN)
        self.assertEqual(self.asked, [])

    def test_known_falls_back_to_the_original(self):
        self.assertEqual(translate.known(self.conn, ENGLISH), ENGLISH)

    def test_headline_of_the_card_becomes_the_known_title(self):
        """Читатель видел карточку — её заголовок и покажем в закладках."""
        translate.remember_headlines(self.conn, [(ENGLISH, RUSSIAN)])
        self.assertEqual(translate.known(self.conn, ENGLISH), RUSSIAN)

    def test_russian_headline_is_not_remembered(self):
        """Русский заголовок переписывать карточкой незачем: он уже понятен."""
        translate.remember_headlines(self.conn, [(RUSSIAN, "Другой текст")])
        self.assertEqual(translate.known(self.conn, RUSSIAN), RUSSIAN)

    def test_item_facts_speak_russian(self):
        """👍 и 🔖 кладут в базу то, что читатель видел, а не строку из фида."""
        self.conn.execute(
            "INSERT OR REPLACE INTO items(url_hash,url,source_id,title,summary,"
            "fetched_at) VALUES ('h1','https://e.com/1','phoronix',?,'','now')",
            (ENGLISH,))
        self.conn.commit()
        translate.remember_headlines(self.conn, [(ENGLISH, RUSSIAN)])
        self.assertEqual(storage.item_facts(self.conn, "h1")["title"], RUSSIAN)


if __name__ == "__main__":
    unittest.main()
