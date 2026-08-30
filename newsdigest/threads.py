# -*- coding: utf-8 -*-
"""Сюжетные цепочки: чем эта новость продолжает вчерашнюю.

Дедупликация отвечает на вопрос «показывать ли вторую новость»: одно и то же
событие читателю дважды не нужно. Но чаще всего ответ — «показывать», и за ним
прячутся два очень разных случая:

    «Землетрясение M7 у берегов Японии» / «Землетрясение: 200 погибших»
    «Nvidia отчиталась за квартал»      / «Nvidia показала новую видеокарту»

В первой паре вторая новость — следующий шаг того же сюжета, и читателю,
который видел первую, она читается иначе. Во второй общего только компания.
Отбору эта разница не нужна — показываем обе, — а читателю нужна, и модель её
уже проводит: `llm.MORE` («то же событие, счётчик сдвинулся») и `llm.NEXT`
(«другое событие того же сюжета») — это и есть цепочка, а `llm.OTHER` — нет
(см. `llm.DUP_SYSTEM`). Лишнего вопроса это не стоит: пара и так перед
моделью.

Здесь то, что из этого поля вышло. `dupes` помнит приговор про пару сигнатур —
он общий для всех подписчиков. `threads` помнит связь между двумя строками
`sent` конкретного читателя: у каждого своя история, а значит, и своя цепочка.
Запись появляется в момент отправки (`pipeline`, `breaking`): пока новость не
ушла, связывать нечего.

Цепочка строится переходом от новой новости к старой и обрывается на первой,
у которой предшественника нет. Направление задано временем, поэтому петли не
бывает; на всякий случай обход всё равно помнит, где уже был, — база живёт
долго, а строка в неё может попасть и не отсюда.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import now_iso

#: Сколько предшественников показываем под карточкой. Цепочка иногда вырастает
#: длинной (землетрясение живёт в лентах неделю), но читателю под новостью
#: нужен не архив, а память: три шага назад — это «с чего всё началось» и пара
#: заметных поворотов между.
DEPTH = 3

#: Сюжетов в блоке «Сюжеты недели» и минимальная длина, при которой сюжет туда
#: попадает: цепочка из двух — это ещё не сюжет, а просто связанная пара.
TOP = 5
TOP_MIN = 3


def remember(conn, chat_id, url_hash, prior) -> None:
    """Связывает новость с той, которую она продолжает.

    Молчит, если связывать нечего или не с чем: `prior` берётся из приговора
    модели, а приговор бывает и пустым.
    """
    if not url_hash or not prior or url_hash == prior:
        return
    conn.execute(
        "INSERT OR IGNORE INTO threads(chat_id, url_hash, prior, at) "
        "VALUES (?,?,?,?)", (str(chat_id), url_hash, prior, now_iso()))


def links(conn, chat_id) -> dict:
    """Все связи читателя: новость -> та, которую она продолжает.

    Читаются разом и целиком. Связей у читателя за два месяца набираются
    десятки — на порядки меньше самой истории, — а лента строит цепочки сразу
    для двадцати карточек, и запрос на каждую был бы дороже всей таблицы.
    """
    return {row["url_hash"]: row["prior"] for row in conn.execute(
        "SELECT url_hash, prior FROM threads WHERE chat_id=? ORDER BY at",
        (str(chat_id),))}


def walk(priors, url_hash, depth=DEPTH) -> list:
    """Предшественники новости, от ближайшего к самому раннему."""
    out, seen, at = [], {url_hash}, priors.get(url_hash, "")
    while at and at not in seen and len(out) < depth:
        out.append(at)
        seen.add(at)
        at = priors.get(at, "")
    return out


def rows_by_hash(conn, chat_id, hashes) -> dict:
    """Строки истории по их хэшам — тем же одним запросом на всю страницу."""
    out, hashes = {}, [h for h in dict.fromkeys(hashes) if h]
    for start in range(0, len(hashes), 400):     # SQLite не любит длинные IN
        part = hashes[start:start + 400]
        marks = ",".join("?" * len(part))
        for row in conn.execute(
                "SELECT url_hash, title, headline, url, section, sent_at "
                "FROM sent WHERE chat_id=? AND url_hash IN (%s)" % marks,
                [str(chat_id)] + part):
            out[row["url_hash"]] = row
    return out


def earlier(conn, chat_id, hashes, depth=DEPTH) -> dict:
    """«Ранее по теме» для пачки новостей: хэш -> список предшественников.

    Каждый предшественник — словарь с заголовком, ссылкой и временем. У
    новостей без цепочки ключа в ответе нет вовсе: пустой список под карточкой
    и его отсутствие рисуются одинаково, а лишний ключ пришлось бы отличать.
    """
    hashes = [h for h in dict.fromkeys(hashes) if h]
    if not hashes:
        return {}
    priors = links(conn, chat_id)
    if not priors:
        return {}

    chains = {h: walk(priors, h, depth) for h in hashes}
    chains = {h: chain for h, chain in chains.items() if chain}
    if not chains:
        return {}

    known = rows_by_hash(conn, chat_id, [h for chain in chains.values()
                                         for h in chain])
    out = {}
    for url_hash, chain in chains.items():
        # звенья, вычищенные из истории по сроку хранения, пропускаем молча:
        # цепочка от этого становится короче, а не рвётся
        step = [{"hash": h,
                 "title": known[h]["headline"] or known[h]["title"],
                 "url": known[h]["url"] or "",
                 "section": known[h]["section"] or "",
                 "at": known[h]["sent_at"] or ""}
                for h in chain if h in known]
        if step:
            out[url_hash] = step
    return out


def roots(priors) -> dict:
    """Начало сюжета для каждой новости: новость -> самая ранняя в её цепочке.

    Цепочка обходится целиком, без `DEPTH`: сюжет считается по всей длине, а
    под карточкой показывается только его хвост.
    """
    out = {}
    for url_hash in priors:
        chain = walk(priors, url_hash, depth=len(priors) + 1)
        out[url_hash] = chain[-1] if chain else url_hash
    return out


def top(conn, chat_id, days=7, limit=TOP) -> list:
    """Сюжеты, которые развивались последнее время.

    Возвращает список словарей: с чего сюжет начался, сколько в нём новостей и
    когда пришла последняя. Сортировка — по свежести последней новости, а не
    по длине: читателю важно, что происходит сейчас, а не что было самым
    громким за неделю.
    """
    priors = links(conn, chat_id)
    if not priors:
        return []
    origin = roots(priors)

    # в сюжет входит и та новость, с которой он начался: она в `priors` не
    # значится, потому что сама ничего не продолжает
    members = {}
    for url_hash, root in origin.items():
        members.setdefault(root, set()).update((url_hash, root))
    members = {root: nodes for root, nodes in members.items()
               if len(nodes) >= TOP_MIN}
    if not members:
        return []

    known = rows_by_hash(conn, chat_id,
                         [h for nodes in members.values() for h in nodes])
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()

    out = []
    for root, nodes in members.items():
        rows = [known[h] for h in nodes if h in known]
        if root not in known or len(rows) < TOP_MIN:
            continue                    # начало сюжета вычищено — показать нечего
        last = max((row["sent_at"] or "") for row in rows)
        if last < since:
            continue
        out.append({"hash": root,
                    "title": known[root]["headline"] or known[root]["title"],
                    "section": known[root]["section"] or "",
                    "count": len(rows),
                    "at": last})
    out.sort(key=lambda s: s["at"], reverse=True)
    return out[:max(1, int(limit))]
