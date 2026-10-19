# -*- coding: utf-8 -*-
"""Конвейер выпуска: взять свежее из базы → отранжировать → написать → отправить.

Выпуск собирается по разделам. По расписанию это подборка «по паре новостей
из каждого раздела», по команде /news — топ одного раздела. Механика общая:

    1) материалы за окно читаются из базы ОДИН раз на весь выпуск;
    2) каждый раздел кластеризует свои материалы и отбирает кандидатов;
    2а) спорные пары — те, что слова не свели, а событие у них одно, —
       разбирает модель одним запросом на весь выпуск (dedup.py);
    3) модель ранжирует кандидаты каждого раздела своим портретом читателя
       (разделы идут параллельно — иначе выпуск из полутора десятков разделов
       собирался бы минуты). Плановые выпуски ранжируются группой: читателям
       с одним портретом — один запрос на раздел (`rank_together`);
    4) разделы разбираются по очереди, и всё уже занятое пропускается —
       поэтому одна и та же новость не приходит дважды под двумя вывесками;
    5) карточки пишутся пачками и уходят одним сообщением — оглавлением
       выпуска, из которого разделы открываются кнопками (issueview.py).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import (config, dedup, factcheck, issueview, safety, sections,
               subscribers, threads, translate)
from .config import CFG, local_now, log, now_iso
from .feedback import persona_hint, weighted_prescore
from .llm import (LLMError, card_key, card_text, llm_cost, rank_clusters,
                  summarize)
from .profiles import PROFILES, title, weight
from .rank import SentIndex, cluster, primary_of, select
from .render import fit_blocks, issue_info
from .sources import sources_for
from .storage import (cards_known, db, log_run, meta_set, remember_cards,
                      save_issue, save_leftover)
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

    Раздел проставлен при сборе по СОДЕРЖАНИЮ (`classify.route_all`) — берём
    его. Материал без раздела раскладываем по-старому, по источнику: так
    ведут себя записи, накопленные до маршрутизации, и те, где решить не
    удалось. Hacker News в старом пути проверяется по ключевым словам раздела.

    Материал, у которого раздел проставлен и он ЧУЖОЙ, сюда не попадает даже
    если его лента приписана к этой теме: ровно ради этого всё и затевалось —
    новость про GPT-6 от Reuters идёт в «ИИ», а не в «Политику».
    """
    allowed = sources_for(topic)
    keywords = [k.lower() for k in PROFILES.get(topic, {}).get("keywords", [])]
    out = []
    for row in rows:
        section = (row.get("section") or "").strip()
        if section:
            if section == topic:
                out.append(row)
            continue
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
    # событие, у которого не осталось ни одной ссылки, годной для показа,
    # дальше не идёт: ссылка в карточке обязательна, а лицо кластера уже
    # выбрано с оглядкой на неё (`rank.primary_of`)
    fresh, _dropped = safety.drop_unsafe(fresh)
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


def count_for(topic, count, scaled=True):
    """Сколько новостей взять в этот раздел.

    Разделов теперь шестнадцать, и «Игры» с «Кино» занимали бы в выпуске
    столько же места, сколько «Политика». Вес раздела (`profiles.weight`)
    это выравнивает. На запрос конкретного раздела вес не влияет: там
    читатель сам сказал, сколько ему нужно.
    """
    if not scaled:
        return count
    return max(1, int(round(count * weight(topic))))


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


# -------------------------------------------------------------------- выпуск
def build_and_send(dry_run=False, chat_id=None, sub=None, topics=None,
                   count=None, close_day=True) -> dict:
    """Собирает и отправляет выпуск одному читателю.

    topics — какие разделы взять (по умолчанию разделы подписчика),
    count — сколько новостей на раздел (по умолчанию /set each).
    """
    if sub is not None and chat_id is None:
        chat_id = sub["chat_id"]
    reader = Reader(str(chat_id or config.TG_CHAT), sub, topics, count,
                    close_day)
    return run_issues([reader], dry_run=dry_run, strict=True)[0]


