# -*- coding: utf-8 -*-
"""Разделы выпуска: как их называть, как разбирать и кто на какие подписан.

Раздел — это тема из profiles.PROFILES, взятая с той стороны, с которой на
неё смотрит читатель: у него есть список разделов, и плановый выпуск идёт
по этому списку. Здесь только справочник и разбор имён; сборка выпуска —
в pipeline.py.

Имя раздела можно писать как удобно: `medicine`, `медицина`, `мед` —
всё это один и тот же раздел. Поэтому команды принимают человеческий ввод
с телефона, а не только латинские идентификаторы.

У читателя есть ещё и личный топ (`favorites`): до пяти разделов, которые он
хочет видеть первыми. Список разделов отвечает на вопрос «что приходит»,
топ — на вопрос «в каком порядке»; `order()` сводит их вместе.
"""
from __future__ import annotations

from .config import CFG
from .profiles import DEFAULT_SECTIONS, PROFILES, label, title

#: сколько разделов имеет смысл держать в выпуске. Это не запрет,
#: а защита от «включил всё и получил простыню на 60 новостей».
MAX_SECTIONS = 20

#: сколько разделов можно поднять в начало выпуска. Пять — это ровно тот
#: размер, который читается с первого экрана; если «первым делом» идёт
#: половина выпуска, то никакого «первым делом» уже нет.
MAX_FAVORITES = 5


def known() -> list:
    """Все разделы: сначала подборка по умолчанию, потом остальные."""
    rest = sorted(name for name in PROFILES if name not in DEFAULT_SECTIONS)
    return [name for name in DEFAULT_SECTIONS if name in PROFILES] + rest


def source_map() -> dict:
    """Источник -> раздел, которому он принадлежит.

    Один и тот же сайт встречается в нескольких разделах (arstechnica есть и
    в «ИИ», и в «Железе»), поэтому берём первый по порядку `known()` — тот же
    порядок, в котором разделы разбираются при сборке выпуска. Так подпись под
    новостью совпадает с вывеской, под которой она пришла.

    Нужно ленте на странице: у старых записей в истории раздел не сохранён, а
    показать его надо.
    """
    out = {}
    for topic in known():
        for feed in PROFILES.get(topic, {}).get("feeds", ()) or ():
            out.setdefault(feed[0], topic)
    return out


def by_source(source_id: str) -> str:
    """Раздел источника. Пусто — источника нет ни в одном профиле."""
    return source_map().get(str(source_id or ""), "")


def _aliases() -> dict:
    """Имя (любое) -> идентификатор раздела. Пересборка каждый раз намеренна:
    PROFILES меняется на лету командами /feed и правкой profiles.json."""
    table = {}
    for name, body in PROFILES.items():
        table[name.lower()] = name
        table[str(body.get("title") or name).lower()] = name
        for alias in (body.get("aliases") or ()):
            table[str(alias).lower()] = name
    return table


def resolve(name: str) -> str:
    """'Мед' -> 'medicine'. Незнакомое имя -> пустая строка."""
    key = str(name or "").strip().lower().lstrip("/#").rstrip(",;")
    if not key:
        return ""
    table = _aliases()
    if key in table:
        return table[key]
    # «медицин» и «медицинa» тоже должны находиться: ищем по началу слова,
    # но только если совпадение однозначное
    hits = {topic for alias, topic in table.items() if alias.startswith(key)}
    return hits.pop() if len(hits) == 1 else ""


def parse(raw) -> tuple:
    """Строка или список имён -> (разделы без повторов, непонятые имена)."""
    if isinstance(raw, (list, tuple, set)):
        words = [str(x) for x in raw]
    else:
        words = str(raw or "").replace(",", " ").split()
    found, unknown = [], []
    for word in words:
        topic = resolve(word)
        if not topic:
            unknown.append(word)
        elif topic not in found:
            found.append(topic)
    return found, unknown


def store(topics) -> str:
    """Как список разделов лежит в базе и в env."""
    return ",".join(topics)


def defaults() -> list:
    """Разделы по умолчанию — из CFG, а если там пусто, то встроенная подборка."""
    topics, _unknown = parse(CFG.get("sections") or DEFAULT_SECTIONS)
    return topics


def field(sub, name) -> str:
    """Строковое поле подписчика. Пусто, если подписчика нет или база старее."""
    if sub is None:
        return ""
    try:
        return (sub[name] or "").strip()
    except (IndexError, KeyError):       # база ещё не знает про колонку
        return ""


def for_sub(sub=None) -> list:
    """Разделы конкретного подписчика: личные, а если их нет — общие."""
    personal = field(sub, "sections")
    if personal:
        topics, _unknown = parse(personal)
        if topics:
            return topics
    return defaults()


def favorites(sub=None) -> list:
    """Личный топ разделов: они идут в выпуске первыми.

    Свой топ подписчика, а если он ничего не отметил — общий из CFG. Порядок
    внутри топа тот, в котором разделы отмечали: первым отмеченный идёт первым.
    """
    topics, _unknown = parse(field(sub, "favorites") or CFG.get("favorites") or "")
    return topics[:MAX_FAVORITES]


def order(topics, sub=None) -> list:
    """Избранные разделы — вперёд, остальные — в прежнем порядке.

    Избранное, которого в списке разделов нет, не выбрасывается, а добавляется:
    отметив ⭐ Крипту (её нет в подборке по умолчанию), читатель ждёт её в
    выпуске, а не молчания. Порядок здесь решает не только вид выпуска: разделы
    разбираются по очереди, и новость, попавшая сразу в два раздела, достаётся
    тому, что стоит выше (`pipeline.usable`). Поэтому «моя тема» получает её
    первой — ровно этого от избранного и ждут.
    """
    top = [t for t in favorites(sub) if t in PROFILES]
    return top + [t for t in topics if t not in top]


def plan(sub=None) -> list:
    """Что попадёт в плановый выпуск и в каком порядке. Пустой список разделов =
    старое поведение: один выпуск по основной теме (CFG['topic'])."""
    topics = order(for_sub(sub), sub)
    if not topics:
        return [CFG["topic"]] if CFG["topic"] in PROFILES else []
    return topics[:MAX_SECTIONS]


def per_section(sub=None) -> int:
    """Сколько новостей на раздел в плановом выпуске."""
    value = 0
    if sub is not None:
        try:
            value = int(sub["per_section"] or 0)
        except (IndexError, KeyError, TypeError, ValueError):
            value = 0
    return max(1, value or int(CFG["per_section"]))


def persona(topics) -> str:
    """Портрет читателя для запросов, охватывающих несколько разделов.

    По одному разделу берём его собственный портрет — он точнее. Для смеси
    склеивать портреты бессмысленно (получится противоречивый текст), поэтому
    описываем читателя через список его интересов.
    """
    topics = [t for t in topics if t in PROFILES]
    if not topics:
        return "внимательный читатель, которому важны факты, а не мнения."
    if len(topics) == 1:
        return PROFILES[topics[0]]["persona"]
    return ("читатель ежедневного дайджеста. Его разделы: %s. Он ценит факты, "
            "конкретику и последствия события; не любит пересказ пресс-релизов, "
            "спекуляции и тексты без новости внутри."
            % ", ".join(title(t) for t in topics))


def describe(topics) -> str:
    """«🩺 Медицина, ⚽ Спорт» — для сообщений бота."""
    return ", ".join(label(t) for t in topics) or "нет ни одного"
