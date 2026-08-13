# -*- coding: utf-8 -*-
"""Настройки, которые можно менять из чата, — и их проверка.

Одно описание на всё: и подсказка в /settings, и разбор значения в /set,
и запись в ~/.newsdigest/env, чтобы правка пережила перезапуск. Проверки
здесь нарочно придирчивые и объясняют, что не так: настройка, введённая
с телефона одной строкой, не должна тихо превратиться в мусор.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import config, subscribers
from .config import CFG, write_env
from .profiles import PROFILES

TRUE = ("1", "true", "yes", "on", "да", "вкл", "включить")
FALSE = ("0", "false", "no", "off", "нет", "выкл", "выключить")


class Invalid(ValueError):
    """Значение не годится. Текст показывается пользователю как есть."""


class Setting:
    def __init__(self, key, env, parse, describe, show=None):
        self.key, self.env = key, env
        self.parse, self.describe = parse, describe
        self.show = show or (lambda v: str(v))

    def current(self):
        return self.show(CFG[self.key])


# --------------------------------------------------------------- разборщики
def as_bool(raw):
    low = raw.strip().lower()
    if low in TRUE:
        return True
    if low in FALSE:
        return False
    raise Invalid("нужно вкл или выкл")


def as_int(low, high):
    def parse(raw):
        try:
            value = int(raw.strip())
        except ValueError:
            raise Invalid("нужно целое число от %d до %d" % (low, high))
        if not low <= value <= high:
            raise Invalid("допустимо от %d до %d" % (low, high))
        return value
    return parse


def as_float(low, high):
    def parse(raw):
        try:
            value = float(raw.strip().replace(",", "."))
        except ValueError:
            raise Invalid("нужно число от %.1f до %.1f" % (low, high))
        if not low <= value <= high:
            raise Invalid("допустимо от %.1f до %.1f" % (low, high))
        return value
    return parse


def as_time(raw):
    parts = raw.strip().split(":")
    if len(parts) != 2:
        raise Invalid("формат ЧЧ:ММ, например 09:00")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise Invalid("формат ЧЧ:ММ, например 09:00")
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise Invalid("часы 0-23, минуты 0-59")
    return "%02d:%02d" % (hour, minute)


def as_quiet(raw):
    value = raw.strip().lower()
    if value in ("нет", "off", "-", "никогда"):
        return ""
    parts = value.split("-")
    if len(parts) != 2:
        raise Invalid("формат ЧЧ:ММ-ЧЧ:ММ, например 23:00-08:00 (или «нет»)")
    return "%s-%s" % (as_time(parts[0]), as_time(parts[1]))


def as_topic(raw):
    value = raw.strip()
    if value not in PROFILES:
        raise Invalid("такой темы нет. Есть: %s" % ", ".join(sorted(PROFILES)))
    return value


def as_tz(raw):
    value = raw.strip()
    if value and not (Path("/usr/share/zoneinfo") / value).exists():
        raise Invalid("такого пояса нет в системе. Пример: Europe/Riga")
    return value


def as_text(limit=60):
    def parse(raw):
        value = raw.strip()
        if not value:
            raise Invalid("пустое значение")
        if len(value) > limit:
            raise Invalid("слишком длинно (максимум %d символов)" % limit)
        return value
    return parse


def on_off(value):
    return "вкл" if value else "выкл"


# ------------------------------------------------------------------- реестр
SPEC = {
    "topic": Setting("topic", "ND_TOPIC", as_topic,
                     "тема выпуска: " + ", ".join(sorted(PROFILES))),
    "time": Setting("send_at", "ND_SEND_AT", as_time,
                    "во сколько присылать выпуск, ЧЧ:ММ"),
    "tz": Setting("tz", "ND_TZ", as_tz, "часовой пояс, например Europe/Riga"),
    "max": Setting("max_items", "ND_MAX_ITEMS", as_int(1, 20),
                   "сколько новостей в выпуске максимум"),
    "min": Setting("min_items", "ND_MIN_ITEMS", as_int(1, 20),
                   "ниже этого числа выпуск считается тихим днём"),
    "score": Setting("min_score", "ND_MIN_SCORE", as_float(1, 10),
                     "порог важности 1-10: ниже не публикуем"),
    "every": Setting("collect_every_h", "ND_COLLECT_EVERY", as_int(1, 24),
                     "раз в сколько часов собирать новости"),
    "language": Setting("language", "ND_LANGUAGE", as_text(30),
                        "язык карточек"),
    "silent": Setting("silent", "ND_SILENT", as_bool,
                      "отправлять без звука", on_off),
    "breaking": Setting("breaking", "ND_BREAKING", as_bool,
                        "присылать срочное вне расписания", on_off),
    "quiet": Setting("breaking_quiet", "ND_BREAKING_QUIET", as_quiet,
                     "часы тишины для срочного, ЧЧ:ММ-ЧЧ:ММ или «нет»",
                     lambda v: v or "нет"),
    "buttons": Setting("feedback_buttons", "ND_FEEDBACK", as_bool,
                       "кнопки 👍/👎/🔖 под выпуском", on_off),
    "taste": Setting("feedback_weight", "ND_FEEDBACK_WEIGHT", as_float(0, 1),
                     "насколько сильно реакции двигают отбор, 0-1",
                     lambda v: "%.2f" % v),
    "model": Setting("model_summary", "ND_MODEL_SUMMARY", as_text(60),
                     "модель, которая пишет карточки"),
}

#: привычные синонимы — чтобы не гадать, как называется настройка
ALIASES = {
    "send_at": "time", "время": "time", "тема": "topic", "пояс": "tz",
    "max_items": "max", "min_items": "min", "min_score": "score",
    "порог": "score", "collect_every": "every", "язык": "language",
    "тихо": "quiet", "срочные": "breaking", "кнопки": "buttons",
    "feedback_weight": "taste", "вкусы": "taste", "звук": "silent",
}


def resolve(name: str):
    key = (name or "").strip().lower().lstrip("-")
    key = ALIASES.get(key, key)
    return key, SPEC.get(key)


def apply(name: str, raw: str):
    """Проверяет и применяет настройку. Возвращает (имя, показанное значение).

    Бросает Invalid с человеческим объяснением, если значение не годится.
    """
    key, setting = resolve(name)
    if not setting:
        raise Invalid("не знаю настройку «%s». Список: /set" % name)
    value = setting.parse(raw)

    # взаимные ограничения проверяем до записи, иначе получится выпуск,
    # у которого минимум больше максимума
    if key == "max" and value < CFG["min_items"]:
        raise Invalid("максимум не может быть меньше минимума (%d). "
                      "Сначала /set min %d" % (CFG["min_items"], value))
    if key == "min" and value > CFG["max_items"]:
        raise Invalid("минимум не может быть больше максимума (%d). "
                      "Сначала /set max %d" % (CFG["max_items"], value))

    CFG[setting.key] = value
    stored = "1" if value is True else "0" if value is False else str(value)
    os.environ[setting.env] = stored        # чтобы load_env не откатил правку
    write_env({setting.env: stored}, allow_empty=True)
    if key == "tz":
        config.init_tz()
    return key, setting.current()


def overview():
    """Пары (имя, значение, пояснение) для /settings и /set без аргументов."""
    return [(name, SPEC[name].current(), SPEC[name].describe)
            for name in sorted(SPEC)]


# ---------------------------------------------------- личные настройки чата
#: что подписчик волен менять у себя; остальное — только владелец
PERSONAL = {"topic": "topic", "time": "send_at", "tz": "tz", "language": "language",
            "max": "max_items", "score": "min_score", "silent": "silent"}


def personal_view(sub) -> dict:
    """Личные значения подписчика в терминах имён настроек."""
    if sub is None:
        return {}
    out = {}
    for name, field in PERSONAL.items():
        value = sub[field]
        if value in (None, subscribers.BLANK[field]):
            continue
        out[name] = SPEC[name].show(bool(value) if field == "silent" else value)
    return out


def apply_for(conn, chat_id, owner, name, raw):
    """Правка настройки от имени чата.

    Владелец меняет значения по умолчанию для всех, остальные — только свои.
    Возвращает (имя, показанное значение, 'global'|'personal').
    """
    key, setting = resolve(name)
    if not setting:
        raise Invalid("не знаю настройку «%s». Список: /settings" % name)

    if owner:
        key, shown = apply(key, raw)
        subscribers.remember_global_change(setting.key, CFG[setting.key])
        return key, shown, "global"

    if key not in PERSONAL:
        raise Invalid("«%s» меняет только владелец бота. Ваши настройки: %s"
                      % (key, ", ".join(sorted(PERSONAL))))
    value = setting.parse(raw)
    if key == "max" and value < CFG["min_items"]:
        raise Invalid("в выпуске не может быть меньше %d новостей" % CFG["min_items"])
    subscribers.set_field(conn, chat_id, PERSONAL[key],
                          int(value) if key == "silent" else value)
    return key, setting.show(value), "personal"
