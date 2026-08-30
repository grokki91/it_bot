# -*- coding: utf-8 -*-
"""Второй слой дедупликации: спорную зону разбирает модель.

Проверяем ровно тот случай, ради которого он появился: «Умер Тим Карри» в
вечернем выпуске и «Коллеги прощаются с Тимом Карри» — срочным четыре часа
спустя. Общих слов у них два, по словам это разные новости.
"""
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import (breaking, dedup, llm, rank, storage,  # noqa: E402
                        subscribers, textutil, threads)
from newsdigest.config import CFG, now_iso  # noqa: E402
from newsdigest.llm import LLMError  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

CHAT = "77"

#: то самое крушение: 15:14 и 17:08 одного вечера, оба — срочным. Вторая
#: заметка знает о событии МЕНЬШЕ первой: лодка стала паромом, 270 стало 260,
#: а про 159 спасённых в ней нет ни слова
BOAT = ("У берегов Северного Кипра перевернулась лодка с 270 людьми. "
        "Спасательная операция продолжается, 159 пассажиров уже найдены")
FERRY = ("Паром с 260 пассажирами затонул у берегов Северного Кипра. "
         "На борту находилось более 260 человек")
RESCUE = ("У берегов Северного Кипра найдены 200 из 270 пассажиров лодки. "
          "Спасательная операция продолжается вторые сутки")

DEATH = "Тим Карри, звезда «Шоу ужасов Рокки Хоррора», умер в 80 лет"
TRIBUTE = "Коллеги, включая Кэрол Бернетт и Люка Эванса, прощаются с Тимом Карри"
CAUSE = "Названа причина смерти Тима Карри"
MEMORY = "Тима Карри вспоминают близкие"
OTHER = "Nvidia представила ускоритель Rubin для дата-центров"


class DedupCase(unittest.TestCase):
    """Общая обвязка: чистая база, подменённая модель, счётчик её вызовов."""

    def setUp(self):
        self.conn = storage.db()
        for table in ("items", "sent", "dupes"):
            self.conn.execute("DELETE FROM %s" % table)
        self.conn.commit()
        self.saved = {k: CFG[k] for k in CFG}
        self.asked = []
        self._real = dedup.judge_duplicates
        self.answer(True)

    def tearDown(self):
        dedup.judge_duplicates = self._real
        CFG.update(self.saved)
        self.conn.close()

    def answer(self, same, follows=False):
        """Подменить модель: она отвечает `same` про каждую пару."""
        self.answers(lambda _a, _b: same, follows)

    def says(self, kind, gain=""):
        """То же, но словом самой модели: llm.SAME, LESS, MORE, NEXT, OTHER.

        Вердикт из слова собирается тем же `llm.verdict_of`, что и на живом
        ответе: тест едет по той же дороге, а не по её описанию.
        """
        self.answers(lambda _a, _b: (kind, gain))

    def answers(self, decide, follows=False):
        """То же, но ответ зависит от пары: decide(что видел, что просится).

        Вернуть можно и «да/нет» (тогда `follows` говорит, продолжение ли
        это), и слово модели, и пару (слово, что нового).
        """
        def verdict(a, b):
            out = decide(a, b)
            if isinstance(out, tuple):
                return llm.verdict_of(*out)
            if isinstance(out, str):
                return llm.verdict_of(out)
            if out:
                return llm.verdict_of(llm.SAME)
            return llm.verdict_of(llm.NEXT if follows else llm.OTHER)

        def fake(pairs):
            self.asked.append(list(pairs))
            return ({i: verdict(a, b) for i, (a, b) in enumerate(pairs)},
                    {"in": 5, "out": 5})
        dedup.judge_duplicates = fake

    def group(self, title, source="src", url=None):
        return [item(url or "https://%s.com/%d" % (source, abs(hash(title)) % 9999),
                     title, source)]

    def send(self, title, source="src", days_ago=0):
        """Положить новость в историю читателя — как будто она уже уходила."""
        row = item("https://%s.com/%d" % (source, abs(hash(title)) % 9999),
                   title, source)
        at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO sent(chat_id,url_hash,sig,title,url,source_id,"
            "digest_date,sent_at,headline,summary) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (CHAT, row["url_hash"], row["sig"], row["title"], row["url"], source,
             "2026-08-26", at, row["title"], ""))
        self.conn.commit()

    def index(self):
        return rank.SentIndex(self.conn, CHAT)