def build_group(subs) -> list:
    """Плановые выпуски сразу нескольким подписчикам — статистика по порядку.

    Новости собираются один раз на всех, а ранжировать каждому отдельно
    значило бы платить модели за одних и тех же кандидатов по разу на
    человека. Поэтому ранжирование здесь общее (`rank_together`), а всё
    личное — история, вкусы, отбор, карточки — остаётся своим. Выпуск, упавший
    с ошибкой, соседей не роняет: на его месте None, причина — в логе.
    """
    readers = [Reader(str(sub["chat_id"]), sub) for sub in subs]
    return run_issues(readers, strict=False)


def per_section_count(plan, sub) -> int:
    """Сколько новостей на раздел. Единственный раздел получает полный выпуск —
    иначе «читаю только спорт» означало бы две новости в день."""
    if len(plan) > 1:
        return sections.per_section(sub)
    return max(CFG["max_items"], sections.per_section(sub))


class Reader:
    """Один адресат выпуска: что ему собрать и что собрано к ранжированию."""

    def __init__(self, chat_id, sub=None, topics=None, count=None,
                 close_day=True):
        self.chat_id, self.sub, self.close_day = chat_id, sub, close_day
        self.topics, self.count = topics, count
        self.stats = {"candidates": 0, "clusters": 0, "selected": 0, "sent": 0,
                      "cost": 0.0, "sections": 0, "empty": []}
        self.conn = None
        self.plan, self.mark, self.hint, self.index = [], "", "", None
        self.shortlists, self.rankings = [], {}

    def done(self, status) -> dict:
        """Записывает прогон и отпускает базу: дальше этот выпуск не идёт."""
        log_run(self.conn, "digest", status, self.stats)
        self.close()
        return self.stats

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def run_issues(readers, dry_run=False, strict=True) -> list:
    """Выпуски по шагам на всех сразу: кандидаты → общее ранжирование → выпуск.

    strict — ошибку пробрасывать. Без него она пишется в лог и стоит только
    того читателя, у кого случилась: плановая рассылка не должна оставлять
    без выпуска всех из-за одного.
    """
    results = [None] * len(readers)

    def guarded(pos, step):
        reader = readers[pos]
        try:
            # личные настройки — на каждом шаге свои: между шагами идут чужие
            with subscribers.overlay(reader.sub):
                results[pos] = step(reader)
        except Exception:  # noqa: BLE001 — см. strict
            reader.close()
            if strict:
                raise
            log.exception("Выпуск для %s не собрался", reader.chat_id)

    for pos in range(len(readers)):
        guarded(pos, prepare)
    ranked = [reader for reader in readers if reader.conn is not None]
    try:
        rank_together(ranked)
    except Exception:  # noqa: BLE001 — см. strict
        for reader in ranked:
            reader.close()
        if strict:
            raise
        log.exception("Ранжирование не удалось — выпуски не собраны")
        return results
    for pos, reader in enumerate(readers):
        if reader.conn is not None:
            guarded(pos, lambda r: finish(r, dry_run))
    return results


