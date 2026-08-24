# -*- coding: utf-8 -*-
"""Числовые сигналы срочности: магнитуда, уровень тревоги, каталог CISA."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import signals, storage  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

from test_core import item  # noqa: E402


class TestMagnitude(unittest.TestCase):
    def test_usgs_headline(self):
        self.assertEqual(signals.magnitude("M 7.4 - 100km SE of Kamaishi"), 7.4)
        self.assertEqual(signals.magnitude("M 5 - offshore"), 5.0)

    def test_no_number(self):
        self.assertEqual(signals.magnitude("Earthquake shakes the coast"), 0.0)

    def test_not_a_magnitude(self):
        """«M» рядом с чем попало магнитудой не становится."""
        self.assertEqual(signals.magnitude("Model M 3 released by IBM"), 3.0)
        self.assertEqual(signals.magnitude("Mercedes AMG"), 0.0)


class TestDisaster(unittest.TestCase):
    def test_gdacs_levels(self):
        self.assertEqual(signals.disaster_level("Red alert for tropical cyclone"),
                         "red")
        self.assertEqual(signals.disaster_level("Orange Alert for flood"), "orange")
        self.assertEqual(signals.disaster_level("Green alert for earthquake"), "")


class TestCve(unittest.TestCase):
    def test_extracts_and_normalises(self):
        self.assertEqual(signals.cve_ids("Fixes cve-2026-1234 and CVE-2025-99999"),
                         {"CVE-2026-1234", "CVE-2025-99999"})

    def test_nothing_to_find(self):
        self.assertEqual(signals.cve_ids("A patch was released"), set())


class TestFloor(unittest.TestCase):
    def quake(self, value):
        return signals.floor_for(
            {"title": "M %s - 100km SE of somewhere" % value, "summary": ""})

    def test_big_quake_is_global(self):
        urgency, scope, why = self.quake("7.4")
        self.assertGreaterEqual(urgency, CFG["breaking_flash_score"])
        self.assertEqual(scope, "global")
        self.assertIn("7.4", why)

    def test_medium_quake_is_national(self):
        urgency, scope, _why = self.quake("6.2")
        self.assertGreaterEqual(urgency, CFG["breaking_alert_score"])
        self.assertLess(urgency, CFG["breaking_flash_score"])
        self.assertEqual(scope, "national")

    def test_small_quake_waits_for_the_issue(self):
        urgency, _scope, _why = self.quake("4.8")
        self.assertEqual(urgency, 0.0)

    def test_exploited_vulnerability(self):
        urgency, scope, why = signals.floor_for(
            {"title": "Attackers exploit CVE-2026-1234 in the wild",
             "summary": ""}, kev={"CVE-2026-1234"})
        self.assertGreaterEqual(urgency, CFG["breaking_alert_score"])
        self.assertEqual(scope, "industry")
        self.assertIn("CVE-2026-1234", why)

    def test_vulnerability_outside_the_catalogue_is_ordinary(self):
        """«Нашли дыру» и «дыру эксплуатируют» — разные новости."""
        urgency, _scope, _why = signals.floor_for(
            {"title": "Researchers disclose CVE-2026-5555", "summary": ""},
            kev={"CVE-2026-1234"})
        self.assertEqual(urgency, 0.0)


class TestRaiseFloors(unittest.TestCase):
    def group(self, title):
        return [item("https://usgs.gov/x", title, source="usgs-quakes", tier=1)]

    def test_number_beats_a_low_model_score(self):
        """Ровно та поломка, ради которой всё это: модель занизила M7.4."""
        quake = self.group("M 7.4 - 100km SE of Kamaishi")
        rated = {id(quake): {"urgency": 3.0, "scope": "niche",
                             "category": "policy"}}
        signals.raise_floors(rated, [quake])
        self.assertGreaterEqual(rated[id(quake)]["urgency"],
                                CFG["breaking_flash_score"])
        self.assertEqual(rated[id(quake)]["scope"], "global")

    def test_model_opinion_is_not_lowered(self):
        """Поднимаем, но не опускаем: у модели есть контекст, которого нет у числа."""
        quake = self.group("M 6.1 - offshore")
        rated = {id(quake): {"urgency": 9.8, "scope": "global",
                             "category": "policy"}}
        signals.raise_floors(rated, [quake])
        self.assertEqual(rated[id(quake)]["urgency"], 9.8)

    def test_ordinary_news_untouched(self):
        plain = self.group("Совещание перенесли на четверг")
        rated = {}
        signals.raise_floors(rated, [plain])
        self.assertEqual(rated, {})


class TestKevCache(unittest.TestCase):
    def setUp(self):
        self.conn = storage.db()
        for key in ("kev_ids", "kev_at", "kev_try"):
            self.conn.execute("DELETE FROM meta WHERE k=?", (key,))
        self.conn.commit()
        self.saved = CFG["use_kev"]

    def tearDown(self):
        CFG["use_kev"] = self.saved
        self.conn.close()

    def test_switched_off_means_no_request(self):
        CFG["use_kev"] = False
        self.assertEqual(signals.kev_ids(self.conn), set())

    def test_cached_catalogue_is_reused(self):
        CFG["use_kev"] = True
        storage.meta_set(self.conn, "kev_ids", "CVE-2026-1234 CVE-2025-1")
        storage.meta_set(self.conn, "kev_at", storage.now_iso())
        self.assertEqual(signals.kev_ids(self.conn),
                         {"CVE-2026-1234", "CVE-2025-1"})


if __name__ == "__main__":
    unittest.main()
