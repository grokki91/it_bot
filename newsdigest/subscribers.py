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
PERSONAL = ("topic", "sections", "favorites", "per_section", "send_at", "tz",
            "language", "max_items", "min_score", "silent")

#: как «пусто» выглядит для каждого поля
BLANK = {"topic": "", "sections": "", "favorites": "", "per_section": 0,
         "send_at": "", "tz": "", "language": "", "max_items": 0,
         "min_score": 0.0, "silent": -1}

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
        # день из одиночной версии закрываем целиком: в нём был ровно один
        # выпуск, а метка теперь хранит ещё и его номер в сутках
        conn.execute("UPDATE subscribers SET last_digest=? WHERE chat_id=?",
                     ("%s#%d" % (last, per_day()), owner))
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


def set_last_digest(conn, chat_id, mark):
    """Запоминает, какой выпуск подписчику уже ушёл. Метка — из slot_mark()."""
    conn.execute("UPDATE subscribers SET last_digest=? WHERE chat_id=?",
                 (mark, str(chat_id)))
    conn.commit()


# ------------------------------------------------------------ личные значения
def field_of(sub, field):
    """Значение поля подписчика или «пусто», если колонки ещё нет."""
    try:
        return sub[field]
    except (IndexError, KeyError):       # база старше этой версии
        return BLANK[field]


def overrides(sub) -> dict:
    """Только то, что подписчик задал сам, в виде ключей CFG."""
    if sub is None:
        return {}
    out = {}
    for field in PERSONAL:
        value = field_of(sub, field)
        if value != BLANK[field] and value is not None:
            out["silent" if field == "silent" else field] = (
                bool(value) if field == "silent" else value)
    return out


def describe(sub) -> str:
    parts = []
    for field in PERSONAL:
        value = field_of(sub, field)
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


#: больше четырёх выпусков в сутки — это уже лента, а не дайджест
MAX_PER_DAY = 4


def per_day() -> int:
    """Сколько выпусков в сутки. Настройка общая: разделение по подписчикам
    удорожало бы модель, а расписание у всех и так одно."""
    try:
        value = int(CFG["per_day"])
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, MAX_PER_DAY))


def slots_for(sub=None) -> list:
    """Минуты суток, в которые идёт выпуск: [540, 1260] — это 09:00 и 21:00.

    Выпуски раскладываются по суткам равномерно от send_at, поэтому «дважды
    в день» — это ваше время и оно же плюс 12 часов. Отсчёт ведётся от начала
    суток (send_at % шаг), иначе последний выпуск наезжал бы на первый.
    """
    hour, minute = send_at_for(sub)
    step = 1440 // per_day()
    first = (hour * 60 + minute) % step
    return [first + step * i for i in range(1440 // step)]


def slot_index(sub=None, now=None) -> int:
    """Какой выпуск суток уже пора отправить: 1, 2… 0 — первый ещё впереди."""
    now = now if now is not None else now_for(sub)
    minutes = now.hour * 60 + now.minute
    passed = [i for i, at in enumerate(slots_for(sub), 1) if minutes >= at]
    return passed[-1] if passed else 0


def slot_mark(sub=None, now=None) -> str:
    """Метка выпуска для колонки last_digest: «2026-08-15#2» — второй за день.

    Раньше там лежала просто дата: один выпуск в сутки, один день — одна
    запись. С несколькими выпусками дня уже мало, нужен ещё и номер.
    """
    now = now if now is not None else now_for(sub)
    return "%s#%d" % (now.strftime("%Y-%m-%d"), slot_index(sub, now) or 1)


def schedule_human(sub=None) -> str:
    """«09:00 и 21:00» — все выпуски суток одной строкой, для логов и справки."""
    times = ["%02d:%02d" % (at // 60, at % 60) for at in slots_for(sub)]
    if len(times) == 1:
        return times[0]
    return "%s и %s" % (", ".join(times[:-1]), times[-1])


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
    """Кому пора отправлять выпуск прямо сейчас.

    Пропущенный выпуск не догоняется: если бот молчал с утра, в девять вечера
    придёт вечерний выпуск, а не два подряд — метка сравнивается с текущим
    слотом, а не со всеми прошедшими.
    """
    ready = []
    for sub in active(conn):
        now = now_for(sub)
        if not slot_index(sub, now):       # первый выпуск суток ещё впереди
            continue
        if sub["last_digest"] == slot_mark(sub, now):
            continue
        if retry_pending(conn, sub["chat_id"]):
            continue
        ready.append(sub)
    return ready


def tz_of(sub=None) -> str:
    """Пояс подписчика или общий пояс бота — для подписи под временем."""
    return (sub["tz"] if sub is not None and sub["tz"] else config.tz_label())


def next_send_at(sub=None, now=None) -> datetime:
    """Момент следующего выпуска — в местном времени подписчика.

    Момент, а не строка: из него получаются и «завтра в 09:00», и дата, и
    «через сколько», причём без второго обхода расписания.
    """
    now = now if now is not None else now_for(sub)
    minutes = now.hour * 60 + now.minute
    slots = slots_for(sub)
    ahead = [at for at in slots if at > minutes]
    at = ahead[0] if ahead else slots[0]
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(days=0 if ahead else 1, minutes=at)


def next_send_human(sub=None, now=None) -> str:
    """«сегодня в 21:00 (Europe/Riga)» — когда ждать следующий выпуск."""
    now = now if now is not None else now_for(sub)
    at = next_send_at(sub, now)
    when = "сегодня" if at.date() == now.date() else "завтра"
    return "%s в %02d:%02d (%s)" % (when, at.hour, at.minute, tz_of(sub))


def left_human(sub=None, now=None) -> str:
    """«3 ч 40 мин» — сколько ждать следующего выпуска."""
    now = now if now is not None else now_for(sub)
    total = max(int((next_send_at(sub, now) - now).total_seconds()) // 60, 0)
    hours, minutes = divmod(total, 60)
    if hours and minutes:
        return "%d ч %d мин" % (hours, minutes)
    if hours:
        return "%d ч" % hours
    return "%d мин" % minutes if minutes else "меньше минуты"
