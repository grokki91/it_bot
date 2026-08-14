# -*- coding: utf-8 -*-
"""Сбор материалов: RSS-фиды темы плюс Hacker News."""
from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .config import CFG, log, now_iso
from .feedparse import parse_date, parse_feed, strip_html
from .net import http_get
from .profiles import PROFILES
from .storage import db, log_run, meta_set
from .textutil import canonical_url, signature, url_hash


def fetch_source(src):
    """(id, url, tier, category) -> (src, items, error). Не бросает исключений."""
    source_id, url, tier, category = src
    try:
        status, raw = http_get(url)
        if status in (403, 405, 429, 451):      # похоже на защиту от ботов — пробуем ещё
            status, raw = http_get(url, ua=CFG["fallback_user_agent"])
        if status != 200 or not raw:
            return src, [], "HTTP %s" % status
        entries = parse_feed(raw)
    except Exception as exc:  # noqa: BLE001 — падение источника не роняет прогон
        return src, [], "%s: %s" % (type(exc).__name__, exc)

    window = datetime.now(timezone.utc) - timedelta(hours=CFG["window_hours"])
    out = []
    for entry in entries[: CFG["max_per_feed"]]:
        published = entry["published"]
        if published and published < window:
            continue
        title, body = entry["title"], entry["summary"]
        out.append({
            "url_hash": url_hash(entry["link"]),
            "url": canonical_url(entry["link"]),
            "source_id": source_id,
            "tier": tier,
            "category": category,
            "title": title,
            "summary": body[:700],
            "published_at": published.isoformat(timespec="seconds") if published else None,
            "sig": signature(title + " " + body[:250]),
            "social": 0.0,
        })
    return src, out, ""


def fetch_hackernews(keywords=None):
    """HN даёт готовый числовой сигнал важности — баллы и комментарии."""
    since = int(time.time()) - CFG["window_hours"] * 3600
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story"
           "&numericFilters=created_at_i>%d,points>%d&hitsPerPage=80"
           % (since, CFG["hn_min_points"]))
    try:
        status, raw = http_get(url, timeout=20)
        hits = json.loads(raw.decode("utf-8", "replace")).get("hits", []) if status == 200 else []
    except Exception as exc:  # noqa: BLE001
        log.warning("Hacker News недоступен: %s", exc)
        return []

    keywords = keywords_for() if keywords is None else keywords
    out = []
    for hit in hits:
        title = strip_html(hit.get("title") or "", 300)
        if not title or not any(k in title.lower() for k in keywords):
            continue
        link = hit.get("url") or ("https://news.ycombinator.com/item?id=%s"
                                  % hit.get("objectID"))
        points = float(hit.get("points") or 0)
        created = datetime.fromtimestamp(
            hit.get("created_at_i", time.time()), timezone.utc)
        out.append({
            "url_hash": url_hash(link),
            "url": canonical_url(link),
            "source_id": "hackernews",
            "tier": CFG["hn_tier"],
            "category": "community",
            "title": title,
            "summary": "Hacker News: %d баллов, %d комментариев."
                       % (int(points), hit.get("num_comments") or 0),
            "published_at": created.isoformat(timespec="seconds"),
            "sig": signature(title),
            "social": min(points / 300.0, 1.0),
        })
    return out


def is_muted(conn, source_id) -> bool:
    """Сломанный источник молчит сутки, потом пробуем снова — сам вернётся в строй."""
    row = conn.execute("SELECT fails, err_at FROM health WHERE source_id=?",
                       (source_id,)).fetchone()
    if not row or row["fails"] < CFG["mute_after_fails"] or not row["err_at"]:
        return False
    last = parse_date(row["err_at"])
    return bool(last and datetime.now(timezone.utc) - last < timedelta(hours=24))


def mark_health(conn, source_id, ok, err="", count=0):
    if ok:
        conn.execute(
            "INSERT INTO health(source_id, ok_at, fails, last_count) VALUES (?,?,0,?) "
            "ON CONFLICT(source_id) DO UPDATE SET ok_at=excluded.ok_at, fails=0, "
            "last_count=excluded.last_count", (source_id, now_iso(), count))
    else:
        conn.execute(
            "INSERT INTO health(source_id, err, err_at, fails) VALUES (?,?,?,1) "
            "ON CONFLICT(source_id) DO UPDATE SET err=excluded.err, "
            "err_at=excluded.err_at, fails=health.fails+1",
            (source_id, err[:200], now_iso()))
    conn.commit()


