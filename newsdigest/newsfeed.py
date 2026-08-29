# -*- coding: utf-8 -*-
"""Лента новостей для страницы: что уже пришло читателю и что вокруг неё.

Страница в браузере когда-то показывала переписку с ботом — те же сообщения,
что уходят в Telegram. Прочитать выпуск так можно, а вот вернуться к вчерашней
новости, посмотреть один раздел или найти что-то поиском — нет: всё лежало
внутри текста сообщения.

Здесь лента собирается из истории `sent`: одна отправленная новость — одна
карточка. Ничего не досчитывается и не запрашивается заново, к модели мы не
ходим — показываем ровно то, что читателю уже показывали. Поэтому лента
открывается мгновенно и не стоит ни копейки.

Три вида ленты живут на одних и тех же карточках:

    news    всё, что приходило (с фильтром по разделу и поиском)
    saved   закладки 🔖
    liked   отмеченное 👍

Отсюда же берутся и «Уведомления»: та же история, но крупным планом — когда
была рассылка, сколько в ней было новостей и пять самых важных ссылок (см.
`mailings`).

Карточка ленты — это заголовок И текст новости: по одному заголовку понять,
о чём новость, нельзя, а открывать ради этого источник читатель не должен.
Текст берётся из истории, а где его там нет (записи до версии 3.5, выпуски,
собранные при недоступной модели) — из самого материала в `items`.

Язык проверяется здесь ещё раз, уже после выпуска: см. `russify`.

Telegram этого всего не касается: там по-прежнему приходит сообщение.
"""
from __future__ import annotations

import re
import sqlite3
import time
import urllib.parse

from . import safety, sections, translate
from .config import CFG, local_now, log, to_local
from .feedparse import clean_title, parse_date
from .profiles import emoji as topic_emoji
from .profiles import title as topic_title
from .render import MONTHS
from .textutil import STOPWORDS

#: сколько карточек отдаём за один запрос страницы
PAGE = 20

#: сколько строк истории просматриваем при поиске перебором. Больше в `sent`
#: и не бывает: она живёт keep_sent_days и подрезается при каждом сборе.
#: Общая лента ходит этим путём, только когда индекса нет (см. `matching`)
SEARCH_ROWS = 3000

#: столько последних заголовков разбираем на популярные темы
TOPIC_ROWS = 120

#: оттенок плашки раздела (H в hsl). Подобраны так, чтобы соседи в меню не
#: сливались, а привычные разделы выглядели ожидаемо: происшествия красные,
#: политика синяя, экономика зелёная
TONES = {
    "ai": 265, "hardware": 205, "robots": 190, "space": 250, "climate": 150,
    "science": 175, "medicine": 340, "health": 130, "politics": 215,
    "economy": 145, "sports": 95, "incidents": 5, "cinema": 285, "games": 275,
    "crypto": 40, "cybersec": 20, "custom": 230,
}

#: оттенок «Главного» и новостей без раздела
PLAIN_TONE = 220

#: части запроса, которые лента понимает
VIEWS = ("news", "saved", "liked")

