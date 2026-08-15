# -*- coding: utf-8 -*-
"""Сборка текста выпуска и подгонка под лимит Telegram.

Выпуск состоит из блоков: раздел и его новости. Один блок без названия —
это обычный дайджест по одной теме (так выглядел выпуск до версии 3.2),
один блок с названием — ответ команды /news, много блоков — плановый
выпуск по всем разделам.
"""
from __future__ import annotations

import html as html_mod

from . import feedback
from .config import CFG, local_now, log
from .profiles import emoji as topic_emoji
from .profiles import title as topic_title
from .rank import primary_of
from .telegram import TG_LIMIT

EMOJI = {"labs": "🚀", "research": "🔬", "opensource": "🛠", "media": "📰",
         "community": "💬", "business": "💰", "policy": "⚖️", "other": "📌"}
MONTHS = ("января февраля марта апреля мая июня июля августа сентября октября "
          "ноября декабря").split()


def esc(text) -> str:
    return html_mod.escape(str(text or ""), quote=False)


def card_block(num, card, group, score, category, trim):
    """Одна новость: номер, заголовок, суть, зачем это знать и ссылка."""
    main = primary_of(group)
    title = card.get("headline") or main["title"]
    others = sorted({i["source_id"] for i in group} - {main["source_id"]})[:2]
    also = " · " + esc(", ".join(others)) if others else ""
    link = '🔗 <a href="%s">%s</a>%s · ⭐ %.1f' % (
        esc(main["url"]), esc(main["source_id"]), also, score)
    head = "%s <b>%d. %s</b>" % (EMOJI.get(category, "📌"), num, esc(title))
    if trim >= 2:
        return "%s\n%s" % (head, link)
    lines = [head]
    what = str(card.get("what") or main["summary"][:300]).strip()
    if what:
        lines.append(esc(what))
    why = str(card.get("why") or "").strip()
    if why and trim == 0:
        lines.append("💡 " + esc(why))
    lines.append(link)
    return "\n".join(lines)


def header(blocks, scanned, count, note="") -> list:
    """Шапка выпуска. У выпуска из одного раздела в ней его название."""
    day = local_now()
    date = "%d %s" % (day.day, MONTHS[day.month - 1])
    if len(blocks) == 1 and blocks[0][0]:
        lines = ["%s <b>%s</b> · %s" % (topic_emoji(blocks[0][0]),
                                        esc(topic_title(blocks[0][0])), date),
                 "<i>%d из %d материалов за сутки</i>" % (count, scanned)]
    elif len(blocks) == 1:
        lines = ["📡 <b>Дайджест · %s</b>" % date,
                 "<i>%d из %d материалов за сутки</i>" % (count, scanned)]
    else:
        lines = ["📡 <b>Дайджест · %s</b>" % date,
                 "<i>%d новостей из %d материалов · разделов: %d</i>"
                 % (count, scanned, len(blocks))]
    if note:
        lines.append("<i>%s</i>" % esc(note))
    return lines + [""]


def render_blocks(blocks, scanned, trim=0, head=True, note=""):
    """trim: 0 — полный вид, 1 — без «почему», 2 — только заголовки со ссылками.

    Нумерация сквозная внутри сообщения: под сообщением своя клавиатура,
    и «3 👍» должно указывать на третью новость именно этого сообщения.
    """
    count = sum(len(cards) for _topic, cards in blocks)
    parts = header(blocks, scanned, count, note) if head else []
    text = "\n".join(parts)
    num = 0
    chunks = []
    for topic, cards in blocks:
        if topic and len(blocks) > 1:
            chunks.append("%s <b>%s</b>" % (topic_emoji(topic),
                                            esc(topic_title(topic))))
        for card, group, score, category in cards:
            num += 1
            chunks.append(card_block(num, card, group, score, category, trim))
    return (text + "\n" if text else "") + "\n\n".join(chunks)


def flatten(blocks) -> list:
    return [card for _topic, cards in blocks for card in cards]


def fits(text) -> bool:
    return len(text) <= TG_LIMIT - 60


