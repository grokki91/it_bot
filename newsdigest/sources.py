# -*- coding: utf-8 -*-
"""Сбор материалов: RSS-фиды темы плюс Hacker News."""
from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import classify, safety, trust, userprofiles
from .config import CFG, log, now_iso
from .feedparse import parse_date, parse_feed, strip_html
from .net import http_get
from .profiles import PROFILES
from .storage import db, log_run, meta_set
from .textutil import canonical_url, signature, url_hash


def fetch_source(src):
    """(id, url, tier, category) -> (src, items, total, error).

    `items` — свежее за `window_hours`, `total` — сколько записей в ленте было
    вообще. Различать их обязательно: блог, который пишет раз в две недели,
    отдаёт полную ленту и ноль свежего, и это нормальная работа, а не поломка.
    Исключений не бросает.
    """
    source_id, url, tier, category = src
    try:
        status, raw = http_get(url)
        if status in (403, 405, 429, 451):      # похоже на защиту от ботов — пробуем ещё
            status, raw = http_get(url, ua=CFG["fallback_user_agent"])
        if status != 200 or not raw:
            return src, [], 0, "HTTP %s" % status
        entries = parse_feed(raw)
    except Exception as exc:  # noqa: BLE001 — падение источника не роняет прогон
        return src, [], 0, "%s: %s" % (type(exc).__name__, exc)

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
    return src, out, len(entries), ""


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


def muted_row(row) -> bool:
    """Отключён ли источник — по строке health, без похода в базу.

    Отдельной функцией, чтобы обход фидов и список источников на странице
    считали «отключён» одинаково: иначе в настройках висело бы одно, а
    собиралось бы другое.
    """
    if not row or (row["fails"] or 0) < CFG["mute_after_fails"] or not row["err_at"]:
        return False
    last = parse_date(row["err_at"])
    return bool(last and datetime.now(timezone.utc) - last < timedelta(hours=24))


def is_muted(conn, source_id) -> bool:
    """Сломанный источник молчит сутки, потом пробуем снова — сам вернётся в строй."""
    return muted_row(conn.execute(
        "SELECT fails, err_at FROM health WHERE source_id=?",
        (source_id,)).fetchone())


def mark_health(conn, source_id, ok, err="", count=0, total=None):
    """Отметка о состоянии источника.

    «ok» — это не только «HTTP 200»: фид, который отвечает двухсоткой и отдаёт
    ноль записей, тоже сломан, просто молча. Так ведёт себя витрина Google
    News, когда перестаёт работать поисковый синтаксис, и лента, у которой
    сменился адрес. Поэтому пустые ответы считаются отдельно и видны
    в `digest.py status`.

    Пусто — это ноль записей В ЛЕНТЕ (`total`), а не ноль свежих (`count`).
    Разница принципиальная: `rust-blog` пишет раз в несколько недель и почти
    всегда отдаёт ноль свежего за 30 часов — если считать это молчанием, в
    отчёте окажутся полтора десятка исправных блогов, а сломанный `who-news`
    потеряется среди них. `total=None` — счёта не было, считаем по старому.
    """
    seen = count if total is None else total
    if ok and not seen:
        conn.execute(
            "INSERT INTO health(source_id, ok_at, fails, last_count, empty, empty_at) "
            "VALUES (?,?,0,0,1,?) ON CONFLICT(source_id) DO UPDATE SET "
            "ok_at=excluded.ok_at, fails=0, last_count=0, empty=health.empty+1, "
            "empty_at=COALESCE(NULLIF(health.empty_at,''), excluded.empty_at), "
            "fail_since=''",
            (source_id, now_iso(), now_iso()))
    elif ok:
        conn.execute(
            "INSERT INTO health(source_id, ok_at, fails, last_count) VALUES (?,?,0,?) "
            "ON CONFLICT(source_id) DO UPDATE SET ok_at=excluded.ok_at, fails=0, "
            "last_count=excluded.last_count, empty=0, empty_at=NULL, fail_since=''",
            (source_id, now_iso(), count))
    else:
        conn.execute(
            "INSERT INTO health(source_id, err, err_at, fails, fail_since) "
            "VALUES (?,?,?,1,?) "
            "ON CONFLICT(source_id) DO UPDATE SET err=excluded.err, "
            "err_at=excluded.err_at, fails=health.fails+1, "
            # первый сбой после успеха, а не последний: отсюда и считается,
            # сколько недель источника нет
            "fail_since=COALESCE(NULLIF(health.fail_since,''), excluded.fail_since)",
            (source_id, err[:200], now_iso(), now_iso()))
    conn.commit()


