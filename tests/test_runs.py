# -*- coding: utf-8 -*-
"""Журнал прогонов: по нему считают расход и отвечают «стало лучше или хуже».

Смысл теста один: у видов прогонов разная частота. Срочное проверяется раз в
15 минут, выпуск выходит дважды в сутки — и при общем потолке в 200 строк
проверки срочного вытирали историю выпусков за полтора дня.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ND_HOME", tempfile.mkdtemp(prefix="ndtest-"))

from newsdigest import storage  # noqa: E402


class RunsCase(unittest.TestCase):
    """История прогонов: проверки срочного не должны вытирать выпуски."""

    def setUp(self):
        self.conn = storage.db()
        self.conn.execute("DELETE FROM runs")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_breaking_does_not_crowd_out_digests(self):
        """Срочное проверяется раз в 15 минут, выпуск выходит дважды в сутки.

        При общем потолке в 200 строк проверки срочного съедали всю историю за
        полтора дня, и `report` отвечал «стало лучше или хуже» по вчерашнему.
        """
        for _ in range(3):
            storage.log_run(self.conn, "digest", "ok", {"cost": 0.01})
        for _ in range(storage.KEEP_RUNS + 50):
            storage.log_run(self.conn, "breaking", "below-threshold", {"best": 5})

        left = dict(self.conn.execute(
            "SELECT kind, COUNT(*) n FROM runs GROUP BY kind").fetchall())
        self.assertEqual(left["digest"], 3)
        self.assertEqual(left["breaking"], storage.KEEP_RUNS)

    def test_each_kind_keeps_its_own_tail(self):
        for _ in range(storage.KEEP_RUNS + 10):
            storage.log_run(self.conn, "digest", "ok", {})
        row = self.conn.execute("SELECT COUNT(*) n FROM runs "
                                "WHERE kind='digest'").fetchone()
        self.assertEqual(row["n"], storage.KEEP_RUNS)

if __name__ == "__main__":
    unittest.main()
