# -*- coding: utf-8 -*-
"""Бот живёт по Москве: расписание, «сегодня» и время в выпуске — по МСК.

Сервер может стоять в UTC (обычный VPS) — результат от этого не зависит.
Часы подменяются, ждать полуночи не нужно.
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import config, spend, subscribers  # noqa: E402
from newsdigest.config import CFG  # noqa: E402

HAS_MOSCOW = (Path("/usr/share/zoneinfo") / "Europe/Moscow").exists()


def clock(utc):
    """datetime, у которого «сейчас» — заданный момент UTC."""
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return utc.astimezone(tz)
            return utc.astimezone().replace(tzinfo=None)  # пояс процесса
    return Fixed


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class TestMoscowDefault(unittest.TestCase):
    @unittest.skipIf(os.environ.get("ND_TZ"), "пояс задан снаружи через ND_TZ")
    def test_default_is_moscow(self):
        self.assertEqual(CFG["tz"], "Europe/Moscow")

    def test_fallback_offset_is_moscow(self):
        self.assertEqual(CFG["tz_offset"], 3)


class MoscowClock(unittest.TestCase):
    """Сервер в UTC, бот — в Europe/Moscow (или в запасном UTC+3)."""
    zone = "Europe/Moscow"

    def setUp(self):
        self.saved = (CFG["tz"], CFG["tz_offset"], CFG["send_at"], CFG["per_day"],
                      os.environ.get("TZ"), config.datetime)
        os.environ["TZ"] = "UTC"
        if hasattr(time, "tzset"):
            time.tzset()
        CFG.update(tz=self.zone, tz_offset=3, send_at="09:00", per_day=2)
        config.init_tz()

    def tearDown(self):
        (CFG["tz"], CFG["tz_offset"], CFG["send_at"], CFG["per_day"],
         old_tz, config.datetime) = self.saved
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        if hasattr(time, "tzset"):
            time.tzset()
        config.init_tz()

    def at(self, moment):
        config.datetime = clock(moment)

    def test_label(self):
        self.assertIn(config.tz_label(), ("Europe/Moscow", "UTC+3"))

    def test_local_now_is_utc_plus_3_all_year(self):
        # В Москве нет перехода на летнее время: +3 и зимой, и летом
        for moment in (utc(2026, 1, 15, 6, 0), utc(2026, 7, 15, 6, 0)):
            self.at(moment)
            self.assertEqual(config.local_now(), datetime(*moment.timetuple()[:3], 9, 0))
            self.assertEqual(config.to_local(moment).strftime("%H:%M"), "09:00")

    def test_to_local_naive_is_utc(self):
        # Из базы время приходит без пояса — это UTC
        self.assertEqual(config.to_local(datetime(2026, 3, 1, 21, 30)),
                         datetime(2026, 3, 2, 0, 30))

    def test_schedule_by_moscow(self):
        # 09:00 и 21:00 по Москве — это 06:00 и 18:00 UTC
        for moment, slot in ((utc(2026, 10, 25, 5, 59), 0),
                             (utc(2026, 10, 25, 6, 0), 1),
                             (utc(2026, 10, 25, 17, 59), 1),
                             (utc(2026, 10, 25, 18, 0), 2)):
            self.at(moment)
            self.assertEqual(subscribers.slot_index(None), slot, moment)

    def test_slot_mark_uses_moscow_date(self):
        # 22:30 UTC — уже следующие сутки в Москве
        self.at(utc(2026, 10, 24, 22, 30))
        self.assertTrue(subscribers.slot_mark(None).startswith("2026-10-25#"))

    def test_spend_day_starts_at_moscow_midnight(self):
        self.at(utc(2026, 10, 24, 20, 59))
        self.assertEqual(spend.today(), "2026-10-24")
        self.at(utc(2026, 10, 24, 21, 0))
        self.assertEqual(spend.today(), "2026-10-25")
        self.assertEqual(spend.since(7), "2026-10-19")


@unittest.skipUnless(HAS_MOSCOW, "в системе нет tzdata для Europe/Moscow")
class TestMoscowZone(MoscowClock):
    def test_zone_applied(self):
        self.assertEqual(config.tz_label(), "Europe/Moscow")


class TestMoscowOffset(MoscowClock):
    """Без имени пояса — запасное смещение, то же время."""
    zone = ""

    def test_offset_applied(self):
        self.assertEqual(config.tz_label(), "UTC+3")


del MoscowClock

if __name__ == "__main__":
    unittest.main()
