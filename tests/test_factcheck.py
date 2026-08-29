# -*- coding: utf-8 -*-
"""Не вброс ли это: обеспеченность заявления, а не его истинность.

Проверяем то, что модуль обещает, и особенно — чего он НЕ делает. Придержать
новость может только слой, который выдаёт себя сам: несуществующий DOI и
вечный жанр без подтверждений. Всё остальное публикуется с оговоркой, потому
что выпуск на две трети состоит из новостей с одним источником, и правило
«один источник — значит держим» вырезало бы из него половину.

Сеть и модель не трогаются: Crossref и DeepSeek подменены.
"""
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import factcheck, storage  # noqa: E402
from newsdigest.config import CFG  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

#: заголовки, на которых видно разницу между вбросом и новостью
HOAX = "Британские учёные доказали: найдено лекарство от рака"
PAPER = ("Учёные Стэнфорда описали новый механизм деления клеток "
         "в журнале Nature, doi:10.1038/s41586-026-01234-5")
PLAIN = "Postgres 18 ускорил очистку таблиц"


class FactCase(unittest.TestCase):
    """Общая обвязка: чистая база, подменённые Crossref и модель."""

    def setUp(self):
        self.conn = storage.db()
        self.conn.execute("DELETE FROM claims")
        self.conn.commit()
        self.saved = {k: CFG[k] for k in CFG}
        self._crossref, self._judge = factcheck.crossref, factcheck.judge_claims
        self.asked = []
        # по умолчанию: Crossref молчит (сети нет), модель не спрашиваем
        factcheck.crossref = lambda _doi: {}
        CFG["fact_llm"] = False

    def tearDown(self):
        factcheck.crossref, factcheck.judge_claims = self._crossref, self._judge
        CFG.update(self.saved)
        self.conn.close()

    def answers(self, verdict, note=""):
        """Подменить модель: она отвечает так про каждое заявление."""
        def judge(claims):
            self.asked.append(list(claims))
            return ({i: (verdict, note) for i in range(len(claims))},
                    {"in": 0, "out": 0, "cached": 0})
        factcheck.judge_claims = judge
        CFG["fact_llm"] = True

    def group(self, title, sources=("phys-all",), url="https://phys.org/news/1"):
        """Кластер об одном событии из перечисленных источников."""
        return [item(url + "#" + name, title, name) for name in sources]


class TestSignals(unittest.TestCase):
    """Бесплатные слои: кто говорит и на что ссылаются."""

    def test_doi_is_found_in_the_text(self):
        paper = factcheck.paper_of([item("https://phys.org/1", PAPER, "phys-all")])
        self.assertEqual(paper["doi"], "10.1038/s41586-026-01234-5")
        self.assertEqual(paper["journal"], "nature")
        self.assertTrue(paper["institution"])

    def test_preprint_is_recognised_by_its_home(self):
        paper = factcheck.paper_of(
            [item("https://arxiv.org/abs/2608.01234", "Новая модель сверхпроводника")])
        self.assertEqual(paper["preprint"], "arXiv")

    def test_short_names_do_not_match_ordinary_words(self):
        """«РАН» не должно находиться в «стран», а «ВОЗ» — в «возраст»."""
        paper = factcheck.paper_of(
            [item("https://e.com/1", "В разных странах вырос возраст выхода на пенсию")])
        self.assertFalse(paper["institution"])

    def test_markers_catch_the_eternal_genre(self):
        flags = factcheck.markers([item("https://e.com/1", HOAX)])
        self.assertTrue(flags["hoax"])
        self.assertTrue(flags["vague"])

    def test_markers_leave_ordinary_news_alone(self):
        flags = factcheck.markers([item("https://e.com/1", PLAIN)])
        self.assertFalse(any(flags.values()))

    def test_witnesses_count_publishers_not_feeds(self):
        """Шесть лент Guardian — одна редакция, а не шесть подтверждений."""
        group = [item("https://e.com/%d" % i, "Событие", name) for i, name in
                 enumerate(("guardian-world", "guardian-environment"))]
        self.assertEqual(factcheck.witnesses(group)["publishers"], 1)


