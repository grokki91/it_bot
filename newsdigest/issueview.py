# -*- coding: utf-8 -*-
"""Выпуск в Telegram: оглавление, экраны разделов и переходы между ними.

Выпуск на полтора десятка разделов — это простыня в три-четыре сообщения.
Чтобы добраться до «Науки», её приходилось пролистывать целиком, а четыре
строки статистики в шапке («23 новости · 13 разделов · из 3775 материалов за
сутки · без новостей: Роботы») занимали первый экран вместо самих новостей.

Поэтому выпуск приходит одним сообщением-оглавлением: время суток, дата,
сколько новостей — и всё; дальше главное за день и кнопки разделов. Нажатие
правит то же самое сообщение (editMessageText), а не присылает новое, поэтому
чат не растёт: читатель ходит «разделы → ИИ и технологии → назад» внутри
одного сообщения, как по экранам приложения.

Экран описывается маршрутом в callback_data: `nav:<выпуск>:<экран>[:<раздел>]`.

    home           оглавление: главное за день и разделы
    top / secs     то же, но с полным списком главного / разделов
    sec:<раздел>   раздел: первые новости и кнопки реакций
    all:<раздел>   он же целиком

Сам выпуск в кнопку не влезает (64 байта на всё), поэтому он лежит в базе,
а в кнопке едет только его номер. Выпуск, которого в базе уже нет (старый,
вычищенный), листаться перестаёт — сообщение при этом остаётся читаемым.
"""
from __future__ import annotations

from .config import CFG
from .profiles import emoji as topic_emoji
from .profiles import title as topic_title
from .render import (BUTTONS, MARK, card_facts, card_text, esc, fits,
                     is_pressed, plural, short)
from .telegram import TG_LIMIT

#: главных новостей на первом экране и сколько их всего под кнопкой «ещё»
TOP_SHOWN, TOP_MAX = 3, 7
#: разделов в оглавлении, пока не нажали «остальные»
SECTIONS_SHOWN = 6
#: новостей на экране раздела, пока не нажали «ещё»
SECTION_SHOWN = 5
#: длина сути в оглавлении: строка-другая, дальше — в разделе
SENTENCE = 120

NAV = "nav"
HOME, TOP, SECS, SEC, ALL = "home", "top", "secs", "sec", "all"


# ------------------------------------------------------------------- выпуск
def snapshot(blocks, info) -> dict:
    """Выпуск, разложенный по разделам, — то, из чего собираются экраны.

    Складывается один раз при отправке и ложится в базу: через час, когда
    читатель нажмёт «Наука», ни кластеров, ни ответа модели уже нет.
    """
    return {"day": info["day"], "slot": list(info["slot"]),
            "count": info["count"], "note": info.get("note") or "",
            "sections": [{"topic": topic or "",
                          "cards": [card_facts(card, group, score)
                                    for card, group, score, _cat in cards]}
                         for topic, cards in blocks if cards]}


def sections_of(issue) -> list:
    return list(issue.get("sections") or ())


def section_of(issue, topic):
    """Раздел выпуска по имени. Нет такого — None."""
    for block in sections_of(issue):
        if block.get("topic", "") == (topic or ""):
            return block
    return None


def cards_of(block) -> list:
    return list((block or {}).get("cards") or ())


def top_cards(issue, limit=TOP_MAX) -> list:
    """Главное за день: самые высокие оценки со всего выпуска.

    Порядок разделов сохраняется только внутри раздела — оглавлению нужен
    другой срез: что случилось важного, независимо от того, под какой
    вывеской оно лежит.
    """
    cards = [card for block in sections_of(issue) for card in cards_of(block)]
    return sorted(cards, key=lambda c: -float(c.get("score") or 0))[:limit]


def label(topic) -> str:
    """«🤖 ИИ и технологии» — подпись кнопки раздела."""
    return "%s %s" % (topic_emoji(topic), topic_title(topic)) if topic else "Выпуск"


def route(ident, name=HOME, arg="") -> str:
    """Маршрут экрана в callback_data. Длиннее 64 байт не бывает: номер
    выпуска — число, имя раздела — латиница из profiles."""
    return ":".join(part for part in (NAV, str(ident), name, str(arg or ""))
                    if part != "")


def parse(data) -> tuple:
    """'nav:12:sec:ai' -> (12, 'sec', 'ai'). Чужой маршрут -> (0, '', '')."""
    parts = str(data or "").split(":")
    if len(parts) < 3 or parts[0] != NAV or not parts[1].isdigit():
        return 0, "", ""
    return int(parts[1]), parts[2], (parts[3] if len(parts) > 3 else "")