def prepare(reader):
    """Всё личное до ранжирования: разделы, история, вкусы, кандидаты.

    Возвращает статистику, если выпуск кончился, не начавшись, и None, если
    кандидаты есть и ждут модели.
    """
    sub, stats = reader.sub, reader.stats
    plan = list(reader.topics) if reader.topics else sections.plan(sub)
    reader.count = reader.count or per_section_count(plan, sub)
    reader.conn = conn = db()
    # какой выпуск суток собираем, решаем на входе: сборка занимает минуты, и
    # начатый в 20:59 выпуск должен закрыть свой слот, а не следующий
    reader.mark = subscribers.slot_mark(sub)
    reader.plan = plan = [topic for topic in plan if topic in PROFILES]
    if not plan:
        log.warning("Не задано ни одного раздела — выпуск не формируется")
        return reader.done("empty")

    rows = fresh_rows(conn)
    stats["candidates"] = len(rows)
    if not rows:
        log.warning("Нет свежих материалов — дайджест не формируется")
        return reader.done("empty")

    # прескоринг с поправкой на вкусы: реакции читателя решают, кто вообще
    # доедет до модели, — а это самый дешёвый способ учесть обратную связь
    ordering = weighted_prescore(conn, reader.chat_id)
    reader.hint = persona_hint(conn, reader.chat_id)
    reader.index = index = SentIndex(conn, reader.chat_id)

    reader.shortlists = shortlists = [
        (topic, shortlist_for(rows, topic, index, ordering,
                              CFG["section_candidates"]))
        for topic in plan]
    # слова развели кандидатов по кластерам, но одно событие всё ещё может
    # стоять в выпуске дважды — под разными заголовками или следом за вчерашним
    # выпуском. Спорное разбирается здесь, ДО ранжирования: платить за оценку
    # повтора незачем
    stats["cost"] += dedup.prune(conn, index, shortlists)
    # обеспеченность заявления — здесь же, до ранжирования: платить за оценку
    # и карточку события, которое не выйдет, незачем. Придержанное не
    # теряется — оно ждёт подтверждения и выйдет следующим выпуском
    stats["cost"] += factcheck.screen(conn, shortlists)
    stats["clusters"] = sum(len(s) for _topic, s in shortlists)
    if not stats["clusters"]:
        log.warning("После дедупликации новых новостей не осталось")
        return reader.done("empty")
    # дальше выпуск ждёт соседей по группе — незаписанное держало бы базу
    # запертой для них
    conn.commit()
    return None


def finish(reader, dry_run) -> dict:
    """Отбор по готовым оценкам, карточки и отправка — каждому своё."""
    conn, chat_id, index = reader.conn, reader.chat_id, reader.index
    stats, plan, count = reader.stats, reader.plan, reader.count
    blocks, spare = [], []
    # вес раздела применяем только к плановому выпуску: единственный раздел
    # приходит по запросу, и урезать его было бы странно
    scaled = len(plan) > 1
    for topic, shortlist in reader.shortlists:
        ranking = reader.rankings.get(topic) or []
        limits = limits_for(count_for(topic, count, scaled))
        picked = select(usable(ranking, shortlist, index), shortlist, **limits)
        for group, _score, _cat in picked:
            index.remember(group)
        spare.extend(leftover_rows(ranking, shortlist, picked))
        if picked:
            blocks.append((topic, picked))
        else:
            stats["empty"].append(topic)

    # хвост кладём как есть, без перевода: /more в Telegram больше нет (бот
    # только рассылает), а платить за перевод до тридцати заголовков в каждом
    # выпуске ради команды, которую никто не вызовет, незачем. Ленте на странице
    # хвост не нужен — она строится из `sent`
    spare = sorted(spare, key=lambda r: -r["score"])[:30]
    save_leftover(conn, chat_id, spare)
    if not blocks:
        log.warning("Ничего не прошло порог важности %.1f — тихий день",
                    CFG["min_score"])
        return reader.done("empty")

    cards = write_cards(conn, blocks, plan, stats)
    mark_earlier(conn, chat_id, index, cards)
    stats["selected"] = sum(len(block) for _topic, block in cards)
    stats["sections"] = len(cards)

    info = issue_info(cards, stats["candidates"], empty_note(stats))

    if dry_run:
        print()
        print(preview(cards, info))
        print("\n[dry-run] отправки не было. Примерная стоимость запроса: $%.4f"
              % stats["cost"])
        return reader.done("dry-run")

    stats["sent"] = send_issue(conn, chat_id, cards, info)

    day = local_now().strftime("%Y-%m-%d")
    for topic, block in cards:
        for card, group, score, category in block:
            main = primary_of(group)
            # вместе с новостью сохраняем и то, что читатель увидел: раздел,
            # написанный моделью заголовок, суть и оценку. По этой строке
            # страница потом собирает ленту, не заглядывая в текст сообщения
            conn.execute(
                "INSERT OR IGNORE INTO sent(chat_id,url_hash,sig,title,url,"
                "digest_date,sent_at,source_id,category,section,headline,"
                "summary,score,caveat) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (chat_id, main["url_hash"], main["sig"], main["title"],
                 main["url"], day, now_iso(), main["source_id"], category,
                 topic or "", str(card.get("headline") or "")[:300],
                 str(card.get("what") or "")[:500], float(score),
                 factcheck.caveat_of(group)))
            # чем эта новость продолжает вчерашнюю — приговор модели, вынесенный
            # ещё при разборе дублей. Пишется здесь, а не там: пока новость не
            # ушла, связывать было нечего
            threads.remember(conn, chat_id, main["url_hash"],
                             index.prior_of(main["url_hash"]))
            for item in group:
                # 'sent' здесь значит «кому-то уже уходило» и бережёт материал
                # от уборки; персональный дедуп живёт в таблице sent
                conn.execute("UPDATE items SET state='sent' WHERE url_hash=?",
                             (item["url_hash"],))
    conn.commit()
    if reader.close_day:
        # метка — «дата#номер выпуска в сутках»: следующий выпуск дня ждёт
        # своего часа, а этот повторно не соберётся
        subscribers.set_last_digest(conn, chat_id, reader.mark)
        meta_set(conn, "last_digest_date", day)
    reader.done("ok")
    log.info("Отправлено: %d новостей из %d разделов, %d сообщение(й), ~$%.4f",
             stats["selected"], stats["sections"], stats["sent"], stats["cost"])
    return stats


