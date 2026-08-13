# -*- coding: utf-8 -*-
"""Конвейер выпуска: взять свежее из базы → отранжировать → написать → отправить."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import config, subscribers
from .config import CFG, local_now, log, now_iso
from .feedback import persona_hint, weighted_prescore
from .llm import LLMError, llm_cost, rank_clusters, summarize
from .profiles import PROFILES, profile
from .rank import already_sent, cluster, primary_of, select
from .render import feedback_keyboard, fit_message
from .sources import sources_for
from .storage import db, log_run, meta_set, save_leftover
from .telegram import plain, tg_send


def remember_leftover(conn, chat_id, ranking, shortlist, picked) -> None:
    """Сохраняет кандидатов, которые модель оценила, но выпуск их не вместил.

    Их показывает команда /more — и это бесплатно: ранжирование уже оплачено.
    """
    chosen = {id(group) for group, _score, _cat in picked}
    rows = []
    for entry in ranking:
        try:
            idx = int(entry.get("id", -1))
            score = float(entry.get("score") or 0)
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(shortlist):
            continue
        group = shortlist[idx]
        if id(group) in chosen:
            continue
        main = primary_of(group)
        rows.append({"url_hash": main["url_hash"], "title": main["title"],
                     "url": main["url"], "source_id": main["source_id"],
                     "category": entry.get("category") or main["category"],
                     "score": score})
    save_leftover(conn, chat_id, rows[:30])


def fresh_items(conn, topic):
    """Свежие материалы, относящиеся к теме этого читателя.

    Материалы собираются сразу по всем темам подписчиков, поэтому чужие
    источники надо отсечь. Hacker News общий для всех — его записи
    проверяем по ключевым словам темы.
    """
    window = (datetime.now(timezone.utc)
              - timedelta(hours=CFG["window_hours"])).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE fetched_at > ? ORDER BY published_at DESC",
        (window,))]
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


def build_and_send(dry_run=False, chat_id=None, sub=None) -> dict:
    """Собирает и отправляет выпуск одному читателю."""
    if sub is not None and chat_id is None:
        chat_id = sub["chat_id"]
    chat_id = str(chat_id or config.TG_CHAT)
    with subscribers.overlay(sub):
        return _build_and_send(dry_run, chat_id)


def _build_and_send(dry_run, chat_id) -> dict:
    conn = db()
    stats = {"candidates": 0, "clusters": 0, "selected": 0, "sent": 0, "cost": 0.0}
    prof = profile()

    rows = fresh_items(conn, CFG["topic"])
    stats["candidates"] = len(rows)
    if not rows:
        log.warning("Нет свежих материалов — дайджест не формируется")
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    groups = cluster(rows, CFG["similarity"])
    fresh = [g for g in groups
             if not already_sent(conn, g, CFG["similarity"], chat_id)]
    stats["clusters"] = len(fresh)
    if not fresh:
        log.warning("После дедупликации новых новостей не осталось")
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    # прескоринг с поправкой на вкусы: реакции читателя решают, кто вообще
    # доедет до модели, — а это самый дешёвый способ учесть обратную связь
    ordering = weighted_prescore(conn, chat_id)
    shortlist = sorted(fresh, key=ordering, reverse=True)[: CFG["llm_candidates"]]
    persona = prof["persona"] + persona_hint(conn, chat_id)

    # 1) ранжирование
    try:
        ranking, usage = rank_clusters(shortlist, persona)
        stats["cost"] += llm_cost(usage)
    except LLMError as exc:                 # деградируем, но выпуск не срываем
        log.error("Ранжирование не удалось (%s) — беру порядок прескоринга", exc)
        ranking = [{"id": i, "score": 7.0, "category": primary_of(g)["category"]}
                   for i, g in enumerate(shortlist)]

    picked = select(ranking, shortlist)
    remember_leftover(conn, chat_id, ranking, shortlist, picked)
    if not picked:
        log.warning("Ничего не прошло порог важности %.1f — тихий день", CFG["min_score"])
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats
    if len(picked) < CFG["min_items"]:
        log.info("Отобрано только %d новостей — это нормально для тихого дня", len(picked))

    # 2) саммари одним запросом на весь выпуск
    try:
        cards_map, usage = summarize(picked, persona, CFG["language"])
        stats["cost"] += llm_cost(usage)
    except LLMError as exc:
        log.error("Саммари не удалось (%s) — публикую исходные заголовки", exc)
        cards_map = {}

    cards = []
    for idx, (group, score, category) in enumerate(picked):
        main = primary_of(group)
        card = cards_map.get(idx) or {"headline": main["title"],
                                      "what": main["summary"][:300], "why": ""}
        cards.append((card, group, score, category))
    stats["selected"] = len(cards)

    messages = fit_message(cards, stats["candidates"])

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
    for _card, group, _score, category in cards:
        main = primary_of(group)
        conn.execute("INSERT OR IGNORE INTO sent(chat_id,url_hash,sig,title,url,"
                     "digest_date,sent_at,source_id,category) VALUES (?,?,?,?,?,?,?,?,?)",
                     (chat_id, main["url_hash"], main["sig"], main["title"],
                      main["url"], day, now_iso(), main["source_id"], category))
        for item in group:
            # 'sent' здесь значит «кому-то уже уходило» и бережёт материал от
            # уборки; персональный дедуп живёт в таблице sent
            conn.execute("UPDATE items SET state='sent' WHERE url_hash=?",
                         (item["url_hash"],))
    conn.commit()
    subscribers.set_last_digest(conn, chat_id, day)
    meta_set(conn, "last_digest_date", day)
    log_run(conn, "digest", "ok", stats)
    conn.close()
    log.info("Отправлено: %d новостей, %d сообщение(й), ~$%.4f",
             stats["selected"], stats["sent"], stats["cost"])
    return stats
