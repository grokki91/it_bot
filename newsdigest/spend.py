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

Тем же путём пишутся `shadow` — счётчики сверки DeepSeek с бесплатной моделью
(она идёт в фоновой нити и так же не должна ждать занятую базу) — и `free_use`:
какая бесплатная модель сколько ответила и как часто совпала с DeepSeek.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import timedelta

from .config import DB_FILE, local_now, log

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

#: колонки сверки с бесплатной моделью (`storage.SCHEMA`, таблица shadow)
SHADOW_FIELDS = ("asks", "failed", "items", "agree", "risky")

#: колонки учёта по бесплатным моделям (таблица free_use)
MODEL_FIELDS = ("answers", "rejected", "errors", "items", "agree")


def _upsert(table, fields, key="kind") -> str:
    return ("INSERT INTO %s(day, %s, %s) VALUES (?,?,%s) "
            "ON CONFLICT(day, %s) DO UPDATE SET %s"
            % (table, key, ", ".join(fields), ",".join("?" * len(fields)), key,
               ", ".join("%s=%s+excluded.%s" % (f, f, f) for f in fields)))


UPSERT = _upsert("spend", FIELDS)
SHADOW_UPSERT = _upsert("shadow", SHADOW_FIELDS)
MODEL_UPSERT = _upsert("free_use", MODEL_FIELDS, key="model")

_lock = threading.Lock()
#: (сутки, назначение) -> значения по FIELDS
_pending = {}
#: (сутки, назначение) -> значения по SHADOW_FIELDS
_shadow = {}
#: (сутки, модель) -> значения по MODEL_FIELDS
_models = {}


def today() -> str:
    """Сутки расхода — по вашему поясу: «сегодня» с полуночи, а не с 03:00."""
    return local_now().strftime("%Y-%m-%d")


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


def _count(book, key, values) -> None:
    """Прибавляет счётчики к накопителю и сбрасывает в базу. Ничего не роняет."""
    try:
        with _lock:
            acc = book.setdefault((today(), key), [0] * len(values))
            for at, value in enumerate(values):
                acc[at] += int(value)
        flush()
    except Exception as exc:  # noqa: BLE001
        log.debug("Счётчики не записаны: %s", exc)


def shadow(kind, asks=0, failed=0, items=0, agree=0, risky=0) -> None:
    """Счётчики одной сверки с бесплатной моделью."""
    _count(_shadow, kind or "other", (asks, failed, items, agree, risky))


def model(name, answers=0, rejected=0, errors=0, items=0, agree=0) -> None:
    """Счётчики одной бесплатной модели: её ответы и совпадения с DeepSeek."""
    _count(_models, name or "?", (answers, rejected, errors, items, agree))


def flush() -> None:
    """Сбрасывает накопленное в базу. База занята — остаётся до следующего раза."""
    with _lock:
        books = [(UPSERT, _pending, dict(_pending)),
                 (SHADOW_UPSERT, _shadow, dict(_shadow)),
                 (MODEL_UPSERT, _models, dict(_models))]
        for _sql, book, _batch in books:
            book.clear()
    if not any(batch for _sql, _book, batch in books):
        return
    try:
        if not DB_FILE.exists():
            raise sqlite3.OperationalError("базы ещё нет")
        conn = sqlite3.connect(str(DB_FILE), timeout=WAIT)
        try:
            for sql, _book, batch in books:
                conn.executemany(sql, [(day, kind) + tuple(acc)
                                       for (day, kind), acc in batch.items()])
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        with _lock:
            for _sql, book, batch in books:
                for key, acc in batch.items():
                    mine = book.setdefault(key, [0] * len(acc))
                    for at, value in enumerate(acc):
                        mine[at] += value
        log.debug("Расход подождёт следующего запроса: %s", exc)


def shadow_asks(day) -> int:
    """Сколько запросов сверки ушло за сутки — с запуска процесса и до него.
    База занята — считаем только своё: лимит сверки не стоит задержки."""
    flush()
    with _lock:
        pending = sum(acc[0] for (day_, _kind), acc in _shadow.items() if day_ == day)
    try:
        if not DB_FILE.exists():
            raise sqlite3.OperationalError("базы ещё нет")
        conn = sqlite3.connect(str(DB_FILE), timeout=WAIT)
        try:
            row = conn.execute("SELECT SUM(asks) FROM shadow WHERE day=?",
                               (day,)).fetchone()
        finally:
            conn.close()
        return pending + int(row[0] or 0)
    except sqlite3.Error:
        return pending


def since(days: int) -> str:
    """Первые сутки окна: «за неделю» это сегодня и шесть дней до него."""
    return (local_now()
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


def shadow_totals(conn, days: int = 7) -> list:
    """Сверка с бесплатной моделью за окно по видам."""
    flush()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT kind, SUM(asks) AS asks, SUM(failed) AS failed, "
            "SUM(items) AS items, SUM(agree) AS agree, SUM(risky) AS risky "
            "FROM shadow WHERE day >= ? GROUP BY kind", (since(days),))]
    except sqlite3.OperationalError:       # база старой версии без таблицы
        return []


def model_totals(conn, days: int = 7) -> list:
    """Бесплатные модели за окно: самая отвечающая — первой."""
    flush()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT model, SUM(answers) AS answers, SUM(rejected) AS rejected, "
            "SUM(errors) AS errors, SUM(items) AS items, SUM(agree) AS agree "
            "FROM free_use WHERE day >= ? GROUP BY model "
            "ORDER BY answers DESC, model", (since(days),))]
    except sqlite3.OperationalError:       # база старой версии без таблицы
        return []


def label(kind: str) -> str:
    return dict(LABELS).get(kind, kind or "прочее")
