# -*- coding: utf-8 -*-
"""Основа скриптов реальной интеграции: `tools/itest_<тема>.py`.

Такой скрипт пишется под PR, когда тестов мало: правка трогает живые данные
(миграция, пересчёт базы), контракт Telegram или DeepSeek, раздачу выпусков
по подписчикам. `tools/prcheck.py` запускает его сам, если он есть в правке.

    import itest

    def main(rep):
        itest.prod_copy()                  # ND_HOME → копия ~/.newsdigest
        from newsdigest import storage
        sent = itest.capture_telegram()    # Bot API перехвачен: ничего не уходит
        conn = storage.db()
        before = itest.rows(conn, "SELECT id, url, title FROM sent")
        ...                                # то, что меняет PR
        rep.compare("sent", before, itest.rows(conn, "SELECT id, url, title FROM sent"))
        rep.metric("messages", expected=len(chats), actual=len(sent))
        rep.check(not itest.integrity(conn), "целостность базы")

    if __name__ == "__main__":
        itest.run(main)

Печатает отчёт и выходит с кодом 0 / 1 / 3:

    integration: PASS | FAIL | BLOCKED
    sent: expected=9849, actual=9849
    differences: 0

PASS — только если что-то сверено и расхождений нет: совпадения счётчиков
мало, сверяются сами строки и инварианты. Исключение — FAIL с причиной; нет
живых данных (песочница, CI) — BLOCKED. Живая база не меняется: работа идёт на
копии, снятой через backup API, — демона можно не останавливать.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: сколько расхождений показать примерами — полный список не нужен
EXAMPLES = 5
#: код возврата «нечем проверить» — его ждёт `tools/prcheck.py`
BLOCKED = 3


class Blocked(Exception):
    """Нечем проверить: нет живых данных или доступа."""


class Report:
    """Что сверено и где не сошлось."""

    def __init__(self):
        self.lines, self.examples = [], []
        self.checked = self.differences = 0

    def _count(self, bad):
        self.checked += 1
        self.differences += len(bad)
        self.examples += bad[:max(0, EXAMPLES - len(self.examples))]

    def metric(self, name, expected, actual):
        """Число против числа: отправлено, строк, подписчиков."""
        self.lines.append("%s: expected=%s, actual=%s" % (name, expected, actual))
        self._count([] if expected == actual
                    else ["%s: ждали %s, вышло %s" % (name, expected, actual)])

    def compare(self, name, expected: dict, actual: dict):
        """Построчно, ключ → значение: одинаковое число строк — ещё не те строки."""
        bad = []
        for key in sorted(expected.keys() | actual.keys(), key=repr):
            if key not in actual:
                bad.append("%s: нет %r" % (name, key))
            elif key not in expected:
                bad.append("%s: лишняя %r" % (name, key))
            elif expected[key] != actual[key]:
                bad.append("%s: иначе %r" % (name, key))
        self.lines.append("%s: expected=%d, actual=%d" % (name, len(expected),
                                                         len(actual)))
        self._count(bad)

    def check(self, ok, what):
        """Инвариант, который не сводится к счёту: целостность, нет побочных эффектов."""
        self._count([] if ok else ["нарушено: " + what])


def run(main):
    """Выполняет main(rep), печатает отчёт и выходит с кодом по статусу."""
    rep, status, reason = Report(), "PASS", ""
    try:
        main(rep)
    except Blocked as exc:
        status, reason = "BLOCKED", str(exc)
    except Exception as exc:  # noqa: BLE001 — любое падение — FAIL, а не PASS
        status, reason = "FAIL", "%s: %s" % (type(exc).__name__, exc)
    else:
        if not rep.checked:
            status, reason = "FAIL", "ничего не сверено"
        elif rep.differences:
            status = "FAIL"
    print("integration: " + status)
    print("\n".join(rep.lines + (["differences: %d" % rep.differences]
                                 if rep.checked else [])
                    + ["  - " + ex for ex in rep.examples]
                    + (["reason: " + reason[:200]] if reason else [])))
    sys.stdout.flush()
    sys.exit({"PASS": 0, "BLOCKED": BLOCKED}.get(status, 1))


def prod_copy(source=None) -> Path:
    """ND_HOME → временная копия живого каталога. Вызывать до импорта newsdigest:
    пути к базе он берёт при импорте."""
    if "newsdigest.config" in sys.modules:
        raise RuntimeError("prod_copy() — до импорта newsdigest")
    src = Path(source or os.environ.get("ND_HOME") or Path.home() / ".newsdigest")
    db = src / "digest.db"
    if not db.is_file():
        raise Blocked("нет живой базы %s" % db)
    dst = Path(tempfile.mkdtemp(prefix="nd-itest-"))
    atexit.register(shutil.rmtree, str(dst), True)
    live = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(str(dst / "digest.db"))
    try:
        live.backup(copy)
    finally:
        live.close()
        copy.close()
    for name in ("profiles.json", "env"):
        if (src / name).is_file():
            shutil.copy2(str(src / name), str(dst / name))
    os.environ["ND_HOME"] = str(dst)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return dst


def capture_telegram() -> list:
    """Bot API → список (метод, payload). Звать после `config.load_env()`:
    тот перечитывает токен."""
    from newsdigest import config, telegram
    calls = []

    def fake(url, payload, headers=None, timeout=60):
        calls.append((url.rsplit("/", 1)[-1], payload))
        return 200, {"ok": True, "result": {
            "message_id": len(calls), "chat": {"id": payload.get("chat_id")}}}, ""

    telegram.post_json = fake
    config.TG_TOKEN = config.TG_TOKEN or "itest"
    return calls


def rows(conn, sql, params=()) -> dict:
    """Выборка → {первый столбец: остальные} для Report.compare.
    Ключ обязан быть уникальным: иначе дубли схлопнутся и спрячут расхождение."""
    out = {}
    for row in conn.execute(sql, params):
        if row[0] in out:
            raise ValueError("ключ не уникален: %r" % (row[0],))
        out[row[0]] = tuple(row)[1:]
    return out


def integrity(conn) -> list:
    """Что не так с базой: integrity_check, внешние ключи, индексы FTS."""
    bad = [r[0] for r in conn.execute("PRAGMA integrity_check") if r[0] != "ok"]
    bad += ["внешний ключ: %s rowid=%s → %s" % tuple(r[:3])
            for r in conn.execute("PRAGMA foreign_key_check")]
    for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND sql LIKE 'CREATE VIRTUAL TABLE%fts5%'").fetchall():
        try:
            conn.execute('INSERT INTO "%s"("%s") VALUES(\'integrity-check\')'
                         % (name, name))
        except sqlite3.Error as exc:
            bad.append("%s: %s" % (name, exc))
    return bad