def collect(topics=None) -> dict:
    """Обходит источники. topics=None — все разделы, которые кто-то читает;
    список разделов — только их фиды (так /news отвечает за секунды, а не
    ждёт обхода сотни источников)."""
    conn = db()
    stats = {"ok": 0, "failed": 0, "muted": 0, "fetched": 0, "new": 0}
    partial = topics is not None
    topics = list(topics) if partial else topics_in_use(conn)
    feeds = all_feeds(topics)
    sources = [s for s in feeds if not is_muted(conn, s[0])]
    stats["muted"] = len(feeds) - len(sources)

    rows = []
    with ThreadPoolExecutor(max_workers=CFG["concurrency"]) as pool:
        for src, items, err in pool.map(fetch_source, sources):
            if err:
                stats["failed"] += 1
                mark_health(conn, src[0], False, err)
                log.warning("%s: %s", src[0], err)
            else:
                stats["ok"] += 1
                mark_health(conn, src[0], True, count=len(items))
                rows.extend(items)

    if CFG["use_hackernews"]:
        rows.extend(fetch_hackernews(keywords_for(topics)))

    stats["fetched"] = len(rows)
    before = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
    for row in rows:
        conn.execute(
            "INSERT INTO items(url_hash,url,source_id,tier,category,title,summary,"
            "published_at,fetched_at,sig,social) "
            "VALUES (:url_hash,:url,:source_id,:tier,:category,:title,:summary,"
            ":published_at,:fetched_at,:sig,:social) "
            "ON CONFLICT(url_hash) DO UPDATE SET social=MAX(items.social, excluded.social)",
            dict(row, fetched_at=now_iso()))
    conn.commit()
    stats["new"] = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] - before

    cutoff_i = (datetime.now(timezone.utc)
                - timedelta(days=CFG["keep_items_days"])).isoformat()
    cutoff_s = (datetime.now(timezone.utc)
                - timedelta(days=CFG["keep_sent_days"])).isoformat()
    conn.execute("DELETE FROM items WHERE fetched_at < ? AND state != 'sent'", (cutoff_i,))
    conn.execute("DELETE FROM sent WHERE sent_at < ?", (cutoff_s,))
    conn.commit()

    # частичный сбор (один раздел по команде /news) не считается обходом всего
    # списка: иначе плановый сбор отложился бы на несколько часов
    if partial:
        stats["topics"] = topics
    else:
        meta_set(conn, "last_collect", now_iso())
    log_run(conn, "collect", "ok", stats)
    conn.close()
    log.info("Сбор: источников ok=%d, ошибок=%d, отключено=%d, получено=%d, новых=%d",
             stats["ok"], stats["failed"], stats["muted"], stats["fetched"], stats["new"])
    return stats


def topics_in_use(conn=None) -> list:
    """Разделы, которые кто-то читает: общие плюс личные разделы подписчиков.

    Собирать надо для всех сразу — один обход фидов на всех подписчиков,
    а не по обходу на каждого.
    """
    from .sections import defaults, for_sub
    from .subscribers import active

    topics = []

    def add(name):
        name = (name or "").strip()
        if name and name in PROFILES and name not in topics:
            topics.append(name)

    add(CFG["topic"])               # раздел по умолчанию: /news и срочные
    for name in defaults():
        add(name)
    close = conn is None
    conn = conn or db()
    try:
        for sub in active(conn):
            add(sub["topic"])
            for name in for_sub(sub):
                add(name)
    except sqlite3.Error as exc:                # база ещё не готова — не беда
        log.debug("Не смог прочитать подписчиков: %s", exc)
    finally:
        if close:
            conn.close()
    return topics


def all_feeds(topics=None) -> list:
    """Источники всех используемых тем без повторов."""
    seen, feeds = set(), []
    for topic in (topics if topics is not None else topics_in_use()):
        for feed in PROFILES.get(topic, {}).get("feeds", []):
            if feed[0] not in seen:
                seen.add(feed[0])
                feeds.append(feed)
    return feeds


def sources_for(topic: str) -> set:
    """Имена источников темы — по ним материалы фильтруются под подписчика."""
    return {f[0] for f in PROFILES.get(topic, {}).get("feeds", [])}


def keywords_for(topics=None) -> list:
    words = []
    for topic in (topics if topics is not None else topics_in_use()):
        for word in PROFILES.get(topic, {}).get("keywords", []):
            if word.lower() not in words:
                words.append(word.lower())
    return words
