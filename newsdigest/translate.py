# -*- coding: utf-8 -*-
"""Язык выпуска: читатель видит новость по-русски, откуда бы она ни пришла.

Источники намеренно международные — Reuters, Phoronix, Nature, arXiv: надёжность
важнее языка, и отказываться от первоисточника ради русской ленты нельзя. Но в
русском дайджесте английская карточка — это брак, а не «новость как есть».

Просьбы в промпте для этого мало. Модель почти всегда пишет на нужном языке, но
на английском источнике нет-нет да и оставит заголовок нетронутым, а когда
саммари не написалось вовсе, в выпуск идёт заголовок прямо из фида — тогда
половина ленты оказывается на латинице. Поэтому язык проверяется ПОСЛЕ модели:

    1) в готовой карточке ищем текст, на языке выпуска не написанный;
    2) всё найденное уходит в модель одним запросом на перевод;
    3) перевод оседает в таблице `translations` — та же новость другому
       подписчику, а потом и в /more, и в /saved достаётся уже без запроса.

Проверка алфавитом работает только для языка с собственной письменностью, то
есть у нас — для русского. Читателю, выбравшему `/set language english`, мы
ничего не переводим: определять произвольный язык эвристикой не выйдет, а модель
и так пишет карточку на его языке.
"""
from __future__ import annotations

import hashlib
import re

from .config import CFG, log, now_iso
from .llm import LLMError, llm_cost, translate_texts

#: поля карточки, которые видит читатель
FIELDS = ("headline", "what", "why")

CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
#: любая буква любого алфавита (цифры и знаки не в счёт)
LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)

#: тексты короче не судим: «GPT-5», «Redis 8.0» и «Nvidia Rubin» не принадлежат
#: ни одному языку, а переводить их нечего
MIN_LETTERS = 12
#: доля кириллицы среди букв, ниже которой текст считаем непереведённым. Порог
#: низкий нарочно: «GitHub Copilot Workspace вышел в открытый доступ» — обычный
#: русский заголовок, и кириллицы в нём меньше половины
MIN_SHARE = 0.3

#: как в настройке может быть назван русский язык
RUSSIAN = ("рус", "rus", "ru")


def language_of(language=None) -> str:
    return str(CFG["language"] if language is None else language).strip().lower()


def checkable(language=None) -> bool:
    """Умеем ли мы проверить язык этого выпуска глазами, без запроса к модели."""
    return language_of(language).startswith(RUSSIAN)


def share(text) -> float:
    """Доля кириллицы среди букв. У строки без букв доли нет — считаем её своей."""
    letters = LETTERS.findall(str(text or ""))
    if not letters:
        return 1.0
    return len(CYRILLIC.findall(str(text))) / float(len(letters))


def foreign(text, language=None) -> bool:
    """Текст явно не на языке выпуска — значит, читателю его показывать рано."""
    if not checkable(language):
        return False
    if len(LETTERS.findall(str(text or ""))) < MIN_LETTERS:
        return False
    return share(text) < MIN_SHARE


def improved(source, result, language=None) -> bool:
    """Ответ модели — перевод, а не эхо исходной строки.

    Отвергать всё, что и после перевода выглядит нерусским, нельзя: заголовок
    из одних имён («Nvidia Rubin: CES 2026 keynote») по-русски тоже останется
    наполовину латинским, и такой перевод — лучшее, что бывает. Поэтому смотрим
    не на порог, а на разницу: кириллицы должно стать заметно больше.
    """
    source, result = str(source or "").strip(), str(result or "").strip()
    if not result or result.lower() == source.lower():
        return False
    return not foreign(result, language) or share(result) - share(source) >= 0.2


# ------------------------------------------------------------------------ кэш
def key_of(text) -> str:
    return hashlib.sha256(str(text).strip().encode("utf-8")).hexdigest()[:32]


#: сколько хэшей кладём в один IN (...) — у SQLite потолок на число параметров
LOOKUP = 200
#: длиннее в кэше не храним: карточка и так короче
KEEP = 600


