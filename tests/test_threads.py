# -*- coding: utf-8 -*-
"""Сюжетные цепочки: «Ранее по теме» и блок «Сюжеты недели».

Проверяется тот же случай, ради которого модуль появился: землетрясение
вечером, число жертв ночью, реакция властей утром. Дедупликация всё это
называет «разными новостями» — и правильно делает, показывать их надо все.
Здесь смотрим на второе поле того же вердикта: разные, но сюжет один.

Модель не вызывается: `dedup.judge_duplicates` подменён, сеть не трогается.
"""
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import dedup, llm, newsfeed, rank, storage, threads  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

from test_core import item  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False

CHAT = "77"

#: Один сюжет: три события, у которых общее слово ровно одно. По словам это
#: разные новости (совпадение около 0.15 при пороге 0.32) — ровно та спорная
#: зона, которую разбирает модель.
QUAKE = "Землетрясение магнитудой 7,1 обрушилось на остров Хонсю"
TOLL = "Хонсю: число погибших при землетрясении выросло до 200"
AID = "На Хонсю направили десять тысяч спасателей"
NVIDIA = "Nvidia представила ускоритель Rubin для дата-центров"


class ThreadCase(unittest.TestCase):
    """Общая обвязка: чистая база и история читателя."""

    def setUp(self):
        self.conn = storage.db()
        for table in ("items", "sent", "dupes", "threads"):
            self.conn.execute("DELETE FROM %s" % table)
        self.conn.commit()
        self.saved = {k: CFG[k] for k in CFG}

    def tearDown(self):
        CFG.update(self.saved)
        self.conn.close()

    def send(self, title, hours_ago=0, section="science", source="src"):
        """Новость в истории читателя. Возвращает её url_hash."""
        row = item("https://%s.com/%d" % (source, abs(hash(title)) % 99999),
                   title, source)
        at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO sent(chat_id,url_hash,sig,title,url,source_id,"
            "section,digest_date,sent_at,headline,summary) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (CHAT, row["url_hash"], row["sig"], row["title"], row["url"], source,
             section, "2026-08-26", at, row["title"], title))
        self.conn.commit()
        return row["url_hash"]

    def chain(self, *titles):
        """Сюжет из нескольких новостей, от самой ранней к самой поздней.

        Возвращает их хэши в том же порядке. Связь ставится от новой к
        старой — так её и находит `dedup`.
        """
        hashes, step = [], len(titles)
        for title in titles:
            step -= 1
            hashes.append(self.send(title, hours_ago=step * 5))
        for at in range(1, len(hashes)):
            threads.remember(self.conn, CHAT, hashes[at], hashes[at - 1])
        self.conn.commit()
        return hashes


class TestEarlier(ThreadCase):
    """«Ранее по теме» под карточкой."""

    def test_chain_is_built_from_the_newest_backwards(self):
        quake, toll, aid = self.chain(QUAKE, TOLL, AID)
        found = threads.earlier(self.conn, CHAT, [aid])
        self.assertEqual([step["hash"] for step in found[aid]], [toll, quake])
        self.assertEqual(found[aid][0]["title"], TOLL)

    def test_the_first_news_of_a_story_has_no_chain(self):
        quake, _toll, _aid = self.chain(QUAKE, TOLL, AID)
        self.assertNotIn(quake, threads.earlier(self.conn, CHAT, [quake]))

    def test_unrelated_news_has_no_chain(self):
        self.chain(QUAKE, TOLL)
        other = self.send(NVIDIA)
        self.assertEqual(threads.earlier(self.conn, CHAT, [other]), {})

    def test_chain_is_cut_to_depth(self):
        titles = ["Шаг %d этого сюжета" % at for at in range(6)]
        hashes = self.chain(*titles)
        found = threads.earlier(self.conn, CHAT, [hashes[-1]])
        self.assertEqual(len(found[hashes[-1]]), threads.DEPTH)

    def test_a_pruned_link_shortens_the_chain_but_does_not_break_it(self):
        """Середину сюжета вычистил срок хранения — хвост всё равно виден."""
        quake, toll, aid = self.chain(QUAKE, TOLL, AID)
        self.conn.execute("DELETE FROM sent WHERE url_hash=?", (toll,))
        self.conn.commit()
        found = threads.earlier(self.conn, CHAT, [aid])
        self.assertEqual([step["hash"] for step in found[aid]], [quake])

    def test_a_cycle_does_not_hang_the_walk(self):
        """Петля в базе — не повод зациклиться: строка могла попасть и не от нас."""
        first, second = self.chain(QUAKE, TOLL)
        threads.remember(self.conn, CHAT, first, second)
        self.conn.commit()
        found = threads.earlier(self.conn, CHAT, [second])
        self.assertEqual([step["hash"] for step in found[second]], [first])

    def test_a_news_never_follows_itself(self):
        alone = self.send(QUAKE)
        threads.remember(self.conn, CHAT, alone, alone)
        self.conn.commit()
        self.assertEqual(threads.earlier(self.conn, CHAT, [alone]), {})

    def test_another_readers_chain_is_not_shown(self):
        _quake, toll = self.chain(QUAKE, TOLL)
        self.assertEqual(threads.earlier(self.conn, "999", [toll]), {})


