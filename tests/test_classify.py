# -*- coding: utf-8 -*-
"""Маршрутизация новостей по разделам: словарь правил и каскад целиком.

Сеть и модель здесь не трогаются: проверяются детерминированные ветки,
а обращение к модели подменяется.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import classify, pipeline, storage  # noqa: E402
from newsdigest.config import CFG  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402
from newsdigest.profiles import DEFAULT_SECTIONS  # noqa: E402

from test_core import item  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                    "routing.json")

#: доля эталонного набора, которую словарь обязан разложить правильно.
#: Ниже — правила сломались; заметно выше стоит поднять и порог
MIN_ACCURACY = 0.90


def golden():
    with open(DATA, encoding="utf-8") as handle:
        return json.load(handle)["cases"]


class TestGolden(unittest.TestCase):
    """Эталонный набор: тот самый ответ на «правильно ли поделено на разделы»."""

    def test_rules_match_the_golden_set(self):
        cases = golden()
        wrong = []
        for case in cases:
            got, _conf = classify.by_rules(case["title"], case["lead"],
                                           DEFAULT_SECTIONS)
            if got != case["section"]:
                wrong.append("%-9s -> %-9s  %s"
                             % (case["section"] or "«не решать»", got or "-",
                                case["title"][:60]))
        accuracy = 1.0 - len(wrong) / float(len(cases))
        self.assertGreaterEqual(
            accuracy, MIN_ACCURACY,
            "точность %.0f%% при пороге %.0f%%. Разошлись:\n  %s"
            % (accuracy * 100, MIN_ACCURACY * 100, "\n  ".join(wrong)))

    def test_ambiguous_headlines_are_left_to_the_model(self):
        """Честно неоднозначное правила решать не должны: угадывание хуже отказа."""
        for case in golden():
            if case["section"]:
                continue
            got, _conf = classify.by_rules(case["title"], case["lead"],
                                           DEFAULT_SECTIONS)
            self.assertEqual(got, "", "правила решили за модель: %s" % case["title"])


class TestRules(unittest.TestCase):
    def test_short_words_need_word_boundaries(self):
        """«ии» не должно находиться в «дай», а «ai» — в «said»."""
        got, _conf = classify.by_rules("Он сказал: дай мне это", "said again",
                                       DEFAULT_SECTIONS)
        self.assertEqual(got, "")

    def test_title_outweighs_lead(self):
        """Тема заголовка важнее темы лида."""
        got, _conf = classify.by_rules(
            "Землетрясение магнитудой 7 разрушило город",
            "Учёные изучают физику разломов", DEFAULT_SECTIONS)
        self.assertEqual(got, "incidents")

    def test_climate_wins_over_science(self):
        """У климата свой раздел — «Наука» не должна его забирать."""
        got, _conf = classify.by_rules(
            "Учёные измерили рекордное таяние ледников Арктики",
            "Исследование опубликовано", DEFAULT_SECTIONS)
        self.assertEqual(got, "climate")

    def test_unused_section_is_not_a_destination(self):
        """Нельзя уводить новость в раздел, на который никто не подписан."""
        got, _conf = classify.by_rules(
            "Bitcoin ETF approved by the SEC", "Crypto markets rallied",
            ["politics", "economy"])
        self.assertNotEqual(got, "crypto")


class TestRouteAll(unittest.TestCase):
    """Каскад целиком: узкий фид, словарь, кэш, модель, запасной путь."""

    def setUp(self):
        self.conn = storage.db()
        self.conn.execute("DELETE FROM routes")
        self.conn.commit()
        self.saved = {k: CFG[k] for k in ("classify_llm", "classify_max")}
        self.asked = []
        self._real = classify.ask_model

    def tearDown(self):
        CFG.update(self.saved)
        classify.ask_model = self._real
        self.conn.close()

    def stub_model(self, answer=None):
        def fake(rows, topics):
            self.asked.append(list(rows))
            return (answer or {}), 0.0
        classify.ask_model = fake

    def test_strict_feed_skips_everything(self):
        """Узкая лента — раздел из фида, ни словаря, ни модели."""
        CFG["classify_llm"] = False
        rows = [item("https://openai.com/a", "Совершенно нейтральный текст",
                     source="openai", tier=1)]
        classify.route_all(self.conn, rows, DEFAULT_SECTIONS)
        self.assertEqual(rows[0]["section"], "ai")
        self.assertEqual(rows[0]["route_conf"], 1.0)

    def test_wide_feed_routed_by_content(self):
        """Ради этого всё и затевалось: AI-новость от Reuters идёт в «ИИ»."""
        CFG["classify_llm"] = False
        rows = [item("https://reuters.com/a",
                     "OpenAI releases GPT-6 with 2M context window",
                     source="reuters-world", tier=1)]
        classify.route_all(self.conn, rows, DEFAULT_SECTIONS)
        self.assertEqual(rows[0]["section"], "ai")

    def test_undecided_stays_empty(self):
        """Не решилось — раздел пуст, и дальше работает старый путь."""
        CFG["classify_llm"] = False
        rows = [item("https://reuters.com/a", "Компания объявила о реорганизации",
                     source="reuters-world", tier=1)]
        classify.route_all(self.conn, rows, DEFAULT_SECTIONS)
        self.assertEqual(rows[0]["section"], "")

    def test_model_asked_only_about_wide_feeds(self):
        CFG["classify_llm"] = True
        self.stub_model()
        rows = [
            item("https://openai.com/a", "Что-то нейтральное", source="openai"),
            item("https://reuters.com/b", "Компания объявила о реорганизации",
                 source="reuters-world"),
        ]
        classify.route_all(self.conn, rows, DEFAULT_SECTIONS)
        self.assertEqual(len(self.asked), 1)
        asked_sources = {row["source_id"] for row in self.asked[0]}
        self.assertEqual(asked_sources, {"reuters-world"})

    def test_model_answer_is_cached(self):
        """Тот же материал вторым проходом не оплачиваем."""
        CFG["classify_llm"] = True
        self.stub_model({0: "economy"})
        rows = [item("https://reuters.com/b", "Компания объявила о реорганизации",
                     source="reuters-world")]
        classify.route_all(self.conn, rows, DEFAULT_SECTIONS)
        self.assertEqual(rows[0]["section"], "economy")

        again = [item("https://reuters.com/b", "Компания объявила о реорганизации",
                      source="reuters-world")]
        self.asked = []
        classify.route_all(self.conn, again, DEFAULT_SECTIONS)
        self.assertEqual(again[0]["section"], "economy")
        self.assertEqual(self.asked, [], "второй раз спросили модель заново")

    def test_model_failure_is_not_fatal(self):
        """Модель легла — раздел пуст, выпуск собирается по-старому."""
        CFG["classify_llm"] = True

        def boom(rows, topics):
            raise LLMError("нет связи")
        classify.ask_model = boom
        rows = [item("https://reuters.com/b", "Компания объявила о реорганизации",
                     source="reuters-world")]
        classify.route_all(self.conn, rows, DEFAULT_SECTIONS)
        self.assertEqual(rows[0]["section"], "")

    def test_model_budget_respected(self):
        CFG["classify_llm"] = True
        CFG["classify_max"] = 2
        self.stub_model()
        rows = [item("https://reuters.com/%d" % n, "Нейтральный текст %d" % n,
                     source="reuters-world") for n in range(5)]
        classify.route_all(self.conn, rows, DEFAULT_SECTIONS)
        self.assertEqual(len(self.asked[0]), 2)


class TestForTopic(unittest.TestCase):
    """`pipeline.for_topic` после маршрутизации."""

    def test_routed_section_wins_over_feed(self):
        rows = [dict(item("https://reuters.com/a", "GPT-6 вышел",
                          source="reuters-world"), section="ai")]
        self.assertEqual(pipeline.for_topic(rows, "ai"), rows)
        self.assertEqual(pipeline.for_topic(rows, "politics"), [])

    def test_old_rows_fall_back_to_feed(self):
        """Записи, накопленные до маршрутизации, раскладываются по источнику."""
        rows = [item("https://reuters.com/a", "Что-то", source="reuters-world")]
        self.assertEqual(pipeline.for_topic(rows, "politics"), rows)


if __name__ == "__main__":
    unittest.main()