def silent_days(row) -> tuple:
    """Сколько дней источник молчит и почему. (дней, причина, с какого дня).

    Два разных молчания. Первое — не отвечает вовсе: HTTP-ошибка, и счёт идёт
    от первого сбоя после последнего успеха (`fail_since`). Второе — отвечает
    двухсоткой и пустотой: лента жива, но в ней ничего нет (`empty_at`), и
    это тоже выпадение из выпуска, только тихое.
    """
    if not row:
        return 0, "", ""
    now = datetime.now(timezone.utc)
    best = (0, "", "")
    for stamp, empty in ((row["fail_since"] if "fail_since" in row.keys()
                          else "", False), (row["empty_at"], True)):
        since = parse_date(stamp or "")
        if not since:
            continue
        days = (now - since).days
        if empty and (row["empty"] or 0) < CFG["quiet_after_empty"]:
            continue                # пока это просто тихая неделя, а не поломка
        if days > best[0]:
            best = (days, "отвечает пустотой" if empty else "не отвечает", stamp)
    return best


def stale_rows(conn, days=None) -> list:
    """Источники, молчащие дольше срока. Их пора убрать из обхода."""
    days = CFG["archive_after_days"] if days is None else days
    if not days:
        return []
    feeds = {f[0]: f for f in all_feeds(topics=list(PROFILES))}
    out = []
    for row in conn.execute("SELECT * FROM health"):
        feed = feeds.get(row["source_id"])
        if feed is None:
            continue
        quiet, why, since = silent_days(row)
        if quiet < days:
            continue
        out.append({"source_id": feed[0], "topic": "", "url": feed[1],
                    "tier": feed[2], "category": feed[3],
                    "reason": "%s %d дн." % (why, quiet),
                    "err": (row["err"] or "")[:200], "silent_since": since})
    return sorted(out, key=lambda r: r["source_id"])


def archive_stale(conn, days=None) -> list:
    """Убирает в архив то, что молчит дольше срока. Возвращает убранное.

    Зовётся после обхода. Лента, которой нет две недели, не чинится
    ожиданием — это шесть бесполезных запросов в сутки и строка в списке
    проблемных, на которую перестают смотреть. В архиве она не пропадает:
    `feeds --archive` проверяет, не ожило ли что-нибудь, и возвращает.
    """
    from . import userprofiles          # ниже по уровню: профили знают о нас
    from .storage import archive_put, clear_health

    gone = []
    for row in stale_rows(conn, days):
        try:
            topic, _feed = userprofiles.archive_feed(row["source_id"])
        except ValueError as exc:
            log.warning("Не убрал %s в архив: %s", row["source_id"], exc)
            continue
        row["topic"] = topic
        archive_put(conn, row)
        clear_health(conn, row["source_id"])   # счёт пойдёт заново, если вернётся
        log.warning("Источник %s убран в архив: %s", row["source_id"],
                    row["reason"])
        gone.append(row)
    return gone


