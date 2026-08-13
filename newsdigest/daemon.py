# -*- coding: utf-8 -*-
"""Фоновый цикл: сбор по расписанию и отправка выпуска в назначенное время."""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

from .config import CFG, HOME, LOG_FILE, local_now, log, tz_label
from .feedparse import parse_date
from .pipeline import build_and_send
from .sources import collect
from .storage import db, meta_get


def daemon():
    log.info("Демон запущен. Тема: %s. Отправка в %s (%s). Сбор раз в %d ч.",
             CFG["topic"], CFG["send_at"], tz_label(), CFG["collect_every_h"])
    log.info("Каталог данных: %s | лог: %s", HOME, LOG_FILE)
    from .cli import require_secrets
    require_secrets()
    try:
        hour, minute = [int(x) for x in CFG["send_at"].split(":")]
    except ValueError:
        sys.exit("Некорректное время отправки: %r. Формат ЧЧ:ММ" % CFG["send_at"])

    while True:
        try:
            conn = db()
            last_collect = parse_date(meta_get(conn, "last_collect", ""))
            last_digest = meta_get(conn, "last_digest_date", "")
            conn.close()

            need_collect = (last_collect is None or
                            datetime.now(timezone.utc) - last_collect
                            >= timedelta(hours=CFG["collect_every_h"]))
            now = local_now()
            today = now.strftime("%Y-%m-%d")
            due = now.hour * 60 + now.minute >= hour * 60 + minute
            need_digest = due and last_digest != today

            if need_digest:
                log.info("Время дайджеста — собираю свежее и отправляю")
                collect()
                build_and_send()
            elif need_collect:
                collect()
        except Exception as exc:  # noqa: BLE001 — демон не должен умирать
            log.exception("Ошибка в цикле: %s", exc)
        time.sleep(60)
