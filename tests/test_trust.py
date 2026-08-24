# -*- coding: utf-8 -*-
"""Реестр источников: издатель, класс, доверие и кто становится лицом кластера."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import breaking, rank, trust  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

from test_core import item  # noqa: E402


class TestPublisher(unittest.TestCase):
    def test_one_redaction_is_one_publisher(self):
        """Шесть лент Guardian — одна редакция, а не шесть подтверждений."""
        names = {trust.publisher(s) for s in
                 ("guardian-world", "guardian-business", "guardian-sport",
                  "guardian-film", "guardian-health", "guardian-environment")}
        self.assertEqual(names, {"theguardian.com"})

    def test_google_news_shopfront_keeps_real_publisher(self):
        """Витрина Google News — не издатель: Reuters остаётся Reuters."""
        self.assertEqual(trust.publisher("reuters-world"), "reuters.com")
        self.assertEqual(trust.publisher("reuters-markets"), "reuters.com")
        self.assertEqual(trust.publisher("afp"), "afp.com")

    def test_subdomains_resolve_to_publisher(self):
        self.assertEqual(trust.publisher("bbc-world"), "bbc.co.uk")
        self.assertEqual(trust.publisher("bbc-sport"), "bbc.co.uk")
        self.assertEqual(trust.publisher("ap-topnews"), "apnews.com")

    def test_unknown_source_is_its_own_publisher(self):
        """Чужой фид из /feed add остаётся независимым подтверждением."""
        self.assertEqual(trust.publisher("nobody-knows-me"), "nobody-knows-me")
        self.assertNotEqual(trust.publisher("mine-a"), trust.publisher("mine-b"))


class TestTrust(unittest.TestCase):
    def test_press_release_below_independent_desk(self):
        """Главное, ради чего заведён модуль: анонс вендора весит меньше разбора."""
        self.assertLess(trust.trust("openai"), trust.trust("arstechnica"))
        self.assertLess(trust.trust("nvidia-blog"), trust.trust("techpowerup"))

    def test_aggregator_below_journal(self):
        self.assertLess(trust.trust("phys-all"), trust.trust("nature"))
        self.assertLess(trust.trust("sd-medicine"), trust.trust("lancet"))

    def test_state_agency_lowest(self):
        self.assertLess(trust.trust("tass"), trust.trust("bbc-world"))
        self.assertEqual(trust.kind("tass"), "state")

    def test_public_broadcaster_is_not_state(self):
        """Общественный вещатель — не госагентство: у него независимость в законе."""
        self.assertEqual(trust.kind("bbc-world"), "independent")
        self.assertEqual(trust.kind("dw-russian"), "independent")

    def test_unknown_source_falls_back_to_tier(self):
        """Незнакомый источник ведёт себя как раньше — по tier."""
        self.assertAlmostEqual(trust.trust("no-such-source"),
                               trust.KIND_TRUST["other"])


class TestPrimary(unittest.TestCase):
    def test_analysis_wins_over_press_release(self):
        """Ссылка ведёт на разбор, хотя пресс-релиз и есть первоисточник."""
        group = [item("https://openai.com/a", "GPT-6", source="openai", tier=1),
                 item("https://arstechnica.com/a", "GPT-6",
                      source="arstechnica", tier=2)]
        self.assertEqual(rank.primary_of(group)["source_id"], "arstechnica")

    def test_press_release_alone_still_leads(self):
        """Проверять было некому — порядок прежний, по tier."""
        group = [item("https://openai.com/a", "GPT-6", source="openai", tier=1),
                 item("https://collider.com/a", "GPT-6", source="collider", tier=3)]
        self.assertEqual(rank.primary_of(group)["source_id"], "openai")

    def test_state_agency_yields_to_wire(self):
        group = [item("https://tass.ru/a", "Событие", source="tass", tier=2),
                 item("https://apnews.com/a", "Событие", source="ap-topnews", tier=1)]
        self.assertEqual(rank.primary_of(group)["source_id"], "ap-topnews")


class TestPrescore(unittest.TestCase):
    def test_trusted_source_scores_higher(self):
        strong = [item("https://apnews.com/a", "Событие", source="ap-topnews", tier=1)]
        weak = [item("https://tass.ru/b", "Событие", source="tass", tier=1)]
        self.assertGreater(rank.prescore(strong), rank.prescore(weak))


class TestConsensus(unittest.TestCase):
    """`is_hot` — дешёвый фильтр срочного, до всякой модели."""

    def setUp(self):
        self.saved = {k: CFG[k] for k in
                      ("breaking_min_sources", "breaking_min_wires",
                       "breaking_min_wide", "breaking_social")}

    def tearDown(self):
        CFG.update(self.saved)

    def test_same_redaction_is_not_consensus(self):
        """Две ленты Guardian плюс tier-1 — это два издателя, а не три сайта."""
        group = [
            item("https://theguardian.com/a", "Наводнение", source="guardian-world"),
            item("https://theguardian.com/b", "Наводнение",
                 source="guardian-environment"),
            item("https://nasa.gov/c", "Наводнение", source="nasa", tier=1),
        ]
        self.assertFalse(breaking.is_hot(group))

    def test_three_real_publishers_with_primary(self):
        group = [
            item("https://theguardian.com/a", "Наводнение", source="guardian-world"),
            item("https://bbc.co.uk/b", "Наводнение", source="bbc-world"),
            item("https://nasa.gov/c", "Наводнение", source="nasa", tier=1),
        ]
        self.assertTrue(breaking.is_hot(group))

    def test_two_wires_are_enough(self):
        """AP и Reuters об одном событии — эталон подтверждения."""
        group = [
            item("https://apnews.com/a", "Землетрясение", source="ap-topnews", tier=1),
            item("https://reuters.com/b", "Землетрясение",
                 source="reuters-world", tier=1),
        ]
        self.assertTrue(breaking.is_hot(group))

    def test_soft_section_gets_wide_consensus(self):
        """В спорте нет ни одного tier-1 — раньше срочное там было невозможно."""
        group = [
            item("https://bbc.co.uk/a", "Финал", source="bbc-sport"),
            item("https://espn.com/b", "Финал", source="espn"),
            item("https://skysports.com/c", "Финал", source="skysports"),
            item("https://championat.com/d", "Финал", source="championat"),
        ]
        self.assertTrue(breaking.is_hot(group))

    def test_three_soft_publishers_still_quiet(self):
        """Широкий консенсус — это именно широкий: трёх профильных сайтов мало."""
        group = [
            item("https://bbc.co.uk/a", "Финал", source="bbc-sport"),
            item("https://espn.com/b", "Финал", source="espn"),
            item("https://skysports.com/c", "Финал", source="skysports"),
        ]
        self.assertFalse(breaking.is_hot(group))



class TestRegistry(unittest.TestCase):
    """Реестр и подборка не должны разъезжаться."""

    def test_every_builtin_source_is_classified(self):
        """Встроенный источник без записи в реестре молча падает в 'other'."""
        from newsdigest.profiles import BUILTIN
        unknown = sorted({feed[0] for body in BUILTIN.values()
                          for feed in body["feeds"]}
                         - set(trust.SOURCE_META))
        self.assertEqual(unknown, [], "нет записи в SOURCE_META: %s" % unknown)

    def test_every_candidate_is_classified(self):
        """Кандидат должен попасть в нужную весовую категорию сразу при добавлении."""
        from newsdigest import candidates
        unknown = sorted({row[1] for row in candidates.all_candidates()}
                         - set(trust.SOURCE_META))
        self.assertEqual(unknown, [], "нет записи в SOURCE_META: %s" % unknown)

    def test_candidates_do_not_duplicate_existing_feeds(self):
        from newsdigest import candidates
        from newsdigest.profiles import BUILTIN
        have = {feed[1] for body in BUILTIN.values() for feed in body["feeds"]}
        clash = [row[1] for row in candidates.all_candidates() if row[2] in have]
        self.assertEqual(clash, [], "такие ленты уже есть: %s" % clash)

    def test_every_section_has_a_fast_lane_or_a_reason(self):
        """Без tier-1 и без агентств срочное в разделе невозможно в принципе."""
        from newsdigest.profiles import BUILTIN, DEFAULT_SECTIONS
        from newsdigest import candidates
        planned = {row[0] for row in candidates.all_candidates()}
        blind = []
        for topic in DEFAULT_SECTIONS:
            feeds = BUILTIN[topic]["feeds"]
            if any(f[2] == 1 for f in feeds) or topic in planned:
                continue
            blind.append(topic)
        self.assertEqual(blind, [],
                         "нет ни первоисточника, ни кандидата в него: %s" % blind)

if __name__ == "__main__":
    unittest.main()
