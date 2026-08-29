# -*- coding: utf-8 -*-
"""Куда ведёт ссылка. Заслон между чужим фидом и читателем.

Всё, что бот показывает, — это ссылки из чужих лент. Своих адресов у него нет
вовсе: карточка новости заканчивается строкой `🔗 <a href="URL">источник</a>`,
где URL приехал из RSS, а подписью стоит наше имя источника. И вот тут
начинается неприятное:

    <a href="https://apnews.com@phish.tk/login">apnews</a>

Читатель видит «apnews», ссылка ведёт на phish.tk. Всё, что до `@`, браузер
считает именем пользователя и выбрасывает. Проверить это глазами нельзя —
в Telegram видна только подпись. И `textutil.canonical_url` такую ссылку не
трогает: он снимает трекинг, а не защищает.

Откуда вообще берётся плохая ссылка, если ленты у нас свои и проверенные:

    * лента взломана или домен издания протух и его перекупили;
    * ссылка внутри записи ведёт НЕ к издателю ленты: партнёрский материал,
      реклама, сокращатель, перехваченный редирект;
    * агрегатор. Hacker News, Reddit и Google News — это витрины, куда ссылку
      кладёт кто угодно. Мы их читаем, и они ведут наружу по определению;
    * `feeds --candidates`: чужой фид, про который мы ещё ничего не знаем.

Поэтому каскад, от бесплатного к дорогому — как в `classify` и `dedup`:

    0. форма ссылки (`shaped_badly`). Схема, `@` в адресе, IP вместо имени,
       чужой порт, punycode, управляющие символы. Ничего не стоит и ловит
       ровно тот случай, что выше;
    1. ссылка ведёт к своему издателю (`own_publisher`). У узкой ленты
       Guardian материал лежит на theguardian.com — совпало, и дальше можно
       не смотреть. Это подавляющее большинство ссылок, и они бесплатны;
    2. знакомый домен (`familiar`). Издатель из нашего же реестра — или
       домен, который мы сами видим неделями и десятками записей. Свежий
       домен, всплывший однажды, — не то же самое, что nature.com;
    3. сокращатель (`resolve`). bit.ly не даёт читателю увидеть, куда он
       идёт, — и ровно поэтому фишинг живёт в сокращателях. Разворачиваем
       цепочку редиректов и судим конечный адрес, его же и публикуем;
    4. внешняя база угроз (`threat`). Google Safe Browsing, пачкой и по
       желанию: без ключа слой просто выключен. Он единственный знает про
       домен то, чего не знаем мы, — что его вчера отметили как фишинг.

**Приговор ссылке — это не приговор новости.** Кластер — это несколько
заметок об одном событии, и если ссылка одной из них никуда не годится, лицо
кластера просто переходит к следующей (`face_of` — та же механика, что у
`trust.demoted`, где пресс-релиз уступает разбору). Читатель получает ту же
новость со ссылкой на издателя, который не фишинг. И только если у события
нет ни одной годной ссылки, оно не публикуется вовсе.

Направление осторожности здесь обратное всему остальному в проекте. Дедуп
сомневается — показывает новость: потерять её хуже, чем повторить. Здесь
наоборот: не смогли поручиться за ссылку — не публикуем её. Непоказанная
новость стоит читателю ничего, показанный фишинг — денег.

Проверка живёт при сборе (`sources.collect`), рядом с маршрутизацией: один
раз на материал, вердикт ложится в базу. Сборке выпуска он достаётся даром.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config, trust
from .config import CFG, log, now_iso
from .net import post_json
from .profiles import PROFILES

#: вердикт ссылки. Хранится в `items.safe`
OK, UNSAFE, UNKNOWN = "ok", "unsafe", "unknown"

#: схемы, которые вообще можно показать читателю. `javascript:` и `data:`
#: в ленте делать нечего, `ftp://` читателя тоже никуда не приведёт
SCHEMES = ("http", "https")

#: сокращатели: ссылка не показывает, куда ведёт. Разворачиваем и судим цель
SHORTENERS = {
    "bit.ly", "t.co", "goo.gl", "tinyurl.com", "ow.ly", "buff.ly", "is.gd",
    "cutt.ly", "rb.gy", "shorturl.at", "t.ly", "rebrand.ly", "bl.ink",
    "trib.al", "dlvr.it", "ift.tt", "lnkd.in", "fb.me", "amzn.to", "nyti.ms",
    "reut.rs", "apne.ws", "cnb.cx", "bbc.in", "on.wsj.com", "wapo.st",
    "econ.st", "flip.it", "sco.lt", "clck.ru", "vk.cc", "u.to",
}

#: домены-витрины: ссылка наружу для них норма, а не подозрение. Их материал
#: судится по конечному адресу, а не по совпадению с издателем
STOREFRONTS = {
    "news.ycombinator.com", "reddit.com", "news.google.com", "lobste.rs",
    "slashdot.org", "techmeme.com", "flipboard.com", "medium.com",
    "substack.com", "github.com", "youtube.com", "x.com", "twitter.com",
}

#: зоны, где живёт бесплатная регистрация и почти вся типовая фишинговая
#: масса. Само по себе это не приговор — приговор это вместе с незнакомым
#: доменом и слабым источником (см. `verdict`)
RISKY_TLDS = {
    "tk", "ml", "ga", "cf", "gq", "top", "xyz", "click", "link", "work",
    "buzz", "rest", "cam", "surf", "monster", "quest", "zip", "mov", "kim",
    "country", "stream", "download", "loan", "date", "racing", "party",
}

#: управляющие символы и всё, чему в адресе делать нечего: перевод строки
#: внутри href разваливает разметку сообщения, а пробел прячет хвост адреса
JUNK = re.compile(r"[\x00-\x20\x7f-\x9f<>\"'\\{}|^`]")

#: `xn--` — адрес, записанный не латиницей. Сам по себе законен, но
#: «аррӏе.com» от «apple.com» читателю не отличить, а у наших изданий
#: интернационализированных доменов нет ни одного
PUNYCODE = re.compile(r"(?:^|\.)xn--", re.IGNORECASE)

#: чистый IP вместо имени. У новостного сайта такого не бывает
IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

_cache = {}


def reset() -> None:
    """Сбросить разбор PROFILES. Зовётся, когда профили пересобраны."""
    _cache.clear()


# ------------------------------------------------------------------ разбор URL
def parts(url: str):
    """Разбор адреса. Возвращает `urllib.parse.ParseResult` или None."""
    try:
        return urllib.parse.urlparse(str(url or "").strip())
    except ValueError:
        return None


def host(url: str) -> str:
    """Настоящий хост ссылки: без `user@`, без порта, без `www.`.

    Именно здесь ломается наивная проверка. `urlparse('https://apnews.com@'
    'phish.tk/x').netloc` — это `apnews.com@phish.tk`, и всё, что ищет в нём
    знакомое имя, находит apnews. Браузер же пойдёт на phish.tk: хост — это
    то, что ПОСЛЕ последней собаки.
    """
    piece = parts(url)
    if piece is None:
        return ""
    netloc = piece.netloc or ""
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    name = netloc.split(":")[0].strip().strip(".").lower()
    return name[4:] if name.startswith("www.") else name


def userinfo(url: str) -> bool:
    """Есть ли в адресе `user:pass@` — та самая маскировка хоста."""
    piece = parts(url)
    return bool(piece is not None and "@" in (piece.netloc or ""))


def tld(name: str) -> str:
    return name.rsplit(".", 1)[-1] if "." in name else ""


def under(name: str, domain: str) -> bool:
    """Хост принадлежит домену: сам домен или его поддомен.

    Проверка суффиксом, а не вхождением. `theguardian.com.secure-login.tk`
    содержит `theguardian.com` — и именно на это рассчитан: имя издания
    ставят в начало, чтобы оно попало в видимую часть адресной строки.
    """
    name, domain = (name or "").lower(), (domain or "").lower()
    return bool(domain) and (name == domain or name.endswith("." + domain))


def brand_bait(name: str) -> str:
    """Чужое имя внутри незнакомого домена: `theguardian.com.secure-login.tk`.

    Возвращает домен издания, под которое рядятся, или пустую строку. Ищем
    только те, что уже есть в нашем реестре: подделывают известное.
    """
    if known(name):
        return ""
    for domain in publishers():
        if len(domain) >= 7 and domain in name and not under(name, domain):
            return domain
    return ""


# ------------------------------------------------- слой 0: форма ссылки
def shaped_badly(url: str) -> str:
    """Чем ссылка плоха по одному своему виду. Пусто — ничем.

    Сеть здесь не трогается вовсе: это чистый разбор строки, и он ловит
    маскировку хоста — единственный способ показать читателю одно, а увести
    на другое.
    """
    raw = str(url or "").strip()
    if not raw:
        return "пустая ссылка"
    piece = parts(raw)
    if piece is None:
        return "неразбираемый адрес"
    if piece.scheme.lower() not in SCHEMES:
        return "схема %s" % (piece.scheme.lower() or "не указана")
    if JUNK.search(raw):
        return "управляющие символы в адресе"
    if userinfo(raw):
        # ровно тот случай: подпись «apnews», переход на phish.tk
        return "хост спрятан за @"
    name = host(raw)
    if not name or "." not in name:
        return "нет домена"
    if IPV4.match(name) or ":" in (piece.netloc or "").rsplit("@", 1)[-1].strip("[]"):
        return "адрес вместо имени"
    if PUNYCODE.search(name):
        return "punycode-домен"
    if len(name) > 100 or name.count(".") > 6:
        return "неправдоподобный домен"
    try:
        port = piece.port
    except ValueError:
        return "неразбираемый порт"
    if port not in (None, 80, 443):
        return "нестандартный порт %s" % port
    bait = brand_bait(name)
    if bait:
        return "домен рядится под %s" % bait
    return ""


# ------------------------------------- слой 1-2: издатель и знакомый домен
def publishers() -> set:
    """Домены изданий, которые мы читаем. Естественный белый список: пара
    сотен реальных редакций, и ссылка внутрь любой из них вопросов не вызывает.
    """
    names = _cache.get("publishers")
    if names is None:
        names = set()
        for body in PROFILES.values():
            for feed in body.get("feeds") or ():
                if len(feed) >= 2:
                    names.add(trust.publisher(str(feed[0])))
                    names.add(host(str(feed[1])))
        names |= set(trust.PUBLISHER.values())
        names.discard("")
        _cache["publishers"] = names
    return names


def known(name: str) -> bool:
    """Домен принадлежит изданию из нашего реестра."""
    return any(under(name, domain) for domain in publishers())


def own_publisher(url: str, source_id: str) -> bool:
    """Ссылка ведёт туда же, где живёт сам источник.

    Для узкой ленты это норма и повод больше ничего не проверять. Для витрины
    (Hacker News, Reddit, Google News) — наоборот, ссылка наружу и есть смысл
    её существования, поэтому им это ничего не говорит.
    """
    return under(host(url), trust.publisher(str(source_id or "")))


def storefront(source_id: str) -> bool:
    """Источник — витрина: ссылку туда кладёт кто угодно."""
    return (trust.publisher(str(source_id or "")) in STOREFRONTS
            or trust.kind(str(source_id or "")) == "aggregator")


def note_hosts(conn, rows) -> None:
    """Копит нашу собственную репутацию доменов: когда увидели впервые и
    сколько раз встречали.

    Бесплатный и на удивление честный сигнал. Домен, который приходит к нам
    неделями и десятками записей, не бывает свежерегистрированной подделкой:
    фишинговый домен живёт дни и в новостных лентах не задерживается.
    """
    counts = {}
    for row in rows:
        name = host(row.get("url"))
        if name:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return
    stamp = now_iso()
    conn.executemany(
        "INSERT INTO hosts(host, first_seen, last_seen, seen) VALUES (?,?,?,?) "
        "ON CONFLICT(host) DO UPDATE SET seen = hosts.seen + excluded.seen, "
        "last_seen = excluded.last_seen",
        [(name, stamp, stamp, count) for name, count in counts.items()])
    conn.commit()


def familiar(conn, name: str) -> bool:
    """Домен давно и часто у нас бывает — значит, это не однодневка."""
    if not name:
        return False
    row = conn.execute(
        "SELECT first_seen, seen FROM hosts WHERE host = ?", (name,)).fetchone()
    if not row or row["seen"] < CFG["safe_seen_min"]:
        return False
    age = (datetime.now(timezone.utc)
           - timedelta(days=CFG["safe_seen_days"])).isoformat()
    return str(row["first_seen"]) <= age


# --------------------------------------------- слой 3: развернуть сокращатель
def shortener(name: str) -> bool:
    return name in SHORTENERS


def resolve(url: str) -> str:
    """Куда на самом деле ведёт сокращённая ссылка. Пусто — выяснить не вышло.

    Ходим по цепочке редиректов руками, а не отдаём её `net._opener`: нам
    нужен каждый шаг. Ссылка, которая уводит на адрес дурной формы посреди
    цепочки, — это и есть перехваченный редирект, и узнать об этом по одному
    конечному адресу нельзя.
    """
    seen, current = set(), str(url or "")
    for _hop in range(max(1, int(CFG["safe_hops"]))):
        if current in seen:
            return ""                       # цикл редиректов
        seen.add(current)
        if shaped_badly(current):
            return ""
        status, location = _hop_once(current)
        if not location:
            return current if status and status < 400 else ""
        current = urllib.parse.urljoin(current, location)
    return ""                               # слишком длинная цепочка


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Опенер, который не идёт за редиректом сам: шаги считаем мы."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def _hop_once(url: str, method="HEAD"):
    """Один шаг цепочки: (статус, куда redirect). Сеть наружу не пускает.

    HEAD хватает почти всегда — сокращатель только для того и существует,
    чтобы ответить редиректом. Кто HEAD не принимает (405, 501), тому
    достанется GET: тело мы не читаем, редирект приходит в заголовке.
    """
    request = urllib.request.Request(url, method=method)
    request.add_header("User-Agent", CFG["user_agent"])
    try:
        with urllib.request.build_opener(_NoRedirect).open(
                request, timeout=CFG["safe_timeout"]) as resp:
            return resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        where = exc.headers.get("Location", "") if exc.headers else ""
        if not where and exc.code in (405, 501) and method == "HEAD":
            return _hop_once(url, "GET")
        return exc.code, where
    except Exception as exc:                # noqa: BLE001 — таймауты, DNS, TLS
        log.debug("Не развернул %s: %s: %s", url, type(exc).__name__, exc)
        return 0, ""


# ------------------------------------------ слой 4: внешняя база угроз
def safebrowsing_key() -> str:
    """Ключ Google Safe Browsing. Пусто — слой выключен, и это нормально."""
    return str(getattr(config, "SB_KEY", "") or "").strip()


def threat(urls) -> dict:
    """Что про эти адреса думает Google Safe Browsing: {адрес: тип угрозы}.

    Единственный слой, который знает про домен то, чего не знаем мы: что его
    вчера отметили как фишинг. Одним запросом до 500 адресов, ключ бесплатный,
    без ключа слой просто не работает — все остальные при этом работают.

    Молчание в ответ — это не «чисто», а «не знаем»: сеть могла не дойти.
    Поэтому пустой словарь ничего не разрешает, он лишь не запрещает.
    """
    urls = [u for u in dict.fromkeys(urls) if u]
    key = safebrowsing_key()
    if not urls or not key or not CFG["safebrowsing"]:
        return {}
    payload = {
        "client": {"clientId": "newsdigest", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING",
                            "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": u} for u in urls[:500]],
        },
    }
    url = "https://safebrowsing.googleapis.com/v4/threatMatches:find?key=" + key
    status, data, err = post_json(url, payload, timeout=CFG["safe_timeout"])
    if status != 200 or data is None:
        # ключ мог протухнуть или кончиться квота — это не повод рушить сбор
        log.warning("Safe Browsing не ответил (HTTP %s %s) — сужу своими слоями",
                    status, str(err)[:120])
        return {}
    out = {}
    for match in data.get("matches") or ():
        hit = str((match.get("threat") or {}).get("url") or "")
        if hit:
            out[hit] = str(match.get("threatType") or "THREAT")
    return out


def asked_before(conn, names) -> dict:
    """Что Safe Browsing уже говорил про эти домены: {домен: угроза или ""}.

    Пустая строка — «спрашивали, чисто», и это тоже ответ: без него чистый
    домен уезжал бы во внешнюю базу каждый прогон. Отличается он не значением,
    а наличием ключа в словаре.

    Ответ живёт `safe_ttl_h`: вчера чистый домен сегодня бывает взломан, и
    вечная память тут вредна.
    """
    out = {}
    names = [n for n in dict.fromkeys(names) if n]
    fresh = (datetime.now(timezone.utc)
             - timedelta(hours=max(1, int(CFG["safe_ttl_h"])))).isoformat()
    for start in range(0, len(names), 400):     # SQLite не любит длинные IN
        part = names[start:start + 400]
        marks = ",".join("?" * len(part))
        for row in conn.execute(
                "SELECT host, verdict FROM hosts WHERE host IN (%s) "
                "AND checked_at != '' AND checked_at >= ?" % marks,
                part + [fresh]):
            out[row["host"]] = row["verdict"]
    return out


def remember_verdict(conn, verdicts) -> None:
    """Кладёт ответ внешней базы рядом с доменом: второй раз не спрашиваем."""
    stamp = now_iso()
    rows = [(name, stamp, stamp, verdict, stamp) for name, verdict in verdicts]
    if not rows:
        return
    conn.executemany(
        "INSERT INTO hosts(host, first_seen, last_seen, seen, verdict, checked_at) "
        "VALUES (?,?,?,0,?,?) ON CONFLICT(host) DO UPDATE SET "
        "verdict = excluded.verdict, checked_at = excluded.checked_at", rows)
    conn.commit()


# ------------------------------------------------------------------- приговор
def verdict(conn, url: str, source_id: str) -> tuple:
    """Судить одну ссылку по бесплатным слоям. Возвращает (вердикт, причина).

    Сеть здесь не трогается: сокращатели и внешнюю базу разбирает `check`,
    пачкой и с лимитом. Здесь — то, что можно решить, ничего не спрашивая.
    """
    bad = shaped_badly(url)
    if bad:
        return UNSAFE, bad

    name = host(url)
    if own_publisher(url, source_id) and not storefront(source_id):
        return OK, "издатель свой"
    if known(name):
        return OK, "издание из реестра"
    if shortener(name):
        return UNKNOWN, "сокращатель"
    if familiar(conn, name):
        return OK, "домен давно знаком"

    # дальше — незнакомый домен. Сам по себе он не преступление: у витрин
    # это норма, а половина хороших ссылок HN ведёт в чей-то личный блог.
    # Но рискованная зона у незнакомого домена — это уже сочетание
    if tld(name) in RISKY_TLDS:
        return UNSAFE, "незнакомый домен в зоне .%s" % tld(name)
    return UNKNOWN, "домен незнаком"


def check(conn, rows) -> dict:
    """Проверить ссылки собранных материалов. Правит `rows` на месте.

    Зовётся из `sources.collect` — там же, где маршрутизация: один раз на
    материал, вердикт ложится в базу вместе с ним. Дальше и выпуск, и лента
    на странице получают его даром.

    Порядок дорогих слоёв важен: сначала разворачиваем сокращатели (после
    этого часть из них оказывается ссылками на знакомые издания и вопросов
    больше не вызывает), и только оставшееся спрашиваем у внешней базы.
    """
    stats = {"checked": 0, "unsafe": 0, "unknown": 0, "resolved": 0, "threats": 0}
    if not rows:
        return stats
    note_hosts(conn, rows)
    if not CFG["safe_links"]:
        for row in rows:
            row["safe"], row["safe_why"] = OK, "проверка выключена"
        return stats

    for row in rows:
        row["safe"], row["safe_why"] = verdict(conn, row.get("url"), row.get("source_id"))
    stats["checked"] = len(rows)

    # сокращатели: разворачиваем и судим цель. Публикуем тоже развёрнутую —
    # читателю полезнее видеть, куда он идёт, чем bit.ly
    if CFG["safe_resolve"]:
        pending = [r for r in rows if r["safe_why"] == "сокращатель"]
        for row in pending[:max(0, int(CFG["safe_resolve_max"]))]:
            target = resolve(row["url"])
            if not target:
                row["safe"], row["safe_why"] = UNSAFE, "сокращатель не развернулся"
                continue
            stats["resolved"] += 1
            row["url"] = target
            row["safe"], row["safe_why"] = verdict(conn, target, row.get("source_id"))

    # внешняя база — только про то, за что не поручились свои слои
    if CFG["safebrowsing"] and safebrowsing_key():
        doubted = [r for r in rows if r["safe"] == UNKNOWN]
        names = [host(r["url"]) for r in doubted]
        seen = asked_before(conn, names)
        ask = [r["url"] for r in doubted if host(r["url"]) not in seen]
        found = threat(ask)
        fresh = {}
        for row in doubted:
            name = host(row["url"])
            if name in seen:
                hit = seen[name]
            else:
                hit = found.get(row["url"], "")
                fresh[name] = hit
            if hit:
                row["safe"], row["safe_why"] = UNSAFE, "Safe Browsing: %s" % hit.lower()
                stats["threats"] += 1
        remember_verdict(conn, fresh.items())

    stats["unsafe"] = sum(1 for r in rows if r["safe"] == UNSAFE)
    stats["unknown"] = sum(1 for r in rows if r["safe"] == UNKNOWN)
    if stats["unsafe"]:
        # что именно не так — в debug: причина содержит чужой адрес целиком
        log.warning("Ссылки: отбраковано %d из %d", stats["unsafe"], stats["checked"])
        for row in rows:
            if row["safe"] == UNSAFE:
                log.debug("Небезопасная ссылка (%s): %s", row["safe_why"], row["url"])
    return stats


# ------------------------------------------------------- применение к выпуску
def safe(item) -> bool:
    """Годится ли ссылка материала для показа читателю.

    UNKNOWN — годится. Незнакомый домен есть у половины хороших ссылок с
    Hacker News, и молчать про них значило бы выбросить целый источник;
    приговор выносят слои, которые точно знают, что не так. Строгость
    включается `safe_strict`: тогда публикуется только то, за что поручились.
    """
    mark = str((item or {}).get("safe") or "") or UNKNOWN
    if mark == UNSAFE:
        return False
    return mark == OK or not CFG["safe_strict"]


def face_of(group):
    """Материалы кластера, чью ссылку можно показать. Пусто — показывать нечего.

    Отсюда `rank.primary_of` и берёт лицо кластера: небезопасная ссылка не
    убивает новость, а лишь уступает место следующей заметке о том же событии.
    """
    return [item for item in group if safe(item)]


def publishable(group) -> bool:
    """Есть ли у события хоть одна ссылка, которую не стыдно показать."""
    return bool(face_of(group))


def drop_unsafe(groups) -> tuple:
    """Отсеять события, у которых годных ссылок не осталось вовсе.

    Возвращает (что осталось, сколько снято). Такое бывает редко — обычно
    достаточно того, что лицо кластера сместилось, — но когда бывает,
    показывать нечего: ссылка в карточке обязательна.
    """
    kept = [g for g in groups if publishable(g)]
    return kept, len(groups) - len(kept)


def outward(url: str) -> str:
    """Ссылка наружу для страницы в браузере. Пусто — ссылку не ставить.

    Второй рубеж, у самого HTML. В истории лежат новости, собранные до того,
    как проверка появилась, и вердикта у них нет вовсе — а страница открыта
    всем. Форма ссылки при этом проверяется без базы и без сети, так что
    рубеж ничего не стоит.
    """
    url = str(url or "").strip()
    return "" if shaped_badly(url) else url


def stats_line(stats) -> str:
    """Строка для `status`: что проверка сделала за прогон."""
    if not stats.get("checked"):
        return ""
    bits = ["проверено %d" % stats["checked"]]
    if stats.get("unsafe"):
        bits.append("отбраковано %d" % stats["unsafe"])
    if stats.get("resolved"):
        bits.append("развёрнуто %d" % stats["resolved"])
    if stats.get("threats"):
        bits.append("по базе угроз %d" % stats["threats"])
    return "Ссылки: " + ", ".join(bits)


def known_bad(conn, days=7) -> list:
    """Домены, которые проверка забраковала за последние дни — для `status`."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [dict(row) for row in conn.execute(
        "SELECT host, verdict, checked_at FROM hosts WHERE verdict != '' "
        "AND checked_at >= ? ORDER BY checked_at DESC LIMIT 20", (since,))]
