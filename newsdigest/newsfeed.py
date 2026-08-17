# -*- coding: utf-8 -*-
"""Лента новостей для страницы: что уже пришло читателю и что вокруг неё.

Страница в браузере показывала переписку с ботом — те же сообщения, что
уходят в Telegram. Прочитать выпуск так можно, а вот вернуться к вчерашней
новости, посмотреть один раздел или найти что-то поиском — нет: всё лежит
внутри текста сообщения.

Здесь лента собирается из истории `sent`: одна отправленная новость — одна
карточка. Ничего не досчитывается и не запрашивается заново, к модели мы не
ходим — показываем ровно то, что читателю уже показывали. Поэтому лента
открывается мгновенно и не стоит ни копейки.

Три вида ленты живут на одних и тех же карточках:

    news    всё, что приходило (с фильтром по разделу и поиском)
    saved   закладки 🔖
    liked   отмеченное 👍

Telegram этого всего не касается: там по-прежнему приходит сообщение.
"""
from __future__ import annotations

import re
import urllib.parse

from . import sections, translate
from .config import local_now, to_local
from .feedparse import parse_date
from .profiles import emoji as topic_emoji
from .profiles import title as topic_title
from .textutil import STOPWORDS

#: сколько карточек отдаём за один запрос страницы
PAGE = 20

#: сколько строк истории просматриваем при поиске. Больше в `sent` и не
#: бывает: она живёт keep_sent_days и подрезается при каждом сборе
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
SOURCES = {
    "news": """
        SELECT url_hash, title, headline, summary, url, source_id, section,
               score, sent_at AS at
          FROM sent
         WHERE chat_id = ?
    """,
    "saved": """
        SELECT b.url_hash AS url_hash, b.title AS title,
               COALESCE(n.headline, '') AS headline,
               COALESCE(n.summary, '') AS summary,
               CASE WHEN b.url != '' THEN b.url
                    ELSE COALESCE(n.url, '') END AS url,
               CASE WHEN b.source_id != '' THEN b.source_id
                    ELSE COALESCE(n.source_id, '') END AS source_id,
               COALESCE(n.section, '') AS section,
               COALESCE(n.score, 0) AS score, b.at AS at
          FROM saved b
          LEFT JOIN sent n ON n.chat_id = b.chat_id AND n.url_hash = b.url_hash
         WHERE b.chat_id = ?
    """,
    "liked": """
        SELECT f.url_hash AS url_hash, f.title AS title,
               COALESCE(n.headline, '') AS headline,
               COALESCE(n.summary, '') AS summary,
               COALESCE(n.url, '') AS url,
               CASE WHEN f.source_id != '' THEN f.source_id
                    ELSE COALESCE(n.source_id, '') END AS source_id,
               COALESCE(n.section, '') AS section,
               COALESCE(n.score, 0) AS score, f.at AS at
          FROM feedback f
          LEFT JOIN sent n ON n.chat_id = f.chat_id AND n.url_hash = f.url_hash
         WHERE f.chat_id = ? AND f.verdict = 'up'
    """,
}


# ------------------------------------------------------------------ мелочи
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


def _hit(row, words) -> bool:
    """Поиск идёт в Python: LOWER() в SQLite знает только латиницу, а
    искать «Ормузский» и «ормузский» читатель должен одинаково."""
    blob = " ".join(str(row[key] or "") for key in
                    ("title", "headline", "summary", "source_id", "url")).lower()
    return all(word in blob for word in words)


def needle(query: str) -> list:
    """Слова запроса, приведённые к основе.

    Основа — начало слова, поэтому она находится подстрокой в любой его форме:
    «Иране» ищет и «Иран», и «Ирана». Без этого поиск по-русски работал бы
    только при точном попадании в падеж, а плашка «Иран» в популярных темах
    не находила бы половину новостей, из которых сама и составлена.
    """
    return [stem(word) for word in str(query or "").lower().split() if word]


def page(conn, chat_id, view="news", section="", query="", offset=0, limit=PAGE):
    """Карточки одной страницы ленты и признак «есть ещё».

    Без поиска пагинация делается базой. С поиском — по прочитанным строкам:
    отбор идёт по словам в Python (см. `_hit`), и SQL про него не знает.
    """
    base = SOURCES.get(view) or SOURCES["news"]
    args, offset, limit = [str(chat_id)], max(int(offset), 0), max(int(limit), 1)
    if section:
        clause, params = _section_filter(section)
        base = "SELECT * FROM (%s) WHERE %s" % (base, clause)
        args += params

    words = needle(query)
    if not words:
        rows = list(conn.execute(base + " ORDER BY at DESC LIMIT ? OFFSET ?",
                                 args + [limit + 1, offset]))
        return rows[:limit], len(rows) > limit

    found = [row for row in conn.execute(base + " ORDER BY at DESC LIMIT ?",
                                         args + [SEARCH_ROWS])
             if _hit(row, words)]
    window = found[offset:offset + limit + 1]
    return window[:limit], len(window) > limit


def cards(conn, rows, verdicts=None, saved=None) -> list:
    """Строки истории -> карточки для страницы."""
    verdicts, saved = verdicts or {}, saved or ()
    smap = sections.source_map()
    now = local_now()
    out = []
    for row in rows:
        topic = row["section"] or smap.get(row["source_id"], "")
        # заголовок карточки писала модель; у записей, сделанных до появления
        # этой колонки, берём заголовок фида — и его же перевод, если он есть
        head = row["headline"] or translate.known(conn, row["title"])
        out.append({
            "hash": row["url_hash"],
            "title": head,
            "summary": str(row["summary"] or "")[:240],
            "url": row["url"] or "",
            "source": domain(row["url"]) or row["source_id"] or "источник",
            "section": topic,
            "label": topic_title(topic) if topic else "Новости",
            "emoji": topic_emoji(topic) if topic else "📰",
            "tone": tone(topic),
            "at": stamp(row["at"], now),
            "score": round(float(row["score"] or 0), 1),
            "saved": row["url_hash"] in saved,
            "verdict": verdicts.get(row["url_hash"], ""),
        })
    return out


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
