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
    sec:<раздел>   раздел: первые новости
    all:<раздел>   он же целиком
    share:<экран>  «поделиться» на любом из экранов выше: текст тот же, а
                   вместо кнопок — его новости, нажал — выбрал чат.
                   `share` — с оглавления, `share:top`, `share:sec:ai`…

Сам выпуск в кнопку не влезает (64 байта на всё), поэтому он лежит в базе,
а в кнопке едет только его номер. Выпуск, которого в базе уже нет (старый,
вычищенный), листаться перестаёт — сообщение при этом остаётся читаемым.

Кнопок 👍/👎/🔖 под новостями здесь нет. Ряд из трёх кнопок на каждую новость
превращал экран раздела в пульт — пять новостей, пятнадцать кнопок, — и под
ним терялись переходы, ради которых кнопки и нужны. Оценивают и откладывают
на странице, а в Telegram выпуск читают.
"""
from __future__ import annotations

from urllib.parse import quote

from .profiles import emoji as topic_emoji
from .profiles import short as topic_short
from .profiles import title as topic_title
from .render import card_facts, card_text, esc, fits, plural, short
from .telegram import TG_LIMIT

#: главных новостей на первом экране и сколько их всего под кнопкой «ещё».
#: Пять — столько читают с первого экрана, не листая; остальное главное
#: раскрывается кнопкой и не теряется
TOP_SHOWN, TOP_MAX = 5, 10
#: разделов в оглавлении, пока не нажали «все разделы»
SECTIONS_SHOWN = 6
#: кнопок разделов в ряду. Столбик кнопок во всю ширину вытягивал оглавление
#: в девять одинаковых полос — длиннее самих новостей над ним. В три ряда
#: имена уже не влезают, в два — влезают целиком (profiles.short)
SECTIONS_ROW = 2
#: новостей на экране раздела, пока не нажали «ещё»
SECTION_SHOWN = 5
#: длина сути в оглавлении: строка-другая, дальше — в разделе
SENTENCE = 120

NAV = "nav"
HOME, TOP, SECS, SEC, ALL = "home", "top", "secs", "sec", "all"
SHARE = "share"
#: ссылка, по которой Telegram сам открывает выбор чата для пересылки
SHARE_URL = "https://t.me/share/url?url=%s&text=%s"
#: подпись новости в пересылке: заголовок и строка сути, не простыня
SHARE_TEXT = 200
#: короче заголовок в пересылке не режем — лучше одна ссылка без подписи
SHARE_MIN = 20
#: сколько байт ссылок на всё экран «поделиться». Telegram отвергает
#: слишком тяжёлую разметку (REPLY_MARKUP_TOO_LONG) и экран не открывается,
#: а кириллица в ссылке раздувается в шесть раз: «%D0%9F» на букву. Десять
#: подписей по 200 букв — это больше 12 КБ, поэтому подпись ужимается так,
#: чтобы все ссылки вместе уложились в бюджет
SHARE_BUDGET = 4000
#: подпись кнопки с новостью на экране «поделиться»: столько букв видно на
#: кнопке во всю ширину телефона. Длиннее — и Telegram обрывает заголовок
#: на полуслове («обходят защит»), а так он кончается целым словом и «…»
SHARE_LABEL = 34
#: всплывашка при открытии «поделиться». Объяснение живёт в ней, а не в
#: сообщении: текст остаётся тем, что читатель только что читал
SHARE_HINT = "Выберите новость — Telegram спросит, в какой чат её отправить"


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
    """«🤖 ИИ и технологии» — полное имя раздела со значком."""
    return "%s %s" % (topic_emoji(topic), topic_title(topic)) if topic else "Выпуск"


def route(ident, name=HOME, arg="") -> str:
    """Маршрут экрана в callback_data. Длиннее 64 байт не бывает: номер
    выпуска — число, имя раздела — латиница из profiles."""
    return ":".join(part for part in (NAV, str(ident), name, str(arg or ""))
                    if part != "")


def parse(data) -> tuple:
    """'nav:12:sec:ai' -> (12, 'sec', 'ai'). Чужой маршрут -> (0, '', '').

    Всё, что после экрана, — его аргумент целиком: у «поделиться» это
    маршрут экрана, с которого пришли ('nav:12:share:sec:ai' -> 'sec:ai').
    """
    parts = str(data or "").split(":")
    if len(parts) < 3 or parts[0] != NAV or not parts[1].isdigit():
        return 0, "", ""
    return int(parts[1]), parts[2], ":".join(parts[3:])


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


def top_entry(card) -> str:
    """Главная новость в оглавлении: заголовок, строка сути и источник.

    Без номера: «1.», «5.» перед жирным заголовком делали оглавление похожим
    на анкету, а порядок и так виден — новости идут по убыванию важности.
    """
    lines = ["<b>%s</b>" % esc(card["title"])]
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
            top_entry(card) for card in top[:take]) + tail

    take = max(0, min(want, len(top)))
    while take > 1:
        text = build(take)
        if fits(text):
            return text, take
        take -= 1
    return build(take)[:TG_LIMIT], take


def hub_view(issue, name=HOME) -> tuple:
    """Текст оглавления и главные новости, которые в него поместились."""
    text, shown = hub_text(issue, TOP_MAX if name == TOP else TOP_SHOWN,
                           note=(name == SECS))
    return text, top_cards(issue)[:shown]


def section_button(ident, topic) -> dict:
    """Кнопка раздела: значок и короткое имя, без числа новостей.

    «· 2» на каждой кнопке — это столбик цифр, которые ничего не решают:
    сколько новостей в разделе, видно, когда его откроешь. Имя короткое
    (profiles.short): в пол-экрана «Компьютерное железо» не помещается.
    """
    return {"text": "%s %s" % (topic_emoji(topic), topic_short(topic)),
            "callback_data": route(ident, SEC, topic)}


def grid(buttons, size=SECTIONS_ROW) -> list:
    """Кнопки рядами по `size`: разделы стоят сеткой, а не столбиком."""
    return [buttons[at:at + size] for at in range(0, len(buttons), size)]


def folded(blocks) -> bool:
    """Прячет ли оглавление часть разделов за «☰ Все разделы».

    Кнопка сама занимает целый ряд, поэтому прятать стоит, только если это
    экономит ряды: восемь разделов — те же четыре ряда, что шесть и «☰».
    """
    def height(count):
        return -(-count // SECTIONS_ROW)
    return height(len(blocks)) > height(SECTIONS_SHOWN) + 1


def hub_keyboard(issue, ident, shown, name=HOME) -> list:
    """Кнопки оглавления: «ещё главное», сетка разделов, «все разделы» и
    нижний ряд — «Мои темы» и «Поделиться».

    Разделы стоят по два в ряд, служебные кнопки — своими рядами сверху и
    снизу: так сетка читается как меню, а не как девять одинаковых полос.
    """
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
    wide, hidden = name == SECS, folded(blocks)
    limit = SECTIONS_SHOWN if hidden and not wide else len(blocks)
    rows += grid([section_button(ident, block["topic"])
                  for block in blocks[:limit]])
    if hidden:
        rows.append([{"text": "⬆️ Свернуть разделы",
                      "callback_data": route(ident, HOME)} if wide else
                     {"text": "☰ Все разделы",
                      "callback_data": route(ident, SECS)}])
    # порядок разделов читатель правит отсюда: в чате команд нет, а место,
    # где на этот порядок смотрят, — ровно это оглавление
    from .prefsview import entry              # prefsview знает про нас — тут
    last = entry(ident)
    if share_cards(top_cards(issue)[:shown]):
        last.append(share_button(ident, name))
    rows.append(last)
    return rows


def hub_screen(issue, ident, name=HOME) -> tuple:
    if name not in (HOME, TOP, SECS):
        name = HOME
    text, cards = hub_view(issue, name)
    return text, hub_keyboard(issue, ident, len(cards), name)


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


def section_keyboard(issue, ident, block, shown, full=False) -> list:
    """Кнопки раздела: «ещё», «к разделам» и «поделиться»."""
    cards = cards_of(block)
    rows = []
    left = len(cards) - shown
    if left > 0:
        rows.append([{"text": "⬇️ Ещё %d %s" % (left, plural(
            left, "новость", "новости", "новостей")),
            "callback_data": route(ident, ALL, block["topic"])}])
    last = []
    if len(sections_of(issue)) > 1:
        last.append({"text": "← К разделам", "callback_data": route(ident, HOME)})
    if share_cards(cards[:shown]):
        last.append(share_button(ident, ALL if full else SEC, block["topic"]))
    if last:
        rows.append(last)
    return rows


def section_view(issue, block, full=False) -> tuple:
    """Текст раздела и новости, которые в него поместились."""
    cards = cards_of(block)
    # выпуску из одного раздела оглавления не досталось — строку о пустых
    # разделах, кроме как здесь, показать негде
    text, shown = section_text(issue, block,
                               len(cards) if full else SECTION_SHOWN,
                               note=len(sections_of(issue)) == 1)
    return text, cards[:shown]


def section_screen(issue, ident, topic, full=False):
    block = section_of(issue, topic)
    if block is None:                   # раздела нет — показываем оглавление
        return hub_screen(issue, ident)
    text, cards = section_view(issue, block, full)
    return text, section_keyboard(issue, ident, block, len(cards), full)


# ---------------------------------------------------------------- поделиться
def share_button(ident, name=HOME, topic="") -> dict:
    """«📤 Поделиться» — одна кнопка на экран. В маршруте едет сам экран:
    список предложит ровно те новости, что на нём видны, а «Отмена»
    вернёт туда же."""
    origin = "" if name == HOME else ":".join(
        part for part in (name, topic) if part)
    return {"text": "📤 Поделиться", "callback_data": route(ident, SHARE, origin)}


def share_origin(issue, arg) -> tuple:
    """С какого экрана нажали «поделиться»: (экран, раздел).

    Кнопки выпусков, разосланных раньше, несут только раздел (`share:ai`)
    или ничего (`share`) — это экран раздела и оглавление.
    """
    name, _sep, topic = str(arg or "").partition(":")
    if name not in (HOME, TOP, SECS, SEC, ALL):
        name, topic = (SEC, str(arg)) if arg else (HOME, "")
    if name in (SEC, ALL) and section_of(issue, topic) is None:
        name, topic = HOME, ""
    blocks = sections_of(issue)
    if name in (HOME, TOP, SECS) and len(blocks) == 1:
        name, topic = SEC, blocks[0]["topic"]    # оглавления у такого нет
    return name, topic


def share_cards(cards) -> list:
    """Новости, которыми есть чем поделиться: с настоящей ссылкой."""
    return [card for card in cards
            if str(card.get("url") or "").startswith(("http://", "https://"))]


def share_texts(card, lead=True):
    """Подписи новости для пересылки — от полной к самой короткой.

    Полная — заголовок и строка сути отдельным абзацем. Суть идёт целиком
    или никак: «…в Австралии — В…» в чужом чате выглядит поломкой, а не
    экономией места. Дальше — один заголовок, а не влезает и он — он же,
    укороченный по словам. lead=False — без сути, только заголовок.
    """
    title = " ".join(str(card.get("title") or "").split())
    what = sentence(card.get("what")) if lead else ""
    if title and what and not what.endswith("…") \
            and len(title) + len(what) + 2 <= SHARE_TEXT:
        yield "%s\n\n%s" % (title, what)
    limit = SHARE_TEXT
    while title and limit >= SHARE_MIN:
        yield short(title, limit)
        limit = min(len(title), limit) * 3 // 4


def share_link(card, budget=0, lead=True) -> str:
    """Ссылка «переслать в чат»: Telegram сам покажет список чатов.

    Пересылается не сообщение выпуска целиком (в нём десяток новостей), а
    одна новость: ссылка на первоисточник и под ней подпись (share_texts).
    `budget` — предел длины ссылки в байтах: берётся самая полная подпись,
    которая в него влезает, а не влезает никакая — остаётся одна ссылка,
    превью статьи Telegram покажет сам.
    """
    url = quote(card["url"], safe="")
    for text in share_texts(card, lead):
        link = SHARE_URL % (url, quote(text, safe=""))
        if not budget or len(link) <= budget:
            return link
    return (SHARE_URL % (url, "")).rsplit("&text=", 1)[0]


def share_links(cards, budget=SHARE_BUDGET) -> list:
    """Ссылки для всех кнопок экрана — вместе не длиннее `budget` байт.

    Сначала каждой новости — ровная доля бюджета. Короткому заголовку она
    велика, длинному или с сутью — мала, поэтому недобранное раздаётся
    сверху вниз, с главной новости: сперва — вернуть целиком укороченные
    заголовки, и только потом — на строку сути.
    """
    links = [share_link(card, budget // max(len(cards), 1)) for card in cards]
    for lead in (False, True):
        spare = budget - sum(len(link) for link in links)
        for at, card in enumerate(cards):
            fuller = share_link(card, len(links[at]) + max(spare, 0), lead)
            if len(fuller) > len(links[at]):
                spare -= len(fuller) - len(links[at])
                links[at] = fuller
    return links


def share_screen(issue, ident, arg="") -> tuple:
    """«Поделиться»: тот же экран, но вместо его кнопок — его новости.

    Отдельный экран с шапкой-инструкцией и десятью пронумерованными кнопками
    во всю ширину выходил длиннее самого выпуска. Теперь текст остаётся тем,
    что читатель только что читал, а кнопками под ним становятся ровно те
    новости, что в нём видны: пять главных — пять кнопок. Кнопка новости —
    ссылка t.me/share: нажал — и Telegram сразу открывает выбор чата, лишнего
    шага через бота нет. Что делать, подсказывает всплывашка (`hint`),
    «Отмена» возвращает прежние кнопки.
    """
    name, topic = share_origin(issue, arg)
    if name in (SEC, ALL):
        text, cards = section_view(issue, section_of(issue, topic), name == ALL)
    else:
        text, cards = hub_view(issue, name)
    cards = share_cards(cards)
    rows = [[{"text": short(card["title"], SHARE_LABEL), "url": link}]
            for card, link in zip(cards, share_links(cards))]
    rows.append([{"text": "✖️ Отмена", "callback_data": route(ident, name, topic)}])
    return text, rows


def hint(name, keyboard) -> str:
    """Всплывашка при открытии экрана: подсказка есть только у «поделиться»."""
    if name != SHARE:
        return ""
    if any("url" in button for row in keyboard for button in row):
        return SHARE_HINT
    return "Здесь нечем поделиться."


# ------------------------------------------------------------------- маршрут
def screen(issue, ident, name=HOME, arg="") -> tuple:
    """Экран выпуска: текст сообщения и кнопки под ним.

    Выпуск из одного раздела (ответ `/news`) оглавления не получает: листать
    в нём нечего, и читатель сразу видит новости.
    """
    blocks = sections_of(issue)
    if not blocks:
        return hub_text(issue, 0)[0], []
    if name == SHARE:
        return share_screen(issue, ident, arg)
    if len(blocks) == 1 and name in (HOME, TOP, SECS):
        name, arg = SEC, blocks[0]["topic"]
    if name in (SEC, ALL):
        return section_screen(issue, ident, arg, name == ALL)
    return hub_screen(issue, ident, name)


def screens(issue, ident=0) -> list:
    """Все экраны выпуска подряд: [(имя, текст)] — для `--dry-run` в терминале.

    Разделы печатаются целиком, а оглавление — со строкой о пустых разделах:
    в чате она ждёт за кнопкой «☰ Все разделы», а в терминале кнопок нет.
    """
    blocks = sections_of(issue)
    full = [(label(block["topic"]), screen(issue, ident, ALL, block["topic"])[0])
            for block in blocks]
    if len(blocks) < 2:
        return full
    return [("оглавление", hub_text(issue, TOP_SHOWN, note=True)[0])] + full
