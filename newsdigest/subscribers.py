# -*- coding: utf-8 -*-
"""Подписчики: у каждого своя тема, своё время и своя история.

Личный скрипт превращается в сервис на несколько чатов. Правило простое:
CFG задаёт значения по умолчанию, а в строке подписчика лежат только его
отличия. Пустая строка, 0 или -1 значат «как у всех» — поэтому личные
настройки не разъезжаются с общими сами по себе.

Владелец (TELEGRAM_CHAT_ID) правит общие настройки, остальные — только
свои. Новый чат по умолчанию попадает в pending и ждёт одобрения: иначе
любой прохожий начнёт тратить ваш баланс модели.
"""
from __future__ import annotations

import contextlib
import threading
from datetime import datetime, timedelta, timezone

from . import config
from .config import CFG, local_now, log, now_iso

#: настройки, которые подписчик может держать своими
PERSONAL = ("topic", "send_at", "tz", "language", "max_items", "min_score", "silent")

#: как «пусто» выглядит для каждого поля
BLANK = {"topic": "", "send_at": "", "tz": "", "language": "",
         "max_items": 0, "min_score": 0.0, "silent": -1}

ROLES = ("owner", "member", "pending")


# ------------------------------------------------------------------- чтение
def get(conn, chat_id):
    return conn.execute("SELECT * FROM subscribers WHERE chat_id=?",
                        (str(chat_id),)).fetchone()


def all_rows(conn, roles=("owner", "member")):
    marks = ",".join("?" * len(roles))
    return list(conn.execute(
        "SELECT * FROM subscribers WHERE role IN (%s) ORDER BY created_at" % marks,
        [str(r) for r in roles]))


def active(conn):
    return [s for s in all_rows(conn) if not s["paused"]]


def ensure_owner(conn):
    """Владелец обязан быть в таблице — на нём держится вся проверка доступа."""
    owner = str(config.TG_CHAT or "")
    if not owner:
        return None
    row = get(conn, owner)
    if row is None:
        conn.execute("INSERT INTO subscribers(chat_id, role, title, created_at) "
                     "VALUES (?, 'owner', 'владелец', ?)", (owner, now_iso()))
        conn.commit()
        adopt_legacy(conn, owner)
    elif row["role"] != "owner":
        conn.execute("UPDATE subscribers SET role='owner' WHERE chat_id=?", (owner,))
        conn.commit()
    return get(conn, owner)


def adopt_legacy(conn, owner):
    """Переносит настройки одиночной версии: extra_chats и общую паузу."""
    from .storage import meta_get, meta_set

    extra = meta_get(conn, "extra_chats", "")
    for chat_id in {c.strip() for c in extra.split(",") if c.strip()}:
        add(conn, chat_id, role="member", title="из extra_chats")
    if extra:
        meta_set(conn, "extra_chats", "")
        log.info("Чаты из extra_chats перенесены в подписчиков")

    if meta_get(conn, "paused", "0") == "1":
        conn.execute("UPDATE subscribers SET paused=1 WHERE chat_id=?", (owner,))
    last = meta_get(conn, "last_digest_date", "")
    if last:
        conn.execute("UPDATE subscribers SET last_digest=? WHERE chat_id=?",
                     (last, owner))
    conn.commit()


# -------------------------------------------------------------------- запись
def add(conn, chat_id, role="member", title="", kind="private"):
    conn.execute(
        "INSERT INTO subscribers(chat_id, title, kind, role, created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET role=excluded.role, "
        "title=CASE WHEN excluded.title != '' THEN excluded.title ELSE "
        "subscribers.title END",
        (str(chat_id), title[:80], kind, role, now_iso()))
    conn.commit()
    return get(conn, chat_id)


def remove(conn, chat_id) -> bool:
    if str(chat_id) == str(config.TG_CHAT):
        raise ValueError("владельца удалить нельзя")
    cur = conn.execute("DELETE FROM subscribers WHERE chat_id=?", (str(chat_id),))
    conn.commit()
    return cur.rowcount > 0


def set_field(conn, chat_id, field, value):
    if field not in PERSONAL + ("paused", "title", "role"):
        raise ValueError("поле %r менять нельзя" % field)
    conn.execute("UPDATE subscribers SET %s=? WHERE chat_id=?" % field,
                 (value, str(chat_id)))
    conn.commit()


def set_last_digest(conn, chat_id, day):
    conn.execute("UPDATE subscribers SET last_digest=? WHERE chat_id=?",
                 (day, str(chat_id)))
    conn.commit()


# ------------------------------------------------------------ личные значения
def overrides(sub) -> dict:
    """Только то, что подписчик задал сам, в виде ключей CFG."""
    if sub is None:
        return {}
    out = {}
    for field in PERSONAL:
        value = sub[field]
        if value != BLANK[field] and value is not None:
            out["silent" if field == "silent" else field] = (
                bool(value) if field == "silent" else value)
    return out


