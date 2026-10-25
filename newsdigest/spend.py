# -*- coding: utf-8 -*-
"""Журнал расхода на модель: сколько и на что ушло за каждые сутки.

Раньше расход считался из строк `runs`, а их хранится по 200 каждого вида.
Сбор и проверка срочного идут раз в 15 минут — двести строк это двое суток,
и «расход за неделю» в `status` складывал неделю выпусков с двумя днями
всего остального. Перевод ленты на странице стоимость не записывал вовсе.

Пишется из единственной двери к модели (`llm.llm_json`), поэтому не пропускает
ни одного запроса, и пишется сутками по назначениям, а не по строке на запрос:
за год это пара тысяч строк.

Запись не имеет права тормозить запрос: к моменту ответа модели другое
соединение (выпуск, сбор) могло оставить базу занятой, а ждать её по тридцать
секунд на каждый вызов дороже любого учёта. Поэтому ждём доли секунды, а что не
записалось — копится в памяти и уходит со следующим запросом.

Модуль не знает про `storage`: тот через `translate` импортирует `llm`, а
`llm` пишет сюда. Сама таблица `spend` создаётся в `storage.SCHEMA`.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .config import DB_FILE, log

#: назначения запросов в порядке, в котором их показывает `status`
LABELS = (
    ("rank", "ранжирование"),
    ("cards", "карточки"),
    ("dupes", "дубли"),
    ("claims", "фактчек"),
    ("urgency", "срочное"),
    ("classify", "разделы"),
    ("translate", "перевод"),
    ("other", "прочее"),
)

#: на какое окно ждём освободившуюся базу, секунд
WAIT = 0.25

#: колонки накопителя по порядку: деньги DeepSeek, потом бесплатный маршрут
FIELDS = ("calls", "tok_in", "tok_cached", "tok_out", "cost", "tok_reason",
          "free_calls", "free_in", "free_out", "fallbacks")

UPSERT = ("INSERT INTO spend(day, kind, %s) VALUES (?,?,%s) "
          "ON CONFLICT(day, kind) DO UPDATE SET %s"
          % (", ".join(FIELDS), ",".join("?" * len(FIELDS)),
             ", ".join("%s=%s+excluded.%s" % (f, f, f) for f in FIELDS)))

_lock = threading.Lock()
#: (сутки, назначение) -> значения по FIELDS
_pending = {}


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _blank() -> list:
    return [0, 0, 0, 0, 0.0, 0, 0, 0, 0, 0]


def add(kind, usage, cost, route="paid") -> None:
    """Записывает один состоявшийся запрос. Учёт запрос никогда не роняет.

    Бесплатный (`route="free"`) идёт в свои колонки: `calls` и `cost` — это
    только то, за что платим DeepSeek.
    """
    try:
        with _lock:
            acc = _pending.setdefault((today(), kind or "other"), _blank())
            if route == "free":
                acc[6] += 1
                acc[7] += int(usage.get("in", 0) or 0)
                acc[8] += int(usage.get("out", 0) or 0)
            else:
                acc[0] += 1
                acc[1] += int(usage.get("in", 0) or 0)
                acc[2] += int(usage.get("cached", 0) or 0)
                acc[3] += int(usage.get("out", 0) or 0)
                acc[4] += float(cost or 0.0)
                acc[5] += int(usage.get("reason", 0) or 0)
        flush()
    except Exception as exc:  # noqa: BLE001 — учёт не повод ронять выпуск
        log.debug("Расход не записан: %s", exc)


def fallback(kind) -> None:
    """Запрос ушёл в бесплатную модель, а ответил в итоге DeepSeek."""
    try:
        with _lock:
            _pending.setdefault((today(), kind or "other"), _blank())[9] += 1
        flush()
    except Exception as exc:  # noqa: BLE001
        log.debug("Откат не записан: %s", exc)


def flush() -> None:
    """Сбрасывает накопленное в базу. База занята — остаётся до следующего раза."""
    with _lock:
        batch = dict(_pending)
        _pending.clear()
    if not batch:
        return
    try:
        if not DB_FILE.exists():
            raise sqlite3.OperationalError("базы ещё нет")
        conn = sqlite3.connect(str(DB_FILE), timeout=WAIT)
        try:
            conn.executemany(UPSERT, [(day, kind) + tuple(acc)
                                      for (day, kind), acc in batch.items()])
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        with _lock:
            for key, acc in batch.items():
                mine = _pending.setdefault(key, _blank())
                for at, value in enumerate(acc):
                    mine[at] += value
        log.debug("Расход подождёт следующего запроса: %s", exc)


def since(days: int) -> str:
    """Первые сутки окна: «за неделю» это сегодня и шесть дней до него."""
    return (datetime.now(timezone.utc)
            - timedelta(days=max(1, int(days)) - 1)).strftime("%Y-%m-%d")


def totals(conn, days: int = 7) -> list:
    """Расход за окно по назначениям, дорогое — первым."""
    flush()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT kind, SUM(calls) AS calls, SUM(tok_in) AS tok_in, "
            "SUM(tok_cached) AS tok_cached, SUM(tok_out) AS tok_out, "
            "SUM(cost) AS cost, SUM(tok_reason) AS tok_reason, "
            "SUM(free_calls) AS free_calls, SUM(free_in) AS free_in, "
            "SUM(free_out) AS free_out, SUM(fallbacks) AS fallbacks "
            "FROM spend WHERE day >= ? "
            "GROUP BY kind ORDER BY cost DESC", (since(days),))]
    except sqlite3.OperationalError:       # база старой версии без таблицы или колонок
        return []


def label(kind: str) -> str:
    return dict(LABELS).get(kind, kind or "прочее")