class TestTop(ThreadCase):
    """Блок «Сюжеты недели» в правой колонке."""

    def test_a_developing_story_gets_into_the_list(self):
        quake, _toll, _aid = self.chain(QUAKE, TOLL, AID)
        top = threads.top(self.conn, CHAT)
        self.assertEqual([story["hash"] for story in top], [quake])
        self.assertEqual(top[0]["count"], 3)
        self.assertEqual(top[0]["title"], QUAKE)

    def test_a_pair_is_not_yet_a_story(self):
        self.chain(QUAKE, TOLL)
        self.assertEqual(threads.top(self.conn, CHAT), [])

    def test_an_old_story_drops_out(self):
        hashes = self.chain(QUAKE, TOLL, AID)
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.conn.executemany("UPDATE sent SET sent_at=? WHERE url_hash=?",
                              [(old, h) for h in hashes])
        self.conn.commit()
        self.assertEqual(threads.top(self.conn, CHAT), [])

    def test_the_freshest_story_goes_first(self):
        stale = self.chain("Первый шаг далёкого сюжета",
                           "Второй шаг далёкого сюжета",
                           "Третий шаг далёкого сюжета")
        older = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.conn.executemany("UPDATE sent SET sent_at=? WHERE url_hash=?",
                              [(older, h) for h in stale])
        self.conn.commit()
        quake, _toll, _aid = self.chain(QUAKE, TOLL, AID)
        self.assertEqual(threads.top(self.conn, CHAT)[0]["hash"], quake)


class TestVerdict(ThreadCase):
    """Откуда связи берутся: второе поле того же вердикта о дублях."""

    def judge(self, same, follows):
        answer = llm.Verdict(same, follows)
        dedup.judge_duplicates = lambda pairs: (
            {i: answer for i in range(len(pairs))}, {"in": 5, "out": 5})

    def setUp(self):
        ThreadCase.setUp(self)
        self._real = dedup.judge_duplicates
        CFG["dup_llm"] = True

    def tearDown(self):
        dedup.judge_duplicates = self._real
        ThreadCase.tearDown(self)

    def candidate(self, title):
        return [item("https://news.com/%d" % abs(hash(title)), title, "news")]

    def test_a_continuation_stays_in_the_issue_and_gets_linked(self):
        quake = self.send(QUAKE)
        index = rank.SentIndex(self.conn, CHAT)
        group = self.candidate(TOLL)
        shortlists = [("science", [group])]

        self.judge(same=False, follows=True)
        dedup.prune(self.conn, index, shortlists)

        # новость другая — из выпуска её не убирают
        self.assertEqual(len(shortlists[0][1]), 1)
        # но сюжет тот же, и это запомнилось
        self.assertEqual(index.prior_of(group[0]["url_hash"]), quake)

    def test_a_different_topic_is_not_linked(self):
        self.send(QUAKE)
        index = rank.SentIndex(self.conn, CHAT)
        group = self.candidate(TOLL)

        self.judge(same=False, follows=False)
        dedup.prune(self.conn, index, [("science", [group])])
        self.assertEqual(index.prior_of(group[0]["url_hash"]), "")

    def test_a_duplicate_is_dropped_and_not_linked(self):
        self.send(QUAKE)
        index = rank.SentIndex(self.conn, CHAT)
        group = self.candidate(TOLL)
        shortlists = [("science", [group])]

        self.judge(same=True, follows=False)
        dedup.prune(self.conn, index, shortlists)
        self.assertEqual(shortlists[0][1], [])
        self.assertEqual(index.prior_of(group[0]["url_hash"]), "")

    def test_the_verdict_survives_in_the_cache(self):
        """Второй раз за тот же вопрос не платим — вместе с полем о сюжете."""
        self.send(QUAKE)
        group = self.candidate(TOLL)
        self.judge(same=False, follows=True)
        dedup.prune(self.conn, rank.SentIndex(self.conn, CHAT),
                    [("science", [group])])

        row = self.conn.execute("SELECT same, follows FROM dupes").fetchone()
        self.assertEqual((row["same"], row["follows"]), (0, 1))


class TestFeed(ThreadCase):
    """Что из этого видно в ленте на странице."""

    def rows(self):
        return list(self.conn.execute(
            "SELECT *, sent_at AS at FROM sent WHERE chat_id=? "
            "ORDER BY sent_at DESC", (CHAT,)))

    def test_the_card_carries_its_chain(self):
        CFG["translate"] = False
        _quake, toll, aid = self.chain(QUAKE, TOLL, AID)
        cards = {card["hash"]: card
                 for card in newsfeed.cards(self.conn, self.rows(), chat_id=CHAT)}
        self.assertEqual([step["title"] for step in cards[aid]["earlier"]],
                         [TOLL, QUAKE])
        self.assertEqual(cards[toll]["earlier"], [{
            "title": QUAKE,
            "url": cards[toll]["earlier"][0]["url"],
            "at": cards[toll]["earlier"][0]["at"],
        }])

    def test_a_card_without_a_story_has_an_empty_chain(self):
        CFG["translate"] = False
        self.send(NVIDIA)
        card = newsfeed.cards(self.conn, self.rows(), chat_id=CHAT)[0]
        self.assertEqual(card["earlier"], [])

    def test_the_rail_shows_the_story(self):
        CFG["translate"] = False
        self.chain(QUAKE, TOLL, AID)
        stories = newsfeed.stories(self.conn, CHAT)
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["title"], QUAKE)
        self.assertEqual(stories[0]["count"], 3)


if __name__ == "__main__":
    unittest.main()