def cached(conn, texts, language=None) -> dict:
    """Что из этих строк уже переведено раньше: {исходный текст: перевод}."""
    keys = {key_of(text): text for text in texts if str(text).strip()}
    if not keys:
        return {}
    lang, order, out = language_of(language), list(keys), {}
    for start in range(0, len(order), LOOKUP):
        part = order[start:start + LOOKUP]
        rows = conn.execute(
            "SELECT src_hash, text FROM translations WHERE lang=? AND src_hash IN (%s)"
            % ",".join("?" * len(part)), [lang] + part)
        for row in rows:
            out[keys[row["src_hash"]]] = row["text"]
    return out


def remember(conn, pairs, language=None) -> None:
    """Кладёт переводы в кэш: тот же заголовок завтра достанется бесплатно."""
    lang = language_of(language)
    rows = [(lang, key_of(src), str(src)[:KEEP], str(dst)[:KEEP], now_iso())
            for src, dst in pairs if str(src).strip() and str(dst).strip()]
    if not rows:
        return
    conn.executemany(
        "INSERT INTO translations(lang,src_hash,src,text,at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(lang,src_hash) DO UPDATE SET text=excluded.text, at=excluded.at",
        rows)
    conn.commit()


def remember_headlines(conn, pairs, language=None) -> None:
    """Запоминает, как заголовок новости выглядел на языке выпуска.

    Карточка — не дословный перевод заголовка, но в /more и в закладках читателю
    нужен ровно тот текст, который он видел в выпуске, а не строка из фида.
    """
    remember(conn, [(src, dst) for src, dst in pairs
                    if src and dst and src != dst and foreign(src, language)],
             language)


def known(conn, text, language=None) -> str:
    """Перевод из кэша (или исходный текст, если его там нет).

    К модели не ходим: /more и /saved отвечают на команду сразу, и ждать
    перевода читатель в этот момент не должен.
    """
    text = str(text or "")
    if not foreign(text, language):
        return text
    row = conn.execute("SELECT text FROM translations WHERE lang=? AND src_hash=?",
                       (language_of(language), key_of(text))).fetchone()
    return row["text"] if row else text


# -------------------------------------------------------------------- перевод
def translated(conn, texts, language=None) -> tuple:
    """{исходный текст: перевод} и стоимость. Сначала кэш, к модели — за остальным.

    Ответ, не ставший русским, не берём и в кэш не кладём: модель иногда
    возвращает исходную строку, и запомнить такое значит закрепить английский
    заголовок навсегда.
    """
    ready = cached(conn, texts, language)
    missing = [text for text in dict.fromkeys(texts)
               if str(text).strip() and text not in ready]
    size, cost = max(1, int(CFG["translate_batch"])), 0.0
    for start in range(0, len(missing), size):
        part = missing[start:start + size]
        try:
            got, usage = translate_texts(part, language_of(language))
        except LLMError as exc:
            log.warning("Перевод %d строк(и) не удался (%s) — оставляю оригинал",
                        len(part), exc)
            continue
        cost += llm_cost(usage)
        fresh = {part[idx]: text for idx, text in got.items()
                 if improved(part[idx], text, language)}
        remember(conn, fresh.items(), language)
        ready.update(fresh)
    return ready, cost


def localize(conn, cards, language=None) -> float:
    """Догоняет карточки до языка выпуска. Возвращает стоимость перевода.

    Карточки правятся на месте — это последняя проверка перед отправкой, и
    дальше по конвейеру уходит уже русский текст.
    """
    if not CFG["translate"]:
        return 0.0
    spots = [(card, field, str(card.get(field) or ""))
             for card in cards for field in FIELDS
             if foreign(card.get(field), language)]
    if not spots:
        return 0.0
    ready, cost = translated(conn, [text for _card, _field, text in spots], language)
    done = 0
    for card, field, text in spots:
        if ready.get(text):
            card[field] = ready[text]
            done += 1
    log.info("Перевёл на язык выпуска: %d фрагмент(ов) из %d", done, len(spots))
    return cost


def localize_titles(conn, rows, language=None) -> float:
    """То же для голых заголовков — хвоста выпуска, который покажет /more."""
    if not CFG["translate"]:
        return 0.0
    spots = [row for row in rows if foreign(row.get("title"), language)]
    if not spots:
        return 0.0
    ready, cost = translated(conn, [row["title"] for row in spots], language)
    for row in spots:
        row["title"] = ready.get(row["title"], row["title"])
    return cost