def collect(topics=None, wire_only=False) -> dict:
    """Обходит источники. topics=None — все разделы, которые кто-то читает;
    список разделов — только их фиды (так /news отвечает за секунды, а не
    ждёт обхода сотни источников).

    wire_only — быстрая полоса: только агентства и службы оповещения
    (`trust.is_wire`). Их два десятка, обход занимает секунды, и именно он
    позволяет проверять срочное раз в четверть часа, а не раз в четыре."""
    conn = db()
    stats = {"ok": 0, "failed": 0, "muted": 0, "fetched": 0, "new": 0}
    partial = topics is not None or wire_only
    topics = list(topics) if topics is not None else topics_in_use(conn)
    feeds = all_feeds(topics, wire_only=wire_only)
    sources = [s for s in feeds if not is_muted(conn, s[0])]
    stats["muted"] = len(feeds) - len(sources)

    rows = []
    with ThreadPoolExecutor(max_workers=CFG["concurrency"]) as pool:
        for src, items, total, err in pool.map(fetch_source, sources):
            if err:
                stats["failed"] += 1
                mark_health(conn, src[0], False, err)
                log.warning("%s: %s", src[0], err)
            else:
                stats["ok"] += 1
                mark_health(conn, src[0], True, count=len(items), total=total)
                rows.extend(items)

    # Hacker News на быстрой полосе не нужен: он не агентство, а форум, и на
    # четвертьчасовом опросе даёт только лишний трафик
    if CFG["use_hackernews"] and not wire_only:
        rows.extend(fetch_hackernews(keywords_for(topics)))

    stats["fetched"] = len(rows)
    # раздел определяем ЗДЕСЬ, один раз на материал: дальше он лежит в базе, и
    # ни выпуску, ни ленте на странице не приходится гадать по источнику.
    # Разделы для маршрутизации — те, что кто-то читает: уводить новость туда,
    # куда никто не подписан, значит её потерять
    stats["cost"] = classify.route_all(conn, rows, topics_in_use(conn))
    # куда ведут ссылки — тоже ЗДЕСЬ и тоже один раз на материал. Сокращатель
    # при этом разворачивается, и в базу ложится уже конечный адрес: читателю
    # полезнее видеть, куда он идёт, чем bit.ly
    links = safety.check(conn, rows)
    if links["unsafe"]:
        # в строку прогона кладём только число: `status` печатает её целиком
        # и обрезает по длине, а подробности и так лежат в логе
        stats["unsafe"] = links["unsafe"]
    before = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
    for row in rows:
        conn.execute(
            "INSERT INTO items(url_hash,url,source_id,tier,category,title,summary,"
            "published_at,fetched_at,sig,social,section,route_conf,safe,safe_why) "
            "VALUES (:url_hash,:url,:source_id,:tier,:category,:title,:summary,"
            ":published_at,:fetched_at,:sig,:social,:section,:route_conf,"
            ":safe,:safe_why) "
            "ON CONFLICT(url_hash) DO UPDATE SET social=MAX(items.social, excluded.social), "
            "safe=excluded.safe, safe_why=excluded.safe_why",
            dict(row, fetched_at=now_iso()))
    conn.commit()
    stats["new"] = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] - before

    cutoff_i = (datetime.now(timezone.utc)
                - timedelta(days=CFG["keep_items_days"])).isoformat()
    cutoff_s = (datetime.now(timezone.utc)
                - timedelta(days=CFG["keep_sent_days"])).isoformat()
    cutoff_r = (datetime.now(timezone.utc)
                - timedelta(days=CFG["keep_routes_days"])).isoformat()
    cutoff_d = (datetime.now(timezone.utc)
                - timedelta(days=CFG["keep_dupes_days"])).isoformat()
    conn.execute("DELETE FROM items WHERE fetched_at < ? AND state != 'sent'", (cutoff_i,))
    conn.execute("DELETE FROM sent WHERE sent_at < ?", (cutoff_s,))
    # сюжетная связь живёт ровно столько, сколько обе её новости: ссылка на
    # вычищенную строку истории — это «Ранее по теме» в пустоту
    conn.execute("DELETE FROM threads WHERE at < ?", (cutoff_s,))
    conn.execute("DELETE FROM routes WHERE at < ?", (cutoff_r,))
    conn.execute("DELETE FROM dupes WHERE at < ?", (cutoff_d,))
    # приговоры фактчека и наша репутация доменов живут дольше самих новостей:
    # домен, знакомый полгода, — это и есть то, ради чего таблица заведена
    conn.execute("DELETE FROM claims WHERE at < ?",
                 ((datetime.now(timezone.utc)
                   - timedelta(days=CFG["keep_claims_days"])).isoformat(),))
    conn.execute("DELETE FROM hosts WHERE last_seen < ? AND verdict = ''",
                 ((datetime.now(timezone.utc)
                   - timedelta(days=CFG["keep_hosts_days"])).isoformat(),))
    # перевод новости живёт ровно столько, сколько сама новость: заголовок
    # двухмесячной давности второй раз уже не понадобится
    conn.execute("DELETE FROM translations WHERE at < ?", (cutoff_s,))
    conn.commit()

    # частичный сбор (один раздел по команде /news) не считается обходом всего
    # списка: иначе плановый сбор отложился бы на несколько часов
    if partial:
        stats["topics"] = topics
    else:
        meta_set(conn, "last_collect", now_iso())
    # быструю полосу обходит и полный сбор, и она сама — но не сбор одного
    # раздела по запросу: агентств в нём может не оказаться вовсе
    if wire_only or not partial:
        meta_set(conn, "last_wire", now_iso())
    log_run(conn, "collect", "ok", stats)
    conn.close()
    log.info("Сбор: источников ok=%d, ошибок=%d, отключено=%d, получено=%d, новых=%d",
             stats["ok"], stats["failed"], stats["muted"], stats["fetched"], stats["new"])
    line = safety.stats_line(links)
    if line:
        log.info("%s", line)
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


