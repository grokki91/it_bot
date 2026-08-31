# -*- coding: utf-8 -*-
"""Разбор ответов модели: JSON от неё регулярно приходит с браком."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import config, llm  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402


class TestLoads(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(llm._loads('{"items": [{"id": 1}]}'),
                         {"items": [{"id": 1}]})

    def test_code_fence(self):
        self.assertEqual(llm._loads('```json\n{"items": [{"id": 1}]}\n```'),
                         {"items": [{"id": 1}]})

    def test_text_around_json(self):
        self.assertEqual(llm._loads('Вот ответ: {"items": []} — готово'),
                         {"items": []})

    def test_unescaped_quotes_inside_value(self):
        """Ровно тот случай, что ронял раздел «кино»: Expecting ',' delimiter."""
        raw = ('{"items": [{"id": 0, "headline": "Фильм "Дюна 3" получил дату", '
               '"what": "Студия назвала дату, трейлер вышел", '
               '"why": "Слот в прокате занят"}]}')
        cards = llm._loads(raw)["items"]
        self.assertEqual(cards[0]["headline"], 'Фильм "Дюна 3" получил дату')
        self.assertEqual(cards[0]["why"], "Слот в прокате занят")

    def test_quote_before_comma_inside_value(self):
        """Кавычка перед запятой — но дальше обычный текст, а не новое поле."""
        raw = '{"items": [{"id": 0, "what": "сериал "Кино", сборы растут"}]}'
        self.assertEqual(llm._loads(raw)["items"][0]["what"],
                         'сериал "Кино", сборы растут')

    def test_trailing_commas(self):
        self.assertEqual(llm._loads('{"items": [{"id": 0, "why": "ок",},],}'),
                         {"items": [{"id": 0, "why": "ок"}]})

    def test_raw_newline_in_value(self):
        self.assertEqual(llm._loads('{"items": [{"id": 0, "what": "две\nстроки"}]}'),
                         {"items": [{"id": 0, "what": "две\nстроки"}]})

    def test_truncated_answer_keeps_complete_items(self):
        """Обрыв по max_tokens: целые карточки должны дойти до выпуска."""
        raw = ('{"items": [{"id": 0, "headline": "раз"}, '
               '{"id": 1, "headline": "два"}, {"id": 2, "headline": "обры')
        items = llm._loads(raw)["items"]
        self.assertEqual([i["id"] for i in items], [0, 1])

    def test_valid_escapes_survive(self):
        raw = r'{"items": [{"id": 0, "what": "путь C:\\temp и \"цитата\""}]}'
        self.assertEqual(llm._loads(raw)["items"][0]["what"],
                         'путь C:\\temp и "цитата"')

    def test_hopeless_text_gives_llm_error(self):
        """Не JSON вообще — понятная ошибка, а не голый ValueError из json."""
        with self.assertRaises(LLMError):
            llm._loads("извините, я не могу выполнить этот запрос")


class TestLlmJson(unittest.TestCase):
    """Сквозная проверка: битый ответ сети не выпадает наружу как ValueError."""

    def setUp(self):
        self.saved = (llm.post_json, config.DS_KEY)
        config.DS_KEY = "test-key"

    def tearDown(self):
        llm.post_json, config.DS_KEY = self.saved

    def _answer(self, content):
        def fake_post_json(url, payload, headers, timeout):
            return 200, {"choices": [{"message": {"content": content}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, ""
        llm.post_json = fake_post_json

    def test_broken_quotes_parsed(self):
        self._answer('{"items": [{"id": 0, "headline": "«Дюна 3»: "IMAX" в мае"}]}')
        data, usage = llm.llm_json("s", "u", "model")
        self.assertEqual(llm.as_list(data)[0]["id"], 0)
        self.assertEqual(usage["in"], 10)

    def test_garbage_raises_llm_error(self):
        self._answer("не сегодня")
        with self.assertRaises(LLMError):
            llm.llm_json("s", "u", "model")


class TestVerdicts(unittest.TestCase):
    """Ответ про пару: одно слово модели — и вся дальнейшая судьба новости."""

    def test_nothing_new_is_a_duplicate(self):
        for kind in (llm.SAME, llm.LESS):
            verdict = llm.verdict_of(kind)
            self.assertTrue(verdict.same, kind)
            self.assertFalse(verdict.follows, kind)

    def test_a_moved_counter_is_shown_and_linked(self):
        verdict = llm.verdict_of(llm.MORE, "найдено 200 из 270")
        self.assertFalse(verdict.same)
        self.assertTrue(verdict.follows)
        self.assertEqual(verdict.gain, "найдено 200 из 270")

    def test_a_continuation_has_no_gain(self):
        """Поле «что нового» осмысленно только там, где событие ТО ЖЕ. У
        продолжения новость своя, и дополнять ей нечего."""
        self.assertEqual(llm.verdict_of(llm.NEXT, "названа причина").gain, "")

    def test_unrelated_news_is_not_a_story(self):
        verdict = llm.verdict_of(llm.OTHER)
        self.assertFalse(verdict.same)
        self.assertFalse(verdict.follows)

    def test_an_unknown_word_is_silence(self):
        """Модель ответила не по шкале — считаем, что не ответила вовсе:
        новость идёт в выпуск, а сюжета мы не знаем."""
        silence = llm.Verdict(False, False, "", "")
        for answer in ("возможно", "", None, "SAME-ISH"):
            self.assertEqual(llm.verdict_of(answer), silence)

    def test_the_word_is_read_case_insensitively(self):
        self.assertEqual(llm.verdict_of(" LESS ").kind, llm.LESS)

    def test_a_long_gain_is_trimmed(self):
        self.assertLessEqual(len(llm.verdict_of(llm.MORE, "ы" * 200).gain), 80)


class TestJudgeDuplicates(TestLlmJson):
    """Разбор ответа целиком — по номерам пар, как он и приходит."""

    def answer(self, raw, pairs):
        self._answer(raw)
        return llm.judge_duplicates(pairs)[0]

    def test_answers_map_to_pairs(self):
        out = self.answer(
            '{"items": [{"id": 0, "news": "less", "gain": ""},'
            ' {"id": 1, "news": "more", "gain": "200 найдены"}]}',
            [("а", "б"), ("в", "г")])
        self.assertTrue(out[0].same)
        self.assertEqual(out[1].gain, "200 найдены")

    def test_a_pair_without_an_answer_stays_unjudged(self):
        """Чего модель не вернула, то в выпуск и идёт: молчание не приговор."""
        out = self.answer('{"items": [{"id": 0, "news": "same"}]}',
                          [("а", "б"), ("в", "г")])
        self.assertEqual(list(out), [0])

    def test_a_number_out_of_range_is_ignored(self):
        self.assertEqual(self.answer('{"items": [{"id": 7, "news": "same"}]}',
                                     [("а", "б")]), {})


class TestTranslateTexts(TestLlmJson):
    """Перевод: ответ раскладывается обратно по номерам строк."""

    def test_answer_maps_to_positions(self):
        self._answer('{"items": [{"id": 1, "text": "вторая"}, '
                     '{"id": 0, "text": "первая"}]}')
        got, usage = llm.translate_texts(["first", "second"], "русский")
        self.assertEqual(got, {0: "первая", 1: "вторая"})
        self.assertEqual(usage["out"], 5)

    def test_unknown_and_empty_rows_are_dropped(self):
        """Модель придумала лишний id или отдала пустую строку — пропускаем."""
        self._answer('{"items": [{"id": 0, "text": "первая"}, '
                     '{"id": 5, "text": "лишняя"}, {"id": 1, "text": "  "}]}')
        got, _usage = llm.translate_texts(["first", "second"], "русский")
        self.assertEqual(got, {0: "первая"})


if __name__ == "__main__":
    unittest.main()