#: Откуда берутся карточки каждого вида. Колонки у всех трёх запросов
#: называются одинаково — дальше фильтр, сортировка и разбор общие.
#: Закладка и оценка живут в своих таблицах и знают о новости мало
#: (заголовок да ссылку), поэтому подтягиваем к ним строку истории: в ней
#: лежит и раздел, и суть, и оценка модели.
#: К каждой новости подтягивается и сам материал (`items`): в нём лежит текст
#: из фида — запасной текст карточки, когда в истории суть не сохранена.
SOURCES = {
    "news": """
        SELECT n.url_hash AS url_hash, n.title AS title,
               n.headline AS headline, n.summary AS summary,
               n.caveat AS caveat,
               COALESCE(i.summary, '') AS lead,
               n.url AS url, n.source_id AS source_id, n.section AS section,
               n.score AS score, n.breaking AS breaking, n.sent_at AS at
          FROM sent n
          LEFT JOIN items i ON i.url_hash = n.url_hash
         WHERE n.chat_id = ?
    """,
    "saved": """
        SELECT b.url_hash AS url_hash, b.title AS title,
               COALESCE(n.headline, '') AS headline,
               COALESCE(n.summary, '') AS summary,
               COALESCE(n.caveat, '') AS caveat,
               COALESCE(i.summary, '') AS lead,
               CASE WHEN b.url != '' THEN b.url
                    ELSE COALESCE(n.url, '') END AS url,
               CASE WHEN b.source_id != '' THEN b.source_id
                    ELSE COALESCE(n.source_id, '') END AS source_id,
               COALESCE(n.section, '') AS section,
               COALESCE(n.score, 0) AS score,
               COALESCE(n.breaking, 0) AS breaking, b.at AS at
          FROM saved b
          LEFT JOIN sent n ON n.chat_id = b.chat_id AND n.url_hash = b.url_hash
          LEFT JOIN items i ON i.url_hash = b.url_hash
         WHERE b.chat_id = ?
    """,
    "liked": """
        SELECT f.url_hash AS url_hash, f.title AS title,
               COALESCE(n.headline, '') AS headline,
               COALESCE(n.summary, '') AS summary,
               COALESCE(n.caveat, '') AS caveat,
               COALESCE(i.summary, '') AS lead,
               COALESCE(n.url, '') AS url,
               CASE WHEN f.source_id != '' THEN f.source_id
                    ELSE COALESCE(n.source_id, '') END AS source_id,
               COALESCE(n.section, '') AS section,
               COALESCE(n.score, 0) AS score,
               COALESCE(n.breaking, 0) AS breaking, f.at AS at
          FROM feedback f
          LEFT JOIN sent n ON n.chat_id = f.chat_id AND n.url_hash = f.url_hash
          LEFT JOIN items i ON i.url_hash = f.url_hash
         WHERE f.chat_id = ? AND f.verdict = 'up'
    """,
}


# ------------------------------------------------------------------ мелочи
def column(row, name, default=""):
    """Значение колонки, которой в строке может и не быть.

    Карточки собираются не только из запросов выше: в тестах и в старых базах
    строка приходит короче, и отсутствие колонки не повод падать.
    """
    try:
        value = row[name]
    except (IndexError, KeyError, TypeError):
        return default
    return default if value is None else value


def tone(topic: str) -> int:
    """Оттенок раздела. Незнакомый (свой, из profiles.json) получает свой
    собственный — устойчивый, чтобы цвет не прыгал от запроса к запросу."""
    if not topic:
        return PLAIN_TONE
    if topic in TONES:
        return TONES[topic]
    return (sum(ord(ch) for ch in topic) * 37) % 360


def domain(url: str) -> str:
    """«ria.ru» из ссылки — именно так источник подписан в ленте."""
    try:
        host = urllib.parse.urlparse(str(url or "")).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


#: сколько текста новости кладём в карточку. Двух-трёх предложений хватает,
#: чтобы понять новость, не открывая источник, — а на странице этот текст ещё
#: и сворачивается до трёх строк, так что карточки остаются одного роста
LEAD = 400


