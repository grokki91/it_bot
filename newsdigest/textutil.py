# -*- coding: utf-8 -*-
"""Нормализация ссылок и сравнение заголовков — основа дедупликации."""
from __future__ import annotations

import hashlib
import re
import urllib.parse

TRACKING = re.compile(
    r"^(utm_|fbclid|gclid|msclkid|mc_cid|mc_eid|ref|ref_src|source|_hsenc|igshid|"
    r"share|at_medium|at_campaign|CMP|smid|guccounter)", re.IGNORECASE)


def canonical_url(url: str) -> str:
    """Снимаем трекинг — самый дешёвый и надёжный слой дедупликации."""
    url = (url or "").strip()
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return url
    if not parts.scheme.startswith("http"):
        return url
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
             if not TRACKING.match(k)]
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    path = re.sub(r"/amp$", "", parts.path.rstrip("/")) or "/"
    return urllib.parse.urlunparse(
        ("https", host, path, "", urllib.parse.urlencode(sorted(query)), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:32]


STOPWORDS = set("""
a an the of for on in to and or with is are was were be been being by at from as it
its this that these those has have had will would can could should new now more most
after before over under how why what when who which you your they their we our
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только
ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни
быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут
где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
""".split())



#: Приглашение дочитать на сайте и подпись движка. Отрезаем только то, что
#: начинает СВОЮ фразу: «рассказал подробнее о планах» — это текст новости, а
#: «Событие случилось. Подробнее на сайте» — уже подпись.
_TAIL_PHRASES = re.compile(
    r"""(?isx) (?: ^ | (?<=[.!?…»)"']) ) \s*
        (?: the\ post\b.{0,150}?appeared\ first\ on\b.{0,80}
          | continue\ reading.{0,80}
          | read\ (?:more|the\ full\ story).{0,60}
          | читать\ (?:далее|дальше|полностью).{0,60}
          | подробнее(?:\ на\ сайте)?.{0,60}
          | share\ this:.*
        ) $""")

#: Метка обрыва, которой лента заканчивает урезанное описание.
_TAIL_MARK = re.compile(r"\s*(?:\[\s*(?:…|\.\.\.)\s*\]|…)$")


def lead_of(title, summary, limit: int = 300) -> str:
    """Начало заметки без повтора заголовка и хвостов ленты.

    Так новость видит модель — и в ранжировании, и в карточке, и в вопросе
    про дубль. Половина лент кладёт в описание сначала сам заголовок слово в
    слово, а в конец — «The post … appeared first on …» и приглашение читать
    дальше. Модели это не сообщает ничего: заголовок она уже видит рядом.

    Чистим ДО обрезки, поэтому в окно попадает суть события, а не служебный
    текст: запрос выходит короче, а видно модели — больше.
    """
    text = " ".join(str(summary or "").split()).strip()
    title = str(title or "").strip()
    if title and text[:len(title)].lower() == title.lower():
        text = text[len(title):].lstrip(" .:;-—–|»)")
    previous = None
    while previous != text:            # хвостов бывает два подряд
        previous = text
        text = _TAIL_MARK.sub("", _TAIL_PHRASES.sub("", text).strip()).strip()
    return text[:limit]


def signature(text: str) -> str:
    """Множество содержательных слов. Для коротких заголовков это работает
    заметно надёжнее SimHash: перефразировка рушит все шинглы, а слова остаются."""
    tokens = re.findall(r"[a-zа-яё0-9]+", (text or "").lower())
    return " ".join(sorted({t for t in tokens if len(t) > 1 and t not in STOPWORDS}))


def sim_sets(a: set, b: set) -> float:
    """0.5*Жаккар + 0.5*перекрытие. Перекрытие спасает, когда один заголовок
    заметно длиннее другого — частый случай у агрегаторов.

    Принимает готовые множества слов: в выпуске по десятку разделов одни и те
    же сигнатуры сравниваются тысячи раз, и разбор строки каждый раз заметен.
    """
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return 0.5 * (inter / len(a | b)) + 0.5 * (inter / min(len(a), len(b)))


def similarity(sig_a: str, sig_b: str) -> float:
    return sim_sets(set(sig_a.split()), set(sig_b.split()))
