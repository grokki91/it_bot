# -*- coding: utf-8 -*-
"""Сборка текста выпуска и подгонка под лимит Telegram."""
from __future__ import annotations

import html as html_mod

from . import feedback
from .config import CFG, local_now, log
from .rank import primary_of
from .telegram import TG_LIMIT

EMOJI = {"labs": "🚀", "research": "🔬", "opensource": "🛠", "media": "📰",
         "community": "💬", "business": "💰", "policy": "⚖️", "other": "📌"}
MONTHS = ("января февраля марта апреля мая июня июля августа сентября октября "
          "ноября декабря").split()


def esc(text) -> str:
    return html_mod.escape(str(text or ""), quote=False)


def render(cards, scanned, trim=0):
    """trim: 0 — полный вид, 1 — без «почему», 2 — только заголовки со ссылками."""
    day = local_now()
    head = ["📡 <b>Дайджест · %d %s</b>" % (day.day, MONTHS[day.month - 1]),
            "<i>%d из %d материалов за сутки</i>" % (len(cards), scanned), ""]
    blocks = []
    for num, (card, group, score, category) in enumerate(cards, 1):
        main = primary_of(group)
        title = card.get("headline") or main["title"]
        others = sorted({i["source_id"] for i in group} - {main["source_id"]})[:2]
        also = " · " + esc(", ".join(others)) if others else ""
        link = '🔗 <a href="%s">%s</a>%s · ⭐ %.1f' % (
            esc(main["url"]), esc(main["source_id"]), also, score)
        if trim >= 2:
            blocks.append("%s <b>%d. %s</b>\n%s"
                          % (EMOJI.get(category, "📌"), num, esc(title), link))
            continue
        lines = ["%s <b>%d. %s</b>" % (EMOJI.get(category, "📌"), num, esc(title))]
        what = str(card.get("what") or main["summary"][:300]).strip()
        if what:
            lines.append(esc(what))
        why = str(card.get("why") or "").strip()
        if why and trim == 0:
            lines.append("💡 " + esc(why))
        lines.append(link)
        blocks.append("\n".join(lines))
    return "\n".join(head) + "\n" + "\n\n".join(blocks)


def fit_message(cards, scanned):
    """Возвращает список пар (текст, карточки этого сообщения).

    Карточки нужны вместе с текстом: под каждым сообщением своя клавиатура
    реакций, и номера кнопок должны совпадать с номерами в тексте.
    """
    for trim in (0, 1, 2):
        text = render(cards, scanned, trim)
        if len(text) <= TG_LIMIT - 60:
            if trim and CFG["one_message"]:
                log.info("Сообщение длинное — сократил детализацию (уровень %d)", trim)
            return [(text, cards)]
    half = max(len(cards) // 2, 1)          # всё ещё длинно — режем по новостям
    if len(cards) <= 1:
        return [(render(cards, scanned, 2)[:TG_LIMIT], cards)]
    return fit_message(cards[:half], scanned) + fit_message(cards[half:], scanned)


def breaking_card(card, group, score, category):
    """Отдельная карточка для срочного: одна новость, но с пометкой ⚡."""
    main = primary_of(group)
    others = sorted({i["source_id"] for i in group} - {main["source_id"]})[:3]
    lines = ["⚡ <b>Срочно</b>", "",
             "%s <b>%s</b>" % (EMOJI.get(category, "📌"),
                               esc(card.get("headline") or main["title"]))]
    what = str(card.get("what") or main["summary"][:300]).strip()
    if what:
        lines.append(esc(what))
    why = str(card.get("why") or "").strip()
    if why:
        lines.append("💡 " + esc(why))
    confirm = " · подтверждают: " + esc(", ".join(others)) if others else ""
    lines.append('🔗 <a href="%s">%s</a>%s · ⭐ %.1f'
                 % (esc(main["url"]), esc(main["source_id"]), confirm, score))
    lines.append("<i>Остальное придёт в утреннем выпуске.</i>")
    return "\n".join(lines)


# --------------------------------------------------------- кнопки под выпуском
MARK = "✓"
BUTTONS = ((feedback.UP, "👍"), (feedback.DOWN, "👎"), ("save", "🔖"))


def feedback_keyboard(cards):
    """Ряд кнопок на каждую новость: «1 👍», «1 👎», «1 🔖».

    В callback_data влезает только 64 байта, поэтому кладём туда хэш ссылки —
    по нему потом находятся и заголовок, и источник.
    """
    if not CFG["feedback_buttons"]:
        return None
    keyboard = []
    for num, (_card, group, _score, _cat) in enumerate(cards, 1):
        url_hash = primary_of(group)["url_hash"]
        keyboard.append([{"text": "%d %s" % (num, icon),
                          "callback_data": "fb:%s:%s" % (kind, url_hash)}
                         for kind, icon in BUTTONS])
    return keyboard


def mark_pressed(keyboard, data, pressed=True):
    """Отмечает нажатую кнопку галочкой прямо в присланной Telegram разметке.

    Оценка и закладка независимы: 👍 снимает 👎 в своём ряду, а 🔖 живёт сам
    по себе. Итоговое состояние решает вызывающий — он же пишет его в базу.
    """
    kind = data.split(":")[1] if data.count(":") >= 2 else ""
    for row in keyboard:
        if not any(b.get("callback_data") == data for b in row):
            continue
        for button in row:
            other = button.get("callback_data", "")
            other_kind = other.split(":")[1] if other.count(":") >= 2 else ""
            bare = button.get("text", "").replace(MARK, "")
            if other == data:
                button["text"] = bare + MARK if pressed else bare
            elif kind in (feedback.UP, feedback.DOWN) and other_kind in (
                    feedback.UP, feedback.DOWN):
                button["text"] = bare
    return keyboard
