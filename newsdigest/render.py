# -*- coding: utf-8 -*-
"""Сборка текста выпуска и подгонка под лимит Telegram.

Здесь живут карточка новости и сплошная лента (`ND_TG_VIEW=feed`). По
умолчанию выпуск уходит экранами — оглавление и разделы по кнопкам, — их
собирает issueview.py из тех же карточек.

Сплошная лента читается как одно целое: шапка с датой и временем суток, а под
ней разделы друг за другом. Один блок без названия — это обычный дайджест по
одной теме (так выглядел выпуск до версии 3.2), один блок с названием — ответ
команды /news, много блоков — плановый выпуск по всем разделам.

Читатель видит выпуск как единое целое, поэтому:

    * новости не нумеруются — сквозная нумерация через разделы сбивала
      с толку («7» под вывеской, где новость всего одна);
    * заголовок новости не помечается значком: значок категории повторял то,
      что и так написано в вывеске раздела, а жирного шрифта хватает, чтобы
      увидеть, где начинается следующая новость;
    * шапка считает весь выпуск, а не то, что влезло в первое сообщение;
    * не поместившееся уходит следующим сообщением с пометкой «продолжение»,
      чтобы хвост ленты не выглядел новым выпуском.
"""
from __future__ import annotations

import html as html_mod

from . import factcheck
from .config import CFG, local_now, log, to_local
from .feedparse import parse_date
from .profiles import emoji as topic_emoji
from .profiles import title as topic_title
from .rank import primary_of
from .telegram import TG_LIMIT

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


def day(now=None) -> str:
    """«16 августа» — дата словами."""
    when = now or local_now()
    return "%d %s" % (when.day, MONTHS[when.month - 1])


def today(now=None) -> str:
    """«сегодня, 16 августа» — дата словами и явная отметка «сегодня»."""
    return "сегодня, " + day(now)


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
            "date": today(), "day": day(), "slot": slot()}


def stamp(published) -> str:
    """«20:48» — время новости в поясе читателя. Пусто, если даты в фиде нет."""
    when = parse_date(str(published or ""))
    return to_local(when).strftime("%H:%M") if when else ""


#: сколько букв «Ранее по теме» оставляем в сообщении. Строка тут напоминает,
#: а не пересказывает: длинный хвост съел бы место у самих новостей
EARLIER = 70


def card_facts(card, group, score) -> dict:
    """Карточка простыми полями — всё, что читатель о новости увидит.

    Тройка (карточка, кластер, оценка) живёт ровно столько, сколько идёт
    сборка выпуска. Экранам выпуска карточка нужна и через час — когда
    читатель нажмёт «ИИ и технологии», — поэтому нужное складывается в
    словарь: он переживает и запись в базу, и перезапуск бота.
    """
    main = primary_of(group)
    return {"hash": main["url_hash"],
            "title": str(card.get("headline") or main["title"]),
            "what": str(card.get("what") or main["summary"][:300]).strip(),
            "why": str(card.get("why") or "").strip(),
            "url": main["url"], "source": main["source_id"],
            "also": sorted({i["source_id"] for i in group}
                           - {main["source_id"]})[:2],
            "score": float(score), "at": stamp(main.get("published_at")),
            # чем новость продолжает уже прочитанное (newsdigest/threads.py).
            # Один шаг назад, не вся цепочка: место в сообщении считанное,
            # а цепочка целиком есть на странице
            "earlier": str(card.get("earlier") or "").strip(),
            # оговорка фактчека: показывается вместе с новостью, а не вместо
            # неё. «Препринт, без рецензирования» — это то, что читатель
            # должен знать, чтобы прочесть заголовок правильно
            "caveat": factcheck.caveat_of(group)}


def card_text(facts, trim=0, when=False) -> str:
    """Одна новость: заголовок, суть, зачем это знать и ссылка.

    when — дописывать ли время новости. На экране раздела оно к месту (видно,
    насколько свежее), в сплошной ленте только удлиняет строку.
    """
    also = " · " + esc(", ".join(facts.get("also") or ())) if facts.get("also") else ""
    link = '🔗 <a href="%s">%s</a>%s · ⭐ %.1f' % (
        esc(facts["url"]), esc(facts["source"]), also, facts["score"])
    if when and facts.get("at"):
        link += " · " + esc(facts["at"])
    head = "<b>%s</b>" % esc(facts["title"])
    if trim >= 2:
        return "%s\n%s" % (head, link)
    lines = [head]
    if facts["what"]:
        lines.append(esc(facts["what"]))
    if facts.get("caveat"):
        # оговорка идёт ВЫШЕ «зачем это знать» и не срезается вместе с ним:
        # если места хватило на суть новости, хватит и на «это препринт»
        lines.append("⚠️ " + esc(facts["caveat"]))
    if facts["why"] and trim == 0:
        lines.append("💡 " + esc(facts["why"]))
    # сюжет — первое, что срезается при нехватке места: без него новость
    # читается, просто читатель не вспомнит, с чего всё началось
    if facts.get("earlier") and trim == 0:
        lines.append("🧵 Ранее: " + esc(short(facts["earlier"], EARLIER)))
    lines.append(link)
    return "\n".join(lines)


