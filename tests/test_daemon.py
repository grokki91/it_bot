# -*- coding: utf-8 -*-
"""Планировщик: что и когда попадает в очередь фоновых задач."""
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import config, daemon, storage, subscribers  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

logging.getLogger("nd").addHandler(logging.NullHandler())
logging.getLogger("nd").propagate = False


class FakeWorker:
    """Очередь, которая ничего не выполняет — только запоминает заявки."""

    def __init__(self):
        self.jobs = []

    def submit(self, name, fn, chat_id=""):
        self.jobs.append((name, fn))
        return True

    def busy(self):
        return ""

    def names(self):
        return [name for name, _fn in self.jobs]

    def run_all(self):
        for _name, fn in self.jobs:
            fn()


class TestTick(unittest.TestCase):
    OWNER = "700"

    def setUp(self):
        self.worker = FakeWorker()
        self._owner = config.TG_CHAT
        config.TG_CHAT = self.OWNER
        self.saved = {k: CFG[k] for k in ("send_at", "per_day", "collect_every_h",
                                          "breaking", "breaking_every_min")}
        CFG["breaking"] = False
        CFG["per_day"] = 1              # один выпуск в сутки: так «23:59» = «ещё рано»

        self.collected = []
        self.digests = []
        self._real = (daemon.collect, daemon.build_and_send)
        daemon.collect = self.fake_collect
        daemon.build_and_send = self.fake_digest

        conn = storage.db()
        try:
            for table in ("subscribers", "meta", "items", "sent"):
                conn.execute("DELETE FROM %s" % table)
            conn.commit()
            subscribers.ensure_owner(conn)
        finally:
            conn.close()

    def tearDown(self):
        daemon.collect, daemon.build_and_send = self._real
        config.TG_CHAT = self._owner
        CFG.update(self.saved)

    def fake_collect(self, topics=None, wire_only=False):
        self.collected.append("wire" if wire_only else 1)
        return {}

    def fake_digest(self, sub=None, **kw):
        self.digests.append(sub["chat_id"])
        return {"sent": 1}

    def mark_collected(self, hours_ago):
        conn = storage.db()
        try:
            storage.meta_set(conn, "last_collect",
                             (datetime.now(timezone.utc)
                              - timedelta(hours=hours_ago)).isoformat())
        finally:
            conn.close()

    def mark_wire(self, minutes_ago=0):
        """Когда последний раз обходили быструю полосу (агентства)."""
        conn = storage.db()
        try:
            storage.meta_set(conn, "last_wire",
                             (datetime.now(timezone.utc)
                              - timedelta(minutes=minutes_ago)).isoformat())
        finally:
            conn.close()

    def quiet(self):
        """Ни полного сбора, ни быстрой полосы пока не нужно."""
        self.mark_collected(0)
        self.mark_wire(0)

    def test_nothing_to_do_stays_quiet(self):
        CFG["send_at"] = "23:59"          # время выпуска ещё не подошло
        self.quiet()
        daemon.tick(self.worker)
        self.assertEqual(self.worker.names(), [])

    def test_stale_collection_is_scheduled(self):
        CFG["send_at"] = "23:59"
        CFG["collect_every_h"] = 4
        self.mark_collected(5)
        self.mark_wire(0)
        daemon.tick(self.worker)
        self.assertEqual(self.worker.names(), ["collect"])

    def test_fast_lane_runs_between_collections(self):
        """Срочное не ждёт полного сбора: агентства опрашиваются отдельно."""
        CFG["send_at"] = "23:59"
        CFG["collect_every_h"] = 4
        CFG["breaking_every_min"] = 15
        self.mark_collected(1)            # полный сбор ещё не нужен
        self.mark_wire(20)                # а быстрая полоса уже устарела
        daemon.tick(self.worker)
        self.assertEqual(self.worker.names(), ["wire"])

        self.worker.run_all()
        self.assertEqual(self.collected, ["wire"])

    def test_fresh_fast_lane_is_not_repeated(self):
        CFG["send_at"] = "23:59"
        CFG["collect_every_h"] = 4
        CFG["breaking_every_min"] = 15
        self.mark_collected(1)
        self.mark_wire(5)
        daemon.tick(self.worker)
        self.assertEqual(self.worker.names(), [])

    def test_due_subscriber_gets_collect_then_digest(self):
        CFG["send_at"] = "00:01"          # время уже прошло
        self.quiet()
        daemon.tick(self.worker)
        # срочное проверяется и в час выпуска: раньше здесь стоял ранний выход
        self.assertEqual(self.worker.names(),
                         ["collect", "digest:%s" % self.OWNER, "breaking"])

        self.worker.run_all()
        self.assertEqual(self.collected, [1])
        self.assertEqual(self.digests, [self.OWNER])

    def test_every_due_subscriber_gets_his_own_job(self):
        CFG["send_at"] = "00:01"
        conn = storage.db()
        try:
            subscribers.add(conn, "800", role="member")
            subscribers.add(conn, "900", role="member")
            subscribers.set_field(conn, "900", "send_at", "23:59")   # ему рано
        finally:
            conn.close()
        daemon.tick(self.worker)
        self.assertIn("digest:800", self.worker.names())
        self.assertNotIn("digest:900", self.worker.names())

    def test_paused_subscriber_is_skipped_even_after_queueing(self):
        CFG["send_at"] = "00:01"
        daemon.tick(self.worker)
        conn = storage.db()
        try:
            subscribers.set_field(conn, self.OWNER, "paused", 1)
        finally:
            conn.close()
        self.worker.run_all()             # задача уже в очереди, но пауза важнее
        self.assertEqual(self.digests, [])

    def test_second_digest_of_the_day_is_queued_again(self):
        """Выпуск дважды в сутки: утренняя метка вечерний выпуск не закрывает."""
        CFG["send_at"], CFG["per_day"] = "00:00", 2      # слоты 00:00 и 12:00
        self.quiet()
        conn = storage.db()
        try:
            sub = subscribers.get(conn, self.OWNER)
            now = subscribers.now_for(sub)
            here = subscribers.slot_index(sub, now)
            subscribers.set_last_digest(conn, self.OWNER,
                                        subscribers.slot_mark(sub, now))
        finally:
            conn.close()
        daemon.tick(self.worker)
        self.assertEqual(self.worker.names(), [])        # этот выпуск уже ушёл

        conn = storage.db()
        try:                              # метка соседнего слота не считается
            subscribers.set_last_digest(
                conn, self.OWNER,
                "%s#%d" % (now.strftime("%Y-%m-%d"), 3 - here))
        finally:
            conn.close()
        again = FakeWorker()
        daemon.tick(again)
        self.assertIn("digest:%s" % self.OWNER, again.names())

    def test_empty_digest_backs_off_for_an_hour(self):
        CFG["send_at"] = "00:01"
        daemon.build_and_send = lambda sub=None, **kw: {"sent": 0}
        daemon.tick(self.worker)
        self.worker.run_all()

        again = FakeWorker()
        daemon.tick(again)
        self.assertNotIn("digest:%s" % self.OWNER, again.names())

        conn = storage.db()
        try:                              # час прошёл — пробуем снова
            storage.meta_set(conn, "digest_attempt:%s" % self.OWNER,
                             (datetime.now(timezone.utc)
                              - timedelta(hours=2)).isoformat())
        finally:
            conn.close()
        third = FakeWorker()
        daemon.tick(third)
        self.assertIn("digest:%s" % self.OWNER, third.names())


if __name__ == "__main__":
    unittest.main()