def all_feeds(topics=None, wire_only=False) -> list:
    """Источники всех используемых тем без повторов.

    wire_only — только быстрая полоса: агентства и службы оповещения.
    """
    seen, feeds = set(), []
    for topic in (topics if topics is not None else topics_in_use()):
        for feed in PROFILES.get(topic, {}).get("feeds", []):
            if feed[0] in seen or (wire_only and not trust.is_wire(feed[0])):
                continue
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


# ------------------------------------------------------- список источников
def health_map(conn) -> dict:
    """Что база знает о фидах — одним запросом на всех.

    Источников три сотни, и спрашивать про каждый отдельно ради одного
    экрана не стоит: строк в `health` столько же, сколько источников.
    """
    try:
        rows = conn.execute(
            "SELECT source_id, ok_at, err, err_at, fails, last_count, "
            "empty, empty_at FROM health")
    except sqlite3.Error as exc:            # база ещё не готова — не беда
        log.debug("Не смог прочитать health: %s", exc)
        return {}
    return {row["source_id"]: row for row in rows}


def feed_state(row) -> dict:
    """Состояние источника словами кода: отвечает, сбоит, молчит, отключён.

    Порядок важен: отключённый источник сбоит по определению, а молчание
    у сбоящего никого не интересует — сначала называем то, из-за чего
    новостей нет прямо сейчас.
    """
    if row is None:
        return {"state": "new", "fails": 0, "empty": 0, "count": 0,
                "err": "", "at": ""}
    fails, empty = int(row["fails"] or 0), int(row["empty"] or 0)
    if muted_row(row):
        state = "muted"
    elif fails:
        state = "fail"
    elif empty >= CFG["quiet_after_empty"]:
        state = "quiet"
    else:
        state = "ok"
    return {"state": state, "fails": fails, "empty": empty,
            "count": int(row["last_count"] or 0),
            "err": str(row["err"] or "")[:120],
            "at": str((row["err_at"] if state in ("fail", "muted")
                       else row["ok_at"]) or "")}


def overview(conn=None, topics=None) -> list:
    """Источники по разделам — для показа: откуда бот берёт новости.

    Ничего не собирает и в сеть не ходит: список фидов из профилей плюс то,
    что о них знает `health` с прошлых обходов. Разделы — те, которые
    кто-то читает: остальные лежат в profiles.py, но не опрашиваются, и
    называть их источниками новостей было бы неправдой.
    """
    close = conn is None
    conn = conn or db()
    try:
        health = health_map(conn)
        topics = list(topics) if topics is not None else topics_in_use(conn)
    finally:
        if close:
            conn.close()

    out = []
    for topic in topics:
        feeds = []
        for source_id, url, tier, category in sorted(
                PROFILES.get(topic, {}).get("feeds", [])):
            feeds.append(dict(feed_state(health.get(source_id)),
                              id=source_id, url=url,
                              tier=int(tier), category=str(category),
                              wire=trust.is_wire(source_id),
                              custom=userprofiles.is_custom(topic, source_id)))
        out.append({"topic": topic, "feeds": feeds})
    return out
