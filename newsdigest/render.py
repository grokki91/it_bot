# -*- coding: utf-8 -*-
"""Сборка текста выпуска и подгонка под лимит Telegram.

Выпуск — это одна лента: шапка с датой и временем суток, а под ней разделы
друг за другом. Один блок без названия — это обычный дайджест по одной теме
(так выглядел выпуск до версии 3.2), один блок с названием — ответ команды
/news, много блоков — плановый выпуск по всем разделам.

Читатель видит выпуск как единое целое, поэтому:

    * новости не нумеруются — сквозная нумерация через разделы сбивала
      с толку («7» под вывеской, где новость всего одна);
    * шапка считает весь выпуск, а не то, что влезло в первое сообщение;
    * не поместившееся уходит следующим сообщением с пометкой «продолжение»,
      чтобы хвост ленты не выглядел новым выпуском.
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

#: время суток выпуска: с какого часа и как это называется. Порядок обратный —
#: берём первое подходящее сверху вниз.
SLOTS = ((23, "🌙", "Ночной выпуск"), (17, "🌆", "Вечерний выпуск"),
         (11, "☀️", "Дневной выпуск"), (5, "🌅", "Утренний выпуск"))

#: пометка в шапке второго и последующих сообщений одного выпуска
CONT = "продолжение"


def esc(text) -> str:
    return html_mod.escape(str(text or ""), quote=False)


def plural(count, one, few, many) -> str:
    """«1 новость», «2 новости», «7 новостей»."""
    tail = abs(int(count)) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def slot(now=None) -> tuple:
    """(эмодзи, название) выпуска по местному времени сборки."""
    hour = (now or local_now()).hour
    for since, icon, name in SLOTS:
        if hour >= since:
            return icon, name
    return SLOTS[0][1], SLOTS[0][2]      # до пяти утра — всё ещё ночь


def today(now=None) -> str:
    """«сегодня, 16 августа» — дата словами и явная отметка «сегодня»."""
    day = now or local_now()
    return "сегодня, %d %s" % (day.day, MONTHS[day.month - 1])


def issue_info(blocks, scanned, note="") -> dict:
    """Паспорт выпуска: считается один раз на весь выпуск.

    Сообщений может быть несколько, но шапка обязана говорить про выпуск
    целиком: иначе «7 новостей · разделов: 4» описывает первое сообщение,
    а следом приходит ещё три раздела — и выглядит это как второй выпуск.
    """
    return {"count": sum(len(cards) for _topic, cards in blocks),
            "sections": len(blocks),
            "topic": blocks[0][0] if len(blocks) == 1 else "",
            "scanned": scanned, "note": note,
            "date": today(), "slot": slot()}


def card_block(card, group, score, category, trim):
    """Одна новость: заголовок, суть, зачем это знать и ссылка."""
    main = primary_of(group)
    title = card.get("headline") or main["title"]
    others = sorted({i["source_id"] for i in group} - {main["source_id"]})[:2]
    also = " · " + esc(", ".join(others)) if others else ""
    link = '🔗 <a href="%s">%s</a>%s · ⭐ %.1f' % (
        esc(main["url"]), esc(main["source_id"]), also, score)
    head = "%s <b>%s</b>" % (EMOJI.get(category, "📌"), esc(title))
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


def counts(info) -> str:
    """«7 новостей · 4 раздела · из 2955 материалов» — вторая строка шапки."""
    count, total = info["count"], info["sections"]
    parts = ["%d %s" % (count, plural(count, "новость", "новости", "новостей"))]
    if total > 1:
        parts.append("%d %s" % (total, plural(total, "раздел", "раздела",
                                              "разделов")))
    parts.append("из %d материалов за сутки" % info["scanned"])
    return " · ".join(parts)


def header(info) -> list:
    """Шапка выпуска: время суток, дата и что внутри.

    У выпуска по одному разделу (ответ /news) в первой строке его название:
    это не «утренний выпуск», а подборка по запросу.
    """
    if info["topic"]:
        first = "%s <b>%s</b> · %s" % (topic_emoji(info["topic"]),
                                       esc(topic_title(info["topic"])),
                                       info["date"])
    else:
        icon, name = info["slot"]
        first = "%s <b>%s</b> · %s" % (icon, esc(name), info["date"])
    lines = [first, "<i>%s</i>" % counts(info)]
    if info["note"]:
        lines.append("<i>%s</i>" % esc(info["note"]))
    return lines + [""]


def cont_header(info) -> list:
    """Шапка продолжения: тот же выпуск, просто не влез в одно сообщение."""
    icon = topic_emoji(info["topic"]) if info["topic"] else info["slot"][0]
    name = topic_title(info["topic"]) if info["topic"] else info["slot"][1]
    return ["%s <i>%s · %s · %s</i>" % (icon, esc(name), info["date"], CONT), ""]


def render_blocks(blocks, info, trim=0, head="full"):
    """trim: 0 — полный вид, 1 — без «почему», 2 — только заголовки со ссылками.

    head: 'full' — полная шапка, 'cont' — пометка продолжения, None — без шапки.
    Названия разделов показываем, когда их в выпуске больше одного: в выпуске
    по одному разделу его имя уже стоит в шапке.
    """
    parts = header(info) if head == "full" else (
        cont_header(info) if head == "cont" else [])
    text = "\n".join(parts)
    chunks = []
    for topic, cards in blocks:
        if topic and info["sections"] > 1:
            chunks.append("%s <b>%s</b>" % (topic_emoji(topic),
                                            esc(topic_title(topic))))
        for card, group, score, category in cards:
            chunks.append(card_block(card, group, score, category, trim))
    return (text + "\n" if text else "") + "\n\n".join(chunks)


def flatten(blocks) -> list:
    return [card for _topic, cards in blocks for card in cards]


def fits(text) -> bool:
    return len(text) <= TG_LIMIT - 60


def fit_blocks(blocks, scanned, head=True, note=""):
    """Возвращает список пар (текст, карточки этого сообщения).

    Карточки нужны вместе с текстом: под каждым сообщением своя клавиатура
    реакций, и подписи кнопок берутся из этих же карточек.

    Подборка по десятку разделов в одно сообщение не влезает никогда. Ужимать
    её до голых заголовков — значит выбросить то, ради чего дайджест и нужен,
    поэтому режем по разделам: лучше три сообщения с сутью, чем одно из
    ссылок. Внутри одного раздела наоборот: сначала ужимаем, режем в крайнем.
    """
    blocks = [(topic, cards) for topic, cards in blocks if cards]
    if not blocks:
        return []
    info = issue_info(blocks, scanned, note)
    return number_parts(pack(blocks, info, "full" if head else None))


def pack(blocks, info, head="full"):
    """Раскладывает разделы по сообщениям, сохраняя их порядок."""
    if len(blocks) == 1:
        return fit_one(blocks, info, head)

    text = render_blocks(blocks, info, 0, head)
    if fits(text):
        return [(text, flatten(blocks))]

    packed, current = [], []
    for block in blocks:
        probe = render_blocks(current + [block], info, 0,
                              head if not packed else "cont")
        if current and not fits(probe):
            packed.append(current)
            current = [block]
        else:
            current.append(block)
    packed.append(current)

    messages = []
    for at, group in enumerate(packed):
        messages.extend(fit_one(group, info, head if at == 0 else "cont"))
    return messages


def fit_one(blocks, info, head="full"):
    """Одно сообщение: ужимаем детализацию, а если и это не помогло — режем."""
    for trim in (0, 1, 2):
        text = render_blocks(blocks, info, trim, head)
        if fits(text):
            if trim and CFG["one_message"]:
                log.info("Сообщение длинное — сократил детализацию (уровень %d)", trim)
            return [(text, flatten(blocks))]

    topic, cards = blocks[0]
    if len(cards) <= 1:
        return [(render_blocks(blocks, info, 2, head)[:TG_LIMIT], cards)]
    half = max(len(cards) // 2, 1)
    return (fit_one([(topic, cards[:half])], info, head)
            + fit_one([(topic, cards[half:])], info, "cont"))


def number_parts(messages):
    """Дописывает в шапку продолжения «2 из 3».

    Сколько всего будет частей, известно только когда выпуск уже нарезан,
    поэтому номер проставляется последним шагом — по готовым сообщениям.
    """
    total = len(messages)
    if total < 2:
        return messages
    out = []
    for at, (text, cards) in enumerate(messages, 1):
        head, sep, rest = text.partition("\n")
        if at > 1 and head.endswith(CONT + "</i>"):
            numbered = "%s %d из %d</i>" % (head[:-len("</i>")], at, total)
            if len(numbered) + len(sep) + len(rest) <= TG_LIMIT:
                text = numbered + sep + rest
        out.append((text, cards))
    return out


# ------------------------------------------- совместимость с выпуском одной темы
def render(cards, scanned, trim=0):
    blocks = [(None, cards)]
    return render_blocks(blocks, issue_info(blocks, scanned), trim)


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


def unmarked(text) -> str:
    """Подпись кнопки без отметки о нажатии. Снимаем только хвостовую: в
    подписи теперь стоит заголовок новости, и «✓» может встретиться в нём."""
    text = str(text or "")
    return text[:-len(MARK)] if text.endswith(MARK) else text


#: сколько букв заголовка влезает в кнопку, не разъезжаясь на телефоне
LABEL = 18


def short(text, limit=LABEL) -> str:
    """Начало заголовка для подписи кнопки: режем по слову, а не по букве."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(" ,.:;—-·") + "…"


def feedback_keyboard(cards):
    """Ряд кнопок на каждую новость: «👍 Заголовок», «👎», «🔖».

    Номеров в выпуске больше нет, поэтому ряд подписан началом заголовка —
    иначе непонятно, какую именно новость оцениваешь. Одна новость (срочное)
    подписи не требует: она в сообщении единственная.

    В callback_data влезает только 64 байта, поэтому кладём туда хэш ссылки —
    по нему потом находятся и заголовок, и источник.
    """
    if not CFG["feedback_buttons"]:
        return None
    keyboard = []
    for card, group, _score, _cat in cards:
        main = primary_of(group)
        row = [{"text": icon,
                "callback_data": "fb:%s:%s" % (kind, main["url_hash"])}
               for kind, icon in BUTTONS]
        if len(cards) > 1:
            row[0]["text"] += " " + short(card.get("headline") or main["title"])
        keyboard.append(row)
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
            bare = unmarked(button.get("text"))
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
            bare = unmarked(button.get("text"))
            if other == data:
                button["text"] = bare + MARK if pressed else bare
            elif kind in (feedback.UP, feedback.DOWN) and other_kind in (
                    feedback.UP, feedback.DOWN):
                button["text"] = bare
    return keyboard