def fit_blocks(blocks, scanned, head=True, note=""):
    """Возвращает список пар (текст, карточки этого сообщения).

    Карточки нужны вместе с текстом: под каждым сообщением своя клавиатура
    реакций, и номера кнопок должны совпадать с номерами в тексте.

    Подборка по десятку разделов в одно сообщение не влезает никогда. Ужимать
    её до голых заголовков — значит выбросить то, ради чего дайджест и нужен,
    поэтому режем по разделам: лучше три сообщения с сутью, чем одно из
    ссылок. Внутри одного раздела наоборот: сначала ужимаем, режем в крайнем.
    """
    blocks = [(topic, cards) for topic, cards in blocks if cards]
    if not blocks:
        return []
    if len(blocks) == 1:
        return fit_one(blocks, scanned, head, note)

    text = render_blocks(blocks, scanned, 0, head, note)
    if fits(text):
        return [(text, flatten(blocks))]

    packed, current = [], []
    for block in blocks:
        first = not packed
        probe = render_blocks(current + [block], scanned, 0,
                              head and first, note if first else "")
        if current and not fits(probe):
            packed.append(current)
            current = [block]
        else:
            current.append(block)
    packed.append(current)

    messages = []
    for at, group in enumerate(packed):
        messages.extend(fit_blocks(group, scanned, head and at == 0,
                                   note if at == 0 else ""))
    return messages


def fit_one(blocks, scanned, head=True, note=""):
    """Один раздел: ужимаем детализацию, а если и это не помогло — режем."""
    for trim in (0, 1, 2):
        text = render_blocks(blocks, scanned, trim, head, note)
        if fits(text):
            if trim and CFG["one_message"]:
                log.info("Сообщение длинное — сократил детализацию (уровень %d)", trim)
            return [(text, flatten(blocks))]

    topic, cards = blocks[0]
    if len(cards) <= 1:
        return [(render_blocks(blocks, scanned, 2, head, note)[:TG_LIMIT], cards)]
    half = max(len(cards) // 2, 1)
    return (fit_one([(topic, cards[:half])], scanned, head, note)
            + fit_one([(topic, cards[half:])], scanned, False))


# ------------------------------------------- совместимость с выпуском одной темы
def render(cards, scanned, trim=0):
    return render_blocks([(None, cards)], scanned, trim)


def fit_message(cards, scanned):
    return fit_blocks([(None, cards)], scanned)


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
    lines.append("<i>Остальное придёт в очередном выпуске.</i>")
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


#: свёрнутый вид: одна строка вместо десятков кнопок
MORE, LESS = "fb:more:x", "fb:less:x"


def data_of(button) -> str:
    """callback_data кнопки. Раскладка может прийти из базы — не доверяем форме."""
    return str(button.get("callback_data") or "") if isinstance(button, dict) else ""


def is_feedback(keyboard) -> bool:
    """True, если это раскладка реакций, а не заявка на подписку."""
    rows = [row for row in (keyboard or []) if row]
    return bool(rows) and all(data_of(b).startswith("fb:")
                              for row in rows for b in row)


def rows_of(keyboard) -> list:
    """Ряды новостей: без служебных «показать/свернуть»."""
    return [row for row in (keyboard or [])
            if row and all(data_of(b) not in (MORE, LESS) for b in row)]


def collapse(keyboard):
    """Сворачивает раскладку в одну кнопку «Оценить новости».

    Сворачивать нечего, если новость всего одна (срочное) или это вообще не
    реакции: один ряд из трёх кнопок глаз не режет.
    """
    news = rows_of(keyboard)
    if len(news) < 2 or not is_feedback(news):
        return keyboard
    return [[{"text": "👍 👎 🔖 Оценить новости (%d)" % len(news),
              "callback_data": MORE}]]


def expand(keyboard, verdicts=None, saved=None):
    """Разворачивает свёрнутое: ряды новостей плюс строка «Свернуть».

    Отметки о нажатом ставим заново из базы: сохранённая раскладка их не
    помнит, а читателю важно видеть, что он уже оценил.
    """
    news = [[dict(button) for button in row if isinstance(button, dict)]
            for row in rows_of(keyboard)]
    news = [row for row in news if row]
    if not news:
        return keyboard             # разворачивать нечего — ничего не трогаем
    for row in news:
        for button in row:
            bare = str(button.get("text") or "").replace(MARK, "")
            button["text"] = bare + MARK if is_pressed(
                data_of(button), verdicts, saved) else bare
    return news + [[{"text": "▲ Свернуть", "callback_data": LESS}]]


def is_pressed(data, verdicts=None, saved=None) -> bool:
    """Нажата ли кнопка сейчас — по тому, что записано в базе."""
    parts = str(data or "").split(":")
    if len(parts) < 3 or parts[0] != "fb":
        return False
    kind, url_hash = parts[1], parts[2]
    if kind == "save":
        return url_hash in (saved or ())
    return (verdicts or {}).get(url_hash) == kind


def for_delivery(keyboard):
    """Что показать под сообщением: свёрнутое или полное — по настройке."""
    if not keyboard or str(CFG["feedback_style"]).lower() != "compact":
        return keyboard
    return collapse(keyboard)


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
