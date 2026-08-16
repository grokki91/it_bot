# -*- coding: utf-8 -*-
"""Срочные новости: то, что не должно ждать планового выпуска.

Логика намеренно консервативная. Ложное «срочно» в час ночи раздражает
сильнее, чем десяток непойманных событий, поэтому кандидат обязан пройти
три независимые проверки:

    1) подтверждение — о событии за окно написали несколько РАЗНЫХ сайтов,
       и хотя бы один из них первоисточник (либо оно взорвало Hacker News);
    2) оценка модели — тот же промпт ранжирования, порог заметно выше
       обычного (breaking_min_score);
    3) приличия — тихие часы, лимит на сутки и пауза рассылки.

Отправленное сразу попадает в историю `sent`, поэтому в плановом выпуске
оно уже не повторится.

Проверка идёт не «на каждого подписчика», а на группу: кандидаты зависят
только от набора разделов, поэтому читатели с одинаковым набором получают
одну оценку модели на всех — как разделы планового выпуска ранжируются одним
заходом в `pipeline.rank_all`. Личным остаётся всё остальное: история
отправленного, тихие часы, суточный лимит, язык карточки и сама отправка.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config, sections, subscribers
from .config import CFG, local_now, log, now_iso
from .feedback import persona_hint
from .llm import LLMError, llm_cost, rank_clusters, summarize
from .pipeline import for_topic, fresh_rows
from .rank import SentIndex, cluster, prescore, primary_of
from .render import breaking_card, feedback_keyboard
from .storage import db, log_run, meta_get, meta_set
from .telegram import tg_send

#: сколько кандидатов максимум берём от одного читателя. Общий список группы
#: получается объединением и ограничен CFG["llm_candidates"]
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


def sent_today(conn, chat_id="") -> int:
    today = local_now().strftime("%Y-%m-%d")
    if meta_get(conn, "breaking_date:%s" % chat_id, "") != today:
        return 0
    try:
        return int(meta_get(conn, "breaking_count:%s" % chat_id, "0"))
    except ValueError:
        return 0


def count_sent(conn, chat_id="") -> None:
    today = local_now().strftime("%Y-%m-%d")
    meta_set(conn, "breaking_count:%s" % chat_id, sent_today(conn, chat_id) + 1)
    meta_set(conn, "breaking_date:%s" % chat_id, today)


def is_hot(group) -> bool:
    """Первый фильтр — дешёвый и без модели: консенсус или взрыв на HN."""
    sources = {i["source_id"] for i in group}
    has_primary = any(i["tier"] == 1 for i in group)
    if len(sources) >= CFG["breaking_min_sources"] and has_primary:
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


def why_not(conn, sub=None, chat_id="") -> str:
    """Причина, по которой проверка не запускается. Пусто — можно работать."""
    if not CFG["breaking"]:
        return "выключено (ND_BREAKING=0)"
    if sub is not None and sub["paused"]:
        return "рассылка на паузе"
    if in_quiet_hours():
        return "тихие часы %s" % CFG["breaking_quiet"]
    if sent_today(conn, chat_id) >= CFG["breaking_max_per_day"]:
        return "лимит %d в сутки исчерпан" % CFG["breaking_max_per_day"]
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
    """Ответ модели, разложенный по кластерам: id(кластер) -> (балл, категория)."""
    out = {}
    for entry in ranking:
        try:
            idx, score = int(entry.get("id", -1)), float(entry.get("score") or 0)
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(shortlist):
            continue
        group = shortlist[idx]
        if score > out.get(id(group), (0.0, ""))[0]:
            out[id(group)] = (score, entry.get("category")
                              or primary_of(group)["category"])
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
        try:
            ranking, usage = rank_clusters(shortlist, persona)
        except LLMError as exc:
            # без оценки модели срочное не отправляем: слишком легко ошибиться
            log.warning("Срочное не проверить (%s) — подождёт выпуска", exc)
            for chat_id, _sub in ready:
                log_run(conn, "breaking", "llm-failed",
                        {"candidates": len(pools[chat_id]), "sent": 0,
                         "cost": 0.0, "best": 0.0})
            return 0

        # общий запрос делим на всех: иначе в `status` расход одного читателя
        # выглядел бы как расход целой группы
        cost = llm_cost(usage) / len(ready)
        rated = rated_by_group(ranking, shortlist)

        sent, cards = 0, {}
        for chat_id, sub in ready:
            with subscribers.overlay(sub):
                sent += deliver(conn, chat_id, pools[chat_id], rated,
                                persona, cards, cost)
        return sent
    finally:
        conn.close()


def best_of(pool, rated):
    """Лучший кандидат этого читателя по оценке модели."""
    best, best_score, category = None, 0.0, "other"
    for group in pool:
        score, cat = rated.get(id(group), (0.0, ""))
        if score > best_score:
            best, best_score = group, score
            category = cat or primary_of(group)["category"]
    return best, best_score, category


def card_for(group, score, category, persona, cache):
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
    card = card or {"headline": main["title"], "what": main["summary"][:300],
                    "why": ""}
    cache[key] = card
    return card, cost


def deliver(conn, chat_id, pool, rated, persona, cards, cost) -> int:
    """Отправляет читателю лучшее из его кандидатов — если оно прошло порог."""
    stats = {"candidates": len(pool), "sent": 0, "cost": cost, "best": 0.0}
    best, best_score, category = best_of(pool, rated)
    stats["best"] = best_score

    if best is None or best_score < CFG["breaking_min_score"]:
        log.debug("Срочных нет: лучшая оценка %.1f из нужных %.1f",
                  best_score, CFG["breaking_min_score"])
        log_run(conn, "breaking", "below-threshold", stats)
        return 0

    card, spent = card_for(best, best_score, category, persona, cards)
    stats["cost"] += spent
    main = primary_of(best)

    text = breaking_card(card, best, best_score)
    tg_send(chat_id, text,
            keyboard=feedback_keyboard([(card, best, best_score, category)]),
            silent=False)
    stats["sent"] = 1

    conn.execute(
        "INSERT OR IGNORE INTO sent(chat_id,url_hash,sig,title,url,digest_date,"
        "sent_at,source_id,category) VALUES (?,?,?,?,?,?,?,?,?)",
        (chat_id, main["url_hash"], main["sig"], main["title"], main["url"],
         local_now().strftime("%Y-%m-%d"), now_iso(), main["source_id"], category))
    for row in best:
        conn.execute("UPDATE items SET state='sent' WHERE url_hash=?",
                     (row["url_hash"],))
    conn.commit()
    count_sent(conn, chat_id)
    log_run(conn, "breaking", "ok", stats)
    log.info("Срочное отправлено %s: %s (оценка %.1f, ~$%.4f)",
             chat_id, main["title"][:70], best_score, stats["cost"])
    return 1
