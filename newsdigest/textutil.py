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
