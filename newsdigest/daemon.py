# -*- coding: utf-8 -*-
"""Фоновый режим: расписание в одной нити, приём нажатий — в главной.

Демон не только спит и просыпается по часам — он ещё и слушает Telegram,
поэтому нитей три:
    главная   — long-poll getUpdates: кнопки под выпуском и заявки новых чатов;
    scheduler — раз в минуту смотрит, не пора ли собрать или отправить;
    worker    — выполняет тяжёлое (сбор, запросы к модели) по одной задаче.

Команд в Telegram нет: бот там только рассылает выпуски. Управление живёт на
странице в браузере (web.py) и в терминале.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone

from . import breaking, config, sections, subscribers
from .bot import Worker, drain_backlog, poll_forever
from .config import CFG, HOME, LOG_FILE, log, tz_label
from .feedparse import parse_date
from .pipeline import build_and_send
from .sources import collect
from .storage import db, meta_get

TICK_SECONDS = 60


def send_at_minutes() -> int:
    hour, minute = [int(x) for x in CFG["send_at"].split(":")]
    return hour * 60 + minute


def digest_job(sub):
    """Замыкание на конкретного подписчика — у каждого своя тема и время."""
    chat_id = sub["chat_id"]

    def job():
        conn = db()
        try:
            fresh = subscribers.get(conn, chat_id)
        finally:
            conn.close()
        if fresh is None or fresh["paused"]:    # успел отписаться, пока ждал
            return
        log.info("Время выпуска для %s", chat_id)
        stats = build_and_send(sub=fresh)
        if not stats.get("sent"):
            # день не закрываем: новости могут появиться позже. Но и повторять
            # каждую минуту нельзя — ранжирование стоит денег
            conn = db()
            try:
                subscribers.note_empty(conn, chat_id)
            finally:
                conn.close()
            log.info("Для %s выпуска не набралось — вернусь через час", chat_id)
    return job


def urgent_job(subs, wire=False):
    """Проверка срочного: при желании — со своим коротким обходом агентств.

    Подписчиков проверяем разом: у кого разделы совпадают, тем хватит одной
    оценки модели на всех. Сводка накопленного важного идёт следом, тем же
    заходом: отдельный таймер ей не нужен, она сама решает, пора ли.
    """
    def job():
        if wire:
            collect(wire_only=True)
        breaking.check_all(subs)
        breaking.flush_all(subs)
    return job


def tick(worker) -> None:
    """Один заход планировщика: решает, что запустить, и уходит."""
    conn = db()
    try:
        subscribers.ensure_owner(conn)
        last_collect = parse_date(meta_get(conn, "last_collect", ""))
        last_wire = parse_date(meta_get(conn, "last_wire", ""))
        ready = subscribers.due(conn)
        waiting = subscribers.active(conn)
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    need_collect = (last_collect is None or
                    now - last_collect >= timedelta(hours=CFG["collect_every_h"]))
    # быстрая полоса: пара десятков агентств и служб оповещения. Обход занимает
    # секунды, поэтому срочное проверяется раз в четверть часа, а не раз в
    # четыре — землетрясение не должно ждать очередного полного сбора
    need_wire = (last_wire is None or
                 now - last_wire
                 >= timedelta(minutes=max(1, int(CFG["breaking_every_min"]))))

    if ready:
        # свежее собираем один раз на всех, дальше выпуск каждому свой
        worker.submit("collect", collect)
        for sub in ready:
            worker.submit("digest:%s" % sub["chat_id"], digest_job(sub))
        # ...и срочное проверяем тоже. Раньше здесь стоял ранний выход, и в
        # часы планового выпуска проверка не выполнялась вовсе
        worker.submit("breaking", urgent_job(waiting))
        return

    if need_collect:
        # срочное ищем сразу после сбора: свежие материалы уже в базе
        def job():
            collect()
            urgent_job(waiting)()
        worker.submit("collect", job)
    elif need_wire:
        worker.submit("wire", urgent_job(waiting, wire=True))


def scheduler_loop(worker, stop) -> None:
    while not stop.is_set():
        try:
            tick(worker)
        except Exception as exc:  # noqa: BLE001 — планировщик не должен умирать
            log.exception("Ошибка планировщика: %s", exc)
        stop.wait(TICK_SECONDS)


def daemon():
    plan = sections.plan()
    log.info("Демон запущен. Разделов: %d (%s). Отправка в %s (%s) по %d новости "
             "на раздел. Сбор раз в %d ч.",
             len(plan), ", ".join(plan), subscribers.schedule_human(), tz_label(),
             CFG["per_section"], CFG["collect_every_h"])
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

    if CFG["web"]:
        # страница живёт в том же процессе: тот же worker нужен ей, чтобы
        # показывать, чем бот занят прямо сейчас
        from . import web as webui
        webui.start_background(worker)

    if not CFG["listen"]:
        log.info("Telegram не слушаю (ND_LISTEN=0) — только отправка по расписанию")
        try:
            while True:
                stop.wait(3600)
        except KeyboardInterrupt:
            stop.set()
        return

    conn = db()
    try:
        drain_backlog(conn)
        count = len(subscribers.all_rows(conn))
    finally:
        conn.close()
    log.info("Слушаю Telegram: кнопки под выпуском и заявки новых чатов. "
             "Команды бот не выполняет — они на странице в браузере. "
             "Владелец: chat_id %s, подписчиков: %d", config.TG_CHAT, count)
    try:
        poll_forever(worker, stop)
    except KeyboardInterrupt:
        stop.set()
        log.info("Остановлен по Ctrl+C")