# --------------------------------------------------------------- оглавление
def sentence(text, limit=SENTENCE) -> str:
    """Первое предложение сути: оглавлению хватает строки, остальное — в разделе."""
    text = " ".join(str(text or "").split())
    head = text.split(". ")[0].strip()
    if head and head != text and len(head) < limit:
        return head + "."
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.:;—-·") + "…"


def hub_head(issue) -> list:
    """Шапка оглавления. Всё, что было в ней раньше — сколько разделов, сколько
    материалов просмотрено, где сегодня пусто, — ушло: это про работу бота, а
    не про новости. Кому нужно — `/status` и страница в браузере."""
    icon, name = (list(issue.get("slot") or ()) + ["📰", "Выпуск"])[:2]
    count = int(issue.get("count") or 0)
    return ["%s <b>%s</b>" % (esc(icon), esc(str(name).upper())),
            "<i>%s · %d %s</i>" % (esc(issue.get("day")), count,
                                   plural(count, "новость", "новости",
                                          "новостей")),
            ""]


def top_entry(at, card) -> str:
    """Главная новость в оглавлении: номер, заголовок, строка сути и источник."""
    lines = ["<b>%d. %s</b>" % (at, esc(card["title"]))]
    what = sentence(card.get("what"))
    if what:
        lines.append(esc(what))
    lines.append('🔗 <a href="%s">%s</a> · ⭐ %.1f'
                 % (esc(card["url"]), esc(card["source"]),
                    float(card.get("score") or 0)))
    return "\n".join(lines)


def hub_text(issue, want=TOP_SHOWN, note=False) -> tuple:
    """Текст оглавления и сколько главных новостей в него поместилось.

    note — дописать ли строку о разделах, где сегодня пусто. В шапке ей не
    место (она про работу бота, а не про новости), но на экране со списком
    разделов она нужна: иначе непонятно, бот пропустил раздел или там правда
    тихо.
    """
    top = top_cards(issue)
    head = hub_head(issue)
    tail = ("\n\n<i>%s</i>" % esc(issue.get("note"))
            if note and issue.get("note") else "")

    def build(take):
        if not take:
            return "\n".join(head).rstrip() + tail
        parts = head + ["<b>ГЛАВНОЕ СЕГОДНЯ</b>", ""]
        return "\n".join(parts) + "\n\n".join(
            top_entry(at, card) for at, card in enumerate(top[:take], 1)) + tail

    take = max(0, min(want, len(top)))
    while take > 1:
        text = build(take)
        if fits(text):
            return text, take
        take -= 1
    return build(take)[:TG_LIMIT], take


def hub_keyboard(issue, ident, shown, wide=False) -> list:
    """Кнопки оглавления: «ещё главное», разделы, «остальные разделы»."""
    rows = []
    left = len(top_cards(issue)) - shown
    if left > 0:
        rows.append([{"text": "⬇️ Ещё %d %s" % (left, plural(
            left, "главная новость", "главные новости", "главных новостей")),
            "callback_data": route(ident, TOP)}])
    elif shown > TOP_SHOWN:
        rows.append([{"text": "⬆️ Свернуть главное",
                      "callback_data": route(ident, HOME)}])

    blocks = sections_of(issue)
    # ради одного спрятанного раздела кнопку «остальные» не заводим: она
    # занимает ровно ту же строку, что и сам раздел
    limit = len(blocks) if wide or len(blocks) <= SECTIONS_SHOWN + 1 \
        else SECTIONS_SHOWN
    for block in blocks[:limit]:
        rows.append([{"text": "%s · %d" % (label(block["topic"]),
                                           len(cards_of(block))),
                      "callback_data": route(ident, SEC, block["topic"])}])
    rest = len(blocks) - limit
    if rest > 0:
        rows.append([{"text": "☰ Остальные %d %s" % (rest, plural(
            rest, "раздел", "раздела", "разделов")),
            "callback_data": route(ident, SECS)}])
    elif wide and len(blocks) > SECTIONS_SHOWN:
        rows.append([{"text": "⬆️ Свернуть разделы",
                      "callback_data": route(ident, HOME)}])
    # порядок разделов читатель правит отсюда: в чате команд нет, а место,
    # где на этот порядок смотрят, — ровно это оглавление
    from .prefsview import entry              # prefsview знает про нас — тут
    rows.append(entry(ident))
    return rows


def hub_screen(issue, ident, name=HOME) -> tuple:
    text, shown = hub_text(issue, TOP_MAX if name == TOP else TOP_SHOWN,
                           note=(name == SECS))
    return text, hub_keyboard(issue, ident, shown, wide=(name == SECS))