# ------------------------------------------------------------------- отправка
def screens_view() -> bool:
    """Выпуск экранами (оглавление + разделы) или старой сплошной лентой."""
    return str(CFG["tg_view"]).lower() != "feed"


def send_issue(conn, chat_id, cards, info) -> int:
    """Отправляет выпуск читателю. Возвращает, сколько сообщений ушло.

    Экранами выпуск всегда умещается в одно сообщение: разделы читатель
    открывает кнопками, и сообщение переписывается на месте.
    """
    if not screens_view():
        return send_feed(chat_id, cards, info)
    issue = issueview.snapshot(cards, info)
    ident = save_issue(conn, chat_id, issue)
    text, keyboard = issueview.screen(issue, ident)
    tg_send(chat_id, text, keyboard=keyboard or None)
    return 1


def send_feed(chat_id, cards, info) -> int:
    """Старый вид: сплошная лента, при нужде в два-три сообщения подряд.

    Кнопок под ней нет: оценивают и откладывают новости на странице.
    """
    sent = 0
    for text, _chunk in fit_blocks(cards, info["scanned"], note=info["note"]):
        tg_send(chat_id, text)
        sent += 1
        time.sleep(1.0)
    return sent


def preview(cards, info) -> str:
    """Что увидит читатель — для `--dry-run` в терминале.

    У выпуска экранами их несколько, поэтому печатаются все подряд: иначе в
    терминале было бы видно только оглавление, а проверять надо весь выпуск.
    """
    rule = "\n" + "─" * 60 + "\n"
    if not screens_view():
        return rule.join(plain(text) for text, _chunk
                         in fit_blocks(cards, info["scanned"], note=info["note"]))
    issue = issueview.snapshot(cards, info)
    return rule.join("[ %s ]\n\n%s" % (name, plain(text))
                     for name, text in issueview.screens(issue))


#: столько шортлистов раздела, самое большее, модель видит одним запросом.
#: Объединение кандидатов группы растёт с числом читателей, а длинный список
#: модель оценивает дольше и хуже; лишнее уходит следующим запросом
POOL_FACTOR = 3


def group_key(group) -> tuple:
    """Опознаёт кластер в шортлистах разных читателей: по составу, а не по
    объекту — у каждого читателя кластеры собраны заново."""
    return tuple(sorted(item["url_hash"] for item in group))


