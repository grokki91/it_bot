# -*- coding: utf-8 -*-
"""Срочные новости: то, что не должно ждать до утреннего выпуска.

Логика намеренно консервативная. Ложное «срочно» в час ночи раздражает
сильнее, чем десяток непойманных событий, поэтому кандидат обязан пройти
три независимые проверки:

    1) подтверждение — о событии за окно написали несколько РАЗНЫХ сайтов,
       и хотя бы один из них первоисточник (либо оно взорвало Hacker News);
    2) оценка модели — тот же промпт ранжирования, порог заметно выше
       обычного (breaking_min_score);
    3) приличия — тихие часы, лимит на сутки и пауза рассылки.

Отправленное сразу попадает в историю `sent`, поэтому в утреннем выпуске
оно уже не повторится.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config
from .config import CFG, local_now, log, now_iso
from .feedback import persona_hint
from .llm import LLMError, llm_cost, rank_clusters, summarize
from .profiles import profile
from .rank import already_sent, cluster, prescore, primary_of
from .render import breaking_card, feedback_keyboard
from .storage import db, log_run, meta_get, meta_set
from .telegram import tg_send

#: сколько кандидатов максимум показываем модели за один заход
MAX_CANDIDATES = 3


def parse_quiet(value: str):
    """'23:00-08:00' -> (1380, 480). Пусто или мусор -> None (тихих часов нет)."""
    try:
        start, end = str(value or "").split("-")
        sh, sm = [int(x) for x in start.strip().split(":")]
        eh, em = [int(x) for x in end.strip().split(":")]
    except (ValueError, AttributeError):
        return None
    return sh * 60 + sm, eh * 60 + em


def in_quiet_hours(now=None) -> bool:
    window = parse_quiet(CFG["breaking_quiet"])
    if not window:
        return False
    start, end = window
    if start == end:
        return False
    minutes = (now or local_now()).hour * 60 + (now or local_now()).minute
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end       # окно через полночь


def sent_today(conn) -> int:
    today = local_now().strftime("%Y-%m-%d")
    if meta_get(conn, "breaking_date", "") != today:
        return 0
    try:
        return int(meta_get(conn, "breaking_count", "0"))
    except ValueError:
        return 0


def count_sent(conn) -> None:
    today = local_now().strftime("%Y-%m-%d")
    meta_set(conn, "breaking_count", sent_today(conn) + 1)
    meta_set(conn, "breaking_date", today)


def is_hot(group) -> bool:
    """Первый фильтр — дешёвый и без модели: консенсус или взрыв на HN."""
    sources = {i["source_id"] for i in group}
    has_primary = any(i["tier"] == 1 for i in group)
    if len(sources) >= CFG["breaking_min_sources"] and has_primary:
        return True
    return max(i["social"] for i in group) >= CFG["breaking_social"]


def candidates(conn):
    """Свежие неотправленные кластеры, похожие на срочные, лучшие — первыми."""
    window = (datetime.now(timezone.utc)
              - timedelta(hours=CFG["breaking_window_h"])).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE fetched_at > ? AND state = 'new'", (window,))]
    if not rows:
        return []
    hot = [g for g in cluster(rows, CFG["similarity"]) if is_hot(g)]
    hot = [g for g in hot if not already_sent(conn, g, CFG["similarity"])]
    return sorted(hot, key=prescore, reverse=True)[:MAX_CANDIDATES]


def why_not(conn) -> str:
    """Причина, по которой проверка не запускается. Пусто — можно работать."""
    from .bot import is_paused
    if not CFG["breaking"]:
        return "выключено (ND_BREAKING=0)"
    if is_paused(conn):
        return "рассылка на паузе"
    if in_quiet_hours():
        return "тихие часы %s" % CFG["breaking_quiet"]
    if sent_today(conn) >= CFG["breaking_max_per_day"]:
        return "лимит %d в сутки исчерпан" % CFG["breaking_max_per_day"]
    return ""


def check(chat_id=None) -> int:
    """Ищет и отправляет срочное. Возвращает число отправленных сообщений."""
    chat_id = chat_id or config.TG_CHAT
    conn = db()
    stats = {"candidates": 0, "sent": 0, "cost": 0.0, "best": 0.0}
    try:
        skip = why_not(conn)
        if skip:
            log.debug("Срочные не проверяю: %s", skip)
            return 0

        groups = candidates(conn)
        stats["candidates"] = len(groups)
        if not groups:
            return 0

        prof = profile()
        persona = prof["persona"] + persona_hint(conn, chat_id)
        try:
            ranking, usage = rank_clusters(groups, persona)
            stats["cost"] += llm_cost(usage)
        except LLMError as exc:
            # без оценки модели срочное не отправляем: слишком легко ошибиться
            log.warning("Срочное не проверить (%s) — подождёт выпуска", exc)
            log_run(conn, "breaking", "llm-failed", stats)
            return 0

        best, best_score, category = None, 0.0, "other"
        for entry in ranking:
            try:
                idx, score = int(entry.get("id", -1)), float(entry.get("score") or 0)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(groups) and score > best_score:
                best, best_score = groups[idx], score
                category = entry.get("category") or primary_of(groups[idx])["category"]
        stats["best"] = best_score

        if best is None or best_score < CFG["breaking_min_score"]:
            log.debug("Срочных нет: лучшая оценка %.1f из нужных %.1f",
                      best_score, CFG["breaking_min_score"])
            log_run(conn, "breaking", "below-threshold", stats)
            return 0

        picked = [(best, best_score, category)]
        try:
            cards, usage = summarize(picked, persona, CFG["language"])
            stats["cost"] += llm_cost(usage)
            card = cards.get(0)
        except LLMError as exc:
            log.warning("Карточка для срочного не написалась (%s) — беру заголовок", exc)
            card = None
        main = primary_of(best)
        card = card or {"headline": main["title"], "what": main["summary"][:300],
                        "why": ""}

        text = breaking_card(card, best, best_score, category)
        tg_send(chat_id, text,
                keyboard=feedback_keyboard([(card, best, best_score, category)]),
                silent=False)
        stats["sent"] = 1

        conn.execute(
            "INSERT OR IGNORE INTO sent(url_hash,sig,title,url,digest_date,sent_at,"
            "source_id,category) VALUES (?,?,?,?,?,?,?,?)",
            (main["url_hash"], main["sig"], main["title"], main["url"],
             local_now().strftime("%Y-%m-%d"), now_iso(), main["source_id"], category))
        for row in best:
            conn.execute("UPDATE items SET state='sent' WHERE url_hash=?",
                         (row["url_hash"],))
        conn.commit()
        count_sent(conn)
        log_run(conn, "breaking", "ok", stats)
        log.info("Срочное отправлено: %s (оценка %.1f, ~$%.4f)",
                 main["title"][:70], best_score, stats["cost"])
        return 1
    finally:
        conn.close()