# ------------------------------------------------------------------- раздел
def section_head(issue, block) -> list:
    count = len(cards_of(block))
    topic = block.get("topic", "")
    name = topic_title(topic) if topic else str(
        (list(issue.get("slot") or ()) + ["Выпуск"])[-1])
    return ["%s <b>%s</b>" % (esc(topic_emoji(topic)), esc(name)),
            "<i>%d %s · %s</i>" % (count, plural(count, "новость", "новости",
                                                 "новостей"),
                                   esc(issue.get("day"))),
            ""]


def section_text(issue, block, want=SECTION_SHOWN, note=False) -> tuple:
    """Текст раздела и сколько новостей в него поместилось.

    Сначала ужимаем детализацию (как в сплошной ленте), и только если раздел
    не влезает и голыми заголовками — показываем меньше новостей: остальные
    остаются под кнопкой «ещё», а не пропадают.
    """
    cards = cards_of(block)
    head = "\n".join(section_head(issue, block))
    tail = ("\n\n<i>%s</i>" % esc(issue.get("note"))
            if note and issue.get("note") else "")

    def build(take, trim):
        return head + "\n" + "\n\n".join(
            card_text(card, trim, when=True) for card in cards[:take]) + tail

    take = max(1, min(want, len(cards)))
    for trim in (0, 1, 2):
        text = build(take, trim)
        if fits(text):
            return text, take
    while take > 1:
        take -= 1
        text = build(take, 2)
        if fits(text):
            return text, take
    return build(1, 2)[:TG_LIMIT], 1


def section_keyboard(issue, ident, block, shown, verdicts=None, saved=None) -> list:
    """Кнопки раздела: реакции на показанные новости, «ещё» и «к разделам»."""
    cards = cards_of(block)
    rows = []
    if CFG["feedback_buttons"]:
        for card in cards[:shown]:
            row = [{"text": icon,
                    "callback_data": "fb:%s:%s" % (kind, card["hash"])}
                   for kind, icon in BUTTONS]
            if shown > 1:
                row[0]["text"] += " " + short(card["title"])
            for button in row:
                if is_pressed(button["callback_data"], verdicts, saved):
                    button["text"] += MARK
            rows.append(row)
    left = len(cards) - shown
    if left > 0:
        rows.append([{"text": "⬇️ Ещё %d %s" % (left, plural(
            left, "новость", "новости", "новостей")),
            "callback_data": route(ident, ALL, block["topic"])}])
    if len(sections_of(issue)) > 1:
        rows.append([{"text": "← К разделам", "callback_data": route(ident, HOME)}])
    return rows


def section_screen(issue, ident, topic, full=False, verdicts=None, saved=None):
    block = section_of(issue, topic)
    if block is None:                   # раздела нет — показываем оглавление
        return hub_screen(issue, ident)
    want = len(cards_of(block)) if full else SECTION_SHOWN
    # выпуску из одного раздела оглавления не досталось — строку о пустых
    # разделах, кроме как здесь, показать негде
    text, shown = section_text(issue, block, want,
                               note=len(sections_of(issue)) == 1)
    return text, section_keyboard(issue, ident, block, shown, verdicts, saved)


# ------------------------------------------------------------------- маршрут
def screen(issue, ident, name=HOME, arg="", verdicts=None, saved=None) -> tuple:
    """Экран выпуска: текст сообщения и кнопки под ним.

    Выпуск из одного раздела (ответ `/news`) оглавления не получает: листать
    в нём нечего, и читатель сразу видит новости.
    """
    blocks = sections_of(issue)
    if not blocks:
        return hub_text(issue, 0)[0], []
    if len(blocks) == 1 and name in (HOME, TOP, SECS):
        name, arg = SEC, blocks[0]["topic"]
    if name in (SEC, ALL):
        return section_screen(issue, ident, arg, name == ALL, verdicts, saved)
    return hub_screen(issue, ident, name)


def screens(issue, ident=0) -> list:
    """Все экраны выпуска подряд: [(имя, текст)] — для `--dry-run` в терминале.

    Разделы печатаются целиком, а оглавление — со строкой о пустых разделах:
    в чате она ждёт за кнопкой «остальные разделы», а в терминале кнопок нет.
    """
    blocks = sections_of(issue)
    full = [(label(block["topic"]), screen(issue, ident, ALL, block["topic"])[0])
            for block in blocks]
    if len(blocks) < 2:
        return full
    return [("оглавление", hub_text(issue, TOP_SHOWN, note=True)[0])] + full
