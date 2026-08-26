# -*- coding: utf-8 -*-
"""Экран «Мои темы»: читатель сам выбирает, что идёт в выпуске первым.

Команд в Telegram нет и заводить их не хочется: бот там только рассылает.
Но порядок разделов — это единственная настройка, которую читатель хочет
трогать регулярно («спорт мне важнее политики»), и гонять его за этим на VPS
неправильно. Поэтому топ настраивается так же, как читается выпуск, —
кнопками: строка на раздел, нажал — отметил, нажал ещё раз — снял.

Экран описывается маршрутом в callback_data: `pref:<действие>:<раздел>:<выпуск>`.

    open           показать экран
    fav:<раздел>   отметить раздел или снять отметку
    clear          сбросить топ, вернуть обычный порядок

Номер выпуска едет с собой только ради кнопки «← К выпуску»: экран открывают
из оглавления, и вернуться в него надо в то же самое сообщение. Ноль значит
«пришли не из выпуска» — тогда кнопки возврата просто нет.

Сам список хранится строкой в `subscribers.favorites`, разбирается и
применяется в `sections.order()`; здесь только показ и переключение.
"""
from __future__ import annotations

from . import sections
from .profiles import PROFILES, label
from .render import esc, plural
from .telegram import TG_LIMIT

PREF = "pref"
OPEN, FAV, CLEAR = "open", "fav", "clear"

#: Telegram даёт на callback_data 64 байта — раздел с длинным именем из
#: profiles.json в маршрут не влезет, и кнопку для него мы просто не рисуем
CALLBACK_LIMIT = 64


def route(name=OPEN, topic="", ident=0) -> str:
    """Маршрут экрана в callback_data."""
    return ":".join((PREF, name, str(topic or ""), str(int(ident or 0))))


def parse(data) -> tuple:
    """'pref:fav:ai:12' -> ('fav', 'ai', 12). Чужой маршрут -> ('', '', 0)."""
    parts = str(data or "").split(":")
    if len(parts) < 2 or parts[0] != PREF:
        return "", "", 0
    ident = parts[3] if len(parts) > 3 else "0"
    topic = sections.resolve(parts[2]) if len(parts) > 2 else ""
    return parts[1], topic, (int(ident) if ident.isdigit() else 0)


def fits(data) -> bool:
    return len(data.encode("utf-8")) <= CALLBACK_LIMIT


# --------------------------------------------------------------------- текст
def toggle(chosen, topic) -> tuple:
    """Новый список избранного и что сказать читателю во всплывашке.

    Список не заменяется, а дополняется с конца: порядок отметок — это и есть
    порядок разделов в выпуске, и добавление шестой темы не должно молча
    выкидывать первую.
    """
    if topic in chosen:
        return [t for t in chosen if t != topic], "Убрал %s" % label(topic)
    if len(chosen) >= sections.MAX_FAVORITES:
        return list(chosen), ("Уже %d — сначала снимите одну"
                              % sections.MAX_FAVORITES)
    return list(chosen) + [topic], "%s — теперь №%d" % (label(topic),
                                                        len(chosen) + 1)


def text(sub) -> str:
    """Что написано на экране: сам топ и что он значит."""
    chosen = sections.favorites(sub)
    lines = ["⭐ <b>МОИ ТЕМЫ</b>", ""]
    if chosen:
        lines += ["%d. %s" % (at, esc(label(topic)))
                  for at, topic in enumerate(chosen, 1)]
        left = sections.MAX_FAVORITES - len(chosen)
        lines += ["", "<i>Эти разделы идут в выпуске первыми, остальные — "
                      "следом.%s</i>"
                  % ("" if not left else " Можно отметить ещё %d." % left)]
    else:
        lines += ["<i>Пока ничего не отмечено — разделы идут обычным порядком.",
                  "Отметьте до %d разделов, и выпуск будет начинаться "
                  "с них.</i>" % sections.MAX_FAVORITES]
    return "\n".join(lines)[:TG_LIMIT]


def mark(topic, chosen, mine) -> str:
    """⭐ с номером — в топе, ✅ — просто в выпуске, ▫️ — не приходит вовсе."""
    if topic in chosen:
        return "⭐%d" % (chosen.index(topic) + 1)
    return "✅" if topic in mine else "▫️"


def keyboard(sub, ident=0) -> list:
    """Строка на каждый раздел плюс «сбросить» и возврат в выпуск."""
    chosen = sections.favorites(sub)
    mine = set(sections.plan(sub))
    rows = []
    for topic in sections.known():
        data = route(FAV, topic, ident)
        if not fits(data):              # раздел из profiles.json с длинным именем
            continue
        rows.append([{"text": "%s %s" % (mark(topic, chosen, mine), label(topic)),
                      "callback_data": data}])
    if chosen:
        rows.append([{"text": "♻️ Сбросить (%d %s)"
                              % (len(chosen), plural(len(chosen), "тема", "темы",
                                                     "тем")),
                      "callback_data": route(CLEAR, "", ident)}])
    if ident:
        from .issueview import HOME
        from .issueview import route as issue_route
        rows.append([{"text": "← К выпуску", "callback_data": issue_route(ident,
                                                                         HOME)}])
    return rows


def screen(sub, ident=0) -> tuple:
    """Текст экрана и кнопки под ним."""
    return text(sub), keyboard(sub, ident)


def entry(ident=0) -> list:
    """Строка-кнопка «Мои темы» — её подставляют под выпуск и под расписание."""
    return [{"text": "⭐ Мои темы", "callback_data": route(OPEN, "", ident)}]


def store(topics) -> str:
    """Список избранного в том виде, в каком его принимает /set top."""
    return sections.store([t for t in topics if t in PROFILES]) or "сброс"
