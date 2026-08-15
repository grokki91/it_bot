# -*- coding: utf-8 -*-
"""Конвейер выпуска: взять свежее из базы → отранжировать → написать → отправить.

Выпуск собирается по разделам. По расписанию это подборка «по паре новостей
из каждого раздела», по команде /news — топ одного раздела. Механика общая:

    1) материалы за окно читаются из базы ОДИН раз на весь выпуск;
    2) каждый раздел кластеризует свои материалы и отбирает кандидатов;
    3) модель ранжирует кандидаты каждого раздела своим портретом читателя
       (разделы идут параллельно — иначе выпуск из полутора десятков разделов
       собирался бы минуты);
    4) разделы разбираются по очереди, и всё уже занятое пропускается —
       поэтому одна и та же новость не приходит дважды под двумя вывесками;
    5) карточки пишутся пачками и уходят одним-двумя сообщениями.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import config, sections, subscribers
from .config import CFG, local_now, log, now_iso
from .feedback import persona_hint, weighted_prescore
from .llm import LLMError, llm_cost, rank_clusters, summarize
from .profiles import PROFILES, title
from .rank import SentIndex, cluster, primary_of, select
from .render import feedback_keyboard, fit_blocks
from .sources import sources_for
from .storage import db, log_run, meta_set, save_leftover
from .telegram import plain, tg_send


def fresh_rows(conn):
    """Все материалы за окно свежести — один запрос на весь выпуск."""
    window = (datetime.now(timezone.utc)
              - timedelta(hours=CFG["window_hours"])).isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE fetched_at > ? ORDER BY published_at DESC",
        (window,))]


def for_topic(rows, topic):
    """Материалы, относящиеся к этому разделу.

    Собирается сразу по всем разделам всех подписчиков, поэтому чужие
    источники надо отсечь. Hacker News общий для всех — его записи
    проверяем по ключевым словам раздела.
    """
    allowed = sources_for(topic)
    keywords = [k.lower() for k in PROFILES.get(topic, {}).get("keywords", [])]
    out = []
    for row in rows:
        if row["source_id"] in allowed:
            out.append(row)
        elif row["source_id"] == "hackernews" and any(
                k in row["title"].lower() for k in keywords):
            out.append(row)
    return out


def fresh_items(conn, topic):
    """Свежие материалы одного раздела (используется срочными новостями)."""
    return for_topic(fresh_rows(conn), topic)


def shortlist_for(rows, topic, index, ordering, limit):
    """Кандидаты раздела: кластеры, которых читатель ещё не видел."""
    items = for_topic(rows, topic)
    if not items:
        return []
    fresh = [g for g in cluster(items, CFG["similarity"])
             if not index.seen(g, CFG["similarity"])]
    return sorted(fresh, key=ordering, reverse=True)[:limit]


def rank_shortlist(shortlist, persona):
    """Оценка модели. Она упала — идём по прескорингу, выпуск не срываем."""
    try:
        ranking, usage = rank_clusters(shortlist, persona)
        return ranking, llm_cost(usage)
    except LLMError as exc:
        log.error("Ранжирование не удалось (%s) — беру порядок прескоринга", exc)
        return ([{"id": i, "score": 7.0, "category": primary_of(g)["category"]}
                 for i, g in enumerate(shortlist)], 0.0)


def limits_for(count):
    """Лимиты отбора внутри раздела.

    Пара новостей раздела должна прийти из разных источников, иначе в выпуске
    оказываются два материала одного сайта об одном и том же. При большом
    запросе лимит ослабевает — иначе просто нечем набрать десятку.
    """
    return {"limit": count, "min_items": count,
            "per_source": max(1, count // 2), "per_category": max(2, count)}


def leftover_rows(ranking, shortlist, picked):
    """Кандидаты, которых модель оценила, но выпуск не вместил — запас для /more."""
    chosen = {id(group) for group, _score, _cat in picked}
    rows = []
    for entry in ranking:
        try:
            idx = int(entry.get("id", -1))
            score = float(entry.get("score") or 0)
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(shortlist) or id(shortlist[idx]) in chosen:
            continue
        main = primary_of(shortlist[idx])
        rows.append({"url_hash": main["url_hash"], "title": main["title"],
                     "url": main["url"], "source_id": main["source_id"],
                     "category": entry.get("category") or main["category"],
                     "score": score})
    return rows


def usable(ranking, shortlist, index):
    """Отбрасывает кандидатов, которых уже занял соседний раздел."""
    out = []
    for entry in ranking:
        try:
            idx = int(entry.get("id", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(shortlist) and not index.seen(shortlist[idx],
                                                        CFG["similarity"]):
            out.append(entry)
    return out


# --------------------------------------------------------------------- выпуск
def build_and_send(dry_run=False, chat_id=None, sub=None, topics=None,
                   count=None, close_day=True) -> dict:
    """Собирает и отправляет выпуск одному читателю.

    topics — какие разделы взять (по умолчанию разделы подписчика),
    count — сколько новостей на раздел (по умолчанию /set each).
    """
    if sub is not None and chat_id is None:
        chat_id = sub["chat_id"]
    chat_id = str(chat_id or config.TG_CHAT)
    with subscribers.overlay(sub):
        plan = list(topics) if topics else sections.plan(sub)
        per = count if count else per_section_count(plan, sub)
        return _build_and_send(dry_run, chat_id, plan, per, close_day, sub)


def per_section_count(plan, sub) -> int:
    """Сколько новостей на раздел. Единственный раздел получает полный выпуск —
    иначе «читаю только спорт» означало бы две новости в день."""
    if len(plan) > 1:
        return sections.per_section(sub)
    return max(CFG["max_items"], sections.per_section(sub))


def _build_and_send(dry_run, chat_id, plan, count, close_day, sub=None) -> dict:
    conn = db()
    stats = {"candidates": 0, "clusters": 0, "selected": 0, "sent": 0,
             "cost": 0.0, "sections": 0, "empty": []}
    # какой выпуск суток собираем, решаем на входе: сборка занимает минуты, и
    # начатый в 20:59 выпуск должен закрыть свой слот, а не следующий
    mark = subscribers.slot_mark(sub)
    plan = [topic for topic in plan if topic in PROFILES]
    if not plan:
        log.warning("Не задано ни одного раздела — выпуск не формируется")
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    rows = fresh_rows(conn)
    stats["candidates"] = len(rows)
    if not rows:
        log.warning("Нет свежих материалов — дайджест не формируется")
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    # прескоринг с поправкой на вкусы: реакции читателя решают, кто вообще
    # доедет до модели, — а это самый дешёвый способ учесть обратную связь
    ordering = weighted_prescore(conn, chat_id)
    hint = persona_hint(conn, chat_id)
    index = SentIndex(conn, chat_id)

    shortlists = [(topic, shortlist_for(rows, topic, index, ordering,
                                        CFG["section_candidates"]))
                  for topic in plan]
    stats["clusters"] = sum(len(s) for _topic, s in shortlists)
    if not stats["clusters"]:
        log.warning("После дедупликации новых новостей не осталось")
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    rankings = rank_all(shortlists, hint, stats)

    blocks, spare = [], []
    limits = limits_for(count)
    for topic, shortlist in shortlists:
        ranking = rankings.get(topic) or []
        picked = select(usable(ranking, shortlist, index), shortlist, **limits)
        for group, _score, _cat in picked:
            index.remember(group)
        spare.extend(leftover_rows(ranking, shortlist, picked))
        if picked:
            blocks.append((topic, picked))
        else:
            stats["empty"].append(topic)

    save_leftover(conn, chat_id, sorted(
        spare, key=lambda r: -r["score"])[:30])
    if not blocks:
        log.warning("Ничего не прошло порог важности %.1f — тихий день",
                    CFG["min_score"])
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    cards = write_cards(blocks, plan, stats)
    stats["selected"] = sum(len(block) for _topic, block in cards)
    stats["sections"] = len(cards)

    messages = fit_blocks(cards, stats["candidates"], note=empty_note(stats))

    if dry_run:
        print()
        print(("\n" + "─" * 60 + "\n").join(plain(text) for text, _ in messages))
        print("\n[dry-run] отправки не было. Примерная стоимость запроса: $%.4f"
              % stats["cost"])
        log_run(conn, "digest", "dry-run", stats)
        conn.close()
        return stats

    for text, chunk in messages:
        tg_send(chat_id, text, keyboard=feedback_keyboard(chunk))
        stats["sent"] += 1
        time.sleep(1.0)

    day = local_now().strftime("%Y-%m-%d")
    for topic, block in cards:
        for _card, group, _score, category in block:
            main = primary_of(group)
            conn.execute(
                "INSERT OR IGNORE INTO sent(chat_id,url_hash,sig,title,url,"
                "digest_date,sent_at,source_id,category) VALUES (?,?,?,?,?,?,?,?,?)",
                (chat_id, main["url_hash"], main["sig"], main["title"],
                 main["url"], day, now_iso(), main["source_id"], category))
            for item in group:
                # 'sent' здесь значит «кому-то уже уходило» и бережёт материал
                # от уборки; персональный дедуп живёт в таблице sent
                conn.execute("UPDATE items SET state='sent' WHERE url_hash=?",
                             (item["url_hash"],))
    conn.commit()
    if close_day:
        # метка — «дата#номер выпуска в сутках»: следующий выпуск дня ждёт
        # своего часа, а этот повторно не соберётся
        subscribers.set_last_digest(conn, chat_id, mark)
        meta_set(conn, "last_digest_date", day)
    log_run(conn, "digest", "ok", stats)
    conn.close()
    log.info("Отправлено: %d новостей из %d разделов, %d сообщение(й), ~$%.4f",
             stats["selected"], stats["sections"], stats["sent"], stats["cost"])
    return stats


def rank_all(shortlists, hint, stats) -> dict:
    """Ранжирует разделы. Каждый — своим портретом читателя, все — параллельно.

    Параллельность здесь про ожидание сети: полтора десятка последовательных
    запросов к модели складываются в минуты, а выпуск нужен к завтраку.
    """
    jobs = [(topic, shortlist) for topic, shortlist in shortlists if shortlist]

    def one(job):
        topic, shortlist = job
        persona = PROFILES[topic]["persona"] + hint
        return topic, rank_shortlist(shortlist, persona)

    if len(jobs) <= 1:
        results = [one(job) for job in jobs]
    else:
        workers = max(1, min(int(CFG["section_workers"]), len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, jobs))

    out = {}
    for topic, (ranking, cost) in results:
        out[topic] = ranking
        stats["cost"] += cost
    return out


def write_cards(blocks, plan, stats):
    """Просит модель написать карточки на весь выпуск и раскладывает их обратно."""
    flat = [pick for _topic, picked in blocks for pick in picked]
    try:
        cards_map, usage = summarize(flat, sections.persona(plan), CFG["language"])
        stats["cost"] += llm_cost(usage)
    except LLMError as exc:
        log.error("Саммари не удалось (%s) — публикую исходные заголовки", exc)
        cards_map = {}

    out, idx = [], 0
    for topic, picked in blocks:
        block = []
        for group, score, category in picked:
            main = primary_of(group)
            card = cards_map.get(idx) or {"headline": main["title"],
                                          "what": main["summary"][:300], "why": ""}
            block.append((card, group, score, category))
            idx += 1
        out.append((topic, block))
    return out


def empty_note(stats) -> str:
    """Строка про разделы, где сегодня нового не нашлось. Молчание хуже: без
    неё непонятно, бот пропустил раздел или в нём правда пусто."""
    empty = stats.get("empty") or []
    if not empty or not stats.get("sections"):
        return ""
    if len(empty) > 4:
        return "без новостей: %d раздела(ов)" % len(empty)
    return "без новостей: " + ", ".join(title(topic) for topic in empty)


# --------------------------------------------------------- один раздел по запросу
def build_section(topic, count=0, chat_id=None, sub=None, dry_run=False) -> dict:
    """Топ раздела по запросу: /news спорт 10. День выпуска не закрывает."""
    count = max(1, min(int(count or CFG["section_items"]),
                       int(CFG["section_max_items"])))
    return build_and_send(dry_run=dry_run, chat_id=chat_id, sub=sub,
                          topics=[topic], count=count, close_day=False)