class TestVerdicts(FactCase):
    """Что решают бесплатные слои и, главное, чего они не решают."""

    def test_ordinary_single_source_news_goes_out_as_is(self):
        """Выпуск на две трети состоит из таких новостей — это норма."""
        verdict, note, _ask = factcheck.by_signals(
            self.conn, self.group(PLAIN, ("postgresql",)), "dev")
        self.assertEqual(verdict, factcheck.OK)
        self.assertEqual(note, "")

    def test_single_source_in_a_watched_section_gets_a_caveat_not_a_hold(self):
        verdict, note, _ask = factcheck.by_signals(
            self.conn, self.group("Найдена связь кофе и долголетия", ("phys-all",)),
            "medicine")
        self.assertEqual(verdict, factcheck.CAVEAT)
        self.assertTrue(note)

    def test_eternal_genre_without_confirmation_is_held(self):
        verdict, note, _ask = factcheck.by_signals(
            self.conn, self.group(HOAX, ("phys-all",)), "medicine")
        self.assertEqual(verdict, factcheck.HOLD)
        self.assertIn("жанр", note)

    def test_eternal_genre_with_confirmation_goes_out(self):
        """Иногда за громким жанром стоит настоящая новость про заявку."""
        confirmed = self.group(HOAX, ("reuters-world", "ap-topnews", "bbc-world"))
        verdict, _note, _ask = factcheck.by_signals(self.conn, confirmed, "medicine")
        self.assertEqual(verdict, factcheck.OK)

    def test_nonexistent_doi_is_the_only_deterministic_hold(self):
        factcheck.crossref = lambda _doi: {"missing": True}
        verdict, note, ask = factcheck.by_signals(
            self.conn, self.group(PAPER, ("phys-all",)), "science")
        self.assertEqual(verdict, factcheck.HOLD)
        self.assertIn("не существует", note)
        self.assertFalse(ask)           # модели тут делать нечего

    def test_existing_doi_settles_the_question(self):
        factcheck.crossref = lambda _doi: {"title": "Cell division mechanism",
                                           "year": "2026"}
        verdict, note, ask = factcheck.by_signals(
            self.conn, self.group(PAPER, ("phys-all",)), "science")
        self.assertEqual(verdict, factcheck.OK)
        self.assertEqual(note, "")
        self.assertFalse(ask)

    def test_crossref_silence_does_not_condemn(self):
        """Сеть могла не дойти, и Crossref знает не про все журналы."""
        factcheck.crossref = lambda _doi: {}
        verdict, _note, _ask = factcheck.by_signals(
            self.conn, self.group(PAPER, ("phys-all",)), "science")
        self.assertNotEqual(verdict, factcheck.HOLD)

    def test_preprint_is_a_caveat_not_a_verdict(self):
        """LK-99 тоже был препринтом, и это была настоящая новость."""
        group = [item("https://arxiv.org/abs/2608.01", "Сверхпроводник при 20 °C",
                      "arxiv")]
        verdict, note, _ask = factcheck.by_signals(self.conn, group, "science")
        self.assertEqual(verdict, factcheck.CAVEAT)
        self.assertIn("препринт", note.lower())


class TestScreen(FactCase):
    """Точка входа выпуска: что доезжает до читателя и с какой пометкой."""

    def shortlists(self, *pairs):
        return [(topic, [group]) for topic, group in pairs]

    def test_held_event_leaves_the_issue(self):
        lists = self.shortlists(("medicine", self.group(HOAX, ("phys-all",))))
        factcheck.screen(self.conn, lists)
        self.assertEqual(lists[0][1], [])

    def test_ordinary_news_is_untouched(self):
        lists = self.shortlists(("dev", self.group(PLAIN, ("postgresql",))))
        factcheck.screen(self.conn, lists)
        self.assertEqual(len(lists[0][1]), 1)

    def test_caveat_reaches_the_card(self):
        group = [item("https://arxiv.org/abs/2608.01", "Сверхпроводник при 20 °C",
                      "arxiv")]
        lists = self.shortlists(("science", group))
        factcheck.screen(self.conn, lists)
        self.assertEqual(len(lists[0][1]), 1)
        self.assertIn("препринт", factcheck.caveat_of(group).lower())

    def test_hold_expires_by_itself(self):
        """Карантин — это задержка, а не приговор навсегда."""
        group = self.group(HOAX, ("phys-all",))
        lists = self.shortlists(("medicine", group))
        factcheck.screen(self.conn, lists)
        self.assertEqual(lists[0][1], [])

        CFG["fact_hold_h"] = 1
        self.conn.execute("UPDATE claims SET at = '2020-01-01T00:00:00+00:00'")
        self.conn.commit()
        # жанр никуда не делся, поэтому событие придержат снова — но уже
        # новым решением, а не старым: истёкший карантин не вечен
        self.assertFalse(factcheck.held(self.conn, group[0]["sig"]))

    def test_confirmation_lifts_the_hold_early(self):
        """Подтвердили независимые издатели — ждать больше нечего."""
        alone = self.group(HOAX, ("phys-all",))
        factcheck.screen(self.conn, self.shortlists(("medicine", alone)))
        self.assertTrue(factcheck.held(self.conn, alone[0]["sig"]))

        wide = self.group(HOAX, ("reuters-world", "ap-topnews", "bbc-world"))
        lists = self.shortlists(("medicine", wide))
        factcheck.screen(self.conn, lists)
        self.assertEqual(len(lists[0][1]), 1)

    def test_switch_off_costs_nothing_and_changes_nothing(self):
        CFG["factcheck"] = False
        lists = self.shortlists(("medicine", self.group(HOAX, ("phys-all",))))
        self.assertEqual(factcheck.screen(self.conn, lists), 0.0)
        self.assertEqual(len(lists[0][1]), 1)


