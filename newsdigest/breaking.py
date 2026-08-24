# -*- coding: utf-8 -*-
"""Срочные новости: то, что не должно ждать планового выпуска.

Логика намеренно консервативная. Ложное «срочно» в час ночи раздражает
сильнее, чем десяток непойманных событий, поэтому кандидат обязан пройти
три независимые проверки:

    1) подтверждение — о событии за окно написали несколько разных ИЗДАТЕЛЕЙ,
       и хотя бы один из них первоисточник (либо о нём сообщили сразу два
       мировых агентства, либо оно взорвало Hacker News);
    2) оценка срочности — ОТДЕЛЬНЫМ промптом (llm.rate_urgency), не тем, что
       ранжирует выпуск. Тот меряет «интересно ли читателю» — на такой шкале
       землетрясение у персоны «инженер-разработчик» получает три балла.
       Срочность так мерить нельзя: это про масштаб события, а не про вкусы;
    3) приличия — тихие часы, лимит на сутки и пауза рассылки.

Уровня два, и ведут они себя по-разному:

    ⚡ молния (urgency >= breaking_flash_score) уходит немедленно, отдельным
      сообщением. Тихие часы обходит только событие мирового масштаба:
      ради землетрясения M7 человека будят и ночью;
    🔔 важное (>= breaking_alert_score) копится в очереди и уходит одной
      короткой сводкой раз в breaking_alert_every_h. За тихие часы оно
      накапливается и догоняет утром — раньше просто терялось.

Всё остальное ждёт планового выпуска, как и прежде.

Отправленное (и поставленное в очередь) сразу попадает в историю `sent`,
поэтому в плановом выпуске оно уже не повторится.

Проверка идёт не «на каждого подписчика», а на группу: кандидаты зависят
только от набора разделов, поэтому читатели с одинаковым набором получают
одну оценку модели на всех — как разделы планового выпуска ранжируются одним
заходом в `pipeline.rank_all`. Личным остаётся всё остальное: история
отправленного, тихие часы, суточный лимит, язык карточки и сама отправка.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config, sections, signals, subscribers, translate, trust
from .config import CFG, local_now, log, now_iso
from .feedback import persona_hint
from .feedparse import parse_date
from .llm import LLMError, llm_cost, rate_urgency, summarize
from .pipeline import card_of, for_topic, fresh_rows
from .rank import SentIndex, cluster, prescore, primary_of
from .render import alert_bulletin, breaking_card, feedback_keyboard
from .storage import db, log_run, meta_get, meta_set
from .telegram import tg_send

#: сколько кандидатов максимум берём от одного читателя. Общий список группы
#: получается объединением и ограничен CFG["llm_candidates"]
MAX_CANDIDATES = 3

#: уровни срочности. Молния уходит сразу, важное копится и уходит сводкой
FLASH, ALERT = "flash", "alert"

#: насколько широк круг задетых событием. Только global обходит тихие часы
SCOPES = ("global", "national", "industry", "niche")


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


def sent_today(conn, chat_id="", level=FLASH) -> int:
    """Сколько срочного этого уровня уже ушло читателю сегодня."""
    today = local_now().strftime("%Y-%m-%d")
    if meta_get(conn, "breaking_date:%s:%s" % (level, chat_id), "") != today:
        return 0
    try:
        return int(meta_get(conn, "breaking_count:%s:%s" % (level, chat_id), "0"))
    except ValueError:
        return 0


def count_sent(conn, chat_id="", level=FLASH) -> None:
    today = local_now().strftime("%Y-%m-%d")
    meta_set(conn, "breaking_count:%s:%s" % (level, chat_id),
             sent_today(conn, chat_id, level) + 1)
    meta_set(conn, "breaking_date:%s:%s" % (level, chat_id), today)


def limit_for(level) -> int:
    return int(CFG["breaking_max_per_day"] if level == FLASH
               else CFG["alert_max_per_day"])


def level_of(urgency: float) -> str:
    """Во что превращается оценка срочности. Пусто — ждёт планового выпуска."""
    if urgency >= CFG["breaking_flash_score"]:
        return FLASH
    if urgency >= CFG["breaking_alert_score"]:
        return ALERT
    return ""


def blocked(conn, chat_id, level, scope="") -> str:
    """Причина, по которой этот уровень сейчас не отправить. Пусто — можно.

    Тихие часы у уровней разные: молния мирового масштаба их обходит, а
    важное в них не отправляется — но и не теряется: оно копится в очереди
    и уходит утром.
    """
    if sent_today(conn, chat_id, level) >= limit_for(level):
        return "лимит %d в сутки исчерпан" % limit_for(level)
    if level == FLASH and in_quiet_hours():
        if CFG["flash_override_quiet"] and scope == "global":
            return ""
        return "тихие часы %s" % CFG["breaking_quiet"]
    return ""


def is_hot(group) -> bool:
    """Первый фильтр — дешёвый и без модели: консенсус или взрыв на HN.

    Подтверждения считаются по ИЗДАТЕЛЯМ, а не по фидам. Одна редакция
    держит по нескольку лент (Guardian — шесть, ScienceDaily — пять), и статья,
    попавшая сразу в две из них, раньше выглядела как два независимых сайта.

    Сколько подтверждений нужно, зависит от того, кто подтверждает:
      * два мировых агентства — этого достаточно, они и есть эталон
        подтверждения (`breaking_min_wires`);
      * обычный набор — `breaking_min_sources` издателей, и хотя бы один
        первоисточник;
      * раздел без первоисточников вообще (спорт, кино — там нет ни одного
        tier-1) иначе не дал бы срочного НИКОГДА, поэтому для него работает
        более широкий консенсус без требования tier-1
        (`breaking_min_wide`).
    """
    names = trust.publishers(group)
    wires = {trust.publisher(i["source_id"]) for i in group
             if trust.kind(i["source_id"]) == "wire"}
    if len(wires) >= CFG["breaking_min_wires"]:
        return True
    has_primary = any(i["tier"] == 1 for i in group)
    if len(names) >= CFG["breaking_min_sources"] and has_primary:
        return True
    if len(names) >= CFG["breaking_min_wide"]:
        return True
    return max(i["social"] for i in group) >= CFG["breaking_social"]


def hot_clusters(conn, topics=None):
    """Свежие кластеры, похожие на срочные, лучшие — первыми.

    Смотрим по всем разделам читателя: землетрясение или отставка правительства
    ждать до утра должны не больше, чем релиз новой модели.

    Ни история, ни настройки конкретного читателя сюда не входят — только
    разделы. Поэтому на группу с одинаковым набором разделов кластеризация
    (самое дорогое место после самой модели) делается один раз.
    """
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=CFG["breaking_window_h"])).isoformat()
    everything = fresh_rows(conn)
    seen, rows = set(), []
    for topic in (topics or [CFG["topic"]]):
        for row in for_topic(everything, topic):
            if row["fetched_at"] > cutoff and row["url_hash"] not in seen:
                seen.add(row["url_hash"])
                rows.append(row)
    if not rows:
        return []
    hot = [g for g in cluster(rows, CFG["similarity"]) if is_hot(g)]
    return sorted(hot, key=prescore, reverse=True)


def unseen(hot, index):
    """Кандидаты конкретного читателя: из общего списка убираем то,
    что ему уже уходило."""
    return [g for g in hot if not index.seen(g, CFG["similarity"])][:MAX_CANDIDATES]


def candidates(conn, chat_id="", topics=None):
    """Свежие неотправленные кластеры одного читателя, лучшие — первыми."""
    return unseen(hot_clusters(conn, topics), SentIndex(conn, chat_id))


# ------------------------------------------------------ сводка важного (🔔)
def pending_alerts(conn, chat_id) -> list:
    """Что накопилось у читателя и ещё не ушло сводкой."""
    return list(conn.execute(
        "SELECT * FROM alerts WHERE chat_id=? AND sent_at='' ORDER BY urgency DESC",
        (str(chat_id),)))


def bulletin_due(conn, chat_id) -> bool:
    """Пора ли отдавать накопленное.

    В тихие часы — нет: важное копится. Поэтому после девятичасовой ночи
    условие «прошло больше breaking_alert_every_h» выполняется само собой, и
    отдельного правила про «догнать утром» не нужно.
    """
    if in_quiet_hours():
        return False
    last = parse_date(meta_get(conn, "alert_bulletin:%s" % chat_id, ""))
    if last is None:
        return True
    gap = timedelta(hours=max(1, int(CFG["breaking_alert_every_h"])))
    return datetime.now(timezone.utc) - last >= gap


def flush_alerts(conn, chat_id) -> int:
    """Отдаёт накопленное важное одной сводкой. Возвращает число сообщений."""
    rows = pending_alerts(conn, chat_id)
    if not rows or not bulletin_due(conn, chat_id):
        return 0
    tg_send(chat_id, alert_bulletin(rows), silent=True)
    conn.executemany("UPDATE alerts SET sent_at=? WHERE id=?",
                     [(now_iso(), row["id"]) for row in rows])
    meta_set(conn, "alert_bulletin:%s" % chat_id, now_iso())
    conn.commit()
    log_run(conn, "breaking", "bulletin",
            {"sent": 1, "items": len(rows), "cost": 0.0})
    log.info("🔔 Сводка важного %s: %d новост(и/ей)", chat_id, len(rows))
    return 1


def flush_all(subs) -> int:
    """Сводки всем, кому пора. Зовётся тем же таймером, что и проверка срочного."""
    conn = db()
    try:
        total = 0
        for sub in subs:
            if sub["paused"]:
                continue
            with subscribers.overlay(sub):
                total += flush_alerts(conn, str(sub["chat_id"]))
        return total
    finally:
        conn.close()


def why_not(conn, sub=None, chat_id="") -> str:
    """Причина, по которой проверка не запускается вовсе. Пусто — можно работать.

    Тихие часы сюда больше не входят: в них проверка идёт, потому что молнию
    мирового масштаба надо доставить и ночью, а важное — накопить к утру.
    Останавливаемся, только когда доставить нельзя НИЧЕГО.
    """
    if not CFG["breaking"]:
        return "выключено (ND_BREAKING=0)"
    if sub is not None and sub["paused"]:
        return "рассылка на паузе"
    if (sent_today(conn, chat_id, FLASH) >= limit_for(FLASH)
            and sent_today(conn, chat_id, ALERT) >= limit_for(ALERT)):
        return "лимит срочного на сутки исчерпан"
    return ""


def check(chat_id=None, sub=None) -> int:
    """Ищет и отправляет срочное. Возвращает число отправленных сообщений."""
    if sub is not None and chat_id is None:
        chat_id = sub["chat_id"]
    chat_id = str(chat_id or config.TG_CHAT)
    return check_group(plan_key(sub), [(chat_id, sub)])


def plan_key(sub) -> tuple:
    """Набор разделов подписчика — он же ключ группировки.

    Считается под его настройками: раздел по умолчанию (CFG['topic']) у
    подписчика может быть свой.
    """
    with subscribers.overlay(sub):
        return tuple(sections.plan(sub)) or (CFG["topic"],)


def check_all(subs) -> int:
    """Проверяет срочное сразу для всех подписчиков.

    Раньше проверка шла в цикле по подписчикам, и при одинаковых разделах
    модель дважды ранжировала одних и тех же кандидатов — за один и тот же
    ответ платили по разу на человека. Теперь подписчики группируются по
    набору разделов, и на группу идёт один запрос.
    """
    groups = {}
    for sub in subs:
        groups.setdefault(plan_key(sub), []).append((str(sub["chat_id"]), sub))
    return sum(check_group(topics, readers)
               for topics, readers in groups.items())


def allowed(conn, readers) -> list:
    """Кому проверку вообще запускаем: пауза, тихие часы и суточный лимит."""
    out = []
    for chat_id, sub in readers:
        with subscribers.overlay(sub):
            skip = why_not(conn, sub, chat_id)
        if skip:
            log.debug("Срочные для %s не проверяю: %s", chat_id, skip)
        else:
            out.append((chat_id, sub))
    return out


def group_persona(conn, topics, readers) -> str:
    """Портрет читателя для общего запроса.

    Личная дописка про «зашло/не зашло» попадает в него, только если она у
    всех в группе одна и та же — обычно это читатель в группе один или
    обратной связи ещё нет. Иначе запрос перестал бы быть общим, а срочное
    отбирается порогом 8.0: платить за отдельный запрос на каждого ради
    десятых балла незачем.
    """
    hints = {persona_hint(conn, chat_id) for chat_id, _sub in readers}
    return sections.persona(list(topics)) + (hints.pop() if len(hints) == 1 else "")


def shared_shortlist(pools, hot) -> list:
    """Кандидаты всей группы одним списком, в общем порядке прескоринга.

    У каждого читателя свой список: история отправленного личная. Но списки
    почти всегда совпадают, а модели нужен один — берём объединение, чтобы
    ничьи кандидаты не потерялись. Потолок общий с плановым выпуском: даже
    у очень разной группы прайс за один запрос остаётся предсказуемым.
    """
    chosen = {id(group) for pool in pools.values() for group in pool}
    limit = max(1, int(CFG["llm_candidates"]))
    return [group for group in hot if id(group) in chosen][:limit]


def rated_by_group(ranking, shortlist) -> dict:
    """Ответ модели, разложенный по кластерам: id(кластер) -> оценка срочности.

    Оценка — словарь: urgency (1-10), scope (global/national/industry/niche)
    и категория. scope решает, будить ли человека в тихие часы.
    """
    out = {}
    for entry in ranking:
        try:
            idx = int(entry.get("id", -1))
            urgency = float(entry.get("urgency", entry.get("score")) or 0)
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(shortlist):
            continue
        group = shortlist[idx]
        if urgency > out.get(id(group), {}).get("urgency", 0.0):
            scope = str(entry.get("scope") or "").strip().lower()
            out[id(group)] = {
                "urgency": urgency,
                "scope": scope if scope in SCOPES else "industry",
                "category": entry.get("category") or primary_of(group)["category"],
            }
    return out


def check_group(topics, readers) -> int:
    """Проверка срочного для читателей с одинаковым набором разделов.

    Общая у них ровно одна вещь — оценка модели. Всё остальное личное:
    кандидаты отсеиваются по своей истории, порог и лимит считаются на
    каждого, карточка пишется на его языке.
    """
    conn = db()
    try:
        ready = allowed(conn, readers)
        if not ready:
            return 0

        hot = hot_clusters(conn, list(topics))
        pools = {chat_id: unseen(hot, SentIndex(conn, chat_id))
                 for chat_id, _sub in ready}
        # у кого своих кандидатов не осталось, тот в общем запросе не участвует
        # и его долю за него не платит
        ready = [(chat_id, sub) for chat_id, sub in ready if pools[chat_id]]
        if not ready:
            return 0
        shortlist = shared_shortlist(pools, hot)

        persona = group_persona(conn, topics, ready)
        kev = signals.kev_ids(conn)
        try:
            ranking, usage = rate_urgency(shortlist, persona)
        except LLMError as exc:
            # Без оценки модели срочное обычно не отправляем: слишком легко
            # разбудить человека ради пересказа чужой новости. Но событие с
            # твёрдым числом в её мнении не нуждается — магнитуда 7.4 остаётся
            # магнитудой 7.4, что бы там ни ответил DeepSeek
            rated = signals.raise_floors({}, shortlist, kev)
            if not rated:
                log.warning("Срочное не проверить (%s) — подождёт выпуска", exc)
                for chat_id, _sub in ready:
                    log_run(conn, "breaking", "llm-failed",
                            {"candidates": len(pools[chat_id]), "sent": 0,
                             "cost": 0.0, "best": 0.0})
                return 0
            log.warning("Модель недоступна (%s), но числа говорят сами за себя",
                        exc)
            sent = 0
            for chat_id, sub in ready:
                with subscribers.overlay(sub):
                    sent += deliver(conn, chat_id, pools[chat_id], rated,
                                    persona, {}, 0.0)
            return sent

        # общий запрос делим на всех: иначе в `status` расход одного читателя
        # выглядел бы как расход целой группы
        cost = llm_cost(usage) / len(ready)
        rated = signals.raise_floors(rated_by_group(ranking, shortlist),
                                     shortlist, kev)

        sent, cards = 0, {}
        for chat_id, sub in ready:
            with subscribers.overlay(sub):
                sent += deliver(conn, chat_id, pools[chat_id], rated,
                                persona, cards, cost)
        return sent
    finally:
        conn.close()


def best_of(pool, rated):
    """Самый срочный кандидат этого читателя. (кластер, оценка) или (None, ...)."""
    best, rating = None, {"urgency": 0.0, "scope": "", "category": "other"}
    for group in pool:
        entry = rated.get(id(group))
        if entry and entry["urgency"] > rating["urgency"]:
            best, rating = group, entry
    return best, rating


def card_for(conn, group, score, category, persona, cache):
    """Карточка срочного и её цена. Одну и ту же новость двум читателям группы
    пишем один раз: портрет у них общий, а язык обычно тоже. Не написалась —
    запасной заголовок тоже общий: модель лежит сразу для всех, и ходить к ней
    заново на каждого читателя значит только тянуть время."""
    key = (id(group), CFG["language"])
    if key in cache:
        return cache[key], 0.0
    try:
        cards, usage = summarize([(group, score, category)], persona,
                                 CFG["language"])
        card, cost = cards.get(0), llm_cost(usage)
    except LLMError as exc:
        log.warning("Карточка для срочного не написалась (%s) — беру заголовок", exc)
        card, cost = None, 0.0
    main = primary_of(group)
    card = card_of(card, main)
    # запасной заголовок пришёл прямо из фида, да и модель на английском
    # источнике иногда оставляет его как есть — доводим до языка выпуска
    cost += translate.localize(conn, [card])
    translate.remember_headlines(conn, [(main["title"], card.get("headline"))])
    cache[key] = card
    return card, cost


def remember_sent(conn, chat_id, group, card, rating, section) -> None:
    """Кладёт срочное в историю читателя.

    Это и защита от повтора в плановом выпуске, и то, из чего страница строит
    ленту. Пишется и для молнии, и для важного в момент постановки в очередь:
    событие читателю уже обещано, и выпуск повторять его не должен.

    breaking=1 — единственное, чем эта запись отличается от плановой. По ней
    страница рисует срочное иначе: обводка у карточки, молния в уведомлениях.
    В самом сообщении пометка есть (`render.breaking_card`), но история про
    текст сообщения ничего не знает.
    """
    main = primary_of(group)
    conn.execute(
        "INSERT OR IGNORE INTO sent(chat_id,url_hash,sig,title,url,digest_date,"
        "sent_at,source_id,category,section,headline,summary,score,breaking) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (chat_id, main["url_hash"], main["sig"], main["title"], main["url"],
         local_now().strftime("%Y-%m-%d"), now_iso(), main["source_id"],
         rating["category"], section,
         str(card.get("headline") or "")[:300],
         str(card.get("what") or "")[:500], float(rating["urgency"])))
    for row in group:
        conn.execute("UPDATE items SET state='sent' WHERE url_hash=?",
                     (row["url_hash"],))


def section_of(main) -> str:
    """Вывеска срочного: раздел, проставленный при сборе, иначе — по источнику.

    Свой раздел у срочного не считается: кандидаты берутся сразу по всем
    разделам читателя.
    """
    return (main.get("section") or "") or sections.by_source(main["source_id"])


def send_flash(conn, chat_id, group, card, rating, stats) -> int:
    """⚡ Молния: отдельное сообщение прямо сейчас."""
    main = primary_of(group)
    urgency, category = rating["urgency"], rating["category"]
    tg_send(chat_id, breaking_card(card, group, urgency),
            keyboard=feedback_keyboard([(card, group, urgency, category)]),
            silent=False)
    remember_sent(conn, chat_id, group, card, rating, section_of(main))
    conn.commit()
    count_sent(conn, chat_id, FLASH)
    stats["sent"], stats["level"] = 1, FLASH
    log_run(conn, "breaking", "ok", stats)
    log.info("⚡ Молния %s: %s (срочность %.1f, %s, ~$%.4f)", chat_id,
             main["title"][:70], urgency, rating["scope"], stats["cost"])
    return 1


def queue_alert(conn, chat_id, group, card, rating, stats) -> int:
    """🔔 Важное: в очередь. Уйдёт сводкой, а накопленное ночью — утром.

    Считается отправленным сразу: событие читателю уже обещано, лимит на сутки
    тратится здесь, и сводка потом ничего не отбрасывает.
    """
    main = primary_of(group)
    section = section_of(main)
    conn.execute(
        "INSERT INTO alerts(chat_id,url_hash,title,url,source_id,section,"
        "headline,what,urgency,scope,at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (chat_id, main["url_hash"], main["title"], main["url"],
         main["source_id"], section, str(card.get("headline") or "")[:300],
         str(card.get("what") or "")[:500], float(rating["urgency"]),
         rating["scope"], now_iso()))
    remember_sent(conn, chat_id, group, card, rating, section)
    conn.commit()
    count_sent(conn, chat_id, ALERT)
    stats["queued"], stats["level"] = 1, ALERT
    log_run(conn, "breaking", "queued", stats)
    log.info("🔔 В очередь важного %s: %s (срочность %.1f)", chat_id,
             main["title"][:70], rating["urgency"])
    return 0        # читатель этого пока не увидел — считаем при отправке сводки


def deliver(conn, chat_id, pool, rated, persona, cards, cost) -> int:
    """Раскладывает лучшее из кандидатов читателя по уровням срочности."""
    stats = {"candidates": len(pool), "sent": 0, "queued": 0, "cost": cost,
             "best": 0.0, "level": ""}
    best, rating = best_of(pool, rated)
    stats["best"] = rating["urgency"]
    level = level_of(rating["urgency"]) if best is not None else ""

    if not level:
        log.debug("Срочных нет: лучшая срочность %.1f из нужных %.1f",
                  rating["urgency"], CFG["breaking_alert_score"])
        log_run(conn, "breaking", "below-threshold", stats)
        return 0

    skip = blocked(conn, chat_id, level, rating["scope"])
    if skip and level == FLASH and not blocked(conn, chat_id, ALERT):
        # молнию сейчас нельзя (тихие часы или лимит), но событие важное —
        # пусть подождёт в очереди, а не пропадёт совсем
        level, skip = ALERT, ""
    if skip:
        log.debug("Срочное для %s придержано: %s", chat_id, skip)
        stats["level"] = level
        log_run(conn, "breaking", "held", stats)
        return 0

    card, spent = card_for(conn, best, rating["urgency"], rating["category"],
                           persona, cards)
    stats["cost"] += spent
    if level == FLASH:
        return send_flash(conn, chat_id, best, card, rating, stats)
    return queue_alert(conn, chat_id, best, card, rating, stats)