class TestGrayZone(unittest.TestCase):
    """Границы: где слова решают сами, а где зовут модель."""

    def score(self, a, b):
        return textutil.similarity(textutil.signature(a), textutil.signature(b))

    def test_rewording_never_reaches_the_model(self):
        """Пересказ ловится словами — вопрос модели тут лишний расход."""
        self.assertGreaterEqual(self.score(DEATH, DEATH.replace("умер", "скончался")),
                                CFG["similarity"])

    def test_other_angle_lands_in_the_gray_zone(self):
        """А смена угла — ровно тот случай, ради которого всё затевалось.

        Общее слово у выпуска и срочного было ровно одно — «карри». Порог
        спорной зоны обязан быть ниже такого совпадения, иначе вопрос модели
        просто не задаётся.
        """
        self.assertTrue(dedup.gray(self.score(DEATH, TRIBUTE)))
        self.assertTrue(dedup.gray(self.score(DEATH, CAUSE)))

    def test_unrelated_news_stay_below(self):
        self.assertFalse(dedup.gray(self.score(DEATH, OTHER)))


class TestWeighing(unittest.TestCase):
    """Порядок вопросов: про что спрашиваем, когда лимит не резиновый."""

    def docs(self, *texts):
        return [set(textutil.signature(t).split()) for t in texts]

    def test_rare_shared_word_outweighs_a_common_one(self):
        """«Карри» встречается в паре новостей, «компания» — в каждой второй.
        Спрашивать модель надо про первую пару, и лимит вопросов тратится
        сверху вниз."""
        pool = self.docs(DEATH, TRIBUTE,
                         "Компания представила новый продукт",
                         "Компания отчиталась за новый квартал",
                         *["Компания %d показала новый отчёт" % n for n in range(20)])
        weights = dedup.idf(pool)
        rare = dedup.weigh(pool[0], pool[1], weights)         # общее — «карри»
        common = dedup.weigh(pool[2], pool[3], weights)       # общее — «компания»
        self.assertGreater(rare, common)

    def test_nothing_in_common_weighs_nothing(self):
        pool = self.docs(DEATH, OTHER)
        self.assertEqual(dedup.weigh(pool[0], pool[1], dedup.idf(pool)), 0.0)


class TestPairKey(unittest.TestCase):
    def test_order_does_not_matter(self):
        self.assertEqual(dedup.pair_key("а б", "в г"), dedup.pair_key("в г", "а б"))

    def test_different_pairs_differ(self):
        self.assertNotEqual(dedup.pair_key("а б", "в г"), dedup.pair_key("а б", "д"))