class TestModel(FactCase):
    """Модель судит о форме заявления и может придержать, но не выбросить."""

    def test_model_hold_removes_an_unconfirmed_event(self):
        self.answers(factcheck.HOLD, "заявление без адреса")
        group = self.group("Открыт новый вид сознания у растений", ("phys-all",))
        lists = [("science", [group])]
        factcheck.screen(self.conn, lists)
        self.assertEqual(lists[0][1], [])

    def test_model_cannot_hold_what_the_world_confirmed(self):
        """Три редакции против одного суждения модели — побеждают редакции."""
        self.answers(factcheck.HOLD, "не нравится")
        group = self.group("Запущен телескоп нового поколения",
                           ("reuters-world", "ap-topnews", "bbc-world"))
        lists = [("science", [group])]
        factcheck.screen(self.conn, lists)
        self.assertEqual(len(lists[0][1]), 1)

    def test_model_caveat_reaches_the_reader(self):
        self.answers(factcheck.CAVEAT, "заголовок шире исследования")
        group = self.group("Кофе продлевает жизнь", ("phys-all",))
        lists = [("medicine", [group])]
        factcheck.screen(self.conn, lists)
        self.assertEqual(factcheck.caveat_of(group), "заголовок шире исследования")

    def test_broken_model_does_not_break_the_issue(self):
        """Модель недоступна — работают бесплатные слои, как и раньше."""
        def broken(_claims):
            raise LLMError("нет связи")
        factcheck.judge_claims = broken
        CFG["fact_llm"] = True
        group = self.group("Кофе продлевает жизнь", ("phys-all",))
        lists = [("medicine", [group])]
        self.assertEqual(factcheck.screen(self.conn, lists), 0.0)
        self.assertEqual(len(lists[0][1]), 1)

    def test_verdict_is_cached_and_not_paid_for_twice(self):
        self.answers(factcheck.CAVEAT, "данные одной лаборатории")
        group = self.group("Кофе продлевает жизнь", ("phys-all",))
        factcheck.screen(self.conn, [("medicine", [group])])
        self.assertEqual(len(self.asked), 1)
        factcheck.screen(self.conn, [("medicine", [group])])
        self.assertEqual(len(self.asked), 1)        # второй раз не спрашивали

    def test_the_model_is_not_told_what_we_suspect(self):
        """Подсказка «мы думаем, это вброс» превращает вопрос в просьбу
        согласиться. Показываем событие, а не свои выводы."""
        self.answers(factcheck.OK)
        group = self.group(HOAX + " по данным Nature", ("phys-all", "sd-science"))
        factcheck.screen(self.conn, [("medicine", [group])])
        asked = self.asked[0][0] if self.asked else {}
        for key in asked:
            self.assertNotIn(key, ("verdict", "suspicion", "markers", "hoax"))


class TestBreakingIsStricter(FactCase):
    """Срочное будит человека, поэтому неподтверждённое туда не идёт."""

    def test_unconfirmed_eternal_genre_never_goes_as_breaking(self):
        out, _cost = factcheck.vet(self.conn, [self.group(HOAX, ("phys-all",))])
        self.assertEqual(out, [])

    def test_confirmed_event_goes(self):
        group = self.group("Землетрясение M7 у берегов Японии",
                           ("reuters-world", "ap-topnews", "bbc-world"))
        out, _cost = factcheck.vet(self.conn, [group])
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