def shorten(text: str, limit: int = LEAD) -> str:
    """Обрезка по слову: обрывать текст на середине слова некрасиво."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(" ,.:;—-·") + "…"


def urgent(row) -> bool:
    """Пришла ли новость как срочная — вне расписания, по тревоге.

    Метку ставит `breaking.deliver`; у записей, сделанных до её появления,
    колонки может не быть вовсе — такие считаем обычными.
    """
    return bool(column(row, "breaking", 0))


def body(row) -> str:
    """Текст карточки: суть, написанная моделью, а если её нет — текст из фида.

    Пустая суть — это записи, сделанные до появления колонки (версия 3.5), и
    выпуски, собранные, пока модель была недоступна. Показывать в такой
    карточке один заголовок незачем: сам материал никуда не делся и лежит
    рядом, в `items`, — он и идёт в дело.
    """
    return shorten(column(row, "summary") or column(row, "lead"))


def stamp(iso: str, now=None) -> str:
    """«18:27» у сегодняшнего, «вчера, 18:27», дальше — «15.08»."""
    at = parse_date(iso)
    if at is None:
        return ""
    local = to_local(at)
    now = now if now is not None else local_now()
    days = (now.date() - local.date()).days
    if days == 0:
        return local.strftime("%H:%M")
    if days == 1:
        return "вчера, " + local.strftime("%H:%M")
    return local.strftime("%d.%m")


# ------------------------------------------------------------------- склонения
#: кириллическое слово — только такие и склоняются
CYRILLIC = re.compile(r"^[А-Яа-яЁё-]+$")

#: окончания, которые отрезаем. Не морфология, а грубая обрезка, и её хватает
#: сразу двум местам: поиску (запрос «Иране» должен находить «Иран») и подсчёту
#: тем (иначе главная новость недели рассыпается на три редких слова и в список
#: популярного не попадает вовсе). Длинные окончания стоят раньше коротких —
#: их и проверяем первыми
ENDINGS = ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
           "ах", "ях", "ов", "ев", "ей", "ой", "ий", "ый", "ая", "яя", "ое",
           "ее", "ые", "ие", "ам", "ям", "ом", "ем",
           "у", "ю", "а", "я", "ы", "и", "е", "о", "ь")


def stem(word: str) -> str:
    """Основа слова. Латиница не склоняется — её не трогаем."""
    low = word.lower()
    if not CYRILLIC.match(low):
        return low
    for end in ENDINGS:
        if low.endswith(end) and len(low) - len(end) >= 4:
            return low[:-len(end)]
    return low


# -------------------------------------------------------------------- выборка
def _section_filter(topic: str) -> tuple:
    """Условие «эта новость из раздела»: по сохранённому разделу, а у старых
    записей — по источнику. Иначе после обновления вся история оказалась бы
    вне разделов."""
    from .sources import sources_for

    names = sorted(sources_for(topic))
    if not names:
        return "section = ?", [topic]
    return ("(section = ? OR (section = '' AND source_id IN (%s)))"
            % ",".join("?" * len(names)), [topic] + names)


def wanted(section) -> list:
    """Разделы, по которым отбираем ленту: одно имя или их набор.

    Страница умеет держать несколько фильтров сразу («только наука и спорт»),
    и приходит от неё либо строка с одним разделом, либо список.
    """
    names = [section] if isinstance(section, str) else list(section or ())
    out = []
    for name in names:
        topic = str(name or "").strip()
        if topic and topic not in out:
            out.append(topic)
    return out


def _sections_filter(topics) -> tuple:
    """Условие «новость из любого выбранного раздела»."""
    clauses, args = [], []
    for topic in topics:
        clause, params = _section_filter(topic)
        clauses.append(clause)
        args += params
    return "(%s)" % " OR ".join(clauses), args


def _hit(row, words) -> bool:
    """Запасной поиск — перебором в Python.

    LOWER() в SQLite знает только латиницу, а искать «Ормузский» и
    «ормузский» читатель должен одинаково: регистр приходится сворачивать
    самим. Так лента искала всегда, и так она ищет до сих пор в закладках, в
    избранном и везде, где нет индекса (см. `matching`).
    """
    blob = " ".join(str(column(row, key)) for key in
                    ("title", "headline", "summary", "lead", "source_id",
                     "url")).lower()
    return all(word in blob for word in words)


def needle(query: str) -> list:
    """Слова запроса, приведённые к основе.

    Основа — начало слова, поэтому она находится подстрокой в любой его форме:
    «Иране» ищет и «Иран», и «Ирана». Без этого поиск по-русски работал бы
    только при точном попадании в падеж, а плашка «Иран» в популярных темах
    не находила бы половину новостей, из которых сама и составлена.
    """
    return [stem(word) for word in str(query or "").lower().split() if word]


#: Слово, в котором есть хоть один знак, — из такого FTS5 выделит токен.
#: Запрос из одних скобок и точек индексу сказать нечего, и разбирать его
#: идёт перебор: там «...» — это просто подстрока.
WORDY = re.compile(r"[^\W_]", re.UNICODE)


def match_query(words) -> str:
    """Слова запроса -> выражение MATCH для FTS5. Пусто — индекс не поможет.

    Каждое слово ищется префиксом: в `words` лежат основы (см. `needle`), а
    основа — это начало слова, и «иран*» находит и «Иран», и «в Иране».
    Слова соединяются через AND: запрос «иран нефть» сужает ответ, а не
    расширяет его, — ровно как у `_hit`.

    Слово берётся в кавычки: в нём может оказаться точка («ria.ru») или
    дефис, а для FTS5 это синтаксис, а не буквы. В кавычках это фраза —
    подряд идущие слова, что для «ria.ru» и требуется.
    """
    if not all(WORDY.search(word) for word in words):
        return ""
    return " AND ".join('"%s"*' % word.replace('"', '""') for word in words)


def matching(conn, view, chat_id, words) -> tuple:
    """Условие «эта новость нашлась поиском» для SQL — или пусто.

    Пусто значит «индексом не обойтись, ищи перебором»: FTS5 в сборке нет,
    индекс ещё не собран или запрос ему не по зубам.

    Индекс стоит над историей (`sent`) — по ней и идёт общая лента. Закладки
    и оценки живут в своих таблицах, и новость, чью запись в истории уже
    подрезал `keep_sent_days`, в индексе не значится: там поиск остаётся
    перебором. Строк там немного — это отметки одного читателя, а не вся
    история, — и найтись в закладках должно всё, что в них лежит.
    """
    from . import storage

    if view != "news" or not storage.searchable(conn):
        return "", []
    expr = match_query(words)
    if not expr:
        return "", []
    return ("url_hash IN (SELECT n.url_hash FROM sent n WHERE n.chat_id = ?"
            " AND n.rowid IN (SELECT rowid FROM %s WHERE %s MATCH ?))"
            % (storage.SEARCH_TABLE, storage.SEARCH_TABLE)), [str(chat_id), expr]


def page(conn, chat_id, view="news", section="", query="", offset=0, limit=PAGE):
    """Карточки одной страницы ленты и признак «есть ещё».

    `section` — раздел или их набор: читатель на странице может закрепить
    несколько разделов сразу, и тогда лента идёт по ним всем.

    Пагинацию делает база — и без поиска, и с поиском по индексу. Только
    там, где индекса нет (см. `matching`), отбор идёт по словам в Python
    (`_hit`), SQL про него не знает, и страница нарезается из прочитанного.
    """
    base = SOURCES.get(view) or SOURCES["news"]
    args, offset, limit = [str(chat_id)], max(int(offset), 0), max(int(limit), 1)
    topics = wanted(section)
    if topics:
        clause, params = _sections_filter(topics)
        base = "SELECT * FROM (%s) WHERE %s" % (base, clause)
        args += params

    words = needle(query)
    clause, params = matching(conn, view, chat_id, words) if words else ("", [])
    if clause:
        indexed = "SELECT * FROM (%s) WHERE %s" % (base, clause)
        try:
            rows = list(conn.execute(
                indexed + " ORDER BY at DESC LIMIT ? OFFSET ?",
                args + params + [limit + 1, offset]))
        except sqlite3.OperationalError as exc:
            # индекс не разобрал запрос — не повод отдать читателю ошибку:
            # ниже лежит перебор, он разберёт что угодно
            log.warning("Поиск «%s» мимо индекса: %s", query, exc)
        else:
            return rows[:limit], len(rows) > limit

    if not words:
        rows = list(conn.execute(base + " ORDER BY at DESC LIMIT ? OFFSET ?",
                                 args + [limit + 1, offset]))
        return rows[:limit], len(rows) > limit

    found = [row for row in conn.execute(base + " ORDER BY at DESC LIMIT ?",
                                         args + [SEARCH_ROWS])
             if _hit(row, words)]
    window = found[offset:offset + limit + 1]
    return window[:limit], len(window) > limit


def cards(conn, rows, verdicts=None, saved=None, chat_id=None) -> list:
    """Строки истории -> карточки для страницы, доведённые до языка выпуска."""
    verdicts, saved = verdicts or {}, saved or ()
    smap = sections.source_map()
    now = local_now()
    out = []
    for row in rows:
        topic = row["section"] or smap.get(row["source_id"], "")
        # заголовок карточки писала модель; у записей, сделанных до появления
        # этой колонки, берём заголовок фида
        out.append({
            "hash": row["url_hash"],
            "title": clean_title(
                str(column(row, "headline") or column(row, "title"))),
            "summary": body(row),
            "url": outward(row["url"]),
            "caveat": str(column(row, "caveat") or ""),
            "source": domain(row["url"]) or row["source_id"] or "источник",
            "section": topic,
            "label": topic_title(topic) if topic else "Новости",
            "emoji": topic_emoji(topic) if topic else "📰",
            "tone": tone(topic),
            "at": stamp(row["at"], now),
            "score": round(float(row["score"] or 0), 1),
            "breaking": urgent(row),
            "saved": row["url_hash"] in saved,
            "verdict": verdicts.get(row["url_hash"], ""),
        })
    changed = russify(conn, out)
    if changed and chat_id is not None:
        remember(conn, chat_id, [card for card in out if card["hash"] in changed])
    return out


# --------------------------------------------------------- лента по-русски
#: поля карточки, которые читает человек, — их язык и проверяем
SPEECH = ("title", "summary")

#: сколько строк за один показ ленты имеет смысл отправить модели. Страница
#: ждёт ответа, поэтому берём столько, сколько нужно одной странице ленты,
#: и ни строкой больше: остальное догонит следующий заход
TRANSLATE_LIMIT = 45

#: модель не ответила — столько секунд к ней не ходим. Иначе каждая прокрутка
#: ленты упирается в таймауты и повторы, а страница висит вместе с ними
TRANSLATE_PAUSE = 300

#: строки, которые модель уже отказалась переводить (вернула их же). Платить
#: за них на каждом показе ленты незачем; после перезапуска попробуем снова
STUBBORN_MAX = 4000

_stubborn = set()
_silent_until = 0.0


def ask_model(conn, texts) -> dict:
    """Перевод того, чего не нашлось в кэше. Пусто — модель не помогла."""
    global _silent_until
    if not texts or not CFG["translate"] or time.time() < _silent_until:
        return {}
    part = [text for text in texts
            if translate.key_of(text) not in _stubborn][:TRANSLATE_LIMIT]
    if not part:
        return {}
    ready, _cost = translate.translated(conn, part)
    if not ready:
        # модель недоступна: молчим минуты, но строки не запоминаем — когда
        # она вернётся, лента доведёт их до языка выпуска сама
        _silent_until = time.time() + TRANSLATE_PAUSE
        return {}
    if len(_stubborn) > STUBBORN_MAX:
        _stubborn.clear()
    _stubborn.update(translate.key_of(text) for text in part if text not in ready)
    return ready


def russify(conn, cards) -> set:
    """Догоняет карточки до языка выпуска. Возвращает хэши изменившихся.

    Выпуск переводится при сборке, но в истории всё равно оседает английский: у
    записей, сделанных до версии 3.5, заголовок взят прямо из фида, а когда
    модель была недоступна, на языке источника остаётся и заголовок, и суть.
    Читателю от причины не легче — лента обязана быть на его языке, чем бы ни
    закончилась сборка выпуска.

    Поэтому язык проверяется ещё раз здесь, ровно как перед отправкой (см.
    `translate`): сначала кэш переводов — он закрывает почти всё и не стоит
    ничего, — и только за остатком один запрос к модели.
    """
    spots = [(card, name) for card in cards for name in SPEECH
             if translate.foreign(card.get(name))]
    if not spots:
        return set()
    texts = list(dict.fromkeys(card[name] for card, name in spots))
    ready = translate.cached(conn, texts)
    ready.update(ask_model(conn, [text for text in texts if text not in ready]))

    changed = set()
    for card, name in spots:
        fresh = ready.get(card[name])
        if fresh:
            card[name] = shorten(fresh) if name == "summary" else fresh
            changed.add(card["hash"])
    return changed


def remember(conn, chat_id, cards) -> None:
    """Русский текст оседает в истории.

    Перевод и так лежит в кэше, но карточка целиком — это ещё и поиск: пока в
    `sent` английский заголовок, запрос «Иран» эту новость не находит. Заодно
    заполняется пустая суть у старых записей, и второй раз лента её из `items`
    не достаёт.
    """
    conn.executemany(
        "UPDATE sent SET headline=?, summary=? WHERE chat_id=? AND url_hash=?",
        [(str(card["title"])[:300], str(card["summary"])[:500],
          str(chat_id), card["hash"]) for card in cards])
    conn.commit()


# ------------------------------------------------------------------ рассылки
#: разрыв между соседними записями истории, после которого это уже другая
#: рассылка. Выпуск оседает в истории одним махом — все его новости в пределах
#: секунд, — а следующий приходит через часы. Получаса хватает, чтобы не
#: склеить два выпуска и не разорвать один
MAILING_GAP = 30 * 60

#: сколько рассылок показываем в «Уведомлениях»
MAILINGS = 10

#: сколько ссылок берём из рассылки — самые важные по оценке модели
MAILING_LINKS = 5

#: строк истории, по которым собираются рассылки. На десяток выпусков этого
#: с запасом хватает, а перебирать всю историю ради заголовков незачем
MAILING_ROWS = 500


def outward(url: str) -> str:
    """Ссылка наружу. Адрес пришёл из чужого фида, поэтому проверяется здесь
    ещё раз, у самого HTML.

    Схемы мало. `https://apnews.com@phish.tk/x` — это http(s), и старая
    проверка его пропускала: подпись «apnews», переход на phish.tk. Разбор
    формы адреса живёт в `safety.shaped_badly` и стоит столько же — ни базы,
    ни сети он не трогает.

    Второй рубеж нужен не для порядка: в истории лежат новости, собранные до
    того, как проверка появилась, вердикта у них нет вовсе, а страница
    открыта всем.
    """
    return safety.outward(url)


def spoken_date(local, now) -> str:
    """«сегодня, 17 августа» — дата рассылки словами."""
    day = "%d %s" % (local.day, MONTHS[local.month - 1])
    days = (now.date() - local.date()).days
    if days == 0:
        return "сегодня, " + day
    if days == 1:
        return "вчера, " + day
    return day if local.year == now.year else "%s %d" % (day, local.year)


def link_of(row, smap) -> dict:
    """Строка истории -> ссылка для «Уведомлений»: заголовок, источник, оценка.

    Заголовок берём тот же, что в ленте, и заново к модели за ним не ходим:
    английские записи доводит до русского сама лента (см. `russify`), а
    уведомления показывают уже осевший в истории текст.
    """
    topic = row["section"] or smap.get(row["source_id"], "")
    url = outward(column(row, "url"))
    return {"title": clean_title(
                str(column(row, "headline") or column(row, "title"))),
            "url": url,
            "source": domain(url) or column(row, "source_id") or "источник",
            "score": round(float(column(row, "score", 0) or 0), 1),
            "breaking": urgent(row),
            "emoji": topic_emoji(topic) if topic else "📰"}


def mailing(group, smap, now) -> dict:
    """Одна рассылка: когда пришла, сколько в ней было и что в ней главное."""
    rows = group["rows"]
    topics = {row["section"] or smap.get(row["source_id"], "") for row in rows}
    best = sorted(rows, key=lambda row: -float(column(row, "score", 0) or 0))
    local = to_local(group["at"])
    return {"id": str(rows[0]["sent_at"]),
            "when": spoken_date(local, now),
            "time": local.strftime("%H:%M"),
            "breaking": any(urgent(row) for row in rows),
            "count": len(rows),
            "sections": len([topic for topic in topics if topic]),
            "links": [link_of(row, smap) for row in best[:MAILING_LINKS]]}


def latest(conn, chat_id) -> str:
    """Самая свежая новость ленты. По ней страница и замечает пополнение.

    Владельцу для этого хватает последней рассылки, а гость про рассылки не
    знает вовсе — ему нужна примета попроще, и лучше самой новой новости
    ничего нет: она и так лежит первой карточкой в ленте.
    """
    row = conn.execute("SELECT url_hash FROM sent WHERE chat_id = ? "
                       "ORDER BY sent_at DESC LIMIT 1",
                       (str(chat_id),)).fetchone()
    return row["url_hash"] if row else ""


def mailings(conn, chat_id, limit=MAILINGS) -> list:
    """Уведомления: краткая сводка последних рассылок, свежая сверху.

    Отдельной таблицы выпусков нет, и заводить её незачем: новости одной
    рассылки лежат в истории подряд и в пределах секунд. Рассылка — это
    соседние записи без разрыва длиннее MAILING_GAP; так же отделяется и
    срочная новость, пришедшая вне расписания.
    """
    rows = list(conn.execute(
        "SELECT url_hash, title, headline, url, source_id, section, score, "
        "breaking, sent_at FROM sent WHERE chat_id = ? "
        "ORDER BY sent_at DESC LIMIT ?",
        (str(chat_id), MAILING_ROWS)))
    groups = []
    for row in rows:
        at = parse_date(row["sent_at"])
        if at is None:
            continue
        if groups and (groups[-1]["edge"] - at).total_seconds() <= MAILING_GAP:
            groups[-1]["edge"] = at      # хвост группы: от него меряем разрыв
            groups[-1]["rows"].append(row)
            continue
        if len(groups) >= limit:
            break
        groups.append({"at": at, "edge": at, "rows": [row]})
    smap, now = sections.source_map(), local_now()
    return [mailing(group, smap, now) for group in groups]


# --------------------------------------------------------------- панели вокруг
def counts(conn, chat_id) -> dict:
    """Сколько новостей в каждом разделе; ключ "" — сколько всего."""
    smap = sections.source_map()
    out, total = {}, 0
    for row in conn.execute(
            "SELECT section, source_id, COUNT(*) AS n FROM sent "
            "WHERE chat_id = ? GROUP BY section, source_id", (str(chat_id),)):
        topic = row["section"] or smap.get(row["source_id"], "")
        out[topic] = out.get(topic, 0) + row["n"]
        total += row["n"]
    out[""] = total
    return out


def menu(conn, chat_id, plan) -> list:
    """Пункты меню разделов.

    Сначала разделы читателя, следом те, где новости есть, а сам раздел уже
    отключён: история от этого не исчезает, и добраться до неё надо.
    """
    have = counts(conn, chat_id)
    order = [t for t in plan if t]
    order += [t for t in sorted(have) if t and t not in order and have[t]]
    out = [{"id": "", "title": "Главное", "emoji": "🏠", "tone": PLAIN_TONE,
            "count": have.get("", 0)}]
    for topic in order:
        out.append({"id": topic, "title": topic_title(topic),
                    "emoji": topic_emoji(topic), "tone": tone(topic),
                    "count": have.get(topic, 0)})
    return out


def sources(conn, chat_id, limit=5) -> list:
    """Популярные источники: кто чаще всех доезжает до выпуска.

    Рядом — средняя оценка модели по его новостям. Это честная цифра: ровно
    та ⭐, что стоит под новостью в выпуске. У записей, сделанных до появления
    колонки, оценки нет — такие в среднее не идут, и звёздочки у источника
    не будет вовсе.
    """
    out = []
    for row in conn.execute(
            "SELECT source_id, COUNT(*) AS n, MAX(url) AS url, "
            "AVG(CASE WHEN score > 0 THEN score END) AS rating "
            "FROM sent WHERE chat_id = ? AND source_id != '' "
            "GROUP BY source_id ORDER BY n DESC, source_id LIMIT ?",
            (str(chat_id), int(limit))):
        out.append({"id": row["source_id"],
                    "name": domain(row["url"]) or row["source_id"],
                    "count": row["n"],
                    "rating": round(row["rating"], 1) if row["rating"] else 0})
    return out


#: слова, которые встречаются в любых новостях и темой не являются.
#: Короткие отсекает сам разбор (см. WORD), поэтому здесь только длинные
NOISE = set("""
после против более менее может могут будет будут было были стал стала стали
своих своей своего этого этом этой этих который которые которая которое
чтобы также однако пока если между около почти сколько зачем куда снова
через среди вокруг кроме вдоль сквозь помимо вместо вместе несмотря
поэтому потому впервые очень сразу теперь пока ещё уже итоге время
заявил заявила заявили сообщил сообщила сообщили назвал назвала назвали
объявил объявила объявили рассказал рассказала рассказали считает планирует
получил получила получили показал показала показали
новый новая новые новое первый первая первые самый самая года году годах
what when with from that this have will been they their there here
about into over under than then some more most other said says will
""".split())

#: слово темы: с буквы, не короче четырёх знаков
WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9-]{3,}")

def _form(seen) -> str:
    """Как показать тему: чаще встречавшаяся форма, а при равенстве — та, что
    с большой буквы и покороче. Так в плашке оказывается «Иран», а не
    «иранского»."""
    return sorted(seen.items(),
                  key=lambda kv: (-kv[1], not kv[0][:1].isupper(),
                                  len(kv[0]), kv[0]))[0][0]


def topics(conn, chat_id, limit=10) -> list:
    """Популярные темы: слова, которые чаще других встречаются в заголовках.

    Никакой модели: частотный разбор последних TOPIC_ROWS заголовков. Этого
    хватает — имена стран, компаний и событий всплывают сами, а служебные
    слова отсеиваются стоп-списком.
    """
    counted, forms = {}, {}
    for row in conn.execute(
            "SELECT headline, title FROM sent WHERE chat_id = ? "
            "ORDER BY sent_at DESC LIMIT ?", (str(chat_id), TOPIC_ROWS)):
        for word in WORD.findall(row["headline"] or row["title"] or ""):
            root = stem(word)
            # служебное слово отсекаем и по нему самому, и по основе: так один
            # «заявил» в списке закрывает и «заявили», и «заявила»
            if word.lower() in NOISE or word.lower() in STOPWORDS or root in NOISE:
                continue
            counted[root] = counted.get(root, 0) + 1
            seen = forms.setdefault(root, {})
            seen[word] = seen.get(word, 0) + 1
    ranked = sorted(counted.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"word": _form(forms[root]), "count": n}
            for root, n in ranked[:limit] if n > 1]