def describe(sub) -> str:
    parts = []
    for field in PERSONAL:
        value = sub[field]
        if value != BLANK[field] and value is not None:
            parts.append("%s=%s" % (field, "вкл" if field == "silent" and value
                                    else "выкл" if field == "silent" else value))
    return ", ".join(parts) or "всё как по умолчанию"


#: пока идёт выпуск для подписчика, CFG временно показывает ЕГО значения.
#: Работа идёт в одной фоновой нити, так что наложение всегда одно; замок
#: нужен, чтобы правка настроек из чата в этот момент не потерялась.
_LOCK = threading.RLock()
_ACTIVE = None


@contextlib.contextmanager
def overlay(sub):
    """Временно накладывает личные настройки подписчика на CFG."""
    global _ACTIVE
    patch = overrides(sub)
    if not patch:
        yield
        return
    with _LOCK:
        saved = {key: CFG[key] for key in patch}
        CFG.update(patch)
        previous, _ACTIVE = _ACTIVE, saved
    try:
        yield
    finally:
        with _LOCK:
            CFG.update(saved)
            _ACTIVE = previous


def remember_global_change(key, value):
    """Правка общей настройки во время выпуска не должна пропасть.

    Если сейчас наложены личные настройки подписчика, новое значение
    кладётся в сохранённый снимок — тот, который вернётся после выпуска.
    """
    with _LOCK:
        if _ACTIVE is not None and key in _ACTIVE:
            _ACTIVE[key] = value


# ------------------------------------------------------------------ время
def zone(name):
    try:
        from zoneinfo import ZoneInfo          # Python 3.9+
        return ZoneInfo(name)
    except Exception:                          # noqa: BLE001 — нет модуля или пояса
        return None


def now_for(sub) -> datetime:
    """Местное время подписчика. Свой пояс — если он задан и известен системе."""
    name = (sub["tz"] or "").strip() if sub is not None else ""
    if name:
        tzinfo = zone(name)
        if tzinfo is not None:
            return datetime.now(timezone.utc).astimezone(tzinfo).replace(tzinfo=None)
        log.debug("Пояс %r подписчика не поддерживается — беру общий", name)
    return local_now()


def send_at_for(sub) -> tuple:
    raw = (sub["send_at"] or "").strip() if sub is not None else ""
    raw = raw or CFG["send_at"]
    try:
        hour, minute = [int(x) for x in raw.split(":")]
        return hour, minute
    except ValueError:
        log.warning("Некорректное время отправки %r — беру 09:00", raw)
        return 9, 0


#: пустой выпуск не занимает день целиком, но и повторять его каждую минуту
#: нельзя: ранжирование стоит денег. Пробуем снова через час.
RETRY_AFTER_H = 1


def note_empty(conn, chat_id) -> None:
    """Помечает неудачную попытку: новостей не нашлось, день не закрыт."""
    from .storage import meta_set
    meta_set(conn, "digest_attempt:%s" % chat_id, now_iso())


def retry_pending(conn, chat_id) -> bool:
    """True — недавно уже пробовали и ничего не нашли, ждём."""
    from .feedparse import parse_date
    from .storage import meta_get

    last = parse_date(meta_get(conn, "digest_attempt:%s" % chat_id, ""))
    if last is None:
        return False
    return datetime.now(timezone.utc) - last < timedelta(hours=RETRY_AFTER_H)


def due(conn):
    """Кому пора отправлять выпуск прямо сейчас."""
    ready = []
    for sub in active(conn):
        now = now_for(sub)
        if sub["last_digest"] == now.strftime("%Y-%m-%d"):
            continue
        hour, minute = send_at_for(sub)
        if now.hour * 60 + now.minute < hour * 60 + minute:
            continue
        if retry_pending(conn, sub["chat_id"]):
            continue
        ready.append(sub)
    return ready


def next_send_human(sub) -> str:
    hour, minute = send_at_for(sub)
    now = now_for(sub)
    done_today = sub is not None and sub["last_digest"] == now.strftime("%Y-%m-%d")
    passed = now.hour * 60 + now.minute >= hour * 60 + minute
    when = "завтра" if (done_today or passed) else "сегодня"
    label = (sub["tz"] if sub is not None and sub["tz"] else config.tz_label())
    return "%s в %02d:%02d (%s)" % (when, hour, minute, label)


def stale_day(sub) -> str:
    """Вчерашняя дата в поясе подписчика — чтобы /digest не блокировал утренний."""
    return (now_for(sub) - timedelta(days=1)).strftime("%Y-%m-%d")