def rank_together(readers) -> None:
    """Ранжирует кандидатов сразу на всех читателей с одним портретом.

    Модель видит раздел и портрет читателя, а портрет — это раздел плюс
    `persona_hint`, подсказка из личных 👍/👎. Читатели с одной подсказкой
    (чаще всего — пустой) получили бы от модели одно и то же, поэтому в
    каждом разделе объединение их кандидатов ранжируется одним запросом.
    Общая шкала внутри раздела от этого только честнее нескольких
    независимых шкал на тех же кандидатах.

    Оценки каждому раздаются только на его собственный шортлист: кандидат
    соседа, которого читатель уже видел или который не прошёл его
    прескоринг, к нему не попадает. Стоимость запроса делится поровну.
    """
    teams = {}
    for reader in readers:
        teams.setdefault(reader.hint, []).append(reader)
    for hint, team in teams.items():
        pools = {}
        for reader in team:
            for topic, shortlist in reader.shortlists:
                groups, ids = pools.setdefault(topic, ([], {}))
                for group in shortlist:
                    key = group_key(group)
                    if key not in ids:
                        ids[key] = len(groups)
                        groups.append(group)
        stats = {"cost": 0.0, "requests": 0}
        rankings = rank_all([(topic, groups) for topic, (groups, _ids)
                             in pools.items()], hint, stats)
        for reader in team:
            reader.rankings = {
                topic: project(rankings.get(topic) or [], pools[topic][1],
                               shortlist)
                for topic, shortlist in reader.shortlists}
            reader.stats["cost"] += stats["cost"] / len(team)
        if len(team) > 1:
            alone = sum(1 for reader in team
                        for _topic, shortlist in reader.shortlists if shortlist)
            log.info("Ранжирование на %d читателей: %d запрос(ов) вместо %d, "
                     "~$%.4f", len(team), stats["requests"], alone,
                     stats["cost"])


def project(ranking, ids, shortlist) -> list:
    """Оценки общего пула → оценки одного шортлиста, в его собственном порядке.

    Порядок шортлиста — личный прескоринг читателя: по нему `select` разводит
    равные оценки, а когда модель не ответила, других оценок и нет.
    """
    scored = {}
    for entry in ranking:
        try:
            scored.setdefault(int(entry.get("id", -1)), entry)
        except (AttributeError, TypeError, ValueError):
            continue
    out = []
    for idx, group in enumerate(shortlist):
        entry = scored.get(ids.get(group_key(group)))
        if entry is not None:
            out.append(dict(entry, id=idx))
    return out


def rank_pool(pool, persona):
    """Ранжирует кандидатов раздела. Возвращает (оценки, стоимость, запросов)."""
    size = max(1, int(CFG["section_candidates"]) * POOL_FACTOR)
    ranking, cost, requests = [], 0.0, 0
    for start in range(0, len(pool), size):
        part, part_cost = rank_shortlist(pool[start:start + size], persona)
        for entry in part:
            try:
                ranking.append(dict(entry, id=int(entry.get("id", -1)) + start))
            except (AttributeError, TypeError, ValueError):
                continue
        cost += part_cost
        requests += 1
    return ranking, cost, requests


def rank_all(shortlists, hint, stats) -> dict:
    """Ранжирует разделы. Каждый — своим портретом читателя, все — параллельно.

    Параллельность здесь про ожидание сети: полтора десятка последовательных
    запросов к модели складываются в минуты, а выпуск нужен к завтраку.
    """
    jobs = [(topic, shortlist) for topic, shortlist in shortlists if shortlist]

    def one(job):
        topic, shortlist = job
        persona = PROFILES[topic]["persona"] + hint
        return topic, rank_pool(shortlist, persona)

    if len(jobs) <= 1:
        results = [one(job) for job in jobs]
    else:
        workers = max(1, min(int(CFG["section_workers"]), len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, jobs))

    out = {}
    for topic, (ranking, cost, requests) in results:
        out[topic] = ranking
        stats["cost"] += cost
        stats["requests"] = stats.get("requests", 0) + requests
    return out


