# -*- coding: utf-8 -*-
"""Конвейер выпуска: взять свежее из базы → отранжировать → написать → отправить."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import config
from .config import CFG, local_now, log, now_iso
from .llm import LLMError, llm_cost, rank_clusters, summarize
from .profiles import profile
from .rank import already_sent, cluster, prescore, primary_of, select
from .render import fit_message
from .storage import db, log_run, meta_set
from .telegram import plain, tg_send


def build_and_send(dry_run=False) -> dict:
    conn = db()
    stats = {"candidates": 0, "clusters": 0, "selected": 0, "sent": 0, "cost": 0.0}
    prof = profile()

    window = (datetime.now(timezone.utc)
              - timedelta(hours=CFG["window_hours"])).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE fetched_at > ? AND state != 'sent' "
        "ORDER BY published_at DESC", (window,))]
    stats["candidates"] = len(rows)
    if not rows:
        log.warning("Нет свежих материалов — дайджест не формируется")
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    groups = cluster(rows, CFG["similarity"])
    fresh = [g for g in groups if not already_sent(conn, g, CFG["similarity"])]
    stats["clusters"] = len(fresh)
    if not fresh:
        log.warning("После дедупликации новых новостей не осталось")
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats

    shortlist = sorted(fresh, key=prescore, reverse=True)[: CFG["llm_candidates"]]

    # 1) ранжирование
    try:
        ranking, usage = rank_clusters(shortlist, prof["persona"])
        stats["cost"] += llm_cost(usage)
    except LLMError as exc:                 # деградируем, но выпуск не срываем
        log.error("Ранжирование не удалось (%s) — беру порядок прескоринга", exc)
        ranking = [{"id": i, "score": 7.0, "category": primary_of(g)["category"]}
                   for i, g in enumerate(shortlist)]

    picked = select(ranking, shortlist)
    if not picked:
        log.warning("Ничего не прошло порог важности %.1f — тихий день", CFG["min_score"])
        log_run(conn, "digest", "empty", stats)
        conn.close()
        return stats
    if len(picked) < CFG["min_items"]:
        log.info("Отобрано только %d новостей — это нормально для тихого дня", len(picked))

    # 2) саммари одним запросом на весь выпуск
    try:
        cards_map, usage = summarize(picked, prof["persona"], CFG["language"])
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
        print(("\n" + "─" * 60 + "\n").join(plain(m) for m in messages))
        print("\n[dry-run] отправки не было. Примерная стоимость запроса: $%.4f"
              % stats["cost"])
        log_run(conn, "digest", "dry-run", stats)
        conn.close()
        return stats

    for text in messages:
        tg_send(config.TG_CHAT, text)
        stats["sent"] += 1
        time.sleep(1.0)

    day = local_now().strftime("%Y-%m-%d")
    for _card, group, _score, _cat in cards:
        main = primary_of(group)
        conn.execute("INSERT OR IGNORE INTO sent(url_hash,sig,title,url,digest_date,sent_at)"
                     " VALUES (?,?,?,?,?,?)",
                     (main["url_hash"], main["sig"], main["title"], main["url"],
                      day, now_iso()))
        for item in group:
            conn.execute("UPDATE items SET state='sent' WHERE url_hash=?",
                         (item["url_hash"],))
    conn.commit()
    meta_set(conn, "last_digest_date", day)
    log_run(conn, "digest", "ok", stats)
    conn.close()
    log.info("Отправлено: %d новостей, %d сообщение(й), ~$%.4f",
             stats["selected"], stats["sent"], stats["cost"])
    return stats