def card_block(card, group, score, trim):
    """Новость сплошной ленты — та же карточка, собранная на лету."""
    return card_text(card_facts(card, group, score), trim)


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
        for card, group, score, _category in cards:
            chunks.append(card_block(card, group, score, trim))
    return (text + "\n" if text else "") + "\n\n".join(chunks)


def flatten(blocks) -> list:
    return [card for _topic, cards in blocks for card in cards]


def fits(text) -> bool:
    return len(text) <= TG_LIMIT - 60


def fit_blocks(blocks, scanned, head=True, note=""):
    """Возвращает список пар (текст, карточки этого сообщения).

    Карточки едут вместе с текстом: по ним видно, какие новости попали в
    какое сообщение.

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


def breaking_card(card, group, score, gain=""):
    """Отдельная карточка для срочного: одна новость, но с пометкой ⚡.

    `gain` — что в ней нового по сравнению с тем, что читатель уже видел.
    Событие то же самое, и без такой строки читателю пришлось бы искать
    отличие самому, сличая два сообщения (`dedup`, `llm.MORE`). Пусто —
    событие новое, сличать не с чем.
    """
    main = primary_of(group)
    others = sorted({i["source_id"] for i in group} - {main["source_id"]})[:3]
    lines = ["⚡ <b>Срочно</b>", "",
             "<b>%s</b>" % esc(card.get("headline") or main["title"])]
    gain = " ".join(str(gain or "").split())[:120].strip()
    if gain:
        lines.append("🔁 Новое: " + esc(gain))
    what = str(card.get("what") or main["summary"][:300]).strip()
    if what:
        lines.append(esc(what))
    note = factcheck.caveat_of(group)
    if note:
        lines.append("⚠️ " + esc(note))
    why = str(card.get("why") or "").strip()
    if why:
        lines.append("💡 " + esc(why))
    confirm = " · подтверждают: " + esc(", ".join(others)) if others else ""
    lines.append('🔗 <a href="%s">%s</a>%s · ⭐ %.1f'
                 % (esc(main["url"]), esc(main["source_id"]), confirm, score))
    lines.append("<i>Остальное придёт в очередном выпуске.</i>")
    return "\n".join(lines)


def alert_bulletin(rows) -> str:
    """🔔 Сводка важного: то, что не тянет на молнию, но и не ждёт до утра.

    Коротко — по строке на новость. Читатель уже понял по значку, что это
    внеплановое; разворачивать здесь нечего, для этого есть выпуск.
    """
    lines = ["🔔 <b>Важное за последние часы</b>", ""]
    for row in rows:
        headline = str(row["headline"] or row["title"])
        lines.append("• <b>%s</b>" % esc(headline))
        what = str(row["what"] or "").strip()
        if what:
            lines.append(esc(what))
        lines.append('🔗 <a href="%s">%s</a> · ⭐ %.1f'
                     % (esc(row["url"]), esc(row["source_id"]),
                        float(row["urgency"] or 0)))
        lines.append("")
    lines.append("<i>Подробности — в очередном выпуске.</i>")
    return "\n".join(lines)


# ------------------------------------------------------------- подписи кнопок
# Кнопок 👍/👎/🔖 под выпуском больше нет: ряд из трёх кнопок на каждую новость
# превращал выпуск в пульт, и чем больше новостей, тем сильнее он резал глаз.
# Оценивают и откладывают на странице, а в Telegram выпуск только читают.


def short(text, limit) -> str:
    """Начало заголовка для подписи кнопки: режем по слову, а не по букве.

    Смотрим на букву за пределом: если там пробел, слово на границе целое и
    остаётся («…по семейной…», а не «…по…»).
    """
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1].rsplit(" ", 1)[0][:limit] or text[:limit]
    return cut.rstrip(" ,.:;—-·") + "…"