def write_cards(conn, blocks, plan, stats):
    """Просит модель написать карточки на весь выпуск и раскладывает их обратно.

    Спрашиваем только про то, чего в кэше нет. Одна и та же новость приходит
    нескольким подписчикам с одинаковым набором разделов, а карточка у неё при
    том же портрете читателя и том же языке одна — второй раз она стоила бы
    ровно столько же, сколько первый, и слово в слово повторила бы первую.
    """
    flat = [pick for _topic, picked in blocks for pick in picked]
    persona, language = sections.persona(plan), CFG["language"]
    keys = [card_key(persona, language, card_text(group))
            for group, _score, _cat in flat]
    known = cards_known(conn, keys)
    cards_map = {idx: known[key] for idx, key in enumerate(keys) if key in known}
    todo = [idx for idx, key in enumerate(keys) if key not in known]
    hits = len(keys) - len(todo)
    if todo:
        try:
            written, usage = summarize([flat[idx] for idx in todo], persona,
                                       language)
            stats["cost"] += llm_cost(usage)
        except LLMError as exc:
            log.error("Саммари не удалось (%s) — публикую исходные заголовки", exc)
            written = {}
        fresh = {}
        for place, card in written.items():
            if 0 <= place < len(todo):
                cards_map[todo[place]] = card
                fresh[keys[todo[place]]] = card
        remember_cards(conn, fresh.items())
    if hits:
        log.info("Карточки: написано %d, взято из кэша %d",
                 len(cards_map) - hits, hits)

    out, idx = [], 0
    for topic, picked in blocks:
        block = []
        for group, score, category in picked:
            block.append((card_of(cards_map.get(idx), primary_of(group)),
                          group, score, category))
            idx += 1
        out.append((topic, block))
    stats["cost"] += localize(conn, out)
    return out


def mark_earlier(conn, chat_id, index, cards) -> None:
    """Дописывает в карточку то, что она продолжает, — одной строкой.

    Связь нашлась при разборе дублей (`dedup.prune`), а заголовок той новости
    лежит в истории читателя. Берётся ближайший шаг назад, а не вся цепочка:
    в Telegram выпуск — одно сообщение, и место в нём считанное; вся цепочка
    есть на странице, где её и листают.

    Зовётся ПОСЛЕ `write_cards`: заголовок в истории уже на языке выпуска, и
    второй раз его переводить незачем.
    """
    priors = {}
    for _topic, block in cards:
        for _card, group, _score, _cat in block:
            head = primary_of(group)["url_hash"]
            prior = index.prior_of(head)
            if prior:
                priors[head] = prior
    if not priors:
        return
    known = threads.rows_by_hash(conn, chat_id, list(priors.values()))
    for _topic, block in cards:
        for card, group, _score, _cat in block:
            row = known.get(priors.get(primary_of(group)["url_hash"], ""))
            if row is not None:
                card["earlier"] = str(row["headline"] or row["title"])[:200]


def card_of(written, main) -> dict:
    """Карточка новости: что написала модель, а чего не написала — из источника.

    Пустое поле добираем здесь, а не при выводе: дальше карточка идёт на
    перевод, и заголовок из фида должен попасть туда вместе с остальными.
    """
    written = written if isinstance(written, dict) else {}
    return {"headline": written.get("headline") or main["title"],
            "what": written.get("what") or main["summary"][:300],
            "why": written.get("why") or ""}


def localize(conn, blocks) -> float:
    """Доводит готовые карточки до языка выпуска. Возвращает стоимость перевода.

    Сюда карточка приходит любой: и написанная моделью (тогда переводить обычно
    нечего), и запасная — из заголовка фида, когда саммари не написалось. Это
    последняя проверка перед отправкой, поэтому она одна на весь выпуск и
    относится ко всем разделам сразу.

    Заодно запоминаем пару «заголовок источника → то, что увидел читатель»: по
    ней /more и /saved потом покажут русский текст без запроса к модели.
    """
    cards = [card for _topic, block in blocks for card, _g, _s, _c in block]
    cost = translate.localize(conn, cards)
    translate.remember_headlines(
        conn, [(primary_of(group)["title"], card.get("headline"))
               for _topic, block in blocks for card, group, _s, _c in block])
    return cost


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
