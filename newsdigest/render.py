# -*- coding: utf-8 -*-
"""Сборка текста выпуска и подгонка под лимит Telegram."""
from __future__ import annotations

import html as html_mod

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
    """Возвращает список сообщений. Сначала пытаемся уместить всё в одно."""
    for trim in (0, 1, 2):
        text = render(cards, scanned, trim)
        if len(text) <= TG_LIMIT - 60:
            if trim and CFG["one_message"]:
                log.info("Сообщение длинное — сократил детализацию (уровень %d)", trim)
            return [text]
    half = max(len(cards) // 2, 1)          # всё ещё длинно — режем по новостям
    if len(cards) <= 1:
        return [render(cards, scanned, 2)[:TG_LIMIT]]
    return fit_message(cards[:half], scanned) + fit_message(cards[half:], scanned)