class TestAgainstHistory(DedupCase):
    def test_same_event_other_wording_is_dropped(self):
        """Ровно случай Тима Карри: вечером выпуск, ночью — то же самое."""
        self.send(DEATH, "hollywoodreporter")
        shortlists = [("cinema", [self.group(TRIBUTE, "indiewire")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(shortlists[0][1], [])
        self.assertEqual(len(self.asked), 1)

    def test_next_chapter_survives(self):
        """«Названа причина смерти» — уже другая новость, и она должна прийти."""
        self.answer(False)
        self.send(DEATH, "hollywoodreporter")
        shortlists = [("cinema", [self.group(CAUSE, "deadline")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 1)

    def test_unrelated_news_is_not_even_asked_about(self):
        self.send(DEATH, "hollywoodreporter")
        shortlists = [("ai", [self.group(OTHER, "nvidia")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 1)
        self.assertEqual(self.asked, [])

    def test_verdict_is_cached(self):
        """Второй выпуск за тот же вопрос не платит."""
        self.send(DEATH, "hollywoodreporter")
        for _ in range(2):
            shortlists = [("cinema", [self.group(TRIBUTE, "indiewire")])]
            dedup.prune(self.conn, self.index(), shortlists)
            self.assertEqual(shortlists[0][1], [])
        self.assertEqual(len(self.asked), 1)

    def test_model_failure_leaves_the_word_layer(self):
        """Модель молчит — новость идёт в выпуск, а не пропадает."""
        def fail(pairs):
            raise LLMError("нет связи")
        dedup.judge_duplicates = fail
        self.send(DEATH, "hollywoodreporter")
        shortlists = [("cinema", [self.group(TRIBUTE, "indiewire")])]
        self.assertEqual(dedup.prune(self.conn, self.index(), shortlists), 0.0)
        self.assertEqual(len(shortlists[0][1]), 1)

    def test_switch_off_asks_nothing(self):
        CFG["dup_llm"] = False
        self.send(DEATH, "hollywoodreporter")
        shortlists = [("cinema", [self.group(TRIBUTE, "indiewire")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 1)
        self.assertEqual(self.asked, [])

    def test_old_history_is_left_alone(self):
        """Через неделю это уже не повтор, а возвращение к теме."""
        CFG["dup_window_h"] = 1
        self.send(DEATH, "hollywoodreporter", days_ago=3)
        shortlists = [("cinema", [self.group(TRIBUTE, "indiewire")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 1)
        self.assertEqual(self.asked, [])

    def test_only_the_top_of_a_section_is_checked(self):
        """Хвост раздела в выпуск не попадёт — и вопросов о нём не задаём."""
        CFG["dup_candidates"] = 1
        self.send(DEATH, "hollywoodreporter")
        shortlists = [("cinema", [self.group(OTHER, "nvidia"),
                                  self.group(TRIBUTE, "indiewire")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 2)
        self.assertEqual(self.asked, [])

    def test_question_limit_holds(self):
        CFG["dup_llm_max"] = 1
        self.send(DEATH, "hollywoodreporter")
        shortlists = [("cinema", [self.group(TRIBUTE, "indiewire"),
                                  self.group(CAUSE, "deadline")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(self.asked[0]), 1)


class TestBatching(DedupCase):
    """Вопросы уходят пачками: обрыв одной не должен стоить всех вердиктов.

    Лимит вопросов поднят до сотни, а сотня пар одним куском упирается в
    потолок ответа модели — ровно та беда, ради которой пачками пишутся и
    карточки выпуска (`llm.summarize`).
    """

    def pairs(self, count):
        """count пар «повтор истории», про каждую придётся спросить отдельно.

        Номера двузначные не для красоты: `textutil.signature` выбрасывает
        односимвольные токены, и с «номер 0» против «номер 1» у всех пар
        совпали бы и сигнатуры, и ключ — а значит, и вердикт на всех один.
        """
        out = []
        for at in range(count):
            title = "Актёр номер %d ушёл из жизни в 80 лет" % (10 + at)
            self.send(title, "hollywoodreporter")
            out.append(self.group("Коллеги прощаются с актёром номер %d" % (10 + at),
                                  "indiewire"))
        return out

    def test_questions_are_split_into_batches(self):
        CFG["dup_batch"] = 2
        shortlists = [("cinema", self.pairs(5))]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(sorted(len(batch) for batch in self.asked), [1, 2, 2])
        self.assertEqual(shortlists[0][1], [])          # и все пятеро сняты

    def test_a_broken_batch_costs_only_itself(self):
        """Первая пачка не ответила — вердикты остальных всё равно в силе."""
        CFG["dup_batch"] = 2
        calls = []

        def flaky(pairs):
            calls.append(list(pairs))
            if len(calls) == 1:
                raise LLMError("оборвался ответ")
            return ({i: llm.Verdict(True, False) for i in range(len(pairs))},
                    {"in": 5, "out": 5})
        dedup.judge_duplicates = flaky

        shortlists = [("cinema", self.pairs(4))]
        dedup.prune(self.conn, self.index(), shortlists)
        # четыре пары, пачки по две: одна пачка потеряна, вторая сработала
        self.assertEqual(len(shortlists[0][1]), 2)

    def test_a_broken_batch_is_not_cached_as_a_verdict(self):
        """Про пары из потерянной пачки спросим ещё раз, а не запомним «разные»."""
        CFG["dup_batch"] = 2

        def fail(pairs):
            raise LLMError("нет связи")
        dedup.judge_duplicates = fail
        shortlists = [("cinema", self.pairs(2))]
        self.assertEqual(dedup.prune(self.conn, self.index(), shortlists), 0.0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM dupes").fetchone()["c"], 0)

    def test_the_limit_holds_across_batches(self):
        CFG["dup_batch"], CFG["dup_llm_max"] = 2, 3
        # история наполняется здесь, и `index()` должен читать её уже полной
        shortlists = [("cinema", self.pairs(5))]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(sum(len(batch) for batch in self.asked), 3)


class TestInsideOneSection(DedupCase):
    """Два кандидата ОДНОГО раздела об одном событии — их склеивают.

    Ровно случай с Эль-Ниньо: две заметки о том же исследовании кораллов
    пришли в «Климат» одним выпуском, одна за другой. Связывания тут мало —
    список кандидатов раздела фильтруется один раз, до отбора, и связанная
    пара доезжала до выпуска целиком.
    """

    def test_two_candidates_become_one_cluster(self):
        first, second = self.group(DEATH, "hollywoodreporter"), \
            self.group(TRIBUTE, "indiewire")
        shortlists = [("cinema", [first, second])]
        dedup.prune(self.conn, self.index(), shortlists)

        self.assertEqual(len(shortlists[0][1]), 1)
        self.assertIs(shortlists[0][1][0], first)      # остаётся первый по прескорингу
        self.assertEqual(len(first), 2)                # но материалов у него два

    def test_the_card_is_written_by_both_notes(self):
        """Смысл склейки: карточку пишет модель, и теперь ей видны обе заметки."""
        first, second = self.group(DEATH, "hollywoodreporter"), \
            self.group(TRIBUTE, "indiewire")
        dedup.prune(self.conn, self.index(), [("cinema", [first, second])])
        self.assertEqual({i["source_id"] for i in rank.voices(first)},
                         {"hollywoodreporter", "indiewire"})

    def test_different_events_stay_apart(self):
        """«Названа причина смерти» — следующая новость, склеивать нечего."""
        self.answer(False)
        first, second = self.group(DEATH, "hollywoodreporter"), \
            self.group(CAUSE, "deadline")
        shortlists = [("cinema", [first, second])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 2)
        self.assertEqual(len(first), 1)

    def test_a_chain_of_three_collapses_into_one(self):
        """А и Б — одно, Б и В — одно: в выпуске должен остаться один кластер,
        и материалы всех троих должны лежать в нём, а не потеряться."""
        first = self.group(DEATH, "hollywoodreporter")
        second = self.group(TRIBUTE, "indiewire")
        third = self.group("Тим Карри ушёл из жизни", "variety")
        shortlists = [("cinema", [first, second, third])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 1)
        self.assertEqual({i["source_id"] for i in shortlists[0][1][0]},
                         {"hollywoodreporter", "indiewire", "variety"})

    def test_the_chain_closes_even_when_it_starts_in_the_middle(self):
        """Модель развела А и Б, но признала одним А—В и Б—В. Значит, событие
        всё-таки одно: в выпуске должен остаться один кластер, и материалы
        всех троих — в нём.

        Проверяем именно это: убирать из выпуска надо тот кластер, в котором
        материалы уже не лежат, а не тот, кого назвали в паре, — иначе кластер
        остаётся стоять, а его заметки к этому времени уже переехали к соседу.
        """
        self.answers(lambda seen, new: not ("умер" in seen and "прощаются" in new))
        first = self.group(DEATH, "hollywoodreporter")
        second = self.group(TRIBUTE, "indiewire")
        third = self.group(MEMORY, "variety")
        shortlists = [("cinema", [first, second, third])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 1)
        self.assertEqual({i["source_id"] for i in shortlists[0][1][0]},
                         {"hollywoodreporter", "indiewire", "variety"})

    def test_nothing_is_fused_into_a_candidate_that_is_leaving(self):
        """Первого кандидата читатель уже видел — его убирают. Вливать в него
        второго нельзя: вместе с ним ушла бы и вторая новость, про которую
        модель прямо сказала, что она ДРУГАЯ.

        «Коллеги прощаются» — повтор вечернего «умер Тим Карри»; «названа
        причина смерти» — следующая новость, и прийти она должна.
        """
        self.answers(lambda seen, new: not ("умер" in seen and "причина" in new))
        self.send(DEATH, "hollywoodreporter")
        first, second = self.group(TRIBUTE, "indiewire"), self.group(CAUSE, "deadline")
        shortlists = [("cinema", [first, second])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(shortlists[0][1], [second])
        self.assertEqual(len(second), 1)

    def test_the_budget_goes_to_the_pairs_that_duplicate_right_now(self):
        """Спорных пар в выпуске под сотню, а спросить можно про три десятка.

        Пара внутри раздела даст два одинаковых блока сразу; пара из разных
        разделов — только если оба кандидата пройдут отбор. Первых при этом на
        порядок меньше, и по одному весу общих слов они вытеснялись вторыми:
        в выпуске из шестнадцати разделов до модели доезжала одна из восьми.

        Здесь у межразделной пары общее слово редкое («Нвидиа»), а у
        внутрираздельной — частое («компания»), то есть вес против неё.
        Спросить всё равно должны про неё.
        """
        CFG["dup_llm_max"] = 1
        first = self.group("Компания открыла офис в Мюнхене", "variety")
        second = self.group("Компания Нвидиа закрыла павильон в Лондоне", "deadline")
        far = self.group("Нвидиа отчиталась за третий квартал", "reuters")
        shortlists = [("cinema", [first, second]), ("ai", [far])]
        # «компания» должна быть частым словом — иначе редкость слов ни при чём
        for at, name in enumerate(("Астра", "Бета", "Гамма", "Дельта", "Эпсилон")):
            shortlists.append(("pad%d" % at,
                               [self.group("Компания %s объявила о планах" % name,
                                           "wire%d" % at)]))
        dedup.prune(self.conn, self.index(), shortlists)

        self.assertEqual(len(self.asked), 1)
        self.assertEqual(len(self.asked[0]), 1)
        seen, new = self.asked[0][0]
        self.assertIn("Мюнхене", seen)
        self.assertIn("павильон", new)
        self.assertEqual(len(shortlists[0][1]), 1)      # и склеили их же

    def test_switch_off_keeps_both(self):
        CFG["dup_llm"] = False
        first, second = self.group(DEATH, "hollywoodreporter"), \
            self.group(TRIBUTE, "indiewire")
        shortlists = [("cinema", [first, second])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 2)
        self.assertEqual(self.asked, [])


class TestAcrossSections(DedupCase):
    """Два кандидата РАЗНЫХ разделов об одном событии — их только связывают."""

    def test_loser_falls_only_when_the_winner_is_taken(self):
        index = self.index()
        first, second = self.group(DEATH, "hollywoodreporter"), \
            self.group(TRIBUTE, "indiewire")
        shortlists = [("cinema", [first]), ("main", [second])]
        dedup.prune(self.conn, index, shortlists)

        # пока никого не выбрали — оба на месте: выбросить кандидата, которого
        # никто не показал, значит просто потерять новость
        self.assertEqual(len(shortlists[0][1]), 1)
        self.assertEqual(len(shortlists[1][1]), 1)
        self.assertFalse(index.seen(second, CFG["similarity"]))

        index.remember(first)
        self.assertTrue(index.seen(second, CFG["similarity"]))

    def test_sections_are_not_fused(self):
        """Склейка решила бы за отбор, в каком разделе новости жить: не попади
        она в первый — из второго её уже никто бы не показал."""
        first, second = self.group(DEATH, "hollywoodreporter"), \
            self.group(TRIBUTE, "indiewire")
        shortlists = [("cinema", [first]), ("main", [second])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)

    def test_different_events_are_not_linked(self):
        self.answer(False)
        index = self.index()
        first, second = self.group(DEATH, "hollywoodreporter"), \
            self.group(CAUSE, "deadline")
        dedup.prune(self.conn, index, [("cinema", [first]), ("main", [second])])
        index.remember(first)
        self.assertFalse(index.seen(second, CFG["similarity"]))


class TestConfirmNew(DedupCase):
    """То же для срочного — там повтор обходится дороже всего."""

    def test_repeat_of_the_evening_issue_is_cancelled(self):
        self.send(DEATH, "hollywoodreporter")
        fresh, _cost = dedup.confirm_new(self.conn, self.index(),
                                         [self.group(TRIBUTE, "indiewire")])
        self.assertEqual(fresh, [])

    def test_genuinely_new_event_goes_through(self):
        self.answer(False)
        self.send(DEATH, "hollywoodreporter")
        fresh, _cost = dedup.confirm_new(self.conn, self.index(),
                                         [self.group(CAUSE, "deadline")])
        self.assertEqual(len(fresh), 1)

    def test_empty_input_costs_nothing(self):
        self.assertEqual(dedup.confirm_new(self.conn, self.index(), []), ([], 0.0))


class TestShrunkRetelling(DedupCase):
    """Пересказ, который знает МЕНЬШЕ уже показанного.

    Ровно случай с Кипром: в 15:14 читателю пришло «перевернулась лодка с 270,
    159 найдены», в 17:08 — «паром с 260 пассажирами затонул». Формально
    событие «другое»: другое судно, другой глагол, другое число. По сути ему
    не сообщили ничего и вдобавок отняли спасённых.
    """

    def test_a_poorer_retelling_is_dropped(self):
        self.says(llm.LESS)
        self.send(BOAT, "reuters-world")
        shortlists = [("world", [self.group(FERRY, "rbc")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(shortlists[0][1], [])

    def test_a_moved_counter_goes_through(self):
        """А «найдено 200 из 270» — это новость, и прийти она должна."""
        self.says(llm.MORE, "найдено 200 из 270")
        self.send(BOAT, "reuters-world")
        shortlists = [("world", [self.group(RESCUE, "aljazeera")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 1)

    def test_the_update_remembers_what_it_adds(self):
        """Найденное новое доезжает до показа: без него читателю пришлось бы
        искать отличие самому, сличая два сообщения."""
        self.says(llm.MORE, "найдено 200 из 270")
        self.send(BOAT, "reuters-world")
        index, group = self.index(), self.group(RESCUE, "aljazeera")
        dedup.prune(self.conn, index, [("world", [group])])
        head = rank.primary_of(group)["url_hash"]
        self.assertTrue(index.prior_of(head))
        self.assertEqual(index.gain_of(head), "найдено 200 из 270")

    def test_a_continuation_has_nothing_to_add(self):
        """У продолжения сюжета новость своя: дополнять ему нечего, и строки
        «что нового» под ним быть не должно."""
        self.says(llm.NEXT, "причина смерти")
        self.send(DEATH, "hollywoodreporter")
        index, group = self.index(), self.group(CAUSE, "deadline")
        dedup.prune(self.conn, index, [("cinema", [group])])
        head = rank.primary_of(group)["url_hash"]
        self.assertTrue(index.prior_of(head))
        self.assertEqual(index.gain_of(head), "")

    def test_the_whole_answer_is_cached(self):
        """В кэше лежит само слово модели, а не выжимка из него: иначе второй
        выпуск не отличил бы урезанный пересказ от прямого повтора."""
        self.says(llm.LESS)
        self.send(BOAT, "reuters-world")
        dedup.prune(self.conn, self.index(),
                    [("world", [self.group(FERRY, "rbc")])])
        row = self.conn.execute("SELECT * FROM dupes").fetchone()
        self.assertEqual(row["kind"], llm.LESS)
        self.assertEqual((row["same"], row["follows"]), (1, 0))

    def test_an_unknown_word_is_treated_as_silence(self):
        """Модель ответила не по шкале — ведём себя как при обрыве связи:
        новость идёт в выпуск, а не пропадает."""
        self.says("возможно")
        self.send(BOAT, "reuters-world")
        shortlists = [("world", [self.group(FERRY, "rbc")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 1)


class TestMovedCounter(DedupCase):
    """Повтор, у которого сдвинулся счётчик.

    Обратная беда, и она страшнее. «У берегов Кипра найдены 200 из 270
    пассажиров» совпадает с вечерним «перевернулась лодка с 270 людьми, 159
    найдены» на 0.56 при пороге 0.32 — первый слой снимает такую заметку
    молча, и «нашли ещё сорок человек» до читателя не доходит вовсе. А следит
    он именно за этим.
    """

    def issue(self, title, source="aljazeera"):
        """История, индекс и выпуск с одним кандидатом — в правильном порядке."""
        self.send(BOAT, "reuters-world")
        index, group = self.index(), self.group(title, source)
        return index, group, [("world", [group])]

    def test_a_repeat_with_a_new_number_is_asked_about(self):
        self.says(llm.MORE, "найдено 200 из 270")
        _index, _group, shortlists = self.issue(RESCUE)
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(self.asked), 1)

    def test_the_moved_counter_returns_to_the_issue(self):
        self.says(llm.MORE, "найдено 200 из 270")
        index, group, shortlists = self.issue(RESCUE)
        dedup.prune(self.conn, index, shortlists)
        self.assertEqual(shortlists[0][1], [group])
        self.assertEqual(index.gain_of(rank.primary_of(group)["url_hash"]),
                         "найдено 200 из 270")

    def test_an_unnamed_update_stays_out(self):
        """Слова уже сказали «повтор». Вернуть заметку может только названный
        факт, а не «кажется, тут что-то новое»."""
        self.says(llm.MORE, "")
        _index, _group, shortlists = self.issue(RESCUE)
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(shortlists[0][1], [])

    def test_the_same_number_told_again_stays_out(self):
        """Другая оценка того же — не сдвиг счётчика, а тот же счётчик."""
        self.says(llm.LESS)
        _index, _group, shortlists = self.issue(RESCUE)
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(shortlists[0][1], [])

    def test_a_repeat_without_numbers_costs_nothing(self):
        """Пересказ без единого нового числа — обычный повтор: его снимает
        первый слой, и вопроса модели он не стоит."""
        self.send(DEATH, "hollywoodreporter")
        shortlists = [("cinema", [self.group(DEATH.replace("умер", "скончался"),
                                             "variety")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(shortlists[0][1], [])
        self.assertEqual(self.asked, [])

    def test_silence_leaves_the_repeat_where_it_was(self):
        """Модель не ответила — ведём себя как первый слой: повтор снят.

        Здесь молчание толкуется наоборот, чем в спорной зоне, и это нарочно:
        там слова сомневаются и новость идёт в выпуск, здесь слова уже сказали
        «повтор», и без ответа модели решение остаётся за ними.
        """
        def fail(pairs):
            raise LLMError("нет связи")
        dedup.judge_duplicates = fail
        _index, _group, shortlists = self.issue(RESCUE)
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(shortlists[0][1], [])

    def test_an_old_repeat_is_not_reopened(self):
        """Через неделю это уже не «сдвинулся счётчик», а возвращение к теме."""
        CFG["dup_window_h"] = 1
        self.send(BOAT, "reuters-world", days_ago=3)
        shortlists = [("world", [self.group(RESCUE, "aljazeera")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(self.asked, [])


class TestMoved(unittest.TestCase):
    """Дешёвый повод спросить: появилось ли у кандидата новое число."""

    def words(self, text):
        return set(textutil.signature(text).split())

    def test_a_new_number_counts(self):
        self.assertTrue(self.moved(RESCUE, BOAT))

    def test_the_same_numbers_do_not(self):
        self.assertFalse(self.moved("Найдены 159 из 270 пассажиров",
                                    "Лодка с 270 людьми, 159 найдены"))

    def test_words_alone_are_not_a_counter(self):
        self.assertFalse(self.moved("Спасательная операция продолжается",
                                    "Лодка с 270 людьми, 159 найдены"))

    def moved(self, new, seen):
        return dedup.moved(self.words(new), self.words(seen))


class TestBreakingNeedsSomethingNew(DedupCase):
    """Планка срочного на ступень выше: мало, чтобы событие было «не то же».

    Выпуск — список, и лишняя карточка в нём стоит строчки. Срочное приходит
    отдельным сообщением и со звуком, поэтому «то же самое, но чуть иначе»
    второй раз за вечер там недопустимо.
    """

    def candidate(self, title=FERRY, source="rbc"):
        """История и кандидат к ней. Историю наполняем ПЕРВОЙ: индекс читает
        её в момент создания, и построенный раньше времени пуст."""
        self.send(BOAT, "reuters-world")
        return self.index(), [self.group(title, source)]

    def test_an_update_without_a_named_fact_does_not_wake_anyone(self):
        """Модель выбрала «есть новое», а назвать его не смогла. Промпт велит
        в сомнениях выбирать more — значит, пустое поле и есть сомнение."""
        self.says(llm.MORE, "")
        index, groups = self.candidate()
        fresh, _cost = dedup.confirm_new(self.conn, index, groups)
        self.assertEqual(fresh, [])

    def test_a_named_fact_breaks_through(self):
        """А названное новое будит: за этим срочное и нужно."""
        self.says(llm.MORE, "найдено 200 из 270")
        self.send(BOAT, "reuters-world")
        index = self.index()
        group = self.group("Спасатели у Кипра подняли на борт ещё 41 человека",
                           "aljazeera")
        fresh, _cost = dedup.confirm_new(self.conn, index, [group])
        self.assertEqual(fresh, [group])
        self.assertEqual(index.gain_of(rank.primary_of(group)["url_hash"]),
                         "найдено 200 из 270")

    def test_a_shrunk_retelling_never_breaks(self):
        self.says(llm.LESS)
        index, groups = self.candidate()
        fresh, _cost = dedup.confirm_new(self.conn, index, groups)
        self.assertEqual(fresh, [])

    def test_a_cancelled_candidate_does_not_return_in_the_issue(self):
        """Событие закрыто целиком: показывать в выпуске то, что уже было,
        незачем — ни целиком, ни в урезанном виде."""
        self.says(llm.LESS)
        index, groups = self.candidate()
        dedup.confirm_new(self.conn, index, groups)
        self.assertTrue(index.seen(groups[0], CFG["similarity"]))

    def test_only_the_call_is_cancelled_not_the_event(self):
        """Отменяется звонок, а не новость: строгость срочного касается
        срочного. В плановом выпуске эту заметку рассудит `prune` — по своей,
        более мягкой мерке.
        """
        self.says(llm.MORE, "")
        index, groups = self.candidate()
        dedup.confirm_new(self.conn, index, groups)
        self.assertFalse(index.seen(groups[0], CFG["similarity"]))

    def test_a_word_for_word_repeat_is_not_even_asked_about(self):
        """Граница нарочная. В выпуске повтор со сдвинувшимся счётчиком
        переспрашивается у модели (`moved`), в срочном — нет: там слово первого
        слоя окончательное. Разбудить человека ради того, что он уже читал,
        хуже, чем показать это же строчкой в очередном выпуске, — куда такая
        новость и попадёт.
        """
        self.says(llm.MORE, "найдено 200 из 270")
        index, groups = self.candidate(RESCUE, "aljazeera")
        fresh, _cost = dedup.confirm_new(self.conn, index, groups)
        self.assertEqual(fresh, [])
        self.assertEqual(self.asked, [])

    def test_the_issue_is_not_that_strict(self):
        """В выпуске тот же вердикт новость не снимает: там она стоит строчки,
        а не звонка среди ночи."""
        self.says(llm.MORE, "")
        self.send(BOAT, "reuters-world")
        shortlists = [("world", [self.group(FERRY, "rbc")])]
        dedup.prune(self.conn, self.index(), shortlists)
        self.assertEqual(len(shortlists[0][1]), 1)

    def test_switch_off_restores_the_old_behaviour(self):
        CFG["dup_breaking_gain"] = False
        self.says(llm.MORE, "")
        index, groups = self.candidate()
        fresh, _cost = dedup.confirm_new(self.conn, index, groups)
        self.assertEqual(len(fresh), 1)


class TestSentIndex(DedupCase):
    """Что `near` находит там, где `seen` уже не смотрит."""

    def test_near_sees_every_item_of_the_cluster(self):
        """Лицо кластера — не единственный его материал: слова второго
        источника могут сойтись с историей, даже когда слова первого не сошлись."""
        self.send(DEATH, "hollywoodreporter")
        group = self.group(OTHER, "nvidia") + self.group(DEATH, "variety")
        score, _sig, text, _at = self.index().near(group)
        self.assertGreaterEqual(score, CFG["similarity"])
        self.assertIn("Карри", text)

    def test_near_on_empty_history(self):
        self.assertEqual(rank.SentIndex().near(self.group(DEATH)),
                         (0.0, "", "", ""))

    def test_marked_signature_counts_as_seen(self):
        index = self.index()
        group = self.group(DEATH)
        self.assertFalse(index.seen(group, CFG["similarity"]))
        index.mark(rank.primary_of(group)["sig"])
        self.assertTrue(index.seen(group, CFG["similarity"]))


class TestBreakingEndToEnd(DedupCase):
    """Тот самый случай целиком: вечерний выпуск → срочное четыре часа спустя."""

    def setUp(self):
        DedupCase.setUp(self)
        conn = storage.db()
        try:
            for table in ("meta", "runs", "subscribers", "alerts"):
                conn.execute("DELETE FROM %s" % table)
            conn.commit()
            subscribers.add(conn, CHAT, role="member", title="тест")
        finally:
            conn.close()
        CFG["use_kev"] = False                  # тесты в сеть не ходят
        self.posted = []
        self._breaking = (breaking.tg_send, breaking.rate_urgency,
                          breaking.summarize)
        breaking.tg_send = lambda chat, text, keyboard=None, silent=None: \
            self.posted.append(text)
        breaking.rate_urgency = lambda groups, persona: (
            [{"id": i, "urgency": 9.5, "scope": "global", "category": "media"}
             for i in range(len(groups))], {"in": 5, "out": 5})
        breaking.summarize = lambda picked, persona, lang: (
            {0: {"headline": "Умер Тим Карри", "what": "суть", "why": "важно"}},
            {"in": 5, "out": 5})

    def tearDown(self):
        (breaking.tg_send, breaking.rate_urgency,
         breaking.summarize) = self._breaking
        DedupCase.tearDown(self)

    def hot(self, title):
        """Событие, подтверждённое четырьмя изданиями, — кандидат в срочные."""
        conn = storage.db()
        try:
            for source in ("indiewire", "deadline", "guardian-film", "variety"):
                row = item("https://%s.com/curry" % source, title, source)
                conn.execute(
                    "INSERT OR REPLACE INTO items(url_hash,url,source_id,tier,"
                    "category,title,summary,published_at,fetched_at,sig,social,"
                    "section) VALUES (:url_hash,:url,:source_id,:tier,:category,"
                    ":title,:summary,:published_at,:fetched_at,:sig,:social,"
                    "'cinema')", dict(row, fetched_at=now_iso()))
            conn.commit()
        finally:
            conn.close()

    def test_evening_issue_is_not_repeated_as_breaking(self):
        self.send(DEATH, "hollywoodreporter")
        self.hot(TRIBUTE)
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.posted, [])

    def test_next_chapter_still_breaks_through(self):
        """Дедупликация не должна затыкать рот новостям, которые ДРУГИЕ."""
        self.answer(False)
        self.send(DEATH, "hollywoodreporter")
        self.hot(CAUSE)
        self.assertEqual(breaking.check(chat_id=CHAT), 1)
        self.assertEqual(len(self.posted), 1)

    def test_the_evening_wreck_does_not_come_back_two_hours_later(self):
        """Тот вечер целиком: в 15:14 ушло «перевернулась лодка с 270, 159
        найдены», в 17:08 те же события пришли из других лент и короче."""
        self.says(llm.LESS)
        self.send(BOAT, "reuters-world")
        self.hot(FERRY)
        self.assertEqual(breaking.check(chat_id=CHAT), 0)
        self.assertEqual(self.posted, [])

    def test_an_update_says_what_is_new_in_it(self):
        """А то, что прошло, объясняет читателю разницу само: сличать два
        сообщения — не его работа."""
        self.says(llm.MORE, "найдено 200 из 270")
        self.send(BOAT, "reuters-world")
        self.hot("Спасатели у Кипра подняли на борт ещё 41 человека")
        self.assertEqual(breaking.check(chat_id=CHAT), 1)
        self.assertIn("🔁 Новое: найдено 200 из 270", self.posted[0])


if __name__ == "__main__":
    unittest.main()
