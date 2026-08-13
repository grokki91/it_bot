# -*- coding: utf-8 -*-
"""Фоновый режим: расписание в одной нити, приём команд — в главной.

Раньше демон только спал и просыпался по часам. Теперь он ещё и слушает
Telegram, поэтому нитей три:
    главная   — long-poll getUpdates, отвечает на команды;
    scheduler — раз в минуту смотрит, не пора ли собрать или отправить;
    worker    — выполняет тяжёлое (сбор, запросы к модели) по одной задаче.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone

from . import breaking, config
from .bot import Worker, drain_backlog, is_paused, poll_forever
from .config import CFG, HOME, LOG_FILE, local_now, log, tz_label
from .feedparse import parse_date
from .pipeline import build_and_send
from .sources import collect
from .storage import db, meta_get

TICK_SECONDS = 60


def send_at_minutes() -> int:
    hour, minute = [int(x) for x in CFG["send_at"].split(":")]
    return hour * 60 + minute


def tick(worker) -> None:
    """Один заход планировщика: решает, что запустить, и уходит."""
    conn = db()
    try:
        last_collect = parse_date(meta_get(conn, "last_collect", ""))
        last_digest = meta_get(conn, "last_digest_date", "")
        paused = is_paused(conn)
    finally:
        conn.close()

    need_collect = (last_collect is None or
                    datetime.now(timezone.utc) - last_collect
                    >= timedelta(hours=CFG["collect_every_h"]))
    now = local_now()
    today = now.strftime("%Y-%m-%d")
    due = now.hour * 60 + now.minute >= send_at_minutes()
    need_digest = due and last_digest != today and not paused

    if need_digest:
        def job():
            log.info("Время дайджеста — собираю свежее и отправляю")
            collect()
            build_and_send()
        worker.submit("digest", job)
    elif need_collect:
        # срочное ищем сразу после сбора: свежие материалы уже в базе,
        # а до утреннего выпуска может оставаться половина суток
        def job():
            collect()
            breaking.check()
        worker.submit("collect", job)


def scheduler_loop(worker, stop) -> None:
    while not stop.is_set():
        try:
            tick(worker)
        except Exception as exc:  # noqa: BLE001 — планировщик не должен умирать
            log.exception("Ошибка планировщика: %s", exc)
        stop.wait(TICK_SECONDS)


def daemon():
    log.info("Демон запущен. Тема: %s. Отправка в %s (%s). Сбор раз в %d ч.",
             CFG["topic"], CFG["send_at"], tz_label(), CFG["collect_every_h"])
    log.info("Каталог данных: %s | лог: %s", HOME, LOG_FILE)
    from .cli import require_secrets
    require_secrets()
    try:
        send_at_minutes()
    except ValueError:
        sys.exit("Некорректное время отправки: %r. Формат ЧЧ:ММ" % CFG["send_at"])

    worker = Worker().start()
    stop = threading.Event()
    threading.Thread(target=scheduler_loop, args=(worker, stop),
                     name="nd-scheduler", daemon=True).start()

    if not CFG["listen"]:
        log.info("Приём команд выключен (ND_LISTEN=0) — работаю только по расписанию")
        try:
            while True:
                stop.wait(3600)
        except KeyboardInterrupt:
            stop.set()
        return

    conn = db()
    try:
        drain_backlog(conn)
    finally:
        conn.close()
    log.info("Слушаю команды в Telegram. Владелец: chat_id %s. Справка в чате: /help",
             config.TG_CHAT)
    try:
        poll_forever(worker, stop)
    except KeyboardInterrupt:
        stop.set()
        log.info("Остановлен по Ctrl+C")
