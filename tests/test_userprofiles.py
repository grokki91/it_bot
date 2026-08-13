# -*- coding: utf-8 -*-
"""profiles.json: слияние со встроенными темами и правка через команды."""
import json
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import config, userprofiles  # noqa: E402
from newsdigest.profiles import BUILTIN, PROFILES, profile  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False


class ProfilesCase(unittest.TestCase):
    def setUp(self):
        if config.PROFILES_FILE.exists():
            config.PROFILES_FILE.unlink()
        userprofiles.apply()

    tearDown = setUp

    def put(self, data):
        userprofiles.write(data)
        userprofiles.apply()


class TestSourceIds(unittest.TestCase):
    def test_derives_readable_names(self):
        cases = {
            "https://www.theverge.com/rss/index.xml": "theverge",
            "https://blog.rust-lang.org/feed.xml": "rust-lang",
            "https://example.co.uk/rss": "example",
            "https://plain.example.com/rss": "example",
            "https://github.com/vllm-project/vllm/releases.atom": "gh-vllm",
            "не ссылка": "source",
        }
        for url, expected in cases.items():
            self.assertEqual(userprofiles.source_id_for(url), expected, url)


class TestMerge(ProfilesCase):
    def test_builtin_untouched_without_file(self):
        self.assertEqual(len(PROFILES["ai"]["feeds"]), len(BUILTIN["ai"]["feeds"]))

    def test_user_feed_extends_builtin_topic(self):
        self.put({"ai": {"feeds": [["mine", "https://e.com/rss", 1, "labs"]]}})
        ids = [f[0] for f in PROFILES["ai"]["feeds"]]
        self.assertIn("mine", ids)
        self.assertIn("openai", ids)

    def test_remove_feeds_drops_builtin(self):
        self.put({"ai": {"remove_feeds": ["openai"]}})
        self.assertNotIn("openai", [f[0] for f in PROFILES["ai"]["feeds"]])

    def test_persona_can_be_replaced(self):
        self.put({"ai": {"persona": "любитель ретрокомпьютеров"}})
        self.assertEqual(PROFILES["ai"]["persona"], "любитель ретрокомпьютеров")

    def test_new_topic_appears(self):
        self.put({"гаджеты": {"persona": "читатель", "keywords": ["phone"],
                              "feeds": [["e", "https://e.com/rss", 2, "media"]]}})
        self.assertIn("гаджеты", PROFILES)
        self.assertEqual(profile("гаджеты")["keywords"], ["phone"])

    def test_short_forms_are_accepted(self):
        # запись фида можно дать строкой, объектом или списком
        self.put({"ai": {"feeds": ["https://plainfeed.dev/rss",
                                   {"url": "https://objectfeed.dev/rss",
                                    "tier": 1, "category": "labs"}]}})
        by_id = {f[0]: f for f in PROFILES["ai"]["feeds"]}
        self.assertEqual(by_id["plainfeed"][2], 2)
        self.assertEqual(by_id["objectfeed"][2], 1)
        self.assertEqual(by_id["objectfeed"][3], "labs")

    def test_broken_file_falls_back_to_builtin(self):
        config.PROFILES_FILE.write_text("{это не json", encoding="utf-8")
        userprofiles.apply()
        self.assertEqual(len(PROFILES["ai"]["feeds"]), len(BUILTIN["ai"]["feeds"]))

    def test_broken_topic_is_skipped_others_survive(self):
        config.PROFILES_FILE.write_text(json.dumps(
            {"ai": "строка вместо объекта",
             "crypto": {"keywords": ["новое"]}}), encoding="utf-8")
        userprofiles.apply()
        self.assertIn("новое", PROFILES["crypto"]["keywords"])
        self.assertEqual(len(PROFILES["ai"]["feeds"]), len(BUILTIN["ai"]["feeds"]))


class TestEditing(ProfilesCase):
    def test_add_and_remove_roundtrip(self):
        feed = userprofiles.add_feed("ai", "https://newthing.dev/feed.xml", 1, "labs")
        self.assertEqual(feed[0], "newthing")
        self.assertIn("newthing", [f[0] for f in PROFILES["ai"]["feeds"]])
        self.assertTrue(userprofiles.is_custom("ai", "newthing"))

        self.assertTrue(userprofiles.remove_feed("ai", "newthing"))
        self.assertNotIn("newthing", [f[0] for f in PROFILES["ai"]["feeds"]])
        self.assertFalse(userprofiles.remove_feed("ai", "newthing"))

    def test_removing_builtin_persists(self):
        userprofiles.remove_feed("ai", "techcrunch")
        userprofiles.apply()                      # как после перезапуска демона
        self.assertNotIn("techcrunch", [f[0] for f in PROFILES["ai"]["feeds"]])
        self.assertIn("techcrunch", userprofiles.read()["ai"]["remove_feeds"])

    def test_duplicate_url_rejected(self):
        userprofiles.add_feed("ai", "https://newthing.dev/feed.xml")
        with self.assertRaises(ValueError):
            userprofiles.add_feed("ai", "https://newthing.dev/feed.xml")

    def test_bad_url_rejected(self):
        with self.assertRaises(ValueError):
            userprofiles.add_feed("ai", "ftp://example.com/feed")

    def test_name_collision_gets_suffix(self):
        userprofiles.add_feed("ai", "https://openai.com/other/rss.xml")
        ids = [f[0] for f in PROFILES["ai"]["feeds"]]
        self.assertIn("openai", ids)
        self.assertIn("openai-2", ids)

    def test_invalid_tier_and_category_fall_back(self):
        feed = userprofiles.add_feed("ai", "https://x.dev/rss", tier=9,
                                     category="выдумка")
        self.assertEqual((feed[2], feed[3]), (2, "media"))

    def test_keywords_add_and_remove(self):
        words = userprofiles.edit_keywords("ai", add=["MLOps", "mlops", "vector"])
        self.assertIn("mlops", words)
        self.assertEqual(words.count("mlops"), 1)
        words = userprofiles.edit_keywords("ai", remove=["mlops"])
        self.assertNotIn("mlops", words)

    def test_builtin_keyword_removal_persists(self):
        userprofiles.edit_keywords("ai", remove=["gpt"])
        userprofiles.apply()
        self.assertNotIn("gpt", PROFILES["ai"]["keywords"])

    def test_persona_editing(self):
        userprofiles.set_persona("ai", "инженер по данным")
        userprofiles.apply()
        self.assertEqual(PROFILES["ai"]["persona"], "инженер по данным")


if __name__ == "__main__":
    unittest.main()
