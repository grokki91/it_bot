# -*- coding: utf-8 -*-
"""Аудит показа новостей: чему бот верит, что он показал и в каком порядке.

Отчёт для разбора глазами — одним текстовым файлом, без сети и без единой
записи в базу: база открывается только на чтение, поэтому запускать можно при
работающем демоне.

    python3 tools/audit.py > audit.txt
    python3 tools/audit.py --days 30 --examples > audit.txt

`status` отвечает на вопрос «работает ли оно», `report` — «стало лучше или
хуже», а этот разбор — «чему верить и в каком порядке показывать». Он сводит
вместе то, что иначе лежит в разных таблицах:

    воронка источника    сколько принёс материалов и сколько из них дошло
                         до читателя. Лента, которая даёт сотню записей и ноль
                         показов, — это не источник, а расход
    вес источника        доверие, издатель и класс: описан ли он в реестре
                         (`trust.SOURCE_META`) или взвешен вслепую по tier
    балл против реакции   предсказывает ли оценка модели 👍 и 👎. Это
                         единственная внешняя проверка ранжирования
    повторы в истории    пары, которые дедуп должен был свести, а они ушли
                         читателю обеими
    свежесть             сколько часов новости на момент показа

Что уходит в отчёт: цифры, имена источников и разделов. Chat_id заменены на
«чат-1», ссылки — на домены, текст пропущен через `newsdigest/redact.py`.
Заголовки печатаются только с `--examples`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Скрипт кладут и рядом с проектом, и отдельным файлом в /tmp — чтобы снять
# отчёт, не трогая рабочее дерево (иначе автообновление увидит правки и встанет).
# Поэтому пакет ищем сначала рядом с собой, а потом в текущем каталоге.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT if (_ROOT / "newsdigest").is_dir() else Path.cwd()))

from newsdigest import config, factcheck, profiles, redact, safety  # noqa: E402
from newsdigest import sections, sources, trust, userprofiles      # noqa: E402
from newsdigest.config import CFG, WEIGHTS                          # noqa: E402
from newsdigest.feedparse import parse_date                         # noqa: E402
from newsdigest.profiles import PROFILES                            # noqa: E402
from newsdigest.textutil import sim_sets                            # noqa: E402

TOP = 15          # длина списков: отчёт читают глазами, а не грепом
PAIR_WINDOW_H = 72  # в каком окне ищем повтор, ушедший читателю дважды


# ------------------------------------------------------------------ утилиты
def head(text) -> None:
    print("\n\n=== %s ===" % text)


def part(text) -> None:
    print("\n--- %s ---" % text)


def clean(text, limit=88) -> str:
    """Строка в отчёт: одной строкой, без секретов, обрезанная."""
    return redact.scrub(" ".join(str(text or "").split()))[:limit]


def share(n, total) -> float:
    return (float(n) / total) if total else 0.0


def bar(value, width=18) -> str:
    filled = int(round(max(0.0, min(value, 1.0)) * width))
    return "█" * filled + "·" * (width - filled)


def q(conn, sql, args=()) -> list:
    """Запрос, который не падает на базе постарше: нет таблицы — нет строк."""
    try:
        return list(conn.execute(sql, args))
    except sqlite3.OperationalError as exc:
        print("  (пропускаю: %s)" % exc)
        return []


def one(conn, sql, args=()):
    rows = q(conn, sql, args)
    return rows[0] if rows else None


def ago(days) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def hours_between(later, earlier):
    a, b = parse_date(later or ""), parse_date(earlier or "")
    if not a or not b:
        return None
    return (a - b).total_seconds() / 3600.0


def home_topics() -> dict:
    """Источник -> раздел, к ленте которого он приписан."""
    index = {}
    for name, body in PROFILES.items():
        for feed in body.get("feeds") or ():
            index.setdefault(str(feed[0]), name)
    return index


def chat_names(conn) -> dict:
    """Chat_id -> «чат-1». В отчёт настоящие номера не уходят."""
    ids = [row["chat_id"] for row in q(
        conn, "SELECT DISTINCT chat_id FROM sent ORDER BY chat_id")]
    return {cid: "чат-%d" % (at + 1) for at, cid in enumerate(ids)}


# ----------------------------------------------------------------- разделы
def overview(conn, days) -> None:
    head("Чем меряем")
    size = Path(conn.execute("PRAGMA database_list").fetchone()["file"] or "")
    print("  база: %s" % (size if str(size) else "?"))
    print("  период отчёта: %d дн.   история отправленного живёт %d дн.,"
          " собранное — %d дн." % (days, CFG["keep_sent_days"],
                                   CFG["keep_items_days"]))
    print("  порог важности min_score %.1f   склейка similarity %.2f"
          "   серая зона от %.2f" % (CFG["min_score"], CFG["similarity"],
                                     CFG["dup_gray"]))
    print("  на раздел %d   в выпуске %d..%d   на источник не больше %d"
          "   на категорию %d" % (CFG["per_section"], CFG["min_items"],
                                  CFG["max_items"], CFG["max_per_source"],
                                  CFG["max_per_category"]))
    print("  прескоринг: доверие %.2f  подтверждения %.2f  HN %.2f"
          "  свежесть %.2f   реакции двигают отбор на %.2f"
          % (WEIGHTS["trust"], WEIGHTS["corroboration"], WEIGHTS["social"],
             WEIGHTS["freshness"], CFG["feedback_weight"]))
    print("  окно свежести %d ч   сбор каждые %d ч   выпусков в сутки %d"
          % (CFG["window_hours"], CFG["collect_every_h"], CFG["per_day"]))
    print("  модель решает раздел: %s   разбирает дубли: %s   фактчек: %s"
          % (CFG["classify_llm"], CFG["dup_llm"], CFG["factcheck"]))


def feeds_alive(conn) -> None:
    """Живы ли ленты. Смотрим в health — это итог последних обходов."""
    head("1. Источники: отвечают ли")
    feeds = sources.all_feeds(topics=list(PROFILES))
    health = {row["source_id"]: row for row in q(conn, "SELECT * FROM health")}
    used = sources.topics_in_use(conn)
    polled = {f[0] for topic in used for f in PROFILES.get(topic, {}).get("feeds", [])}

    broken = [r for r in health.values() if (r["fails"] or 0) > 0]
    muted = [r for r in health.values() if sources.muted_row(r)]
    quiet = [r for r in health.values()
             if (r["empty"] or 0) >= CFG["quiet_after_empty"]]
    never = sorted(sid for sid in polled if sid not in health)
    idle = [f[0] for f in feeds if f[0] not in polled]

    print("  лент в профилях: %d   опрашивается (разделы в работе): %d"
          % (len(feeds), len(polled)))
    print("  сбоят сейчас: %d   заглушены на сутки: %d   отвечают пустотой: %d"
          % (len(broken), len(muted), len(quiet)))
    print("  в обходе ни разу не были: %d   не опрашиваются"
          " (раздел не в плане): %d" % (len(never), len(idle)))

    if broken:
        part("Сбоят (подряд, без успеха)")
        for row in sorted(broken, key=lambda r: -(r["fails"] or 0))[:TOP]:
            print("  %-22s сбоев %-4d последний успех %s  %s"
                  % (row["source_id"][:22], row["fails"] or 0,
                     (row["ok_at"] or "никогда")[:10], clean(row["err"], 46)))
    if quiet:
        part("Отвечают 200 и ноль записей (адрес мог смениться)")
        for row in sorted(quiet, key=lambda r: -(r["empty"] or 0))[:TOP]:
            print("  %-22s пустых обходов %-4d молчит с %s"
                  % (row["source_id"][:22], row["empty"] or 0,
                     (row["empty_at"] or "?")[:10]))
    if never:
        print("\n  Раздел в работе, а лента ни разу не отвечала: %s"
              % ", ".join(never[:TOP]))
    if idle:
        print("\n  Не опрашиваются, потому что их раздел никто не читает: %d"
              " лент — %s" % (len(idle), ", ".join(sorted(idle)[:TOP])))


def feeds_weight(conn) -> None:
    """Чем взвешен источник: класс, доверие, издатель."""
    head("2. Источники: чем они взвешены")
    feeds = sources.all_feeds(topics=list(PROFILES))
    unknown = [f for f in feeds if f[0] not in trust.SOURCE_META]
    kinds = defaultdict(int)
    for feed in feeds:
        kinds[trust.kind(feed[0])] += 1

    print("  классы лент: %s" % "  ".join(
        "%s %d" % (name, kinds[name])
        for name in sorted(kinds, key=lambda k: -kinds[k])))
    print("  не описаны в реестре (доверие берётся по tier, вслепую): %d из %d"
          % (len(unknown), len(feeds)))
    for source_id, url, tier, category in unknown[:TOP]:
        print("    %-22s tier %s  доверие %.2f  издатель %s"
              % (source_id[:22], tier, trust.trust(source_id),
                 trust.publisher(source_id)))

    # издатель — единица счёта подтверждений: шесть лент Guardian не должны
    # выглядеть как шесть независимых подтверждений события
    by_publisher = defaultdict(list)
    for feed in feeds:
        by_publisher[trust.publisher(feed[0])].append(feed[0])
    many = sorted(((name, ids) for name, ids in by_publisher.items()
                   if len(ids) > 1), key=lambda p: -len(p[1]))
    if many:
        part("Издатели, под которыми больше одной ленты")
        for name, ids in many[:TOP]:
            print("  %-24s %d: %s" % (name[:24], len(ids),
                                      ", ".join(sorted(ids))[:60]))


def funnel(conn, days) -> None:
    """Воронка источника: собрано → показано. Кто наполняет, а кто шумит."""
    window = min(days, CFG["keep_items_days"])
    since = ago(window)
    head("3. Воронка: собрано → показано (за %d дн.)" % window)

    items = {row["source_id"]: row for row in q(
        conn,
        "SELECT source_id, COUNT(*) n, "
        "SUM(CASE WHEN section != '' THEN 1 ELSE 0 END) routed, "
        "SUM(CASE WHEN safe = ? THEN 1 ELSE 0 END) unsafe "
        "FROM items WHERE fetched_at > ? GROUP BY source_id",
        (safety.UNSAFE, since))}
    sent = {row["source_id"]: row for row in q(
        conn, "SELECT source_id, COUNT(*) n, AVG(score) avg FROM sent "
              "WHERE sent_at > ? GROUP BY source_id", (since,))}
    likes = defaultdict(lambda: [0, 0])
    for row in q(conn, "SELECT source_id, verdict, COUNT(*) n FROM feedback "
                       "WHERE at > ? GROUP BY source_id, verdict", (since,)):
        likes[row["source_id"]][0 if row["verdict"] == "up" else 1] += row["n"]

    total_items = sum(r["n"] for r in items.values())
    total_sent = sum(r["n"] for r in sent.values())
    print("  материалов собрано: %d   показано: %d   доля дошедших: %.1f%%"
          % (total_items, total_sent, 100.0 * share(total_sent, total_items)))

    part("Кто наполняет выпуск (по показам)")
    print("  %-22s %6s %6s %7s %6s %8s %s"
          % ("источник", "собр.", "пок.", "доля", "балл", "👍/👎", "довер."))
    order = sorted(sent.items(), key=lambda kv: -kv[1]["n"])[:TOP]
    for source_id, row in order:
        got = items.get(source_id, {"n": 0})["n"]
        up, down = likes[source_id]
        print("  %-22s %6d %6d %6.1f%% %6.1f %4d/%-3d %.2f"
              % (source_id[:22], got, row["n"],
                 100.0 * share(row["n"], got), row["avg"] or 0, up, down,
                 trust.trust(source_id)))
    if order:
        first = order[0]
        if first[1]["n"] > total_sent * 0.25:
            print("  %s занимает больше четверти показов — перекос"
                  % first[0])

    noise = sorted(((sid, row["n"]) for sid, row in items.items()
                    if row["n"] >= 20 and sid not in sent),
                   key=lambda kv: -kv[1])
    if noise:
        part("Собираем, но под своим именем не показываем ни разу")
        seen = absorbed(conn, [sid for sid, _n in noise[:TOP]], since)
        print("  %-22s %7s %16s   %s"
              % ("источник", "собр.", "событие показано", "класс"))
        for source_id, got in noise[:TOP]:
            hit, total = seen.get(source_id, (0, 0))
            print("  %-22s %7d %9d %5.0f%%   %-11s доверие %.2f"
                  % (source_id[:22], got, hit, 100.0 * share(hit, total),
                     trust.kind(source_id), trust.trust(source_id)))
        print("  «Событие показано» — материал доехал до читателя, но лицом"
              " кластера стал другой издатель.")
        print("  Высокая доля — лента работает как задумано: пересказ и"
              " пресс-релиз не должны быть лицом.")
        print("  Доля у нуля — её событий в выпуске не было вовсе: вот эту"
              " ленту и надо разбирать.")

    polled = {f[0] for topic in sources.topics_in_use(conn)
              for f in PROFILES.get(topic, {}).get("feeds", [])}
    dry = sorted(sid for sid in polled if sid not in items)
    if dry:
        print("\n  Опрашиваются, но ни одного материала за период: %d лент — %s"
              % (len(dry), ", ".join(dry[:TOP])))


def absorbed(conn, source_ids, since) -> dict:
    """Сколько материалов источника ВСЁ-ТАКИ дошли до читателя — но под именем
    другого издателя. Возвращает {источник: (дошло, всего)}.

    `sent.source_id` — это лицо кластера (`rank.primary_of`), а не список всех,
    кто об этом событии написал. Поэтому «ноль показов» у ленты значит одно из
    двух, и путать их нельзя: её событий не было в выпуске вовсе — или были, но
    ссылку дали тому, кто эту же новость проверял. Первую надо чинить или
    убирать, вторая работает ровно как задумано (`trust.demoted` не пускает
    пересказ и пресс-релиз в лицо кластера).

    Сравниваем по сигнатурам, тем же порогом, что и склейка. Чтобы не гонять
    все материалы против всей истории, показанное разложено в обратный индекс:
    слово -> кто его упоминал.
    """
    rows = [(row["sig"] or "").split() for row in conn.execute(
        "SELECT sig FROM sent WHERE sent_at > ? AND sig != ''", (since,))]
    index = defaultdict(set)
    for at, words in enumerate(rows):
        for word in words:
            index[word].add(at)
    shown = [set(words) for words in rows]

    out = {}
    for source_id in source_ids:
        hit = total = 0
        for row in conn.execute(
                "SELECT sig FROM items WHERE source_id = ? AND fetched_at > ?"
                " AND sig != ''", (source_id, since)):
            words = set((row["sig"] or "").split())
            if not words:
                continue
            total += 1
            near = set()
            for word in words:
                near.update(index.get(word, ()))
            if any(sim_sets(words, shown[at]) >= CFG["similarity"] for at in near):
                hit += 1
        out[source_id] = (hit, total)
    return out


def ranking(conn, days) -> None:
    """Балл модели: как он распределён и насколько отбор идёт на грани."""
    since = ago(days)
    head("4. Ранжирование: балл модели")
    scores = [row["score"] for row in q(
        conn, "SELECT score FROM sent WHERE sent_at > ? AND breaking = 0",
        (since,))]
    if not scores:
        print("  показанных новостей за период нет")
        return

    edges = [0, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.1]
    print("  показано (плановых): %d   медиана %.1f   мин %.1f   макс %.1f"
          % (len(scores), statistics.median(scores), min(scores), max(scores)))
    for low, high in zip(edges, edges[1:]):
        n = sum(1 for s in scores if low <= s < high)
        if n:
            print("  %4.1f–%-4.1f %5d  %s" % (low, high, n,
                                              bar(share(n, len(scores)))))

    below = sum(1 for s in scores if s < CFG["min_score"])
    if below:
        print("\n  Ниже порога %.1f показано: %d (%.0f%%) — значит, в отборе"
              " срабатывали послабления"
              % (CFG["min_score"], below, 100.0 * share(below, len(scores))))
    near = sum(1 for s in scores if s < CFG["min_score"] + 0.3)
    print("  В пределах 0.3 от порога: %d (%.0f%%) — эти новости решались"
          " случайностью округления"
          % (near, 100.0 * share(near, len(scores))))

    left = q(conn, "SELECT score, shown FROM leftover ORDER BY score DESC")
    if left:
        top = left[0]["score"]
        lost = sum(1 for row in left if row["score"] >= min(scores))
        print("\n  Хвост последнего выпуска (leftover): %d кандидатов,"
              " лучший балл %.1f" % (len(left), top))
        print("  Из них не хуже самой слабой показанной новости: %d" % lost)
        if lost:
            print("  Столько новостей отсеялось не баллом, а лимитами"
                  " (на раздел, источник, категорию).")


def taste(conn, days) -> None:
    """Балл против реакции — единственная внешняя проверка ранжирования."""
    since = ago(days)
    head("5. Балл модели против реакций читателя")
    rows = q(conn,
             "SELECT f.verdict v, s.score score, s.section section,"
             " s.source_id source FROM feedback f JOIN sent s"
             " ON s.url_hash = f.url_hash AND s.chat_id = f.chat_id"
             " WHERE f.at > ?", (since,))
    shown = one(conn, "SELECT COUNT(*) n FROM sent WHERE sent_at > ?", (since,))
    total_shown = shown["n"] if shown else 0
    if not rows:
        print("  реакций за период нет: отбор идёт без поправки на вкусы,")
        print("  и проверить ранжирование внешним сигналом нечем.")
        print("  Показано новостей: %d" % total_shown)
        return

    up = [r["score"] for r in rows if r["v"] == "up"]
    down = [r["score"] for r in rows if r["v"] == "down"]
    print("  реакций: %d на %d показанных (%.1f%%)"
          % (len(rows), total_shown, 100.0 * share(len(rows), total_shown)))
    print("  👍 %d, средний балл %.2f     👎 %d, средний балл %.2f"
          % (len(up), statistics.mean(up) if up else 0,
             len(down), statistics.mean(down) if down else 0))
    if up and down:
        delta = statistics.mean(up) - statistics.mean(down)
        print("  разница: %+.2f — %s" % (
            delta,
            "балл предсказывает реакцию" if delta > 0.3 else
            "балл реакцию не предсказывает: ранжирует не то, что нужно читателю"))

    by_section = defaultdict(lambda: [0, 0])
    by_source = defaultdict(lambda: [0, 0])
    for row in rows:
        at = 0 if row["v"] == "up" else 1
        by_section[row["section"] or "—"][at] += 1
        by_source[row["source"]][at] += 1

    part("По разделам (👍/👎)")
    for name, (u, d) in sorted(by_section.items(), key=lambda kv: -sum(kv[1])):
        print("  %-22s %3d/%-3d  %s" % (profiles.title(name)[:22] if name != "—"
                                        else "—", u, d,
                                        bar(share(u, u + d))))
    hated = sorted(((s, v) for s, v in by_source.items() if v[1] > v[0]),
                   key=lambda kv: -kv[1][1])
    if hated:
        part("Источники, где 👎 больше, чем 👍")
        for source_id, (u, d) in hated[:TOP]:
            print("  %-22s %3d/%-3d  доверие %.2f  класс %s"
                  % (source_id[:22], u, d, trust.trust(source_id),
                     trust.kind(source_id)))


def freshness(conn, days, examples=False) -> None:
    """Сколько часов новости в момент показа и не ушёл ли повтор."""
    since = ago(min(days, CFG["keep_items_days"]))
    head("6. Что читатель увидел: свежесть и повторы")
    lag = []
    for row in q(conn, "SELECT s.sent_at sent_at, i.published_at pub FROM sent s"
                       " JOIN items i ON i.url_hash = s.url_hash"
                       " WHERE s.sent_at > ?", (since,)):
        value = hours_between(row["sent_at"], row["pub"])
        if value is not None and value >= 0:
            lag.append(value)
    if lag:
        lag.sort()
        p90 = lag[min(len(lag) - 1, int(round(0.9 * (len(lag) - 1))))]
        old = sum(1 for h in lag if h > 24)
        print("  задержка от публикации до показа: медиана %.1f ч,"
              " p90 %.1f ч, максимум %.1f ч"
              % (statistics.median(lag), p90, lag[-1]))
        print("  показано старше суток: %d из %d (%.0f%%)"
              % (old, len(lag), 100.0 * share(old, len(lag))))
    else:
        print("  сопоставить показанное с датой публикации не удалось"
              " (материалы старше %d дней уже вычищены)" % CFG["keep_items_days"])

    # Повтор, доехавший до читателя: две записи его истории об одном событии.
    # Такие пары дедуп обязан был свести — каждая из них ошибка показа
    part("Повторы в истории одного читателя (окно %d ч)" % PAIR_WINDOW_H)
    names = chat_names(conn)
    rows = q(conn, "SELECT chat_id, sig, title, headline, sent_at FROM sent"
                   " WHERE sent_at > ? AND sig != '' ORDER BY sent_at",
             (ago(days),))
    by_chat = defaultdict(list)
    for row in rows[:4000]:
        by_chat[row["chat_id"]].append(row)

    # считаем не пары, а новости: у каждой ищем ближайшую из показанных ей
    # РАНЬШЕ. Одно событие, разошедшееся по пяти лентам, — это одна ошибка
    # показа, а не десять пар
    hard, gray, checked, shown_examples = 0, 0, 0, []
    for chat_id, feed in by_chat.items():
        words = [set((r["sig"] or "").split()) for r in feed]
        for b in range(1, len(feed)):
            best, at = 0.0, -1
            for a in range(b - 1, -1, -1):
                gap = hours_between(feed[b]["sent_at"], feed[a]["sent_at"])
                if gap is None or gap > PAIR_WINDOW_H:
                    break
                score = sim_sets(words[a], words[b])
                if score > best:
                    best, at = score, a
            if at < 0:
                continue
            checked += 1
            if best >= CFG["similarity"]:
                hard += 1
                if len(shown_examples) < 5:
                    shown_examples.append((names.get(chat_id, "?"), best,
                                           feed[at], feed[b]))
            elif best >= CFG["dup_gray"]:
                gray += 1
    print("  проверено новостей (у которых было что-то раньше в окне): %d"
          % checked)
    print("  выше порога склейки (%.2f): %d (%.1f%%) — это повторы, которых"
          " читатель видеть не должен"
          % (CFG["similarity"], hard, 100.0 * share(hard, checked)))
    print("  в серой зоне (%.2f..%.2f): %d (%.1f%%) — их разбирает модель,"
          " и здесь видно, сколько она пропустила"
          % (CFG["dup_gray"], CFG["similarity"], gray,
             100.0 * share(gray, checked)))
    if shown_examples and examples:
        for chat, score, a, b in shown_examples:
            print("   · %s  %.2f" % (chat, score))
            print("     %s" % clean(a["headline"] or a["title"]))
            print("     %s" % clean(b["headline"] or b["title"]))
    elif shown_examples:
        print("  Примеры заголовков — запустите с --examples")


def routing(conn, days) -> None:
    """Разделы: кто наполняется, кто молчит и как работает маршрутизация."""
    since = ago(days)
    head("7. Разделы и маршрутизация")
    rows = q(conn, "SELECT section, COUNT(*) n, AVG(score) avg FROM sent"
                   " WHERE sent_at > ? GROUP BY section ORDER BY n DESC",
             (since,))
    total = sum(row["n"] for row in rows)
    for row in rows:
        name = profiles.title(row["section"]) if row["section"] else "без раздела"
        print("  %-22s %4d  %s  ср. балл %.1f"
              % (name[:22], row["n"], bar(share(row["n"], total)),
                 row["avg"] or 0))

    seen = {row["section"] for row in rows}
    silent = [t for t in sections.plan() if t not in seen]
    if silent:
        print("\n  Ни одной новости за период: %s"
              % ", ".join(profiles.title(t) for t in silent))

    part("Маршрутизация по содержанию")
    row = one(conn, "SELECT COUNT(*) n,"
                    " SUM(CASE WHEN section != '' THEN 1 ELSE 0 END) routed,"
                    " AVG(route_conf) conf FROM items WHERE fetched_at > ?",
              (ago(min(days, CFG["keep_items_days"])),))
    if row and row["n"]:
        print("  раздел определён: %d из %d (%.0f%%), средняя уверенность %.2f"
              % (row["routed"] or 0, row["n"],
                 100.0 * share(row["routed"] or 0, row["n"]), row["conf"] or 0))

    # новость, уехавшая из «домашнего» раздела своей ленты, — это и есть
    # работа классификатора: видно, много ли он двигает
    home = home_topics()
    moved = 0
    rows = q(conn, "SELECT source_id, section FROM sent WHERE sent_at > ?"
                   " AND section != ''", (since,))
    for row in rows:
        if home.get(row["source_id"]) and home[row["source_id"]] != row["section"]:
            moved += 1
    if rows:
        print("  показано не из «домашнего» раздела своей ленты: %d из %d (%.0f%%)"
              % (moved, len(rows), 100.0 * share(moved, len(rows))))


def credibility(conn, days) -> None:
    """Достоверность того, что реально дошло до читателя."""
    since = ago(days)
    head("8. Достоверность показанного")
    kinds = defaultdict(int)
    for row in q(conn, "SELECT source_id, COUNT(*) n FROM sent"
                       " WHERE sent_at > ? GROUP BY source_id", (since,)):
        kinds[trust.kind(row["source_id"])] += row["n"]
    total = sum(kinds.values())
    for name in sorted(kinds, key=lambda k: -kinds[k]):
        print("  %-12s %4d  %s  доверие класса %.2f"
              % (name, kinds[name], bar(share(kinds[name], total)),
                 trust.KIND_TRUST.get(name, 0)))
    weak = sum(kinds[k] for k in trust.WEAK_KINDS)
    if total:
        print("  пресс-релизы, госагентства и пересказы: %d из %d (%.0f%%)"
              % (weak, total, 100.0 * share(weak, total)))

    row = one(conn, "SELECT COUNT(*) n, SUM(CASE WHEN caveat != '' THEN 1 ELSE 0"
                    " END) c FROM sent WHERE sent_at > ?", (since,))
    if row and row["n"]:
        print("\n  с оговоркой фактчека показано: %d из %d (%.0f%%)"
              % (row["c"] or 0, row["n"], 100.0 * share(row["c"] or 0, row["n"])))
    for verdict in (factcheck.OK, factcheck.CAVEAT, factcheck.HOLD):
        got = one(conn, "SELECT COUNT(*) n FROM claims WHERE verdict = ?"
                        " AND at > ?", (verdict, since))
        print("  приговоров «%s»: %d" % (verdict, got["n"] if got else 0))

    part("Ссылки")
    for verdict in (safety.OK, safety.UNSAFE, safety.UNKNOWN, ""):
        got = one(conn, "SELECT COUNT(*) n FROM items WHERE safe = ?"
                        " AND fetched_at > ?",
                  (verdict, ago(min(days, CFG["keep_items_days"]))))
        print("  %-9s %d" % (verdict or "(не проверялось)", got["n"] if got else 0))
    bad = q(conn, "SELECT source_id, COUNT(*) n FROM items WHERE safe = ?"
                  " AND fetched_at > ? GROUP BY source_id ORDER BY n DESC",
            (safety.UNSAFE, ago(min(days, CFG["keep_items_days"]))))
    for row in bad[:TOP]:
        print("  забраковано у %-22s %d" % (row["source_id"][:22], row["n"]))
    why = q(conn, "SELECT safe_why, COUNT(*) n FROM items WHERE safe = ?"
                  " AND fetched_at > ? GROUP BY safe_why ORDER BY n DESC",
            (safety.UNSAFE, ago(min(days, CFG["keep_items_days"]))))
    for row in why[:TOP]:
        print("    %-52s %d" % (clean(row["safe_why"], 52), row["n"]))


def runs(conn, days) -> None:
    """Прогоны и деньги: сколько выпусков вышло пустыми и во что обходится."""
    since = ago(days)
    head("9. Прогоны")
    counts = defaultdict(int)
    cost = 0.0
    candidates, selected = [], []
    for row in q(conn, "SELECT kind, status, stats FROM runs WHERE at > ?",
                 (since,)):
        counts[(row["kind"], row["status"])] += 1
        try:
            stats = json.loads(row["stats"])
        except (ValueError, TypeError):
            continue
        cost += float(stats.get("cost") or 0)
        if row["kind"] == "digest":
            candidates.append(int(stats.get("candidates") or 0))
            selected.append(int(stats.get("selected") or 0))
    for (kind, status) in sorted(counts, key=lambda k: -counts[k]):
        print("  %-10s %-16s %d" % (kind, status, counts[(kind, status)]))
    print("\n  расход модели за период: $%.4f" % cost)
    if candidates:
        print("  кандидатов на выпуск: медиана %d   отобрано: медиана %d"
              % (statistics.median(candidates), statistics.median(selected)))
    empty = counts.get(("digest", "empty"), 0)
    total = sum(n for (kind, _s), n in counts.items() if kind == "digest")
    if empty:
        print("  пустых выпусков: %d из %d (%.0f%%)"
              % (empty, total, 100.0 * share(empty, total)))
    print("\n  (в базе хранится последние 200 прогонов — за длинный период"
          " цифры этого раздела будут неполными)")


# -------------------------------------------------------------------- запуск
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Аудит показа новостей: источники, ранжирование, повторы.")
    parser.add_argument("--days", type=int, default=14,
                        help="за сколько дней считать (по умолчанию 14)")
    parser.add_argument("--db", default="", help="путь к базе (по умолчанию"
                                                 " ~/.newsdigest/digest.db)")
    parser.add_argument("--examples", action="store_true",
                        help="печатать заголовки-примеры")
    args = parser.parse_args(argv)

    config.load_env()          # пороги и веса — те же, что у живого демона
    userprofiles.apply()       # и список источников тоже: profiles.json учтён
    path = Path(args.db) if args.db else config.DB_FILE
    if not path.exists():
        print("Базы нет: %s" % path)
        return 1

    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    days = max(1, args.days)
    try:
        print("Аудит от %s, период %d дн."
              % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), days))
        overview(conn, days)
        feeds_alive(conn)
        feeds_weight(conn)
        funnel(conn, days)
        ranking(conn, days)
        taste(conn, days)
        freshness(conn, days, args.examples)
        routing(conn, days)
        credibility(conn, days)
        runs(conn, days)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
